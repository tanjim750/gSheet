# gSheet

Generic Python Google Sheets service foundation. Configuration, authentication,
the low-level Sheets API client, and the generic SheetService are implemented.
Repositories, the facade, and business-specific wrappers remain unimplemented.
Python 3.11+ is required.

## Configuration

`GSheetConfig` contains immutable `APIConfig`, `AuthConfig`, `RetryConfig`,
`CacheConfig`, and `OperationalConfig` sections. Explicit section objects replace
the entire corresponding environment section. Remaining precedence is process
environment, explicitly selected `.env` file, then application defaults.
`.env` loading is optional through the `dotenv` package extra; see `.env.example`
for supported variables. Loading never mutates the process environment.

Configuration alone requires no credentials and makes no network calls. Sensitive
paths are hidden from representations and redacted by `GSheetConfig.to_dict()`.
Do not use generic object introspection for logging or serialization.
Scopes must be supported raw HTTPS URLs, comma-separated in GSHEET_API_SCOPES.
Markdown links, malformed URLs, unknown scopes, and duplicates are rejected as
`ConfigurationError` for `api.scopes`; values are never rewritten into scope URLs.

## Authentication interface

The root `gsheet` package exports `CredentialManager`, `AuthenticationStatus`,
`AuthenticationError`, and `AuthenticationErrorCode` alongside configuration.
Each manager takes a `GSheetConfig` and a required, stable `connection_id` keyword
argument. The trusted application assigns that identifier; it must not come from
spreadsheet data or AI-selected credentials. Use a distinct manager per connection.

Public lifecycle methods:

- `initialize(reload=False)` loads only configured local files. OAuth without a
  stored token returns a status requiring interaction; no browser or network is
  started. `reload=True` discards local state and rereads the configured files.
- `status` reports initialization, local validity, refresh need, and interaction
  need without contacting Google. Local validity does not detect remote revocation.
- `requires_interactive_authorization()` initializes locally if necessary and
  reports whether OAuth interaction is needed.
- `get_credentials()` returns a sensitive Google-compatible object to the future
  API client, refreshing on demand if enabled and required. It never starts
  OAuth interaction. Do not expose returned objects through tools, logs, or JSON.
- `refresh()` explicitly refreshes credentials over HTTPS, using the configured
  request timeout. It honors `refresh_enabled`, including for service accounts.
- `authorize_interactively(allow_browser=False, reauthorize=False,
  timeout_seconds=300)` is OAuth-only. `allow_browser=True` is mandatory. Existing
  in-memory credentials require `reauthorize=True` for replacement. Explicit
  reauthorization checks storage ownership/binding without requiring the old
  credentials to initialize successfully. The local
  callback wait defaults to 300 seconds; token exchange separately uses the API
  request timeout. This synchronous call waits for the user and uses a loopback
  listener, PKCE, OAuth state validation, explicit consent, and account selection.
- `reset()` forgets in-memory state without deleting token files or revoking access.

## Service-account lifecycle

Set service-account mode and an explicit JSON credential path. Loading validates
the credential type, required fields, signing key, Google token endpoint, and
configured scopes. No ADC discovery, domain-wide delegation, identity fallback,
or network validation occurs. A new service-account credential normally needs a
token refresh before use. Refresh can establish a usable access token, but cannot
prove access to a particular spreadsheet. Replace the configured file and reload
to replace the identity deliberately.

## OAuth lifecycle and storage

OAuth requires a configured Google **installed-application** client JSON file.
Web-client configurations, custom identity-provider endpoints, and alternative
Google universes are unsupported. With no token file configured, authorization
and refresh use memory only. Missing stored tokens require explicit interactive
authorization. Expired tokens refresh only when refresh is enabled and a refresh
token exists. Tokens with missing expiry are treated as needing refresh.

Configured token files use Google authorized-user JSON plus a format version and
a SHA-256 binding of the connection identifier. Client identity and exact scope
sets must match. Plain Google token files without a binding are deliberately not
adopted. Use a new per-connection token path and explicitly authorize to migrate;
never relabel a token belonging to another business.

Persistence writes a temporary file in the destination directory, flushes it,
and publishes it atomically. On POSIX, tokens must be owned by the current user,
have no group/other permissions or extra hard links, and are written with mode
0600. Final symlinks are rejected where the OS supports `O_NOFOLLOW`. The parent
directory must be owned by the current user and not group/world writable.
Credential files must be regular files; their contents are never logged.

A sibling `.lock` file serializes cooperating writers. Stored bindings and the
loaded-file fingerprint are checked before replacement; new files are created
without overwriting an existing destination. A crash can leave a stale lock;
remove it only after ensuring no writer is active. Protect the directory from
uncooperative writers. File contents are plaintext protected by OS permissions,
not encryption; non-POSIX permission guarantees depend on the host ACLs.

