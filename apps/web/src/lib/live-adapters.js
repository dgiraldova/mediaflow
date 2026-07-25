const artworkPalettes = [
  ["#d7c4b4", "#5d4437"],
  ["#c9dfda", "#2d645c"],
  ["#8aa3a1", "#233e43"],
  ["#d8c9a4", "#53663f"],
];

const titleFromFileName = (fileName = "Untitled media") =>
  fileName
    .replace(/\.[^/.]+$/, "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());

export const formatTimestamp = (milliseconds = 0) => {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
};

export const formatDuration = (milliseconds) =>
  milliseconds == null ? "Processing" : formatTimestamp(milliseconds);

export const formatBytes = (bytes) => {
  if (bytes == null) return "Size pending";
  if (bytes < 1_000_000) return `${Math.round(bytes / 1000)} KB`;
  if (bytes < 1_000_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`;
  return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
};

export const normalizeStatus = (status) =>
  ({
    queued: "pending",
    completed: "ready",
    aborted: "failed",
  })[status] ?? status;

export const toAssetViewModel = (asset, index = 0) => ({
  id: asset.id,
  name: titleFromFileName(asset.original_filename),
  fileName: asset.original_filename,
  status: normalizeStatus(asset.status),
  mediaType: asset.media_type,
  duration: formatDuration(asset.duration_ms),
  durationMs: asset.duration_ms ?? 0,
  source: "Live API",
  uploadedAt: "Synced from workspace",
  moments: 0,
  size: formatBytes(asset.byte_size),
  visual: asset.media_type === "image" ? "object" : "portrait",
  colors: artworkPalettes[index % artworkPalettes.length],
  width: asset.width,
  height: asset.height,
  error: asset.error_message,
  previewUrl: asset.playback_url ?? asset.preview_url ?? null,
  thumbnailUrl: asset.thumbnail_url ?? null,
  previewSource: asset.preview_url ? "Stored locally" : null,
  description:
    asset.status === "ready"
      ? "This asset is indexed and ready for transcript and moment discovery."
      : `Live processing status: ${normalizeStatus(asset.status)}.`,
});

export const applyProcessingJob = (asset, job) => ({
  ...asset,
  status: normalizeStatus(job.status),
  processingStage: job.stage,
  processingProgress: job.progress,
  error: job.error_message ?? asset.error,
});

export const toTranscriptViewModel = (segment) => ({
  id: segment.id,
  startMs: segment.start_ms,
  endMs: segment.end_ms,
  time: formatTimestamp(segment.start_ms),
  speaker: segment.speaker ?? "Unknown speaker",
  text: segment.text,
});

export const toMomentViewModel = (moment) => ({
  id: moment.id,
  title: moment.title,
  time: `${formatTimestamp(moment.start_ms)}–${formatTimestamp(moment.end_ms)}`,
  startMs: moment.start_ms,
  endMs: moment.end_ms,
  type: moment.category,
  score: moment.score,
});

export const toSearchResultViewModel = (result, index = 0) => ({
  id: result.moment_id,
  assetId: result.asset_id,
  momentId: result.moment_id,
  title: result.title,
  assetName: titleFromFileName(result.asset_name ?? result.asset_id),
  mediaType: result.media_type ?? "video",
  timestamp: formatTimestamp(result.start_ms),
  startMs: result.start_ms,
  endMs: result.end_ms,
  excerpt: result.excerpt,
  reason: result.match_reasons.join(" · "),
  score: result.score,
  previewUrl: result.preview_url ?? null,
  thumbnailUrl: result.thumbnail_url ?? null,
  playbackUrl: result.playback_url ?? null,
  colors: artworkPalettes[index % artworkPalettes.length],
});
