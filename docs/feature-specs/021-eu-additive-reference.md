# 021 — EU additive identity and legal reference

**Status:** Accepted  
**Scope:** Germany launch; EU legal reference; German and English package-label matching

## Purpose

The app may recognise an EU food additive and explain its official identity, names, functions, source revision, and cited origin possibilities. This reference is explanatory evidence only. EU authorisation, safety evaluation, or an E-number must never become a product-specific origin claim or a halal conclusion.

## Requirements

### HFEU-ADD-001 — Reviewed official source boundary

The initial reference SHALL use a reviewed public path that requires no API credential. Stable EUR-Lex CELEX/ELI identifiers SHALL pin the Union-list and specification revisions. Commission and EFSA pages MAY provide explanatory context. Hidden endpoints and undocumented scraping SHALL NOT be used.

Every admitted reference revision SHALL record reuse/attribution terms, review time, next review time, and the limitation that EUR-Lex legislation remains authoritative.

### HFEU-ADD-002 — Identity-only model

Each additive record SHALL keep its additive identifier, status, official German/English names, conservative aliases, technological functions where supported, legal references and revision dates, and optional cited origin possibilities.

The reference model SHALL NOT contain `halal`, `haram`, product-origin, certification, or final-assessment fields. A cited origin possibility describes what an official source permits or describes; it does not establish which origin a scanned product used.

### HFEU-ADD-003 — Conservative package-text matching

The matcher SHALL recognise canonical E-number forms and common package whitespace variants, including letter suffixes and Roman sub-identifiers such as `E 472 a` and `E 160 a (i)`. German/English official names and reviewed aliases MAY match inside functional-class syntax such as `Emulgator: Lecithine`.

Name matching SHALL use token/phrase boundaries and SHALL NOT use fuzzy edit-distance matching. The exact matched source span SHALL be retained for explanation and tests.

### HFEU-ADD-004 — Multiple origins remain unresolved

When an official reference describes multiple possible origins, every supported possibility SHALL be preserved. The reference adapter SHALL NOT select one origin for a product. Product-specific ingredient, manufacturer, process, or certification evidence is required by the halal methodology before an origin-dependent conclusion can be made.

### HFEU-ADD-005 — Deterministic revisions and impact

A new reviewed reference SHALL be diffed against the previous reviewed version. Additions, removals, names/aliases, functions, origin possibilities, status, and legal-reference changes SHALL be reported deterministically.

Removed identifiers SHALL remain reviewable rather than disappearing silently. The change report SHALL identify methodology rules whose explicit E-number aliases are affected so reassessment can be selective.

### HFEU-ADD-006 — Compact offline projection

The committed reference SHALL support a deterministic compact SQLite projection with indexed additive-name lookup. Its metadata SHALL include dataset version, official source revision, review dates, attribution, license identifier, and legal-effect limitation.

The iOS app MAY bundle the canonical JSON reference rather than duplicate the full product catalog database, but offline product results SHALL be able to show a matching additive identity and its official reference without network access.

### HFEU-ADD-007 — User-visible meaning

An additive explanation SHALL distinguish:

- what identity matched the exact ingredient text;
- official technological functions where available;
- cited origin possibilities or source limitations;
- the official reference/revision;
- the explicit warning that EU additive identity/authorisation is not halal certification and does not prove the origin used in the scanned product.

The additive explanation SHALL NOT replace or upgrade the authoritative halal assessment shown by the product-result screen.

## Initial reviewed seed

The first committed dataset is intentionally a bounded reviewed seed, not a claim to contain every EU additive. It includes examples that exercise the difficult semantics: E 120, E 160a(i), E 322, E 422, E 471, E 472a, E 901, E 904, and E 920. Expansion uses the same reviewed-source, diff, impact, and test contract.

## Validation

Release/PR gates SHALL cover:

- canonical uniqueness and alias collisions;
- German/English E-number and name fixtures;
- suffix/subtype and OCR-style spacing without fuzzy matching;
- false-positive boundaries;
- multiple-origin preservation;
- removed/changed additive impact;
- selective methodology-rule impact;
- deterministic indexed SQLite projection;
- offline iOS explanation semantics with no additive-derived halal verdict.
