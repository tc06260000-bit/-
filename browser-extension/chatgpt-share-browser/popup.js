/* Toolbar UI. Reads the active ChatGPT content script only. */
(async function () {
  "use strict";
  const status = document.getElementById("status");
  const button = document.getElementById("audit");
  const summary = document.getElementById("summary");
  const findings = document.getElementById("findings");
  const followup = document.getElementById("followup");
  let tabId;
  function render(report) {
    findings.replaceChildren();
    summary.textContent = report?.summary || "尚未检查当前对话。";
    for (const finding of (report?.findings || [])) {
      const item = document.createElement("li");
      item.textContent = finding.message || finding.code || "需要核对";
      findings.appendChild(item);
    }
    followup.style.display = report && ["issues", "review", "unverified"].includes(report.status) ? "block" : "none";
  }
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    const tab = tabs[0];
    if (!tab?.id || !tab.url?.startsWith("https://chatgpt.com/")) throw new Error("请先打开 ChatGPT 普通聊天或分享页。");
    tabId = tab.id;
    const state = await chrome.tabs.sendMessage(tabId, { type: "SHARE_LINK_GUARD_GET_STATE" });
    status.textContent = state.pageType === "share" ? "当前是分享阅读页，没有输入框是正常的。" :
      state.protected ? "发送前原文核对已可用，空闲时保持静默。" : "当前未识别到唯一输入框；仍可尝试核对已显示的链接回复。";
    render(state.lastAudit || state.audit?.report);
    button.disabled = false;
  } catch (error) {
    status.textContent = "请打开 ChatGPT 网页并刷新一次，再点这个扩展图标。";
    summary.textContent = String(error?.message || error);
  }
  button.addEventListener("click", async () => {
    button.disabled = true;
    status.textContent = "正在读取原文并核对最近的链接回复…";
    try {
      const result = await chrome.tabs.sendMessage(tabId, { type: "SHARE_LINK_GUARD_AUDIT_NOW" });
      if (!result?.ok) throw new Error(result?.error || "当前页面暂时无法核对，请刷新后重试。");
      render(result.report || result.state?.lastAudit);
      status.textContent = "本次检查已结束。";
    } catch (error) { status.textContent = String(error?.message || error); }
    finally { button.disabled = false; }
  });
  document.getElementById("copy").addEventListener("click", async () => {
    const field = document.getElementById("prompt");
    try {
      await navigator.clipboard.writeText(field.value);
      document.getElementById("copy").textContent = "已复制";
    } catch (_) {
      field.focus();
      field.select();
      document.getElementById("copy").textContent = "已选中，请按 Ctrl+C";
    }
  });
})();