Revoked/expired grants return a structured error requiring explicit reauthorization.
Refresh failures discard potentially mutated in-memory credentials. For a
transient failure, explicitly reload before retrying; there is no automatic
fallback or manager retry loop. Normal initialization still rejects corrupt or
scope-mismatched tokens. Explicit reauthorization can replace incompatible scopes,
expired/revoked credentials, or corrupt credential fields when the JSON connection
and OAuth-client binding remain intact. Old tokens and in-memory credentials are
preserved until new authorization, validation, and atomic storage succeed. The old
file fingerprint is checked again before replacement to reject concurrent changes.
Foreign, unbound, insecure, or unparseable files remain blocked: their ownership
cannot be safely established. Select a new connection-specific storage location
to authorize without deleting or overwriting them. Reauthorization does not revoke
older grants.

## Errors and authorization boundary

`AuthenticationError.to_dict()` exposes only a fixed code/message, retryability,
and whether interaction is required. Categories cover missing/invalid credentials,
unsupported mode, missing authorization, expired/revoked grants, unavailable/failed
refresh, insufficient scopes, storage failures, connection mismatches, network
failures, missing interaction consent, and failed authorization.
Upstream exception messages are not propagated. The callback handler never logs
request URLs, and authentication operations suppress dependency auth/HTTP logging
for their executing thread while secrets are in use.

Authentication establishes credentials, not spreadsheet authorization. No manager
method accepts a spreadsheet ID or grants administrative privileges. The API
client enforces connection-specific spreadsheet and operation permissions before
each request and obtains credentials through the manager, respecting its refresh
policy. Valid-looking local scopes are not
proof that Google actually granted them or that a spreadsheet is accessible.

Runtime authentication dependencies are `google-auth[requests]` and
`google-auth-oauthlib`. No Google Sheets data-access dependency is included.

## Low-level API client

`SheetsClient(config, credentials, authorization=...)` owns a `SheetsTransport`.
Use the same trusted connection's configuration, credential manager, and grants.
Both constructors are inert: they neither initialize credentials nor send requests.
Calls are synchronous and serialized per transport; use `close()` or the context
manager to release the HTTP session. No repository or business interpretation is
performed. HTTP requests use the existing requests transport dependency and
Google-compatible credentials; there is no discovery-client dependency.

`SheetsClient.execute()` and `SheetsTransport.request()` accept a `SheetsEndpoint`
enum, explicit spreadsheet ID, optional `range_name`, `params`, and JSON `body`.
The transport owns endpoint validation and execution; client wrappers only select
endpoints and assemble arguments. Arbitrary URLs and caller-defined safety labels
are unsupported. The fixed endpoint registry determines methods and retry safety.

| Client method | Sheets API endpoint | HTTP |
|---|---|---|
| `get_spreadsheet` | `spreadsheets.get` | GET |
| `get_values` | `spreadsheets.values.get` | GET |
| `batch_get_values` | `spreadsheets.values.batchGet` | GET |
| `update_values` | `spreadsheets.values.update` | PUT |
| `batch_update_values` | `spreadsheets.values.batchUpdate` | POST |
| `append_values` | `spreadsheets.values.append` | POST |
| `clear_values` | `spreadsheets.values.clear` | POST |
| `batch_update` | `spreadsheets.batchUpdate` | POST |

No supported endpoint uses HTTP DELETE; deletion is a permitted POST batch request.
IDs and A1 ranges are encoded as path components. A1 parsing, coordinate resolution,
padding, and dimensional checks are provided separately by SheetService.
Query parameters use Google's camelCase names and endpoint-specific allowlists;
boolean, range-list, enum, and field-mask input types are checked locally. Unknown
query parameters, unsupported endpoints, non-finite JSON numbers, malformed value
arrays, and unsupported batch request types are rejected before authentication.
Google validates the remaining endpoint-specific schemas and field-mask syntax.

Value writes default to `RAW`; `USER_ENTERED` must be explicit. Values retain
Google's null/blank behavior, without normalization. Spreadsheet metadata defaults
to `spreadsheetId,properties,sheets.properties` with grid data disabled. Explicit
`fields`, `ranges`, and `includeGridData` support targeted reads. Google's rules
for field masks versus includeGridData still apply. Batch range counts obey
`max_read_batch_ranges` and `max_write_batch_ranges`; the latter is a values-range
limit, not a structural-request count. `max_read_cells` is not enforced by this
raw endpoint layer; SheetService bounds and limits reads using fresh dimensions.

`APIResponse` contains `data` (unaltered Google JSON), status code, local request
ID, attempt count, and elapsed seconds. Its representation excludes data. Treat
data as untrusted, potentially sensitive spreadsheet content; do not log or expose
it indiscriminately. No padding, row parsing, or result normalization is performed.

## Request authorization

