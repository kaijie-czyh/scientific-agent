/* Run with: node tests/test_conversation_product.js. No browser or dependencies. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../web/static/conversation-home.js'), 'utf8');
const documentState = {activeElement: null};

// A small DOM stand-in exercises the production controllers and their network
// boundaries; live browser acceptance covers layout and native dialog behavior.
class Element {
    constructor(tag, cls = '', text = '') {
        this.tag = tag; this.className = cls; this._text = text; this.children = [];
        this.dataset = {}; this.events = {}; this.isConnected = true;
        this.classList = {toggle() {}};
    }
    append(...children) { for(const child of children)child.parentElement = this; this.children.push(...children); }
    replaceChildren(...children) { this.children = children; this._text = ''; }
    setAttribute() {}
    addEventListener(name, callback) { this.events[name] = callback; }
    querySelector(selector) { return this.all().find(n => ['details', 'summary'].includes(selector) ? n.tag === selector : selector === '[data-teaching="directory"]' && n.dataset.teaching === 'directory') || null; }
    contains(element) { return this === element || this.all().includes(element); }
    all() { return this.children.flatMap(n => [n, ...n.all()]); }
    get textContent() { return this._text + this.children.map(n => n.textContent).join('\n'); }
    set textContent(text) { this._text = text; this.children = []; }
    focus() { this.focused = true; documentState.activeElement = this; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    remove() { this.isConnected = false; if(this.parentElement)this.parentElement.children = this.parentElement.children.filter(n => n !== this); }
    dispatchEvent(event) { this.events[event.type]?.(event); }
}
const E = (tag, cls, text) => new Element(tag, cls, text);
const button = (text, callback, cls) => { const n = E('button', cls, text); n.onclick = callback; return n; };
const notices = [], calls = [], closedPanels = [];
let response, failure;
const context = vm.createContext({
    E, button, URLSearchParams, Event, document: documentState,
    current: v => v.active !== false,
    request: async (url, options = {}) => { calls.push({url, options}); if(failure)throw failure; return response; },
    notice: (v, text) => notices.push(text),
    panel: (...args) => { closedPanels.push(args); args[0].r.panel.hidden = !args[1]; },
    saveDraft: v => { v.saved = v.r.input.value; },
    openMaterial() {},
});
const functions = [
    source.slice(source.indexOf('    function runtimeBlocked('), source.indexOf('    async function capabilities(')),
    source.slice(source.indexOf('    function planQuality('), source.indexOf('    function planReview(')),
    source.slice(source.indexOf('    async function preparePlanInput('), source.indexOf('    async function chooseReply(')),
].join('\n');
vm.runInContext(functions + '\nthis.controllers={runtimeBlocked,allowGeneration,loadRuntime,planQuality,preparePlanInput};', context);
// paintHeader belongs to the full app; the tested refresh must call it without
// navigating, generating, or depending on unrelated project-list DOM.
context.paintHeader = () => {};
const c = context.controllers;
const view = () => ({
    pid: 'p1', cid: 'c1', material: {}, reads: new AbortController(),
    root: E('section'), r: {runtime: E('section'), panel: Object.assign(E('aside'), {hidden:true}), input: Object.assign(E('textarea'), {value: '', maxLength: 6000}), planPending: E('p'), planTools: E('details'), planMethod: {}, planQuestion: {}},
});
const find = (root, text) => root.all().find(n => n.tag === 'button' && n.textContent === text);
const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
    let v = view();
    v.runtime = {mode: 'rules', status: 'rules_mode', checks: [{status: 'missing_credentials'}]};
    assert.equal(await c.allowGeneration(v), true, 'missing model credentials cannot block rule guidance');
    v.runtime = {mode: 'model', status: 'needs_configuration', checks: [{task_type: 'literature_search', status: 'missing_credentials'}]};
    assert.equal(await c.allowGeneration(v), false);
    assert.match(notices.at(-1), /草稿已保留/);
    assert.equal(calls.length, 0, 'a known configuration failure must not send any request');
    v.runtime.checks.push({task_type: 'research_synthesize', status: 'configured_unverified'});
    assert.equal(await c.allowGeneration(v), true, 'a partial configuration failure cannot block configured functionality');
    v.runtime.checks.pop();
    v.material.teaching = {};
    assert.equal(await c.allowGeneration(v), true, 'teaching is independent of model configuration');
    v.material = {}; v.runtime = {mode: 'model', status: 'configured_unverified'};
    assert.equal(await c.allowGeneration(v), true, 'configured means permitted to try, never verified success');

    v = view(); response = {schema_version: 'runtime-status.v1', mode: 'rules', status: 'rules_mode', title: '离线规则指导', message: '未调用模型', actions: [], checks: []};
    await c.loadRuntime(v);
    assert.equal(v.runtime.status, 'rules_mode');
    assert.equal(calls.at(-1).url, '/api/runtime-status');
    assert.equal(calls.at(-1).options.method, undefined, 'status refresh is GET only');
    assert.equal(v.r.input.value, '');
    find(v.r.runtime, '重新读取配置').focus();
    await c.loadRuntime(v);
    assert.equal(documentState.activeElement.tag, 'summary', 'refresh keeps keyboard focus in the replacement control');

    v.r.input.value = '我的未发送草稿'; v.planRequest = {question_id: 'q1'};
    const originalRequest = v.planRequest;
    let preparation = c.preparePlanInput(v, '核对引用', null);
    find(v.root, '保留草稿，暂不追加').onclick();
    assert.equal(await preparation, false);
    assert.equal(v.r.input.value, '我的未发送草稿');
    assert.equal(v.planRequest, originalRequest);
    preparation = c.preparePlanInput(v, '核对引用', null);
    v.root.children.at(-1).all().find(n => n.tag === 'button' && n.textContent === '保留并追加').onclick();
    assert.equal(await preparation, true);
    assert.equal(v.r.input.value, '我的未发送草稿\n\n核对引用');
    assert.equal(v.planRequest, null, 'explicitly confirmed discussion clears generation metadata');
    assert.equal(v.saved, v.r.input.value);
    v.r.input.maxLength = v.r.input.value.length;
    const saved = v.r.input.value;
    preparation = c.preparePlanInput(v, '更多要求', {question_id: 'q2'});
    v.root.children.at(-1).all().find(n => n.tag === 'button' && n.textContent === '保留并追加').onclick();
    assert.equal(await preparation, false);
    assert.equal(v.r.input.value, saved, 'oversized preparation cannot replace an existing draft');
    assert.equal(v.planRequest, null);

    const leaving = view(); leaving.r.input.value = '切换前的草稿';
    preparation = c.preparePlanInput(leaving, '补充要求', null);
    assert.equal(await c.preparePlanInput(leaving, '重复点击', null), false, 'only one confirmation is allowed at a time');
    leaving.active = false; leaving.reads.abort();
    assert.equal(await preparation, false, 'navigation cancels pending preparation');
    assert.equal(leaving.r.input.value, '切换前的草稿');
    assert.equal(leaving.preparingPlan, false);
    assert.equal(leaving.root.children.length, 0);

    v = view();
    response = {status: 'needs_attention', label: '有待补充或复核项', checks: [
        {code: 'structure', label: '方案结构', status: 'pass', detail: '已有结构化方案'},
        {code: 'conditions', label: '研究条件', status: 'attention', detail: '请确认设备'},
    ], evidence: [{statement: '材料性质陈述', title: '来源', paper_id: 'paper1', location: '摘要（未核对全文）', quote: '1 mS/cm at 25 °C', matched: true}],
    condition_details: [{field: 'resources', label: '资源／设备', value: '尚未提供仪器', basis: 'unverified'}], unconfirmed: ['确认温度'], review_questions: ['引文是否支持陈述？'], notice: '不证明科学正确性',
    material_review: [{statement: '材料性质陈述', source_matched: true, quantities: ['1 mS/cm'], temperatures: ['25 °C'], condition_mentions: [], evidence_type: 'experimental', review_points: ['核对测量条件']}], material_notice: '仅为原文线索'};
    const review = E('details'); review.open = true;
    const quality = c.planQuality(v, {item_id: 'plan1', title: '待审方案'}, false, review);
    review.events.toggle(); await flush();
    assert.match(quality.textContent, /优先核对 1 项/);
    assert.match(quality.textContent, /尚未由你确认/);
    assert.match(quality.textContent, /1 mS\/cm/);
    assert.match(quality.textContent, /摘要（未核对全文）/);
    const beforeDiscussion = calls.length;
    v.r.panel.hidden = false;
    v.r.input.value = '保留我的草稿';
    preparation = find(quality, '讨论这一项').onclick();
    v.root.children.at(-1).all().find(n => n.tag === 'button' && n.textContent === '保留草稿，暂不追加').onclick();
    await preparation;
    assert.equal(v.r.input.value, '保留我的草稿');
    assert.equal(closedPanels.length, 0, 'cancelled preparation must keep the reader open');
    preparation = find(quality, '讨论这一项').onclick();
    v.root.children.at(-1).all().find(n => n.tag === 'button' && n.textContent === '保留并追加').onclick();
    await preparation;
    assert.match(v.r.input.value, /^保留我的草稿\n\n请和我核对方案“待审方案”/);
    assert.equal(calls.length, beforeDiscussion, 'preparing a discussion cannot send or generate');
    assert.equal(closedPanels.length, 1);
    failure = new Error('offline');
    await find(quality, '重新核对').onclick();
    assert.match(quality.textContent, /保留上次检查/);
    assert.match(quality.textContent, /1 mS\/cm/, 'failed refresh retains the previous report');
    assert.equal(find(quality, '重新核对').disabled, false, 'retry remains actionable after failure');
    assert.ok(calls.every(call => call.options.method === undefined), 'all review and readiness operations must stay read-only');
    console.log('Conversation product behavior checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
