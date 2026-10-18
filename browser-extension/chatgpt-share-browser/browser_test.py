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


def app_html(kind: str = "textarea", *, recognized: bool = True, ambiguous_send: bool = False, unrelated_form: bool = False) -> str:
    identifier = "prompt-textarea" if recognized else "unrecognized-composer"
    if kind == "textarea":
        editor = f'<textarea id="{identifier}" aria-label="Message ChatGPT"></textarea>'
    elif kind == "contenteditable":
        editor = f'<div id="{identifier}" class="ProseMirror" contenteditable="true" role="textbox" aria-label="Message ChatGPT"></div>'
    elif kind == "october":
        editor = '<div contenteditable="true" role="textbox" aria-label="Message ChatGPT"></div>'
    elif kind == "lexical":
        editor = '<div data-testid="prompt-textarea" data-lexical-editor="true" contenteditable="true" role="textbox" aria-label="Message ChatGPT"></div>'
    else:
        raise ValueError("Unknown mock editor kind")
    if not recognized:
        editor = '<input type="text" id="unrecognized-composer" aria-label="Unknown composer">'
    modern = kind in ("october", "lexical")
    form_attributes = 'data-type="unknown-composer"' if not recognized else ('data-chatgpt-composer' if modern else 'data-type="unified-composer"')
    send_button = '<button type="submit" aria-label="发送">发送</button>' if modern else '<button type="submit" data-testid="send-button" aria-label="Send prompt">Send</button>'
    if ambiguous_send:
        send_button += '<button type="submit" aria-label="发送">Duplicate send</button>'
    unrelated = '<form id="unrelated-form"><button type="submit" data-testid="send-button" aria-label="发送">Another form send</button></form>' if unrelated_form else ''
    return """<!doctype html><html><head><meta charset="utf-8"><title>Mock ChatGPT composer</title>
<style>body{font-family:sans-serif}textarea,[contenteditable]{display:block;width:700px;height:120px;border:1px solid black;padding:8px}button{padding:10px}#app-status{margin:15px}</style>
</head><body>""" + unrelated + "<main><form " + form_attributes + ">" + editor + send_button + """
</form><div id="app-status">Mock page</div></main><script>
window.__sent = [];
window.__inputEvents = [];
window.__draft = '';
window.__unrelatedSends = 0;
const form = document.querySelector('main form');
const editor = document.querySelector('textarea,input,[contenteditable]');
const send = form.querySelector('button[type="submit"]');
const unrelated = document.querySelector('#unrelated-form');
if (unrelated) unrelated.addEventListener('submit', event => { event.preventDefault(); window.__unrelatedSends++; });
function text() { return 'value' in editor ? editor.value : editor.innerText; }
function clear() { if ('value' in editor) editor.value = ''; else editor.textContent = ''; }
editor.addEventListener('input', () => { window.__draft = text(); window.__inputEvents.push(text()); });
editor.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    form.requestSubmit(send);
  }
});
form.addEventListener('submit', event => {
  event.preventDefault();
  // Model a controlled composer: changing DOM without an input event is not
  // enough to update the application's actual outbound message.
  window.__sent.push({text: window.__draft, url: location.href});
  window.__draft = '';
  clear();
});
</script></body></html>"""


@dataclass
class Scenario:
    kind: str = "textarea"
    recognized: bool = True
    ambiguous_send: bool = False
    unrelated_form: bool = False
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
        if parsed.scheme == "https" and parsed.netloc == "chatgpt.com" and parsed.path.startswith("/share/"):
            self.share_requests.append(url)
            self.requested.set()
            if self.delay:
                await self.release.wait()
            await route.fulfill(status=self.response_status, content_type="text/html; charset=utf-8", body=self.response_html)
        elif parsed.scheme == "https" and parsed.netloc == "chatgpt.com" and route.request.is_navigation_request():
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=app_html(self.kind, recognized=self.recognized, ambiguous_send=self.ambiguous_send, unrelated_form=self.unrelated_form))
        else:
            self.other_requests.append(url)
            await route.abort("blockedbyclient")


class Harness:
    def __init__(self, browser: Browser, extension_dir: Path):
        self.browser = browser
        self.extension_dir = extension_dir
        self.scripts = "\n;\n".join((extension_dir / filename).read_text(encoding="utf-8") for filename in ("core.js", "content.js"))

    async def open(self, scenario: Scenario) -> tuple[BrowserContext, Page]:
        context = await self.browser.new_context(service_workers="block")
        await context.route("**/*", scenario.route)
        await context.add_init_script(script=self.scripts)
        page = await context.new_page()
        page.on("pageerror", lambda error: scenario.page_errors.append(str(error)))
        page.set_default_timeout(5000)
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
        if scenario.page_errors:
            await context.close()
            raise AssertionError("Document-start script error: " + "; ".join(scenario.page_errors))
        await page.wait_for_selector("#share-link-guard-status")
        return context, page


async def editor_text(page: Page) -> str:
    return await page.locator("#prompt-textarea").evaluate("e => 'value' in e ? e.value : e.innerText")


async def status_text(page: Page) -> str:
    return await page.locator("#share-link-guard-status").inner_text()


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
        text = await status_text(page)
        assert "未保护" in text, "Unrecognized composer was not visibly reported as unprotected: " + text
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
        text = await status_text(page)
        assert "未保护" in text, "Ambiguous send controls were not visibly reported as unprotected: " + text
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
        assert "未保护" in await status_text(page), "Old error notice concealed loss of protection"
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
