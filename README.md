# Mediaflow demo backend

This is the fastest local backend slice for the demo: an organization-scoped API
that creates a video asset, accepts Team C's processing updates, and returns its
current status. It deliberately uses SQLite and the `X-User-Id` demo header;
replace them with Supabase Auth/Postgres/RLS after the demo.

## Run

```sh
cp .env.example .env
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/uvicorn app.main:app --reload --port 3000
```

OpenAPI is available at `http://127.0.0.1:3000/docs`. The web client should use
`http://localhost:3000/api/v1`; CORS is enabled for Vite on port 5173.

For the Team A frontend, set `VITE_DEMO_MODE=false` and sign in with:

```
alex@northstar.studio
mediaflow-demo
```

`POST /api/v1/auth/login` returns a 15-minute Bearer token. The existing
`X-User-Id: demo-user` header remains available for Team C's local worker demo.

## Search and collections contract

With the Bearer token, search the seeded library using:

```json
POST /api/v1/search
{ "query": "easy onboarding" }
```

The response contains `search_id` and timestamped `results`, each with
`asset_id`, `moment_id`, `start_ms`, `end_ms`, `excerpt`, `match_reasons`, and
`score`. Collections use `GET`/`POST /api/v1/collections`; create with a
`name` (and optional `description`), then add a search result with
`POST /api/v1/collections/{collection_id}/items` and `{ "moment_id": "..." }`.

## Team C contract

1. Create an asset before beginning work:

```sh
curl -X POST http://127.0.0.1:3000/api/v1/uploads/initiate \
  -H 'Content-Type: application/json' -H 'X-User-Id: demo-user' \
  -d '{"organization_id":"demo-org","original_filename":"sample.mp4","media_type":"video"}'
```

2. Report FFprobe/proxy progress using the returned `asset_id`:

```sh
curl -X PATCH http://127.0.0.1:3000/api/v1/internal/assets/ASSET_ID/processing \
  -H 'Content-Type: application/json' -H 'X-Internal-Token: change-me-before-sharing' \
  -d '{"stage":"ffprobe","status":"processing","progress":50,"duration_ms":42000,"width":1920,"height":1080}'
```

Allowed worker statuses are `queued`, `processing`, `completed`, and `failed`.
Set `error_message` when reporting `failed`. The status read endpoint is:

```sh
curl http://127.0.0.1:3000/api/v1/assets/ASSET_ID -H 'X-User-Id: demo-user'
```

When Team C has generated derivatives, include their storage object keys in the
same worker update:

```json
{ "stage": "proxy", "status": "completed", "progress": 100,
  "proxy_key": "proxies/ASSET_ID.mp4", "thumbnail_key": "thumbnails/ASSET_ID.jpg" }
```

Set `MEDIAFLOW_MEDIA_BASE_URL` to Team C's local server or media CDN. The
frontend can then obtain the proxy through `GET /api/v1/assets/{asset_id}/playback-url`.
Failed assets can be returned to Team C's queue with `POST /api/v1/assets/{asset_id}/retry`.

## Transcript and moment handoff

After transcription and moment generation, Team C replaces the canonical
search data through these internal-token endpoints:

```text
PUT /api/v1/internal/assets/{asset_id}/transcript
PUT /api/v1/internal/assets/{asset_id}/moments
```

Transcript payloads contain `segments` with `start_ms`, `end_ms`, optional
`speaker`, and `text`. Moment payloads contain `moments` with a stable `id`,
`title`, timestamps, `category`, and score from 0 to 100. Repeating an
identical payload is idempotent; the persisted transcript and moments become
available through the public asset and search endpoints immediately.

After the browser has uploaded directly to storage, it calls
`POST /api/v1/uploads/{upload_id}/complete` with optional `byte_size` and
`checksum_sha256` (a 64-character SHA-256 digest). This moves the asset from
`uploading` to `processing`. A checksum already present in the same
organization returns `409` with code `duplicate_asset`.

Team C can now claim completed uploads with the internal token:

```json
POST /api/v1/internal/workflows/ingest
{ "worker_id": "media-worker-1", "limit": 1 }
```

The response returns `jobs` containing the asset, processing job, organization,
and original upload metadata. Claiming moves each job to
`preparing_file`/`processing` at 15%; a repeat claim will not receive it again.
`/api/v1/internal/workflows/ingest/claim` is also available as an alias.

When known, include `checksum_sha256` and `provider_asset_id` in the existing
worker processing `PATCH`; both are persisted on the public asset record. Team
C then reports `completed` or `failed` through that endpoint. The public asset
status is `ready` when processing completes. A user can cancel before
completion through `POST /api/v1/uploads/{upload_id}/abort`.

## Verification

```sh
.venv/bin/python -m pytest
```
