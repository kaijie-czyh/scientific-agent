"""Entry choices reuse real project routes; offline HTTP and temporary data only."""

import pytest

from tests.test_conversation_home import home, tree
from tests.test_local_product_acceptance import no_network, ok, product


ENTRY_SETTINGS = (
    ("beginner", "orientation", "conditions"),
    ("direct", "literature", "exploration"),
    ("direct", "question", "question"),
    ("direct", "literature", "reading"),
)


def begin(h, entry, request_id, project_id=None):
    mode, stage, step = ENTRY_SETTINGS[entry]
    body = {
        "request_id": request_id,
        "title": "人工智能",
        "domain": "ai",
        "guidance_mode": mode,
        "entry_stage": stage,
        "discussion_step": step,
    }
    if project_id:
        body["project_id"] = project_id
    return ok(h.client.post("/api/conversations/start", json=body))


def workspace(h, pid):
    return ok(h.client.get(f"/api/projects/{pid}/workspace?include_chat=false"))[
        "workspace"
    ]


def formal_state(h, pid):
    current = workspace(h, pid)
    return {key: current[key] for key in ("revision", "items", "focus")}


def upload_readable(h, pid):
    return ok(
        h.client.post(
            f"/api/projects/{pid}/upload-paper",
            files={
                "file": (
                    "reading-fixture.md",
                    "软件流程测试材料，并非真实论文：在相同任务下比较两种方法，结果尚未验证。".encode(),
                    "text/markdown",
                )
            },
        )
    )["paper_id"]


def test_four_entry_settings_share_project_but_do_not_advance_research(product):
    h = product
    # Preparing the landing page never creates a project or an execution request.
    before = tree(h.root)
    for path in ("/", "/workspace", "/api/conversation-capabilities"):
        assert h.client.get(path).status_code == 200
    assert tree(h.root) == before

    pid = None
    conversations = []
    for index, settings in enumerate(ENTRY_SETTINGS):
        identity = begin(h, index, f"entry-{index}", project_id=pid)
        pid = pid or identity["project_id"]
        assert identity["project_id"] == pid
        cid = identity["conversation_id"]
        conversations.append(cid)
        base = f"/api/projects/{pid}/conversations/{cid}"

        files = tree(h.root)
        meta = ok(h.client.get(base))["conversation"]
        assert (
            meta["guidance_mode"], meta["entry_stage"], meta["discussion_step"]
        ) == settings
        route = ok(h.client.get(base + "/research-route"))
        assert route["discussion_step"] == settings[2]
        # An entry label cannot fabricate evidence, a confirmed question or a plan.
        assert route["step"] == "narrow_interest"
        assert route["question_id"] is None
        assert route["provided_readable_papers"] == 0
        assert route["planReady"] is False
        assert next(s for s in route["steps"] if s["id"] == "design")[
            "blocked_reason"
        ]
        assert ok(h.client.get(base + "/messages"))["messages"] == []
        current = formal_state(h, pid)
        assert current["revision"] == 0 and current["items"] == []
        assert not any(current["focus"].values())
        assert tree(h.root) == files

    assert len(set(conversations)) == 4
    assert len(ok(h.client.get("/api/projects"))["projects"]) == 1

    # Changing discussion focus is a settings write, not formal research adoption.
    base = f"/api/projects/{pid}/conversations/{conversations[0]}"
    baseline = formal_state(h, pid)
    changed = ok(
        h.client.patch(
            base,
            json={
                "guidance_mode": "direct",
                "entry_stage": "literature",
                "discussion_step": "reading",
            },
        )
    )
    assert changed["conversation"]["discussion_step"] == "reading"
    assert formal_state(h, pid) == baseline
    assert ok(h.client.get(base + "/research-route"))["discussion_step"] == "reading"


def test_paper_entry_reads_existing_material_without_search_or_adoption(product):
    h = product
    identity = begin(h, 3, "paper-entry")
    pid, cid = identity["project_id"], identity["conversation_id"]
    base = f"/api/projects/{pid}/conversations/{cid}"
    paper_id = upload_readable(h, pid)
    baseline = formal_state(h, pid)

    files = tree(h.root)
    route = ok(h.client.get(base + "/research-route"))
    assert route["discussion_step"] == "reading"
    assert route["step"] == "read_and_gap"
    assert route["provided_readable_papers"] == 1
    assert route["readable_paper_ids"] == [paper_id]
    assert not route["offer_search"] and not route["planReady"]
    assert tree(h.root) == files

    response = ok(
        h.client.post(
            base + "/messages",
            json={
                "request_id": "read-uploaded-only",
                "content": "只阅读我已上传的资料，说明阅读范围与待核查点；不要检索，不生成方案。",
                "selected_paper_ids": [paper_id],
            },
        )
    )
    assert response["status"] == "completed"
    assert response["messages"][-1]["mode"] == "rules"
    assert set(response["messages"][-1]["cited_paper_ids"]) <= {paper_id}
    assert formal_state(h, pid) == baseline
    assert not any(s["kind"] == "plan" for s in response["suggestions"])

    # Reopening evidence and available downloads must not redo a search or generation.
    files = tree(h.root)
    for path in (
        base + "/research-route",
        base + "/messages",
        f"/api/projects/{pid}/workspace/materials?kind=plans",
        f"/api/projects/{pid}/exports?conversation_id={cid}",
    ):
        assert h.client.get(path).status_code == 200
    assert tree(h.root) == files
    assert formal_state(h, pid) == baseline


