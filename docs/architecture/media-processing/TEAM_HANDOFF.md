# Handoff from Member C (media pipeline, AI and integrations)

What landed, what each of you needs from me, and what I need from each of you.

Branch: `claude/media-pipeline-integrations-5b7075`

---

## What works today

Run this from the repository root — no Docker, Temporal, cloud credentials or
API keys required:

```bash
apps/worker/.venv/Scripts/python scripts/dev/demo_pipeline.py
```

It runs Member B's real API in-process and drives it with the worker's real
code: upload → FFprobe metadata → proxy/thumbnail keys → transcript →
segmentation → moments → `ready` → natural-language search → idempotent re-run.

```bash
cd apps/worker && .venv/Scripts/python -m pytest -q     # 35 passed, 6 skipped
python -m pytest -q                                      # Member B's 10 still pass
```

---

## Member A — frontend

### You can build against these now

**Playback.** `GET /api/v1/assets/{id}/playback-url` returns a working URL once
the worker reports a proxy. Start my media server first:

```bash
cd apps/worker && .venv/Scripts/python -m worker.media_server
```

It serves on `127.0.0.1:8001` with HTTP Range support, so `<video>` seeking
works. Set `MEDIAFLOW_MEDIA_BASE_URL=http://127.0.0.1:8001` in the API's `.env`.
Before a proxy exists the endpoint returns **409** — please render "still
processing" rather than an error state.

**Processing stages.** The worker reports these `stage` values through the
processing job, in this order. Use them for the status UI; they are already
user-safe (no vendor names), but the display strings are yours to choose:

| `stage` | Suggested label | `progress` |
|---|---|---|
| `queued` | Queued | 0 |
| `preparing_file` | Preparing file | 15 |
| `generating_preview` | Generating preview | 35 |
| `transcribing_speech` | Transcribing speech | 55 |
| `understanding_video` | Understanding video | 70 |
| `identifying_moments` | Identifying useful moments | 85 |
| `preparing_search` | Preparing search | 95 |
| `ready` | Ready | 100 |
| `failed` | Failed | 100 |

Poll `GET /api/v1/assets/{id}/processing-job` while `status` is `processing`.

**Errors.** `error_message` on a failed asset is prefixed with a machine code
in brackets, e.g. `[unsupported_media] Unsupported file type '.mkv'.` Codes you
will see: `unsupported_media`, `corrupted_source`, `duplicate_asset`,
`video_indexing_failed`, `ingestion_failed`. Feel free to strip the bracketed
prefix for display and branch on it for retry affordances — only `failed`
assets accept `POST /api/v1/assets/{id}/retry`.

**Moments.** Each has `category` from a controlled vocabulary, so you can use
fixed colors/icons: `hook`, `testimonial`, `education`, `demonstration`,
`objection`, `offer`, `call_to_action`, `behind_the_scenes`, `social_proof`,
`brand_story`, `general_b_roll`, `other`. `score` is 0–100.

### What I need from you

1. **Upload part size.** My presigned multipart flow needs a part count up
   front. Tell me the chunk size your uploader uses (I suggest 8 MiB) so the
   two agree.
2. **Thumbnail sizes.** I generate one main thumbnail at 1280px wide and 5
   previews at 640px. If the grid wants a different size, say so now — it is
   one ffmpeg flag, but regenerating later means reprocessing.
3. **Do you want a filmstrip?** I can emit a contact sheet for scrubbing
   previews. Not built; only worth it if the player will use it.

---

## Member B — backend

### What I am sending you

I persist through your three internal endpoints and they work as documented.
`worker/repositories/http_api.py` is the client;
`worker/repositories/api_mapping.py` is the translation layer.

**Moment ids are deterministic** — `uuid5` over
`(asset_id, start_ms, end_ms, moment_type)`. That is what makes reprocessing
upsert instead of duplicate against your upsert-by-id endpoint. Please do not
reassign moment ids server-side; it would break idempotency and orphan
collection items.

### Schema gaps — worker output with nowhere to go

These are produced today and logged instead of stored. Each is a small
migration on your side; I need no code changes beyond removing a fallback.
Ordered by how much they cost us:

1. **`assets.checksum_sha256`** (+ unique index on
   `(organization_id, checksum_sha256)`). Without it
   `find_by_checksum` always returns `None`, so **duplicate detection is
   effectively off** — spec §4.3 and §6.5 both require it. I already compute
   the checksum on every ingestion.
2. **`assets.provider_asset_id`**. Twelve Labs returns an id when it finishes
   indexing. Without storing it we cannot run provider search against an
   indexed asset later, which blocks the multimodal half of Phase 3 search
   (MVP1-043).
3. **Moment taxonomy columns** — `topics`, `pain_points`, `benefits`,
   `funnel_stages`, `people_labels`, `product_labels`, `keywords` (all
   `TEXT[]`), plus `visual_description` / `marketing_description`. Today all of
   this collapses into a single `category` string. The classifier already
   produces the full set, and search filters (MVP1-045) need them.
