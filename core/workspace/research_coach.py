"""Read-only research guidance, not a second workflow or autonomous executor.

The project workflow remains authoritative. This projection tells the guide
what evidence to obtain/read next and supplies a server-bound search proposal.
Nothing here calls a model, retrieves a paper, or confirms a research record.
"""
from __future__ import annotations

import re
import json

# These are user-selected discussion tasks, not a second research state machine.
# Completion and permission still come exclusively from the project workflow.
STEP_AREAS = {"conditions": "overview", "exploration": "overview", "reading": "literature",
              "synthesis": "literature", "question": "question", "design": "study"}
DISCUSSION_STEPS = (
    ("conditions", "明确兴趣与条件", "先说清想研究什么、已知条件和仍不确定的部分。", "讨论兴趣与条件",
     "请结合我已说明的兴趣和条件，帮我缩小研究范围；没有确定的条件保持待确认。"),
    ("exploration", "开始选题探索", "比较少量可研究的切入方向，暂不把方向当成正式研究问题。", "开始选题探索",
     "我们开始选题探索。请结合已有条件和资料比较少量合适的切入方向，说明需要哪些论文依据；先不采纳问题或生成完整方案。"),
    ("reading", "检索入库与论文阅读", "调用原有有界检索，把资料存入文献库，再按实际摘要或正文范围阅读。", "阅读已有论文",
     "开始论文阅读。请优先阅读本项目已有摘要和可用正文片段，比较研究对象、方法、条件与局限，引用真实来源并说明阅读范围；不重复检索。"),
    ("synthesis", "研究现状与 research gap", "复用已保存的阅读材料和整理报告，比较已有工作与待核查的研究缺口。", "整理研究现状与缺口",
     "现在整理研究现状和 research gap。请复用本项目已有论文和报告，区分文献明确的局限、跨论文比较及待验证设想，说明证据缺口；不重新检索、不声称已证实创新。"),
    ("question", "讨论并确认研究问题", "把方向收窄成一个能设计方法回答的问题；候选须由你明确采纳。", "讨论候选研究问题",
     "请基于已读材料、研究缺口和我的条件，提出少量有区别的候选研究问题，解释范围、依据与可行性。先给草案，等我明确选择后再确认。"),
    ("design", "形成并审阅研究方案", "围绕已确认问题和论文依据组织方法，审阅修改后明确采纳、下载。", "准备研究方案请求", ""),
)


def saved_reading_context(chat, readable_ids):
    """Reuse two saved task reports as an index; original excerpts are evidence.

    No table creation/migration, no execution, no excluded paper as evidence.
    Reports and papers are project-shared, whereas chat history stays local.
    """
    with chat._connection() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sra_research_tasks'").fetchone():
            return []
        rows=conn.execute(
            "SELECT r.task_id,r.topic,r.status,r.report FROM sra_research_tasks r "
            "JOIN sra_conversations c ON c.conversation_id=r.conversation_id AND c.project_id=r.project_id "
            "WHERE r.project_id=? AND r.report IS NOT NULL AND r.report!='null' "
            "ORDER BY r.created_at DESC,r.task_id DESC LIMIT 2", (chat.project_id,)
        ).fetchall()
    allowed=set(readable_ids)
    result=[]
    for row in rows:
        try:
            report=json.loads(row["report"])
        except (TypeError,ValueError):
            continue
        if not isinstance(report,dict):
            continue
        items = report.get("limitations")
        limitations=[{"point":str(item.get("point", ""))[:500],"paper_ids":item["paper_ids"]}
                     for item in (items if isinstance(items,list) else []) if isinstance(item,dict)
                     and isinstance(item.get("paper_ids"),list) and item["paper_ids"]
                     and all(isinstance(pid,str) for pid in item["paper_ids"])
                     and set(item["paper_ids"])<=allowed][:4]
        result.append({"task_id":row["task_id"],"topic":row["topic"],"status":row["status"],
                       "limitations_index":limitations,
                       "notice":"已保存报告只是阅读索引；具体论断仍须核对本次提供的原论文摘要/片段。未纳入资料不能作为依据，不代表已阅读全文。"})
    return result


