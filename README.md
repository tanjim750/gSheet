# gSheet

Generic Python Google Sheets service foundation. Configuration, authentication,
and the low-level Sheets API client are implemented. Repositories, high-level
services, and the facade remain unimplemented. Python 3.11+ is required.

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
  in-memory credentials require `reauthorize=True` for replacement. The local
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
fallback or manager retry loop. Corrupt, foreign, insecure, or scope-mismatched
token files fail closed; select a new connection-specific location to authorize
again without overwriting them. Reauthorization does not revoke older grants.

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
padding, dimensional checks, and high-level cell semantics belong to later layers.
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
limit, not a structural-request count. `max_read_cells` needs future range/grid
resolution and is not enforced by this raw endpoint layer.

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
the mutation. Known 4xx rejections remain confirmed failures. No blanket batch
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
