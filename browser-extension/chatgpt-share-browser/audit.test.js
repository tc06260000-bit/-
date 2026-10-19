'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const Audit = require('./audit.js');
const Core = require('./core.js');
const H = Audit._test;
const SOURCE = 'https://chatgpt.com/share/11111111-1111-4111-8111-111111111111';
const PAGE = 'https://chatgpt.com/share/22222222-2222-4222-8222-222222222222';
const OTHER = 'https://chatgpt.com/share/33333333-3333-4333-8333-333333333333';
const source = (url = SOURCE) => ({ url, messages: [
  { id: 'T0001', role: 'user', text: '真实问题。' }, { id: 'T0002', role: 'assistant', text: '原文回答。' }
] });
const linkedPage = (url = SOURCE) => ({ url: PAGE, messages: [
  { id: 'T0001', role: 'user', text: '请读 ' + url }, { id: 'T0002', role: 'assistant', text: '我读了十条消息。[T9999]' }
] });
const diagnostic = { analyzeReply: text => ({ status: text.includes('T9999') ? 'issues' : 'no_mechanical_issue',
  findings: [{ code: 'MECHANICAL_ONLY', severity: 'info', message: '不是无幻觉保证' }], summary: '机械核对结果' }) };
const defer = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const settle = () => new Promise(resolve => setImmediate(resolve));

function fakeElement(role, text) {
  const attributes = { 'data-message-author-role': role };
  return {
    isConnected: true, innerText: text, textContent: text, turnState: null,
    getAttribute: name => attributes[name] || null,
    hasAttribute: name => Object.hasOwn(attributes, name),
    getClientRects: () => [{}],
    closest(selector) {
      if (selector === '[data-talvt-turn-state]' && this.turnState) return { getAttribute: () => this.turnState };
      return null;
    },
    contains(other) { return other === this; },
    querySelector: () => null, querySelectorAll: () => [],
    cloneNode() { return this; }
  };
}
function environment(href = 'https://chatgpt.com/c/a') {
  const location = { href }, messages = [], controls = [], timers = new Map();
  let number = 0, callback = null;
  const doc = { querySelectorAll(selector) { return selector.includes('data-message-author-role') ? messages : controls; } };
  const env = { location, document: doc, window: { getComputedStyle: () => ({ display: 'block', visibility: 'visible' }) },
    core: Core, diagnostics: diagnostic, quietMs: 1200,
    setTimeout(fn) { const id = ++number; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); }, setInterval() { return 1; }, clearInterval() {},
    MutationObserver: class { constructor(fn) { callback = fn; } observe() {} disconnect() { callback = null; } }
  };
  return { env, location, messages, controls, timers,
    notify() { if (callback) callback(); },
    flush() { const work = [...timers.values()]; timers.clear(); for (const fn of work) fn(); }
  };
}