def build_research_route(context, text):
    from core.workspace.guidance_policy import is_paused
    from core.workspace.plans import method_hint

    records = context.get("records", [])
    focus = context.get("research_mainline", {}).get("focus", {})
    questions = [r for r in records if r["kind"] == "question"]
    question = next((r for r in questions if r["item_id"] == focus.get("question")), None)
    if question is None and len(questions) == 1:
        question = questions[0]
    papers = context.get("papers", [])
    readable = [p for p in papers if p.get("abstract", "").strip() or p.get("excerpts")]
    topic = context.get("topic", "").strip()
    broad = topic.lower() in {"ai", "人工智能", "计算机", "材料", "新研究", "研究", "待明确主题的研究", ""} or topic.startswith('待明确主题的研究 ·')
    paused = is_paused(text)
    no_search = bool(re.search(r"(?:不要|不必|暂不|先不|不用|别).{0,5}(?:检索|搜索|找论文|查论文|联网)", text))
    reading_only = bool(re.search(r"(?:只|先).{0,5}(?:解释|阅读|读|看).{0,8}(?:概念|上传|已有|这篇|论文|材料)", text))
    no_options = bool(re.search(r"(?:不要|不用|别).{0,5}(?:按钮|选项|卡片)", text))
    if paused:
        step, why = "paused", "用户要求暂停，不追加推进操作。"
    elif question and question.get("needs_review"):
        step, why = "review_basis", "先核对当前问题及其变化的来源，不跳过复核。"
    elif (context.get("current_plan") or {}).get("needs_review"):
        step, why = "review_basis", "现有方案或来源需要复核，先核对变化，不能把旧方案当作当前有效方案。"
    elif context.get("current_plan"):
        step, why = "review_plan", "已有正式方案，优先审阅、比较修改或导出，不重复从零选题。"
    elif broad and not papers and not question:
        step, why = "narrow_interest", "先选一个能理解的对象或应用，不要求用户写专业检索词或填完整条件表。"
    elif not readable:
        step, why = "collect_evidence", "系统应主动准备少量代表性文献的检索范围；元信息不足以分析方法与研究缺口。"
    elif not question:
        step, why = "read_and_gap", "先阅读已有摘要/片段，再提出有依据的待验证问题；不把证据不足直接称为创新。"
    else:
        step, why = "shape_method", "已有问题和可读材料，结合已知及未知条件讨论方法、比较/论证与可行性。"
    method, method_reason = method_hint(text + "\n" + (question or {}).get("content", "") + "\n" + topic)
    selected = context.get("conversation", {}).get("discussion_step")
    suggested = {"narrow_interest":"conditions", "collect_evidence":"reading", "read_and_gap":"reading",
                 "shape_method":"design", "review_plan":"design", "review_basis":"question", "paused":"conditions"}[step]
    if selected not in STEP_AREAS:
        selected = {"orientation":"conditions", "literature":"reading", "question":"question", "design":"design"}.get(
            context.get("conversation", {}).get("entry_stage"), suggested)
    steps=[]
    for sid,label,purpose,action_label,content in DISCUSSION_STEPS:
        action = "discuss"
        blocked = ""
        if sid == "reading" and not readable:
            action, action_label = "search", "检索论文并存入文献库"
        if sid in ("synthesis", "question") and not readable:
            blocked = "尚无可供内容分析的摘要或正文，请先检索入库或补充已有论文。"
        if sid == "design":
            action = "plan"
            if not question:
                blocked = "先从候选中明确采纳并选择一个研究问题；不会自动替你确认。"
            elif step == "review_basis":
                blocked = "当前问题或方案的依据需要复核，请先核对研究材料。"
        steps.append({"id":sid,"label":label,"purpose":purpose,"action":action,"action_label":action_label,
                      "content":content,"blocked_reason":blocked})
    pos = [s["id"] for s in steps].index(selected)
    next_step = steps[pos + 1] if pos + 1 < len(steps) else None
    return {
        "step": step,
        "why": why,
        "question_id": (question or {}).get("item_id"),
        "provided_readable_papers": len(readable),
        "provided_metadata_only": len(papers) - len(readable),
        "readable_paper_ids": [p["paper_id"] for p in readable],
        "suggested_method": method,
        "method_reason": method_reason,
        "search_topic": (question or {}).get("title") or topic,
        "offer_search": step == "collect_evidence" and not broad and not no_search and not reading_only and not no_options,
        "discussion_step": selected,
        "suggested_step": suggested,
        "steps": steps,
        "next_step": {"id":next_step["id"], "label":next_step["label"], "blocked_reason":next_step["blocked_reason"]} if next_step else None,
        "notice": "步骤仅控制本次讨论和明确操作，不代表研究已完成。正式问题与方案以已采纳材料为准。",
        "conditions": {k: {"value": v, "state": "confirmed" if v else "unknown"}
                       for k, v in context.get("brief", {}).items()},
        "user_statements": [m["content"][:600] for m in context.get("history", []) if m["role"] == "user"][-6:] + [text[:1000]],
    }


