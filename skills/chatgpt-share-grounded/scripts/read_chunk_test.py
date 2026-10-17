"""Read-back integrity and bounded-pagination tests using real offline exports.

These checks distinguish successful file verification from actual source
reading.  Mutations refresh outer hashes where needed, so semantic consistency
checks cannot accidentally be replaced by a checksum-only implementation.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import read_chunk
import read_share
from read_share_test import URL, OTHER_URL, fixture_html


class ChunkReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sequence = 0

    def tearDown(self):
        self.temporary.cleanup()

    def run_chunk(self, manifest_path, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = read_chunk.main([str(manifest_path), *map(str, args)])
        result = json.loads(stdout.getvalue()) if stdout.getvalue() else None
        error = json.loads(stderr.getvalue()) if stderr.getvalue() else None
        return code, result, error

    def make_export(self, user="用户原文：中文与 emoji 🧪。", assistant="助手原文：回答完整保留。"):
        self.sequence += 1
        html_path = self.root / f"input-{self.sequence}.html"
        html_path.write_text(fixture_html(user, assistant), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), patch.object(read_share, "fetch_html") as network:
            code = read_share.main([URL, "--html", str(html_path), "--out", str(self.root / "exports")])
        network.assert_not_called()
        self.assertEqual(code, 0, stderr.getvalue())
        manifest_path = Path(json.loads(stdout.getvalue())["manifest_path"])
        return manifest_path, json.loads(manifest_path.read_text(encoding="utf-8"))

    @staticmethod
    def save_manifest(path, manifest):
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def refresh_record(base, record):
        data = (base / record["path"]).read_bytes()
        record["byte_count"] = len(data)
        record["sha256"] = hashlib.sha256(data).hexdigest()
        if "character_count" in record:
            record["character_count"] = len(data.decode("utf-8"))

    def modify_messages(self, path, manifest, mutate):
        record = manifest["messages_file"]
        message_path = path.parent / record["path"]
        document = json.loads(message_path.read_text(encoding="utf-8"))
        mutate(document)
        message_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.refresh_record(path.parent, record)
        self.save_manifest(path, manifest)

    def modify_text_file(self, path, manifest, record, mutate):
        target = path.parent / record["path"]
        original = target.read_text(encoding="utf-8")
        target.write_text(mutate(original), encoding="utf-8")
        self.refresh_record(path.parent, record)
        self.save_manifest(path, manifest)

    def assert_rejected(self, path):
        code, result, error = self.run_chunk(path, "--verify-only")
        self.assertEqual(code, 1, result)
        self.assertIsNone(result)
        self.assertEqual(error["status"], "error")
        self.assertEqual(error["code"], "EXPORT_VERIFICATION_FAILED")
        self.assertTrue(error["message"])

    def read_all_pages(self, path, source_option, source_id, limit=17):
        offset, pieces, pages = 0, [], []
        while True:
            code, result, error = self.run_chunk(path, source_option, source_id, "--offset", offset, "--limit", limit)
            self.assertEqual(code, 0, error)
            self.assertEqual(result["status"], "source_page")
            self.assertEqual(result["id"], source_id)
            self.assertEqual(result["source_url"], URL)
            self.assertEqual(result["offset"], offset)
            self.assertTrue(result["text"])
            self.assertLessEqual(len(result["text"]), limit)
            self.assertEqual(result["end_offset"], offset + len(result["text"]))
            pieces.append(result["text"])
            pages.append(result)
            if result["next_offset"] is None:
                self.assertEqual(result["end_offset"], result["total_characters"])
                break
            self.assertEqual(result["next_offset"], result["end_offset"])
            self.assertGreater(result["next_offset"], offset)
            offset = result["next_offset"]
            self.assertLess(len(pages), 20000, "pagination did not terminate")
        reconstructed = "".join(pieces)
        self.assertEqual(len(reconstructed), pages[-1]["total_characters"])
        return reconstructed, pages

    def test_verify_only_is_integrity_report_not_a_read_receipt(self):
        user = "只校验文件时不能输出这句正文"
        path, manifest = self.make_export(user=user)
        code, result, error = self.run_chunk(path, "--verify-only")
        self.assertEqual(code, 0, error)
        self.assertEqual(result["status"], "files_verified")
        self.assertEqual(result["message_count"], 2)
        self.assertEqual(result["chunk_count"], len(manifest["chunks"]))
        self.assertEqual(result["extraction_status"], "verified_text_only")
        self.assertEqual(result["acquisition"]["mode"], "local_html")
        self.assertFalse(result["acquisition"]["transport_url_verified"])
        self.assertIn("不代表模型已读", result["notice"])
        self.assertNotIn(user, json.dumps(result, ensure_ascii=False))
        self.assertNotIn("text", result)
        self.assertNotIn("read_complete", result)
        self.assertNotIn("coverage_complete", result)
        self.assertEqual(manifest["acquisition"]["mode"], "local_html")

    def test_inconsistent_acquisition_claims_are_rejected(self):
        for value in [None, {}, {"mode": "unknown", "transport_url_verified": False},
                      {"mode": "local_html", "transport_url_verified": True},
                      {"mode": "live_fetch", "transport_url_verified": False},
                      {"mode": "live_fetch", "transport_url_verified": 1}]:
            with self.subTest(acquisition=value):
                path, manifest = self.make_export()
                manifest["acquisition"] = value
                self.save_manifest(path, manifest)
                self.assert_rejected(path)

    def test_chunk_pages_reconstruct_exact_unicode_and_quoted_text(self):
        user = '用户说："原样保留"\n```text\n[T9999] fake\n````\n组合字符 e\u0301 与 🧪 和尾部空格  '
        assistant = "第一行\r\n第二行\t反斜杠 \\ 及 NUL \x00\n最后原句。"
        path, manifest = self.make_export(user, assistant)
        for chunk in manifest["chunks"]:
            with self.subTest(chunk=chunk["id"]):
                actual, pages = self.read_all_pages(path, "--chunk", chunk["id"])
                expected = (path.parent / chunk["path"]).read_bytes().decode("utf-8")
                self.assertEqual(actual, expected)
                self.assertGreater(len(pages), 1)
        for tid, expected in [("T0001", user), ("T0002", assistant)]:
            with self.subTest(message=tid):
                actual, _ = self.read_all_pages(path, "--message", tid, limit=11)
                self.assertEqual(actual, expected)

    def test_one_oversized_message_survives_many_pages_and_chunk_boundaries(self):
        user = "开头证据\n" + "中段甲🙂\n" * 1800 + "末尾证据"
        assistant = "另一条消息也必须保留。"
        path, manifest = self.make_export(user, assistant)
        self.assertEqual([item["message_ids"] for item in manifest["chunks"]], [["T0001"], ["T0002"]])
        self.assertTrue(manifest["chunks"][0]["oversized_single_message"])
        actual, pages = self.read_all_pages(path, "--message", "T0001", limit=997)
        self.assertEqual(actual, user)
        self.assertGreater(len(pages), 8)
        for record in manifest["chunks"]:
            actual, _ = self.read_all_pages(path, "--chunk", record["id"], limit=1000)
            self.assertEqual(actual, (path.parent / record["path"]).read_bytes().decode("utf-8"))

    def test_offset_at_end_or_beyond_never_returns_an_empty_success_page(self):
        path, manifest = self.make_export()
        sources = [("--chunk", manifest["chunks"][0]["id"], manifest["chunks"][0]["character_count"]),
                   ("--message", "T0001", len("用户原文：中文与 emoji 🧪。"))]
        for option, source_id, length in sources:
            for offset in [length, length + 1, length + 100]:
                with self.subTest(source=source_id, offset=offset):
                    code, result, error = self.run_chunk(path, option, source_id, "--offset", offset)
                    self.assertEqual(code, 1, result)
                    self.assertIsNone(result)
                    self.assertEqual(error["status"], "error")
                    self.assertIn("offset", error["message"])

    def test_negative_offset_and_invalid_limits_are_rejected(self):
        path, _ = self.make_export()
        for args in [("--offset", -1), ("--limit", 0), ("--limit", -1), ("--limit", 4001)]:
            with self.subTest(args=args):
                code, result, error = self.run_chunk(path, "--message", "T0001", *args)
                self.assertEqual(code, 1, result)
                self.assertIsNone(result)
                self.assertEqual(error["status"], "error")

    def test_missing_or_non_integer_offset_is_a_cli_error(self):
        path, _ = self.make_export()
        for suffix in [["--offset"], ["--offset", ""], ["--offset", "1.5"]]:
            with self.subTest(suffix=suffix), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                read_chunk.main([str(path), "--message", "T0001", *suffix])
            self.assertEqual(caught.exception.code, 2)

    def test_unknown_chunk_or_message_cannot_be_read(self):
        path, _ = self.make_export()
        for option, identifier in [("--chunk", "C9999"), ("--message", "T9999")]:
            with self.subTest(option=option):
                code, result, error = self.run_chunk(path, option, identifier)
                self.assertEqual(code, 1, result)
                self.assertIsNone(result)
                self.assertEqual(error["status"], "error")

    def test_file_hash_corruption_is_rejected_for_every_export_kind(self):
        for key in ["transcript", "messages_file", "chunk"]:
            with self.subTest(kind=key):
                path, manifest = self.make_export()
                record = manifest["chunks"][0] if key == "chunk" else manifest[key]
                target = path.parent / record["path"]
                original = target.read_bytes()
                # Keep the byte count stable, so the digest is the failing check.
                target.write_bytes(bytes([original[0] ^ 1]) + original[1:])
                self.assert_rejected(path)

    def test_file_byte_count_corruption_is_rejected(self):
        for key in ["transcript", "messages_file", "chunk"]:
            with self.subTest(kind=key):
                path, manifest = self.make_export()
                record = manifest["chunks"][0] if key == "chunk" else manifest[key]
                record["byte_count"] += 1
                self.save_manifest(path, manifest)
                self.assert_rejected(path)

    def test_quote_check_metadata_cannot_contradict_source(self):
        for target, field, bad in [
            ("first", "quote", "伪造首句"), ("last", "quote", "伪造末句"),
            ("first", "id", "T0002"), ("last", "id", "T0001"),
            ("first", "role", "assistant"), ("last", "role", "user"),
            (None, "title", "伪造标题"),
        ]:
            with self.subTest(target=target, field=field):
                path, manifest = self.make_export()
                holder = manifest["quote_checks"][target] if target else manifest["quote_checks"]
                holder[field] = bad
                self.save_manifest(path, manifest)
                self.assert_rejected(path)

    def test_message_level_hash_character_count_and_citation_are_checked(self):
        for field, value in [("text_sha256", "0" * 64), ("character_count", 999), ("citation", "[T9999]")]:
            with self.subTest(field=field):
                path, manifest = self.make_export()
                self.modify_messages(path, manifest, lambda document, f=field, v=value: document["messages"][0].__setitem__(f, v))
                self.assert_rejected(path)

    def test_message_file_metadata_must_match_manifest(self):
        for field, value in [("source_url", OTHER_URL), ("scope", "all_private_history"),
                             ("title", "另一份标题"), ("schema_version", 99)]:
            with self.subTest(field=field):
                path, manifest = self.make_export()
                self.modify_messages(path, manifest, lambda document, f=field, v=value: document.__setitem__(f, v))
                self.assert_rejected(path)

    def test_manifest_and_chunk_character_counts_are_checked(self):
        for target in ["manifest", "chunk"]:
            with self.subTest(target=target):
                path, manifest = self.make_export()
                holder = manifest if target == "manifest" else manifest["chunks"][0]
                holder["character_count"] += 1
                self.save_manifest(path, manifest)
                self.assert_rejected(path)

    def test_manifest_message_count_and_ordered_ids_are_checked(self):
        mutations = [
            lambda m: m.__setitem__("message_count", 1),
            lambda m: m["chunks"][0].__setitem__("message_ids", ["T0002", "T0001"]),
            lambda m: m["chunks"][0].__setitem__("message_ids", ["T0001"]),
            lambda m: m["chunks"][0].__setitem__("message_ids", ["T0001", "T0001"]),
            lambda m: m["chunks"][0].__setitem__("id", "C0002"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                path, manifest = self.make_export()
                mutate(manifest)
                self.save_manifest(path, manifest)
                self.assert_rejected(path)

    def test_message_file_ids_cannot_be_reordered_even_with_updated_hash(self):
        path, manifest = self.make_export()
        self.modify_messages(path, manifest, lambda document: document["messages"].reverse())
        self.assert_rejected(path)

    def test_reordered_source_blocks_cannot_pass_rehashed_chunk(self):
        path, manifest = self.make_export()
        def reorder(text):
            boundary = text.index("## [T0002]")
            return text[boundary:] + text[:boundary]
        self.modify_text_file(path, manifest, manifest["chunks"][0], reorder)
        self.assert_rejected(path)

    def test_forged_extra_source_block_cannot_pass_rehashed_files(self):
        for key in ["transcript", "chunk"]:
            with self.subTest(kind=key):
                path, manifest = self.make_export()
                record = manifest["chunks"][0] if key == "chunk" else manifest[key]
                self.modify_text_file(path, manifest, record, lambda text: text + "## [T9999] assistant\n\n伪造新增内容\n")
                self.assert_rejected(path)

    def test_substring_in_other_message_cannot_hide_a_missing_body(self):
        path, manifest = self.make_export("原文", "较长原文")
        self.modify_text_file(path, manifest, manifest["chunks"][0], lambda text: text.replace("\n原文\n", "\n正文丢失\n", 1))
        self.assert_rejected(path)

    def test_traversal_and_absolute_file_paths_are_rejected(self):
        for mode in ["traversal", "absolute"]:
            with self.subTest(mode=mode):
                path, manifest = self.make_export()
                record = manifest["chunks"][0]
                external = path.parent.parent / f"external-{self.sequence}.md"
                external.write_bytes((path.parent / record["path"]).read_bytes())
                record["path"] = "../" + external.name if mode == "traversal" else str(external)
                self.save_manifest(path, manifest)
                self.assert_rejected(path)

    def test_symlink_file_cannot_alias_inside_or_outside_export(self):
        for target_scope in ["inside", "outside"]:
            with self.subTest(target=target_scope):
                path, manifest = self.make_export()
                chunk_path = path.parent / manifest["chunks"][0]["path"]
                target = (path.parent if target_scope == "inside" else self.root) / f"linked-{self.sequence}.md"
                target.write_bytes(chunk_path.read_bytes())
                chunk_path.unlink()
                try:
                    chunk_path.symlink_to(target)
                except (OSError, NotImplementedError) as exc:
                    self.skipTest(f"symlinks unavailable: {exc}")
                self.assert_rejected(path)

    def test_symlink_directory_component_is_rejected(self):
        path, manifest = self.make_export()
        chunks = path.parent / "chunks"
        actual = path.parent / "actual-chunks"
        chunks.rename(actual)
        try:
            chunks.symlink_to(actual, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.assert_rejected(path)

    def test_manifest_symlink_is_rejected(self):
        path, _ = self.make_export()
        alias = path.parent / "manifest-alias.json"
        try:
            alias.symlink_to(path)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.assert_rejected(alias)

    def test_missing_or_empty_source_files_are_not_valid_exports(self):
        for mode in ["missing", "empty"]:
            with self.subTest(mode=mode):
                path, manifest = self.make_export()
                record = manifest["chunks"][0]
                chunk_path = path.parent / record["path"]
                if mode == "missing":
                    chunk_path.unlink()
                else:
                    chunk_path.write_bytes(b"")
                    self.refresh_record(path.parent, record)
                    self.save_manifest(path, manifest)
                self.assert_rejected(path)


if __name__ == "__main__":
    unittest.main()
