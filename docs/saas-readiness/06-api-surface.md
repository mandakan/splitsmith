# 06 -- API surface

This doc defines the **HTTP API contract** between the browser /
desktop client and the Splitsmith backend. It covers versioning,
auth headers, error shape, observability, and the deprecation
policy.

It does NOT enumerate every endpoint -- those are documented by
FastAPI's auto-generated OpenAPI schema. This doc is the conventions
that every endpoint follows.

## URL structure

```
https://splitsmith.app/api/v1/<resource>/<id>/<sub-resource>
```

Locked-in conventions:

- **`/api/v1/` from day one.** Even if v1 is the only version, the
  prefix is there. Breaking changes ship as `/api/v2/` with a
  deprecation window (see "Deprecation policy" below).
- **Resources are plural nouns.** `/projects`, `/uploads`, `/jobs`.
- **IDs are ULIDs in path segments.** `/projects/01HX.../jobs/01HX...`.
- **Sub-resources for things that don't exist standalone.**
  `/projects/<id>/stages/<n>/shots` -- a shot list doesn't make
  sense without its stage and project.
- **No verbs in URLs.** Actions go through HTTP methods + dedicated
  endpoints (`POST /projects/<id>/jobs` to start a detection job;
  `DELETE /projects/<id>/jobs/<jid>` to cancel).

The local-mode server uses the same routes. A local request to
`http://127.0.0.1:8765/api/v1/projects/<id>` returns the same
shape as the hosted equivalent. The desktop client and the web SPA
share the same API client code.

## Versioning policy

- **`/api/v1/` is stable.** Once shipped, no breaking changes.
  Additive-only: new optional fields, new endpoints, new query
  parameters with defaults that preserve existing behaviour.
- **Breaking changes ship as `/api/v2/`.** Both versions live in
  parallel for at least 6 months after `/api/v2/` ships (the
  "deprecation window").
- **The "shape" of a v1 response can never change.** Renaming a
  field is breaking. Tightening a type (string -> enum) is breaking.
  Removing a field is breaking. Adding a field is fine.
- **Status codes are part of the contract.** 200 vs 201 vs 204
  matter; we don't change them mid-version.

Every response carries a `X-Splitsmith-API-Version: v1` header for
explicit confirmation.

## Authentication

Two acceptable mechanisms:

1. **Browser:** httpOnly session cookie (set by the magic-link
   callback). CSRF-protected via `X-CSRF-Token` header, fetched from
   `GET /api/v1/auth/csrf`.
2. **Desktop / CLI:** `Authorization: Bearer <access_token>` header.
   Access tokens are short-lived (1h); refresh via `POST /api/v1/
   auth/refresh` with a refresh token from the desktop link flow
   (see 02).

Local mode requires neither -- `LoopbackAuth` resolves to the local
user regardless. Local-mode clients still send the headers if they
have them (e.g. the desktop linked to a hosted account); the local
server ignores them.

Anonymous endpoints (no auth required):

- `GET /api/v1/healthz`
- `POST /api/v1/auth/begin` (start magic link)
- `GET /api/v1/auth/callback` (magic-link verify; sets cookie)
- `POST /api/v1/billing/webhooks/stripe` (validated by Stripe
  signature, not by user auth)

