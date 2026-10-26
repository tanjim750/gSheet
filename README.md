# gSheet

Generic Python Google Sheets library, release **1.1.0**, implementing
the service requirements specification **v1.1**. Configuration, authentication,
the low-level Sheets API client, and the generic SheetService are implemented.
Repositories, the facade, and business-specific wrappers remain unimplemented.
Python 3.11+ is required.

## Installation and first use

Install from this checkout, or from an approved local wheel. This release has
not been published to a package registry:

```sh
python -m pip install .
# Optional explicit .env loading:
python -m pip install '.[dotenv]'
# Or install the locally built distribution:
python -m pip install /path/to/gsheet-1.1.0-py3-none-any.whl
```

The distribution/import name is `gsheet`. Runtime dependencies are
`google-auth[requests]>=2.38,<3` (including the requests HTTP transport) and
`google-auth-oauthlib>=1.2,<2`; the optional dotenv extra is
`python-dotenv>=1.0,<2`. Inspect the installed version with
`importlib.metadata.version("gsheet")`. Python 3.11 is the minimum; the release
audit records the interpreters and dependency versions actually exercised.
Source builds use setuptools 77 or newer for the declared SPDX license metadata;
installing a wheel does not require this build backend.

Enable the **Google Sheets API** in the credentials' Google Cloud project. For a
service account, explicitly share the spreadsheet with its email and grant the
appropriate Google file permissions. For OAuth, configure an installed/desktop
application client and consent access for the intended account. Local operation
grants, configured scopes, Google's consent grant and file permissions must all
permit the request. No Drive discovery endpoint is required for an explicit ID.

The following read example assumes a real credential file and an authorized
spreadsheet ID supplied by the trusted application:

```python
from pathlib import Path
from gsheet import (
    APIConfig, AuthConfig, AuthorizationPolicy, CredentialManager, GSheetConfig,
    OperationKind, SheetService, SheetsClient, WriteSafety,
)

spreadsheet_id = "YOUR_AUTHORIZED_SPREADSHEET_ID"
config = GSheetConfig(
    api=APIConfig(access_mode="read_only"),
    auth=AuthConfig(mode="service_account",
                    service_account_file=Path("/secure/service-account.json")),
    environ={},
)
credentials = CredentialManager(config, connection_id="application-connection")
policy = AuthorizationPolicy({spreadsheet_id}, {OperationKind.READ})
with SheetsClient(config, credentials, authorization=policy) as client:
    service = SheetService(client, spreadsheet_id, limits=config.operations)
    worksheets = service.list_worksheets()
    # Select an exact known title or numeric sheet ID; no default tab is assumed.
    cells = service.read_range("Data", "A1:C3", pad=True)
```

For writes using that service-account identity, deliberately construct read/write
configuration and grants. This example previews a RAW write; executing the same
method with `dry_run=False` submits it. Never treat a preview as a lock:

```python
write_config = GSheetConfig(api=APIConfig(access_mode="read_write"),
                           auth=config.auth, environ={})
write_credentials = CredentialManager(write_config,
                                     connection_id="application-connection")
write_policy = AuthorizationPolicy(
    {spreadsheet_id}, {OperationKind.READ, OperationKind.WRITE})
with SheetsClient(write_config, write_credentials,
                  authorization=write_policy) as client:
    service = SheetService(client, spreadsheet_id, limits=write_config.operations)
    preview = service.update_range("Data", "A1:B1", [["=literal text", 42]],
        safety=WriteSafety(protect_formulas=True, dry_run=True))
```

For OAuth instead, configure `AuthConfig(mode="oauth", oauth_client_file=...,
oauth_token_file=...)` with private, connection-specific storage as described
below. Only an explicit `credentials.authorize_interactively(allow_browser=True)`
starts consent. After scope changes use explicit `reauthorize=True`; a stored
read-only grant is not silently upgraded. A service-account key needs no browser.

