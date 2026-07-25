# MediaFlow media worker

Temporal worker for media processing, AI analysis and source integrations.
Owner: **Member C**.

Architecture documentation lives in
[`docs/architecture/media-processing/`](../../docs/architecture/media-processing/).

## Local development

### With Docker (recommended)

Brings up Temporal, MinIO (local R2) and the worker with FFmpeg preinstalled:

```bash
docker compose -f infrastructure/docker/media/docker-compose.media.yml up --build
```

Temporal Web UI: <http://localhost:8233> · MinIO console: <http://localhost:9001>

### Without Docker

Requires Python 3.12+ and FFmpeg on `PATH`.

```bash
cd apps/worker && uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
```

Then start Temporal and MinIO from the compose file above and run:

```bash
python -m worker.main
```

## Checks

```bash
cd apps/worker && .venv/bin/python -m pytest -q
```

```bash
cd apps/worker && .venv/bin/python -m ruff check worker && .venv/bin/python -m mypy worker
```

## Test media

Fixtures are generated, not committed:

```bash
python scripts/fixtures/media/generate_fixtures.py
```

## Configuration

All settings come from environment variables — see `.env.example` at the
repository root. No secret is ever read from a file in source control.

| Prefix | Purpose |
|---|---|
| `WORKER_` | Runtime: log level, temp dir, analysis version, upload limits |
| `TEMPORAL_` | Temporal host, namespace, task queue, TLS/API key |
| `R2_` | Cloudflare R2 endpoint, credentials, bucket, signed-URL TTLs |
| `TRANSCRIPTION_` | Transcription provider endpoint, key and model |
| `TWELVE_LABS_` | Twelve Labs API key and index id |
| `OPENAI_` | Classification and embedding models |
| `GOOGLE_DRIVE_` | OAuth client, redirect URI, credential encryption key |

**Provider keys are optional.** When one is absent, the worker wires a `Null`
adapter for that provider, so the full pipeline runs offline without spending
API credits. Production deployments must set them all.

## Running the full demo

Three processes. From the repository root:

**1. Member B's API**

```bash
MEDIAFLOW_INTERNAL_WORKER_TOKEN=dev-token MEDIAFLOW_MEDIA_BASE_URL=http://127.0.0.1:8001 python -m uvicorn app.main:create_app --factory --port 3000
```

**2. The ingestion poller** — claims queued uploads and processes them

```bash
cd apps/worker && MEDIAFLOW_API_BASE_URL=http://127.0.0.1:3000 MEDIAFLOW_API_INTERNAL_TOKEN=dev-token .venv/Scripts/python -m worker.poller --simulate
```

**3. The media server** — only needed for actual video playback

```bash
cd apps/worker && .venv/Scripts/python -m worker.media_server
```

Upload through the UI and the asset moves `processing` → `ready` on its own,
with transcript, moments, search results and a playback URL.

### `--simulate`

Without it the poller downloads the uploaded file from R2/MinIO and runs real
FFprobe, FFmpeg and AI providers — that needs storage configured and FFmpeg on
`PATH`. With it, **no media is processed**: metadata, transcripts and moments
are synthetic placeholders so the UI flow can be demonstrated before storage
exists. It logs a warning on every start; never run it in production.

Drop `--simulate` and set the `R2_*` variables once MinIO is running
(`docker compose -f infrastructure/docker/media/docker-compose.media.yml up`).

## Integration with Member B's API

The worker claims work from and persists through the internal endpoints in
`app/main.py`:

```text
POST  /api/v1/internal/workflows/ingest               claim queued jobs
PATCH /api/v1/internal/assets/{asset_id}/processing   status, metadata, checksum,
                                                      provider id, derivative keys
PUT   /api/v1/internal/assets/{asset_id}/transcript   transcript segments
PUT   /api/v1/internal/assets/{asset_id}/moments      searchable moments
```

Duplicate detection is server-side: the worker sends the SHA-256 it computed,
and the API returns 409 if another asset in the organization already has it.
The poller turns that into a `duplicate_asset` failure the user can see.

`worker/repositories/http_api.py` implements the repository Protocols against
them, and `worker/repositories/api_mapping.py` handles the schema differences
(the API's flat `category`/`score` moments versus the worker's full taxonomy).
Set `MEDIAFLOW_API_INTERNAL_TOKEN` to enable it; without it the worker falls
back to in-memory repositories.

Moment ids are derived deterministically from `(asset_id, start_ms, end_ms,
moment_type)`, which is what makes reprocessing idempotent against the API's
upsert-by-id endpoint.

### Media delivery

Member B builds playback URLs as `MEDIAFLOW_MEDIA_BASE_URL/{proxy_key}`. In
local development that is this worker's media server:

```bash
cd apps/worker && .venv/Scripts/python -m worker.media_server
```

It streams proxies and thumbnails from R2/MinIO on `127.0.0.1:8001` with HTTP
Range support so the player can seek, and refuses to serve original media.

### End-to-end demo

Runs Member B's API in-process and drives it with the real worker pipeline —
no Docker, Temporal, cloud credentials or API keys required:

```bash
apps/worker/.venv/Scripts/python scripts/dev/demo_pipeline.py
```

## Status

| Area | State |
|---|---|
| Worker foundation, config, logging | Done |
| R2 client, storage keys, presigned uploads | Done |
| FFprobe inspection, proxy, thumbnails, audio, clip extraction | Done |
| Provider interfaces + Twelve Labs / OpenAI adapters | Done |
| Moment segmentation + marketing taxonomy | Done |
| `IngestAssetWorkflow` + activities + compensation | Done |
| Integration with Member B's API (verified by tests) | Done |
| Media-delivery server for playback | Done |
| `ExportClipWorkflow` + accurate-cut rendering | Done |
| Google Drive OAuth, discovery, sync, download | Done |
| Drive sync wired to real connection rows | Blocked on `source_connections` table |
| Clip exports wired to real export rows | Blocked on `clip_exports` table |
| Verified against real Temporal + MinIO + FFmpeg | Not yet — needs a machine with Docker |

### Known gaps in the current API schema

These worker outputs have nowhere to go yet and are logged instead of stored:

- **Checksums** — no column, so cross-asset deduplication cannot run.
- **`provider_asset_id`** — Twelve Labs ids are not persisted, so provider
  search cannot be re-run against an indexed asset later.
- **Full moment taxonomy** — `topics`, `pain_points`, `benefits`,
  `funnel_stages`, and embeddings collapse to one `category`.
- **`analysis_version`** — reprocessing replaces rather than versioning.

## Tests

`tests/test_ingest_workflow.py` starts Temporal's bundled test server, which
needs a loopback socket that sandboxed environments block. Those tests are
skipped unless `RUN_TEMPORAL_TESTS=1` is set. Everything else runs in seconds.
