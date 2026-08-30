"""Shared Open Food Facts source policy and product projection helpers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

SOURCE_KEY = "open-food-facts"
SOURCE_OPERATOR = "Open Food Facts"
EXPECTED_PRODUCT_SCHEMA_VERSION = "1004"
EXPECTED_API_VERSION = "3.6"
SELECTION_MARKET = "DE"
DEFAULT_EXPORT_URL = "https://static.openfoodfacts.org/data/openfoodfacts-products.jsonl.gz"
DEFAULT_SOURCE_POLICY = Path("Data/sources/open-food-facts/source-policy-v1.json")
DEFAULT_SELECTION_POLICY = Path("Data/selection/catalog-selection-policy-v1.json")
DEFAULT_FIXTURE = Path("Data/sources/open-food-facts/fixture-products.jsonl")
SAFE_KEY = re.compile(r"[^a-z0-9]+")
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")

# Narrow reviewed leaves only. Broad ancestors such as en:fruits are not basic
# exclusions because they can contain processed descendants (HF-DATA-012).
CATEGORY_SIGNAL_TAGS: dict[str, set[str]] = {
    "fresh-fruit": {"en:fresh-fruits"},
    "fresh-vegetable": {"en:fresh-vegetables"},
    "basic-herb": {"en:fresh-herbs", "en:culinary-herbs"},
    "plain-cow-milk": {"en:plain-cow-milks", "en:plain-cow-milk"},
    "plain-water": {"en:plain-waters", "en:plain-water"},
    "bakery": {"en:biscuits", "en:breads", "en:cakes", "en:pastries"},
    "prepared-food": {"en:prepared-meals", "en:ready-meals", "en:prepared-foods"},
    "processed-dairy": {"en:yogurts", "en:cheeses", "en:dairy-desserts"},
    "meat-or-substitute": {"en:meats", "en:meat-alternatives", "en:plant-based-meat-alternatives"},
    "confectionery": {"en:confectioneries", "en:candies", "en:chocolates"},
    "snack": {"en:snacks", "en:chips-and-fries", "en:biscuits"},
    "dessert": {"en:desserts", "en:dairy-desserts", "en:frozen-desserts"},
    "sauce-condiment-seasoning": {"en:sauces", "en:condiments", "en:seasonings"},
    "processed-spread": {"en:spreads", "en:sweet-spreads", "en:savoury-spreads"},
    "formulated-beverage": {"en:soft-drinks", "en:energy-drinks", "en:flavoured-drinks"},
}

FORMULATION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "flavoured": ("flavour", "flavor", "aroma", "arôme"),
    "enzyme": ("enzyme", "enzym"),
    "culture": ("culture", "kultur"),
    "rennet": ("rennet", "lab"),
    "gelatine": ("gelatin", "gelatine", "gelatina"),
    "alcohol-related": ("ethanol", "alcohol", "alkohol", "rum", "wine", "wein"),
    "fortified": ("vitamin", "mineral", "fortified", "angereichert"),
}

IMAGE_FIELDS = {
    "front": "image_front_url",
    "ingredients": "image_ingredients_url",
    "nutrition": "image_nutrition_url",
}

# The acquisition snapshot intentionally keeps only source fields needed by the
# accepted staging/evidence/selection contracts. Localized names and exact
# ingredient strings are retained by prefix, while heavy nutrition and image
# object payloads are left upstream.
PROJECTED_FIXED_FIELDS = {
    "_id",
    "id",
    "code",
    "url",
    "schema_version",
    "product_type",
    "lang",
    "product_name",
    "generic_name",
    "brands",
    "quantity",
    "countries_tags",
    "categories_tags",
    "labels_tags",
    "tags_sources",
    "ingredients_n",
    "ingredients",
    "allergens",
    "allergens_from_ingredients",
    "allergens_tags",
    "traces",
    "traces_tags",
    "additives_n",
    "additives_tags",
    "stores_tags",
    "purchase_places_tags",
    "packaging_tags",
    "image_front_url",
    "image_ingredients_url",
    "image_nutrition_url",
    "codes_tags",
    "barcode_provenance_tags",
    "rev",
    "last_modified_t",
    "_hfeu_source_assigned_no_barcode",
}
PROJECTED_PREFIXES = ("product_name_", "generic_name_", "ingredients_text_", "allergens_", "traces_")


class AdapterError(ValueError):
    """Raised when source data or adapter configuration is unsafe or unsupported."""


@dataclass(frozen=True)
class SourcePolicy:
    raw: dict[str, Any]
    export_url: str
    acquisition_hosts: tuple[str, ...]
    reference_hosts: tuple[str, ...]
    product_schema_version: str
    api_version: str


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"failed to read JSON {path}: {exc}") from exc


def _nonblank(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdapterError(f"{path} must be a non-blank string")
    return value


def load_source_policy(path: Path = DEFAULT_SOURCE_POLICY) -> SourcePolicy:
    raw = load_json(path)
    if not isinstance(raw, dict):
        raise AdapterError("source policy must be an object")
    required = {
        "schemaVersion", "sourceKey", "operator", "sourceClass", "accessMethod",
        "exportURL", "allowedAcquisitionHosts", "allowedReferenceHosts",
        "productSchemaVersion", "apiVersion", "tagSchema", "databaseLicense",
        "databaseContentsLicense", "imageLicense", "attribution",
        "completenessClaimAllowed", "communityRetailerEvidence", "documentation",
    }
    if set(raw) != required:
        raise AdapterError(
            f"source policy keys mismatch; missing={sorted(required-set(raw))}, "
            f"unknown={sorted(set(raw)-required)}"
        )
    if raw["schemaVersion"] != 1 or raw["sourceKey"] != SOURCE_KEY or raw["operator"] != SOURCE_OPERATOR:
        raise AdapterError("unsupported Open Food Facts source policy identity/version")
    if raw["sourceClass"] != "open-database" or raw["accessMethod"] != "public-bulk":
        raise AdapterError("Open Food Facts must be an open-database public-bulk source")
    if raw["tagSchema"] != "tags_sources" or raw["completenessClaimAllowed"] is not False:
        raise AdapterError("source policy must require tags_sources and prohibit completeness claims")

    def hosts(field: str) -> tuple[str, ...]:
        values = raw[field]
        if not isinstance(values, list) or not values:
            raise AdapterError(f"{field} must be a non-empty array")
        result: list[str] = []
        for index, value in enumerate(values):
            host = _nonblank(value, f"{field}[{index}]").lower()
            if "/" in host or ":" in host or host.startswith(".") or host.endswith("."):
                raise AdapterError(f"{field}[{index}] must be a hostname only")
            result.append(host)
        if len(result) != len(set(result)):
            raise AdapterError(f"{field} contains duplicate hosts")
        return tuple(result)

    acquisition_hosts = hosts("allowedAcquisitionHosts")
    reference_hosts = hosts("allowedReferenceHosts")
    export_url = _nonblank(raw["exportURL"], "exportURL")
    parsed = urlparse(export_url)
    if parsed.scheme != "https" or parsed.hostname not in acquisition_hosts:
        raise AdapterError("exportURL must be HTTPS on an admitted acquisition host")
    product_schema = _nonblank(raw["productSchemaVersion"], "productSchemaVersion")
    api_version = _nonblank(raw["apiVersion"], "apiVersion")
    if product_schema != EXPECTED_PRODUCT_SCHEMA_VERSION or api_version != EXPECTED_API_VERSION:
        raise AdapterError(
            f"stale OFF schema/API contract; expected {EXPECTED_PRODUCT_SCHEMA_VERSION}/{EXPECTED_API_VERSION}"
        )
    db = raw["databaseLicense"]
    contents = raw["databaseContentsLicense"]
    images = raw["imageLicense"]
    retailer = raw["communityRetailerEvidence"]
    if not isinstance(db, dict) or db.get("identifier") != "ODbL" or not db.get("attributionRequired") or not db.get("shareAlikeRequired"):
        raise AdapterError("ODbL attribution/share-alike obligations must be explicit")
    if not isinstance(contents, dict) or contents.get("identifier") != "Database Contents License":
        raise AdapterError("database contents license must be represented separately")
    if not isinstance(images, dict) or images.get("identifier") != "CC BY-SA" or images.get("redistributionMode") != "references-only" or images.get("downloadBinaries") is not False:
        raise AdapterError("image policy must be separate CC BY-SA references-only metadata")
    if retailer != {"kind": "community-store-report", "confidence": "low", "officialAvailability": False}:
        raise AdapterError("community retailer policy must remain low-confidence and non-official")
    return SourcePolicy(raw, export_url, acquisition_hosts, reference_hosts, product_schema, api_version)


def strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def _flatten_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str) and value.strip():
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _flatten_strings(item)
    elif isinstance(value, dict):
        for key in sorted(value):
            yield from _flatten_strings(value[key])


def canonical_tags(record: dict[str, Any], field: str) -> list[str]:
    result = set(strings(record.get(f"{field}_tags")))
    tags_sources = record.get("tags_sources")
    if isinstance(tags_sources, dict):
        node = tags_sources.get(field)
        if node is not None:
            result.update(_flatten_strings(node))
        else:
            for source_value in tags_sources.values():
                if isinstance(source_value, dict) and field in source_value:
                    result.update(_flatten_strings(source_value[field]))
    return sorted(tag for tag in result if tag.strip())


def market_for_record(record: dict[str, Any]) -> str:
    countries = set(canonical_tags(record, "countries"))
    if countries & {"en:germany", "de:deutschland"}:
        return "DE"
    if countries & {"en:france", "fr:france"}:
        return "FR"
    if countries & {"en:austria", "de:osterreich", "de:österreich"}:
        return "AT"
    if countries & {"en:switzerland", "de:schweiz"}:
        return "CH"
    return "ZZ"


def source_assigned_no_barcode(record: dict[str, Any]) -> bool:
    """Use explicit provenance only; never reject legitimate 200-prefix retail codes."""
    if record.get("_hfeu_source_assigned_no_barcode") is True:
        return True
    markers = set(strings(record.get("codes_tags"))) | set(strings(record.get("barcode_provenance_tags")))
    return bool(markers & {
        "source-assigned-no-barcode",
        "open-food-facts:source-assigned-no-barcode",
        "en:source-assigned-no-barcode",
    })


def reserved_prefix_ambiguity(record: dict[str, Any]) -> bool:
    """Flag unresolved OFF 200-prefix provenance without treating the prefix as proof."""
    code = record.get("code")
    return (
        isinstance(code, str)
        and code.startswith("200")
        and not source_assigned_no_barcode(record)
    )


def ingredient_language_conflicts(record: dict[str, Any]) -> list[dict[str, str]]:
    """Report exact-text disagreements for the same declared ingredient language."""
    lang = record.get("lang")
    base = record.get("ingredients_text")
    if not (isinstance(lang, str) and LANGUAGE_RE.fullmatch(lang) and isinstance(base, str) and base.strip()):
        return []
    localized = record.get(f"ingredients_text_{lang}")
    if not isinstance(localized, str) or not localized.strip() or localized == base:
        return []
    return [{"languageCode": lang, "baseText": base, "localizedText": localized}]


def ingredient_texts(record: dict[str, Any]) -> list[tuple[str, str]]:
    values: dict[str, str] = {}
    for key, raw in record.items():
        if not key.startswith("ingredients_text_") or key == "ingredients_text_with_allergens":
            continue
        lang = key.removeprefix("ingredients_text_")
        if LANGUAGE_RE.fullmatch(lang) and isinstance(raw, str) and raw.strip():
            values[lang] = raw
    base = record.get("ingredients_text")
    if isinstance(base, str) and base.strip():
        lang = record.get("lang")
        language = lang if isinstance(lang, str) and LANGUAGE_RE.fullmatch(lang) else "und"
        values.setdefault(language, base)
    return sorted(values.items(), key=lambda item: (item[0] != "de", item[0] != "en", item[0]))


def ingredient_count(record: dict[str, Any]) -> int | None:
    value = record.get("ingredients_n")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    ingredients = record.get("ingredients")
    if isinstance(ingredients, list):
        return len([item for item in ingredients if isinstance(item, dict)])
    return None


def category_signals(record: dict[str, Any]) -> list[str]:
    tags = set(canonical_tags(record, "categories"))
    return sorted(signal for signal, exact in CATEGORY_SIGNAL_TAGS.items() if tags & exact)


def formulation_signals(record: dict[str, Any], text: str | None, count: int | None) -> list[str]:
    result: set[str] = set()
    if count is not None and count > 1:
        result.add("multi-ingredient")
    additives_n = record.get("additives_n")
    if strings(record.get("additives_tags")) or (isinstance(additives_n, int) and additives_n > 0):
        result.add("additive")
    if text:
        normalized = text.casefold()
        if "(" in normalized and ")" in normalized:
            result.add("compound")
        for signal, needles in FORMULATION_KEYWORDS.items():
            if any(needle in normalized for needle in needles):
                result.add(signal)
    return sorted(result)


def retailer_keys(record: dict[str, Any]) -> list[str]:
    raw = set(strings(record.get("stores_tags"))) | set(strings(record.get("purchase_places_tags")))
    result: set[str] = set()
    for value in raw:
        text = value.casefold().split(":", 1)[-1]
        text = SAFE_KEY.sub("-", text).strip("-")[:80]
        if text:
            result.add(text)
    return sorted(result)


def remote_images(record: dict[str, Any], policy: SourcePolicy) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    revision = str(record["rev"]) if isinstance(record.get("rev"), int) else None
    for purpose, field in IMAGE_FIELDS.items():
        url = record.get(field)
        if not isinstance(url, str) or not url.strip():
            continue
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in policy.reference_hosts:
            continue
        item: dict[str, Any] = {
            "purpose": purpose,
            "url": url,
            "sourceKey": SOURCE_KEY,
            "imageID": hashlib.sha256(url.encode("utf-8")).hexdigest()[:24],
        }
        if revision is not None:
            item["revision"] = revision
        result.append(item)
    return sorted(result, key=lambda item: (item["purpose"], item["url"]))


def product_name(record: dict[str, Any]) -> str:
    for field in ("product_name_de", "product_name", "generic_name_de", "generic_name"):
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value
    code = record.get("code")
    return f"Open Food Facts product {code}" if isinstance(code, str) and code else "Open Food Facts product"


def timestamp_from_epoch(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def source_revision(record: dict[str, Any]) -> str | None:
    value = record.get("rev")
    return str(value) if isinstance(value, int) and value >= 0 else None


def allergen_text(record: dict[str, Any]) -> str | None:
    for field in ("allergens_de", "allergens", "allergens_from_ingredients"):
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value
    tags = strings(record.get("allergens_tags"))
    return ", ".join(tags) if tags else None


def traces_text(record: dict[str, Any]) -> str | None:
    for field in ("traces_de", "traces"):
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value
    tags = strings(record.get("traces_tags"))
    return ", ".join(tags) if tags else None


def record_to_candidate(record: dict[str, Any], policy: SourcePolicy) -> dict[str, Any]:
    code = record.get("code")
    barcode = code if isinstance(code, str) else ""
    texts = ingredient_texts(record)
    _language, ingredients_text = texts[0] if texts else (None, None)
    count = ingredient_count(record)
    candidate: dict[str, Any] = {
        "sourceRecordID": str(record.get("_id") or record.get("id") or barcode or "missing-code"),
        "barcode": barcode,
        "market": market_for_record(record),
        "productType": record.get("product_type") if isinstance(record.get("product_type"), str) else "food",
        "barcodeKind": "source-assigned-no-barcode" if source_assigned_no_barcode(record) else "retail-gtin",
        "name": product_name(record),
        "categoryTags": canonical_tags(record, "categories"),
        "categorySignals": category_signals(record),
        "formulationSignals": formulation_signals(record, ingredients_text, count),
        "evidenceSignals": ["review"] if any("halal" in x.casefold() for x in canonical_tags(record, "labels")) else [],
        "retailerKeys": retailer_keys(record),
        "remoteImages": remote_images(record, policy),
        "ingredientCount": count,
    }
    brand = record.get("brands")
    if isinstance(brand, str) and brand.strip():
        candidate["brand"] = brand
    if ingredients_text is not None:
        candidate["ingredientsText"] = ingredients_text
    packaging = canonical_tags(record, "packaging")
    if packaging:
        package_signals = {
            SAFE_KEY.sub("-", item.casefold()).strip("-")
            for item in packaging
        }
        package_signals.discard("")
        if package_signals:
            candidate["packageSignals"] = sorted(package_signals)
    return candidate


def project_source_record(record: dict[str, Any]) -> dict[str, Any]:
    """Bound the raw snapshot without losing fields required by the staging contract."""
    return {
        key: value
        for key, value in record.items()
        if key in PROJECTED_FIXED_FIELDS or key.startswith(PROJECTED_PREFIXES)
    }
