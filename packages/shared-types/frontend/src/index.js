export const PROCESSING_STATUS = Object.freeze({
  PENDING: "pending",
  UPLOADING: "uploading",
  PROCESSING: "processing",
  READY: "ready",
  FAILED: "failed",
});

export const MEDIA_TYPE = Object.freeze({
  VIDEO: "video",
  IMAGE: "image",
  AUDIO: "audio",
});

export const ORGANIZATION_ROLE = Object.freeze({
  OWNER: "owner",
  ADMIN: "admin",
  MEMBER: "member",
  VIEWER: "viewer",
});

export const isSearchResult = (value) =>
  Boolean(
    value &&
      typeof value === "object" &&
      typeof value.asset_id === "string" &&
      typeof value.start_ms === "number" &&
      Array.isArray(value.match_reasons),
  );

export const isAsset = (value) =>
  Boolean(
    value &&
      typeof value === "object" &&
      typeof value.id === "string" &&
      typeof value.name === "string" &&
      Object.values(MEDIA_TYPE).includes(value.media_type),
  );