For structured failures, catch `ConfigurationError`, `AuthenticationError`,
`APIClientError`, or `SheetServiceError` and inspect their safe `to_dict()` output.
An uncertain write must be reconciled with a separate read before any retry.
See [release notes](CHANGELOG.md), the [v1.1 specification](docs/gSheet_Service_Requirements.md)
and the [MIT license](LICENSE) ([OSI text](https://opensource.org/license/mit)).

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
- `get_credentials()` returns a sensitive Google-compatible object to the
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
ID, attempt count, elapsed seconds, `cache_hit`, and `cache_age_seconds`. Its representation excludes data. Treat
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
Unknown types fail closed even if named in the policy. These are raw API requests;
the service exposes only the high-level conveniences documented below.

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
credential refresh and backoff can extend total duration. Rolling-minute budgets
reserve both a connection slot and a process-wide project slot atomically. Each
attempt reserves a slot before credential acquisition; an authentication failure
may consume that slot. Exhaustion fails immediately with a structured throttle
error and retry delay. See the P1 operational contract below for project grouping
and multi-process limits. Disabling throttling does not change Google's quotas.

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

The dependency flow is business wrapper → SheetService → SheetsClient →
SheetsTransport → Google Sheets API. One coherent service uses focused range/value utilities and small
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
| Worksheets | `list_worksheets`, `get_worksheet`, `worksheet_exists`, `create_worksheet`, `rename_worksheet`, `delete_worksheet`, `duplicate_worksheet`, `copy_sheet` |
| Cells | `read_cell`, `get_cell`, `update_cell`, `clear_cell`, `update_cells`, `find_cells` |
| Ranges | `read_range`, `update_range`, `clear_range`, `read_ranges`, `update_ranges` |
| Rows | `read_row`, `get_row`, `read_rows`, `update_row`, `update_rows`, `append_rows`, `insert_rows`, `delete_rows`, `move_rows`, `clear_row`, `find_rows` |
| Headers | `read_headers`, `set_headers`, `find_columns`, `find_column`, `rename_header` |
| Columns | `read_column`, `get_column`, `add_columns`, `insert_columns`, `remove_columns`, `delete_columns`, `move_columns`, `reorder_columns` |
| Ordered dimension batches | `batch_structure` |
| P0 detailed reads and resolution | `resolve_range`, `get_grid_data`, `get_merged_ranges` |
| Restricted structural foundation | `batch_update` |
| P1 targeted metadata | `get_spreadsheet_properties`, `get_worksheet_metadata`, `get_dimensions`, `get_effective_cell`, `get_data_validations`, `get_protected_ranges` |
| P1 structure | `merge_cells`, `unmerge_cells`, `resize_sheet` |
| P1 bounded streaming | `iter_range_chunks`, `iter_grid_data_chunks` |
| P2 formatting/setup | `format_range`, `set_data_validation`, `set_column_width`, `set_row_height`, `freeze_rows` |
| P2 metadata | `get_named_ranges`, `get_developer_metadata`, `get_basic_filter`, `get_filter_views` |
| P2 range copying | `copy_paste_range` |

Data/structure methods select a worksheet, except creation and spreadsheet-level
batches. Named-range reads select the entire file; developer-metadata reads can
select a file, worksheet or bounded dimension region. Range copying explicitly
selects source and destination worksheets in the service's one spreadsheet.
Normal mutations return the existing
APIResponse, retaining actual updated ranges/counts and structural replies from
Google. Supported safety-enabled dry-runs return RequestPreview instead.
Creating/duplicating returns the API response containing the new sheet
properties; it does not hide an extra metadata read. `reorder_columns` returns
None when the requested order is already satisfied.

Metadata methods return APIResponse or WorksheetInfo as appropriate; listing
returns worksheet metadata in physical tab order. WorksheetInfo includes ID,
title, tab index, dimensions, merged CellRanges, and raw metadata (excluded from
repr). Metadata caching is opt-in on the client; default reads remain uncached.
Structural mutations bypass the cache to resolve current metadata before sending
numeric sheet IDs. All service requests still go through client
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
delete_columns, add_columns, move_rows, or move_columns, with worksheet, start/end, count,
destination, and inheritance arguments as relevant. batch_structure resolves
metadata once and validates changes in order, tracking dimensions after previous
changes. Callers express later indexes in that resulting grid. It submits one
structural API batch. Worksheet create/rename/delete/duplicate remain separate
methods. Trusted administrative native payloads can use the restricted
`batch_update` on SheetService or SheetsClient.

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

No authentication or execution/retry algorithm was rewritten for P0. P1 adds the
metadata, structural and operational contracts below. P2 extends those same paths
with the optional utilities documented in its own section.

## P1 structural and metadata contract

The v1.1 P1 scope is Sections 14 Phase 2, 21, 22 and 23 Phase 2. Clearing and
dimension insertion/deletion were already implemented; they are reused rather
than replaced. Existing `update_cell`/`update_row` conveniences are also retained.
The P1 manual tests and stabilization checks passed; the historical reports under
`reports/` record the original verifier callback issue and its closure. The final
release audit reruns the available offline checks. These reports are local audit
artifacts and are excluded from distributions.

### Targeted reads and result contracts

| Service method | Result and targeting |
|---|---|
| `get_spreadsheet_properties(fields=None, force_refresh=False)` | APIResponse with native `properties`; optional property paths such as `locale` and `timeZone` narrow the mask |
| `get_worksheet_metadata(worksheet, fields=..., force_refresh=False)` | WorksheetMetadata with spreadsheet/worksheet context, requested fields, native tab metadata, and APIResponse |
| `get_protected_ranges(worksheet, force_refresh=False)` | WorksheetMetadata targeted to `protectedRanges`; inspect `metadata.get("protectedRanges", [])` |
| `get_dimensions(worksheet, region=None, fields=..., force_refresh=False)` | DimensionsResult with grid counts and, for an explicit region, coordinate-aware row/column DimensionInfo entries |
| `get_effective_cell(worksheet, row, column, force_refresh=False)` | One padded CellInfo exposing entered formula/value, effective value, formatted string, formats, and error |
| `get_data_validations(worksheet, region, pad=False, force_refresh=False)` | Bounded GridDataResult; rules appear in `CellInfo.data_validation` and raw CellData |

`WorksheetMetadata.metadata` is the selected native Sheet object, with identity
properties always included. Its `worksheet` is the resolution context, not a
transactional snapshot of every requested property. Default generic tab fields
are properties, merges, protections, basicFilter and filterViews. Optional paths
can narrow these or request rowGroups/columnGroups. Native absent collection
fields are not fabricated in the raw payload. Protection editor details may be
omitted by Google; a read does not grant permission to edit or remove protections.

`get_dimensions` without a region requests only grid properties. Supplying a
bounded A1/CellRange requests rowMetadata and columnMetadata without cell values.
The default dimension fields are hiddenByUser, hiddenByFilter and pixelSize;
callers can narrow them using dot-separated native paths. Arrays are anchored at
Google's startRow/startColumn and returned as indexed tuples; omitted trailing
entries are not invented. DimensionInfo flag/size properties return None for
fields absent from that response. Inspect `requested_fields` and `metadata` to
distinguish a returned flag from absent/default-omitted information. The combined
requested row/column count is bounded by `max_read_cells`. Counts are available
for GRID and DATA_SOURCE sheets; bounded dimension/cell data reads require GRID
because DATA_SOURCE range restrictions can be ignored by Google. OBJECT sheets
have no grid. Generic spreadsheet/tab metadata remains available for other types.

`get_grid_data` also accepts the targeted `dataValidation` field, including nested
paths. Its default remains the same five P0 value/format fields. P2 adds rule
setting, filter read conveniences and scoped developer-metadata reads below.
Filter changes and developer-metadata search/mutation endpoints are not exposed.

Merged ranges retain zero-based, end-exclusive coordinates. Their top-left
`(start_row, start_column)` is the anchor; other cells are covered positions, not
additional copies of the anchor. `get_merged_ranges(force_refresh=True)` fetches
current merge metadata. No nested-header meaning is inferred. Sorted and filtered
worksheets are always read by physical grid coordinates. Hidden rows/columns
remain in those coordinates; reads do not compact or reconstruct a visual view.

### Structure and write safety

`merge_cells(worksheet, region, merge_type="MERGE_ALL", safety=None)` supports
MERGE_ALL, MERGE_ROWS and MERGE_COLUMNS. The target must resolve inside a GRID
worksheet and contain enough cells for the selected merge type. Existing merges
must be wholly contained if intersected. Merge can discard covered-cell values;
the caller must explicitly authorize WRITE, STRUCTURAL, DESTRUCTIVE and the
`mergeCells` request type. No data-preserving merge transformation is implied.

`unmerge_cells(worksheet, region, safety=None)` rejects partial intersection with
an existing merge; it never expands the target to encompass one. It uses the
existing WRITE + STRUCTURAL policy and `unmergeCells` allowlist entry. It does not
restore values previously discarded by a merge.

`resize_sheet(worksheet, rows=None, columns=None, safety=None)` requires at least
one positive count and changes only those gridProperties fields. It rejects
non-GRID mutation targets, a cut through an existing merge, or a requested count
smaller than the existing frozen prefix. Google validates remaining size/layout
constraints. Shrinking may permanently remove values, formulas and formatting;
even expansion requires the existing conservative WRITE + STRUCTURAL +
DESTRUCTIVE grants and an `updateSheetProperties` allowlist entry. It never
automatically unmerges, unfreezes or otherwise repairs the sheet.

`delete_columns` delegates to the existing `remove_columns`; both names remain
available. `insert_rows`, `delete_rows`, `insert_columns`, `remove_columns` and
`delete_columns` now accept `safety=WriteSafety(...)`, forwarding to the same
restricted batch path. Numeric intervals remain zero-based and end-exclusive.
`clear_range`/`clear_cell` retain existing value-only clear behavior and grants.

All these structural conveniences return APIResponse or a validation-only
RequestPreview. Structural preconditions must specify their worksheet explicitly,
just as for batch_structure/batch_update. Formula protection remains unsupported
for structural operations with unresolved effects and is rejected when requested;
it is never silently skipped. Explicit value/identity/dimension/formula
preconditions and dry-runs remain available. There is no locking or atomic
compare-and-swap. Structural effects can invalidate previously saved positions.

## P1 freshness and operational contract

### Cache and lifecycle

CacheConfig now controls a bounded, TTL-based metadata cache owned by each client.
It is disabled by default. Eligible spreadsheets.get responses have grid data
disabled and a mask that excludes GridData and wildcards. Cache keys include
spreadsheet ID, requested ranges and normalized query/field mask. Cached payloads
are copied on storage and retrieval. Cell/validation/dimension-array reads are
never cached. Authorization and closed-client validation run on every cache hit,
without obtaining credentials or consuming an HTTP quota slot.

`force_refresh=True` on spreadsheet, worksheet, merge, property or targeted
metadata reads bypasses eligible entries and replaces that key after a successful
fetch. CacheConfig.force_refresh globally bypasses entries. APIResponse reports
cache_hit and cache_age_seconds; cached HTTP status/request ID/attempts/latency
belong to the original fetch and do not imply a new Google request.
`SheetsClient.invalidate_metadata()` explicitly clears the cache. Any mutation
through that client clears all its entries, even after an uncertain outcome.
P0 coordinate resolution, value/grid reads, mutation target discovery and safety
checks always use fresh bounds. Other clients and direct external edits cannot
invalidate this local cache; TTL is a policy, not a freshness guarantee.

Reuse one CredentialManager per trusted connection and one or more clients with
that same identity/configuration. A SheetService borrows its client and has no
separate close operation. Clients serialize execute/cache access, own their HTTP
sessions, and can be reused across successive calls/services for the same trusted
connection. `close()` or a context manager waits for active execution, closes the
session and discards cached metadata; further requests fail closed. It does not
revoke Google authorization, reset the borrowed manager, or remove token files.
Existing demand-driven refresh and explicit-only OAuth consent remain unchanged.
After deliberately replacing/resetting a connection's credential identity, create
a new client before reuse so no metadata from the previous identity is retained.

### Bounds, project policy and large reads

`iter_range_chunks` and `iter_grid_data_chunks` lazily yield ordinary RangeValues
or GridDataResult tiles. Both require an explicit range (open ends are resolved
once from fresh dimensions). `chunk_cells=None` uses the lower of
read_chunk_cells (default 10,000) and max_read_cells. An explicit positive chunk
size must not exceed max_read_cells. Traversal is row-block then column-block,
splitting very wide ranges horizontally; combine by returned region coordinates,
not by concatenating values blindly. Each tile fetches fresh bounds and obeys
ordinary request/response limits; the total explicit range may exceed the single
request cell bound. Consume lazily to retain bounded memory, and stop iteration
to stop future calls. Exceptions preserve already yielded results; there is no
automatic whole-iterator restart. Concurrent insert/sort/edit/shrink may change
or invalidate later tiles. This is not a coherent snapshot or page-token API.

Value padding is applied independently to each tile. Google's
[ValueRange contract](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values#ValueRange)
omits trailing empty rows and columns. An unentered position returned as an
interior empty string in a larger read can therefore be omitted at a tile edge
and padded with None. Reconstructed chunks preserve positions and exposed value
types, but do not guarantee identical None/empty-string representations to one
large values read. Returned empty strings remain empty strings; no normalization
between None and empty strings is performed. Use detailed grid metadata when
additional empty/omitted distinctions are exposed by Google, without inferring
that every empty string represents an explicitly stored empty string.

OperationalConfig.max_response_bytes (default 10,485,760) bounds decompressed
response-body bytes before JSON decoding. A larger read raises
response_size_limit_exceeded without retrying; reduce cells or requested fields.
A successful-looking write with an oversized/unreadable response has an uncertain
outcome and is not replayed. Known HTTP 4xx rejections remain confirmed failures.
Cell limits do not predict byte size; a single large cell can exceed the byte
bound, and chunking does not automatically resize/retry a failed tile.

Request reservations are synchronized across clients in one Python process.
Separate rolling-minute READ/WRITE counters enforce per-connection limits
(existing *_per_user settings, keyed by the shared CredentialManager) and shared
per-project limits. Set OperationalConfig.quota_project_id to the same trusted
project grouping key and consistent limits for every connection using that Google
API project. This key does not select credentials, set billing headers, or detect
the project's identity. Without it, all unspecified clients share one conservative
application-default project pool. New clients with the same manager retain the
connection's history; a newly created manager starts a new connection history.
Multiple managers representing the same Google user still need application-side
coordination of the user's quota. Project counters remain after clients close
until their entries expire. Multiple processes/services need external admission
control; this implementation cannot coordinate quotas across them.

Reservations fail fast with locally_throttled and retry_after_seconds when either
budget is exhausted; blocked reservations consume neither slot. The lower bound
is effectively enforced by both checks, without queuing or unbounded retry loops.
The existing per-attempt logs retain local request IDs, hashed targets, operation,
latency, result category, HTTP status and retry attempt. Cached results retain the
fetch correlation ID; no synthetic network execution is logged for a cache hit.

### Compatibility, cancellation and deferred work

The API target is Google Sheets REST **v4**, with Python **3.11+** and declared
`google-auth[requests]>=2.38,<3`, `google-auth-oauthlib>=1.2,<2`; optional dotenv
remains `python-dotenv>=1.0,<2`. HTTP execution uses requests through the existing
auth dependency, not discovery-generated wrappers or downloaded files. Existing
public signatures remain valid; additions use optional keyword parameters and
defaulted result fields. Unknown native schemas/fields still require Google's
validation; missing metadata is not reconstructed. Future incompatible API or
dependency-major upgrades require explicit review and separate verification.

The synchronous API has no cooperative cancellation of an in-flight request.
Stopping a chunk iterator prevents future requests, not the active call. Timeouts
apply per HTTP connect/read operation, not as a total wall-clock deadline;
credential refresh, safe-read retries, body streaming and backoff can extend total
duration. Closing from another thread waits for the active client call. Interrupting
the caller or reaching a local timeout cannot cancel a remote write already
accepted by Google; inspect its effect before retrying. No automatic mutation
retry has been introduced.

Native shapes follow Google's [Sheet/GridData reference](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets)
and [batch request reference](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request).
Application byte/cell bounds are independent of Google's [quota policy](https://developers.google.com/workspace/sheets/api/limits).
P0/P1 verification results are recorded separately under `reports/`; P2 manual
verification and the final release audit are recorded there as well.

## P2 optional utilities

P2 covers Section 14 Phase 3 and the P2 methods in Section 21 of specification
v1.1. It adds generic cell/layout tools without interpreting headers or business
data. There are no new dependencies, authentication flows, endpoints, quotas or
cache configuration variables. All requests use the existing client/transport
and their authorization, error, response-size, quota and retry behavior.

### Coverage and compatibility

| Requirement | Existing support reused | P2 addition |
|---|---|---|
| Duplicate a tab | `duplicate_worksheet` / duplicateSheet | `copy_sheet`; both accept optional safety |
| Move columns | `move_columns` / moveDimension | Optional safety keyword; old positional calls remain valid |
| Move rows | Ordered dimension batch machinery | `move_rows`; `StructuralChange("move_rows", ...)` |
| Cell/row conveniences | `read_cell`, `read_row`, update helpers | `get_cell`, `get_row` forwarding aliases |
| Column reads and bounded search | Range reads and limits | `read_column` / `get_column`, `find_cells` |
| Formatting, validation, sizing, freezing | Restricted structural batch types | `format_range`, `set_data_validation`, `set_column_width`, `set_row_height`, `freeze_rows` |
| Named/developer metadata | Targeted spreadsheets.get and dimension metadata | `get_named_ranges`, `get_developer_metadata`; DeveloperMetadataResult |
| Basic filter / saved views | Generic tab metadata | `get_basic_filter`, `get_filter_views` |
| Copy selected cell aspects | copyPaste request type already allowed by registry | `copy_paste_range` with an explicit paste policy |

Pre-existing tab creation/rename/deletion, row/cell writing, column reordering,
clearing, metadata refresh and P0/P1 result contracts remain available. No facade,
repository layer, external spreadsheet engine or business wrapper is introduced.

### Reads, search and metadata

`get_cell(worksheet, row, column, read_mode=...)` forwards to read_cell and returns
the same scalar/None contract. `get_row` forwards to read_row with the existing
start_column/end_column, read_mode and pad keywords and returns a one-row
RangeValues matrix. `read_column` and `get_column` accept a numeric column,
start_row=0, end_row=None, read_mode and pad; they return a vertical RangeValues
matrix. Unknown end rows are resolved against fresh dimensions and read limits
apply. No additional value coercion or blank normalization is introduced.

`find_cells(worksheet, region, value, read_mode=...)` performs one read-limited,
padded physical-range read and returns a tuple of single-cell ResolvedRange
matches in row-major order. Matches have spreadsheet ID, numeric sheet ID, title,
numeric coordinates and A1 access. Matching is exact and case-sensitive for text;
booleans are distinct from 0/1, while equal numeric int/float values match. None
matches unavailable/padded positions in that response, not a guaranteed empty
cell. There is no regex, fuzzy search, full-file scan, header inference, or search
and replace. Callers must explicitly select the range.

`get_named_ranges(force_refresh=False)` returns an APIResponse with spreadsheetId
and the native namedRanges definitions if returned by Google. It does not enable
named-range strings in APIs that require A1/CellRange input.

`get_basic_filter(worksheet, force_refresh=False)` and
`get_filter_views(worksheet, force_refresh=False)` return WorksheetMetadata,
targeted respectively to basicFilter and filterViews. Missing fields stay absent
in native metadata; callers can use `.metadata.get("basicFilter")` or
`.metadata.get("filterViews", [])`. These methods inspect saved definitions and do
not apply a visual view, compact hidden rows, create filters, or sort data.

`get_developer_metadata(worksheet=None, region=None, force_refresh=False)` returns
DeveloperMetadataResult with spreadsheet ID, optional worksheet/region context,
a tuple of native metadata dictionaries, and the APIResponse. The selected native
collection determines scope:

- No worksheet: reads Spreadsheet.developerMetadata.
- Worksheet without region: reads Sheet.developerMetadata.
- Worksheet plus region: reads developerMetadata from the selected rowMetadata
  and columnMetadata through get_dimensions; repeated metadata IDs are deduplicated
  without changing entries. Conflicting payloads for one ID are rejected.

Each returned entry's native `location` remains authoritative. A bounded region
selects row/column metadata, not arbitrary permanent cell identifiers. No whole
grid scan or metadata search endpoint is used; metadata not exposed in the
selected collection is not invented. Region input requires a worksheet. Dimension
read limits and GRID restrictions apply as in P1. These metadata methods support
force-refresh; only eligible non-grid responses use the existing optional cache.
DeveloperMetadataResult.metadata and its response are excluded from repr.

### Formatting and setup mutations

`format_range(worksheet, region, formatting, fields=None, safety=None)` accepts a
native CellFormat object. Without fields, it derives a mask from the supplied
leaf properties, preserving unrelated formatting. Empty nested objects require
an explicit mask to state what should be reset. Explicit fields are a nonempty
list/tuple of relative CellFormat paths such as `textFormat.bold` or
`numberFormat`; they are always prefixed with userEnteredFormat. Wildcards and
cell-value masks are rejected. Explicit parent paths replace/reset that selected
format object according to Google field-mask semantics. An explicit mask can
reset a selected property omitted from formatting; no unspecified value/formula,
note, validation rule or other cell aspect is requested for modification.

`set_data_validation(worksheet, region, rule, filtered_rows_included=False,
safety=None)` requires a native DataValidationRule or an explicit None. None
clears rules only. Rules include a condition with native type/values and optional
inputMessage, strict and showCustomUi. Native condition-specific counts and schema
constraints are ultimately validated by Google. Dropdown lists use ONE_OF_LIST,
range dropdowns ONE_OF_RANGE, and custom formula rules CUSTOM_FORMULA; rule
formulas and references are an explicit caller-selected policy. Checkbox/BOOLEAN
rules can populate an unchecked FALSE value, as observed during P1 fixture setup.
Removing a rule does not imply clearing its cell values. Live verification also
observed Google accepting programmatic USER_ENTERED values that violated both a
strict list rule and a warning-only numeric rule. Stored validation is not a
separate service-side value-validation engine or a guarantee of API rejection.
Native CellFormat semantics are likewise Google-validated; for example, Google
accepted and returned a negative fontSize during verification. Include filtered rows
only with filtered_rows_included=True; this does not change the filter definition.

`set_column_width(worksheet, start, end, width, safety=None)` and
`set_row_height(worksheet, start, end, height, safety=None)` take zero-based,
end-exclusive dimension intervals and positive integer pixel sizes. They change
only pixelSize; hidden/filter state and other dimension properties are not
included in the mutation mask. `freeze_rows(worksheet, count, safety=None)` sets
only frozenRowCount; count is zero through the current row count, with zero
explicitly unfreezing. It never resizes the grid. Google validates remaining
native layout constraints. These setup mutations require GRID worksheets.

### Copies and moves

`copy_sheet(worksheet, title=None, safety=None)` duplicates within the current
spreadsheet by forwarding to duplicate_worksheet. The Google response retains
the new numeric tab ID/properties. Cross-file copying is not implied.

`move_rows(worksheet, start, end, destination, safety=None)` and move_columns
use Google's pre-removal destination convention. Intervals are zero-based and
end-exclusive; an in-source/no-op destination is rejected consistently with the
existing column-move contract. Ordered batch_structure accepts move_rows as well
as all pre-existing StructuralChange operations. Positions are not stable IDs.

`copy_paste_range(source_worksheet, source_region, destination_worksheet,
destination_region, paste_type=REQUIRED, paste_orientation="NORMAL", safety=None)`
copies only within this service's spreadsheet. The paste mode must explicitly be
one of PASTE_NORMAL, PASTE_VALUES, PASTE_FORMAT, PASTE_NO_BORDERS, PASTE_FORMULA,
PASTE_DATA_VALIDATION or PASTE_CONDITIONAL_FORMATTING. Orientation is NORMAL or
TRANSPOSE. Modes select native cell aspects; PASTE_NORMAL/PASTE_NO_BORDERS can also
copy formulas/merges. This is not a USER_ENTERED string-parsing API.

Both ranges resolve against fresh metadata and must be inside GRID sheets.
Partial intersections with existing merges are rejected. The destination height
and width must each be a positive multiple of the oriented source dimensions.
Exact matches copy once; explicit larger multiples use native repetition inside
the stated destination. Smaller or incompatible rectangles are rejected because
Google can otherwise expand a too-small destination beyond its stated end.
No implicit grid expansion, target adjustment or merge repair is performed.

### Authorization, preconditions and outcomes

| Mutation | Required batch request grant | Required operations beyond READ |
|---|---|---|
| format_range | repeatCell | WRITE, STRUCTURAL |
| set_data_validation | setDataValidation | WRITE, STRUCTURAL |
| set_column_width / set_row_height | updateDimensionProperties | WRITE, STRUCTURAL |
| freeze_rows | updateSheetProperties | WRITE, STRUCTURAL, DESTRUCTIVE (existing conservative policy) |
| copy_sheet / duplicate_worksheet | duplicateSheet | WRITE, STRUCTURAL |
| move_rows / move_columns | moveDimension | WRITE, STRUCTURAL, DESTRUCTIVE |
| copy_paste_range | copyPaste | WRITE, STRUCTURAL |

Fresh metadata reads require READ as with existing service mutations. No default
grants are broadened and the supported batch-type registry is unchanged. The
copyPaste grant explicitly permits overwriting destination aspects, while the
source is retained. Per-mode limits belong in trusted application tools; do not
expose unrestricted administrative grants to untrusted sheet content.

All P2 mutations accept WriteSafety and return APIResponse or RequestPreview.
Copies/moves/setup use the same precondition and validation-only machinery;
previews reserve nothing and are never executable authorization capabilities.
For structural/setup operations, preconditions require explicit worksheet
selectors and explicit regions when comparing values/formulas. Raw structural
formula protection remains unsupported and fails closed when requested.

Range copying can resolve its destination, so it additionally supports
protect_formulas=True: every destination position is conservatively treated as
potentially touched, even in format-only modes. A reported formula blocks the
copy. Destination preconditions can omit worksheet/region; source preconditions
must specify them. Formula-protection scans obey max_read_cells. No atomicity or
concurrent-editor isolation is claimed, and a successful response does not
substitute for separate optional read-back.

Eligible metadata caches invalidate on these mutations through the existing
client. Every mutation still receives one HTTP attempt; timeout/ambiguous results
remain uncertain and are never automatically replayed. Errors remain sanitized
and structured. Native Google schema/protection restrictions still apply.

P2 manual verification passed for the documented contract, including all seven
paste modes, moves, scoped metadata, exact search and same-file copies. The two
explicitly approved temporary copies were deleted by their returned IDs, and the
full spreadsheet baseline was restored. See `reports/P2_MANUAL_TEST_REPORT.md`
and its evidence for limitations and corrected utility expectations. The final
release audit uses offline checks; it does not repeat live mutations.

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
automated verifier uses only mocks and synthetic data. Prior live verification
covered the recorded OAuth account and GRID test worksheet; each deployment must
still confirm its own consent, file permissions and target identities.
