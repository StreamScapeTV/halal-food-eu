#!/usr/bin/env python3
"""EU additive identity reference validation, matching, diffing, and compact SQLite export.

This module is deliberately identity-only. Official EU authorisation, names,
functions, and specification context do not establish a product-specific origin
or a halal conclusion.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


class AdditiveReferenceError(ValueError):
    pass


_CANONICAL_ID = re.compile(r"^E[0-9]{3,4}[a-z]?(?:\([ivx]+\))?$")
_INPUT_ID = re.compile(r"^\s*[Ee]\s*([0-9]{3,4})\s*([A-Za-z])?\s*(?:\(\s*([IVXivx]+)\s*\))?\s*$")
_ALLOWED_STATUS = {"active", "changed", "removed"}
_ALLOWED_LEGAL_KINDS = {"union-list", "specification", "amendment", "scientific-context"}


def _timestamp(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AdditiveReferenceError(f"{field} must be a non-blank RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdditiveReferenceError(f"{field} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise AdditiveReferenceError(f"{field} must include a timezone")


def _date(value: Any, field: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise AdditiveReferenceError(f"{field} must be YYYY-MM-DD")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise AdditiveReferenceError(f"{field} must be a valid date") from exc


def _non_blank(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdditiveReferenceError(f"{field} must be non-blank")
    return value.strip()


def canonicalize_additive_id(value: str) -> str:
    if not isinstance(value, str):
        raise AdditiveReferenceError("additive identifier must be text")
    match = _INPUT_ID.fullmatch(value)
    if match is None:
        compact = value.replace(" ", "")
        if _CANONICAL_ID.fullmatch(compact) is not None:
            return compact
        raise AdditiveReferenceError(f"invalid additive identifier {value!r}")
    digits, suffix, roman = match.groups()
    result = f"E{digits}"
    if suffix:
        result += suffix.casefold()
    if roman:
        result += f"({roman.casefold()})"
    return result


def _normalize_character(character: str) -> str:
    decomposed = unicodedata.normalize("NFKD", character)
    pieces: list[str] = []
    for item in decomposed:
        if unicodedata.combining(item):
            continue
        folded = item.casefold()
        for folded_item in folded:
            pieces.append(folded_item if folded_item.isalnum() else " ")
    return "".join(pieces)


def normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    offsets: list[int] = []
    last_space = False
    for index, character in enumerate(text):
        piece = _normalize_character(character)
        if not piece:
            continue
        for normalized_character in piece:
            if normalized_character == " ":
                if last_space:
                    continue
                last_space = True
            else:
                last_space = False
            normalized.append(normalized_character)
            offsets.append(index)
    while normalized and normalized[0] == " ":
        normalized.pop(0)
        offsets.pop(0)
    while normalized and normalized[-1] == " ":
        normalized.pop()
        offsets.pop()
    return "".join(normalized), offsets


def _normalized_name(value: str) -> str:
    return normalize_with_offsets(value)[0]


def _validate_language_map(value: Any, field: str, languages: set[str], *, may_be_empty: bool) -> None:
    if not isinstance(value, dict) or (not may_be_empty and not value):
        raise AdditiveReferenceError(f"{field} must be a {'possibly empty' if may_be_empty else 'non-empty'} object")
    for language, names in value.items():
        if language not in languages:
            raise AdditiveReferenceError(f"{field} uses unsupported language {language!r}")
        if not isinstance(names, list) or not names:
            raise AdditiveReferenceError(f"{field}.{language} must contain names")
        cleaned = [_non_blank(name, f"{field}.{language}") for name in names]
        folded = [_normalized_name(name) for name in cleaned]
        if any(not item for item in folded) or len(folded) != len(set(folded)):
            raise AdditiveReferenceError(f"{field}.{language} contains blank-equivalent or duplicate names")


def validate_additive_identities(raw: dict[str, Any]) -> None:
    expected = {"schemaVersion", "datasetVersion", "referenceRevision", "reviewedAt", "nextReviewAt", "identityOnly", "languages", "source", "entries"}
    if not isinstance(raw, dict) or set(raw) != expected or raw.get("schemaVersion") != 1:
        raise AdditiveReferenceError("additive identity data has unsupported schema or fields")
    _non_blank(raw.get("datasetVersion"), "datasetVersion")
    _non_blank(raw.get("referenceRevision"), "referenceRevision")
    _timestamp(raw.get("reviewedAt"), "reviewedAt")
    _timestamp(raw.get("nextReviewAt"), "nextReviewAt")
    if raw.get("identityOnly") is not True:
        raise AdditiveReferenceError("additive identity data must remain identity-only")
    languages_raw = raw.get("languages")
    if not isinstance(languages_raw, list) or not languages_raw:
        raise AdditiveReferenceError("languages must be a non-empty array")
    languages = {_non_blank(item, "languages[]") for item in languages_raw}
    if len(languages) != len(languages_raw):
        raise AdditiveReferenceError("languages must be unique")

    source = raw.get("source")
    source_fields = {"sourceKey", "jurisdiction", "acquisitionMethod", "unionListCELEX", "unionListELI", "specificationsCELEX", "specificationsELI", "commissionReference", "efsaReference", "licenseIdentifier", "attribution", "legalEffectLimitation"}
    if not isinstance(source, dict) or set(source) != source_fields:
        raise AdditiveReferenceError("source fields mismatch")
    for field in source_fields:
        _non_blank(source.get(field), f"source.{field}")
    if source["sourceKey"] != "eu-additives" or source["jurisdiction"] != "EU":
        raise AdditiveReferenceError("source must remain the reviewed EU additive reference")
    for field in ("unionListELI", "specificationsELI", "commissionReference", "efsaReference"):
        if not source[field].startswith("https://"):
            raise AdditiveReferenceError(f"source.{field} must be HTTPS")

    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise AdditiveReferenceError("entries must be a non-empty array")
    seen_ids: set[str] = set()
    aliases_by_language: dict[str, dict[str, str]] = {language: {} for language in languages}
    required_entry = {"id", "status", "officialNames", "aliases", "technologicalFunctions", "originPossibilities", "legalReferences", "reviewedAt"}
    for index, entry in enumerate(entries):
        prefix = f"entries[{index}]"
        if not isinstance(entry, dict) or set(entry) != required_entry:
            raise AdditiveReferenceError(f"{prefix} fields mismatch")
        additive_id = entry.get("id")
        if not isinstance(additive_id, str) or _CANONICAL_ID.fullmatch(additive_id) is None:
            raise AdditiveReferenceError(f"{prefix}.id is invalid")
        if canonicalize_additive_id(additive_id) != additive_id:
            raise AdditiveReferenceError(f"{prefix}.id is not canonical")
        if additive_id in seen_ids:
            raise AdditiveReferenceError(f"duplicate additive identity {additive_id}")
        seen_ids.add(additive_id)
        if entry.get("status") not in _ALLOWED_STATUS:
            raise AdditiveReferenceError(f"{prefix}.status is unsupported")
        _validate_language_map(entry.get("officialNames"), f"{prefix}.officialNames", languages, may_be_empty=False)
        _validate_language_map(entry.get("aliases"), f"{prefix}.aliases", languages, may_be_empty=True)
        functions = entry.get("technologicalFunctions")
        if not isinstance(functions, list) or len(functions) != len(set(functions)):
            raise AdditiveReferenceError(f"{prefix}.technologicalFunctions must be a unique array")
        for function in functions:
            _non_blank(function, f"{prefix}.technologicalFunctions[]")
        origins = entry.get("originPossibilities")
        if not isinstance(origins, list):
            raise AdditiveReferenceError(f"{prefix}.originPossibilities must be an array")
        origin_keys: set[tuple[str, str]] = set()
        for origin_index, origin in enumerate(origins):
            field = f"{prefix}.originPossibilities[{origin_index}]"
            if not isinstance(origin, dict) or set(origin) != {"kind", "statement", "reference"}:
                raise AdditiveReferenceError(f"{field} fields mismatch")
            kind = _non_blank(origin.get("kind"), f"{field}.kind")
            statement = _non_blank(origin.get("statement"), f"{field}.statement")
            reference = _non_blank(origin.get("reference"), f"{field}.reference")
            if not reference.startswith("https://"):
                raise AdditiveReferenceError(f"{field}.reference must be HTTPS")
            if (kind, statement.casefold()) in origin_keys:
                raise AdditiveReferenceError(f"{field} is duplicated")
            origin_keys.add((kind, statement.casefold()))
        references = entry.get("legalReferences")
        if not isinstance(references, list) or not references:
            raise AdditiveReferenceError(f"{prefix}.legalReferences must be non-empty")
        reference_keys: set[tuple[str, str, str]] = set()
        for reference_index, reference in enumerate(references):
            field = f"{prefix}.legalReferences[{reference_index}]"
            if not isinstance(reference, dict) or set(reference) != {"kind", "reference", "revision"}:
                raise AdditiveReferenceError(f"{field} fields mismatch")
            kind = reference.get("kind")
            if kind not in _ALLOWED_LEGAL_KINDS:
                raise AdditiveReferenceError(f"{field}.kind is unsupported")
            url = _non_blank(reference.get("reference"), f"{field}.reference")
            if not url.startswith("https://"):
                raise AdditiveReferenceError(f"{field}.reference must be HTTPS")
            revision = _non_blank(reference.get("revision"), f"{field}.revision")
            _date(revision, f"{field}.revision")
            key = (kind, url, revision)
            if key in reference_keys:
                raise AdditiveReferenceError(f"{field} is duplicated")
            reference_keys.add(key)
        _timestamp(entry.get("reviewedAt"), f"{prefix}.reviewedAt")
        for language in languages:
            for group in (entry["officialNames"].get(language, []), entry["aliases"].get(language, [])):
                for name in group:
                    normalized = _normalized_name(name)
                    owner = aliases_by_language[language].get(normalized)
                    if owner is not None and owner != additive_id:
                        raise AdditiveReferenceError(f"alias collision in {language}: {name!r} maps to both {owner} and {additive_id}")
                    aliases_by_language[language][normalized] = additive_id


def additive_lookup(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    validate_additive_identities(raw)
    return {entry["id"]: entry for entry in raw["entries"]}


def _id_pattern(additive_id: str) -> re.Pattern[str]:
    canonical = canonicalize_additive_id(additive_id)
    match = re.fullmatch(r"E([0-9]{3,4})([a-z])?(?:\(([ivx]+)\))?", canonical)
    if match is None:
        raise AdditiveReferenceError(f"cannot build matcher for {additive_id}")
    digits, suffix, roman = match.groups()
    body = r"[Ee]\s*" + r"\s*".join(re.escape(digit) for digit in digits)
    if suffix:
        body += rf"\s*{re.escape(suffix)}"
    if roman:
        body += r"\s*\(\s*" + r"\s*".join(re.escape(character) for character in roman) + r"\s*\)"
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)


def _name_pattern(normalized_name: str) -> re.Pattern[str]:
    parts = [re.escape(part) for part in normalized_name.split()]
    body = r"\s+".join(parts)
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")


def _source_span(text: str, offsets: list[int], start: int, end: int) -> dict[str, Any]:
    if not offsets or start < 0 or end <= start or start >= len(offsets):
        raise AdditiveReferenceError("cannot map additive match to source text")
    source_start = offsets[start]
    source_end = offsets[min(end - 1, len(offsets) - 1)] + 1
    return {"start": source_start, "end": source_end, "text": text[source_start:source_end]}


def _display_name(entry: dict[str, Any], language: str) -> str:
    base = language.split("-")[0].casefold()
    names = entry["officialNames"].get(base) or entry["officialNames"].get("en")
    if not names:
        names = next(iter(entry["officialNames"].values()))
    return names[0]


def match_additive_identities(raw: dict[str, Any], text: str, language: str) -> list[dict[str, Any]]:
    validate_additive_identities(raw)
    if not isinstance(text, str):
        raise AdditiveReferenceError("ingredient text must be text")
    base_language = language.split("-")[0].casefold()
    normalized_text, offsets = normalize_with_offsets(text)
    matches: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str]] = set()
    for entry in sorted(raw["entries"], key=lambda item: item["id"]):
        if entry["status"] == "removed":
            continue
        for match in _id_pattern(entry["id"]).finditer(text):
            key = (entry["id"], match.start(), match.end(), "e-number")
            if key in seen:
                continue
            seen.add(key)
            matches.append(_candidate(raw, entry, _direct_span(text, match.start(), match.end()), "e-number", entry["id"], base_language))
        if base_language not in set(raw["languages"]):
            continue
        names: list[str] = []
        names.extend(entry["officialNames"].get(base_language, []))
        names.extend(entry["aliases"].get(base_language, []))
        for name in names:
            normalized_name = _normalized_name(name)
            if not normalized_name:
                continue
            for match in _name_pattern(normalized_name).finditer(normalized_text):
                span = _source_span(text, offsets, match.start(), match.end())
                key = (entry["id"], span["start"], span["end"], "name")
                if key in seen:
                    continue
                seen.add(key)
                matches.append(_candidate(raw, entry, span, "name", name, base_language))
    matches.sort(key=lambda item: (item["sourceSpan"]["start"], item["sourceSpan"]["end"], item["id"], item["matchKind"]))
    return matches


def _direct_span(text: str, start: int, end: int) -> dict[str, Any]:
    return {"start": start, "end": end, "text": text[start:end]}


def _candidate(raw: dict[str, Any], entry: dict[str, Any], span: dict[str, Any], match_kind: str, matched_value: str, language: str) -> dict[str, Any]:
    return {
        "id": entry["id"],
        "displayName": _display_name(entry, language),
        "matchKind": match_kind,
        "matchedValue": matched_value,
        "sourceSpan": span,
        "technologicalFunctions": list(entry["technologicalFunctions"]),
        "originPossibilities": copy.deepcopy(entry["originPossibilities"]),
        "legalReferences": copy.deepcopy(entry["legalReferences"]),
        "datasetVersion": raw["datasetVersion"],
        "referenceRevision": raw["referenceRevision"],
    }


def _entry_comparison(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in entry.items() if key != "reviewedAt"}


def _methodology_rules_for_ids(methodology: dict[str, Any] | None, additive_ids: Iterable[str]) -> list[str]:
    if not isinstance(methodology, dict):
        return []
    wanted = set(additive_ids)
    affected: set[str] = set()
    for rule in methodology.get("rules", []):
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str):
            continue
        for alias in rule.get("aliases", []):
            if not isinstance(alias, dict) or alias.get("match") != "e-number" or not isinstance(alias.get("text"), str):
                continue
            try:
                alias_id = canonicalize_additive_id(alias["text"])
            except AdditiveReferenceError:
                continue
            if alias_id in wanted:
                affected.add(rule["id"])
    return sorted(affected)


def diff_additive_references(previous: dict[str, Any], current: dict[str, Any], methodology: dict[str, Any] | None = None) -> dict[str, Any]:
    before = additive_lookup(previous)
    after = additive_lookup(current)
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed: list[dict[str, Any]] = []
    for additive_id in sorted(set(before).intersection(after)):
        old = _entry_comparison(before[additive_id])
        new = _entry_comparison(after[additive_id])
        if old == new:
            continue
        fields = sorted(key for key in old if old.get(key) != new.get(key))
        changed.append({"id": additive_id, "fields": fields})
    affected_ids = sorted(set(added + removed + [item["id"] for item in changed]))
    affected_rules = _methodology_rules_for_ids(methodology, affected_ids)
    return {
        "schemaVersion": 1,
        "fromDatasetVersion": previous["datasetVersion"],
        "toDatasetVersion": current["datasetVersion"],
        "fromReferenceRevision": previous["referenceRevision"],
        "toReferenceRevision": current["referenceRevision"],
        "sourceRevisionChanged": previous["referenceRevision"] != current["referenceRevision"],
        "added": added,
        "removed": removed,
        "changed": changed,
        "affectedAdditiveIDs": affected_ids,
        "affectedMethodologyRuleIDs": affected_rules,
        "reviewRequired": bool(affected_ids),
    }


_SQL = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE additives (additive_id TEXT PRIMARY KEY,status TEXT NOT NULL,reviewed_at TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE additive_names (additive_id TEXT NOT NULL,language TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('official','alias')),name TEXT NOT NULL,normalized_name TEXT NOT NULL,PRIMARY KEY(additive_id,language,kind,normalized_name),FOREIGN KEY(additive_id) REFERENCES additives(additive_id) ON DELETE CASCADE) WITHOUT ROWID;
CREATE TABLE additive_functions (additive_id TEXT NOT NULL,function TEXT NOT NULL,PRIMARY KEY(additive_id,function),FOREIGN KEY(additive_id) REFERENCES additives(additive_id) ON DELETE CASCADE) WITHOUT ROWID;
CREATE TABLE additive_origins (additive_id TEXT NOT NULL,kind TEXT NOT NULL,statement TEXT NOT NULL,reference TEXT NOT NULL,PRIMARY KEY(additive_id,kind,statement),FOREIGN KEY(additive_id) REFERENCES additives(additive_id) ON DELETE CASCADE) WITHOUT ROWID;
CREATE TABLE additive_legal_references (additive_id TEXT NOT NULL,kind TEXT NOT NULL,reference TEXT NOT NULL,revision TEXT NOT NULL,PRIMARY KEY(additive_id,kind,reference,revision),FOREIGN KEY(additive_id) REFERENCES additives(additive_id) ON DELETE CASCADE) WITHOUT ROWID;
CREATE INDEX idx_additive_names_lookup ON additive_names(language,normalized_name,additive_id);
CREATE INDEX idx_additive_legal_revision ON additive_legal_references(revision,additive_id);
"""


