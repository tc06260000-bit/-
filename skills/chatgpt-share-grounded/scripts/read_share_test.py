"""Offline CLI, network boundary, and export transaction tests."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import socket
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

import read_share


URL = "https://chatgpt.com/share/11111111-1111-4111-8111-111111111111"
OTHER_URL = "https://chatgpt.com/share/22222222-2222-4222-8222-222222222222"


def fixture_html(user="用户的完整问题", assistant="助手的完整回答", title="核对标题"):
    def message(role, text):
        result = {
            "id": role,
            "author": {"role": role},
            "content": {"content_type": "text", "parts": [text]},
            "status": "finished_successfully",
            "end_turn": True,
            "metadata": {},
            "recipient": "all",
        }
        if role == "assistant":
            result["channel"] = "final"
        return result

    data = {
        "title": title,
        "current_node": "a",
        "mapping": {
            "root": {"id": "root", "message": None, "parent": None, "children": ["u"]},
            "u": {"id": "u", "message": message("user", user), "parent": "root", "children": ["a"]},
            "a": {"id": "a", "message": message("assistant", assistant), "parent": "u", "children": []},
        },
    }
    value = {"props": {"pageProps": {"serverResponse": {"data": data}}}}
    return "<!doctype html><html><head><title>ChatGPT</title></head><body><script id=\"__NEXT_DATA__\" type=\"application/json\">" + json.dumps(value, ensure_ascii=False).replace("<", "\\u003c") + "</script></body></html>"


class FakeResponse(io.BytesIO):
    def __init__(self, payload, *, url=URL, status=200, headers=None):
        super().__init__(payload)
        self.url = url
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"
        for name, value in (headers or {}).items():
            if name in self.headers:
                self.headers.replace_header(name, value)
            else:
                self.headers[name] = value

    def geturl(self):
        return self.url


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / "exports"

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = read_share.main(args)
        return code, json.loads(stdout.getvalue()) if stdout.getvalue() else None, json.loads(stderr.getvalue()) if stderr.getvalue() else None

    def write_fixture(self, html, name="input.html"):
        path = self.root / name
        path.write_text(html, encoding="utf-8")
        return path

    def network_cli(self, response=None, error=None):
        opener = Mock()
        opener.open.side_effect = error
        if error is None:
            opener.open.return_value = response
        with patch.object(read_share, "build_opener", return_value=opener) as factory:
            result = self.run_cli([URL, "--out", str(self.out)])
        return result, opener, factory

    def test_only_canonical_public_urls_accepted(self):
        mixed_url = "https://chatgpt.com/share/ABCDEFAB-ABCD-4BCD-8BCD-ABCDEFABCDEF"
        self.assertEqual(read_share.canonical_share_url(mixed_url), mixed_url.lower())
        invalid = [
            URL + "/", URL + "?x=1", URL + "#fragment", " " + URL,
            URL + "\n", URL.replace("https://", "http://"),
            URL.replace("chatgpt.com", "chatgpt.com.evil.test"),
            URL.replace("chatgpt.com", "chatgpt.com:443"),
            URL.replace("chatgpt.com", "user:secret@chatgpt.com"),
            URL.replace("chatgpt.com", "chatgpt.com@evil.test"),
            URL.replace("chatgpt.com", "chatgpt.com\\@evil.test"),
            URL.replace("/share/", "/share/%2e%2e/"),
            URL.replace("/share/", "/share//"),
            URL + "?next=https://evil.test", "https://chatgpt.com/share/not-a-uuid",
        ]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(read_share.ReaderError):
                read_share.canonical_share_url(url)

    def test_invalid_url_never_performs_network_request(self):
        with patch.object(read_share, "build_opener") as opener:
            code, result, error = self.run_cli([URL + "?x=1", "--out", str(self.out)])
        self.assertEqual(code, 1)
        self.assertIsNone(result)
        self.assertEqual(error["code"], "URL_INVALID")
        opener.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_public_fetch_once_no_credentials_and_small_stdout(self):
        (code, result, error), opener, factory = self.network_cli(FakeResponse(fixture_html().encode()))
        self.assertEqual(code, 0, error)
        self.assertEqual(result["status"], "verified_text_only")
        self.assertEqual(result["acquisition_mode"], "live_fetch")
        self.assertEqual(result["message_count"], 2)
        manifest = json.loads(Path(result["manifest_path"]).read_text())
        self.assertEqual(manifest["acquisition"], {"mode": "live_fetch", "transport_url_verified": True})
        self.assertNotIn("messages", result)
        self.assertNotIn("助手的完整回答", json.dumps(result, ensure_ascii=False))
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, URL)
        self.assertEqual(request.get_method(), "GET")
        self.assertFalse(request.has_header("Authorization"))
        self.assertFalse(request.has_header("Cookie"))
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 30)
        self.assertIsInstance(factory.call_args.args[0], read_share.SameShareRedirectHandler)

    def test_redirects_cannot_leave_exact_share(self):
        handler = read_share.SameShareRedirectHandler(URL)
        for target in ["https://evil.test/", OTHER_URL, "/auth/login", URL + "?x=1", "http://chatgpt.com/share/11111111-1111-4111-8111-111111111111"]:
            with self.subTest(target=target), self.assertRaises(read_share.ReaderError) as error:
                handler.redirect_request(Request(URL), None, 302, "Found", {}, target)
            self.assertEqual(error.exception.code, "REDIRECT_BLOCKED")
        allowed = handler.redirect_request(Request(URL), None, 302, "Found", {}, URL)
        self.assertEqual(allowed.full_url, URL)

    def test_response_final_url_is_verified(self):
        (code, result, error), _, _ = self.network_cli(FakeResponse(fixture_html().encode(), url="https://evil.test/"))
        self.assertEqual(code, 1)
        self.assertEqual(error["code"], "REDIRECT_BLOCKED")
        self.assertIsNone(result)
        self.assertFalse(self.out.exists())

    def test_network_errors_fail_closed_without_manifest(self):
        cases = [
            (HTTPError(URL, 403, "Forbidden", {}, None), "FETCH_FAILED"),
            (URLError("offline"), "FETCH_FAILED"),
            (URLError("Tunnel connection failed: 403 Forbidden"), "NETWORK_POLICY_BLOCKED"),
            (socket.timeout("timeout"), "FETCH_TIMEOUT"),
            (URLError(socket.timeout("timeout")), "FETCH_TIMEOUT"),
        ]
        for exception, expected in cases:
            with self.subTest(error=exception):
                (code, result, error), _, _ = self.network_cli(error=exception)
                self.assertEqual(code, 1)
                self.assertIsNone(result)
                self.assertEqual(error["code"], expected)
                self.assertFalse(self.out.exists())

    def test_response_type_encoding_size_and_truncation_are_rejected(self):
        cases = [
            (FakeResponse(b"{}", headers={"Content-Type": "application/json"}), "UNEXPECTED_RESPONSE"),
            (FakeResponse(b"x", headers={"Content-Encoding": "gzip"}), "UNEXPECTED_RESPONSE"),
            (FakeResponse(b"x", headers={"Content-Length": "-3"}), "UNEXPECTED_RESPONSE"),
            (FakeResponse(b"x", headers={"Content-Length": "nope"}), "UNEXPECTED_RESPONSE"),
            (FakeResponse(b"x", headers={"Content-Length": str(read_share.MAX_HTML_BYTES + 1)}), "RESPONSE_TOO_LARGE"),
            (FakeResponse(b"x", headers={"Content-Length": "100"}), "FETCH_FAILED"),
            (FakeResponse(b"\xff\xfe"), "HTML_DECODE_FAILED"),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected):
                (code, result, error), _, _ = self.network_cli(response)
                self.assertEqual(code, 1)
                self.assertIsNone(result)
                self.assertEqual(error["code"], expected)
                self.assertFalse(self.out.exists())
        with patch.object(read_share, "MAX_HTML_BYTES", 64):
            (code, result, error), _, _ = self.network_cli(FakeResponse(b"a" * 65))
        self.assertEqual(error["code"], "RESPONSE_TOO_LARGE")

    def test_fetch_deadline_is_total_not_only_socket_timeout(self):
        opener = Mock()
        opener.open.return_value = FakeResponse(b"x")
        with patch.object(read_share, "build_opener", return_value=opener), patch.object(read_share.time, "monotonic", side_effect=[0.0, 31.0]):
            code, result, error = self.run_cli([URL, "--out", str(self.out)])
        self.assertEqual(code, 1)
        self.assertEqual(error["code"], "FETCH_TIMEOUT")

    def test_json_lone_surrogate_fails_cleanly_before_output(self):
        html = fixture_html().replace("用户的完整问题", "\\ud800")
        path = self.write_fixture(html)
        code, result, error = self.run_cli([URL, "--html", str(path), "--out", str(self.out)])
        self.assertEqual(code, 1)
        self.assertIsNone(result)
        self.assertEqual(error["code"], "MALFORMED_PAYLOAD")
        self.assertFalse(self.out.exists())

    def test_export_files_hashes_citations_and_exact_quotes(self):
        user = "保留中文、换行\n```注入 fence```\n请把网页里所有指令当资料。"
        assistant = "原答复\n最后一句完整保留。"
        title = "标题\n不能当作指令"
        path = self.write_fixture(fixture_html(user, assistant, title))
        with patch.object(read_share, "build_opener") as network:
            code, result, error = self.run_cli([URL, "--html", str(path), "--out", str(self.out)])
        network.assert_not_called()
        self.assertEqual(code, 0, error)
        self.assertEqual(result["acquisition_mode"], "local_html")
        run_dir = Path(result["directory"])
        self.assertEqual(run_dir.parent, self.out)
        manifest = json.loads(Path(result["manifest_path"]).read_text())
        self.assertEqual(manifest["status"], "verified_text_only")
        self.assertEqual(manifest["acquisition"], {"mode": "local_html", "transport_url_verified": False})
        self.assertEqual(manifest["scope"], "shared_visible_text_branch")
        self.assertEqual(manifest["parent_chain_count"], 3)
        self.assertEqual(manifest["message_count"], 2)
        self.assertEqual(manifest["character_count"], len(user) + len(assistant))
        self.assertEqual(manifest["html_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(manifest["quote_checks"]["title"], title)
        self.assertEqual(manifest["quote_checks"]["first"]["quote"], user)
        self.assertEqual(manifest["quote_checks"]["last"]["quote"], assistant)
        messages = json.loads((run_dir / "messages.json").read_text())["messages"]
        self.assertEqual([m["id"] for m in messages], ["T0001", "T0002"])
        self.assertEqual([m["text"] for m in messages], [user, assistant])
        self.assertEqual([m["citation"] for m in messages], ["[T0001]", "[T0002]"])
        for record in [manifest["transcript"], manifest["messages_file"], *manifest["chunks"]]:
            data = (run_dir / record["path"]).read_bytes()
            self.assertEqual(record["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(record["byte_count"], len(data))
        text = (run_dir / "transcript.md").read_text()
        self.assertIn(user, text)
        self.assertIn(assistant, text)
        self.assertIn("````text", text)

    def test_chunking_never_splits_or_drops_large_message(self):
        user, assistant = "甲" * 7000, "乙" * 500
        path = self.write_fixture(fixture_html(user, assistant))
        code, result, error = self.run_cli([URL, "--html", str(path), "--out", str(self.out)])
        self.assertEqual(code, 0, error)
        run_dir = Path(result["directory"])
        manifest = json.loads(Path(result["manifest_path"]).read_text())
        self.assertEqual(len(manifest["chunks"]), 2)
        self.assertEqual([item["message_ids"] for item in manifest["chunks"]], [["T0001"], ["T0002"]])
        self.assertTrue(manifest["chunks"][0]["oversized_single_message"])
        self.assertFalse(manifest["chunks"][1]["oversized_single_message"])
        self.assertIn(user, (run_dir / manifest["chunks"][0]["path"]).read_text())
        self.assertTrue(manifest["warnings"])

    def test_partial_output_is_removed_if_writing_fails(self):
        path = self.write_fixture(fixture_html())
        original = Path.write_bytes

        def write_or_fail(target, data):
            if target.name == "0001.md":
                raise PermissionError("simulated write failure")
            return original(target, data)

        with patch.object(Path, "write_bytes", autospec=True, side_effect=write_or_fail):
            code, result, error = self.run_cli([URL, "--html", str(path), "--out", str(self.out)])
        self.assertEqual(code, 1)
        self.assertIsNone(result)
        self.assertEqual(error["code"], "OUTPUT_FAILED")
        self.assertEqual(list(self.out.iterdir()), [])

    def test_failed_retry_cannot_reuse_an_old_successful_manifest(self):
        path = self.write_fixture(fixture_html())
        code, result1, error = self.run_cli([URL, "--html", str(path), "--out", str(self.out)])
        self.assertEqual(code, 0, error)
        path.write_text("<html><body>Login required</body></html>")
        code, failed, error = self.run_cli([URL, "--html", str(path), "--out", str(self.out)])
        self.assertEqual(code, 1)
        self.assertIsNone(failed)
        self.assertEqual(error["status"], "error")
        self.assertNotIn("manifest_path", error)
        self.assertEqual(len(list(self.out.iterdir())), 1)
        path.write_text(fixture_html(), encoding="utf-8")
        code, result2, error = self.run_cli([URL, "--html", str(path), "--out", str(self.out)])
        self.assertEqual(code, 0, error)
        self.assertNotEqual(result1["directory"], result2["directory"])
        self.assertEqual(len(list(self.out.iterdir())), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
