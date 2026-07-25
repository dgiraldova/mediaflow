import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { App } from "../App";
import { api } from "../lib/api";
import { AuthProvider } from "../state/AuthContext";

const renderApp = (route = "/library") =>
  render(
    <MemoryRouter initialEntries={[route]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );

describe("MediaFlow frontend", () => {
  it("renders the Team A library workflow", () => {
    renderApp();

    expect(screen.getByRole("heading", { name: /your media library/i })).toBeInTheDocument();
    expect(screen.getByText("Customer story — Acme")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add media/i })).toBeInTheDocument();
  });

  it("filters the library by processing status", async () => {
    const user = userEvent.setup();
    renderApp();

    await user.click(screen.getByRole("button", { name: /analyzing/i }));

    expect(screen.getByText("Founder notes — July")).toBeInTheDocument();
    expect(screen.queryByText("Customer story — Acme")).not.toBeInTheDocument();
  });

  it("opens a search result at its media asset", async () => {
    const user = userEvent.setup();
    renderApp("/search");

    await user.click(screen.getByRole("link", { name: /open at 00:31/i }));

    expect(screen.getByRole("heading", { name: "Customer story — Acme" })).toBeInTheDocument();
    expect(screen.getByText(/best parts of this video/i)).toBeInTheDocument();
  });

  it("renders Search results returned by the live API", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "false");
    window.localStorage.setItem("mediaflow.access_token", "signed.jwt");
    vi.spyOn(api.search, "query").mockResolvedValue({
      search_id: "search-live",
      results: [
        {
          asset_id: "customer-story",
          moment_id: "live-moment",
          title: "Live onboarding result",
          start_ms: 31_000,
          end_ms: 53_000,
          excerpt: "This excerpt came from the API.",
          match_reasons: ["Matched live transcript"],
          score: 0.97,
          media_type: "video",
          preview_url: "http://media.test/live-thumbnail.jpg",
          thumbnail_url: "http://media.test/live-thumbnail.jpg",
          playback_url: "http://media.test/live-proxy.mp4",
        },
      ],
    });

    renderApp("/search?q=easy+onboarding");

    expect(await screen.findByText("Live onboarding result")).toBeInTheDocument();
    expect(screen.getByText(/this excerpt came from the api/i)).toBeInTheDocument();
    const preview = screen.getByRole("link", {
      name: "Preview Live onboarding result at 00:31",
    });
    expect(preview).toHaveClass("has-media-preview");
    expect(preview.querySelector("img")).toHaveAttribute(
      "src",
      "http://media.test/live-thumbnail.jpg",
    );
    expect(preview.querySelector("video")).toHaveAttribute(
      "src",
      "http://media.test/live-proxy.mp4#t=31",
    );
    expect(api.search.query).toHaveBeenCalledWith({ query: "easy onboarding" });
  });

  it("hydrates Asset Detail from live metadata, transcript, and moments", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "false");
    window.localStorage.setItem("mediaflow.access_token", "signed.jwt");
    vi.spyOn(api.assets, "get").mockResolvedValue({
      id: "customer-story",
      organization_id: "demo-org",
      original_filename: "customer_story_live.mp4",
      media_type: "video",
      status: "ready",
      byte_size: 2_400_000,
      duration_ms: 112_000,
      width: 1920,
      height: 1080,
    });
    vi.spyOn(api.assets, "transcript").mockResolvedValue([
      {
        id: "segment-live",
        start_ms: 31_000,
        end_ms: 48_000,
        speaker: "Maya",
        text: "This transcript segment came from Postgres.",
      },
    ]);
    vi.spyOn(api.assets, "moments").mockResolvedValue([
      {
        id: "moment-live",
        title: "Live purposeful moment",
        start_ms: 31_000,
        end_ms: 53_000,
        category: "Testimonial",
        score: 96,
      },
    ]);

    renderApp("/library/assets/customer-story?start=31000");

    expect(
      await screen.findByRole("heading", { name: "Customer Story Live" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Live purposeful moment")).toBeInTheDocument();
    expect(screen.getByText(/came from Postgres/i)).toBeInTheDocument();
    expect(api.assets.get).toHaveBeenCalledWith("customer-story");
  });

  it("loads the Library from the live organization and polls processing status", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "false");
    window.localStorage.setItem("mediaflow.access_token", "signed.jwt");
    vi.spyOn(api.assets, "list").mockResolvedValue([
      {
        id: "asset-live",
        organization_id: "demo-org",
        original_filename: "founder_notes_live.mov",
        media_type: "video",
        status: "processing",
        byte_size: 8_400_000,
        duration_ms: null,
        width: null,
        height: null,
        error_message: null,
      },
    ]);
    vi.spyOn(api.assets, "processingJob").mockResolvedValue({
      id: "job-live",
      asset_id: "asset-live",
      stage: "transcription",
      status: "processing",
      progress: 42,
      error_message: null,
    });

    renderApp("/library");

    expect(await screen.findByText("Founder Notes Live")).toBeInTheDocument();
    expect(api.assets.list).toHaveBeenCalledWith({ organization_id: "demo-org" });
    await waitFor(() => {
      expect(api.assets.processingJob).toHaveBeenCalledWith("asset-live");
    });
    expect(await screen.findByText("transcription · 42%")).toBeInTheDocument();
  });

  it("initiates and completes a live upload before refreshing Library", async () => {
    const user = userEvent.setup();
    vi.stubEnv("VITE_DEMO_MODE", "false");
    window.localStorage.setItem("mediaflow.access_token", "signed.jwt");
    vi.spyOn(api.assets, "list")
      .mockResolvedValueOnce([])
      .mockResolvedValue([
        {
          id: "asset-uploaded",
          organization_id: "demo-org",
          original_filename: "customer-kickoff.mp4",
          media_type: "video",
          status: "processing",
          byte_size: 5,
          duration_ms: null,
          width: null,
          height: null,
          error_message: null,
        },
      ]);
    vi.spyOn(api.assets, "processingJob").mockResolvedValue({
      id: "job-uploaded",
      asset_id: "asset-uploaded",
      stage: "queued",
      status: "queued",
      progress: 0,
      error_message: null,
    });
    vi.spyOn(api.uploads, "initiate").mockResolvedValue({
      asset_id: "asset-uploaded",
      upload_id: "upload-live",
      upload_key: "organizations/demo-org/assets/asset-uploaded/new-story.mp4",
      status: "uploading",
    });
    vi.spyOn(api.uploads, "uploadContent").mockResolvedValue({
      asset_id: "asset-uploaded",
      upload_id: "upload-live",
      byte_size: 5,
    });
    vi.spyOn(api.uploads, "complete").mockResolvedValue({
      asset_id: "asset-uploaded",
      upload_id: "upload-live",
      status: "processing",
    });

    renderApp("/library");

    await screen.findByText("Your library is ready for media");
    await user.click(screen.getByRole("button", { name: /add media/i }));
    const file = new File(["media"], "new-story.mp4", { type: "video/mp4" });
    await user.upload(screen.getByLabelText(/drop video, images, or audio/i), file);
    expect(screen.getByLabelText("Preview of new-story.mp4")).toBeInTheDocument();
    const nameInput = screen.getByRole("textbox", { name: /name in mediaflow/i });
    await user.clear(nameInput);
    await user.type(nameInput, "customer-kickoff.mp4");
    await user.click(screen.getByRole("button", { name: "Start upload" }));

    expect(await screen.findByText("Stored locally — analysis queued")).toBeInTheDocument();
    expect(api.uploads.initiate).toHaveBeenCalledWith({
      organization_id: "demo-org",
      original_filename: "customer-kickoff.mp4",
      media_type: "video",
    });
    expect(api.uploads.uploadContent).toHaveBeenCalledWith("upload-live", file);
    expect(api.uploads.complete).toHaveBeenCalledWith("upload-live", {
      byte_size: file.size,
    });
    await waitFor(() => {
      expect(api.assets.list).toHaveBeenCalledTimes(2);
    });
    expect(await screen.findByText("Customer Kickoff")).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: "Customer Kickoff preview" }),
    ).toHaveClass("has-media-preview");
  });
});