4. **`embedding VECTOR`** on moments and transcript segments. I generate
   embeddings (default 1536 dims, `text-embedding-3-small`) and currently drop
   them. **Confirm the dimension before the column is created** — it must
   match `OPENAI_EMBEDDING_DIMENSIONS` exactly or inserts fail.
5. **`analysis_version`** on transcripts and moments. Without it, reprocessing
   replaces rather than versioning, so we cannot A/B a pipeline change or roll
   one back.

### Questions I need answered

1. **Transport.** Internal HTTP endpoints (what I use now) or direct
   repository classes sharing a DB session? The Protocols support both; HTTP
   is working, so this is only worth changing if you want the worker in-process.
2. **Who starts the workflow?** Right now nothing does. On
   `POST /uploads/{id}/complete` you should signal the worker. Simplest for
   MVP: a `POST /api/v1/internal/workflows/ingest` endpoint I poll, or you call
   the Temporal client directly. I'd rather you decide than guess.
3. **Clip exports.** `ExportClipWorkflow` is **built and tested** — it
   validates the range, renders an accurate cut from the proxy, uploads to
   `orgs/{org}/clips/{export_id}/clip.mp4` and reports status. What is missing
   on your side:
   - a `clip_exports` table (`id`, `organization_id`, `asset_id`,
     `source_moment_id`, `start_ms`, `end_ms`, `status`, `output_storage_key`,
     `output_mime_type`, `error_message`, `requested_by`)
   - a user-facing request endpoint (`POST /api/v1/clip-exports`) and a status
     read endpoint
   - internal endpoints for me to mark an export `ready` or `failed`

   Until those exist the workflow still renders and uploads correctly; only
   the status write degrades to a log line.

4. **Google Drive connections.** The connector is **built and tested** —
   OAuth with refresh, folder discovery, incremental listing, streaming
   download, and duplicate detection. What is missing on your side is a
   `source_connections` table (spec §9.2) with at least:
   - `encrypted_credentials JSONB` — I hand you
     `{refresh_token, access_token, expires_at_epoch}`; **the refresh token is
     a long-lived credential and must be encrypted at rest and never returned
     through any public endpoint**
   - `sync_cursor TEXT` — I return an RFC 3339 timestamp; passing it back on
     the next sync is what makes it incremental
   - `status` + an error message, so a revoked grant surfaces in the UI as
     "reconnect" rather than silently failing forever

   Assets from Drive also need `source_connection_id` and
   `source_external_id`, with a unique index on the pair — that plus the
   checksum column is what prevents re-importing the same Drive file.

### One security note

The internal token grants unscoped access to every asset — the endpoints take
no `organization_id` and do no membership check. That is correct for a trusted
worker, but it means the token must never reach a browser or a log. Worth a
second pair of eyes before pilot (spec §6.6).

---

## Shared — nobody owns this yet

- **Nothing starts the ingestion workflow.** The pipeline is complete and
  tested, but no code path triggers it after an upload completes. See Member B
  question 2. This is the single biggest gap to a working demo.
- **`.env.example`** currently documents only the API's variables. The
  worker's full set is in `apps/worker/README.md`; merging them into the root
  file needs coordination since it is a shared file (spec §21).
- **CI.** No workflow runs any of our three test suites.

---

## Member A — Google Drive UI (Phase 4)

The connector is ready; the flow you build against is:

1. Your settings page asks the API for an authorization URL. The API calls
   `GoogleDriveOAuth.authorization_url(state=...)` — **`state` must be an
   unguessable, single-use value the callback verifies**, otherwise the
   callback is open to CSRF.
2. The user consents on Google and lands on the callback endpoint, which
   exchanges the code and stores the connection.
3. Your folder picker calls a listing endpoint backed by
   `DriveClient.list_folders(parent_id=...)` — it returns `{id, name}` pairs
   and supports drilling down from `"root"`.
4. The user picks folders; sync runs on demand or every 15 minutes.

Sync outcomes you should surface per file: `new_file`, `source_modified`
(ingested) and `already_imported`, `duplicate_content`, `unsupported_format`,
`empty_file` (skipped). A connection whose grant was revoked comes back with
status `error` — that needs a "Reconnect" affordance, not a retry button.

Note the scope is **read-only**: we never modify or delete a customer's Drive
files. Worth saying so in the consent screen copy.

---

## What I am doing next

Nothing in my lane is blocked on me any more — Phases 1 through 5 are built
and tested. What remains is integration work that needs your side first:

1. Wiring the export workflow to real `clip_exports` rows (needs Member B).
2. Wiring Drive sync to real `source_connections` rows (needs Member B).
3. End-to-end run against real Temporal + MinIO + FFmpeg in Docker, which I
   cannot execute in this sandbox (no loopback sockets, no FFmpeg binary).
   **Someone should run `docker compose -f
   infrastructure/docker/media/docker-compose.media.yml up --build` on a real
   machine and report back** — that is the one unverified layer.
