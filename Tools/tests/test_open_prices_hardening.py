from __future__ import annotations

import hashlib
import io
import json
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "Tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import open_prices_acquire as acquire_module
import open_prices_common as common
from open_prices_acquire import acquire
from open_prices_normalize import normalize_snapshot


class _FakeResponse:
    def __init__(self, *, url: str, payload: bytes, content_length: str | None) -> None:
        self._url = url
        self._stream = io.BytesIO(payload)
        self.headers = {} if content_length is None else {"Content-Length": content_length}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def geturl(self) -> str:
        return self._url

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


class _FakeOpener:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    def open(self, request, timeout=60):
        return self.response


class OpenPricesAcquisitionHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = common.load_source_policy(ROOT / common.DEFAULT_SOURCE_POLICY)

    def test_strict_json_record_parser_rejects_ambiguous_or_unsafe_json(self) -> None:
        valid = b'{"id":1,"name":"safe"}\n'
        self.assertEqual(acquire_module._strict_json_object(valid), {"id": 1, "name": "safe"})
        for payload in (
            b'{"id":1,"id":2}\n',
            b'{"price":NaN}\n',
            b'{"value":"\\u0001"}\n',
            '{"value":"safe"}'.encode("utf-16"),
            b'[1,2,3]\n',
        ):
            with self.subTest(payload=payload[:40]):
                self.assertIsNone(acquire_module._strict_json_object(payload))

    def test_network_preflight_rejects_private_resolution_and_accepts_public_resolution(self) -> None:
        url = self.policy.export_urls["prices"]
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with mock.patch.object(socket, "getaddrinfo", return_value=private):
            with self.assertRaisesRegex(common.AdapterError, "network security policy"):
                acquire_module._validate_network_target("prices", url, self.policy)

        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with mock.patch.object(socket, "getaddrinfo", return_value=public):
            acquire_module._validate_network_target("prices", url, self.policy)

    def test_redirect_is_rejected_before_urllib_can_follow_it(self) -> None:
        handler = acquire_module._RejectRedirect()
        request = urllib.request.Request(self.policy.export_urls["prices"])
        with self.assertRaisesRegex(common.AdapterError, "redirects are forbidden"):
            handler.redirect_request(
                request,
                io.BytesIO(),
                302,
                "Found",
                {},
                "https://127.0.0.1/latest/meta-data",
            )

    def test_download_rejects_content_length_mismatch(self) -> None:
        url = self.policy.export_urls["prices"]
        response = _FakeResponse(url=url, payload=b"abc", content_length="5")
        with mock.patch.object(acquire_module, "_validate_network_target"), mock.patch.object(
            urllib.request, "build_opener", return_value=_FakeOpener(response)
        ):
            with self.assertRaisesRegex(common.AdapterError, "Content-Length"):
                acquire_module._download("prices", url, self.policy, max_compressed_bytes=16, retries=0)

    def test_projection_strips_precise_or_contributor_fields(self) -> None:
        projected = acquire_module._project(
            "locations",
            {
                "id": 1,
                "osm_name": "REWE Koblenz",
                "osm_display_name": "REWE, Example Street 1, 56068 Koblenz",
                "osm_address_city": "Koblenz",
                "osm_address_postcode": "56068",
                "osm_address_country_code": "DE",
                "osm_lat": 50.35,
                "osm_lon": 7.59,
                "owner": "contributor@example.invalid",
            },
        )
        self.assertEqual(projected["osm_name"], "REWE Koblenz")
        self.assertEqual(projected["osm_address_city"], "Koblenz")
        self.assertNotIn("osm_display_name", projected)
        self.assertNotIn("osm_lat", projected)
        self.assertNotIn("osm_lon", projected)
        self.assertNotIn("owner", projected)


class OpenPricesNormalizationHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = common.load_source_policy(ROOT / common.DEFAULT_SOURCE_POLICY)
        self.aliases = common.load_alias_registry(ROOT / common.DEFAULT_ALIAS_REGISTRY)

    def _fixture(self, temp: Path, snapshot_id: str = "op-hardening") -> tuple[Path, Path]:
        snapshot = temp / "source.jsonl"
        metadata = temp / "metadata.json"
        acquire(
            output=snapshot,
            metadata_output=metadata,
            snapshot_id=snapshot_id,
            mode="fixture",
            policy=self.policy,
            fixtures={key: ROOT / path for key, path in common.DEFAULT_FIXTURES.items()},
            retrieved_at="2026-08-30T00:00:00Z",
        )
        return snapshot, metadata

    @staticmethod
    def _rewrite_metadata_digest(snapshot: Path, metadata: Path) -> None:
        value = json.loads(metadata.read_text(encoding="utf-8"))
        payload = snapshot.read_bytes()
        value["payloadSha256"] = hashlib.sha256(payload).hexdigest()
        value["payloadBytes"] = len(payload)
        metadata.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    def test_conflicting_location_identity_fails_closed_for_linked_observation(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            snapshot, metadata = self._fixture(temp)
            lines = [json.loads(line) for line in snapshot.read_text(encoding="utf-8").splitlines()]
            original = next(line for line in lines if line["kind"] == "location" and line["record"]["id"] == 1)
            conflict = json.loads(json.dumps(original))
            conflict["record"]["osm_brand"] = "Lidl"
            conflict["record"]["osm_tag_value"] = "Lidl"
            conflict["record"]["osm_name"] = "Lidl conflicting identity"
            lines.append(conflict)
            snapshot.write_text("\n".join(json.dumps(line, sort_keys=True) for line in lines) + "\n", encoding="utf-8")
            self._rewrite_metadata_digest(snapshot, metadata)

            evidence, quality, _ = normalize_snapshot(
                snapshot=snapshot,
                metadata_path=metadata,
                policy=self.policy,
                aliases=self.aliases,
            )
            self.assertEqual({item["retailerKey"] for item in evidence["retailerEvidence"]}, {"lidl"})
            self.assertEqual(quality["counts"]["conflictingLocationIDs"], 1)
            self.assertEqual(quality["counts"]["conflictingLocationJoin"], 1)

    def test_conflicting_price_identity_removes_previously_staged_observation(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            snapshot, metadata = self._fixture(temp)
            lines = [json.loads(line) for line in snapshot.read_text(encoding="utf-8").splitlines()]
            original = next(line for line in lines if line["kind"] == "price" and line["record"]["id"] == 1001)
            conflict = json.loads(json.dumps(original))
            conflict["record"]["price"] = 9.99
            lines.append(conflict)
            snapshot.write_text("\n".join(json.dumps(line, sort_keys=True) for line in lines) + "\n", encoding="utf-8")
            self._rewrite_metadata_digest(snapshot, metadata)

            evidence, quality, _ = normalize_snapshot(
                snapshot=snapshot,
                metadata_path=metadata,
                policy=self.policy,
                aliases=self.aliases,
            )
            self.assertEqual({item["retailerKey"] for item in evidence["retailerEvidence"]}, {"lidl"})
            self.assertEqual(quality["counts"]["conflictingPriceIDs"], 1)
            self.assertEqual(quality["counts"]["included"], 1)

    def test_metadata_mode_and_completeness_must_agree(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            snapshot, metadata = self._fixture(temp)
            value = json.loads(metadata.read_text(encoding="utf-8"))
            value["mode"] = "sample"
            metadata.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(common.AdapterError, "completeness contradicts"):
                normalize_snapshot(
                    snapshot=snapshot,
                    metadata_path=metadata,
                    policy=self.policy,
                    aliases=self.aliases,
                )

    def test_quality_reports_observation_freshness_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            snapshot, metadata = self._fixture(temp)
            _, quality, _ = normalize_snapshot(
                snapshot=snapshot,
                metadata_path=metadata,
                policy=self.policy,
                aliases=self.aliases,
            )
            self.assertEqual(quality["oldestObservationAt"], "2026-08-20T00:00:00Z")
            self.assertEqual(quality["newestObservationAt"], "2026-08-21T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