// Selection is one direct user turn and its last assistant, never recursion.
test('latest linked pair does not borrow a reply from the next user turn', () => {
  const messages = [{ role: 'user', text: SOURCE }, { role: 'user', text: '无关问题' }, { role: 'assistant', text: '无关答案' }];
  assert.equal(H.findLatestLinkedPair(messages, Core).pending, true);
});
test('latest linked user supersedes older sources and deduplicates the same URL', () => {
  const messages = [{ role: 'user', text: SOURCE }, { role: 'assistant', text: '旧答' },
    { role: 'user', text: PAGE + '\n' + PAGE }, { role: 'assistant', text: '中间' }, { role: 'assistant', text: '最后' }];
  const pair = H.findLatestLinkedPair(messages, Core);
  assert.equal(pair.sourceUrl, PAGE); assert.equal(pair.reply.text, '最后'); assert.equal(pair.replyIndex, 4);
});
test('ambiguous or malformed latest source cannot fall back to an older valid pair', () => {
  for (const latest of [SOURCE + ' ' + PAGE, 'chatgpt.com/share/broken']) {
    const pair = H.findLatestLinkedPair([{ role: 'user', text: OTHER }, { role: 'assistant', text: '旧答' },
      { role: 'user', text: latest }, { role: 'assistant', text: '新答' }], Core);
    assert.ok(pair.error); assert.equal(pair.sourceUrl, undefined);
  }
});
test('only home to a newly created chat with the exact request can carry context', () => {
  const messages = [{ role: 'user', text: '本次\n原文' }];
  assert.equal(H.canCarryContext('https://chatgpt.com/', 'https://chatgpt.com/c/new', '本次\n原文', messages), true);
  for (const [old, next, request] of [['/c/old', '/c/new', '本次\n原文'], ['/', '/c/new', '别的请求'], ['/', '/share/x', '本次\n原文']]) {
    assert.equal(H.canCarryContext('https://chatgpt.com' + old, 'https://chatgpt.com' + next, request, messages), false);
  }
});
test('page recognition stays on the canonical ChatGPT share origin', () => {
  assert.equal(H.sharePageUrl(PAGE + '?utm=test', Core), PAGE);
  for (const url of ['https://evil.test/share/x', 'https://chatgpt.com/c/x', 'https://chatgpt.com/share/no-id']) assert.equal(H.sharePageUrl(url, Core), null);
});

test('manual share audit fetches exactly current snapshot and one original source', async () => {
  const env = environment(PAGE), calls = [], results = [];
  const app = Audit.create({ ...env.env, onResult: value => results.push(value), fetchShare: async url => {
    calls.push(url); return url === PAGE ? linkedPage() : source();
  } });
  const result = await app.auditCurrentPage({ manual: true });
  assert.deepEqual(calls, [PAGE, SOURCE]); assert.equal(result.status, 'issues'); assert.equal(result.manual, true);
  assert.equal(result.sourceUrl, SOURCE); assert.equal(result.target.type, 'share_reply');
  assert.equal(result.target.scope, 'current_public_share_visible_text_reply'); assert.equal(results.length, 1);
  assert.equal(app.getState().report, result); app.dispose();
});
test('share audit never recursively follows a URL inside the original source', async () => {
  const env = environment(PAGE), calls = [];
  const original = source(); original.messages[0].text = '另一个链接 ' + OTHER;
  const app = Audit.create({ ...env.env, fetchShare: async url => { calls.push(url); return url === PAGE ? linkedPage() : original; } });
  await app.auditCurrentPage(); assert.deepEqual(calls, [PAGE, SOURCE]); app.dispose();
});
test('share self-reference is unverified and only fetches once', async () => {
  const env = environment(PAGE), calls = [];
  const app = Audit.create({ ...env.env, fetchShare: async url => { calls.push(url); return linkedPage(PAGE); } });
  const result = await app.auditCurrentPage(); assert.equal(result.status, 'unverified');
  assert.equal(result.findings[0].code, 'SELF_REFERENCE'); assert.deepEqual(calls, [PAGE]); app.dispose();
});
test('no linked pair is quiet in automatic mode but explained for manual inspection', async () => {
  const env = environment(PAGE), results = [];
  const app = Audit.create({ ...env.env, onResult: value => results.push(value), fetchShare: async () => source(PAGE) });
  app.start(); env.flush(); await settle();
  assert.equal(results.length, 0); assert.equal(app.getState().report.status, 'not_applicable');
  await app.auditCurrentPage({ manual: true }); assert.equal(results.length, 1); assert.equal(results[0].manual, true); app.dispose();
});
test('source fetch errors never become a fabrication verdict', async () => {
  for (const failAt of [PAGE, SOURCE]) {
    const env = environment(PAGE);
    const app = Audit.create({ ...env.env, fetchShare: async url => { if (url === failAt) throw new Error('403'); return linkedPage(); } });
    const result = await app.auditCurrentPage(); assert.equal(result.status, 'unverified');
    assert.equal(result.findings[0].code, 'SOURCE_UNVERIFIED'); app.dispose();
  }
});
test('a fetched source with the wrong identity is unverified', async () => {
  const env = environment(PAGE);
  const app = Audit.create({ ...env.env, fetchShare: async url => url === PAGE ? linkedPage() : source(OTHER) });
  const result = await app.auditCurrentPage(); assert.equal(result.status, 'unverified'); app.dispose();
});
test('older manual fetch completion cannot overwrite a newer manual result', async () => {
  const env = environment(PAGE), first = defer(), results = []; let calls = 0;
  const app = Audit.create({ ...env.env, onResult: value => results.push(value), fetchShare: async url => {
    if (url === PAGE && ++calls === 1) return first.promise;
    return url === PAGE ? linkedPage() : source();
  } });
  const oldJob = app.auditCurrentPage(); const newResult = await app.auditCurrentPage();
  first.resolve(linkedPage()); assert.equal(await oldJob, null); assert.deepEqual(results, [newResult]); app.dispose();
});
test('navigation invalidates an in-flight result even before the observer runs', async () => {
  const env = environment(PAGE), wait = defer(), results = [];
  const app = Audit.create({ ...env.env, onResult: value => results.push(value), fetchShare: async () => wait.promise });
  const work = app.auditCurrentPage(); env.location.href = 'https://chatgpt.com/c/different'; wait.resolve(linkedPage());
  assert.equal(await work, null); assert.equal(results.length, 0); assert.equal(app.getState().report, null); app.dispose();
});
test('setContext invalidates older manual work and clears its report', async () => {
  const env = environment(PAGE), wait = defer(), results = [];
  const app = Audit.create({ ...env.env, onResult: value => results.push(value), fetchShare: async () => wait.promise });
  const work = app.auditCurrentPage(); app.setContext(source(OTHER), { requestText: '新请求' }); wait.resolve(linkedPage());
  assert.equal(await work, null); assert.equal(results.length, 0); assert.equal(app.getState().sourceUrl, OTHER); app.dispose();
});
test('dispose prevents a pending result from reaching the UI', async () => {
  const env = environment(PAGE), wait = defer(), results = [];
  const app = Audit.create({ ...env.env, onResult: value => results.push(value), fetchShare: async () => wait.promise });
  const work = app.auditCurrentPage(); app.dispose(); wait.resolve(linkedPage());
  assert.equal(await work, null); assert.equal(results.length, 0); assert.equal(app.getState().phase, 'disposed');
});

