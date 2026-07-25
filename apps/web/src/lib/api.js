import { createApiClient } from "@mediaflow/api-client";

export const api = createApiClient({
  baseUrl: import.meta.env.VITE_API_URL ?? "http://localhost:3000/api/v1",
  getAccessToken: () => window.localStorage.getItem("mediaflow.access_token"),
});