def build_reference_sqlite(raw: dict[str, Any], output: Path) -> None:
    validate_additive_identities(raw)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    connection = sqlite3.connect(output)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA page_size=4096")
        connection.executescript(_SQL)
        metadata = {
            "schemaVersion": str(raw["schemaVersion"]),
            "datasetVersion": raw["datasetVersion"],
            "referenceRevision": raw["referenceRevision"],
            "reviewedAt": raw["reviewedAt"],
            "nextReviewAt": raw["nextReviewAt"],
            "sourceKey": raw["source"]["sourceKey"],
            "licenseIdentifier": raw["source"]["licenseIdentifier"],
            "attribution": raw["source"]["attribution"],
            "legalEffectLimitation": raw["source"]["legalEffectLimitation"],
        }
        connection.executemany("INSERT INTO metadata(key,value) VALUES (?,?)", sorted(metadata.items()))
        for entry in sorted(raw["entries"], key=lambda item: item["id"]):
            connection.execute("INSERT INTO additives(additive_id,status,reviewed_at) VALUES (?,?,?)", (entry["id"], entry["status"], entry["reviewedAt"]))
            name_rows: list[tuple[str, str, str, str, str]] = []
            for language, names in sorted(entry["officialNames"].items()):
                for name in sorted(names, key=str.casefold):
                    name_rows.append((entry["id"], language, "official", name, _normalized_name(name)))
            for language, names in sorted(entry["aliases"].items()):
                for name in sorted(names, key=str.casefold):
                    name_rows.append((entry["id"], language, "alias", name, _normalized_name(name)))
            connection.executemany("INSERT INTO additive_names(additive_id,language,kind,name,normalized_name) VALUES (?,?,?,?,?)", name_rows)
            connection.executemany("INSERT INTO additive_functions(additive_id,function) VALUES (?,?)", [(entry["id"], function) for function in sorted(entry["technologicalFunctions"])])
            connection.executemany("INSERT INTO additive_origins(additive_id,kind,statement,reference) VALUES (?,?,?,?)", [(entry["id"], item["kind"], item["statement"], item["reference"]) for item in sorted(entry["originPossibilities"], key=lambda item: (item["kind"], item["statement"], item["reference"]))])
            connection.executemany("INSERT INTO additive_legal_references(additive_id,kind,reference,revision) VALUES (?,?,?,?)", [(entry["id"], item["kind"], item["reference"], item["revision"]) for item in sorted(entry["legalReferences"], key=lambda item: (item["kind"], item["reference"], item["revision"]))])
        connection.commit()
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise AdditiveReferenceError("compact additive SQLite has foreign-key violations")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise AdditiveReferenceError("compact additive SQLite integrity check failed")
        connection.execute("VACUUM")
    finally:
        connection.close()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdditiveReferenceError(f"failed to load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AdditiveReferenceError(f"{path} must contain a JSON object")
    return value


def _write_json(path: Path | None, value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path is None:
        print(encoded, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--data", type=Path, default=Path("Data/methodology/additive-identities-v1.json"))
    match = sub.add_parser("match")
    match.add_argument("--data", type=Path, default=Path("Data/methodology/additive-identities-v1.json"))
    match.add_argument("--language", default="de")
    match.add_argument("--text", required=True)
    match.add_argument("--output", type=Path)
    diff = sub.add_parser("diff")
    diff.add_argument("--previous", type=Path, required=True)
    diff.add_argument("--current", type=Path, required=True)
    diff.add_argument("--methodology", type=Path)
    diff.add_argument("--output", type=Path)
    sqlite = sub.add_parser("sqlite")
    sqlite.add_argument("--data", type=Path, default=Path("Data/methodology/additive-identities-v1.json"))
    sqlite.add_argument("--output", type=Path, required=True)
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "validate":
            raw = load_json(args.data)
            validate_additive_identities(raw)
            print(f"Validated EU additive identities {raw['datasetVersion']} ({len(raw['entries'])} entries)")
            return
        if args.command == "match":
            raw = load_json(args.data)
            _write_json(args.output, {"matches": match_additive_identities(raw, args.text, args.language)})
            return
        if args.command == "diff":
            methodology = load_json(args.methodology) if args.methodology else None
            report = diff_additive_references(load_json(args.previous), load_json(args.current), methodology)
            _write_json(args.output, report)
            return
        raw = load_json(args.data)
        build_reference_sqlite(raw, args.output)
        print(f"Built compact EU additive reference {args.output}")
    except AdditiveReferenceError as exc:
        raise SystemExit(f"EU additive reference failed: {exc}") from exc


if __name__ == "__main__":
    main()
