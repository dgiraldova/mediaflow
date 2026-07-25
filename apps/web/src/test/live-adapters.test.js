import {
  applyProcessingJob,
  formatBytes,
  formatTimestamp,
  toAssetViewModel,
  toSearchResultViewModel,
} from "../lib/live-adapters";

describe("live API view-model adapters", () => {
  it("formats API media values for the interface", () => {
    expect(formatTimestamp(31_000)).toBe("00:31");
    expect(formatBytes(2_400_000)).toBe("2.4 MB");
    expect(
      toAssetViewModel({
        id: "asset-1",
        original_filename: "customer_story.mp4",
        status: "completed",
        media_type: "video",
        duration_ms: 91_000,
        byte_size: 2_400_000,
      }),
    ).toMatchObject({
      name: "Customer Story",
      status: "ready",
      duration: "01:31",
    });
  });

  it("maps timestamped search results without static asset data", () => {
    expect(
      toSearchResultViewModel({
        asset_id: "customer-story",
        moment_id: "moment-1",
        title: "Easy onboarding",
        start_ms: 31_000,
        end_ms: 53_000,
        excerpt: "It was easier than expected.",
        match_reasons: ["Matched onboarding", "Matched transcript"],
        score: 1,
        media_type: "video",
        preview_url: "http://media.test/thumbnail.jpg",
        thumbnail_url: "http://media.test/thumbnail.jpg",
        playback_url: "http://media.test/proxy.mp4",
      }),
    ).toMatchObject({
      assetName: "Customer Story",
      timestamp: "00:31",
      reason: "Matched onboarding · Matched transcript",
      mediaType: "video",
      thumbnailUrl: "http://media.test/thumbnail.jpg",
      playbackUrl: "http://media.test/proxy.mp4",
    });
  });

  it("applies processing-job progress to a library asset", () => {
    expect(
      applyProcessingJob(
        { id: "asset-1", status: "processing" },
        {
          asset_id: "asset-1",
          stage: "transcription",
          status: "queued",
          progress: 35,
          error_message: null,
        },
      ),
    ).toMatchObject({
      status: "pending",
      processingStage: "transcription",
      processingProgress: 35,
    });
  });
});
