# Provider and repository contracts

Two boundaries keep the worker decoupled: **AI providers** (owned by Member C)
and **repositories** (implemented by Member B).

## AI-provider interfaces

Defined in `apps/worker/worker/providers/`, matching spec section 13.

| Protocol | Method | Adapter |
|---|---|---|
| `TranscriptionProvider` | `transcribe(audio_path, language_hint)` | `OpenAICompatibleTranscriptionProvider` |
| `VideoIntelligenceProvider` | `index_asset`, `get_indexing_status`, `search` | `TwelveLabsProvider` |
| `StructuredClassificationProvider` | `classify_moment(input)` | `OpenAIClassificationProvider` |
| `EmbeddingProvider` | `embed_texts(texts)` | `OpenAIEmbeddingProvider` |

Every protocol also has a `Null*` adapter. When the corresponding API key is
absent, `worker/main.py` wires the Null adapter instead, so the whole pipeline
runs offline in local development without spending API credits.

### Rules

- Activities depend on the Protocol, never on a vendor SDK.
- All model output is validated before persistence. `MomentClassification` is
  a Pydantic model, and taxonomy values outside the controlled vocabulary are
  stripped even though structured outputs already constrain the schema.
- Provider search results are filtered to the caller's own asset ids before
  being returned, so a provider-side mistake cannot leak another
  organization's segments.

## Repository interfaces

Defined in `apps/worker/worker/repositories/interfaces.py`. These are the
"interface-first pull request" from spec section 22 — Member C codes against
them now, Member B implements them against the real schema.

| Protocol | Used by |
|---|---|
| `AssetRepository` | metadata, storage keys, provider id, status transitions |
| `ProcessingJobRepository` | progress reporting, completion, failure |
| `TranscriptRepository` | idempotent transcript persistence |
| `MomentRepository` | idempotent moment upsert |
| `SourceConnectionRepository` | Google Drive credentials and sync cursor |
| `ClipExportRepository` | clip export status transitions |

`worker/repositories/memory.py` provides in-memory implementations that
enforce the same uniqueness semantics the real database will. They are what
`main.py` wires today, and what tests use — swapping in the real
implementations should require no changes to any activity or workflow.

### Idempotency contract

| Data | Uniqueness key |
|---|---|
| Assets | `(organization_id, checksum_sha256)` |
| Transcript segments | replaced wholesale per `(asset_id, analysis_version)` |
| Moments | `(asset_id, start_ms, end_ms, moment_type, analysis_version)` |
| Derivatives | deterministic storage keys — re-upload overwrites |

## Open questions for Member B

1. **Transport.** Direct repository classes sharing a database connection, or
   internal authenticated HTTP endpoints? The Protocols work either way; only
   the concrete implementation and `main.py` wiring change.
2. **Embedding dimensions.** The worker defaults to 1536
   (`text-embedding-3-small`). The `pgvector` column must match.
3. **Credential encryption.** `SourceConnectionRepository` returns
   `encrypted_credentials` as an opaque dict. Confirm whether decryption
   happens in the repository layer or in the worker.