An immutable `AuthorizationPolicy` supplies explicit `allowed_spreadsheet_ids`,
`allowed_operations` (`OperationKind` members), and `allowed_batch_requests`.
Missing policy and an empty spreadsheet set deny every request. OAuth scopes do
not replace these grants. Write endpoints additionally require read/write API
configuration. Authorization runs before credential acquisition or network work.

Reads require READ. Value updates/appends require WRITE. Clearing additionally
requires DESTRUCTIVE. All structural batches require WRITE and STRUCTURAL plus an
explicit grant for every contained request type. DESTRUCTIVE is also required for
deleteSheet, deleteDimension, deleteNamedRange, mergeCells, cutPaste, moveDimension,
sortRange, and updateSheetProperties. The last is deliberately conservative because
property updates can shrink the grid; even renames require the elevated grant.
Explicit value/spreadsheet readback in a write response additionally requires READ.

Supported batch request types are addSheet, duplicateSheet, insertDimension,
appendDimension, deleteSheet, deleteDimension, mergeCells, unmergeCells,
updateSheetProperties, updateDimensionProperties, repeatCell, setDataValidation,
updateCells, appendCells, copyPaste, cutPaste, moveDimension, addNamedRange,
updateNamedRange, deleteNamedRange, updateBorders, autoResizeDimensions, and sortRange.
Unknown types fail closed even if named in the policy. These are raw API requests,
not implemented high-level structural services.

The trusted caller owns the association between business, connection, and policy.
This layer provides spreadsheet/operation grants, not tab/cell ACLs or business
identity verification. CredentialManager and clients must never be shared across
businesses. Nothing coordinates direct edits or provides record identity,
transactions, or compare-and-swap guarantees.

## Reliability and observability

GET reads may retry only configured categories: rate_limit (429 and recognized
403 quota reasons), temporary_upstream (500/502/503/504 and connection failures),
and timeout (request timeouts/408). TLS/certificate errors are not retried.
Timeouts are excluded by the existing default
retry categories. Total attempts, exponential backoff, delay cap, and jitter use
RetryConfig. Retry-After seconds or HTTP dates are honored; if the server requests
a delay exceeding the configured backoff cap, execution stops and reports that
delay instead of retrying early. No retries are added by the HTTP adapter.

Every mutation has one attempt. Lost connections, timeouts, 5xx, redirects, or
malformed successful responses return `write_outcome_uncertain` and never replay
the mutation. A specifically identified ConnectTimeout occurs before connection
establishment and returns request_timeout/confirmed_failure instead; it still
does not trigger an automatic write retry. ReadTimeout and ambiguous Timeout or
connection interruptions remain uncertain. Known 4xx rejections remain confirmed failures. No blanket batch
atomicity, rollback, or idempotency guarantee is inferred. Read-only retries never
initiate interactive authorization. An HTTP 401 returns an authentication error
for explicit caller handling rather than silently switching identity or replaying.

The configured timeout applies per HTTP attempt, not to the whole operation;
credential refresh and backoff can extend total duration. Local rolling-minute
read/write budgets use the lower of per-project and per-user settings. Each
attempt reserves a slot before credential acquisition; an authentication failure
may consume that slot. Exhaustion fails immediately with a structured throttle
error and retry delay. These budgets are per transport, not distributed project
quota enforcement. Disabling throttling does not change Google's quotas.

Transport sessions disable redirects, netrc discovery, environment proxies, and
implicit transport refresh. TLS verification remains enabled. Proxy configuration
is not exposed in this milestone. Logs contain operation, hashed target, local
request ID, cumulative latency, attempt, status, and outcome, with no URLs, tokens,
query/body content, or upstream error descriptions. Configured logging level is
instance-local; successful request records require INFO logging. Authentication
and HTTP dependency diagnostics are suppressed during sensitive operations.

`APIClientError.to_dict()` reports safe code/message, HTTP status, retryability,
outcome, attempts, request ID, and optional retry delay. Categories distinguish
invalid requests, authentication, permission, not-found/inaccessible, quota, local
throttling, timeout, transport, upstream, malformed response, uncertain write,
other HTTP errors, and closed transport. Credential-manager AuthenticationError
exceptions remain separate and propagate unchanged. Upstream bodies/descriptions
are intentionally excluded from failures.

Additional diagnostics identify `source` (configuration, request_validation,
local_authorization, google_api, transport, local_throttling, or client). Local
permission failures have a fixed `reason`: read_only_configuration,
spreadsheet_not_allowed, operation_not_allowed, or batch_request_not_allowed.
Operation denials also report safe `missing_operations` names. These distinguish
the actual client's read-only configuration from policy rejection and an upstream
HTTP 403. OAuth scope mismatches remain `AuthenticationError` with the existing
insufficient_configured_scopes code; invalid configured scopes fail earlier as
`ConfigurationError`. Request and response content is excluded from diagnostics.

## Diagnosing local integration configuration

