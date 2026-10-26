# gSheet — Service Requirements Specification

**Version:** 1.1  
**Implementation language:** Python  
**Document type:** Functional and non-functional requirements  
**Scope:** Generic Google Sheets service only; business-specific wrappers are separate.

## 1. Purpose

gSheet is a reusable Python service that provides a consistent interface to Google Sheets. It must support spreadsheets with arbitrary tab names, column positions, merged headers, variable header depths, custom columns, and dynamically changing layouts.

gSheet must **not** assume that a sheet represents products, stock, prices, orders, or any other business entity. A separate wrapper will interpret the spreadsheet for a specific business.

**Architecture:** Business/AI wrapper → gSheet → Google Sheets API.

## 2. Core design requirements

1. Remain independent of business terminology and business rules.
2. Work with existing Google spreadsheets without requiring IDs, additional columns, or template conversion.
3. Support multiple spreadsheets and multiple tabs per spreadsheet.
4. Accept arbitrary valid sheet layouts, including merged and nested headers.
5. Preserve exact cell locations and empty-cell positions when needed.
6. Offer clear, stable method names and predictable results for the wrapper.
7. Support efficient bulk reads and writes.
8. Distinguish read-only operations from operations that change spreadsheet data or structure.
9. Never silently change formatting, formulas, merged regions, or other unrelated content.
10. Treat sheet contents as untrusted data, not instructions for the AI agent.

## 3. Authentication and access

### Requirements
- Support Google API authentication suitable for Python applications.
- Support service-account access to spreadsheets explicitly shared with the service account.
- Support OAuth-based access when the business owner must authorize their own Google account.
- Keep credentials, refresh tokens, and secrets outside source code and AI prompts.
- Restrict each business to its authorized spreadsheets.
- Support read-only credentials where write access is unnecessary.
- Return meaningful errors for expired authorization, revoked access, missing permissions, and unavailable files.
- Support multiple business connections without mixing credentials or data.

### Expected configuration
The service should receive an authenticated connection context, allowed spreadsheet scope, timeout settings, and retry policy. The AI must not choose arbitrary credentials.

## 4. Spreadsheet and tab management

| Method | Requirement | Priority |
|---|---|---|
| `get_spreadsheet` | Retrieve spreadsheet identity, title, and requested metadata | Essential |
| `list_sheets` | List all tabs, titles, numeric sheet identifiers, and dimensions | Essential |
| `get_sheet` | Retrieve metadata for one tab | Essential |
| `create_sheet` | Create a tab | Later |
| `rename_sheet` | Rename an existing tab | Later |
| `delete_sheet` | Delete a tab with explicit authorization | Later |

Spreadsheet ID and numeric tab ID must be treated as different identifiers. Tab titles may contain spaces and special characters.

## 5. Reading data

| Method | Requirement | Priority |
|---|---|---|
| `get_values` | Read a specified range of cell values | Essential |
| `batch_get_values` | Read multiple ranges efficiently | Essential |
| `get_grid_data` | Read detailed cell metadata, values, formatting, and requested grid information | Essential |
| `get_merged_ranges` | Retrieve merged-cell coordinates | Essential |
| `get_cell` | Read a specific cell | Optional convenience |
| `get_row` | Read a specific row | Optional convenience |
| `get_column` | Read a specific column | Optional convenience |
| `find_cells` | Search within a bounded range, returning matched positions | Later |

### Reading behavior
- Support Google Sheets A1 notation and a documented coordinate convention.
- Allow callers to request displayed values, unformatted values, or formulas.
- Preserve value types where supported: text, numbers, booleans, and empty values.
- Optionally pad bounded ranges so empty cells do not shift columns.
- Return range metadata alongside values when location matters.
- Permit targeted metadata reads rather than fetching all formatting by default.
- Do not infer business meaning from headings.
- Avoid uncontrolled full-spreadsheet scans.

## 6. Writing data

