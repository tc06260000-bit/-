/* Browser-only integration. No servers, keys, telemetry, or persistent storage. */
(function () {
  "use strict";
  if (globalThis.ShareLinkGuardController) return;
  const Core = globalThis.ShareLinkGuardCore;
  if (!Core || location.hostname !== "chatgpt.com" || window.top !== window) return;

  const MAX_HTML_BYTES = 12 * 1024 * 1024;
  const MAX_PROMPT_CHARS = 60000;
  const FETCH_TIMEOUT_MS = 30000;
  const EDITOR_SELECTOR = [
    "#prompt-textarea",
    '[data-testid="prompt-textarea"]',
    'form[data-chatgpt-composer] [contenteditable="true"][role="textbox"]',
    'form[data-type="unified-composer"] [contenteditable="true"][role="textbox"]',
    '[data-lexical-editor="true"][contenteditable="true"][role="textbox"]',
    '[data-composer-markdown][contenteditable="true"]',
    '[contenteditable="true"][role="textbox"]',
    '[contenteditable="plaintext-only"][role="textbox"]',
    'textarea[aria-label="Message ChatGPT"]',
    'textarea[aria-label="与 ChatGPT 聊天"]',
    'textarea[placeholder="有问题，尽管问"]'
  ].join(",");
  const SEND_LABELS = new Set(["发送", "发送消息", "发送提示", "send", "send message", "send prompt"]);
  const state = {
    phase: "idle", revision: 0, writing: false, transaction: null,
    replayWindow: null, prepared: null, lastExport: null, notice: "",
    protected: false, counter: 0, noticeUntil: 0, noticeToken: 0, lastAudit: null
  };
  let statusElement;
  let statusText;
  let downloadButton;
  let noticeTimer;
  let audit = null;
  let refreshing = false;

  function visible(element) {
    return Boolean(element && element.isConnected && !element.closest('[hidden], [inert], [aria-hidden="true"]') && element.getClientRects().length &&
      getComputedStyle(element).visibility !== "hidden" && getComputedStyle(element).display !== "none");
  }

  function editorCandidate(element) {
    return visible(element) && (element instanceof HTMLTextAreaElement || element.isContentEditable) &&
      !element.disabled && !element.readOnly &&
      !element.closest('[data-message-author-role], [data-user-message-bubble], [data-chatgpt-selection-message-id], [data-testid^="conversation-turn"], [data-chatgpt-search-unit-key]');
  }

  function composer() {
    const matches = [...document.querySelectorAll(EDITOR_SELECTOR)].filter(editorCandidate);
    return matches.length === 1 ? matches[0] : null;
  }

  function readText(element) {
    if (!element) return "";
    return element instanceof HTMLTextAreaElement ? element.value : element.innerText;
  }

  function normalized(text) {
    return String(text).replace(/\r\n/g, "\n").replace(/\u00a0/g, " ").trim();
  }

  function scopeOf(editor) {
    if (!editor) return null;
    const known = editor.closest('form, [data-chatgpt-composer], [data-type="unified-composer"], [data-testid="composer"]');
    if (known && known !== editor) return known;
    let parent = editor.parentElement;
    for (let level = 0; parent && level < 8 && parent !== document.body; level++, parent = parent.parentElement) {
      const buttons = [...parent.querySelectorAll("button")].filter(button => visible(button) && isSendButton(button));
      if (!buttons.length) continue;
      const editors = [...parent.querySelectorAll(EDITOR_SELECTOR)].filter(editorCandidate);
      return buttons.length === 1 && editors.length === 1 && editors[0] === editor ? parent : null;
    }
    return null;
  }

  function isSendButton(button) {
    if (!(button instanceof HTMLButtonElement)) return false;
    const label = (button.getAttribute("aria-label") || "").trim().toLowerCase();
    if (/stop|停止|中止/.test(label) || button.getAttribute("data-testid") === "stop-button") return false;
    return button.getAttribute("data-testid") === "send-button" || button.id === "composer-submit-button" || SEND_LABELS.has(label);
  }

  function sendButton(editor) {
    const scope = scopeOf(editor);
    if (!scope) return null;
    const buttons = [...scope.querySelectorAll("button")].filter(button => visible(button) && isSendButton(button));
    return buttons.length === 1 ? buttons[0] : null;
  }

  function hasAttachments(editor) {
    const scope = scopeOf(editor);
    if (!scope) return true;
    if ([...scope.querySelectorAll('input[type="file"]')].some(input => input.files?.length)) return true;
    if (scope.querySelector("img,video,audio,canvas,iframe")) return true;
    return Boolean(scope.querySelector([
      '[data-testid="file-thumbnail"]', '[data-testid="composer-attachment"]',
      '[data-testid="attachment"]', '[data-testid*="attachment-preview"]',
      '[data-testid*="file-preview"]', '[data-file-id]', 'img[src^="blob:"]'
    ].join(",")));
  }

  function mountStatus() {
    if (statusElement?.isConnected || !document.body) return;
    statusElement = document.createElement("div");
    statusElement.id = "share-link-guard-status";
    statusElement.setAttribute("role", "status");
    statusElement.setAttribute("aria-live", "polite");
    statusElement.hidden = true;
    statusElement.style.cssText = "display:none;position:fixed;right:16px;top:72px;max-width:min(440px,85vw);z-index:2147483647;padding:12px 38px 12px 14px;border:1px solid #64748b;border-radius:10px;background:#0f172a;color:#f8fafc;font:13px/1.6 system-ui,sans-serif;box-shadow:0 2px 12px #0003;white-space:normal;";
    statusText = document.createElement("span");
    downloadButton = document.createElement("button");
    downloadButton.type = "button";
    downloadButton.textContent = "保存已提取原文";
    downloadButton.hidden = true;
    downloadButton.style.cssText = "display:block;margin-top:5px;border:1px solid #94a3b8;border-radius:5px;padding:3px 7px;background:#1e293b;color:#f8fafc;cursor:pointer;";
    downloadButton.addEventListener("click", () => {
      checkPage();
      if (!state.lastExport) return;
      const blob = new Blob([state.lastExport], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "chatgpt-share-source.txt";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "×";
    close.setAttribute("aria-label", "关闭提示");
    close.style.cssText = "position:absolute;top:5px;right:7px;border:0;background:transparent;color:inherit;cursor:pointer;font:20px system-ui;";
    close.addEventListener("click", dismissNotice);
    statusElement.append(statusText, downloadButton, close);
    document.body.appendChild(statusElement);
  }

  function refreshStatus() {
    if (refreshing) return;
    refreshing = true;
    try {
      mountStatus();
      const editor = composer();
      state.protected = Boolean(editor && sendButton(editor));
      // Missing a composer is normal on a share/read-only page. Readiness is
      // available in the toolbar popup; idle pages never get a permanent toast.
      const show = Boolean(state.notice && (state.noticeUntil === Infinity || Date.now() < state.noticeUntil));
      if (statusText && statusText.textContent !== state.notice) statusText.textContent = state.notice;
      if (statusElement) {
        statusElement.hidden = !show;
        statusElement.style.display = show ? "block" : "none";
      }
      if (downloadButton) {
        downloadButton.hidden = !state.lastExport;
        downloadButton.style.display = state.lastExport ? "block" : "none";
      }
    } finally { refreshing = false; }
  }

  function dismissNotice() {
    clearTimeout(noticeTimer);
    state.notice = "";
    state.noticeUntil = 0;
    state.noticeToken++;
    refreshStatus();
  }

  function notice(message, options = {}) {
    clearTimeout(noticeTimer);
    state.notice = message;
    const duration = options.persistent ? Infinity : (options.duration ?? 8000);
    state.noticeUntil = duration === Infinity ? Infinity : Date.now() + duration;
    const token = ++state.noticeToken;
    if (duration !== Infinity) noticeTimer = setTimeout(() => {
      if (state.noticeToken !== token) return;
      state.notice = "";
      state.noticeUntil = 0;
      refreshStatus();
    }, duration);
    refreshStatus();
  }

  function stopEvent(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }

  function cancel(reason) {
    const transaction = state.transaction;
    if (!transaction) return;
    state.transaction = null;
    transaction.controller.abort();
    state.phase = "idle";
    notice(reason || "输入或聊天页面已改变，已取消读取；未自动发送。");
  }

  function stillCurrent(transaction, expectedText = transaction.originalText) {
    return state.transaction === transaction && transaction.editor.isConnected &&
      composer() === transaction.editor && location.href === transaction.href &&
      state.revision === transaction.revision && !hasAttachments(transaction.editor) &&
      normalized(readText(transaction.editor)) === normalized(expectedText);
  }

  function invalidated(transaction, expectedText) {
    if (stillCurrent(transaction, expectedText)) return false;
    if (state.transaction === transaction) cancel();
    return true;
  }

  async function readResponse(response, controller) {
    const declared = Number(response.headers.get("content-length") || 0);
    if (declared > MAX_HTML_BYTES) throw new Error("分享页超过读取上限，未发送。");
    if (!response.body?.getReader) {
      const text = await response.text();
      if (new TextEncoder().encode(text).length > MAX_HTML_BYTES) throw new Error("分享页超过读取上限，未发送。");
      return text;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8", { fatal: true });
    const pieces = [];
    let bytes = 0;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > MAX_HTML_BYTES) {
        controller.abort();
        throw new Error("分享页超过读取上限，未发送。");
      }
      pieces.push(decoder.decode(value, { stream: true }));
    }
    pieces.push(decoder.decode());
    return pieces.join("");
  }

  async function getShare(url, transaction) {
    const response = await fetch(url, {
      method: "GET", credentials: "same-origin", redirect: "error",
      referrerPolicy: "no-referrer", cache: "no-store",
      headers: { Accept: "text/html,application/xhtml+xml" },
      signal: transaction.controller.signal
    });
    if (!response.ok) throw new Error(`分享页返回 HTTP ${response.status}，未发送。`);
    const finalUrl = response.url ? Core.normalizeShareUrl(response.url) : url;
    if (finalUrl !== url) throw new Error("页面转到了其他地址，无法核对来源，未发送。");
    const type = response.headers.get("content-type") || "";
    if (!/^(text\/html|application\/xhtml\+xml)(?:;|$)/i.test(type)) throw new Error("分享链接未返回可核对的网页，未发送。");
    return Core.parseShareHtml(await readResponse(response, transaction.controller), { url });
  }

  async function fetchForAudit(url) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    try { return await getShare(Core.normalizeShareUrl(url), { controller }); }
    catch (error) {
      throw new Error(String(error?.message || error).replace(/[，,]?\s*未发送[。.]?/g, ""));
    }
    finally { clearTimeout(timer); }
  }

  function writeComposer(editor, text) {
    editor.focus();
    if (editor instanceof HTMLTextAreaElement) {
      const descriptor = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
      descriptor.set.call(editor, text);
      editor.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
      editor.dispatchEvent(new Event("change", { bubbles: true }));
    } else {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(editor);
      selection.removeAllRanges();
      selection.addRange(range);
      // Use the browser editing path so ProseMirror/Lexical receives a native
      // edit event. Never replace innerHTML behind the editor framework's back.
      if (!document.execCommand("insertText", false, text)) throw new Error("当前编辑器不支持安全填入原文，未自动发送。");
    }
  }

  function pause(milliseconds) { return new Promise(resolve => setTimeout(resolve, milliseconds)); }

  async function replayPrepared(transaction, prompt, messageCount) {
    if (invalidated(transaction, prompt)) return;
    let button;
    for (let attempt = 0; attempt < 20; attempt++) {
      if (invalidated(transaction, prompt)) return;
      button = sendButton(transaction.editor);
      if (button && !button.disabled && button.getAttribute("aria-disabled") !== "true") break;
      await pause(50);
      button = null;
    }
    if (!button) throw new Error("原文已填入，但发送按钮未就绪；未自动发送，也不会自动重试。");
    state.prepared = { text: prompt, editor: transaction.editor, href: location.href };
    state.phase = "replaying";
    if (audit && transaction.share) {
      audit.setContext(transaction.share, { requestText: prompt });
      state.lastAudit = null;
    }
    state.replayWindow = { button, editor: transaction.editor, text: prompt, href: location.href };
    notice(`已核对本次分享的 ${messageCount} 条文字，正在提交原文。`);
    try {
      // This permit exists only within this synchronous call stack. It cannot
      // accidentally allow a later user event or another message.
      button.click();
    } finally {
      state.replayWindow = null;
    }
    if (state.counter !== transaction.id) return;
    state.transaction = null;
    const draftRemains = transaction.editor.isConnected &&
      normalized(readText(transaction.editor)) === normalized(prompt);
    state.prepared = draftRemains ? { text: prompt, editor: transaction.editor, href: location.href } : null;
    state.phase = draftRemains ? "prepared" : "idle";
    notice(`已核对 ${messageCount} 条分享文字，并触发一次发送。请以聊天界面为准；不会自动重发。`);
  }

  async function run(transaction, url, question) {
    let expired = false;
    const timeout = setTimeout(() => { expired = true; transaction.controller.abort(); }, FETCH_TIMEOUT_MS);
    try {
      const share = await getShare(url, transaction);
      clearTimeout(timeout);
      if (invalidated(transaction)) return;
      const prompt = Core.buildGroundedPrompt(share, { question, maxChars: MAX_PROMPT_CHARS });
      transaction.share = share;
      state.lastExport = prompt;
      if (invalidated(transaction)) return;
      state.phase = "preparing";
      state.writing = true;
      try { writeComposer(transaction.editor, prompt); }
      finally { state.writing = false; }
      await pause(75);
      if (invalidated(transaction, prompt)) return;
      await replayPrepared(transaction, prompt, share.messages.length);
    } catch (error) {
      if (state.transaction !== transaction) return;
      state.transaction = null;
      state.phase = "error";
      transaction.controller.abort();
      const reason = expired ? "读取超时，未发送。" :
        error?.name === "AbortError" ? "读取已取消，未发送。" : String(error?.message || "分享页读取失败，未发送。");
      notice("分享原文核对失败：" + reason);
    } finally { clearTimeout(timeout); }
  }

  function getLinks(text) {
    if (!/chatgpt\.com\/share\/|chat\.openai\.com\/share\//i.test(text)) return [];
    const links = Core.findShareLinks(text);
    if (!links.length) throw new Error("请使用完整的 https://chatgpt.com/share/… 分享地址。");
    return links;
  }

  function handleSend(event, editor, button) {
    const text = readText(editor);
    const windowPermit = state.replayWindow;
    if (windowPermit && editor === windowPermit.editor && location.href === windowPermit.href &&
        normalized(text) === normalized(windowPermit.text) &&
        ((event.type === "click" && button === windowPermit.button) || event.type === "submit")) return;
    if (state.transaction) { stopEvent(event); return; }
    if (state.prepared && normalized(text) === normalized(state.prepared.text)) {
      stopEvent(event);
      notice("这份原文已触发过一次提交，未自动重发。请先检查聊天界面是否已收到。");
      return;
    }
    let links;
    try { links = getLinks(text); }
    catch (error) {
      stopEvent(event);
      state.phase = "error";
      notice("分享链接无效，未发送：" + String(error.message));
      return;
    }
    if (!links.length) return;
    stopEvent(event);
    if (links.length !== 1) { notice("一次请发送一个分享链接，避免混用原文；本次未发送。"); return; }
    if (!editor || !sendButton(editor)) { notice("未保护：没有识别到唯一的输入框和发送按钮，本次链接未发送。"); return; }
    if (hasAttachments(editor)) { notice("这条消息还带有附件或非文字内容，无法完整核对，本次未发送。"); return; }
    const transaction = {
      id: ++state.counter, editor, originalText: text, revision: state.revision,
      href: location.href, controller: new AbortController()
    };
    state.transaction = transaction;
    state.phase = "loading";
    state.prepared = null;
    state.lastExport = null;
    notice("正在读取并核对分享原文；完成前不会发送链接。", { persistent: true });
    // Keep the user's actual request separate from the source conversation.
    const question = text.replace(links[0].raw, "").trim();
    void run(transaction, links[0].url, question);
  }

  document.addEventListener("keydown", event => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    const editor = composer();
    if (editor && (event.target === editor || editor.contains(event.target))) handleSend(event, editor, sendButton(editor));
  }, true);
  document.addEventListener("click", event => {
    const button = event.target instanceof Element ? event.target.closest("button") : null;
    if (!isSendButton(button)) return;
    const editor = composer();
    if (editor && scopeOf(editor)?.contains(button)) handleSend(event, editor, button);
  }, true);
  document.addEventListener("submit", event => {
    const editor = composer();
    if (editor && event.target.contains(editor)) handleSend(event, editor, sendButton(editor));
  }, true);
  document.addEventListener("input", event => {
    const editor = composer();
    if (!editor || (event.target !== editor && !editor.contains(event.target)) || state.writing) return;
    state.revision++;
    if (state.transaction) cancel();
    else {
      state.prepared = null;
      state.notice = "";
      state.phase = "idle";
      refreshStatus();
    }
  }, true);

  let observedHref = location.href;
  function checkPage() {
    if (observedHref !== location.href) {
      observedHref = location.href;
      if (state.transaction) cancel();
      state.prepared = null;
      state.lastExport = null;
      state.lastAudit = null;
      state.phase = "idle";
      if (!state.transaction) dismissNotice();
    }
    if (state.transaction && !state.writing) {
      if (!state.transaction.editor.isConnected || composer() !== state.transaction.editor ||
          hasAttachments(state.transaction.editor)) cancel();
    }
    refreshStatus();
  }
  const observer = new MutationObserver(checkPage);
  observer.observe(document, { childList: true, subtree: true });
  setInterval(checkPage, 250);
  document.addEventListener("DOMContentLoaded", refreshStatus, { once: true });
  function handleAuditResult(report) {
    if (!report) return;
    state.lastAudit = report;
    const needsAttention = ["issues", "review", "unverified"].includes(report.status);
    if (needsAttention || report.manual) {
      const summary = report.summary || (report.status === "no_mechanical_issue" ?
        "当前可核对项未发现不一致；这不能证明回答没有幻觉。" : "本次信息不足，尚不能核实这条回复。");
      notice("回复核对：" + summary, { duration: needsAttention ? 12000 : 8000 });
    }
  }
  if (globalThis.ShareLinkGuardAudit?.create) {
    audit = globalThis.ShareLinkGuardAudit.create({ fetchShare: fetchForAudit, onResult: handleAuditResult });
    audit.start();
  }
  async function auditCurrentPage() {
    checkPage();
    if (!audit) {
      const report = { status: "unverified", manual: true, findings: [], summary: "回复核对模块尚未加载，请更新扩展并刷新网页。" };
      handleAuditResult(report);
      return report;
    }
    notice("正在核对当前对话最近一次分享链接的回复…", { persistent: true });
    const token = state.noticeToken;
    try {
      const report = await audit.auditCurrentPage({ manual: true });
      if (!report && state.noticeToken === token) dismissNotice();
      return report;
    } catch (error) {
      const report = { status: "unverified", manual: true, findings: [], summary: "暂时无法核对：" + String(error?.message || error) };
      if (state.noticeToken === token) handleAuditResult(report);
      return report;
    }
  }
  function getState() {
    checkPage();
    const auditState = audit?.getState() || null;
    return { phase: state.phase, protected: state.protected, notice: state.notice,
      noticeVisible: Boolean(state.notice && (state.noticeUntil === Infinity || Date.now() < state.noticeUntil)),
      pageType: /^\/share\//.test(location.pathname) ? "share" : "chat",
      transactionCount: state.counter, activeTransaction: state.transaction?.id || null,
      audit: auditState, lastAudit: state.lastAudit || auditState?.report || null };
  }
  document.addEventListener("keydown", event => {
    if (event.altKey && event.shiftKey && event.code === "KeyL") {
      event.preventDefault();
      void auditCurrentPage();
    }
  }, true);
  if (typeof chrome !== "undefined" && chrome.runtime?.id) {
    chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
      if (sender.id !== chrome.runtime.id) return;
      if (message?.type === "SHARE_LINK_GUARD_GET_STATE") { sendResponse(getState()); return false; }
      if (message?.type === "SHARE_LINK_GUARD_AUDIT_NOW") {
        auditCurrentPage().then(report => sendResponse({ ok: true, report, state: getState() }))
          .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
        return true;
      }
      return false;
    });
  }
  globalThis.ShareLinkGuardController = Object.freeze({
    getState, refreshStatus, dismissNotice, auditCurrentPage
  });
  refreshStatus();
})();