Use the project's installed Python environment (locally, `.venv/bin/python`) and
verify `gsheet.__file__` points to the intended checkout. A system interpreter may
have a different installation or no gSheet package at all.

`GSheetConfig()` does not automatically read `.env`. To use that file, explicitly
select `GSheetConfig(dotenv_path=Path(".env").absolute())` with `Path` imported from
`pathlib`. Pass that same config object to CredentialManager and SheetsClient.
Constructing another default configuration for the client restores read-only
defaults even if the credential manager used read/write configuration.

Process variables override `.env`; explicit section objects override both, for
the entire section. A scope check using `environ={}` or an explicit APIConfig
verifies that input, not the local `.env` file. Check the actual effective
`config.api.access_mode` and scope tuple. The read/write scope must be exactly
`https://www.googleapis.com/auth/spreadsheets`; Markdown formatting is not part of
the URL. Do not print the full environment or credential objects for diagnostics.

An update rejected with source=configuration and reason=read_only_configuration
needs the correct client configuration, not broader spreadsheet grants. A
local_authorization error identifies policy rejection; a google_api error has
the upstream HTTP status. Successful local write preparation does not establish
that the named tab exists: verify tab titles with metadata first. Never create a
test tab or perform a live write merely to diagnose configuration without the
owner's approval.

## Generic Sheet Service

The dependency flow is business wrapper → SheetService → SheetsClient → Google
Sheets API. One coherent service uses focused range/value utilities and small
physical-grid/result types. It does not implement repositories, the facade,
schema discovery, business entities, or database identity.

Construct with an existing authorized client and an explicit spreadsheet ID:

```python
from gsheet import SheetService

service = SheetService(client, spreadsheet_id, limits=config.operations)
```

Construction performs no I/O, loads no credentials, and starts no OAuth flow.
The caller owns the client lifecycle. Pass the matching configuration's
OperationalConfig as `limits`; omitting it uses the application defaults, not
the client's custom limits. The client independently enforces its own limits
and permissions. A spreadsheet is the entire file; a worksheet is a tab.
Worksheet arguments accept an exact title or numeric sheet ID (not tab index).

### Public method inventory

| Group | Methods |
|---|---|
| Spreadsheet metadata | `get_spreadsheet(fields=...)` |
| Worksheets | `list_worksheets`, `get_worksheet`, `worksheet_exists`, `create_worksheet`, `rename_worksheet`, `delete_worksheet`, `duplicate_worksheet` |
| Cells | `read_cell`, `update_cell`, `clear_cell`, `update_cells` |
| Ranges | `read_range`, `update_range`, `clear_range`, `read_ranges`, `update_ranges` |
| Rows | `read_row`, `read_rows`, `update_row`, `update_rows`, `append_rows`, `insert_rows`, `delete_rows`, `clear_row`, `find_rows` |
| Headers | `read_headers`, `set_headers`, `find_columns`, `find_column`, `rename_header` |
| Columns | `add_columns`, `insert_columns`, `remove_columns`, `move_columns`, `reorder_columns` |
| Ordered dimension batches | `batch_structure` |
| P0 detailed reads and resolution | `resolve_range`, `get_grid_data`, `get_merged_ranges` |
| Restricted structural foundation | `batch_update` |

All data/structure methods take a worksheet argument, except creation,
spreadsheet-level batch_update, and the per-worksheet entries in batch_structure.
Normal mutations return the existing
APIResponse, retaining actual updated ranges/counts and structural replies from
Google. Supported safety-enabled dry-runs return RequestPreview instead.
Creating/duplicating returns the API response containing the new sheet
properties; it does not hide an extra metadata read. `reorder_columns` returns
None when the requested order is already satisfied.

Metadata methods return APIResponse or WorksheetInfo as appropriate; listing
returns worksheet metadata in physical tab order. WorksheetInfo includes ID,
title, tab index, dimensions, merged CellRanges, and raw metadata (excluded from
repr). No metadata cache is kept. Structural mutations resolve current metadata
before sending numeric sheet IDs. All service requests still go through client
authorization; metadata resolution means service mutations also need READ.

### Coordinates and ranges

Every public numeric row/column index is **zero-based**, with **end-exclusive**
intervals. Row 0/column 0 identifies A1. Rows are positions, never stable record
IDs. A worksheet's numeric sheet ID is distinct from its tab index.

`CellRange(start_row, start_column, end_row, end_column)` represents a rectangle;
end bounds may be None until resolved against known worksheet dimensions. Range
methods accept CellRange or A1 text. A1 input may be unqualified or qualified
with the selected worksheet's exact title. A mismatched title is rejected.
All generated references quote titles and double embedded apostrophes.

