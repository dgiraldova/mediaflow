# MediaFlow media worker

Temporal worker for media processing, AI analysis and source integrations.
Owner: **Member C**.

Architecture documentation lives in
[`docs/architecture/media-processing/`](../../docs/architecture/media-processing/).

## Local development

### Local disk (current default)

The API and worker share `var/media`; neither MinIO nor Cloudflare is needed.
From the repository root:

```bash
WORKER_STORAGE_BACKEND=local \
MEDIAFLOW_API_INTERNAL_TOKEN=change-me-before-sharing \
PYTHONPATH=apps/worker .venv/bin/python -m worker.poller
```

The poller automatically claims each completed upload. FFmpeg and FFprobe must
be installed on the host for real video processing. From the repository root,
`corepack pnpm tools:ffmpeg` installs workspace-local binaries without sudo;
the root `dev:worker` command automatically adds them to `PATH`.

### Cloudflare R2 later

Set `WORKER_STORAGE_BACKEND=r2` and the `R2_*` variables. The same worker
pipeline then uses the existing S3-compatible adapter.

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
| `R2_` | Optional Cloudflare R2 endpoint, credentials, bucket, signed-URL TTLs |
| `TRANSCRIPTION_` | Transcription provider endpoint, key and model |
| `TWELVE_LABS_` | Twelve Labs API key and index id |
| `OPENAI_` | Classification and embedding models |
| `GOOGLE_DRIVE_` | OAuth client, redirect URI, credential encryption key |

**Provider keys are optional.** When one is absent, the worker wires a `Null`
adapter for that provider, so the full pipeline runs offline without spending
API credits. When `TRANSCRIPTION_API_KEY` is blank, the worker reuses
`OPENAI_API_KEY` for transcription. Production deployments must configure the
providers they intend to use.

## Running the full demo

Three processes. From the repository root:

**1. API**

```bash
MEDIAFLOW_INTERNAL_WORKER_TOKEN=dev-token .venv/bin/python -m uvicorn app.main:app --port 3000
```

**2. The ingestion poller** — claims queued uploads and processes them

```bash
PYTHONPATH=apps/worker MEDIAFLOW_API_BASE_URL=http://127.0.0.1:3000 MEDIAFLOW_API_INTERNAL_TOKEN=dev-token .venv/bin/python -m worker.poller
```

Upload through the UI and the asset moves `processing` → `ready` on its own,
with transcript, moments, search results and a playback URL.

### `--simulate`

Without it the poller reads the uploaded local file and runs real FFprobe,
FFmpeg and configured AI providers — that needs FFmpeg on
`PATH`. With it, **no media is processed**: metadata, transcripts and moments
are synthetic placeholders so the UI flow can be demonstrated before storage
exists. It logs a warning on every start; never run it in production.

Use `--simulate` only for UI demonstrations.

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

The API serves the worker's proxies and thumbnails directly from the shared
local storage root. The standalone media server remains available for an R2
deployment.

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