COACH_POLICY = """【系统负责的研究推进】
以 research_route 和正式材料为依据。research_route.discussion_step 是用户选择的本次讨论环节，必须围绕该环节答复；它不代表其他环节完成。steps 只用于理解阶段目的，不要把整条流程每轮重复打印。
conditions：明确兴趣与条件。exploration：比较少量选题切入点并提出需要核对的依据，不直接把宽泛方向写成正式问题。
reading：复用已入库论文和原有阅读能力，逐篇或比较阅读摘要/片段，不直接跳去完整方案。synthesis：复用已保存报告与内置研究缺口分析，解释研究现状和待核查 gap，不重新发起检索。question：有依据地细化候选，等待明确采纳。design：围绕已确认问题设计和审阅方案。
用户可通过步骤入口明确开始下一环节；不要要求用户自己组织工具调用，也不因切换步骤自动检索、采纳或启动整个流水线。原有检索可能已保存整理报告，进入现状阶段应复用而非重复生成相同产物。
【避免原地打转】结合本会话已回答的条件，不重复问同一个问题。当前步骤达到最小讨论条件时，用简短小结说明“已经得到什么、哪些仍待确认”，再明确建议用户选择下一个步骤，而不是继续发问卷。尚不确定的算力、期限等允许保留待确认，先讨论保守路线；只有会影响安全或证据有效性的缺口才阻止正式生成或采纳。给用户保留“继续讨论/修改范围”的选择，不因对话轮数自动确认完成。
推进底线：兴趣和对象足够明确即可建议选题探索；有可比较的方向即可建议检索入库并阅读；实际有摘要/片段才讨论论文方法和 gap；读过的材料足以提出有依据的候选即可建议确认问题；当前问题明确采纳且依据有效后才准备正式方案请求。没有摘要时说明缺口并给现有检索/补充入口，不能一直空聊，也不能杜撰论文结论。
先回答本轮问题，再说明本步得到什么、还缺什么，以及你建议代办的具体下一步；只让用户确认方向、必要条件与正式采纳，不让新手自己编排流程。
collect_evidence：主动说明需要补哪类代表性论文、拟比较什么；系统会提供可编辑的有界检索卡片，用户确认后由已有检索任务查找并入库。不要反复要求用户自己找论文、写检索式。未执行前不得说已经找到或读完。
read_and_gap：优先用已入库材料，按“研究对象/问题—方法与条件—已有结论（仅阅读范围内）—局限及未核实事项”比较少量最相关论文。每个具体判断引用实际 paper_id；只读摘要不扩写全文结论，只有元信息不分析方法或结果。
research gap 分开说：论文明确报告的局限、多个工作之间需核对的比较差异、系统提出的待验证设想。给少量候选问题，分别解释文献依据、与用户条件的关系、需要补查什么；‘本次没找到’不等于‘领域没人做’，不保证创新或可发表。不要只列通用的隐私/效率/鲁棒性名词。
shape_method：围绕已经确认的问题，以文献方法、用户已知条件、未知条件与差异设想共同推出方法选择；讨论数据/材料、对照或论证、评价、资源、局限。未知算力和时间不妨碍先比较保守路线，但不得编造确切预算或把讨论条件写成正式条件。不要重新让用户从零选题。
review_plan/review_basis：优先核对已有材料的变化与审阅需求，不重新确认已采纳草案。正式方案仍须用户明确请求生成，问题/方法有效才可生成，明确采纳后才正式保存。全程止于 DESIGN。
普通解释可不产生任何 suggestions/actions；只有已经形成可讨论的具体问题才给 question 草案，不为凑卡片过早让新手采纳宽泛方向。不要要求用户先完成实验才能得到方案。
"""