Exported utilities are `column_index_to_a1`, `column_a1_to_index`,
`row_index_to_a1`, `row_a1_to_index`, `quote_sheet_name`, `parse_a1_range`, and
`format_a1_range`. The parser returns `(optional_title, CellRange)` and supports
cells, rectangles, absolute `$` references, whole columns/rows, and open-ended
forms such as A2:B, B2:5, or A5: (both end bounds unknown). The service explicitly
bounds these using fresh grid dimensions before API reads. Formatting requires bounded coordinates and emits
canonical qualified A1. Named ranges, R1C1 notation, external spreadsheet
references, and interpreting a bare title as a whole sheet are unsupported.

Ranges are checked against fresh grid bounds. Ordinary updates do not implicitly
resize a grid. A write matrix may be ragged and smaller than its target; supplied
values must fit, and omitted trailing values are not padded or cleared.
`update_rows` derives the target rectangle from the supplied matrix and start
position. Explicit clear operations clear contents while preserving formatting;
deleting dimensions removes rows/columns and shifts positions.

### Read and write semantics

Range/row/header reads return RangeValues with spreadsheet ID, WorksheetInfo,
resolved CellRange, values matrix, read mode, and original APIResponse. Batch
reads return one RangeValues per requested range, in request order. `read_cell`
returns a scalar or None when no value is available. Values/formulas are excluded
from RangeValues repr. Raw API response content remains available to trusted
callers and must be handled as potentially sensitive, untrusted data.

Read modes are FORMATTED_VALUE (default), UNFORMATTED_VALUE, and FORMULA. Formatted
reads follow Google formatting/locale; unformatted reads preserve numeric and
boolean types, including date/time serial numbers; FORMULA requests formula
expressions. This service does not silently convert dates, booleans, or numbers
to strings. Detailed effective/formatted/user-entered cell metadata is available
through bounded get_grid_data reads and targeted field masks.

Sparse output is preserved by default. `pad=True` fills omitted bounded positions
with None without modifying the API response. None denotes unavailable API data,
not proof of an empty cell versus an omitted cell. Explicit empty strings remain
empty strings. Google values endpoints cannot expose every empty/missing-cell
distinction; no lossless reconstruction is claimed.

Writes default to RAW. USER_ENTERED is explicit and enables Google's locale-aware
parsing, including formulas. RAW leaves formula-like untrusted strings literal.
None skips a value; an empty string explicitly empties its contents under Google's
values API. No omitted field is treated as a clear request. Existing formulas
inside a caller's explicit mutation target can be overwritten by default. Optional
WriteSafety provides explicit formula protection/preconditions; post-write
verification remains a separate caller read.

### Headers, merges, and dynamic layouts

Header operations require an explicit `header_row`; no worksheet is assumed to
have headers. `read_headers(depth=...)` returns ordinary rows and merge metadata,
without filling merged values into covered cells or inferring a hierarchy.
`set_headers` writes one row; use update_range for a caller-defined multirow header.
Headers need not be unique or database fields. Lookup uses exact displayed strings.
`find_columns` returns every matching physical column. `find_column` rejects
duplicates unless an explicit zero-based occurrence is selected. rename_header
uses the same rule; missing headers never fall back to another column.

The service exposes merged coordinates and permits value updates at merge anchors.
It rejects non-null values addressed to covered merged cells instead of unmerging
or flattening them. A null skip over a covered position is allowed. A structural
move/delete may still conflict with Google's merge/protection rules; upstream
errors propagate. There is no business-specific nested-header interpretation.

`find_rows` requires a bounded row interval and explicit numeric column. It returns
physical row indexes in order, using the selected read representation. Numeric
equality does not confuse booleans with 0/1. Searching for None matches unavailable
positions as represented by the values endpoint, not a guaranteed blank-cell type.

### Structural and batch behavior

`insert_rows`/`insert_columns` insert blank dimensions before an existing index.
The start must be inside the current grid; inheritance from before is invalid at
index 0. `add_columns` appends physical grid columns. `append_rows` appends values
using Google's logical-table detection within the required explicit table_range;
it is not insertion at an exact row. It returns the API's actual append range.
Filling inserted blank rows is a separate explicit update, not an implicit
multi-request transaction. Google validates remaining grid/cell-count constraints.

`move_columns` uses Google's **pre-removal destination index**, not the final index
of the moved block. Destinations inside or immediately adjoining the source are
rejected as invalid/no-op. `reorder_columns(order, start_column=...)` takes a
permutation of the absolute column indexes in one contiguous block, preserves
columns outside that block, and generates ordered moveDimension requests.

`StructuralChange` describes insert_rows, delete_rows, insert_columns,
delete_columns, add_columns, or move_columns, with worksheet, start/end, count,
destination, and inheritance arguments as relevant. batch_structure resolves
metadata once and validates changes in order, tracking dimensions after previous
changes. Callers express later indexes in that resulting grid. It submits one
structural API batch. Worksheet create/rename/delete/duplicate remain separate
methods; arbitrary raw structural payload orchestration stays in SheetsClient.

