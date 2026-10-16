"""新工作台的独立接口；不触发旧 Pipeline 或执行实验。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import Field

from core.llm import LLMRegistry
from core.knowledge.schema import ArtifactType
from core.workspace.guide import STARTERS, build_guide_context, generate_guidance, rule_guidance
from core.workspace.schema import Area, Brief, Contract, ItemInput
from core.workspace.service import RevisionConflict, WorkspaceError, WorkspaceService

logger = logging.getLogger(__name__)


class BriefRequest(Contract):
    expected_revision: int = Field(ge=0)
    brief: Brief


class ItemRequest(Contract):
    expected_revision: int = Field(ge=0)
    item: ItemInput


class ApplyRequest(Contract):
    expected_revision: int = Field(ge=0)


class GuideRequest(Contract):
    expected_revision: int = Field(ge=0)
    area: Area
    message: str = Field(min_length=1, max_length=6000)
    selected_item_id: str | None = None
    selected_paper_ids: list[str] = Field(default_factory=list, max_length=3)
    reading_purpose: Literal["orientation", "method", "comparison", "innovation", "troubleshooting"] = "orientation"


def build_workspace_router(config, require_project, registry_factory=None) -> APIRouter:
    router = APIRouter(prefix="/api/projects", tags=["research-workspace"])

    def service_for(project_id: str):
        project = require_project(project_id)
        service = WorkspaceService(project_id, config.paths.project_db(project_id))
        # Existing create_project is memory-only until a Pipeline is started.
        # Persist identity on first workspace use, so a restart restores this project too.
        if not service.store.get_kv("research.topic"):
            service.store.save_kv("research.topic", project.topic)
        if not service.store.get_kv("project.domain"):
            service.store.save_kv("project.domain", project.domain)
        return service, project

    def view(service, project, workspace=None):
        return {
            **service.view(workspace), "project": {"topic": project.topic, "domain": project.domain},
            "guide_mode": "rules" if config.dry_run else "model", "starters": STARTERS,
        }

    def fail(exc):
        raise HTTPException(status_code=409 if isinstance(exc, RevisionConflict) else 422, detail=str(exc)) from exc

    @router.get("/{project_id}/workspace")
    def get_workspace(project_id: str):
        service, project = service_for(project_id)
        return view(service, project)

    @router.get("/{project_id}/research-materials")
    def research_materials(project_id: str):
        """汇集已有思路与稿件，只读取真实存量，不启动模型或流水线。"""
        service, _ = service_for(project_id)
        latest = {}
        for artifact in service.store.list_artifacts():
            if artifact.artifact_type not in (ArtifactType.PAPER_DRAFT, ArtifactType.REVIEW_NOTE):
                continue
            previous = latest.get(artifact.artifact_group)
            if previous is None or artifact.version > previous.version:
                latest[artifact.artifact_group] = artifact
        root = config.paths.project_artifacts(project_id).resolve()
        artifacts = []
        for artifact in sorted(latest.values(), key=lambda a: a.created_at, reverse=True):
            content = artifact.content
            issue = None
            if content is None and artifact.content_path:
                path = Path(artifact.content_path)
                if not path.is_absolute():
                    path = config.paths.root / path
                path = path.resolve()
                # 不允许元数据将浏览接口变成任意文件读取入口。
                if not path.is_relative_to(root) or path.suffix.lower() not in (".txt", ".md"):
                    issue = "产物路径不在当前项目文本产物目录，未读取"
                else:
                    try:
                        with path.open("r", encoding="utf-8") as stream:
                            content = stream.read(200001)
                    except (OSError, UnicodeError):
                        issue = "已有文件暂时无法读取，请检查文件是否存在及编码"
            artifacts.append({
                "artifact_id": artifact.artifact_id, "title": artifact.title,
                "artifact_type": artifact.artifact_type.value, "version": artifact.version,
                "created_at": artifact.created_at.isoformat(), "content": (content or "")[:200000],
                "available": bool(content), "truncated": len(content or "") > 200000,
                "issue": issue or (None if content else "只有产物元信息，尚无可阅读的正文"),
            })
        return {
            "ideas": [i.model_dump(mode="json") for i in service.store.list_ideas()],
            "artifacts": artifacts,
        }

    @router.put("/{project_id}/workspace/brief")
    def save_brief(project_id: str, request: BriefRequest):
        service, project = service_for(project_id)
        try:
            workspace = service.save_brief(request.brief, request.expected_revision)
        except WorkspaceError as exc:
            fail(exc)
        return view(service, project, workspace)

    @router.post("/{project_id}/workspace/items")
    def add_item(project_id: str, request: ItemRequest):
        service, project = service_for(project_id)
        try:
            workspace = service.add_item(request.item, request.expected_revision)
        except WorkspaceError as exc:
            fail(exc)
        return view(service, project, workspace)

    @router.post("/{project_id}/workspace/guide")
    def guide(project_id: str, request: GuideRequest):
        service, project = service_for(project_id)
        if not request.message.strip():
            raise HTTPException(status_code=422, detail="请输入需要讨论的问题")
        try:
            context = build_guide_context(
                service, project.topic, request.area, request.selected_item_id, request.selected_paper_ids,
                legacy_notes=getattr(project, "notes", None) or service.store.get_kv("user.notes") or [],
            )
            if context["revision"] != request.expected_revision:
                raise RevisionConflict("项目记录已有更新，请刷新后重新发送；你的输入尚未保存")
            if config.dry_run:
                draft = rule_guidance(request.area, request.message, context)
                mode = "rules"
            else:
                try:
                    registry = registry_factory() if registry_factory else LLMRegistry.from_config(config)
                    draft = generate_guidance(
                        registry, request.area, project.domain, request.message, context, request.reading_purpose,
                    )
                except Exception as exc:
                    logger.warning("科研指导调用失败：%s", type(exc).__name__)
                    raise HTTPException(status_code=502, detail="模型暂时无法返回有效指导。输入已保留，请稍后重试或检查模型配置。") from exc
                mode = "model"
            allowed_papers = {p["paper_id"] for p in context["papers"] + context.get("paper_inventory", [])}
            if not set(draft.cited_paper_ids) <= allowed_papers:
                raise WorkspaceError("模型引用了本次上下文之外的论文，回复未保存，请重新生成")
            workspace = service.record_guide(request.area, request.message, draft, mode, request.expected_revision)
        except WorkspaceError as exc:
            fail(exc)
        return view(service, project, workspace)

    @router.post("/{project_id}/workspace/suggestions/{suggestion_id}/apply")
    def apply(project_id: str, suggestion_id: str, request: ApplyRequest):
        service, project = service_for(project_id)
        try:
            workspace = service.apply_suggestion(suggestion_id, request.expected_revision)
        except WorkspaceError as exc:
            fail(exc)
        return view(service, project, workspace)

    @router.get("/{project_id}/workspace/export")
    def export(project_id: str, format: Literal["md", "json"] = "md"):
        service, _ = service_for(project_id)
        content = service.export_markdown() if format == "md" else service.load().model_dump_json(indent=2)
        return Response(
            content=content, media_type="text/markdown" if format == "md" else "application/json",
            headers={"Content-Disposition": f'attachment; filename="{project_id}-workspace.{format}"'},
        )

    return router
