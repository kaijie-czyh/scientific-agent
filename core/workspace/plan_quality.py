"""Read-only delivery checks; source matching is not scientific validation."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from core.workspace.plan_schema import ResearchPlan
from core.workspace.schema import Contract


NOTICE = "仅核对当前资料、引用片段和条件；不证明引文支持论断、科学正确性、创新性或可发表性。"
CONDITIONS = {
    "resources": "资源／设备",
    "time_budget": "期限",
    "budget": "预算",
    "data_access": "资料权限",
}


class DeliveryCheck(Contract):
    code: str
    label: str
    status: Literal["pass", "attention"]
    detail: str


class CitationCheck(Contract):
    section: str
    statement: str
    paper_id: str
    title: str
    doi: str = ""
    quote: str
    matched: bool
    location: str
    page: int | None = None


class PlanQualityReport(Contract):
    version: Literal[1] = 1
    target_id: str
    workspace_revision: int
    status: Literal["legacy", "needs_attention", "ready_for_discussion"]
    label: str
    checks: list[DeliveryCheck]
    evidence: list[CitationCheck] = Field(default_factory=list)
    pending_conditions: list[str] = Field(default_factory=list)
    unconfirmed: list[str] = Field(default_factory=list)
    review_questions: list[str] = Field(default_factory=list)
    notice: str = NOTICE


def assess_plan(plan: ResearchPlan | None, papers, chunks, *, target_id,
                workspace_revision, needs_review=False):
    checks = [DeliveryCheck(
        code="structure", label="方案结构", status="pass" if plan else "attention",
        detail="已有结构化方案。" if plan else "历史方案缺少结构字段，请人工审阅原文；未自动重写。",
    ), DeliveryCheck(
        code="basis", label="材料版本", status="attention" if needs_review else "pass",
        detail="方案或关联问题的依据已变化，先复核再修订。" if needs_review else "未发现已标记的依据变更。",
    )]
    evidence, pending = [], []
    if plan:
        for section in plan.sections:
            for statement in section.statements:
                if statement.basis != "literature":
                    continue
                for pid in statement.paper_ids or [""]:
                    paper = papers.get(pid)
                    found = next((c for c in chunks.get(pid, [])
                                  if statement.quote and statement.quote in c.text), None)
                    abstract = bool(paper and statement.quote and statement.quote in (paper.abstract or ""))
                    matched = bool(paper and pid in plan.references and (found or abstract))
                    if matched and found:
                        location = "正文片段" + (f"（第 {found.page} 页）" if found.page is not None else "（未标页码）")
                    elif matched:
                        location = "摘要（未核对全文）"
                    elif not paper:
                        location = "来源不在当前文献库"
                    elif pid not in plan.references:
                        location = "引用未列入参考来源"
                    else:
                        location = "未匹配当前摘要或正文片段"
                    evidence.append(CitationCheck(
                        section=section.key, statement=statement.text, paper_id=pid,
                        title=paper.title if paper else pid or "未指定来源",
                        doi=(paper.doi or "") if paper else "", quote=statement.quote,
                        matched=matched, location=location, page=found.page if matched and found else None,
                    ))
        pending = [label for field, label in CONDITIONS.items()
                   if getattr(plan.conditions, field).basis != "user"]
        unused = set(plan.references) - {e.paper_id for e in evidence}
        citations_ok = bool(evidence) and all(e.matched for e in evidence) and not unused
        checks += [DeliveryCheck(
            code="citations", label="文献依据", status="pass" if citations_ok else "attention",
            detail=("所引片段可在当前资料中找到，仍需核对是否支持相应陈述。"
                    if citations_ok
                    else "尚无文献事实依据，请补充资料。" if not evidence
                    else "部分引文未匹配当前资料，或参考来源未对应到具体文献陈述，请逐条核对。"),
        ), DeliveryCheck(
            code="conditions", label="研究条件", status="attention" if pending else "pass",
            detail="请确认：" + "、".join(pending) if pending else "条件已标为用户提供；请核对是否适用于本次研究。",
        )]
        if any(e.matched and e.location.startswith("摘要") for e in evidence):
            checks.append(DeliveryCheck(
                code="reading_scope", label="阅读范围", status="attention",
                detail="部分依据仅来自摘要，方法细节和精确参数仍需合法原文核对。",
            ))
    attention = any(c.status == "attention" for c in checks)
    return PlanQualityReport(
        target_id=target_id, workspace_revision=workspace_revision,
        status="legacy" if plan is None else "needs_attention" if attention else "ready_for_discussion",
        label="历史方案，需人工审阅" if plan is None else "有待补充或复核项" if attention else "可供讨论，仍需专业审阅",
        checks=checks, evidence=evidence, pending_conditions=pending,
        unconfirmed=plan.unconfirmed if plan else [],
        review_questions=[
            "引文是否真正支持对应陈述，而不只是出现相同词句？",
            "已有工作是否已回答该问题，所述缺口是否仍需补充检索？",
            "比较条件与验证设计能否回答研究问题，资源是否可行？",
        ],
    )


def render_quality(report: PlanQualityReport):
    lines = ["## 交付检查（依据当前资料）", report.label, "> " + report.notice]
    for check in report.checks:
        lines.append(f"- {check.label}：{check.detail}")
    for entry in report.evidence:
        lines += [f"- 引用：{entry.title}；{entry.location}；DOI：{entry.doi or '未提供'}",
                  "> 对应陈述：" + entry.statement.replace("\n", " "),
                  "> 引用片段：" + entry.quote.replace("\n", " ")]
    lines += ["### 研究者审阅要点", *["- " + question for question in report.review_questions]]
    return "\n\n".join(lines)