Noncontiguous reads/writes and cell updates use the values batch endpoints.
Write regions must not overlap. Read/write range counts honor service limits;
total resolved cells across a read batch must not exceed max_read_cells. Reads
and writes are never silently chunked into partially successful sequences.
Read/update batches currently address one worksheet per service call; ordered
structural batches can address multiple existing worksheets.

No underlying authorization is bypassed: value writes require WRITE; clear
requires DESTRUCTIVE too; structural batches require WRITE, STRUCTURAL, and each
request-type grant. Deletes/moves and conservative updateSheetProperties handling
add DESTRUCTIVE as documented above (including worksheet rename). An unknown
request type or missing grant still fails closed. Uncertain writes propagate
unchanged; the service does not replay mutations or claim rollback across calls.

### Errors, verification, and manual integration

SheetServiceError distinguishes invalid input/range, worksheet-not-found,
header-not-found, ambiguous header, unsupported layout, and malformed service
response. Its diagnostics omit cell contents and titles. APIClientError and
AuthenticationError propagate unchanged, retaining local/upstream distinctions.
worksheet_exists returns False only for a missing worksheet; it never swallows
spreadsheet permission, authentication, or network failures.

Run the standalone standard-library verifier with no real credentials or network:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python verify_sheet_service.py
```

It uses autospecced clients and mocked HTTP, including the actual authorization
path. No tests/ or examples/ directory or extra test dependency is required.

For manual integration, explicitly load the existing authorized configuration,
reuse its CredentialManager/SheetsClient and a spreadsheet-scoped policy, and pass
config.operations into SheetService. First list worksheets and confirm the
dedicated gSheet_Test tab exists. Do not modify Sheet1. Read a bounded range in
each needed mode; only after explicit approval write a disposable bounded test
region on that tab and read it back. Exercise clear, insert/delete, column moves,
rename, and tab deletion only on disposable data with separate explicit approval
and the exact structural/destructive grants. No live calls are made by verification.

Remaining limitations include concurrent external edits between metadata/values
discovery and mutation, no compare-and-swap, no stable row identity, no automatic
header hierarchy or merge discovery beyond exposed metadata, no non-grid data
operations, no cross-worksheet values batching in this interface, and no automatic
chunking. Header lookup followed by rename is also best-effort, not atomic. Direct
external sorts, insertions, or edits can invalidate positions even after fresh
reads; the business wrapper must re-resolve logical targets and coordinate writes.

## Phase 1 (P0) contracts

The service keeps the original 37 methods and adds four: resolve_range,
get_grid_data, get_merged_ranges, and batch_update. No redundant get_values,
list_sheets, or get_sheet aliases are added: read_range/read_ranges,
list_worksheets/get_worksheet, update_range/update_ranges, and append_rows satisfy
the specification's corresponding value/tab operations.

### Gap analysis and coverage

| Requirement | Starting implementation | Starting status | P0 action |
|---|---|---|---|
| §4 spreadsheet/tab metadata | Existing metadata methods | Complete | Preserved typed WorksheetInfo and field selection |
| §5, §18 value reads/alignment | read_range/read_ranges, padding | Complete | Preserved and regression-checked |
| §5, §18 targeted grid and cell semantics | Raw metadata only | Partial | Added CellInfo and GridDataResult |
| §5 merged ranges | WorksheetInfo merges | Partial | Added context-bearing get_merged_ranges |
| §17 range contract | Parser and conversions | Partial | Added resolve_range with explicit bounding |
| §6–7 essential writes/batches | Existing client and convenience service | Complete foundation | Reused; exposed restricted generic batch_update |
| §19 identity/stale-target preconditions | None | Missing | Added WritePrecondition and structured rejection |
| §20 formula overwrite policy | None | Missing | Added opt-in protection of reported formulas |
| §20 authorized dry-run | None | Missing | Added shared client/transport preflight and RequestPreview |
| §10–12, §20 outcomes/security/retry | Existing errors, grants, one-attempt writes | Partial | Kept execution; made success/validation outcomes explicit |
| §23–24 applicable P0 acceptance | Original 15 offline checks | Partial | Extended root verifier with P0 and compatibility checks |

### Resolved ranges and detailed cells

`resolve_range(worksheet, region, bounded=False)` returns ResolvedRange with
spreadsheet_id, numeric sheet_id, exact sheet_title, and CellRange. Unknown end
bounds remain None. `bounded=True` explicitly uses fresh worksheet dimensions;
invalid/out-of-grid ranges are rejected. ResolvedRange.a1 formats only bounded
ranges and otherwise raises a structured range error. It never invents grid size.
`get_merged_ranges` returns ResolvedRanges for each merge, including identity and
numeric start-inclusive/end-exclusive coordinates; it does not interpret headers.

`get_grid_data(worksheet, region, fields=..., pad=False)` requires a range that can
be bounded within the selected GRID worksheet. It resolves fresh dimensions and
enforces max_read_cells, then requests only that range and the selected CellData
fields. Default fields are userEnteredValue, effectiveValue, formattedValue,
userEnteredFormat, and effectiveFormat. Callers may choose any subset or dotted
subfields such as effectiveFormat.numberFormat. Unknown subfield names remain
Google-validated. Wildcards and arbitrary mask syntax are not accepted here.
DATA_SOURCE grid reads are rejected because Google can return their entire grid
despite a requested range; OBJECT sheets have no supported grid.

GridDataResult contains spreadsheet identity, WorksheetInfo, bounded region,
coordinate-ordered CellInfo entries, locale, timezone, and original APIResponse.
Each CellInfo retains row/column, sheet ID, requested_fields, entry_present, and
separate user_entered_value/effective_value/formatted_value and format dictionaries.
The formula, error, and number_format properties expose their respective available
fields without conflating calculated values with entered formulas. Number format
prefers effective formatting, falling back to entered formatting. Numeric date/time
serials remain numeric; no Python date conversion occurs.

Without padding, only reported cell entries are returned, each carrying physical
coordinates. Padding adds entries with entry_present=False for omitted positions.
An entry_present=True cell can still have absent fields; requested_fields tells
callers which fields were selected. None alone does not prove a cell is blank or
that an unrequested field does not exist. Explicit stringValue="" stays distinct
when exposed. Formats, formula errors, and raw cell fields may contain sensitive,
untrusted content and are excluded from representations, not stripped from the
explicit result properties. Rich text, chips, images, links, notes, validations,
and protection/filter metadata have no new typed P0 interface; unrelated properties
are not modified.

### Write safety and previews

Pass optional `safety=WriteSafety(...)` to update_range, update_ranges,
append_rows, clear_range, batch_update, and batch_structure. Value conveniences
update_cell/update_cells/update_row/update_rows, clear_cell/clear_row,
set_headers/rename_header forward the same keyword. Defaults and normal return
types remain unchanged. Existing later-phase worksheet/column conveniences are
preserved; they do not gain implicit protections. To validate a structural change
without applying it, use batch_structure or batch_update with explicit safety.

WriteSafety has preconditions (a tuple of WritePrecondition), protect_formulas
(default False), and dry_run (default False). Applicable write permissions are
checked by SheetsClient.validate_request before precondition/value reads. This
uses the same transport preflight as execution, including spreadsheet scope,
read/write API mode, operation grants, and structural/destructive restrictions.
No credential acquisition, token refresh, HTTP request, or quota reservation is
performed by client preflight itself. Service target resolution and precondition
checks perform authorized metadata/value/grid reads as needed.

WritePrecondition supports:

- worksheet: optional exact title/numeric ID, otherwise the value operation's tab.
- expected_sheet_id and expected_dimensions=(row_count, column_count): compare
  fresh physical metadata, never a database identity.
- region: an explicit range, defaulting to the single value-operation target.
  Multiple targets and raw structural batches require an explicit region for
  value/state checks; structural preconditions also need a worksheet selector.
- expected_values: a full rectangular matrix matching that region, compared
  against a padded read in read_mode (default UNFORMATTED_VALUE). None means no
  value was exposed at that position, not a proven empty-cell type. Numeric values
  are not stringified, and booleans do not compare equal to numeric 0/1.
- expected_formula: an exact formula string for one cell. Use read_mode=FORMULA
  with expected_values for multi-cell formula expectations.
- expected_empty: an observable one-cell state. True means a reported cell entry
  lacks both entered/effective value fields; False means value fields are present.
  If the cell entry is omitted, either expectation fails rather than guessing.

Every specified check must pass. Failed observations raise write_precondition_failed
with outcome=validation_rejection and submit no mutation. Invalid precondition
arguments have the existing service input/range errors. Conditions run before the
entire mutation/batch, not between individual requests inside a structural batch.
Explicit conditions may inspect another worksheet in the same spreadsheet through
normal authorized reads; they do not grant broader spreadsheet access.

protect_formulas checks reported userEnteredValue.formulaValue for cells the value
mutation actually touches (null skips are excluded). It also supports explicit
clear ranges. Its aggregate regions obey the read range/cell limits. A detected
formula raises formula_overwrite_rejected. This policy
does not alter input values, sanitize strings, rewrite formulas, or unmerge cells.
It is unsupported for logical-table append and arbitrary structural changes:
their physical overwrite targets cannot be established safely before execution.
Append may still use explicit identity/dimension/range preconditions, but a table
range check is not a promise about the final append row. Inspect the returned
updated range after actual execution.

With dry_run=True, no mutation is submitted. RequestPreview contains endpoint,
method, spreadsheet ID, target range (when applicable), copied body/query data,
and completed check names. Its outcome is validation_only, not confirmed_success.
Body and target content are excluded from repr; inspect them only in a trusted
review context. A preview is informational, cannot be executed as an authorization
capability, and reserves/locks nothing. A later ordinary write reruns preflight.

The generic batch_update accepts supported native Google request dictionaries;
it does not replace the existing payload-free convenience methods. Basic numeric
position/range checks and the client's request-type/grant checks apply. Structural
previews resolve referenced sheet IDs against fresh metadata (or explicitly
declared new IDs). Complete Google request-schema, merge, protected-range,
resource-limit, and concurrent-editor validation still belongs to Google. Dry-run
cannot guarantee that a later Google request will be accepted. Field masks must
explicitly select intended mutation properties; raw structural payloads remain
an administrative interface requiring explicit request-type grants.

### Outcomes and remaining limits

APIResponse.outcome is confirmed_success. APIClientError retains confirmed_failure,
validation_rejection, or uncertain as appropriate; SheetServiceError now includes
outcome and retryable=False. Service validation failures and stale-target/formula
rejections require caller correction or re-resolution, not automatic retry.
Credential/API errors retain their existing retryability metadata. The service
never automatically replays a mutation, including append, insert, delete, or move.
Success exposes Google's actual affected range/counts when available. A dry-run's
validation_only is a separate non-executed state, not a fifth remote outcome.

Value batches and structural batches retain their endpoint-specific contracts;
the service does not claim identical rollback semantics or transactions across
different calls. Null skips, explicit empty-string contents clearing, clear_values,
and deletion remain distinct operations. RAW remains the literal-safe default;
USER_ENTERED explicitly opts into Google's formula and locale parsing. Optional
post-write verification is a separate read, not a permanence guarantee.

Preconditions and formula checks are best-effort reads followed by writes. Other
clients can insert, sort, rename, or change cells in the gap. These checks are not
atomic compare-and-swap, isolation, oversell prevention, or stable row identity.
Missing/unobservable data can cause a conservative rejection. No header layout or
logical record is inferred, and no alternative target is guessed after a conflict.

No authentication or execution/retry algorithm was rewritten for P0. Cache
policies, distributed quota coordination, cancellation semantics, large-response
chunking, hidden/filter state, validation/protection inspection, and other P1/P2
utilities remain deferred; the pre-existing later-phase conveniences are retained.

### Manual disposable-worksheet verification

Run the offline verifier first:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python verify_sheet_service.py
```