test('setContext ignores old replies, requires its new user, and debounces one new reply', () => {
  const env = environment(), results = [];
  env.messages.push(fakeElement('user', '旧问题'), fakeElement('assistant', '旧回复[T9999]'));
  const app = Audit.create({ ...env.env, onResult: value => results.push(value), fetchShare: async () => source() });
  app.start(); app.setContext(source(), { requestText: '本次真实请求' }); env.notify(); env.flush(); assert.equal(results.length, 0);
  env.messages.push(fakeElement('user', '本次真实请求'), fakeElement('assistant', '新回复[T9999]'));
  env.notify(); assert.equal(results.length, 0); assert.equal(env.timers.size, 1);
  env.flush(); assert.equal(results.length, 1); assert.equal(results[0].target.type, 'live_reply');
  env.notify(); env.flush(); assert.equal(results.length, 1); app.dispose();
});
test('streaming turn waits until complete and text quiet debounce restarts', () => {
  const env = environment(), results = [];
  const app = Audit.create({ ...env.env, onResult: value => results.push(value), fetchShare: async () => source() });
  app.start(); app.setContext(source(), { requestText: '请求' });
  const reply = fakeElement('assistant', '部分'); reply.turnState = 'streaming';
  env.messages.push(fakeElement('user', '请求'), reply); env.notify(); env.flush(); assert.equal(results.length, 0);
  reply.turnState = 'complete'; env.notify(); assert.equal(env.timers.size, 1);
  reply.innerText = '最终[T9999]'; env.notify(); assert.equal(env.timers.size, 1);
  env.flush(); assert.equal(results.length, 1); assert.equal(results[0].status, 'issues'); app.dispose();
});
test('same-chat later user turn cannot bind to an earlier source', () => {
  const env = environment(), results = [];
  const app = Audit.create({ ...env.env, onResult: value => results.push(value), fetchShare: async () => source() });
  app.start(); app.setContext(source(), { requestText: '请求' });
  env.messages.push(fakeElement('user', '请求'), fakeElement('user', '后来别的问题'), fakeElement('assistant', '不相关[T9999]'));
  env.notify(); env.flush(); assert.equal(results.length, 0); app.dispose();
});
test('cross-chat navigation clears source while a confirmed home-to-chat transition carries it', () => {
  for (const allowed of [false, true]) {
    const env = environment(allowed ? 'https://chatgpt.com/' : 'https://chatgpt.com/c/old');
    const app = Audit.create({ ...env.env, fetchShare: async () => source() }); app.start(); app.setContext(source(), { requestText: '请求' });
    env.messages.push(fakeElement('user', '请求')); env.location.href = 'https://chatgpt.com/c/new'; env.notify();
    assert.equal(app.getState().contextActive, allowed); app.dispose();
  }
});
test('manual normal-chat audit selects only currently visible linked user and answer', async () => {
  const env = environment(), calls = [];
  env.messages.push(fakeElement('user', SOURCE), fakeElement('assistant', '回答[T9999]'));
  const app = Audit.create({ ...env.env, fetchShare: async url => { calls.push(url); return source(); } });
  const result = await app.auditCurrentPage(); assert.deepEqual(calls, [SOURCE]);
  assert.equal(result.status, 'issues'); assert.equal(result.target.scope, 'current_page_visible_reply_only'); app.dispose();
});
test('reply changing while manual source fetch runs produces unverified', async () => {
  const env = environment(), wait = defer(); const reply = fakeElement('assistant', '第一版');
  env.messages.push(fakeElement('user', SOURCE), reply);
  const app = Audit.create({ ...env.env, fetchShare: async () => wait.promise });
  const work = app.auditCurrentPage(); reply.innerText = '发生变化'; wait.resolve(source());
  const result = await work; assert.equal(result.status, 'unverified'); assert.equal(result.findings[0].code, 'REPLY_CHANGED'); app.dispose();
});

