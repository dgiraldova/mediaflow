# MediaFlow purpose gallery

MediaFlow stores uploaded media locally, prepares playback proxies and
thumbnails, and makes AI-produced transcripts and moments searchable. Local
development uses SQLite plus `var/media`; Cloudflare R2 remains available as a
later deployment option but is not required.

## Local setup

```sh
cp .env.example .env
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pip install -e 'apps/worker[dev]'
corepack pnpm install
```

Install FFmpeg/FFprobe system-wide or download workspace-local binaries without
sudo:

```sh
corepack pnpm tools:ffmpeg
```

Then run these three processes from the repository root:

```sh
corepack pnpm dev:api
corepack pnpm dev:worker
VITE_DEMO_MODE=false corepack pnpm dev:web
```

Web: `http://127.0.0.1:5173` · API docs: `http://127.0.0.1:3000/docs`

Sign in with:

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

## Upload contract

The web client performs the complete local-storage flow:

```text
POST /api/v1/uploads/initiate
PUT  /api/v1/uploads/{upload_id}/content
POST /api/v1/uploads/{upload_id}/complete
```

The `PUT` streams bytes to an atomic temporary file under `var/media`; complete
verifies the stored size and SHA-256 before queuing the asset. The worker claims
the job immediately, reads the same local file, then writes its proxy and
thumbnail back into `var/media`. Aborting an incomplete upload removes its
stored bytes.

## Worker contract

Report processing progress using the returned `asset_id`:

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

The API serves local originals and generated derivatives through
`/api/v1/media/{storage_key}` with HTTP Range support. The frontend receives
durable `preview_url`, `thumbnail_url`, and `playback_url` fields from asset
responses, so previews survive a browser refresh.
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

After the browser has streamed bytes into local storage, it calls
`POST /api/v1/uploads/{upload_id}/complete`. This moves the asset from
`uploading` to `processing`. An identical SHA-256 already present in the same
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
corepack pnpm check
PYTHONPATH=apps/worker .venv/bin/python -m pytest
```
