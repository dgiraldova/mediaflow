# Ingestion pipeline

`IngestAssetWorkflow` turns one uploaded or discovered file into a ready,
searchable asset. It is started by the API once an upload is verified
(spec section 11.1, step 9).

## Stages

| # | Activity | User-visible stage | Notes |
|---|---|---|---|
| 1 | `validate_asset` | Queued | Format/size check. No retry on rejection. |
| 2 | `acquire_source_file` | Preparing file | Downloads from R2 (or Drive) to local temp disk. |
| 3 | `calculate_checksum` | Preparing file | SHA-256; detects duplicates within the organization. |
| 4 | `inspect_media` | Preparing file | FFprobe: duration, dimensions, orientation, audio presence. |
| 5 | `store_original_if_required` | Preparing file | Idempotent; skips upload if the object already exists. |
| 6 | `generate_video_proxy` | Generating preview | H.264/AAC MP4, ≤1280 wide, ≤30 fps, faststart. |
| 7 | `generate_thumbnail` | Generating preview | Main thumbnail + evenly spaced previews. |
| 8 | `extract_audio` | Transcribing speech | Mono 16 kHz WAV. Skipped when there is no audio track. |
| 9 | `transcribe_audio` | Transcribing speech | Timestamped segments, persisted idempotently. |
| 10 | `index_with_video_provider` | Understanding video | Submits the proxy, polls until indexed. |
| 11 | `detect_candidate_moments` | Identifying useful moments | Pure function over transcript segments. |
| 12 | `classify_moments` | Identifying useful moments | Structured outputs, validated against the taxonomy. |
| 13 | `generate_text_embeddings` | Preparing search | Batched embeddings for vector search. |
| 14 | `persist_search_documents` | Preparing search | Upsert on the moment uniqueness constraint. |
| 15 | `finalize_asset` | Ready | Marks asset ready and the job complete. |
| 16 | `cleanup_temporary_files` | Ready | Removes the local temp directory. |

Stage labels come from `FRIENDLY_STAGE_LABELS` in `worker/domain/enums.py`.
Vendor names are never exposed to users (spec 11.4).

## Branches

- **No audio track.** Steps 8–9 and 11–14 are skipped. The asset still gets a
  proxy, thumbnails and provider indexing, and still reaches `ready` — it is
  findable visually, just not by speech.
- **Duplicate checksum.** The workflow stops right after step 3 and marks the
  asset failed with `duplicate_asset`, before any expensive encoding or AI
  spend.

## Retry policy

| Failure | Behavior |
|---|---|
| Network / storage | Exponential backoff, max 5 attempts |
| AI-provider rate limit | Exponential backoff with jitter, max 5 attempts |
| Invalid media | No retry — `unsupported_media`, non-retryable |
| Corrupted source | No retry — `corrupted_source`, non-retryable |
| Worker termination | Temporal resumes the workflow on another worker |

Temporal's SDK applies randomized jitter to computed retry intervals
automatically, so the AI-provider policy satisfies "backoff with jitter"
without extra configuration.

## Failure handling

Any unhandled failure runs the compensation path:

1. `mark_asset_failed` — asset → `failed`, processing job → `failed`, with an
   error code and message that are safe to show the user.
2. `delete_partial_derivatives` — removes every R2 object written so far, so a
   failed run never leaves orphans behind.
3. `release_temporary_resources` — removes the local temp directory.

The original uploaded media is never deleted automatically.

## Heartbeats

FFmpeg encode/extract activities heartbeat from FFmpeg's `-progress` output,
and provider polling and per-moment classification heartbeat between calls.
`heartbeat_timeout` is set only on those activities — setting it on an
activity that never heartbeats would cause Temporal to time it out while it
is still working correctly.
