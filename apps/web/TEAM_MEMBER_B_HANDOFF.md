# Team Member B handoff

This frontend is complete enough to integrate against the backend incrementally.
It intentionally does not include Express routes, Knex migrations, Postgres
tables, JWT signing/verification, search implementation, or media workers.

## Where Team Member A stopped

- React/Vite ECMAScript application: `apps/web`
- JWT-aware API client: `packages/api-client`
- Frontend runtime constants and validators:
  `packages/shared-types/frontend`
- Demo-mode workflows exist for login, onboarding, library, asset detail,
  transcript seeking, semantic search, upload progress, collections, and team
  invitations.
- The app defaults to demo mode while the backend is unavailable.

## Backend contract already consumed

The API client prefixes routes with `/api/v1` and sends:

```http
Authorization: Bearer <jwt>
Accept: application/json
Content-Type: application/json
```

Expected error body:

```json
{
  "code": "stable_machine_code",
  "message": "Human-readable message",
  "details": {}
}
```

The exact frontend methods and routes are defined in
`packages/api-client/src/index.js`. Treat that file as the current integration
contract until an OpenAPI-generated client replaces it.

## Next action instructions

1. Create `apps/api` with Express in ECMAScript and keep every route under
   `/api/v1`.
2. Configure Knex for Postgres and create the first migrations for users,
   organizations, organization memberships, assets, uploads, transcripts,
   media moments, collections, collection items, and clip exports.
3. Implement JWT issuance and verification. Include at least `sub`, `iat`, and
   `exp`; resolve organization membership server-side rather than trusting an
   organization role supplied by the browser.
4. Add authentication middleware that rejects missing, expired, and malformed
   tokens consistently. Never return password hashes or signing secrets.
5. Implement these endpoints first, in integration order:
   - `POST /auth/register`
   - `POST /auth/login`
   - `POST /auth/refresh`
   - `GET /auth/me`
   - `GET /organizations`
   - `GET /assets`
   - `GET /assets/:asset_id`
   - `GET /assets/:asset_id/transcript`
   - `GET /assets/:asset_id/moments`
   - `POST /search`
   - `GET` and `POST /collections`
   - `GET /organizations/:organization_id/members`
   - `POST /organizations/:organization_id/invitations`
6. Add upload initiation/completion only after the media-storage contract is
   agreed with Team Member C. Large media must upload directly to object storage.
7. Allow CORS from `http://127.0.0.1:5173` in local development and expose the
   API on `http://localhost:3000`.
8. Copy `apps/web/.env.example` to `apps/web/.env.local`, set
   `VITE_DEMO_MODE=false`, and run the web app against the Express API.
9. Verify every query is scoped by the authenticated user's organization.
   Attempt cross-organization asset, transcript, search, and collection access
   in automated tests.
10. Before asking Member A to integrate, run the backend test suite and provide
    an OpenAPI document or sample successful and failing payloads for each route.

For `POST /auth/login`, return at minimum:

```json
{
  "access_token": "signed-jwt",
  "token_type": "Bearer",
  "expires_in": 900
}
```

Prefer a short-lived access JWT plus a rotated refresh token in a
`Secure`, `HttpOnly`, `SameSite` cookie. Do not return the refresh token to
browser JavaScript.

## Checks for Team Member B

- `pnpm check` must continue to pass from the repository root.
- A `401` should be returned when the bearer token is absent or invalid.
- A `403` should be returned when the user is authenticated but lacks
  organization access.
- A missing organization-owned resource should not leak whether another
  organization owns it.
- Duplicate upload initiation should use HTTP `409` and the
  `duplicate_asset` error code.
- Database migrations must be reversible and runnable against a clean Postgres
  database.