Every other endpoint returns 401 if unauth'd. No body, just `WWW-
Authenticate: Bearer realm="splitsmith"`.

## Request and response shape

### Request bodies

JSON only. Content-Type `application/json; charset=utf-8`. Reject
anything else with 415. Exception: tus uploads and webhook payloads,
which have their own content types.

### Response bodies

JSON only, same content type. Empty bodies for 204 No Content (which
we use for successful PUTs and DELETEs that have nothing to return).

Top-level shape for resource responses:

```json
{
  "data": { ... },           // the resource object, or array
  "meta": { ... }            // optional pagination, timing, etc.
}
```

Top-level shape for error responses:

```json
{
  "error": {
    "code": "project_not_found",       // stable string, snake_case
    "message": "Project 01HX... not found.",
    "details": { ... }                  // optional, structured
  },
  "trace_id": "..."                    // for support correspondence
}
```

The `code` is the stable contract; the `message` is human-readable
and may change. Clients that branch on errors must branch on `code`.

Error codes are documented per-endpoint in the OpenAPI schema.

### Pagination

List endpoints default to 50 items, max 200, paginated via cursor:

```json
{
  "data": [...],
  "meta": {
    "cursor": {
      "next": "opaque-string-or-null",
      "prev": "opaque-string-or-null"
    }
  }
}
```

Cursors are opaque base64 strings; clients must not parse them. The
server may invalidate old cursors during data migrations.

## Idempotency

Endpoints that have side effects accept an optional `Idempotency-
Key` header. The key is a client-generated ULID. The server caches
the response for the key for 24h; replays with the same key return
the cached response (200 if it succeeded, original error if it
failed).

This makes "the user clicked submit twice" a non-issue and lets the
desktop sync flow safely retry network failures.

Mandatory idempotency:

- `POST /projects/<id>/jobs` -- detection jobs are expensive; double-
  submission would charge the user twice.
- `POST /uploads` -- starting two upload sessions for the same data
  wastes storage.
- `POST /billing/checkout` -- spinning up two Stripe Checkout
  sessions confuses the user.

Optional but recommended for everything else.

## Rate limiting

Per-user, applied at the API gateway:

- Anonymous endpoints: 60 req/min per IP.
- Authenticated GET: 600 req/min.
- Authenticated POST/PUT/DELETE: 60 req/min.
- `POST /uploads`: 10 req/min (each upload session is heavy).

Limits return 429 with `Retry-After` header. We don't need fancier
than this in v1; if the picked deploy target (Fly / Railway) doesn't
provide gateway-level limits, we wire up `slowapi` or similar.

Rate limits do NOT apply to local mode (the loopback user can do
anything as fast as they want).

## Error semantics

Status codes follow standard usage:

- **200 OK** -- success with body.
- **201 Created** -- POST that created a new resource. Body =
  resource representation.
- **204 No Content** -- success with no body (PUTs, DELETEs, idempotent
  no-ops).
- **400 Bad Request** -- the request is malformed (bad JSON, missing
  required fields). Specific to the request.
- **401 Unauthorized** -- not authenticated.
- **403 Forbidden** -- authenticated but not allowed (ACL fail).
- **404 Not Found** -- resource doesn't exist OR the user can't see
  it (we don't leak existence). Distinguish only via `code` if
  needed for UX.
- **409 Conflict** -- write conflict (concurrent edit) or duplicate
  (slug already taken).
- **413 Payload Too Large** -- upload exceeds 50 GB cap.
- **415 Unsupported Media Type** -- non-JSON to a JSON endpoint.
- **422 Unprocessable Entity** -- semantic validation fail (e.g.
  uploaded file's sha256 doesn't match the declared one).
- **429 Too Many Requests** -- rate limited.
- **500 Internal Server Error** -- bug. Logged to Sentry; user
  sees a generic message + trace ID.
- **503 Service Unavailable** -- worker pool saturated, deploy in
  progress, etc. `Retry-After` set.

5xx responses always include a `trace_id`. Support requests carry
this ID; we look up the Sentry event by it.

## Observability

### Tracing

Each request gets a unique `trace_id` (ULID), set as a response
header (`X-Trace-Id`) and logged with every log line for the
request. Sentry events carry the same ID.

If the request comes in with a `traceparent` header (W3C trace
context), we honour it -- this lets the desktop client trace
through to the server and back. We don't ship a full distributed
tracing setup in v1; just the header passthrough.

### Metrics

Provider-built-in for v1:

- Fly.io / Railway dashboards for request rate, latency, error
  rate.
- Sentry for error tracking + performance traces (optional).

We don't run our own Prometheus / Grafana in v1. If we outgrow
provider dashboards, revisit.

### Logging

Structured logs (JSON), one line per event, going to stdout. Fields:

- `ts`, `level`, `msg`
- `trace_id`, `user_id`, `project_id` (when applicable)
- `route`, `method`, `status`, `duration_ms`

PII never in logs (no emails, no IP unless explicitly needed for
abuse detection).

## OpenAPI schema

FastAPI auto-generates `/openapi.json`. The schema includes:

- Every route, request schema, response schema.
- Auth requirements per route.
- Error responses per route with their `code` strings.
- Examples for every request/response.

We commit to keeping the schema accurate. The frontend's TypeScript
types are generated from this schema (via `openapi-typescript` or
similar) so divergence breaks the build.

## Deprecation policy

When `/api/v2/` ships:

1. `/api/v2/` goes live alongside `/api/v1/`. Both are fully
   supported.
2. `/api/v1/` responses gain a `Sunset` header (RFC 8594) with the
   sunset date (>= 6 months out).
3. `/api/v1/` responses gain a `Deprecation: true` header.
4. The frontend is migrated to `/api/v2/` first.
5. Email goes to all users with active desktop links pointing them
   at the new desktop release that uses `/api/v2/`.
6. After 6 months, `/api/v1/` returns 410 Gone with a body
   explaining the sunset and pointing at upgrade docs.

We do not ship `/api/v3/` until `/api/v1/` is gone.

## Local-mode specifics

The local server defaults to `http://127.0.0.1:8765`. Same routes,
same auth-header tolerance, no rate limits, no Sentry sampling.
CORS allows the dev origin (localhost:3000 / localhost:5173) by
default.

The local server exposes one extra endpoint not in the hosted
version:

- `GET /api/v1/local/projects-dir` -- returns the absolute path of
  the FilesystemStorage root. The desktop UI uses this to render
  "Open in Finder" links. Hosted mode returns 404 for this route.

Endpoints that don't make sense in local mode (billing, magic-link
flows, upload sessions to R2) return 404 with `code:
"not_supported_in_local_mode"`.

## Open questions

- **gRPC for the desktop?** The desktop client could speak gRPC for
  smaller payloads + bidi streaming on long-running jobs. Rejected
  for v1 -- doubles the API surface and JSON-over-HTTP works fine
  for our scale. Revisit if streaming becomes a real need.
- **Server-Sent Events vs polling.** The jobs drawer could subscribe
  to job updates via SSE instead of polling. Probably worth doing
  in v1 -- one endpoint (`GET /api/v1/jobs/stream`), trivial to
  implement with Starlette. Track in 09.
- **Webhooks for users.** Letting users subscribe their own webhooks
  ("notify me when detection is done") is v2+.
- **API tokens for users.** A premium user might want a personal
  access token to script Splitsmith. v2. The desktop link
  mechanism is enough for v1.
