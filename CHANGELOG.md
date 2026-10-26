# Release notes

## 1.1.0 — 2026-10-26 (local release; not published)

Initial stable release implementing the gSheet service specification
v1.1. Version 0.1.0 was the development scaffold; no new runtime features or
breaking public API changes were introduced during release preparation.

- Generic synchronous SheetService over the Google Sheets REST v4 API, with
  service-account and explicitly authorized installed-app OAuth authentication.
- Location-aware values/grid reads, metadata, A1/zero-based range conversion,
  bulk updates, logical-table append, RAW defaults, and explicit clearing.
- Restricted structural operations, merge/unmerge/resize, targeted metadata,
  bounded chunking, optional TTL caching, and shared process-local request budgets.
- Formatting, validation, dimension sizing, freezing, named/developer metadata
  reads, filters, moves, same-spreadsheet copies, seven paste modes and bounded
  exact search/read conveniences.
- Spreadsheet/operation/request-type grants, optional preconditions, formula
  protection where supported, validation-only previews, sanitized errors and
  uncertain-write outcomes without blind replay.
- MIT license, explicit distribution inputs, installation guidance and release
  audit. Historical P0/P1/P2 evidence and the final readiness report are local
  artifacts under `reports/`, excluded from distributions.

Known limits: no transactions or atomic compare-and-swap; positions are not
stable identities; external edits can stale cached metadata or chunked reads.
Quota coordination is process-local. Blank representation depends on Google's
endpoint/range. Native schema and validation behavior remain Google's; strict
cell rules do not guarantee rejection of programmatic writes. Copies stay in one
spreadsheet, search is exact/case-sensitive, and business interpretation is
outside this library. See README for the full safety and compatibility contract.
