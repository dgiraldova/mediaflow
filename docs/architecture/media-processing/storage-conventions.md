# Storage conventions

Media lives in Cloudflare R2 (S3-compatible). Locally, MinIO stands in for it
with the same API — only `R2_ENDPOINT_URL` differs.

## Key layout

```
orgs/{organization_id}/assets/{asset_id}/original/{filename}
orgs/{organization_id}/assets/{asset_id}/proxy/proxy.mp4
orgs/{organization_id}/assets/{asset_id}/thumbnails/main.jpg
orgs/{organization_id}/assets/{asset_id}/thumbnails/preview-{NNN}.jpg
orgs/{organization_id}/assets/{asset_id}/audio/audio.wav
orgs/{organization_id}/clips/{clip_export_id}/clip.mp4
```

Built exclusively through `build_storage_key()` in
`worker/storage/keys.py` — never string-concatenated at call sites.

Two properties matter:

- **Organization first.** A leaked or mis-scoped prefix can never cross a
  tenant boundary.
- **Deterministic.** Re-running an activity overwrites the same object rather
  than creating an orphan, which is what makes derivative generation
  idempotent.

Filenames from users are reduced to their basename and sanitized, so a
crafted `../../etc/passwd` becomes `passwd`.

## Direct-upload flow

The original media must never pass through Next.js or FastAPI (spec 11.1).

1. The API creates the asset in `uploading` state.
2. `R2Client.create_multipart_upload()` returns one presigned URL per part.
3. The browser uploads parts straight to R2.
4. The browser reports completion with each part's ETag.
5. `complete_multipart_upload()` assembles the object.
6. The API verifies object metadata, moves the asset to `queued`, and starts
   `IngestAssetWorkflow`.

`abort_multipart_upload()` cleans up a cancelled or failed upload.

## Access

Buckets are private. Nothing outside the worker reads an object directly:

- Playback and thumbnails use short-lived signed GET URLs
  (`R2_SIGNED_URL_TTL_SECONDS`, default 1 hour).
- Uploads use short-lived presigned PUT/multipart URLs.
- The worker itself uses credentialed S3 access, server-side only.

R2 credentials exist only in the worker and API environments and must never
reach the browser bundle.
