"""工作台的数据契约。运行结果、判断和对话建议分别保存。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

Area = Literal["overview", "literature", "question", "study", "writing"]
ItemKind = Literal["note", "question", "plan", "result", "finding", "outline"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Brief(Contract):
    goal: str = Field(default="", max_length=2000)
    background: str = Field(default="", max_length=2000)
    resources: str = Field(default="", max_length=2000)
    time_budget: str = Field(default="", max_length=1000)


class ItemInput(Contract):
    kind: ItemKind
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=16000)
    paper_ids: list[str] = Field(default_factory=list, max_length=30)
    related_ids: list[str] = Field(default_factory=list, max_length=30)
    metrics: dict[str, Annotated[float, Field(strict=True)]] = Field(default_factory=dict, max_length=100)
    result_status: Literal["completed", "failed", "inconclusive"] | None = None
    assessment: Literal["supports", "refutes", "inconclusive"] | None = None
    supersedes_id: str | None = None


class WorkItem(ItemInput):
    item_id: str = Field(default_factory=lambda: uuid4().hex)
    status: Literal["active", "superseded"] = "active"
    needs_review: bool = False
    created_at: str = Field(default_factory=now)
    origin: Literal["user", "accepted_suggestion"] = "user"


class SuggestedItem(Contract):
    # The guide cannot invent experiment results or approve conclusions.
    kind: Literal["note", "question", "plan", "outline"] = "note"
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=12000)
    paper_ids: list[str] = Field(default_factory=list, max_length=12)
    related_ids: list[str] = Field(default_factory=list, max_length=12)


class GuideDraft(Contract):
    reply: str = Field(min_length=1, max_length=14000)
    cited_paper_ids: list[str] = Field(default_factory=list, max_length=12)
    questions: list[str] = Field(default_factory=list, max_length=3)
    suggestions: list[SuggestedItem] = Field(default_factory=list, max_length=3)


class Suggestion(SuggestedItem):
    suggestion_id: str = Field(default_factory=lambda: uuid4().hex)
    based_on_revision: int
    applied_item_id: str | None = None


class Message(Contract):
    message_id: str = Field(default_factory=lambda: uuid4().hex)
    role: Literal["user", "assistant"]
    content: str
    area: Area
    mode: Literal["user", "rules", "model", "error"]
    cited_paper_ids: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    suggestion_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class Workspace(Contract):
    schema_version: int = 1
    project_id: str
    revision: int = 0
    brief: Brief = Field(default_factory=Brief)
    items: list[WorkItem] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    updated_at: str = Field(default_factory=now)