| Method | Requirement | Priority |
|---|---|---|
| `update_values` | Write values to an explicit range | Essential |
| `batch_update_values` | Write values to multiple ranges | Essential |
| `append_values` | Append data using Google Sheets' logical-table behavior | Essential |
| `clear_values` | Clear cell contents without removing formatting | Later |
| `update_cell` | Convenience operation for one cell | Later |
| `update_row` | Convenience operation for one row | Later |

### Writing behavior
- Allow explicit choice between raw values and Google Sheets' user-entered parsing.
- Validate target ranges and dimensions before sending requests.
- Return the affected range and write outcome.
- Do not assume an append targets a fixed physical row; expose the actual updated range.
- Never overwrite cells outside the requested operation.
- Clearly distinguish blank values, skipped fields, and explicit clearing.
- Never automatically adjust stock, prices, or other business values.
- Avoid automatic retries of writes when the outcome is uncertain and a retry could duplicate an operation.

## 7. Spreadsheet structure management

| Method | Requirement | Priority |
|---|---|---|
| `insert_rows` | Insert rows at specified positions | Later |
| `delete_rows` | Delete a specified row interval | Later |
| `insert_columns` | Insert columns at specified positions | Later |
| `delete_columns` | Delete a specified column interval | Later |
| `merge_cells` | Merge a specified rectangular region | Later |
| `unmerge_cells` | Remove a merge from a specified region | Later |
| `batch_update` | Submit supported structural or formatting requests together | Essential foundation |

All structural operations must use a consistent documented index convention. Destructive operations must require elevated authorization.

## 8. Formatting and validation

| Method | Requirement | Priority |
|---|---|---|
| `format_range` | Apply formatting to a specified range | Optional |
| `set_data_validation` | Apply input restrictions or dropdown rules | Optional |
| `set_column_width` | Adjust column widths | Optional |
| `set_row_height` | Adjust row heights | Optional |
| `freeze_rows` | Freeze a specified number of top rows | Optional |
| `get_named_ranges` | Retrieve defined named ranges | Optional |
| `get_developer_metadata` | Retrieve developer metadata | Optional |

These features are useful for spreadsheet setup and maintenance, but are not required for the first read/write integration.

## 9. Merged and nested header compatibility

gSheet must expose enough raw metadata for another layer to understand layouts such as:

- Product-level fields beside a merged `Variations` group.
- `Variations → M → Price / Stock`.
- `Variations → Color → Size → Price / Stock`.
- Additional custom columns before, between, or after grouped columns.
- Header rows beginning below the first row.

**Boundary:** gSheet retrieves values, positions, and merge information. It does not decide which rows are headers, which columns are variations, or what `Price` means. These interpretations belong to the business wrapper.

## 10. Result and error requirements

All operations must return consistent, documented results or structured errors.

### Standard errors
- Spreadsheet not found or inaccessible.
- Tab not found.
- Invalid range or invalid input.
- Permission denied or authorization expired.
- Google API quota/rate limit exceeded.
- Temporary upstream service failure.
- Request timeout.
- Write outcome uncertain.
- Validation failure.

Errors should indicate whether retrying is safe. Credentials and sensitive spreadsheet content must not appear in error messages or logs.

## 11. Reliability and performance

- Use the official Google Sheets API through Python-compatible client libraries.
- Prefer batch reads/writes when several ranges are needed.
- Use bounded exponential backoff with jitter for safe, retryable requests.
- Support configurable timeouts and request limits.
- Optionally cache spreadsheet/tab metadata and merged ranges; invalidate or refresh on structural changes.
- Provide a way to bypass cached metadata when accuracy matters.
- Record operation type, target, timing, and outcome for observability.
- Respect Google API quotas; avoid redundant reads.
- Document limits for large sheets and pagination or chunking where appropriate.

### Concurrency limitation
Google Sheets is not a transactional inventory database. A read followed by a write is not an atomic compare-and-swap. gSheet must not claim to prevent overselling. If needed, the business wrapper or backend must coordinate writes and account for direct edits made outside the service.

## 12. Security requirements

