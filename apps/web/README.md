# MediaFlow web

Team Member A's React application and user-workflow layer.

## Run locally

From the repository root:

```bash
pnpm install
pnpm dev
```

The app runs at `http://127.0.0.1:5173`. Demo mode is enabled by default so the
frontend can be developed before the Express API is available.

Copy `.env.example` to `.env.local` and set `VITE_DEMO_MODE=false` to use the
JWT-protected API configured by `VITE_API_URL`.

## Ownership boundary

This application consumes, but does not implement, the Express/Knex/Postgres
backend. JWT access tokens are attached by `@mediaflow/api-client`; token
issuance, verification, database migrations, and organization authorization
belong to Team Member B.
