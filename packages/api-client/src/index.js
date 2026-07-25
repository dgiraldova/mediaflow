const DEFAULT_TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  constructor(message, { status = 0, code = "unknown_error", details = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const createQueryString = (params = {}) => {
  const query = new URLSearchParams();

  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    if (Array.isArray(value)) {
      value.forEach((item) => query.append(key, item));
      return;
    }
    query.set(key, value);
  });

  const serialized = query.toString();
  return serialized ? `?${serialized}` : "";
};

export const createApiClient = ({
  baseUrl = "/api/v1",
  getAccessToken = () => null,
  fetchImpl = globalThis.fetch,
  timeoutMs = DEFAULT_TIMEOUT_MS,
} = {}) => {
  if (typeof fetchImpl !== "function") {
    throw new TypeError("A fetch implementation is required.");
  }

  const request = async (path, options = {}) => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    const token = await getAccessToken();
    const headers = new Headers(options.headers);

    headers.set("Accept", "application/json");
    if (options.body && !(options.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }
    if (token) headers.set("Authorization", `Bearer ${token}`);

    try {
      const response = await fetchImpl(`${baseUrl}${path}`, {
        ...options,
        headers,
        signal: controller.signal,
        body:
          options.body && !(options.body instanceof FormData)
            ? JSON.stringify(options.body)
            : options.body,
      });

      const contentType = response.headers.get("content-type") ?? "";
      const payload = contentType.includes("application/json")
        ? await response.json()
        : await response.text();

      if (!response.ok) {
        throw new ApiError(payload?.message ?? "The request could not be completed.", {
          status: response.status,
          code: payload?.code ?? "request_failed",
          details: payload?.details ?? payload,
        });
      }

      return response.status === 204 ? null : payload;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (error.name === "AbortError") {
        throw new ApiError("The request timed out.", { code: "request_timeout" });
      }
      throw new ApiError("The API is currently unavailable.", {
        code: "network_error",
        details: error.message,
      });
    } finally {
      clearTimeout(timeout);
    }
  };

  return {
    request,
    auth: {
      login: (input) => request("/auth/login", { method: "POST", body: input }),
      register: (input) => request("/auth/register", { method: "POST", body: input }),
      refresh: () => request("/auth/refresh", { method: "POST" }),
      me: () => request("/auth/me"),
    },
    organizations: {
      list: () => request("/organizations"),
      create: (input) => request("/organizations", { method: "POST", body: input }),
      members: (organizationId) => request(`/organizations/${organizationId}/members`),
      invite: (organizationId, input) =>
        request(`/organizations/${organizationId}/invitations`, {
          method: "POST",
          body: input,
        }),
    },
    uploads: {
      initiate: (input) => request("/uploads/initiate", { method: "POST", body: input }),
      complete: (uploadId, input) =>
        request(`/uploads/${uploadId}/complete`, { method: "POST", body: input }),
      abort: (uploadId) => request(`/uploads/${uploadId}/abort`, { method: "POST" }),
    },
    assets: {
      list: (params) => request(`/assets${createQueryString(params)}`),
      get: (assetId) => request(`/assets/${assetId}`),
      transcript: (assetId) => request(`/assets/${assetId}/transcript`),
      moments: (assetId) => request(`/assets/${assetId}/moments`),
      processingJob: (assetId) => request(`/assets/${assetId}/processing-job`),
      playbackUrl: (assetId) => request(`/assets/${assetId}/playback-url`),
      retry: (assetId) => request(`/assets/${assetId}/retry`, { method: "POST" }),
    },
    search: {
      query: (input) => request("/search", { method: "POST", body: input }),
      feedback: (searchId, input) =>
        request(`/search/${searchId}/feedback`, { method: "POST", body: input }),
    },
    collections: {
      list: () => request("/collections"),
      get: (collectionId) => request(`/collections/${collectionId}`),
      create: (input) => request("/collections", { method: "POST", body: input }),
      addItem: (collectionId, input) =>
        request(`/collections/${collectionId}/items`, { method: "POST", body: input }),
      removeItem: (collectionId, itemId) =>
        request(`/collections/${collectionId}/items/${itemId}`, { method: "DELETE" }),
    },
    exports: {
      create: (input) => request("/clip-exports", { method: "POST", body: input }),
      get: (exportId) => request(`/clip-exports/${exportId}`),
      downloadUrl: (exportId) => request(`/clip-exports/${exportId}/download-url`),
    },
  };
};
