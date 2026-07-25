# Media processing architecture

Owner: Member C (media pipeline, AI and integrations).

This directory documents how media moves from an upload or a Google Drive
folder to searchable, timestamped moments.

## Documents

- [ingestion-pipeline.md](ingestion-pipeline.md) — the `IngestAssetWorkflow`
  stages, retry behavior and failure handling.
- [provider-contracts.md](provider-contracts.md) — the AI-provider and
  repository interfaces, and what each teammate owns on either side.
- [storage-conventions.md](storage-conventions.md) — R2 key layout, signed
  URLs and the direct-upload flow.

## Component map

```
apps/worker/worker/
├── config.py            Environment-sourced settings (no secrets in code)
├── logging.py           structlog JSON logging
├── main.py              Temporal worker entrypoint and dependency wiring
├── domain/              DTOs + enums mirroring the canonical Postgres schema
├── repositories/        Protocols Member B implements, + in-memory fakes
├── providers/           Transcription, video intelligence, classification, embeddings
├── storage/             R2 client and storage-key conventions
├── media/               FFprobe inspection, FFmpeg proxy/thumbnail/audio/clip
├── moments/             Candidate-moment segmentation + marketing taxonomy
├── activities/          Temporal activities (all I/O lives here)
└── workflows/           Temporal workflows (deterministic orchestration only)
```

## Design rules

1. **Workflows are deterministic.** No I/O, no vendor SDKs, no clocks or
   randomness in `workflows/`. Everything side-effecting is an activity.
2. **Providers are replaceable.** Activities depend on the Protocols in
   `providers/`, never on `openai`/`twelvelabs` directly. Swapping a vendor
   means writing one adapter.
3. **The database is the source of truth.** External provider indexes are
   caches; every moment, transcript and status lives in Postgres.
4. **Everything is idempotent.** Re-running ingestion must not duplicate
   assets, transcripts, moments or derivatives.
5. **Secrets stay server-side.** Provider credentials never reach the
   frontend; the browser only ever receives short-lived signed URLs.
