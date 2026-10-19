#!/usr/bin/env python3
"""Offline Chromium behavior tests; every HTTP request is fulfilled or aborted.

Default mode injects the exact shipped core/content scripts at document start.
--load-extension additionally verifies a real unpacked-extension installation in
a clean persistent Chromium profile. It never opens a real ChatGPT account.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Page, Route, async_playwright


HERE = Path(__file__).resolve().parent
SHARE_URL = "https://chatgpt.com/share/11111111-1111-4111-8111-111111111111"
OTHER_SHARE_URL = "https://chatgpt.com/share/22222222-2222-4222-8222-222222222222"
THIRD_SHARE_URL = "https://chatgpt.com/share/33333333-3333-4333-8333-333333333333"
USER_TEXT = "原始问题：请逐字保留完整用户消息。"
ASSISTANT_TEXT = "原始回答：分享记录必须完整，最后一句也要保留。"


def share_html(*, user: str = USER_TEXT, assistant: str = ASSISTANT_TEXT, media: bool = False) -> str:
    def message(role: str, text: str) -> dict:
        value = {
            "id": role,
            "author": {"role": role},
            "content": {"content_type": "text", "parts": [text]},
            "metadata": {},
            "status": "finished_successfully",
            "recipient": "all",
            "end_turn": True,
        }
        if role == "assistant":
            value["channel"] = "final"
        return value

    user_message = message("user", user)
    if media:
        user_message["content"] = {
            "content_type": "multimodal_text",
            "parts": [user, {"content_type": "image_asset_pointer", "asset_pointer": "file://private-image"}],
        }
    conversation = {
        "title": "离线分享原文验收",
        "current_node": "a",
        "mapping": {
            "root": {"id": "root", "parent": None, "message": None, "children": ["u"]},
            "u": {"id": "u", "parent": "root", "message": user_message, "children": ["a"]},
            "a": {"id": "a", "parent": "u", "message": message("assistant", assistant), "children": []},
        },
    }
    payload = {"props": {"pageProps": {"serverResponse": {"data": conversation}}}}
    serialized = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return '<!doctype html><html><head><title>Shared ChatGPT</title></head><body><script id="__NEXT_DATA__" type="application/json">' + serialized + "</script></body></html>"


def app_html(kind: str = "textarea", *, recognized: bool = True, ambiguous_send: bool = False, unrelated_form: bool = False, history_editor: bool = False) -> str:
    identifier = "prompt-textarea" if recognized else "unrecognized-composer"
    if kind == "textarea":
        editor = f'<textarea id="{identifier}" aria-label="Message ChatGPT"></textarea>'
    elif kind == "contenteditable":
        editor = f'<div id="{identifier}" class="ProseMirror" contenteditable="true" role="textbox" aria-label="Message ChatGPT"></div>'
    elif kind == "october":
        editor = '<div contenteditable="true" role="textbox" aria-label="Message ChatGPT"></div>'
    elif kind == "lexical":
        editor = '<div data-testid="prompt-textarea" data-lexical-editor="true" contenteditable="true" role="textbox" aria-label="Message ChatGPT"></div>'
    elif kind == "noform":
        editor = '<div contenteditable="true" role="textbox" aria-label="Message ChatGPT"></div>'
    else:
        raise ValueError("Unknown mock editor kind")
    if not recognized:
        editor = '<input type="text" id="unrecognized-composer" aria-label="Unknown composer">'
    modern = kind in ("october", "lexical", "noform")
    form_attributes = 'data-type="unknown-composer"' if not recognized else ('data-chatgpt-composer' if modern else 'data-type="unified-composer"')
    send_button = '<button type="submit" aria-label="发送">发送</button>' if modern else '<button type="submit" data-testid="send-button" aria-label="Send prompt">Send</button>'
    if ambiguous_send:
        send_button += '<button type="submit" aria-label="发送">Duplicate send</button>'
    unrelated = '<form id="unrelated-form"><button type="submit" data-testid="send-button" aria-label="发送">Another form send</button></form>' if unrelated_form else ''
    history = '<article><div data-message-author-role="user" data-message-id="old-edit"><div contenteditable="true" role="textbox" data-testid="history-edit-textbox">历史消息编辑器</div></div><button aria-label="发送">历史编辑发送</button></article>' if history_editor else ''
    opening = '<section id="mock-composer" data-testid="composer">' if kind == "noform" else '<form ' + form_attributes + '>'
    closing = '</section>' if kind == "noform" else '</form>'
    return """<!doctype html><html><head><meta charset="utf-8"><title>Mock ChatGPT composer</title>
