# Team Member B handoff

Team A's frontend is wired to the current FastAPI backend for authentication,
Library, Upload registration, processing status, Search, and Asset Detail.
Demo mode remains available for frontend-only work.

## Where Team Member A stopped

- React/Vite ECMAScript application: `apps/web`
- JWT-aware ECMAScript API client: `packages/api-client`
- Frontend runtime constants and validators:
  `packages/shared-types/frontend`
- Live Library:
  `GET /api/v1/assets?organization_id=demo-org`
- Live Upload registration:
  `POST /api/v1/uploads/initiate`, followed by
  `POST /api/v1/uploads/{upload_id}/complete`
- Live processing polling:
  `GET /api/v1/assets/{asset_id}/processing-job` every 2.5 seconds for queued,
  uploading, or processing assets
- Upload-time media preview and editable naming. The edited value is persisted
  through `original_filename`.
- Session-local gallery thumbnails and Asset Detail playback for files selected
  in the current browser session.
- Live Search:
  `POST /api/v1/search`
- Live Asset Detail:
  `GET /api/v1/assets/{asset_id}`, `/transcript`, and `/moments`

Set these values in `apps/web/.env.local` to exercise the integration:

```dotenv
VITE_API_URL=http://127.0.0.1:3000/api/v1
VITE_DEMO_MODE=false
```

The seeded account is:

```text
alex@northstar.studio
mediaflow-demo
```

## Frontend/backend contract

The API client prefixes routes with `/api/v1` and sends:

```http
Authorization: Bearer <jwt>
Accept: application/json
Content-Type: application/json
```

The exact methods and routes are defined in
`packages/api-client/src/index.js`.

Library expects `GET /assets` to return an array of the backend `AssetOut`
shape. Upload sends:

```json
{
  "organization_id": "demo-org",
  "original_filename": "example.mp4",
  "media_type": "video"
}
```

Completion sends the browser file size:

```json
{
  "byte_size": 12345
}
```

Processing polling consumes `asset_id`, `stage`, `status`, `progress`, and
`error_message`. The frontend maps `queued` to `pending` and `completed` to
`ready`, and refreshes Library metadata after a terminal job result.

## Next action instructions for Team Member B

1. Pull `team-a/purpose-gallery-frontend` and run `corepack pnpm check`.
2. Start the API on port 3000 and the web app on port 5173 with live mode
   enabled.
3. Sign in, confirm the seeded assets load in Library, and verify that Search
   and Asset Detail still use the same JWT session.
4. Select a small file in **Add media** and verify the initiate and complete
   calls return `201` and `200`. Confirm the new asset appears as queued and
   that the browser polls its processing job.
5. Run a Team C worker update and confirm the card moves from queued to
   analyzing to ready without refreshing the browser.
6. Decide the remaining binary-transfer contract with Team C. The current
   initiate response returns `upload_key` but no signed upload URL, so Team A
   registers the file metadata and completes the provided API lifecycle; it
   does not upload file bytes to object storage yet.
7. Return a signed upload target from initiation, then expose a thumbnail URL
   and playback URL after Team C creates derivatives. Session-local blob URLs
   already prove the frontend thumbnail/player UI, but they intentionally do
   not survive a page refresh.
8. Add an asset rename endpoint or a dedicated `display_name` field if users
   should rename existing assets after upload. Team A currently supports
   editing the name before upload without changing backend-owned routes.
9. Replace the hard-coded `demo-org` with an organization selected from the
   authenticated user's memberships before production use.
10. Investigate the backend pytest/TestClient hang. The isolated live-server
   contract passes, but `.venv/bin/python -m pytest -q` currently stalls.

## Validation completed by Team A

- ESLint passed.
- 13 Vitest tests passed.
- Vite production build passed.
- Isolated live API contract passed:
  login `200`, Library `200`, initiate `201`, complete `200`, and
  processing-job `200`.

The temporary contract-test database was removed after validation.
