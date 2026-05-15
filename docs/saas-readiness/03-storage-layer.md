# 03 -- Storage layer

This doc defines the **`Storage` abstraction** that lets the same
project code read/write to a local filesystem in local mode and to
Cloudflare R2 (S3-compatible) in hosted mode. It also pins the
**JSON-as-canonical** invariant from doc 00, principle 6, and
explains how the Postgres index relates to those JSON files.

## The `Storage` abstraction

Backed by [`fsspec`](https://filesystem-spec.readthedocs.io/) -- one
API across local FS, S3, R2, GCS, Azure Blob. We don't reimplement
this layer; we wrap fsspec with a small typed surface that hides the
fsspec-specific quirks our callers shouldn't care about.

```python
class Storage(Protocol):
    """
    Project-scoped object storage. All paths are relative to the
    storage root (e.g. 'projects/<id>/match.json').
    """

    async def read_bytes(self, path: str) -> bytes: ...
    async def write_bytes(self, path: str, data: bytes) -> None: ...

    async def read_json(self, path: str, model: type[T]) -> T: ...
    async def write_json(self, path: str, value: BaseModel) -> None: ...

    async def open_stream(
        self, path: str, mode: Literal['rb', 'wb']
    ) -> AsyncIterator[bytes]: ...

    async def stat(self, path: str) -> StorageObject | None:
        """Return None if the path doesn't exist."""

    async def list(self, prefix: str) -> AsyncIterator[StorageObject]: ...

    async def delete(self, path: str) -> None: ...

    async def signed_url(
        self,
        path: str,
        method: Literal['GET', 'PUT'],
        expires: timedelta,
    ) -> str:
        """
        Hosted mode: presigned S3 URL. Local mode: a 'file://' URL
        valid for the same duration through the local server's
        signed-redirect handler.
        """
```

`StorageObject` carries `path`, `size`, `etag`, `last_modified` --
the union of fsspec metadata fields we use.

### Implementations

- **`FilesystemStorage`** -- local mode. Wraps fsspec's `LocalFileSystem`.
  Storage root = `~/.splitsmith/projects/` by default. `signed_url`
  for `'GET'` returns a `file:///` URL the local server's
  `/api/files/signed/<token>` redirects to (token-bound, 1-hour
  expiry, in-memory map -- no DB needed).
- **`S3Storage`** -- hosted mode. Wraps fsspec's `S3FileSystem`
  pointing at R2. Bucket = `SPLITSMITH_BUCKET` env var. Region =
  `auto` (R2 doesn't care). `signed_url` uses R2's S3-compatible
  presign API.
- **`MemoryStorage`** -- test-only. fsspec's `MemoryFileSystem`. For
  unit tests that exercise project lifecycle without touching disk.

We do NOT add a custom HTTP-streaming abstraction. If a caller wants
to stream a large file, they call `open_stream` and iterate; the
underlying fsspec implementation handles range requests on S3 and
buffered file IO locally.

## Cloudflare R2 vs AWS S3

R2 wins for our workload:

- **$0 egress.** Splitsmith serves audio + (eventually) raw video to
  the browser. Egress is the dominant cost on S3 for media. R2
  charges $0 to egress to anyone, anywhere.
- **S3-compatible API.** fsspec talks to R2 via `S3FileSystem` with
  custom endpoint URL. No code changes vs S3.
- **Storage cost.** ~$0.015/GB-month, comparable to S3 IA tier.
- **Class A operations** (PUT, COPY, LIST) cost $4.50/million; Class
  B (GET) $0.36/million. Both are cheaper than S3 standard.

R2 loses on:

- **Lifecycle policies.** Less mature than S3. We don't need much
  lifecycle (we hold project data indefinitely until the user
  deletes); the simple "expire incomplete multipart uploads after
  7 days" rule is supported.
- **Cross-region replication.** Not relevant for v1 -- we're a
  single-region deploy.
- **Vendor lock-in.** Mitigated by fsspec -- swapping to S3 is one
  config change.

We pick R2 for v1, with S3 as the documented escape hatch.

## Storage layout

### Per-project layout (the canonical record)

```
projects/<project_id>/
  match.json
  shooters/
    <shooter_slug>/
      project.json              # MatchProject record
      audio.wav                 # extracted audio (Tier 2 input)
      shots.csv                 # detected shots
      shots.fcpxml              # FCPXML export
      audits/
        <iso_timestamp>.json    # detection audit history
      stage_<n>/
        trim_<shooter>.mp4      # per-stage trims (Tier 1 + 3 only)
  raw/                          # opt-in; only present if user enabled raw upload
    headcam.mp4
  exports/
    <iso_timestamp>.fcpxml
```

This layout is **identical between local mode and hosted mode**. A
local-mode project rsync'd to an R2 bucket produces a working
hosted-mode project (modulo the database index, which gets rebuilt --
see "Index rebuild" below).

Path conventions:

- `<project_id>` -- ULID, opaque, never the display name.
- `<shooter_slug>` -- slugified shooter display name; collision-free
  within a project. Stable once assigned.
- `<iso_timestamp>` -- `YYYYMMDDTHHMMSSZ`, no fractional seconds.

### Why JSON files instead of a database for project state

The principle is in doc 00, derived #6. Restating concretely:

1. **Local mode has no database.** If we put detection results in
   Postgres, local mode either spins up SQLite (more complexity, more
   bugs, harder to ship single-binary) or local mode diverges from
   hosted (worst-case rewrite to add hosted later).
2. **Export is trivial.** "Email this project to a friend" is `tar -
   czf project.tar.gz projects/<id>/` in either mode.
3. **The detection audit trail is the source of truth.** We've
   committed in the project guidance to "optimise for the audit
   trail". JSON files are the audit trail. The DB indexing those
   files is rebuildable.
4. **We don't query detection results across projects.** Postgres
   would shine for "find every shot under 0.18s across all my
   matches". v1 doesn't need that. v2 might want it -- when it does,
   it's an indexer over the JSON, not a primary store.

### What IS in Postgres

Only data that:

- Crosses projects, OR
- Is needed to authorise access before reading any JSON file, OR
- Is mutated frequently in ways that don't fit a write-then-read
  JSON model.

Concretely:

- `users`, `sessions`, `desktop_links` (see 02)
- `projects` -- one row per project, holds `owner_user_id`,
  `storage_path`, `display_name`. Indexes the JSON, doesn't own it.
- `project_members` -- ACL (see 02)
- `upload_sessions` -- in-flight tus uploads (see 05)
- `compute_jobs` -- in-flight + recent detection jobs, for the
  jobs drawer (see 04 + 06)
- `billing_events` -- Stripe webhook log (see 08)

That's the entire schema for v1. ~7 tables. Detection results, shot
times, audit history, shooter projects -- all JSON in storage.

## Index rebuild

If the database is lost or a project is imported from a tarball, the
projects table can be rebuilt by walking the storage prefix:

```python
async def reindex_project(project_id: str) -> None:
    storage = get_storage()
    match = await storage.read_json(
        f'projects/{project_id}/match.json', MatchRecord
    )
    # ...upsert into projects table
```

This makes the JSON files genuinely the source of truth: lose the
database and we lose user accounts + ACLs (bad), but we don't lose
any match data. The reindex is part of the import flow (see
07-sync-and-migration.md).

## Concurrency and conflicts

In local mode there's one writer (the local server, single user).
We rely on local filesystem semantics: write-temp-then-rename for
atomicity. No locking needed.

In hosted mode there can in principle be multiple writers (the user
on two browser tabs, a worker finishing a detection job, the desktop
syncing). v1 mitigates this with:

- **One-writer-per-project at a time.** The first request to a
  project takes a Postgres advisory lock; concurrent writes 409.
  This is coarse but adequate for a single-user-per-project v1.
- **Conditional writes for JSON updates.** Every JSON update reads
  current ETag, modifies, writes with `If-Match`. Conflict =>
  reload + retry once, then surface to the user. This is a
  belt-and-braces check on top of the advisory lock.
- **No partial writes.** All JSON updates are full-file rewrites.
  Append patterns (e.g. audit log) are full reads + full writes
  rather than appends, so there's no torn-write window.

v2 (squad sharing) likely needs proper optimistic concurrency control
on per-stage edits. Defer until then.

## Storage paths in the data model

The `projects.storage_path` column holds the **prefix** under which
the project's files live. In hosted mode this is `s3://splitsmith-
prod/projects/<id>/`. In local mode -- if the local app ever writes
to a Postgres index, which v1 doesn't -- it would be
`file:///Users/.../projects/<id>/`.

The prefix model means a project can move between buckets (e.g. a
data residency request, or a user moving to a self-hosted deployment)
by updating one row + copying objects. The fsspec implementation
behind `Storage` happily handles a per-project bucket choice -- we
just don't expose the configurability in v1.

## What stays out of storage

These do NOT live in the storage layer:

- **Auth tokens.** Postgres-only. Never written to JSON or storage
  paths.
- **Stripe customer IDs / webhook payloads.** Postgres-only.
- **Computed in-memory caches** (e.g. the loaded ONNX model). Live
  in the process, not in storage.
- **Logs.** Stdout in v1; Sentry for errors. We don't write logs to
  R2.

## Open questions

- **Lifecycle for orphaned files.** If a user uploads audio but
  abandons the upload, the bytes sit in R2 forever. We should run a
  weekly "delete files referenced by no project" job. Probably
  trivial; track in 09.
- **Per-project encryption.** R2 encrypts at rest by default. If a
  user wants client-side encryption (their key, our blind storage)
  that's a v3+ feature. v1 trusts R2's server-side encryption.
- **Virus scanning on raw upload.** Probably overkill for video
  files -- the format is fixed and we don't execute anything from
  storage. Skip in v1; revisit if we ever accept user-uploaded
  binaries that could end up on the user's machine.
- **Per-tenant prefixes vs flat.** We currently flat-prefix
  `projects/<id>/`. We could go `users/<owner_id>/projects/<id>/`
  to make per-user deletion (GDPR) easier. Probably worth doing
  even in v1 -- decide during implementation.
