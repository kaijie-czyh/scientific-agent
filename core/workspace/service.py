"""研究记录、依赖检查和用户采纳；不把模型建议当作实验事实。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable

from core.knowledge import KnowledgeStore
from core.workspace.schema import (
    Area, Brief, GuideDraft, ItemInput, Message, Suggestion, WorkItem, Workspace, now,
)

KEY = "research_workspace.v1"
AREA_LABELS = {
    "overview": "项目总览", "literature": "文献阅读", "question": "研究问题",
    "study": "方案与实验", "writing": "成果写作",
}
KIND_LABELS = {
    "note": "阅读与灵感笔记", "question": "研究问题", "plan": "研究方案",
    "result": "用户提交的实验结果", "finding": "用户确认的研究判断", "outline": "写作骨架",
}


class WorkspaceError(ValueError):
    pass


class RevisionConflict(WorkspaceError):
    pass


class WorkspaceService:
    def __init__(self, project_id: str, db_path: Path):
        self.project_id = project_id
        self.db_path = db_path
        self.store = KnowledgeStore(db_path)

    def load(self) -> Workspace:
        data = self.store.get_kv(KEY)
        if data is None:
            return Workspace(project_id=self.project_id)
        workspace = Workspace.model_validate(data)
        if workspace.project_id != self.project_id:
            raise WorkspaceError("工作台与项目不匹配")
        return workspace

    def _mutate(self, expected_revision: int, change: Callable[[Workspace], None]) -> Workspace:
        # A SQLite write transaction guards compare-and-save across threads/processes.
        # No lock is held during a network/model call.
        with sqlite3.connect(self.db_path, timeout=15) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (KEY,)).fetchone()
            workspace = Workspace.model_validate_json(row[0]) if row else Workspace(project_id=self.project_id)
            if workspace.project_id != self.project_id:
                raise WorkspaceError("工作台与项目不匹配")
            if workspace.revision != expected_revision:
                raise RevisionConflict("项目记录已有更新，请刷新后重新核对并提交；你的输入尚未保存")
            change(workspace)
            workspace.revision += 1
            workspace.updated_at = now()
            conn.execute(
                "INSERT OR REPLACE INTO kv_store (key, value, updated_at) VALUES (?, ?, ?)",
                (KEY, workspace.model_dump_json(), workspace.updated_at),
            )
        return workspace

    def save_brief(self, brief: Brief, expected_revision: int) -> Workspace:
        def change(workspace: Workspace) -> None:
            if workspace.brief != brief:
                for item in workspace.items:
                    if item.status == "active" and item.kind != "note":
                        item.needs_review = True
            workspace.brief = brief
        return self._mutate(expected_revision, change)

    def validate_item(self, workspace: Workspace, data: ItemInput) -> None:
        if not data.title.strip() or not data.content.strip():
            raise WorkspaceError("标题和内容不能只包含空白")
        if len(data.paper_ids) != len(set(data.paper_ids)) or len(data.related_ids) != len(set(data.related_ids)):
            raise WorkspaceError("来源和关联记录不能重复")
        papers = {p.paper_id for p in self.store.list_papers()}
        if not set(data.paper_ids) <= papers:
            raise WorkspaceError("引用的论文不在当前项目文献库中")
        by_id = {i.item_id: i for i in workspace.items}
        refs = [by_id.get(i) for i in data.related_ids]
        if any(i is None or i.status != "active" or i.needs_review for i in refs):
            raise WorkspaceError("关联记录不存在、已被替换或需要重新核对")
        if data.supersedes_id:
            old = by_id.get(data.supersedes_id)
            if old is None or old.status != "active" or old.kind != data.kind:
                raise WorkspaceError("只能修订当前有效的同类记录")
            if data.supersedes_id in data.related_ids:
                raise WorkspaceError("新版本不能依赖将被替换的旧版本")
            dependent_ids = {data.supersedes_id}
            while True:
                descendants = {i.item_id for i in workspace.items if set(i.related_ids) & dependent_ids}
                if descendants <= dependent_ids:
                    break
                dependent_ids |= descendants
            if set(data.related_ids) & dependent_ids:
                raise WorkspaceError("新版本不能依赖其旧版本的下游记录，请先重新核对研究依据")
        kinds = {i.kind for i in refs if i is not None}
        if data.kind == "plan" and "question" not in kinds:
            raise WorkspaceError("方案须关联一个已经保存的研究问题")
        if data.kind == "result":
            if "plan" not in kinds or data.result_status is None:
                raise WorkspaceError("实验结果须关联研究方案，并填写运行状态")
            if data.result_status == "completed" and not data.metrics:
                raise WorkspaceError("已完成的计算实验须填写实际指标；运行失败可以只记录原因")
        elif data.metrics or data.result_status is not None:
            raise WorkspaceError("只有实验结果可以填写运行状态和指标")
        if data.kind == "finding":
            results = [i for i in refs if i is not None and i.kind == "result"]
            if not results or data.assessment is None:
                raise WorkspaceError("研究判断须关联实验结果，并选择支持、反驳或无法定论")
            if data.assessment in ("supports", "refutes") and not any(
                i.result_status == "completed" for i in results
            ):
                raise WorkspaceError("运行失败或无法定论的实验不能用于确认支持或反驳设想")
        elif data.assessment is not None:
            raise WorkspaceError("只有研究判断可以填写支持程度")

    def _append_item(self, workspace: Workspace, data: ItemInput, origin: str = "user") -> WorkItem:
        self.validate_item(workspace, data)
        if data.supersedes_id:
            changed = {data.supersedes_id}
            for item in workspace.items:
                if item.item_id == data.supersedes_id:
                    item.status = "superseded"
            # Propagate stale dependencies instead of deleting earlier work.
            while True:
                affected = {
                    i.item_id for i in workspace.items
                    if i.status == "active" and set(i.related_ids) & changed
                }
                if affected <= changed:
                    break
                changed |= affected
            for item in workspace.items:
                if item.item_id in changed and item.status == "active":
                    item.needs_review = True
        item = WorkItem(**data.model_dump(), origin=origin)
        workspace.items.append(item)
        return item

    def add_item(self, data: ItemInput, expected_revision: int) -> Workspace:
        return self._mutate(expected_revision, lambda w: self._append_item(w, data))

    def record_guide(
        self, area: Area, text: str, draft: GuideDraft, mode: str, expected_revision: int,
    ) -> Workspace:
        def change(workspace: Workspace) -> None:
            known = {p.paper_id for p in self.store.list_papers()}
            if not set(draft.cited_paper_ids) <= known:
                raise WorkspaceError("指导回复引用了当前项目中不存在的论文，请重新生成")
            proposal_ids = []
            for suggestion in draft.suggestions:
                data = ItemInput(**suggestion.model_dump())
                self.validate_item(workspace, data)
                proposal = Suggestion(**suggestion.model_dump(), based_on_revision=expected_revision + 1)
                workspace.suggestions.append(proposal)
                proposal_ids.append(proposal.suggestion_id)
            workspace.messages.extend([
                Message(role="user", content=text, area=area, mode="user"),
                Message(
                    role="assistant", content=draft.reply, area=area, mode=mode,
                    cited_paper_ids=draft.cited_paper_ids, questions=draft.questions,
                    suggestion_ids=proposal_ids,
                ),
            ])
        return self._mutate(expected_revision, change)

    def apply_suggestion(self, suggestion_id: str, expected_revision: int) -> Workspace:
        def change(workspace: Workspace) -> None:
            suggestion = next((s for s in workspace.suggestions if s.suggestion_id == suggestion_id), None)
            if suggestion is None:
                raise WorkspaceError("建议不存在")
            if suggestion.applied_item_id:
                raise WorkspaceError("这条建议已经采纳，无需重复保存")
            if suggestion.based_on_revision != expected_revision:
                raise RevisionConflict("这条建议之后项目已有变化，请结合当前材料重新生成建议再采纳")
            data = ItemInput(**suggestion.model_dump(include={
                "kind", "title", "content", "paper_ids", "related_ids",
            }))
            item = self._append_item(workspace, data, origin="accepted_suggestion")
            suggestion.applied_item_id = item.item_id
        return self._mutate(expected_revision, change)

    def paper_summaries(self) -> list[dict]:
        return [{
            "paper_id": p.paper_id, "title": p.title, "year": p.year,
            "abstract": (p.abstract or "")[:1800], "doi": p.doi, "url": p.url,
            "scope": "有摘要，未在工作台确认全文" if p.abstract else "仅元信息",
        } for p in self.store.list_papers()]

    def view(self, workspace: Workspace | None = None) -> dict:
        workspace = workspace or self.load()
        active = [i for i in workspace.items if i.status == "active" and not i.needs_review]
        counts = {kind: sum(i.kind == kind for i in active) for kind in KIND_LABELS}
        papers = self.paper_summaries()
        next_step = (
            "先记录目标、已有基础和可用资源，再围绕一个具体问题阅读文献。"
            if not workspace.brief.goal else
            "先补充或导入相关论文；可以通过现有检索与导入页面操作。"
            if not papers else
            "结合论文中的具体局限，保存一个待检验的研究问题。"
            if not counts["question"] else
            "为当前问题制定最小试跑方案，写清比较条件和支持／反驳标准。"
            if not counts["plan"] else
            "按方案自行执行实验，再提交实际结果；等待结果是正常研究状态。"
            if not counts["result"] else
            "结合实际结果记录研究判断，允许反驳设想或暂时无法定论。"
            if not counts["finding"] else
            "整理有依据的写作骨架，或回到问题与实验补充证据。"
        )
        return {
            "workspace": workspace.model_dump(mode="json"), "papers": papers,
            "counts": counts, "next_step": next_step,
            "needs_review_count": sum(i.status == "active" and i.needs_review for i in workspace.items),
            "capabilities": {
                "records": "已接入", "guide": "已接入：按系统配置使用模型或规则指导",
                "literature": "复用现有论文库；检索与导入使用现有页面",
                "reading": "阅读指导已接入；自动逐段精读待迁入",
                "experiments": "手动执行与结果登记已接入；自动指标分析待迁入",
                "writing": "写作骨架已接入；完整稿件生成待迁入",
            },
        }

    def export_markdown(self) -> str:
        workspace = self.load()
        papers = {p.paper_id: p for p in self.store.list_papers()}
        lines = ["# 科研工作台记录", "", f"项目：{self.project_id} · 版本：{workspace.revision}", ""]
        for field, label in (("goal", "目标"), ("background", "已有基础"), ("resources", "资源"), ("time_budget", "时间")):
            lines.append(f"- {label}：{getattr(workspace.brief, field) or '尚未填写'}")
        for item in workspace.items:
            lines.extend(["", f"## {KIND_LABELS[item.kind]}：{item.title}", "",
                          f"记录 ID：{item.item_id} · 状态：{item.status} · 需要复核：{item.needs_review}", "", item.content])
            if item.result_status:
                lines.extend(["", f"用户提交的运行状态：{item.result_status}", json.dumps(item.metrics, ensure_ascii=False)])
            if item.assessment:
                lines.extend(["", f"用户判断（未经系统独立验证）：{item.assessment}"])
            if item.related_ids:
                lines.extend(["", "关联记录：" + "、".join(item.related_ids)])
            for pid in item.paper_ids:
                p = papers.get(pid)
                lines.append(f"\n来源：{p.title if p else pid}（{pid}；DOI：{p.doi if p else '未知'}）")
        return "\n".join(lines)