def test_returning_question_entry_reuses_confirmed_basis_and_can_take_records(product):
    h = product
    identity = begin(h, 2, "question-entry")
    pid = identity["project_id"]
    paper_id = upload_readable(h, pid)
    baseline = formal_state(h, pid)
    assert baseline["items"] == []

    # This explicit record write represents the user's question confirmation.
    confirmed = ok(
        h.client.post(
            f"/api/projects/{pid}/workspace/items",
            json={
                "expected_revision": baseline["revision"],
                "item": {
                    "kind": "question",
                    "title": "相同任务下如何公平比较两种方法？",
                    "content": "仅形成待讨论的问题；没有实验数据或已验证结果。",
                    "paper_ids": [paper_id],
                },
            },
        )
    )
    question = next(i for i in confirmed["workspace"]["items"] if i["kind"] == "question")
    baseline = formal_state(h, pid)

    # Both returning users and a new beginner discussion retain the formal basis.
    for entry in (2, 0):
        returned = begin(h, entry, f"return-{entry}", project_id=pid)
        cid = returned["conversation_id"]
        base = f"/api/projects/{pid}/conversations/{cid}"
        files = tree(h.root)
        route = ok(h.client.get(base + "/research-route"))
        assert route["discussion_step"] == ENTRY_SETTINGS[entry][2]
        assert route["step"] == "shape_method"
        assert route["question_id"] == question["item_id"]
        assert not next(s for s in route["steps"] if s["id"] == "design")[
            "blocked_reason"
        ]
        assert route["planReady"] is False
        assert tree(h.root) == files
        assert formal_state(h, pid) == baseline

    # A useful, explicitly confirmed intermediate result is downloadable before a plan.
    files = tree(h.root)
    inventory = ok(h.client.get(f"/api/projects/{pid}/exports"))
    records = next(e for e in inventory["entries"] if e["key"] == "records")
    exported = h.client.get(records["url"])
    assert exported.status_code == 200, exported.text
    assert question["title"] in exported.text
    assert "没有实验数据或已验证结果" in exported.text
    assert not any(e["key"].startswith("plan:") for e in inventory["entries"])
    assert formal_state(h, pid) == baseline
    assert tree(h.root) == files


@pytest.mark.parametrize(
    "old_draft", ["请生成实验研究方案", "请检索联邦学习的代表性论文"],
    ids=["old-plan", "old-search"],
)
@pytest.mark.parametrize(
    "entry,prompt",
    [
        (2, "我目前的研究问题是：\n请先帮我核对问题是否具体、可以怎样验证，并整理成待我确认的问题；确认后再讨论方案。"),
        (3, "我手上有一篇论文，希望先读懂研究问题、方法与局限，再整理阅读重点。\n我最想弄清楚的是："),
    ],
    ids=["question-entry", "paper-entry"],
)
def test_switching_entry_keeps_old_draft_without_executing_old_intent(
    product, monkeypatch, old_draft, entry, prompt
):
    h = product
    identity = begin(h, entry, "entry-switch")
    pid, cid = identity["project_id"], identity["conversation_id"]
    base = f"/api/projects/{pid}/conversations/{cid}"
    paper_id = upload_readable(h, pid)
    baseline = formal_state(h, pid)

    from web import workspace_api

    captured = []
    original = workspace_api.rule_guidance

    def observe(area, text, context):
        captured.append((text, dict(context)))
        return original(area, text, context)

    monkeypatch.setattr(workspace_api, "rule_guidance", observe)
    # Exact text composed by chooseEntry + discussOnly preparePlanInput after
    # the user confirms appending the new entry to an existing nonempty draft.
    content = (
        old_draft + "\n\n" + prompt
        + "\n\n本次先按新起点讨论，暂不检索或搜索。"
        + "\n\n本次不生成或修订方案。"
    )
    response = ok(
        h.client.post(
            base + "/messages",
            json={
                "request_id": "discuss-new-entry",
                "content": content,
                "selected_paper_ids": [paper_id],
            },
        )
    )
    assert response["status"] == "completed"
    assert captured and all("plan_generation" not in context for _, context in captured)
    assert captured[0][0] == content
    assert not any(s["kind"] == "plan" for s in response["suggestions"])
    assert formal_state(h, pid) == baseline
    assert ok(h.client.get(base + "/research-tasks"))["tasks"] == []
    assert not any(
        s["kind"] == "plan"
        for s in ok(h.client.get(base + "/suggestions"))["suggestions"]
    )
    saved_users = [
        message["content"]
        for message in ok(h.client.get(base + "/messages"))["messages"]
        if message["role"] == "user"
    ]
    assert saved_users == [content] and saved_users[0].startswith(old_draft)