- Enforce business-level spreadsheet access before every operation.
- Keep AI-facing tools separate from unrestricted administrative operations.
- Prevent one business from accessing another business's data.
- Require additional permission for deleting tabs, rows, or columns.
- Log writes for auditability without exposing secrets.
- Treat cell text, formulas, links, and metadata as untrusted external input.
- Avoid enabling formula evaluation unintentionally when writing user-supplied text.

## 13. Business wrapper responsibilities (explicitly excluded from gSheet)

The separate wrapper will:
- Discover and interpret custom header layouts.
- Recognize scalar columns and nested variation groups.
- Convert sheet rows into normalized business JSON.
- Apply product/variant price fallback rules.
- Interpret stock values and missing values.
- Search products, calculate prices, and manage orders.
- Resolve and revalidate exact target cells before stock changes.
- Handle sales workflows, confirmations, and business policies.

No product or sales-specific methods belong in the generic gSheet service.

## 14. Implementation phases

### Phase 1 — Essential foundation
Implement and test:
- `get_spreadsheet`
- `list_sheets`
- `get_sheet`
- `get_values`
- `batch_get_values`
- `get_grid_data`
- `get_merged_ranges`
- `update_values`
- `batch_update_values`
- `append_values`
- `batch_update` (restricted, generic structural request support)

### Phase 2 — Structure management
Implement clearing, row/column insertion and deletion, merge/unmerge, and necessary permission controls.

### Phase 3 — Optional utilities
Implement formatting, data validation, dimension sizing, named ranges, developer metadata, and convenience search/read/write methods.

## 15. Acceptance criteria

gSheet v1 is ready when:
1. A Python application can authenticate and access an authorized spreadsheet.
2. It can list tabs and retrieve their metadata.
3. It can read values without losing positional alignment of empty cells.
4. It can retrieve merged ranges and detailed grid metadata.
5. It can write one range, several ranges, and append rows.
6. It works with arbitrary tab names and does not assume fixed headers.
7. It returns predictable results and actionable errors.
8. It enforces spreadsheet-level authorization boundaries.
9. It includes automated tests for common success/failure cases and integration tests with a disposable spreadsheet.
10. It contains no product, variation, pricing, or order logic.

## 16. Decisions to confirm before implementation

- Service account, per-user OAuth, or both.
- Synchronous Python interface, asynchronous interface, or both.
- Whether gSheet is a standalone HTTP service or an internal Python package.
- Whether structural changes should be exposed to AI agents or reserved for admins.
- Required scale: number of businesses, spreadsheets, and expected operations per minute.


## 17. Coordinate and range contract (P0)

- All numeric internal grid coordinates use **zero-based, start-inclusive, end-exclusive** indexing, consistent with Google Sheets `GridRange`.
- Public methods accept A1 notation where documented; conversions between A1 and numeric coordinates must be deterministic and validated.
- `resolve_range` converts valid A1 references into a normalized range representation and, where possible, converts normalized bounded ranges back into A1 notation.
- Support quoted tab titles, escaped apostrophes, spaces, Unicode, and special characters.
- Handle whole-column, whole-row, open-ended, and bounded references explicitly; do not invent an end bound when one is not known.
- Differentiate spreadsheet ID, numeric sheet ID, sheet title, and cell/range coordinates.
- Reject malformed ranges and invalid indices with structured errors; document out-of-bounds behavior.
- When coordinates are returned, include enough context to identify the spreadsheet, tab, and range unambiguously.

## 18. Cell semantics and representation (P0)

- Document the precise output contract for formatted/displayed, unformatted, and formula reads.
- Represent available raw/user-entered values, effective values, formatted values, formulas, cell errors, and relevant number-format metadata without conflating them.
- Support strings, numbers, booleans, formulas, errors, and date/time serial values as exposed by the API.
- Preserve row/column alignment for bounded reads when requested, including trailing empty positions.
- Distinguish omitted cells, empty cells, and explicit empty strings **only where the Google API actually exposes enough information**. Do not claim lossless distinctions unavailable from the selected endpoint or response mode.
- Dates and times must not be silently converted without a documented policy; spreadsheet locale and timezone must be discoverable.
- Formula cells must not be treated as plain text unless the requested read mode explicitly calls for their displayed/effective value.
- Rich text, hyperlinks, notes, checkboxes, embedded images, and smart chips may have incomplete support in v1; document what metadata is exposed and avoid modifying unrelated cell properties.