The following manual command performs authorized reads and a preview only. It
does not create tabs or write cells. If gSheet_Test is absent it stops; never
substitute Sheet1. Browser consent, if needed, must be initiated explicitly first.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
from pathlib import Path
from gsheet import (GSheetConfig, CredentialManager, SheetsClient, SheetService,
                    AuthorizationPolicy, OperationKind, WritePrecondition, WriteSafety)

spreadsheet_id = '1h4d3qB90K2JZI5FbIAXRlQuc80WEoTPxh6t3I11FF9Y'
config = GSheetConfig(dotenv_path=Path('.env').absolute())
credentials = CredentialManager(config, connection_id='local-manual-test')
policy = AuthorizationPolicy({spreadsheet_id}, {OperationKind.READ, OperationKind.WRITE})
with SheetsClient(config, credentials, authorization=policy) as client:
    service = SheetService(client, spreadsheet_id, limits=config.operations)
    sheet = service.get_worksheet('gSheet_Test')
    region = service.resolve_range(sheet.sheet_id, 'A1:C3', bounded=True)
    current = service.read_range(sheet.sheet_id, region.region, read_mode='FORMULA', pad=True)
    detail = service.get_grid_data(sheet.sheet_id, 'A1:C3', pad=True)
    print('Read rows:', len(current.values), 'Grid cells:', len(detail.cells))
    conditions = (WritePrecondition(expected_sheet_id=sheet.sheet_id,
                    expected_dimensions=(sheet.row_count, sheet.column_count),
                    expected_values=current.values, read_mode='FORMULA'),)
    values = [['test', 'value', 'status'], ['gsheet', 1, 'OK'], ['oauth', 2, 'OK']]
    preview = service.update_range(sheet.sheet_id, 'A1:C3', values,
        safety=WriteSafety(preconditions=conditions, protect_formulas=True, dry_run=True))
    print(preview.outcome, preview.method, preview.checks)
    # Only after explicit approval, in a separate manual run:
    # result = service.update_range(sheet.sheet_id, 'A1:C3', values,
    #     safety=WriteSafety(preconditions=conditions, protect_formulas=True))
    # print(result.outcome, result.data.get('updatedRange'), result.data.get('updatedCells'))
    # service.read_range(sheet.sheet_id, 'A1:C3', pad=True)  # Separate readback.
PY
```

Live append and batch tests should likewise inspect actual updated ranges on this
disposable worksheet. Structural/destructive live tests need separate explicit
approval, disposable targets, and the corresponding exact policy grants. The
automated verifier uses only mocks and synthetic data; live P0 metadata masks and
consent/permission behavior still require manual integration confirmation.
