# Hosted data model

The twelve tables behind hosted mode (`SPLITSMITH_MODE=hosted`), as declared
in `src/splitsmith/db/models.py`. Tests and the local hosted variant run the
same schema on SQLite; desktop `splitsmith ui` keeps its state on disk under
`~/.splitsmith/` and does not touch these tables.
The entity blocks below are generated from the SQLAlchemy metadata by
`scripts/gen_er_diagram.py`; the notes after them are written by hand.

<!-- BEGIN GENERATED: scripts/gen_er_diagram.py -->
```mermaid
erDiagram
    magic_link_tokens {
        varchar id PK
        varchar email
        varchar token_hash UK
        datetime created_at
        datetime expires_at
        datetime consumed_at
    }
    users {
        varchar id PK
        varchar email UK
        datetime email_verified_at
        varchar display_name
        datetime created_at
        varchar stripe_customer_id UK
        varchar entitlement
        datetime entitlement_until
        varchar external_auth_id
        varchar external_auth_provider
        datetime deleted_at
        json scoreboard_identity
    }
    workers {
        varchar id PK
        varchar name UK
        varchar kind
        boolean enabled
        integer priority
        varchar registration_token_hash UK
        datetime token_expires_at
        datetime registered_at
        varchar worker_token_hash UK
        datetime last_seen_at
        datetime last_wake_at
        varchar version
        json info
        datetime created_at
    }
    compute_jobs {
        varchar id PK
        varchar user_id FK
        varchar kind
        varchar status
        integer stage_number
        varchar shooter_slug
        varchar video_id
        float progress
        varchar message
        varchar error
        boolean cancel_requested
        boolean acknowledged
        json result
        json timings
        json args
        varchar match_id
        datetime created_at
        datetime updated_at
        datetime started_at
        datetime finished_at
    }
    desktop_tokens {
        varchar id PK
        varchar user_id FK
        varchar name
        varchar token_hash UK
        varchar scope
        datetime created_at
        datetime last_used_at
        datetime revoked_at
    }
    device_authorizations {
        varchar id PK
        varchar device_code_hash UK
        varchar user_code UK
        varchar device_name
        varchar scope
        varchar status
        varchar user_id FK
        datetime created_at
        datetime expires_at
        datetime last_polled_at
    }
    match_comments {
        varchar id PK
        varchar user_id FK
        varchar match_id
        varchar slug
        integer stage_number
        float anchor_t
        varchar anchor_kind
        varchar anchor_shot_id
        varchar author_kind
        varchar author_user_id FK
        varchar author_handle
        varchar author_key_hash
        varchar author_code
        varchar share_token_id
        varchar body
        datetime created_at
        datetime deleted_at
    }
    matches {
        varchar id PK
        varchar user_id FK
        varchar match_id
        varchar name
        varchar storage_prefix
        varchar origin
        datetime created_at
        datetime updated_at
    }
    recent_projects {
        varchar id PK
        varchar user_id FK
        varchar path
        varchar name
        varchar kind
        varchar match_id
        datetime last_opened_at
    }
    sessions {
        varchar id PK
        varchar token_hash UK
        varchar user_id FK
        datetime created_at
        datetime last_used_at
        datetime expires_at
        varchar user_agent
        varchar ip
    }
    share_tokens {
        varchar id PK
        varchar user_id FK
        varchar match_id
        varchar token UK
        varchar scope
        datetime created_at
        datetime revoked_at
        datetime expires_at
    }
    state_docs {
        varchar id PK
        varchar user_id FK
        varchar match_id
        varchar doc_kind
        varchar slug
        integer stage_number
        json doc
        integer version
        datetime created_at
        datetime updated_at
    }
    users ||--o{ compute_jobs : "user_id"
    users ||--o{ desktop_tokens : "user_id"
    users o|--o{ device_authorizations : "user_id"
    users ||--o{ match_comments : "user_id"
    users o|--o{ match_comments : "author_user_id"
    users ||--o{ matches : "user_id"
    users ||--o{ recent_projects : "user_id"
    users ||--o{ sessions : "user_id"
    users ||--o{ share_tokens : "user_id"
    users ||--o{ state_docs : "user_id"
```
<!-- END GENERATED -->

## What the foreign keys do not say

Only `user_id` is a database-level foreign key. The other joins are logical
and keyed on `(user_id, match_id)`:

```mermaid
erDiagram
    matches ||..o{ state_docs : "user_id, match_id"
    matches ||..o{ share_tokens : "user_id, match_id"
    matches ||..o{ match_comments : "user_id, match_id"
    matches ||..o{ compute_jobs : "user_id, match_id"
    matches o|..o{ recent_projects : "user_id, match_id (null for desktop paths)"
    share_tokens o|..o{ match_comments : "share_token_id"
```

- `matches` is unique on `(user_id, match_id)`; `match_id` alone is the
  scoreboard/registry id and repeats across users.
- `state_docs` holds every per-match JSON document, discriminated by
  `doc_kind` (`match`, `project`, `audit`, `export_runs`, ...) plus `slug`
  and `stage_number` for per-shooter and per-stage kinds. Adding a
  `doc_kind` is not a local change: see "State doc kinds and the sync
  allowlist" in `CLAUDE.md` before adding one.
- The delete cascades are hand-written queries, not `ON DELETE`:
  `delete_match` filters on `match_id` alone and `delete_shooter` on
  `(match_id, slug)`, so both sweep new per-shooter kinds without edits.
- `match_comments.author_handle` is server-derived; the request model has
  no field for it. `author_user_id` is set for signed-in commenters.
  `author_key_hash` is a client-minted key so an anonymous commenter can
  delete their own comment; it is convenience, not a security boundary.
  `share_token_id` is the moderation primitive: every comment that came
  through one link is one query.
- `workers` has no `user_id`: compute targets belong to the deployment, not
  a tenant. The `kind='railway'` row is seeded from env vars; self-hosted
  agents register through `POST /api/workers/register`.
- Column types are shown as SQLAlchemy reports them (`varchar`, `json`,
  `datetime`). `state_docs.doc` is JSONB on Postgres (generic JSON
  elsewhere so SQLite tests work); the other `json` columns are read and
  written whole and stay generic JSON on every backend.

Regenerate with `uv run --frozen python scripts/gen_er_diagram.py`; pass
`--check` to verify the block is current.
