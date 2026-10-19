/* Passive answer audit. No requests except the caller's canonical share fetcher;
 * never sends messages, changes a conversation, or treats an unchecked source as false.
 */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.ShareLinkGuardAudit = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';
  const MESSAGE_SELECTOR = '[data-message-author-role="user"],[data-message-author-role="assistant"],[data-user-message-bubble],[data-chatgpt-selection-message-id]';
  const UUID = '[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}';
  let requiredCore = null;
  try { if (typeof require === 'function') requiredCore = require('./core.js'); } catch (_) { /* browser */ }
  const normalizeText = value => String(value || '').replace(/\r\n?/g, '\n').replace(/\u00a0/g, ' ').trim();

  function canonicalUrl(value, core) {
    if (core && typeof core.normalizeShareUrl === 'function') return core.normalizeShareUrl(value);
    const match = typeof value === 'string' && value.match(new RegExp('^https://chatgpt\\.com/share/(' + UUID + ')/?$', 'i'));
    if (!match) throw new Error('需要有效的公开 ChatGPT 分享地址。');
    return 'https://chatgpt.com/share/' + match[1].toLowerCase();
  }
  function sharePageUrl(href, core) {
    try {
      const url = new URL(href);
      return url.origin === 'https://chatgpt.com' && url.pathname.startsWith('/share/') ? canonicalUrl(href, core) : null;
    } catch (_) { return null; }
  }
  function routeKey(href) {
    try { const url = new URL(href); return url.origin + url.pathname; }
    catch (_) { return String(href || ''); }
  }
  function canCarryContext(oldHref, newHref, requestText, messages) {
    let oldUrl, newUrl;
    try { oldUrl = new URL(oldHref); newUrl = new URL(newHref); } catch (_) { return false; }
    return oldUrl.origin === 'https://chatgpt.com' && newUrl.origin === oldUrl.origin && oldUrl.pathname === '/' &&
      /^\/c\/[^/]+\/?$/.test(newUrl.pathname) && Boolean(normalizeText(requestText)) &&
      messages.some(message => message.role === 'user' && normalizeText(message.text) === normalizeText(requestText));
  }

  function findLatestLinkedPair(messages, core) {
    for (let index = messages.length - 1; index >= 0; index--) {
      const user = messages[index];
      if (user.role !== 'user' || !/chatgpt\.com\/share\/|chat\.openai\.com\/share\//i.test(user.text || '')) continue;
      let links;
      try {
        links = core.findShareLinks(user.text || '');
        links = [...new Set(links.map(link => canonicalUrl(link.url, core)))];
      } catch (_) { return { error: 'INVALID_SOURCE_LINK', userIndex: index }; }
      if (links.length !== 1) return { error: links.length ? 'AMBIGUOUS_SOURCE_LINKS' : 'INVALID_SOURCE_LINK', userIndex: index };
      let reply = null, replyIndex = -1;
      for (let next = index + 1; next < messages.length && messages[next].role !== 'user'; next++) {
        if (messages[next].role === 'assistant' && normalizeText(messages[next].text)) { reply = messages[next]; replyIndex = next; }
      }
      if (!reply) return { pending: true, sourceUrl: links[0], userIndex: index };
      return { sourceUrl: links[0], userIndex: index, replyIndex, reply };
    }
    return null;
  }

  function visible(element, win) {
    if (!element || element.isConnected === false) return false;
    if (element.closest && element.closest('[hidden],[aria-hidden="true"]')) return false;
    if (element.getClientRects && !element.getClientRects().length) return false;
    if (win && win.getComputedStyle) {
      const style = win.getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
    }
    return true;
  }
  function messageRole(element) {
    const direct = element.getAttribute('data-message-author-role');
    if (direct === 'user' || direct === 'assistant') return direct;
    if (element.hasAttribute('data-user-message-bubble')) return 'user';
    const unit = element.closest('[data-chatgpt-search-unit-key]');
    const key = unit && unit.getAttribute('data-chatgpt-search-unit-key');
    if (key && /:assistant$/.test(key)) return 'assistant';
    if (key && /:user$/.test(key)) return 'user';
    if (element.hasAttribute('data-chatgpt-selection-message-id')) {
      const turn = element.closest('[data-turn-key]');
      if (turn && turn.querySelector('h4[data-conversation-role="assistant"]')) return 'assistant';
    }
    return null;
  }
  function textFromTree(element) {
    const blocks = new Set(['ADDRESS', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'DIV', 'DL', 'DT', 'DD', 'FIGCAPTION', 'FIGURE',
      'FOOTER', 'HEADER', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HR', 'LI', 'MAIN', 'OL', 'P', 'PRE', 'SECTION', 'TABLE', 'TR', 'UL']);
    function walk(node) {
      if (node.nodeType === 3) return node.nodeValue || '';
      const tag = String(node.tagName || '').toUpperCase();
      const attribute = name => node.getAttribute ? node.getAttribute(name) : null;
      if (['BUTTON', 'NAV', 'SCRIPT', 'STYLE', 'TEMPLATE'].includes(tag) || attribute('role') === 'button' ||
          (tag === 'H4' && attribute('data-conversation-role') !== null) || attribute('aria-hidden') === 'true' || node.hidden) return '';
      if (tag === 'BR') return '\n';
      let text = '';
      for (const child of node.childNodes || []) {
        const part = walk(child);
        if (!part) continue;
        const block = blocks.has(String(child.tagName || '').toUpperCase());
        if (block && text && !text.endsWith('\n')) text += '\n';
        text += part;
        if (block && !text.endsWith('\n')) text += '\n';
      }
      return text;
    }
    return walk(element).replace(/^\n+|\n+$/g, '');
  }
  function messageText(element) {
    // Skip controls by DOM identity, never by deleting a same-named line from
    // live text: an answer can legitimately contain the word “Copy/复制”.
    if (!element.querySelector('h4[data-conversation-role],button,[role="button"],nav')) return element.innerText || element.textContent || '';
    return textFromTree(element);
  }
  function domMessages(doc, win) {
    if (!doc || !doc.querySelectorAll) return [];
    const candidates = [...doc.querySelectorAll(MESSAGE_SELECTOR)].filter(element => visible(element, win))
      .map(element => ({ element, role: messageRole(element) })).filter(item => item.role);
    return candidates.filter(item => !candidates.some(other => other !== item && other.role === item.role && item.element.contains(other.element)))
      .map(item => ({ ...item, text: messageText(item.element), key: item.element.getAttribute('data-message-id') ||
        item.element.getAttribute('data-chatgpt-selection-message-id') || item.element.closest('[data-turn-key]')?.getAttribute('data-turn-key') || '' }));
  }
  function generating(doc, win, target) {
    const turn = target?.element?.closest('[data-talvt-turn-state]');
    const state = turn && turn.getAttribute('data-talvt-turn-state');
    if (state && state !== 'complete') return true;
    if (!doc || !doc.querySelectorAll) return false;
    return [...doc.querySelectorAll('button,[data-is-streaming="true"],[data-streaming="true"],[aria-busy="true"]')].some(element => {
      if (!visible(element, win)) return false;
      if (element.getAttribute('data-testid') === 'stop-button') return true;
      if (element.getAttribute('data-is-streaming') === 'true' || element.getAttribute('data-streaming') === 'true' || element.getAttribute('aria-busy') === 'true') return true;
      const label = (element.getAttribute('aria-label') || '').trim();
      return /^(?:stop|停止|中止)$/i.test(label) || /stop generating|stop streaming|停止生成|停止回答|停止响应|中止回答/i.test(label);
    });
  }

  function create(options) {
    const opts = options || {};
    if (typeof opts.fetchShare !== 'function') throw new TypeError('fetchShare is required');
    const win = opts.window || root.window || root;
    const doc = opts.document || win.document || root.document;
    const location = opts.location || win.location || root.location || { href: '' };
    const core = opts.core || root.ShareLinkGuardCore || requiredCore;
    if (!core || typeof core.findShareLinks !== 'function') throw new TypeError('ShareLinkGuardCore is required');
    const diagnostics = () => opts.diagnostics || root.ShareLinkGuardDiagnostics;
    const onResult = typeof opts.onResult === 'function' ? opts.onResult : () => {};
    const quietMs = Number.isFinite(opts.quietMs) ? Math.max(0, opts.quietMs) : 1200;
    const schedule = opts.setTimeout || root.setTimeout.bind(root);
    const unschedule = opts.clearTimeout || root.clearTimeout.bind(root);
    const every = opts.setInterval || root.setInterval.bind(root);
    const stopEvery = opts.clearInterval || root.clearInterval.bind(root);
    let started = false, disposed = false, context = null, report = null, phase = 'idle';
    let epoch = 0, task = 0, observedRoute = routeKey(location.href), timer = null, interval = null, observer = null;
    let pending = null, lastAudited = null, automaticShareRoute = null, manualTask = null;

    function stopPending() { if (timer !== null) unschedule(timer); timer = null; pending = null; }
    function snapshot() { return domMessages(doc, win); }
    function current(job) { return !disposed && job.epoch === epoch && job.task === task && job.route === routeKey(location.href); }
    function newJob(manual) { return { epoch, task: ++task, route: routeKey(location.href), manual: Boolean(manual) }; }
    function targetInfo(type, message, index) { return { type, pageUrl: routeKey(location.href), messageId: message?.key || message?.id || '', index,
      scope: type === 'share_reply' ? 'current_public_share_visible_text_reply' : 'current_page_visible_reply_only',
      completionEvidence: type === 'share_reply' ? 'fixed_public_snapshot' : 'visible_dom_signals_and_quiet_heuristic_only' }; }
    function output(status, summary, sourceUrl, target, manual, findings) {
      return { status, findings: findings || [], sourceUrl: sourceUrl || null, target: target || null, summary, manual: Boolean(manual) };
    }
    function publish(job, value) {
      if (!current(job)) return null;
      report = value;
      phase = 'idle';
      if (manualTask === job.task) manualTask = null;
      if (value.status !== 'not_applicable' || value.manual) {
        try { onResult(value); } catch (_) { /* UI callbacks must not restart or break audits. */ }
      }
      return value;
    }
    function unverified(job, code, summary, sourceUrl, target) {
      return publish(job, output('unverified', summary, sourceUrl, target, job.manual,
        [{ code, severity: 'info', message: summary }]));
    }
    function checkRoute() {
      const next = routeKey(location.href);
      if (next === observedRoute) return;
      const carry = context && canCarryContext(observedRoute, next, context.requestText, snapshot());
      stopPending(); epoch++; task++; manualTask = null; report = null; phase = 'idle'; lastAudited = null;
      if (!carry) context = null;
      observedRoute = next;
      automaticShareRoute = null;
    }
    function contextReply(messages) {
      if (!context) return null;
      let userIndex = -1;
      if (context.requestText) {
        userIndex = messages.findIndex(message => message.role === 'user' && !context.userBaseline.has(message.element) &&
          normalizeText(message.text) === context.requestText);
        if (userIndex < 0 && context.boundUser) userIndex = messages.findIndex(message => message.element === context.boundUser);
        if (userIndex < 0) return null;
        context.boundUser = messages[userIndex].element;
      }
      let reply = null, replyIndex = -1;
      for (let index = userIndex + 1; index < messages.length; index++) {
        const message = messages[index];
        if (message.role === 'user') {
          // Never apply this source to a later independent user turn.
          if (userIndex >= 0) break;
          continue;
        }
        if (message.role === 'assistant' && !context.assistantBaseline.has(message.element) && normalizeText(message.text)) {
          reply = message; replyIndex = index;
        }
      }
      return reply ? { reply, replyIndex } : null;
    }
    function analyze(job, reply, source, target) {
      if (!current(job)) return null;
      const engine = diagnostics();
      if (!engine || typeof engine.analyzeReply !== 'function') return unverified(job, 'DIAGNOSTICS_UNAVAILABLE', '检查模块尚未就绪，暂时无法核对。', source.url, target);
      try {
        const result = engine.analyzeReply(reply.text, source);
        if (!result || !['issues', 'review', 'no_mechanical_issue'].includes(result.status)) throw new Error('Unknown diagnostic result');
        return publish(job, output(result.status, result.summary, source.url, target, job.manual, result.findings || []));
      } catch (_) { return unverified(job, 'DIAGNOSTICS_FAILED', '机械核对未能完成，不能据此判断回答是否编造。', source.url, target); }
    }
    async function fetchChecked(url) {
      const canonical = canonicalUrl(url, core);
      const source = await opts.fetchShare(canonical);
      if (!source || !Array.isArray(source.messages) || !source.messages.length || canonicalUrl(source.url, core) !== canonical) throw new Error('来源结构或分享地址不一致');
      return source;
    }

    async function auditCurrentPage(settings) {
      const manual = !settings || settings.manual !== false;
      if (disposed) return null;
      checkRoute(); stopPending();
      const job = newJob(manual);
      if (manual) manualTask = job.task;
      phase = 'checking';
      const pageShare = sharePageUrl(location.href, core);
      if (pageShare) automaticShareRoute = job.route;
      let sourceUrl = null, target = null;
      try {
        if (pageShare) {
          const page = await fetchChecked(pageShare);
          if (!current(job)) return null;
          const pair = findLatestLinkedPair(page.messages, core);
          if (!pair) return publish(job, output('not_applicable', '当前分享快照中没有“用户给出分享链接后助手回答”的可核对配对。', null, null, manual));
          if (pair.error) return unverified(job, pair.error, '最近一条相关消息的分享来源无效或不唯一，不能选择原文。', null, null);
          if (pair.pending) return publish(job, output('not_applicable', '最近的分享链接后尚无可核对的助手回答。', pair.sourceUrl, null, manual));
          sourceUrl = pair.sourceUrl;
          target = targetInfo('share_reply', pair.reply, pair.replyIndex);
          if (sourceUrl === pageShare) return unverified(job, 'SELF_REFERENCE', '分享记录引用了它自己，不能用自引用验证回答。', sourceUrl, target);
          const source = await fetchChecked(sourceUrl);
          if (!current(job)) return null;
          return analyze(job, pair.reply, source, target);
        }
        const messages = snapshot();
        const live = context && contextReply(messages);
        if (context) {
          if (!live) return publish(job, output('not_applicable', '本次来源之后尚无新的可核对助手回复。', context.share.url, null, manual));
          target = targetInfo('live_reply', live.reply, live.replyIndex);
          if (generating(doc, win, live.reply)) return unverified(job, 'REPLY_IN_PROGRESS', '回答仍在生成，请完成后再检查。', context.share.url, target);
          return analyze(job, live.reply, context.share, target);
        }
        const pair = findLatestLinkedPair(messages, core);
        if (!pair) return publish(job, output('not_applicable', '当前可见聊天中没有找到分享链接及其后的助手回复；未读取虚拟列表之外的历史。', null, null, manual));
        if (pair.error) return unverified(job, pair.error, '最近一条相关消息的分享来源无效或不唯一，无法核对。', null, null);
        if (pair.pending) return publish(job, output('not_applicable', '最近的分享链接后尚无可核对的助手回复。', pair.sourceUrl, null, manual));
        sourceUrl = pair.sourceUrl; target = targetInfo('visible_chat', pair.reply, pair.replyIndex);
        if (generating(doc, win, pair.reply)) return unverified(job, 'REPLY_IN_PROGRESS', '回答仍在生成，请完成后再检查。', sourceUrl, target);
        const originalText = pair.reply.text;
        const source = await fetchChecked(sourceUrl);
        if (!current(job)) return null;
        const now = snapshot();
        const present = now.find(message => message.element === pair.reply.element && message.role === 'assistant');
        if (!present || present.text !== originalText || generating(doc, win, present)) return unverified(job, 'REPLY_CHANGED', '读取原文期间回答发生变化，本次未判断；请待回复完成后重试。', sourceUrl, target);
        return analyze(job, present, source, target);
      } catch (error) {
        return unverified(job, 'SOURCE_UNVERIFIED', '未能读取可核验的分享原文，不能判断回答是否编造。' + (error?.message ? ' ' + String(error.message).slice(0, 240) : ''), sourceUrl || pageShare, target);
      }
    }

    function tick() {
      if (disposed) return;
      checkRoute();
      const pageShare = sharePageUrl(location.href, core);
      if (pageShare && !context && automaticShareRoute !== observedRoute && manualTask === null) {
        automaticShareRoute = observedRoute;
        timer = schedule(() => { timer = null; void auditCurrentPage({ manual: false }); }, quietMs);
        return;
      }
      if (!context || manualTask !== null) return;
      const item = contextReply(snapshot());
      if (!item || generating(doc, win, item.reply)) { stopPending(); return; }
      if (lastAudited && lastAudited.element === item.reply.element && lastAudited.text === item.reply.text) return;
      if (pending && pending.element === item.reply.element && pending.text === item.reply.text) return;
      stopPending();
      const capturedEpoch = epoch, capturedContext = context;
      pending = { element: item.reply.element, text: item.reply.text };
      timer = schedule(() => {
        const expected = pending; timer = null; pending = null;
        if (!expected || disposed || epoch !== capturedEpoch || context !== capturedContext) return;
        const latest = contextReply(snapshot());
        if (!latest || latest.reply.element !== expected.element || latest.reply.text !== expected.text || generating(doc, win, latest.reply)) { tick(); return; }
        const job = newJob(false);
        lastAudited = expected;
        analyze(job, latest.reply, context.share, targetInfo('live_reply', latest.reply, latest.replyIndex));
      }, quietMs);
    }
    function setContext(share, settings) {
      if (disposed) return false;
      const url = canonicalUrl(share && share.url, core);
      if (!share || !Array.isArray(share.messages) || !share.messages.length) throw new TypeError('Verified source messages are required');
      checkRoute(); stopPending(); epoch++; task++; manualTask = null; report = null; phase = 'waiting'; lastAudited = null;
      const messages = snapshot();
      context = { share: { ...share, url }, requestText: normalizeText(settings && settings.requestText),
        userBaseline: new Set(messages.filter(message => message.role === 'user').map(message => message.element)),
        assistantBaseline: new Set(messages.filter(message => message.role === 'assistant').map(message => message.element)), boundUser: null };
      if (started) tick();
      return true;
    }
    function start() {
      if (disposed || started) return api;
      started = true;
      const Observer = opts.MutationObserver || win.MutationObserver || root.MutationObserver;
      if (Observer && doc) {
        observer = new Observer(tick);
        observer.observe(doc, { childList: true, characterData: true, subtree: true, attributes: true,
          attributeFilter: ['data-talvt-turn-state', 'data-is-streaming', 'data-streaming', 'aria-busy', 'hidden', 'aria-hidden', 'style', 'class'] });
      }
      interval = every(tick, 400);
      tick();
      return api;
    }
    function dispose() {
      if (disposed) return;
      disposed = true; epoch++; task++; stopPending();
      if (interval !== null) stopEvery(interval);
      if (observer) observer.disconnect();
      context = null; manualTask = null; phase = 'disposed';
    }
    const api = { start, setContext, auditCurrentPage, getState: () => {
      if (!disposed) checkRoute();
      return { phase, contextActive: Boolean(context), sourceUrl: context?.share.url || report?.sourceUrl || null, report };
    }, dispose };
    return Object.freeze(api);
  }

  return Object.freeze({ create, _test: Object.freeze({ normalizeText, canonicalUrl, sharePageUrl, routeKey, canCarryContext, findLatestLinkedPair, textFromTree }) });
});
