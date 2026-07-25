import { createApiClient } from "@mediaflow/api-client";

describe("JWT API client", () => {
  it("adds the bearer token and serializes list filters", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: async () => ({ items: [] }),
    });
    const client = createApiClient({
      baseUrl: "http://localhost:3000/api/v1",
      getAccessToken: () => "signed.jwt",
      fetchImpl,
    });

    await client.assets.list({ status: "ready", media_type: ["video", "image"] });

    expect(fetchImpl).toHaveBeenCalledWith(
      "http://localhost:3000/api/v1/assets?status=ready&media_type=video&media_type=image",
      expect.objectContaining({
        headers: expect.any(Headers),
      }),
    );
    const requestOptions = fetchImpl.mock.calls[0][1];
    expect(requestOptions.headers.get("Authorization")).toBe("Bearer signed.jwt");
  });

  it("normalizes API errors", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      headers: { get: () => "application/json" },
      json: async () => ({
        code: "duplicate_asset",
        message: "This file already exists.",
      }),
    });
    const client = createApiClient({ fetchImpl });

    await expect(client.uploads.initiate({ file_name: "demo.mp4" })).rejects.toMatchObject({
      status: 409,
      code: "duplicate_asset",
    });
  });

  it("requests organization assets and processing status through the live contract", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: async () => ({}),
    });
    const client = createApiClient({
      baseUrl: "http://localhost:3000/api/v1",
      getAccessToken: () => "signed.jwt",
      fetchImpl,
    });

    await client.assets.list({ organization_id: "demo-org" });
    await client.assets.processingJob("asset-live");

    expect(fetchImpl.mock.calls[0][0]).toBe(
      "http://localhost:3000/api/v1/assets?organization_id=demo-org",
    );
    expect(fetchImpl.mock.calls[1][0]).toBe(
      "http://localhost:3000/api/v1/assets/asset-live/processing-job",
    );
  });
});
