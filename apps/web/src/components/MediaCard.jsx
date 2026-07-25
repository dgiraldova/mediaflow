import { MoreHorizontal, Play, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import { StatusPill } from "./StatusPill";

export const MediaArtwork = ({ asset, className = "" }) => (
  <div
    className={`media-artwork artwork-${asset.visual ?? "portrait"} ${
      asset.previewUrl ? "has-media-preview" : ""
    } ${className}`}
    style={{
      "--art-color-a": asset.colors?.[0] ?? "#d9d2c4",
      "--art-color-b": asset.colors?.[1] ?? "#364a48",
    }}
    aria-label={`${asset.name} preview`}
    role="img"
  >
    {asset.previewUrl && asset.mediaType === "image" ? (
      <img className="media-preview-image" src={asset.previewUrl} alt="" />
    ) : asset.previewUrl && asset.mediaType === "video" ? (
      <video
        className="media-preview-video"
        src={asset.previewUrl}
        muted
        playsInline
        preload="metadata"
        aria-hidden="true"
        onLoadedData={(event) => {
          const video = event.currentTarget;
          if (Number.isFinite(video.duration) && video.duration > 0) {
            video.currentTime = Math.min(0.1, video.duration / 2);
          }
        }}
      />
    ) : (
      <>
        <div className="art-grid" />
        <div className="art-subject">
          <span />
        </div>
      </>
    )}
    {asset.mediaType === "video" && (
      <span className="art-play" aria-hidden="true">
        <Play size={16} fill="currentColor" />
      </span>
    )}
    <span className="art-duration">{asset.duration}</span>
  </div>
);

export const MediaCard = ({ asset }) => (
  <article className="media-card">
    <Link to={`/library/assets/${asset.id}`} className="media-card-link">
      <MediaArtwork asset={asset} />
      <div className="media-card-body">
        <div className="media-card-title-row">
          <div>
            <h3>{asset.name}</h3>
            <p>{asset.fileName}</p>
          </div>
          <button
            className="icon-button media-menu"
            type="button"
            aria-label={`More options for ${asset.name}`}
            onClick={(event) => event.preventDefault()}
          >
            <MoreHorizontal size={18} />
          </button>
        </div>
        <div className="media-card-meta">
          <StatusPill status={asset.status} compact />
          {asset.processingProgress != null &&
          ["pending", "processing", "uploading"].includes(asset.status) ? (
            <span className="processing-progress">
              {asset.processingStage} · {asset.processingProgress}%
            </span>
          ) : (
            asset.moments > 0 && (
            <span className="moment-count">
              <Sparkles size={12} aria-hidden="true" />
              {asset.moments} moments
            </span>
            )
          )}
        </div>
      </div>
    </Link>
  </article>
);