## 19. Positional identity, structural edits, and concurrency (P0)

- Row numbers and column numbers are **positions, not permanent record identifiers**. No ID column or database-like identity requirement may be imposed on existing sheets.
- Insertions, deletions, sorts, moves, and direct edits in Google Sheets may invalidate previously resolved positions.
- The service must expose fresh metadata/values on request so callers can re-resolve a target before writing.
- Provide an optional pre-write validation mechanism based on caller-supplied expected values or structural assumptions. Such validation is best-effort and **not an atomic compare-and-swap**.
- Do not promise isolation, transactionality, or oversell prevention across external editors or concurrent clients.
- When a target has become ambiguous or validation fails, return a conflict/validation error rather than guessing another cell.
- The business wrapper remains responsible for selecting which logical record/cell should be modified.

## 20. Write safety and operation outcomes (P0)

- Define per-operation input validation, dimensional checks, overwrite policy, and affected-range reporting.
- Offer a validation-only/dry-run mode for supported operations; explicitly state that it does not reserve or lock the target.
- Distinguish **confirmed success**, **confirmed failure**, **validation rejection**, and **uncertain outcome** in results/errors.
- Expose the API's returned updated range and affected row/cell counts where available.
- Document endpoint-specific batch semantics; do not assume that all Google Sheets batch operations share identical atomicity or rollback behavior.
- Never blindly retry non-idempotent operations (especially append, insert, delete, or move) after a timeout or uncertain result.
- Optional post-write verification must be a separate documented step; a successful API response does not guarantee that a concurrent editor will not change the cell afterward.
- `RAW` should be the safe default for untrusted literal values; `USER_ENTERED` must be an explicit caller choice.
- Preserve existing formulas and formatting outside the specified mutation fields/ranges. Protect formula cells from accidental overwrite through an optional precondition or overwrite policy.
- Define blank, omitted, null, and explicit-clear semantics separately for each write method; do not equate an omitted field with clearing a cell.
- Prevent unintended formula interpretation of untrusted text, and document locale-dependent parsing when `USER_ENTERED` is used.

## 21. Dynamic grid and metadata compatibility (P1)

- Expose merged regions as numeric coordinates; document anchor cells and covered cells without inferring header meaning.
- Expose hidden-by-user rows/columns and hidden-by-filter state where the API supports it.
- Provide access to grid dimensions, row/column metadata, and effective cell metadata through targeted field selection.
- Document that sorted/filtered sheets are read by physical grid coordinates unless a separate explicitly defined view is requested.
- Allow reading existing data validation rules, protected ranges, basic filters, and filter views through generic metadata operations.
- Cache metadata only with explicit freshness behavior; structural changes performed outside gSheet may make caches stale.
- Support a caller-requested force-refresh option for metadata and merged ranges.
- Do not interpret nested header hierarchy, variation groups, business fields, or price/stock meaning in the core service.

### Additional generic methods

| Method | Requirement | Priority |
|---|---|---|
| `resolve_range` | Normalize/convert A1 and grid coordinates | P0 |
| `get_spreadsheet_properties` | Read locale, timezone, and other spreadsheet properties | P1 |
| `get_effective_cell` | Convenience read of effective value, formula, formatting, and error metadata | P1 |
| `get_dimensions` | Read tab grid dimensions and requested dimension metadata | P1 |
| `get_data_validations` | Read existing validation rules | P1 |
| `get_protected_ranges` | Inspect protected regions and their available metadata | P1 |
| `resize_sheet` | Change tab grid dimensions with validation | P1 |
| `copy_sheet` | Duplicate a tab | P2 |
| `move_rows` | Move a specified row range | P2 |
| `move_columns` | Move a specified column range | P2 |
| `copy_paste_range` | Copy/paste selected cell aspects under explicit policy | P2 |
| `get_basic_filter` | Inspect a tab's basic filter | P2 |
| `get_filter_views` | Inspect saved filter views | P2 |