test('structural text extraction skips the button but preserves an identical real Copy line', () => {
  const text = value => ({ nodeType: 3, nodeValue: value });
  const element = (tagName, children, attributes = {}) => ({ nodeType: 1, tagName, childNodes: children,
    getAttribute: name => Object.hasOwn(attributes, name) ? attributes[name] : null });
  const tree = element('DIV', [element('H4', [text('ChatGPT 说：')], { 'data-conversation-role': 'assistant' }),
    element('P', [text('Copy')]), element('BUTTON', [text('Copy')]),
    element('P', [text('[T0001] 原句'), element('BR', []), text('第二行。')])]);
  assert.equal(H.textFromTree(tree), 'Copy\n[T0001] 原句\n第二行。');
});
test('plain Chinese and English stop labels prevent auditing a partial reply', () => {
  for (const label of ['停止', 'Stop']) {
    const env = environment(), results = [];
    const control = fakeElement('', '');
    control.getAttribute = name => name === 'aria-label' ? label : null;
    env.controls.push(control);
    const app = Audit.create({ ...env.env, onResult: value => results.push(value), fetchShare: async () => source() });
    app.start(); app.setContext(source(), { requestText: '请求' });
    env.messages.push(fakeElement('user', '请求'), fakeElement('assistant', '半句[T9999]'));
    env.notify(); env.flush(); assert.equal(results.length, 0);
    env.controls.length = 0; env.notify(); env.flush(); assert.equal(results.length, 1); app.dispose();
  }
});