<style>body{font-family:sans-serif}textarea,[contenteditable]{display:block;width:700px;height:120px;border:1px solid black;padding:8px}button{padding:10px}#app-status{margin:15px}.whitespace-pre-wrap,.markdown{white-space:pre-wrap}</style>
</head><body>""" + unrelated + '<main><section id="conversation">' + history + '</section>' + opening + editor + send_button + closing + """
<div id="app-status">Mock page</div></main><script>
window.__sent = [];
window.__inputEvents = [];
window.__draft = '';
window.__unrelatedSends = 0;
const form = document.querySelector('main form');
const composer = form || document.querySelector('#mock-composer');
const editor = composer.querySelector('textarea,input,[contenteditable]');
const send = composer.querySelector('button[type="submit"]');
const unrelated = document.querySelector('#unrelated-form');
if (unrelated) unrelated.addEventListener('submit', event => { event.preventDefault(); window.__unrelatedSends++; });
function text() { return 'value' in editor ? editor.value : editor.innerText; }
function clear() { if ('value' in editor) editor.value = ''; else editor.textContent = ''; }
window.__addMessage = (role, text) => {
  const article = document.createElement('article');
  article.setAttribute('data-testid', 'conversation-turn-' + document.querySelectorAll('#conversation article').length);
  const message = document.createElement('div');
  message.setAttribute('data-message-author-role', role);
  message.setAttribute('data-message-id', 'm-' + document.querySelectorAll('#conversation article').length);
  const body = document.createElement('div');
  body.className = role === 'assistant' ? 'markdown' : 'whitespace-pre-wrap';
  body.textContent = text;
  message.append(body);article.append(message);
  document.querySelector('#conversation').append(article);
  return message;
};
editor.addEventListener('input', () => { window.__draft = text(); window.__inputEvents.push(text()); });
editor.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    if (form) form.requestSubmit(send); else send.click();
  }
});
function submitMessage(event) {
  event.preventDefault();
  // Model a controlled composer: changing DOM without an input event is not
  // enough to update the application's actual outbound message.
  window.__sent.push({text: window.__draft, url: location.href});
  window.__addMessage('user', window.__draft);
  window.__draft = '';
  clear();
}
if (form) form.addEventListener('submit', submitMessage); else send.addEventListener('click', submitMessage);
</script></body></html>"""


@dataclass
class Scenario:
    kind: str = "textarea"
    recognized: bool = True
    ambiguous_send: bool = False
    unrelated_form: bool = False
    history_editor: bool = False
    start_url: str = "https://chatgpt.com/"
    reading_page: bool = False
    share_html_by_url: dict[str, str] = field(default_factory=dict)
    runtime_stub: bool = False
    response_html: str = field(default_factory=share_html)
    response_status: int = 200
    delay: bool = False
    share_requests: list[str] = field(default_factory=list)
    other_requests: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    requested: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def route(self, route: Route) -> None:
        url = route.request.url
        parsed = urlsplit(url)
        if self.reading_page and route.request.is_navigation_request() and url == self.start_url:
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body='<!doctype html><html><head><title>Shared conversation</title></head><body><main><div data-message-author-role="user">分享阅读页面</div><div data-message-author-role="assistant">分享中的历史回答</div></main></body></html>')
        elif parsed.scheme == "https" and parsed.netloc == "chatgpt.com" and parsed.path.startswith("/share/"):
            self.share_requests.append(url)
            self.requested.set()
            if self.delay:
                await self.release.wait()
            await route.fulfill(status=self.response_status, content_type="text/html; charset=utf-8", body=self.share_html_by_url.get(url, self.response_html))
        elif parsed.scheme == "https" and parsed.netloc == "chatgpt.com" and route.request.is_navigation_request():
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=app_html(self.kind, recognized=self.recognized, ambiguous_send=self.ambiguous_send, unrelated_form=self.unrelated_form, history_editor=self.history_editor))
        else:
            self.other_requests.append(url)
            await route.abort("blockedbyclient")


class Harness:
    def __init__(self, browser: Browser, extension_dir: Path):
        self.browser = browser
        self.extension_dir = extension_dir
        script_names = json.loads((extension_dir / "manifest.json").read_text(encoding="utf-8"))["content_scripts"][0]["js"]
        self.scripts = "\n;\n".join((extension_dir / filename).read_text(encoding="utf-8") for filename in script_names)

    async def open(self, scenario: Scenario) -> tuple[BrowserContext, Page]:
        context = await self.browser.new_context(service_workers="block")
        await context.route("**/*", scenario.route)
        runtime = """globalThis.chrome=globalThis.chrome||{};chrome.runtime={id:'offline-extension-id',onMessage:{addListener(listener){globalThis.__runtimeListener=listener;}}};globalThis.__sendRuntimeMessage=message=>new Promise(resolve=>__runtimeListener(message,{id:chrome.runtime.id},resolve));\n""" if scenario.runtime_stub else ""
        await context.add_init_script(script=runtime + self.scripts)
        page = await context.new_page()
        page.on("pageerror", lambda error: scenario.page_errors.append(str(error)))
        page.set_default_timeout(5000)
        await page.goto(scenario.start_url, wait_until="domcontentloaded")
        if scenario.page_errors:
            await context.close()
            raise AssertionError("Document-start script error: " + "; ".join(scenario.page_errors))
        await page.wait_for_selector("#share-link-guard-status", state="attached")
        return context, page


async def editor_text(page: Page) -> str:
    return await page.locator("#prompt-textarea").evaluate("e => 'value' in e ? e.value : e.innerText")


async def status_text(page: Page) -> str:
    return await page.locator("#share-link-guard-status").inner_text()


async def expect_quiet(page: Page) -> None:
    toast = page.locator("#share-link-guard-status")
    assert await toast.count() == 0 or not await toast.is_visible(), "An idle page displayed a persistent overlay: " + await status_text(page)


async def sent(page: Page) -> list[dict]:
    return await page.evaluate("window.__sent")


async def expect_count(page: Page, count: int) -> list[dict]:
    await page.wait_for_function("n => window.__sent.length === n", arg=count)
    return await sent(page)


async def expect_blocked(page: Page, original: str) -> None:
    await page.wait_for_function("() => { const c=globalThis.ShareLinkGuardController; return c && !['loading','prepared','replaying'].includes(c.getState().phase); }")
    await page.wait_for_timeout(100)
    assert await sent(page) == [], "Bare link was submitted after failed verification"
    assert await editor_text(page) == original, "Failed verification changed the user's original draft"
    assert (await status_text(page)).strip(), "No failure status was shown"


async def success(harness: Harness, kind: str, entry: str) -> None:
    scenario = Scenario(kind=kind)
    context, page = await harness.open(scenario)
    try:
        original = "请依据原文回答：\n" + SHARE_URL
        await page.locator("#prompt-textarea").fill(original)
        if entry == "enter":
            await page.locator("#prompt-textarea").press("Enter")
        elif entry == "click":
            await page.locator('[data-testid="send-button"]').click()
        else:
            await page.locator("form").evaluate("e => e.requestSubmit()")
        submissions = await expect_count(page, 1)
        await page.wait_for_timeout(150)
        assert len(await sent(page)) == 1, "One gesture caused more than one send"
        body = submissions[0]["text"]
        assert body != original, "Original bare link was sent without transcript"
        assert USER_TEXT in body and ASSISTANT_TEXT in body, "Full source messages were not injected"
        assert SHARE_URL in body, "Source link was not retained"
        assert "T0001" in body and "T0002" in body, "Source citations were not included"
        assert len(scenario.share_requests) == 1, "Share was fetched more than once"
        assert not scenario.other_requests, "Unexpected network request was made"
    finally:
        await context.close()


async def rejected_response(harness: Harness, case: str) -> None:
    scenario = Scenario()
    if case == "network":
        scenario.response_status = 403
    elif case == "parse":
        scenario.response_html = "<html><title>Login required</title><body>Login required</body></html>"
    elif case == "media":
        scenario.response_html = share_html(media=True)
    elif case == "budget":
        scenario.response_html = share_html(assistant="完整原文" * 20000)
    else:
        raise ValueError(case)
    context, page = await harness.open(scenario)
    try:
        await page.locator("#prompt-textarea").fill(SHARE_URL)
        await page.locator("#prompt-textarea").press("Enter")
        await scenario.requested.wait()
        await expect_blocked(page, SHARE_URL)
        assert len(scenario.share_requests) == 1
    finally:
        await context.close()


async def edit_cancels_and_can_retry(harness: Harness) -> None:
    scenario = Scenario(delay=True)
    context, page = await harness.open(scenario)
    try:
        await page.locator("#prompt-textarea").fill(SHARE_URL)
        await page.locator("#prompt-textarea").press("Enter")
        await asyncio.wait_for(scenario.requested.wait(), 5)
        new_text = "我在读取期间改了主意，请保留这份草稿。"
        await page.locator("#prompt-textarea").fill(new_text)
        scenario.release.set()
        await page.wait_for_timeout(300)
        assert await sent(page) == [], "Edited pending draft was sent"
        assert await editor_text(page) == new_text, "Completed fetch overwrote a newer draft"
        scenario.delay = False
        await page.locator("#prompt-textarea").fill(SHARE_URL)
        await page.locator('[data-testid="send-button"]').click()
        await expect_count(page, 1)
        assert len(scenario.share_requests) == 2, "Canceled share could not be retried"
    finally:
        scenario.release.set()
        await context.close()


async def spa_navigation_cancels(harness: Harness) -> None:
    scenario = Scenario(delay=True)
    context, page = await harness.open(scenario)
    try:
        await page.locator("#prompt-textarea").fill(SHARE_URL)
        await page.locator("#prompt-textarea").press("Enter")
        await asyncio.wait_for(scenario.requested.wait(), 5)
        await page.evaluate("history.pushState({}, '', '/c/a-different-chat')")
        scenario.release.set()
        await page.wait_for_timeout(350)
        assert await sent(page) == [], "Share from a previous chat was submitted after SPA navigation"
        assert await editor_text(page) == SHARE_URL, "Navigation completion overwrote original input"
    finally:
        scenario.release.set()
        await context.close()


async def duplicate_gestures_single_transaction(harness: Harness) -> None:
    scenario = Scenario(delay=True)
    context, page = await harness.open(scenario)
    try:
        await page.locator("#prompt-textarea").fill(SHARE_URL)
        await page.locator('[data-testid="send-button"]').evaluate("e => { e.click(); e.click(); }")
        await asyncio.wait_for(scenario.requested.wait(), 5)
        await page.locator("#prompt-textarea").press("Enter")
        await page.locator("form").evaluate("e => e.requestSubmit()")
        scenario.release.set()
        await expect_count(page, 1)
        await page.wait_for_timeout(200)
        assert len(await sent(page)) == 1, "Mixed click/Enter/submit gestures caused duplicate sends"
        assert len(scenario.share_requests) == 1, "Mixed gestures caused duplicate fetches"
    finally:
        scenario.release.set()
        await context.close()


async def shift_enter_and_ime(harness: Harness, kind: str) -> None:
    scenario = Scenario(kind=kind)
    context, page = await harness.open(scenario)
    try:
        await page.locator("#prompt-textarea").fill(SHARE_URL)
        await page.locator("#prompt-textarea").press("Shift+Enter")
        await page.locator("#prompt-textarea").evaluate("e => e.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter',isComposing:true,bubbles:true,cancelable:true}))")
        await page.locator("#prompt-textarea").evaluate("e => e.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter',keyCode:229,bubbles:true,cancelable:true}))")
        await page.wait_for_timeout(150)
        assert await sent(page) == [], "Shift+Enter or IME composition accidentally submitted"
        assert scenario.share_requests == [], "Shift+Enter or IME composition started a fetch"
        assert SHARE_URL in await editor_text(page), "Non-send gesture lost draft"
    finally:
        await context.close()


async def ordinary_message_unaffected(harness: Harness, kind: str) -> None:
    scenario = Scenario(kind=kind)
    context, page = await harness.open(scenario)
    try:
        text = "普通消息：请解释今天的待办事项。"
        await page.locator("#prompt-textarea").fill(text)
        await page.locator("#prompt-textarea").press("Enter")
        submissions = await expect_count(page, 1)
        assert submissions[0]["text"] == text
        assert scenario.share_requests == []
    finally:
        await context.close()


async def failure_can_retry(harness: Harness) -> None:
    scenario = Scenario(response_status=403)
    context, page = await harness.open(scenario)
    try:
        await page.locator("#prompt-textarea").fill(SHARE_URL)
        await page.locator('[data-testid="send-button"]').click()
        await scenario.requested.wait()
        await expect_blocked(page, SHARE_URL)
        scenario.response_status = 200
        await page.locator('[data-testid="send-button"]').click()
        await expect_count(page, 1)
        assert len(scenario.share_requests) == 2
    finally:
        await context.close()


async def unrecognized_ui_reports_unprotected(harness: Harness) -> None:
    scenario = Scenario(recognized=False)
    context, page = await harness.open(scenario)
    try:
        await page.wait_for_function("() => !globalThis.ShareLinkGuardController.getState().protected")
        await expect_quiet(page)
        assert scenario.share_requests == []
    finally:
        await context.close()


async def modern_editor_in_own_form(harness: Harness, kind: str) -> None:
    scenario = Scenario(kind=kind, unrelated_form=True)
    context, page = await harness.open(scenario)
    try:
        editor = page.locator('main [contenteditable="true"][role="textbox"]')
        assert await editor.get_attribute("id") is None, "Modern test accidentally used the legacy id"
        await editor.fill(SHARE_URL)
        await page.locator('main button[aria-label="发送"]').click()
        submissions = await expect_count(page, 1)
        assert USER_TEXT in submissions[0]["text"] and ASSISTANT_TEXT in submissions[0]["text"]
        assert await page.evaluate("window.__unrelatedSends") == 0, "Extension clicked a send button in another form"
        assert len(scenario.share_requests) == 1
    finally:
        await context.close()


async def ambiguous_send_reports_unprotected(harness: Harness) -> None:
    scenario = Scenario(kind="october", ambiguous_send=True)
    context, page = await harness.open(scenario)
    try:
        await page.wait_for_function("() => !globalThis.ShareLinkGuardController.getState().protected")
        await expect_quiet(page)
        assert await sent(page) == []
        assert scenario.share_requests == []
    finally:
        await context.close()


async def missing_scheme_candidate_blocks(harness: Harness) -> None:
    scenario = Scenario()
    context, page = await harness.open(scenario)
    try:
        original = SHARE_URL.removeprefix("https://")
        await page.locator("#prompt-textarea").fill(original)
        await page.locator("#prompt-textarea").press("Enter")
        await expect_blocked(page, original)
        assert scenario.share_requests == [], "Malformed link should not be fetched"
    finally:
        await context.close()


async def composer_nonblob_media_blocks(harness: Harness) -> None:
    scenario = Scenario()
    context, page = await harness.open(scenario)
    try:
        await page.locator("main form").evaluate("e => { const input=document.createElement('input');input.type='file';e.append(input);const image=document.createElement('img');image.src='/backend-api/files/example.png';image.alt='composer attachment';e.append(image); }")
        assert await page.locator('input[type="file"]').evaluate("e => e.files.length") == 0
        await page.locator("#prompt-textarea").fill(SHARE_URL)
        await page.locator("#prompt-textarea").press("Enter")
        await expect_blocked(page, SHARE_URL)
        assert scenario.share_requests == [], "A mixed media draft should not start text-only verification"
    finally:
        await context.close()


async def quick_second_transaction_keeps_own_state(harness: Harness) -> None:
    scenario = Scenario()
    context, page = await harness.open(scenario)
    try:
        await page.locator("#prompt-textarea").fill(SHARE_URL)
        await page.locator("#prompt-textarea").press("Enter")
        await expect_count(page, 1)
        scenario.delay = True
        scenario.requested.clear()
        await page.locator("#prompt-textarea").fill(OTHER_SHARE_URL)
        await page.locator("#prompt-textarea").press("Enter")
        await asyncio.wait_for(scenario.requested.wait(), 5)
        await page.wait_for_timeout(350)
        state = await page.evaluate("ShareLinkGuardController.getState()")
        assert state["phase"] == "loading" and state["activeTransaction"] == 2, "An old send completion changed the newer transaction's state: " + json.dumps(state, ensure_ascii=False)
        assert await editor_text(page) == OTHER_SHARE_URL
        assert len(await sent(page)) == 1
        scenario.release.set()
        submissions = await expect_count(page, 2)
        assert OTHER_SHARE_URL in submissions[1]["text"]
        assert len(scenario.share_requests) == 2
    finally:
        scenario.release.set()
        await context.close()


async def removed_send_reports_unprotected_even_after_error(harness: Harness) -> None:
    scenario = Scenario(response_status=403)
    context, page = await harness.open(scenario)
    try:
        await page.locator("#prompt-textarea").fill(SHARE_URL)
        await page.locator("#prompt-textarea").press("Enter")
        await scenario.requested.wait()
        await expect_blocked(page, SHARE_URL)
        await page.locator('[data-testid="send-button"]').evaluate("e => e.remove()")
        await page.wait_for_function("() => !ShareLinkGuardController.getState().protected")
        text = await status_text(page)
        assert "已保护" not in text and "已就绪" not in text, "Loss of a send control left a false readiness claim"
    finally:
        await context.close()


async def share_reading_page_is_quiet(harness: Harness) -> None:
    scenario = Scenario(start_url=SHARE_URL, reading_page=True)
    context, page = await harness.open(scenario)
    try:
        await page.wait_for_timeout(400)
        await expect_quiet(page)
        assert scenario.share_requests == [], "Opening a share reading page started an unsolicited source fetch"
        assert not await page.evaluate("ShareLinkGuardController.getState().protected")
    finally:
        await context.close()


async def ordinary_chat_is_quiet(harness: Harness) -> None:
    scenario = Scenario()
    context, page = await harness.open(scenario)
    try:
        await expect_quiet(page)
        await page.locator("#prompt-textarea").fill("普通聊天消息")
        await page.locator("#prompt-textarea").press("Enter")
        await expect_count(page, 1)
        await page.evaluate("__addMessage('assistant', '这是不涉及分享链接的普通回复。')")
        await page.wait_for_timeout(1600)
        await expect_quiet(page)
        assert scenario.share_requests == []
    finally:
        await context.close()


async def noform_composer_ignores_history_editor(harness: Harness) -> None:
    scenario = Scenario(kind="noform", history_editor=True)
    context, page = await harness.open(scenario)
    try:
        editor = page.locator('#mock-composer [contenteditable="true"][role="textbox"]')
        await editor.fill(SHARE_URL)
        await page.locator('#mock-composer button[aria-label="发送"]').click()
        submissions = await expect_count(page, 1)
        assert USER_TEXT in submissions[0]["text"] and ASSISTANT_TEXT in submissions[0]["text"]
        assert await page.locator('[data-testid="history-edit-textbox"]').inner_text() == "历史消息编辑器", "Fallback composer selection overwrote a historical message editor"
        assert len(scenario.share_requests) == 1
    finally:
        await context.close()


async def activity_toast_dismisses_and_expires(harness: Harness) -> None:
    scenario = Scenario(response_status=403)
    context, page = await harness.open(scenario)
    try:
        await page.clock.install()
        await expect_quiet(page)
        await page.locator("#prompt-textarea").fill(SHARE_URL)
        await page.locator("#prompt-textarea").press("Enter")
        await scenario.requested.wait()
        await expect_blocked(page, SHARE_URL)
        toast = page.locator("#share-link-guard-status")
        assert await toast.is_visible(), "An active verification failure did not show a notice"
        await toast.get_by_role("button", name=re.compile("关闭")).click()
        await expect_quiet(page)
        await page.evaluate("document.body.appendChild(document.createElement('aside'))")
        await page.clock.fast_forward(30000)
        await expect_quiet(page)
        await page.locator("#prompt-textarea").press("Enter")
        await page.wait_for_function("() => ShareLinkGuardController.getState().phase === 'error'")
        assert await toast.is_visible(), "A new activity failed to show a fresh notice"
        await page.clock.fast_forward(120000)
        await expect_quiet(page)
        await page.evaluate("document.body.appendChild(document.createElement('aside'))")
        await page.clock.fast_forward(1000)
        await expect_quiet(page)
    finally:
        await context.close()


async def audit_state(page: Page) -> dict:
    return await page.evaluate("ShareLinkGuardController.getState().audit")


async def wait_audit_report(page: Page, expected: str | None = None) -> dict:
    await page.wait_for_function("status => { const a=ShareLinkGuardController.getState().audit; return a?.report && (!status || a.report.status===status); }", arg=expected, timeout=8000)
    return (await audit_state(page))["report"]


async def send_source(page: Page) -> None:
    await page.locator("#prompt-textarea").fill(SHARE_URL)
    await page.locator("#prompt-textarea").press("Enter")
    await expect_count(page, 1)


async def reply_audit(harness: Harness, case: str) -> None:
    scenario = Scenario()
    context, page = await harness.open(scenario)
    try:
        await send_source(page)
        replies = {
            "unknown_citation": '[T9999]：“这是不存在的消息。”',
            "wrong_count": "共收到 9 条消息，我已读完全部。",
            "wrong_quote": '[T0001]：“这是一段完全捏造、原文中根本不存在的引用句子。”',
            "correct_quote": '[T0001]：“' + USER_TEXT + '”',
            "no_evidence": "我已经完整读完所有原文。",
        }
        expected = "no_mechanical_issue" if case == "correct_quote" else "review" if case == "no_evidence" else "issues"
        await page.evaluate("text => __addMessage('assistant', text)", replies[case])
        report = await wait_audit_report(page, expected)
        assert report["sourceUrl"] == SHARE_URL
        if expected == "issues":
            assert report["findings"], "A mechanical contradiction had no explicit finding"
            assert await page.locator("#share-link-guard-status").is_visible(), "A detected contradiction did not warn the user"
        if expected == "no_mechanical_issue":
            assert not re.search(r"无幻觉|保证.{0,8}正确|全部.{0,5}真实", await status_text(page)), "A correct citation was overstated as a hallucination guarantee"
        assert len(scenario.share_requests) == 1, "Automatic audit unnecessarily refetched or recursively followed the source"
    finally:
        await context.close()


async def old_reply_is_not_audited_as_new(harness: Harness) -> None:
    scenario = Scenario()
    context, page = await harness.open(scenario)
    try:
        await page.evaluate("__addMessage('assistant', '[T9999]：“发送前的旧回复。”')")
        await send_source(page)
        await page.wait_for_timeout(1600)
        state = await audit_state(page)
        assert not state.get("report"), "An old assistant message was mistaken for the reply to the new source"
        await page.evaluate("text => __addMessage('assistant', text)", '[T0001]：“' + USER_TEXT + '”')
        await wait_audit_report(page, "no_mechanical_issue")
    finally:
        await context.close()


async def chat_navigation_clears_audit_context(harness: Harness) -> None:
    scenario = Scenario()
    context, page = await harness.open(scenario)
    try:
        await send_source(page)
        await page.evaluate("history.pushState({}, '', '/c/new-chat');document.querySelector('#conversation').textContent='';__addMessage('assistant', '[T9999]：“另一个聊天的回复。”')")
        await page.wait_for_timeout(1800)
        state = await audit_state(page)
        assert not state.get("contextActive") and not state.get("sourceUrl"), "A source from the previous chat remained attached"
        assert not state.get("report"), "A reply in another chat was audited against the old source"
        assert len(scenario.share_requests) == 1
    finally:
        await context.close()


async def synchronous_chat_switch_clears_audit_and_download(harness: Harness) -> None:
    scenario = Scenario()
    context, page = await harness.open(scenario)
    try:
        await send_source(page)
        await page.evaluate("__addMessage('assistant', '[T9999]：“A聊天中需要警示的虚构引文。”')")
        await wait_audit_report(page, "issues")
        state_a = await page.evaluate("ShareLinkGuardController.getState()")
        assert state_a["lastAudit"]["sourceUrl"] == SHARE_URL
        save_a = page.locator("#share-link-guard-status").get_by_role("button", name="保存已提取原文")
        assert await save_a.is_visible(), "The regression precondition did not prepare A's downloadable source"
        switched = await page.evaluate("""() => {
          history.pushState({}, '', '/c/synchronous-new-chat');
          document.querySelector('#conversation').textContent = '';
          // Intentionally stay in this same JavaScript turn: neither the
          // MutationObserver nor the 250 ms route poll has run yet.
          const state = ShareLinkGuardController.getState();
          const button = [...document.querySelectorAll('#share-link-guard-status button')]
            .find(element => element.textContent === '保存已提取原文');
          return {state, saveAvailable: Boolean(button && !button.hidden &&
            getComputedStyle(button).display !== 'none'),
            saveVisible: Boolean(button && !button.hidden &&
            button.getClientRects().length && getComputedStyle(button).display !== 'none')};
        }""")
        state_b = switched["state"]
        assert state_b["lastAudit"] is None, "Synchronous getState leaked A's audit report into B"
        assert not state_b["audit"]["contextActive"] and state_b["audit"]["sourceUrl"] is None
        assert state_b["audit"]["report"] is None
        assert not switched["saveAvailable"], "A's source download remained available behind B's hidden notice"
        assert not switched["saveVisible"], "B's notice still exposed the download button for A's source"
        assert len(scenario.share_requests) == 1
    finally:
        await context.close()


async def manual_audit_uses_latest_link_pair(harness: Harness) -> None:
    other_text = "第二份来源的用户原句足够完整，可以明确核对。"
    scenario = Scenario(share_html_by_url={OTHER_SHARE_URL: share_html(user=other_text)})
    context, page = await harness.open(scenario)
    try:
        await page.evaluate("url => { __addMessage('user',url);__addMessage('assistant','[T9999]：“旧链接旧回复。”'); }", SHARE_URL)
        await page.evaluate("arg => { __addMessage('user',arg.url);__addMessage('assistant','[T0001]：“'+arg.text+'”'); }", {"url": OTHER_SHARE_URL, "text": other_text})
        await page.evaluate("ShareLinkGuardController.auditCurrentPage()")
        report = await wait_audit_report(page, "no_mechanical_issue")
        assert report["sourceUrl"] == OTHER_SHARE_URL, "Manual audit mixed sources from an older user turn"
        assert scenario.share_requests == [OTHER_SHARE_URL]
    finally:
        await context.close()


async def navigation_cancels_manual_audit_fetch(harness: Harness) -> None:
    scenario = Scenario(delay=True)
    context, page = await harness.open(scenario)
    try:
        await page.evaluate("url => { __addMessage('user',url);__addMessage('assistant','[T9999]：“待审查回复。”'); }", SHARE_URL)
        await page.evaluate("void ShareLinkGuardController.auditCurrentPage();true")
        await asyncio.wait_for(scenario.requested.wait(), 5)
        await page.evaluate("history.pushState({}, '', '/c/other-chat');document.querySelector('#conversation').textContent='';__addMessage('assistant','其他聊天回复');")
        scenario.release.set()
        await page.wait_for_timeout(1600)
        state = await audit_state(page)
        assert not state.get("contextActive") and not state.get("sourceUrl") and not state.get("report"), "An old pending audit fetch was applied after navigation"
        assert len(scenario.share_requests) == 1
    finally:
        scenario.release.set()
        await context.close()


async def share_nested_source_is_bounded(harness: Harness) -> None:
    scenario = Scenario(
        start_url=SHARE_URL, reading_page=True,
        share_html_by_url={
            SHARE_URL: share_html(user="请阅读这个链接：" + OTHER_SHARE_URL, assistant='[T9999]：“编造引用。”'),
            OTHER_SHARE_URL: share_html(user="此原文还提到链接 " + THIRD_SHARE_URL),
            THIRD_SHARE_URL: share_html(),
        },
    )
    context, page = await harness.open(scenario)
    try:
        await expect_quiet(page)
        await page.evaluate("ShareLinkGuardController.auditCurrentPage()")
        report = await wait_audit_report(page, "issues")
        assert report["sourceUrl"] == OTHER_SHARE_URL
        assert scenario.share_requests == [SHARE_URL, OTHER_SHARE_URL], "Manual share audit recursively fetched nested sources"
    finally:
        await context.close()


async def popup_runtime_message_contract(harness: Harness) -> None:
    scenario = Scenario(runtime_stub=True)
    context, page = await harness.open(scenario)
    try:
        manifest = json.loads((harness.extension_dir / "manifest.json").read_text(encoding="utf-8"))
        popup = harness.extension_dir / manifest["action"]["default_popup"]
        assert popup.is_file()
        popup_script = (harness.extension_dir / "popup.js").read_text(encoding="utf-8")
        assert "chrome.tabs.sendMessage" in popup_script and "SHARE_LINK_GUARD_AUDIT_NOW" in popup_script
        assert "SHARE_LINK_GUARD_GET_STATE" in popup_script
        state = await page.evaluate("__sendRuntimeMessage({type:'SHARE_LINK_GUARD_GET_STATE'})")
        assert state["protected"] and not state["noticeVisible"]
        await page.evaluate("url=>{__addMessage('user',url);__addMessage('assistant','[T9999]：“历史中不存在的引用。”');}", SHARE_URL)
        result = await page.evaluate("__sendRuntimeMessage({type:'SHARE_LINK_GUARD_AUDIT_NOW'})")
        assert result["ok"] and result["report"]["status"] == "issues"
        assert len(scenario.share_requests) == 1
    finally:
        await context.close()


async def actual_unpacked_extension(playwright, extension_dir: Path) -> dict:
    """Separate installation verification; no add_init_script is used here."""
    with tempfile.TemporaryDirectory(prefix="share-guard-chromium-") as profile:
        context = await playwright.chromium.launch_persistent_context(
            profile,
            executable_path="/usr/bin/chromium",
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-extensions-except=" + str(extension_dir), "--load-extension=" + str(extension_dir)],
            ignore_default_args=["--disable-extensions"],
            service_workers="block",
        )
        scenario = Scenario()
        await context.route("**/*", scenario.route)
        page = await context.new_page()
        page.set_default_timeout(7000)
        try:
            await page.goto("chrome://extensions-internals/", wait_until="domcontentloaded")
            registered = json.loads(await page.locator("body").inner_text())
            installed = any(record.get("path") == str(extension_dir) and record.get("registry_status") == "ENABLED" for record in registered)
            if not installed:
                policy_path = Path("/etc/chromium/policies/managed/extensions.json")
                try:
                    policy = json.loads(policy_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    policy = {}
                if "*" in policy.get("ExtensionInstallBlocklist", []):
                    return {
                        "mode": "actual_unpacked_extension", "status": "blocked",
                        "reason": "Chromium administrator policy blocks unpacked extension installation; policy was not changed or bypassed.",
                        "registered": False, "policy_path": str(policy_path),
                        "policy": {"ExtensionInstallBlocklist": ["*"]},
                    }
                raise AssertionError("The unpacked extension was not registered by Chromium")
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
            await page.wait_for_selector("#share-link-guard-status")
            await page.locator("#prompt-textarea").fill(SHARE_URL)
            await page.locator('[data-testid="send-button"]').click()
            submissions = await expect_count(page, 1)
            assert USER_TEXT in submissions[0]["text"] and ASSISTANT_TEXT in submissions[0]["text"]
            assert len(scenario.share_requests) == 1
            assert len(await sent(page)) == 1
            return {"mode": "actual_unpacked_extension", "status": "passed", "network": "all HTTP requests mocked or aborted", "share_fetches": len(scenario.share_requests), "sends": len(submissions)}
        finally:
            await context.close()


async def main_async(args) -> int:
    tests: list[tuple[str, Callable[[Harness], Awaitable[None]]]] = [
        ("textarea_enter_expands_and_sends_once", lambda h: success(h, "textarea", "enter")),
        ("textarea_click_expands_and_sends_once", lambda h: success(h, "textarea", "click")),
        ("contenteditable_enter_expands_and_sends_once", lambda h: success(h, "contenteditable", "enter")),
        ("contenteditable_click_expands_and_sends_once", lambda h: success(h, "contenteditable", "click")),
        ("form_submit_expands_and_sends_once", lambda h: success(h, "textarea", "submit")),
        ("network_failure_blocks_bare_link", lambda h: rejected_response(h, "network")),
        ("parse_failure_blocks_bare_link", lambda h: rejected_response(h, "parse")),
        ("media_blocks_bare_link", lambda h: rejected_response(h, "media")),
        ("oversize_blocks_bare_link", lambda h: rejected_response(h, "budget")),
        ("pending_edit_cancels_and_can_retry", edit_cancels_and_can_retry),
        ("spa_navigation_cancels", spa_navigation_cancels),
        ("duplicate_gestures_single_transaction", duplicate_gestures_single_transaction),
        ("textarea_shift_enter_and_ime", lambda h: shift_enter_and_ime(h, "textarea")),
        ("contenteditable_shift_enter_and_ime", lambda h: shift_enter_and_ime(h, "contenteditable")),
        ("textarea_ordinary_message_unaffected", lambda h: ordinary_message_unaffected(h, "textarea")),
        ("contenteditable_ordinary_message_unaffected", lambda h: ordinary_message_unaffected(h, "contenteditable")),
        ("failed_fetch_can_retry", failure_can_retry),
        ("unrecognized_composer_reports_unprotected", unrecognized_ui_reports_unprotected),
        ("october_contenteditable_chinese_send_own_form", lambda h: modern_editor_in_own_form(h, "october")),
        ("lexical_chinese_send_own_form", lambda h: modern_editor_in_own_form(h, "lexical")),
        ("ambiguous_send_controls_report_unprotected", ambiguous_send_reports_unprotected),
        ("missing_scheme_candidate_blocks", missing_scheme_candidate_blocks),
        ("composer_nonblob_media_blocks", composer_nonblob_media_blocks),
        ("quick_second_transaction_keeps_own_state", quick_second_transaction_keeps_own_state),
        ("removed_send_reports_unprotected_after_error", removed_send_reports_unprotected_even_after_error),
        ("share_reading_page_is_quiet", share_reading_page_is_quiet),
        ("ordinary_chat_is_quiet", ordinary_chat_is_quiet),
        ("noform_composer_ignores_history_editor", noform_composer_ignores_history_editor),
        ("activity_toast_dismisses_and_expires", activity_toast_dismisses_and_expires),
        ("reply_unknown_citation_warns", lambda h: reply_audit(h, "unknown_citation")),
        ("reply_wrong_message_count_warns", lambda h: reply_audit(h, "wrong_count")),
        ("reply_wrong_source_quote_warns", lambda h: reply_audit(h, "wrong_quote")),
        ("reply_correct_quote_does_not_claim_no_hallucination", lambda h: reply_audit(h, "correct_quote")),
        ("reply_no_evidence_requires_review", lambda h: reply_audit(h, "no_evidence")),
        ("old_reply_is_not_audited_as_new", old_reply_is_not_audited_as_new),
        ("chat_navigation_clears_audit_context", chat_navigation_clears_audit_context),
        ("synchronous_chat_switch_clears_audit_and_download", synchronous_chat_switch_clears_audit_and_download),
        ("manual_audit_uses_latest_link_pair", manual_audit_uses_latest_link_pair),
        ("navigation_cancels_manual_audit_fetch", navigation_cancels_manual_audit_fetch),
        ("share_nested_source_is_bounded", share_nested_source_is_bounded),
        ("popup_runtime_message_contract_mocked", popup_runtime_message_contract),
    ]
    if args.match:
        tests = [(name, test) for name, test in tests if args.match in name]
    results = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            harness = Harness(browser, args.extension_dir)
            for name, test in tests:
                try:
                    await asyncio.wait_for(test(harness), args.test_timeout)
                    result = {"test": name, "status": "passed"}
                except Exception as error:
                    result = {"test": name, "status": "failed", "error": type(error).__name__ + ": " + str(error)}
                results.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            await browser.close()
        if args.load_extension:
            try:
                result = await asyncio.wait_for(actual_unpacked_extension(playwright, args.extension_dir), 25)
            except Exception as error:
                result = {"mode": "actual_unpacked_extension", "status": "failed", "error": type(error).__name__ + ": " + str(error)}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    passed = sum(result["status"] == "passed" for result in results)
    summary = {"mode": "document_start_exact_script_injection", "tests": len(results), "passed": passed, "failed": sum(result["status"] == "failed" for result in results), "blocked": sum(result["status"] == "blocked" for result in results), "real_network": False}
    print(json.dumps(summary), flush=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return int(passed != len(results))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension-dir", type=Path, default=HERE)
    parser.add_argument("--match", help="Run tests whose names contain this string")
    parser.add_argument("--load-extension", action="store_true", help="Also launch Chromium with the real unpacked extension")
    parser.add_argument("--test-timeout", type=float, default=15)
    parser.add_argument("--report", type=Path, help="Save a machine-readable test report")
    args = parser.parse_args()
    args.extension_dir = args.extension_dir.resolve()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
