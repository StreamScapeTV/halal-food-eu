# 002 — Scanning and lookup

**Status:** Accepted  
**Last reviewed:** 2026-08-26

## Supported identifiers

Packaged food in Europe normally uses EAN/GTIN barcodes rather than QR codes. The scanner must therefore prioritize retail symbologies while also accepting a QR code that contains a valid GS1 Digital Link GTIN.

- **HF-SCAN-001:** Camera scanning must recognize EAN-8, EAN-13, UPC-E, UPC-A when surfaced as EAN-13, Code 128 where it contains a GTIN, and QR.
- **HF-SCAN-002:** A plain QR URL is not a product identifier unless a supported GS1 Digital Link Application Identifier `01` yields a valid GTIN-14.
- **HF-SCAN-003:** Manual input must always be available, including when the device lacks supported scanner hardware, camera permission is denied, the simulator is used, or scanning fails.
- **HF-SCAN-004:** Input normalization must remove surrounding whitespace and permitted visual separators, reject non-ASCII digits in a GTIN, validate the GS1 check digit, and canonicalize EAN-8/UPC-A/EAN-13/GTIN-14 to a 14-digit GTIN key.
- **HF-SCAN-005:** Invalid input must produce a specific, recoverable validation message and must not query SQLite.
- **HF-SCAN-006:** The scanner must stop or dismiss after accepting one payload and debounce repeated frames so a single code does not launch repeated lookups.
- **HF-SCAN-007:** Camera frames and recognized payloads must remain on device and must not be persisted merely because they were scanned.
- **HF-SCAN-008:** The scan UI must provide a visible close control, camera guidance, and VoiceOver guidance.

## Lookup behavior

- **HF-LOOKUP-001:** Lookup is exact by normalized GTIN. Fuzzy product-name matching must never substitute for a barcode match.
- **HF-LOOKUP-002:** Lookup must execute asynchronously outside the main actor through the `ProductCatalog` domain boundary.
- **HF-LOOKUP-003:** A new scan or manual submission cancels the previous lookup result task where possible.
- **HF-LOOKUP-004:** The result states are `idle`, `looking-up`, `found`, `not-found`, `invalid-input`, and `failed`.
- **HF-LOOKUP-005:** `not-found` means the valid GTIN does not exist in this catalog version; it must not imply the product is halal or not halal.
- **HF-LOOKUP-006:** `failed` must distinguish a catalog/runtime problem from a normal not-found outcome and offer a retry where retry can help.
- **HF-LOOKUP-007:** A found record must display product name, optional brand, barcode, ingredient observation, assessment, reasons, source, observed date, review date, and catalog/methodology version.
- **HF-LOOKUP-008:** Scanning and lookup must work in airplane mode after installation.

## Camera permission

The app asks for camera access only when the user opens the scanner. Denial does not block manual entry. Settings guidance may be shown after denial, but the app must not repeatedly prompt or shame the user.

## Acceptance examples

| Input | Expected |
| --- | --- |
| `0200000000004` | Valid EAN-13, normalized to `00200000000004` |
| ` 0200 0000 0000 4 ` | Same normalized GTIN after visual separator removal |
| `0200000000005` | Rejected: invalid check digit |
| `https://id.gs1.org/01/00200000000004` | GTIN extracted and validated |
| ordinary recipe QR URL | Rejected as unsupported product payload |
| valid but absent GTIN | `not-found`, never `unknown` product assessment |
