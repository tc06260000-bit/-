"""Strict, dependency-free parsing of public ChatGPT share-page HTML.

Format reference: roykollensvendsen/chatgpt-share-reader (MIT),
skill/chatgpt-share-reader/scripts/read_chatgpt_share.py. This independently
written parser uses the documented React Router flat-array representation but
fails closed on broken branches, unknown visible content, and attachments.
It never executes JavaScript, follows embedded instructions, or fetches assets.
"""

from __future__ import annotations

import json
import re
from collections import deque
from typing import Any

MAX_HTML_CHARS = 12 * 1024 * 1024
MAX_ENTRIES = 250_000
MAX_DEPTH = 150
MAX_BRANCH_NODES = 25_000
SCOPE = "仅校验分享页当前分支的可见文本；不代表原账号未分享的内容，也不保证模型完整保留上下文。"


class ShareParseError(ValueError):
    """An explicit failure; callers must not fall back to guessed content."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise ShareParseError(code, message)


def _json_loads(text: str) -> Any:
    if len(text) > MAX_HTML_CHARS:
        _fail("LIMIT_EXCEEDED", "分享页数据过大，已停止读取。")
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                _fail("LIMIT_EXCEEDED", "分享页数据嵌套过深，已停止读取。")
        elif char in "]}":
            depth -= 1

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail("MALFORMED_PAYLOAD", "分享页包含重复的 JSON 字段。")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        _fail("MALFORMED_PAYLOAD", "分享页包含非标准 JSON 数值。")

    try:
        return json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (json.JSONDecodeError, RecursionError, OverflowError) as error:
        raise ShareParseError("MALFORMED_PAYLOAD", "分享页数据不完整或 JSON 格式已改变。") from error


def _decode_flat(table: Any) -> Any:
    if not isinstance(table, list) or not table:
        _fail("MALFORMED_PAYLOAD", "分享页主数据不是有效的非空数组。")
    if len(table) > MAX_ENTRIES:
        _fail("LIMIT_EXCEEDED", "分享页数据项过多，已停止读取。")
    memo: dict[int, Any] = {}
    pending: deque[tuple[Any, Any, int]] = deque()
    sentinels = {-1: None, -2: float("nan"), -3: float("-inf"), -4: -0.0,
                 -5: None, -6: float("inf"), -7: None}

    def ref(index: Any, depth: int) -> Any:
        # bool is an int subclass in Python, but never a valid encoded reference.
        if type(index) is not int:
            _fail("MALFORMED_PAYLOAD", "分享页包含无效的数据引用。")
        if index < 0:
            if index not in sentinels:
                _fail("MALFORMED_PAYLOAD", "分享页包含未知的特殊引用。")
            return sentinels[index]
        if index >= len(table):
            _fail("MALFORMED_PAYLOAD", "分享页存在越界的数据引用。")
        if index in memo:
            return memo[index]
        if depth > MAX_DEPTH:
            _fail("LIMIT_EXCEEDED", "分享页引用结构过深，已停止读取。")
        value = table[index]
        if not isinstance(value, (dict, list)):
            memo[index] = value
            return value
        if isinstance(value, list) and value and isinstance(value[0], str):
            # Turbo-stream tagged values (Date, Promise, etc.) are opaque data.
            # Never execute or mistake them for transcript text.
            opaque = {"__share_guard_opaque_type__": value[0]}
            memo[index] = opaque
            return opaque
        target: Any = [] if isinstance(value, list) else {}
        memo[index] = target
        pending.append((value, target, depth))
        return target

    root = ref(0, 0)
    while pending:
        value, target, depth = pending.popleft()
        if isinstance(value, list):
            target.extend(ref(index, depth + 1) for index in value)
        else:
            for key, index in value.items():
                name = key
                if re.fullmatch(r"_\d+", key):
                    key_index = int(key[1:])
                    if key_index >= len(table) or not isinstance(table[key_index], str):
                        _fail("MALFORMED_PAYLOAD", "分享页包含无效的对象键引用。")
                    name = table[key_index]
                if name in target:
                    _fail("MALFORMED_PAYLOAD", "分享页包含重复的对象字段。")
                target[name] = ref(index, depth + 1)
    return root


_ENQUEUE = re.compile(r'streamController\.enqueue\s*\(\s*("(?:[^"\\]|\\[\s\S])*")\s*\)')
_NEXT_DATA = re.compile(
    r'<script\b[^>]*\bid\s*=\s*["\']__NEXT_DATA__["\'][^>]*>([\s\S]*?)</script\s*>', re.I
)


def _extract_roots(html: str) -> list[tuple[Any, str]]:
    roots: list[tuple[Any, str]] = []
    chunks = []
    for match in _ENQUEUE.finditer(html):
        chunk = _json_loads(match[1])
        if not isinstance(chunk, str):
            _fail("MALFORMED_PAYLOAD", "分享页的流数据不是字符串。")
        chunks.append(chunk)
        if len(chunks) > 10_000:
            _fail("LIMIT_EXCEEDED", "分享页的流数据分片过多。")
    if chunks:
        stream = "".join(chunks)
        # Later newline-delimited Pn: records resolve promises. They are not
        # silently substituted for a missing conversation in the initial data.
        roots.append((_decode_flat(_json_loads(stream.split("\n", 1)[0])), "react-router"))
    for match in _NEXT_DATA.finditer(html):
        roots.append((_json_loads(match[1]), "next-data"))
    if not roots:
        _fail("NO_CONVERSATION", "没有找到分享对话数据。链接可能失效、需要登录、被验证页拦截，或页面格式已改变。")
    return roots


def _find_conversation(roots: list[tuple[Any, str]]) -> tuple[dict[str, Any], str]:
    candidates: list[tuple[dict[str, Any], str]] = []
    visited: set[int] = set()
    pending = deque((value, source_format, 0) for value, source_format in roots)
    inspected = 0
    while pending:
        value, source_format, depth = pending.popleft()
        if not isinstance(value, (dict, list)) or id(value) in visited:
            continue
        inspected += 1
        if inspected > MAX_ENTRIES or depth > MAX_DEPTH:
            _fail("LIMIT_EXCEEDED", "分享页结构过大或过深，已停止读取。")
        visited.add(id(value))
        if isinstance(value, dict) and "mapping" in value and "current_node" in value:
            candidates.append((value, source_format))
            # Transcript strings are data; do not search them for instructions
            # or parse them as another embedded conversation.
            continue
        for child in value.values() if isinstance(value, dict) else value:
            if isinstance(child, (dict, list)):
                pending.append((child, source_format, depth + 1))
    if not candidates:
        _fail("NO_CONVERSATION", "未找到完整对话结构，不能确认已读取全文。")
    if len(candidates) != 1:
        _fail("AMBIGUOUS_CONVERSATION", "页面包含多个对话数据，无法可靠确认应读取哪一份。")
    return candidates[0]


def _has_attachment(message: dict[str, Any]) -> bool:
    fields = ("attachments", "files", "file_ids", "image_url", "image_urls", "audio", "video")
    for holder in (message, message.get("metadata"), message.get("content")):
        if isinstance(holder, dict) and any(holder.get(key) for key in fields):
            return True
    return False


def _visible_text(message: Any) -> tuple[str, str] | None:
    if not isinstance(message, dict) or not isinstance(message.get("author"), dict):
        _fail("UNSUPPORTED_FORMAT", "消息作者结构未知，无法确认完整性。")
    role = message["author"].get("role")
    metadata = message.get("metadata") or {}
    content = message.get("content") or {}
    if not isinstance(metadata, dict) or not isinstance(content, dict):
        _fail("UNSUPPORTED_FORMAT", "消息元数据或内容结构未知。")
    kind = content.get("content_type")
    if (role in ("system", "developer", "tool")
        or metadata.get("is_visually_hidden_from_conversation") or metadata.get("is_hidden")
        or kind in ("thoughts", "reasoning_recap", "model_editable_context", "user_editable_context")
        or message.get("channel") in ("analysis", "justify", "confidence")
        or (role == "assistant" and message.get("recipient") not in (None, "", "all"))):
        return None
    if role not in ("user", "assistant"):
        _fail("UNSUPPORTED_FORMAT", "出现未知的可见消息角色，已停止读取。")
    if message.get("channel") not in (None, "", "final", "commentary"):
        _fail("UNSUPPORTED_FORMAT", "出现未知消息频道，无法确认可见文本。")
    if "status" in message and message["status"] != "finished_successfully":
        _fail("INCOMPLETE_MESSAGE", "当前分支包含尚未成功完成的可见消息，无法确认内容完整，已停止读取。")
    if _has_attachment(message):
        _fail("UNSUPPORTED_ATTACHMENT", "分享对话含有附件、图片、音频或视频；当前工具不能验证这些内容，已停止读取。")
    if kind not in ("text", "multimodal_text", "code"):
        code = "UNSUPPORTED_ATTACHMENT" if re.search(r"image|audio|video|file|multimodal", str(kind), re.I) else "UNSUPPORTED_FORMAT"
        _fail(code, "出现无法完整读取的可见内容类型：" + str(kind) + "。已停止读取。")
    if kind == "code" and isinstance(content.get("text"), str):
        text = content["text"]
    elif isinstance(content.get("parts"), list):
        parts: list[str] = []
        for part in content["parts"]:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("content_type") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
            else:
                part_kind = str(part.get("content_type", "")) if isinstance(part, dict) else ""
                code = "UNSUPPORTED_ATTACHMENT" if re.search(r"image|audio|video|file|asset", part_kind, re.I) else "UNSUPPORTED_FORMAT"
                _fail(code, "消息含有图片、音视频、附件或未知片段，无法确认全文，已停止读取。")
        text = "\n".join(parts)
    else:
        _fail("UNSUPPORTED_FORMAT", "可见文本消息缺少可读取的文本片段。")
    if not text.strip():
        _fail("EMPTY_MESSAGE", "当前分支存在空白的可见消息，无法确认内容完整。")
    return role, text


def parse_share_html(html: str) -> dict[str, Any]:
    """Return the checked current-branch text, or raise ShareParseError.

    A success only verifies the available share-page branch. It cannot verify
    unshared turns, private attachments, the source author's factual accuracy,
    or the receiving model's context capacity or future answers.
    """
    if not isinstance(html, str) or not html.strip():
        _fail("EMPTY_PAGE", "分享页面为空，已停止读取。")
    if len(html) > MAX_HTML_CHARS:
        _fail("LIMIT_EXCEEDED", "分享页面过大，已停止读取。")
    if (re.search(r"<title[^>]*>\s*(?:Just a moment|Attention Required|Access denied|Verify you are human)", html, re.I)
        or re.search(r'''id=["']cf-challenge-running["']|class=["']cf-challenge''', html, re.I)):
        _fail("CHALLENGE_PAGE", "分享链接返回验证或访问限制页面；请先确认链接可以公开打开。")
    conversation, source_format = _find_conversation(_extract_roots(html))
    mapping = conversation["mapping"]
    node_id = conversation["current_node"]
    if not isinstance(mapping, dict) or not mapping or not isinstance(node_id, str):
        _fail("BROKEN_BRANCH", "分享页的当前分支结构不完整。")
    if len(mapping) > MAX_BRANCH_NODES:
        _fail("LIMIT_EXCEEDED", "分享页包含过多消息节点，已停止读取。")
    chain: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    while node_id is not None:
        if not isinstance(node_id, str) or node_id not in mapping:
            _fail("BROKEN_BRANCH", "当前分支引用了不存在的消息，已停止读取。")
        if node_id in seen:
            _fail("BRANCH_CYCLE", "当前对话分支存在循环，已停止读取。")
        if len(chain) >= MAX_BRANCH_NODES:
            _fail("LIMIT_EXCEEDED", "当前对话分支过长，已停止读取。")
        seen.add(node_id)
        node = mapping[node_id]
        if not isinstance(node, dict):
            _fail("BROKEN_BRANCH", "消息节点格式无效。")
        if node.get("id", node_id) != node_id:
            _fail("BROKEN_BRANCH", "消息节点 ID 与分支映射不一致。")
        if "parent" not in node:
            _fail("BROKEN_BRANCH", "消息缺少父节点信息，无法确认已读到分支起点。")
        chain.append((node_id, node))
        node_id = node.get("parent")
    messages = []
    hidden_messages = 0
    for node_id, node in reversed(chain):
        if node.get("message") is None:
            continue
        visible = _visible_text(node["message"])
        if visible is None:
            hidden_messages += 1
            continue
        role, text = visible
        messages.append({"node_id": node_id, "number": len(messages) + 1, "role": role, "text": text})
    if not messages:
        _fail("EMPTY_CONVERSATION", "当前分支没有可校验的可见文本消息。")
    return {
        "title": conversation.get("title") if isinstance(conversation.get("title"), str) else "",
        "messages": messages,
        "parent_chain_count": len(chain),
        "node_count": len(mapping),
        "hidden_messages": hidden_messages,
        "source_format": source_format,
        "scope": SCOPE,
        "branch_validated": True,
    }