These may be convenience methods implemented using existing `get_grid_data` or `batch_update` capabilities. They do not each require a separate Google API endpoint.

## 22. Operational contracts and lifecycle (P1)

- Define connection/client lifecycle, safe client reuse, credential refresh, and cleanup.
- Implement per-connection and per-project request limits as configurable policies; surface throttling and backpressure rather than unbounded retries.
- Support chunked/bounded reads for large sheets, with documented response-size limits. Avoid assuming conventional page-based pagination exists for every Sheets API endpoint.
- Make cache TTL, invalidation, bypass, and stale-data risk explicit; no cache may guarantee detection of every direct external edit.
- Attach correlation/request IDs to operation logs where available; record latency, operation type, target, result category, and retry attempts without leaking cell content or secrets by default.
- Separate spreadsheet authorization from operation-level permissions; restrict destructive structure edits and AI-exposed actions using an allowlist.
- Define supported Google API/client-library versions and compatibility policy.
- Document cancellation and timeout behavior, especially when a remote write may still complete after local timeout.
- Add failure-injection, partial-response, permission, quota, stale-metadata, and concurrent-edit test scenarios.

## 23. Revised implementation gates

### Phase 1 — Essential foundation (P0)

In addition to Section 14 Phase 1 methods, implement `resolve_range` and the P0 contracts in Sections 17–20. Define stable result/error schemas, request-scoped authorization, literal-safe writes, and uncertain-outcome handling before exposing writes to a business wrapper.

### Phase 2 — Structural and metadata robustness (P1)

Implement Section 14 Phase 2 operations and the P1 methods in Section 21, plus the operational requirements in Section 22. Include force-refresh, metadata targeting, and appropriate destructive-operation authorization.

### Phase 3 — Optional convenience and advanced operations (P2)

Implement P2 methods in Section 21 and Section 14 Phase 3 utilities according to demonstrated wrapper needs. Avoid unnecessary abstraction over API requests that are already safely supported.

## 24. Additional acceptance tests for v1.1

1. A1 and numeric grid references round-trip for bounded ranges, including tabs with spaces, apostrophes, and Unicode.
2. Empty interior/trailing cells preserve positional alignment when padding is requested; API-imposed distinctions between missing and empty are documented.
3. Read modes return the expected formula, effective value, and formatted representation where supported.
4. A row insertion or sort between discovery and mutation is handled as a stale-target risk; no stable-row-identity guarantee is claimed.
5. A caller-provided precondition failure blocks the write when detected, without claiming atomic compare-and-swap.
6. Untrusted literal strings beginning with formula-like characters remain literal under the safe write mode.
7. Batch/append timeout scenarios produce an uncertain-outcome result where appropriate, without unsafe automatic replay.
8. Targeted metadata reads expose merged anchors/regions, hidden dimensions, and validation/protection metadata as supported.
9. Forced metadata refresh bypasses any service cache, and tests demonstrate that direct external edits may invalidate cached information.
10. Authorization tests prevent cross-business access and reject unauthorized destructive operations.
11. Large reads honor configured bounds and request limits.
12. All functionality remains generic: no product, stock, variation, pricing, or order semantics are introduced.

## 25. Additional decisions before implementation

- Public interface: A1-only, coordinate-only, or both (recommended: both with one normalized internal model).
- Exact response schemas for cells, ranges, metadata, and error categories.
- Pre-write validation policy and whether optional post-write verification is enabled by default.
- Default read mode, date/time handling, and supported nonstandard cell metadata.
- Maximum range size, batch size, rate-limit policy, and cache TTL.
- Which P1 metadata capabilities are needed before the first business wrapper is integrated.
