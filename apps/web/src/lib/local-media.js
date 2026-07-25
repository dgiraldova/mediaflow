const localMedia = new Map();

export const mediaTypeForFile = (file) => {
  if (file.type.startsWith("image/")) return "image";
  if (file.type.startsWith("audio/")) return "audio";
  return "video";
};

export const createLocalPreviewUrl = (file) =>
  typeof URL.createObjectURL === "function" ? URL.createObjectURL(file) : null;

export const rememberLocalMedia = (assetId, file) => {
  const existing = localMedia.get(assetId);
  if (existing?.url && typeof URL.revokeObjectURL === "function") {
    URL.revokeObjectURL(existing.url);
  }

  const preview = {
    url: createLocalPreviewUrl(file),
    mediaType: mediaTypeForFile(file),
  };
  localMedia.set(assetId, preview);
  return preview;
};

export const getLocalMedia = (assetId) => localMedia.get(assetId) ?? null;
