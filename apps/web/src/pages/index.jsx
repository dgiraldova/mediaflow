import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  Download,
  Filter,
  FolderHeart,
  Grid2X2,
  HardDrive,
  LayoutList,
  ListFilter,
  MoreHorizontal,
  Play,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Upload,
  UserPlus,
  WandSparkles,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { MediaArtwork, MediaCard } from "../components/MediaCard";
import { StatusPill } from "../components/StatusPill";
import { UploadDialog } from "../components/UploadDialog";
import {
  assets,
  collections as initialCollections,
  moments,
  searchResults,
  teamMembers,
  transcript,
} from "../lib/demo-data";
import { api } from "../lib/api";
import {
  applyProcessingJob,
  formatTimestamp,
  toAssetViewModel,
  toMomentViewModel,
  toSearchResultViewModel,
  toTranscriptViewModel,
} from "../lib/live-adapters";
import { useAuth } from "../state/auth-context";

export const LoginPage = () => {
  const navigate = useNavigate();
  const { login } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);

    try {
      if (import.meta.env.VITE_DEMO_MODE === "false") {
        const session = await api.auth.login({
          email: data.get("email"),
          password: data.get("password"),
        });
        login({ accessToken: session.access_token });
      } else {
        await new Promise((resolve) => window.setTimeout(resolve, 350));
        login();
      }
      navigate("/library");
    } catch (loginError) {
      setError(loginError.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-layout">
      <section className="auth-story">
        <Link className="brand auth-brand" to="/login">
          <span className="brand-mark" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          <span>mediaflow</span>
        </Link>
        <div className="auth-story-copy">
          <span className="eyebrow light">Your media, made useful</span>
          <h1>Find the exact moment your story needs.</h1>
          <p>
            MediaFlow understands every video, image, and conversation—so great content never gets
            lost in a folder again.
          </p>
        </div>
        <div className="auth-proof-card">
          <Sparkles size={18} aria-hidden="true" />
          <p>“Find the moment where a customer says implementation was easier than expected.”</p>
          <span>Found at 00:31 in Customer story — Acme</span>
        </div>
      </section>

      <section className="auth-panel">
        <form className="auth-form" onSubmit={handleSubmit}>
          <div>
            <span className="eyebrow">Welcome back</span>
            <h2>Sign in to your library</h2>
            <p>Use your work account to continue.</p>
          </div>
          <label>
            Work email
            <input name="email" type="email" defaultValue="alex@northstar.studio" required />
          </label>
          <label>
            <span className="label-row">
              Password
              <a href="#forgot">Forgot password?</a>
            </span>
            <input name="password" type="password" defaultValue="mediaflow-demo" required />
          </label>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <button className="button primary auth-submit" type="submit" disabled={busy}>
            {busy ? "Signing in..." : "Sign in"}
            {!busy && <ArrowRight size={17} />}
          </button>
          <div className="auth-security">
            <ShieldCheck size={16} />
            <span>JWT-secured session · organization-isolated access</span>
          </div>
          <p className="auth-switch">
            New to MediaFlow? <Link to="/onboarding">Create your workspace</Link>
          </p>
        </form>
      </section>
    </div>
  );
};

export const OnboardingPage = () => {
  const navigate = useNavigate();
  const { login } = useAuth();
  const [step, setStep] = useState(1);
  const [source, setSource] = useState("upload");

  const finish = () => {
    login();
    navigate("/library");
  };

  return (
    <div className="onboarding-layout">
      <header className="onboarding-header">
        <Link className="brand" to="/login">
          <span className="brand-mark" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          <span>mediaflow</span>
        </Link>
        <span className="onboarding-step">Step {step} of 2</span>
      </header>
      <div className="onboarding-progress">
        <span style={{ width: `${step * 50}%` }} />
      </div>

      <main className="onboarding-card">
        {step === 1 ? (
          <>
            <span className="eyebrow">Create your space</span>
            <h1>What should we call your workspace?</h1>
            <p>Use your company, team, or client name. You can change this later.</p>
            <label className="large-field">
              Workspace name
              <input defaultValue="Northstar Studio" autoFocus />
            </label>
            <button className="button primary" type="button" onClick={() => setStep(2)}>
              Continue <ArrowRight size={17} />
            </button>
          </>
        ) : (
          <>
            <span className="eyebrow">Bring your first media</span>
            <h1>Where does your content live?</h1>
            <p>Start with an upload or connect a folder. You can always add another source.</p>
            <div className="source-options">
              <button
                className={`source-option ${source === "upload" ? "selected" : ""}`}
                type="button"
                onClick={() => setSource("upload")}
              >
                <span className="source-icon">
                  <Upload size={22} />
                </span>
                <span>
                  <strong>Upload from your computer</strong>
                  <small>Best for a few files or a quick start.</small>
                </span>
                <span className="selection-dot">{source === "upload" && <Check size={13} />}</span>
              </button>
              <button
                className={`source-option ${source === "drive" ? "selected" : ""}`}
                type="button"
                onClick={() => setSource("drive")}
              >
                <span className="source-icon">
                  <HardDrive size={22} />
                </span>
                <span>
                  <strong>Connect Google Drive</strong>
                  <small>Sync an existing folder automatically.</small>
                </span>
                <span className="selection-dot">{source === "drive" && <Check size={13} />}</span>
              </button>
            </div>
            <div className="onboarding-actions">
              <button className="button ghost" type="button" onClick={() => setStep(1)}>
                <ArrowLeft size={17} /> Back
              </button>
              <button className="button primary" type="button" onClick={finish}>
                Finish setup <ArrowRight size={17} />
              </button>
            </div>
          </>
        )}
      </main>
    </div>
  );
};

export const LibraryPage = () => {
  const liveApi = import.meta.env.VITE_DEMO_MODE === "false";
  const [view, setView] = useState("grid");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [libraryAssets, setLibraryAssets] = useState(liveApi ? [] : assets);
  const [loading, setLoading] = useState(liveApi);
  const [error, setError] = useState("");

  const loadAssets = useCallback(
    async ({ showLoading = true } = {}) => {
      if (!liveApi) return;
      if (showLoading) setLoading(true);
      setError("");

      try {
        const response = await api.assets.list({ organization_id: "demo-org" });
        setLibraryAssets(response.map(toAssetViewModel));
      } catch (libraryError) {
        setError(libraryError.message);
      } finally {
        if (showLoading) setLoading(false);
      }
    },
    [liveApi],
  );

  useEffect(() => {
    void loadAssets();
  }, [loadAssets]);

  const pollableAssetKey = libraryAssets
    .filter((asset) => ["pending", "processing", "uploading"].includes(asset.status))
    .map((asset) => asset.id)
    .sort()
    .join("|");

  useEffect(() => {
    if (!liveApi || !pollableAssetKey) return undefined;

    let cancelled = false;
    const assetIds = pollableAssetKey.split("|");
    const pollProcessingJobs = async () => {
      const results = await Promise.allSettled(
        assetIds.map((assetId) => api.assets.processingJob(assetId)),
      );
      if (cancelled) return;

      const jobsByAssetId = new Map();
      let reachedTerminalStatus = false;
      results.forEach((result) => {
        if (result.status !== "fulfilled") return;
        jobsByAssetId.set(result.value.asset_id, result.value);
        if (["completed", "failed"].includes(result.value.status)) {
          reachedTerminalStatus = true;
        }
      });

      setLibraryAssets((currentAssets) => {
        let changed = false;
        const nextAssets = currentAssets.map((asset) => {
          const job = jobsByAssetId.get(asset.id);
          if (!job) return asset;
          const nextAsset = applyProcessingJob(asset, job);
          if (
            nextAsset.status === asset.status &&
            nextAsset.processingStage === asset.processingStage &&
            nextAsset.processingProgress === asset.processingProgress &&
            nextAsset.error === asset.error
          ) {
            return asset;
          }
          changed = true;
          return nextAsset;
        });
        return changed ? nextAssets : currentAssets;
      });

      if (reachedTerminalStatus) {
        void loadAssets({ showLoading: false });
      }
    };

    void pollProcessingJobs();
    const pollingInterval = window.setInterval(pollProcessingJobs, 2_500);
    return () => {
      cancelled = true;
      window.clearInterval(pollingInterval);
    };
  }, [liveApi, loadAssets, pollableAssetKey]);

  const visibleAssets = useMemo(
    () =>
      libraryAssets.filter(
        (asset) =>
          (status === "all" ||
            asset.status === status ||
            (status === "processing" &&
              ["pending", "processing", "uploading"].includes(asset.status))) &&
          `${asset.name} ${asset.fileName}`.toLowerCase().includes(query.toLowerCase()),
      ),
    [libraryAssets, query, status],
  );

  const processingCount = libraryAssets.filter((asset) =>
    ["pending", "processing", "uploading"].includes(asset.status),
  ).length;

  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">Northstar Studio</span>
          <h1>Your media library</h1>
          <p>Every file, understood and ready when you need it.</p>
        </div>
        <button className="button primary" type="button" onClick={() => setUploadOpen(true)}>
          <Plus size={17} /> Add media
        </button>
      </div>

      <section className="insight-strip">
        <div className="insight-icon">
          <WandSparkles size={21} />
        </div>
        <div>
          <strong>Your library is getting smarter</strong>
          <p>
            {liveApi ? (
              <>
                <b>{libraryAssets.length} assets</b> synced from the live workspace.
              </>
            ) : (
              <>
                MediaFlow found <b>61 useful moments</b> across your latest uploads.
              </>
            )}
          </p>
        </div>
        <Link to="/search?q=show+me+the+best+customer+moments">
          Explore moments <ArrowRight size={15} />
        </Link>
      </section>

      <div className="toolbar">
        <div className="filter-tabs" aria-label="Filter library by status">
          {[
            ["all", "All files"],
            ["ready", "Ready"],
            ["processing", "Analyzing"],
            ["failed", "Attention"],
          ].map(([value, label]) => (
            <button
              key={value}
              className={status === value ? "active" : ""}
              type="button"
              onClick={() => setStatus(value)}
            >
              {label}
              {value === "processing" && processingCount > 0 && (
                <span className="tab-count">{processingCount}</span>
              )}
            </button>
          ))}
        </div>
        <div className="toolbar-actions">
          <label className="inline-search">
            <Search size={16} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter files..."
              aria-label="Filter files"
            />
          </label>
          <button className="filter-button" type="button">
            <ListFilter size={16} /> Filters
          </button>
          <div className="view-toggle" aria-label="Change library view">
            <button
              className={view === "grid" ? "active" : ""}
              type="button"
              aria-label="Grid view"
              onClick={() => setView("grid")}
            >
              <Grid2X2 size={16} />
            </button>
            <button
              className={view === "list" ? "active" : ""}
              type="button"
              aria-label="List view"
              onClick={() => setView("list")}
            >
              <LayoutList size={17} />
            </button>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="request-state" role="status">
          <span className="loading-spinner" aria-hidden="true" />
          <div>
            <strong>Loading your live library</strong>
            <p>Fetching organization media and processing state.</p>
          </div>
        </div>
      ) : error ? (
        <div className="request-state error-state" role="alert">
          <div>
            <strong>We could not load the library</strong>
            <p>{error}</p>
          </div>
          <button className="button secondary" type="button" onClick={() => loadAssets()}>
            Try again
          </button>
        </div>
      ) : (
        <section className={`media-grid ${view === "list" ? "list-view" : ""}`}>
          {visibleAssets.map((asset) => (
            <MediaCard key={asset.id} asset={asset} />
          ))}
        </section>
      )}

      {!loading && !error && visibleAssets.length === 0 && (
        <div className="empty-state">
          <Search size={24} />
          <h2>{libraryAssets.length === 0 ? "Your library is ready for media" : "No files match"}</h2>
          <p>
            {libraryAssets.length === 0
              ? "Upload your first file to start building a searchable purpose gallery."
              : "Try clearing your search or choosing another processing status."}
          </p>
          <button
            className="button secondary"
            type="button"
            onClick={() => {
              setQuery("");
              setStatus("all");
            }}
          >
            Clear filters
          </button>
        </div>
      )}

      <UploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploaded={() => loadAssets({ showLoading: false })}
        liveApi={liveApi}
        organizationId="demo-org"
      />
    </>
  );
};

export const SearchPage = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialQuery = searchParams.get("q") ?? "customer saying onboarding was easier than expected";
  const [query, setQuery] = useState(initialQuery);
  const [submittedQuery, setSubmittedQuery] = useState(initialQuery);
  const [filterOpen, setFilterOpen] = useState(false);
  const liveApi = import.meta.env.VITE_DEMO_MODE === "false";
  const [results, setResults] = useState(liveApi ? [] : searchResults);
  const [searchId, setSearchId] = useState(null);
  const [loading, setLoading] = useState(liveApi);
  const [error, setError] = useState("");

  const performSearch = useCallback(
    async (nextQuery) => {
      if (!liveApi) {
        setResults(searchResults);
        return;
      }

      setLoading(true);
      setError("");
      try {
        const response = await api.search.query({ query: nextQuery });
        setSearchId(response.search_id);
        setResults(response.results.map(toSearchResultViewModel));
      } catch (searchError) {
        setResults([]);
        setError(searchError.message);
      } finally {
        setLoading(false);
      }
    },
    [liveApi],
  );

  useEffect(() => {
    performSearch(submittedQuery);
  }, [performSearch, submittedQuery]);

  const submitSearch = (event) => {
    event.preventDefault();
    const normalizedQuery = query.trim();
    if (!normalizedQuery) return;
    setSubmittedQuery(normalizedQuery);
    setSearchParams({ q: normalizedQuery });
  };

  return (
    <>
      <div className="search-hero">
        <span className="eyebrow">
          <Sparkles size={13} /> Purpose search
        </span>
        <h1>What are you looking for?</h1>
        <p>Describe an idea, a visual, or a spoken moment. Natural language works best.</p>
        <form className="purpose-search" role="search" onSubmit={submitSearch}>
          <Search size={21} aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Describe the media moment you need"
          />
          {query && (
            <button type="button" aria-label="Clear search" onClick={() => setQuery("")}>
              <X size={17} />
            </button>
          )}
          <button className="button primary" type="submit">
            Search
          </button>
        </form>
        <div className="search-suggestions">
          <span>Try:</span>
          <button type="button" onClick={() => setQuery("a clear product demonstration")}>
            a clear product demonstration
          </button>
          <button type="button" onClick={() => setQuery("founder speaking directly to camera")}>
            founder speaking to camera
          </button>
        </div>
      </div>

      <section className="search-results-section">
        <div className="results-heading">
          <div>
            <span className="eyebrow">
              {loading ? "Searching live library" : `${results.length} moments found`}
            </span>
            <h2>
              Best matches for <q>{submittedQuery}</q>
            </h2>
          </div>
          <button
            className={`filter-button ${filterOpen ? "active" : ""}`}
            type="button"
            onClick={() => setFilterOpen((current) => !current)}
          >
            <Filter size={16} /> Refine
          </button>
        </div>

        {filterOpen && (
          <div className="filter-drawer">
            {["Video", "Google Drive", "Testimonial", "Ready"].map((filter) => (
              <button key={filter} type="button">
                {filter} <X size={13} />
              </button>
            ))}
            <span>AI interpreted 4 filters from your query.</span>
          </div>
        )}

        {error && (
          <div className="request-state error-state" role="alert">
            <div>
              <strong>Search is temporarily unavailable</strong>
              <p>{error}</p>
            </div>
            <button className="button secondary small" type="button" onClick={() => performSearch(submittedQuery)}>
              Try again
            </button>
          </div>
        )}

        {loading && (
          <div className="request-state loading-state" aria-live="polite">
            <span className="loading-spinner" />
            <div>
              <strong>Searching transcripts and moments</strong>
              <p>Asking the live organization-scoped API for the strongest matches…</p>
            </div>
          </div>
        )}

        {!loading && !error && results.length === 0 && (
          <div className="empty-state search-empty-state">
            <Search size={24} />
            <h2>No live moments matched</h2>
            <p>Try a shorter phrase such as “easy onboarding” or “customer story.”</p>
          </div>
        )}

        {!loading && !error && results.length > 0 && (
          <div className="search-result-list" data-search-id={searchId ?? undefined}>
            {results.map((result, index) => (
            <article className="search-result-card" key={result.id}>
              <div
                className="result-preview"
                style={{
                  "--art-color-a": result.colors[0],
                  "--art-color-b": result.colors[1],
                }}
              >
                <span className="result-rank">0{index + 1}</span>
                <span className="result-play">
                  <Play size={17} fill="currentColor" />
                </span>
                <span className="result-timestamp">{result.timestamp}</span>
              </div>
              <div className="result-copy">
                <div className="result-title-row">
                  <div>
                    <span className="result-asset">{result.assetName}</span>
                    <h3>{result.title}</h3>
                  </div>
                  <span className="match-score">{Math.round(result.score * 100)}% match</span>
                </div>
                <blockquote>“{result.excerpt}”</blockquote>
                <div className="match-reason">
                  <Sparkles size={15} />
                  <span>
                    <strong>Why it matched:</strong> {result.reason}
                  </span>
                </div>
                <div className="result-actions">
                  <Link
                    className="button secondary small"
                    to={`/library/assets/${result.assetId}?start=${result.startMs}`}
                  >
                    <Play size={14} /> Open at {result.timestamp}
                  </Link>
                  <button className="button ghost small" type="button">
                    <FolderHeart size={15} /> Save
                  </button>
                  <div className="feedback-controls" aria-label="Rate this result">
                    <button type="button" aria-label="Relevant">
                      <ThumbsUp size={14} />
                    </button>
                    <button type="button" aria-label="Not relevant">
                      <ThumbsDown size={14} />
                    </button>
                  </div>
                </div>
              </div>
            </article>
            ))}
          </div>
        )}
      </section>
    </>
  );
};

export const AssetPage = () => {
  const { assetId } = useParams();
  const [assetSearchParams] = useSearchParams();
  const requestedStartParam = assetSearchParams.get("start");
  const requestedStart = requestedStartParam === null ? Number.NaN : Number(requestedStartParam);
  const demoAsset = assets.find((item) => item.id === assetId) ?? assets[0];
  const liveApi = import.meta.env.VITE_DEMO_MODE === "false";
  const [asset, setAsset] = useState(liveApi ? null : demoAsset);
  const [assetTranscript, setAssetTranscript] = useState(liveApi ? [] : transcript);
  const [assetMoments, setAssetMoments] = useState(liveApi ? [] : moments);
  const [activeStart, setActiveStart] = useState(
    Number.isFinite(requestedStart) && requestedStart >= 0 ? requestedStart : 31_000,
  );
  const [tab, setTab] = useState("transcript");
  const [loading, setLoading] = useState(liveApi);
  const [error, setError] = useState("");
  const playerRef = useRef(null);

  const loadAsset = useCallback(async () => {
    if (!liveApi) {
      setAsset(demoAsset);
      setAssetTranscript(transcript);
      setAssetMoments(moments);
      return;
    }

    setLoading(true);
    setError("");
    try {
      const [assetResponse, transcriptResponse, momentsResponse] = await Promise.all([
        api.assets.get(assetId),
        api.assets.transcript(assetId),
        api.assets.moments(assetId),
      ]);
      const mappedMoments = momentsResponse.map(toMomentViewModel);
      const mappedAsset = toAssetViewModel(assetResponse);
      setAsset({
        ...mappedAsset,
        moments: mappedMoments.length,
        description:
          mappedMoments.length > 0
            ? `Live analysis identified ${mappedMoments.length} purposeful moments, including “${mappedMoments[0].title}.”`
            : mappedAsset.description,
      });
      setAssetTranscript(transcriptResponse.map(toTranscriptViewModel));
      setAssetMoments(mappedMoments);
      if (!(Number.isFinite(requestedStart) && requestedStart >= 0) && mappedMoments[0]) {
        setActiveStart(mappedMoments[0].startMs);
      }
    } catch (assetError) {
      setError(assetError.message);
    } finally {
      setLoading(false);
    }
  }, [assetId, demoAsset, liveApi, requestedStart]);

  useEffect(() => {
    loadAsset();
  }, [loadAsset]);

  const seek = (startMs) => {
    setActiveStart(startMs);
    playerRef.current?.focus();
  };

  if (loading) {
    return (
      <>
        <Link className="back-link" to="/library">
          <ArrowLeft size={16} /> Back to library
        </Link>
        <div className="request-state loading-state asset-request-state" aria-live="polite">
          <span className="loading-spinner" />
          <div>
            <strong>Loading live asset analysis</strong>
            <p>Fetching metadata, transcript segments, and purposeful moments…</p>
          </div>
        </div>
      </>
    );
  }

  if (error || !asset) {
    return (
      <>
        <Link className="back-link" to="/library">
          <ArrowLeft size={16} /> Back to library
        </Link>
        <div className="request-state error-state asset-request-state" role="alert">
          <div>
            <strong>We could not load this asset</strong>
            <p>{error || "The live API returned no asset."}</p>
          </div>
          <button className="button secondary small" type="button" onClick={loadAsset}>
            Try again
          </button>
        </div>
      </>
    );
  }

  return (
    <>
      <Link className="back-link" to="/library">
        <ArrowLeft size={16} /> Back to library
      </Link>
      <div className="asset-heading">
        <div>
          <div className="asset-title-line">
            <h1>{asset.name}</h1>
            <StatusPill status={asset.status} />
          </div>
          <p>
            {asset.fileName} · {asset.duration} · {asset.size}
          </p>
        </div>
        <div className="asset-actions">
          <button className="button secondary" type="button">
            <Download size={16} /> Export clip
          </button>
          <button className="icon-button bordered" type="button" aria-label="More asset options">
            <MoreHorizontal size={18} />
          </button>
        </div>
      </div>

      <div className="asset-layout">
        <section className="player-column">
          <div className="video-player" tabIndex="-1" ref={playerRef}>
            <MediaArtwork asset={asset} className="player-artwork" />
            <button className="player-main-button" type="button" aria-label="Play video">
              <Play size={23} fill="currentColor" />
            </button>
            <div className="player-controls">
              <button type="button" aria-label="Play">
                <Play size={15} fill="currentColor" />
              </button>
              <span>{formatTimestamp(activeStart)}</span>
              <div className="player-track">
                <span
                  style={{
                    width: `${Math.min(
                      100,
                      Math.max(4, (activeStart / (asset.durationMs || 18.7 * 60_000)) * 100),
                    )}%`,
                  }}
                />
              </div>
              <span>{asset.duration}</span>
            </div>
          </div>

          <div className="asset-summary">
            <div>
              <span className="eyebrow">AI summary</span>
              <p>{asset.description}</p>
            </div>
            <div className="summary-tags">
              <span>Customer story</span>
              <span>Onboarding</span>
              <span>Testimonial</span>
            </div>
          </div>

          <section className="moment-section">
            <div className="section-heading-row">
              <div>
                <span className="eyebrow">Purposeful moments</span>
                <h2>Best parts of this video</h2>
              </div>
              <span>{assetMoments.length} live moments</span>
            </div>
            <div className="moment-list">
              {assetMoments.map((moment) => (
                <button className="moment-card" type="button" key={moment.id} onClick={() => seek(moment.startMs)}>
                  <span className="moment-play">
                    <Play size={14} fill="currentColor" />
                  </span>
                  <span className="moment-copy">
                    <strong>{moment.title}</strong>
                    <small>
                      {moment.time} · {moment.type}
                    </small>
                  </span>
                  <span className="moment-score">{moment.score}%</span>
                  <ArrowRight size={16} />
                </button>
              ))}
            </div>
          </section>
        </section>

        <aside className="transcript-panel">
          <div className="panel-tabs">
            <button
              className={tab === "transcript" ? "active" : ""}
              type="button"
              onClick={() => setTab("transcript")}
            >
              Transcript
            </button>
            <button
              className={tab === "details" ? "active" : ""}
              type="button"
              onClick={() => setTab("details")}
            >
              Details
            </button>
          </div>

          {tab === "transcript" ? (
            <>
              <label className="transcript-search">
                <Search size={15} />
                <input placeholder="Search transcript..." aria-label="Search transcript" />
              </label>
              <div className="transcript-list">
                {assetTranscript.map((segment) => (
                  <button
                    className={`transcript-segment ${
                      activeStart === segment.startMs ? "active" : ""
                    }`}
                    type="button"
                    key={segment.id ?? segment.startMs}
                    onClick={() => seek(segment.startMs)}
                  >
                    <span className="transcript-time">{segment.time}</span>
                    <span>
                      <strong>{segment.speaker}</strong>
                      <p>{segment.text}</p>
                    </span>
                  </button>
                ))}
              </div>
            </>
          ) : (
            <dl className="details-list">
              <div>
                <dt>Source</dt>
                <dd>{asset.source}</dd>
              </div>
              <div>
                <dt>Imported</dt>
                <dd>{asset.uploadedAt}</dd>
              </div>
              <div>
                <dt>File size</dt>
                <dd>{asset.size}</dd>
              </div>
              <div>
                <dt>Resolution</dt>
                <dd>{asset.width && asset.height ? `${asset.width} × ${asset.height}` : "Pending"}</dd>
              </div>
              <div>
                <dt>Language</dt>
                <dd>English</dd>
              </div>
            </dl>
          )}
        </aside>
      </div>
    </>
  );
};

export const CollectionsPage = () => {
  const [items, setItems] = useState(initialCollections);
  const [creating, setCreating] = useState(false);

  const createCollection = (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const name = data.get("name");
    if (!name) return;
    setItems((current) => [
      {
        id: name.toLowerCase().replace(/\s+/g, "-"),
        name,
        description: "A new collection ready for useful moments.",
        itemCount: 0,
        updatedAt: "Just now",
        colors: ["#314b50", "#d1ddd8", "#e9dfd0"],
      },
      ...current,
    ]);
    setCreating(false);
  };

  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">Curated workspaces</span>
          <h1>Collections</h1>
          <p>Turn found moments into stories, campaigns, and ready-to-use selects.</p>
        </div>
        <button className="button primary" type="button" onClick={() => setCreating(true)}>
          <Plus size={17} /> New collection
        </button>
      </div>

      <section className="collection-grid">
        {creating && (
          <form className="collection-card new-collection-card" onSubmit={createCollection}>
            <span className="collection-plus">
              <FolderHeart size={23} />
            </span>
            <input name="name" placeholder="Collection name" aria-label="Collection name" autoFocus />
            <div>
              <button className="button primary small" type="submit">
                Create
              </button>
              <button className="button ghost small" type="button" onClick={() => setCreating(false)}>
                Cancel
              </button>
            </div>
          </form>
        )}
        {items.map((collection) => (
          <article className="collection-card" key={collection.id}>
            <div className="collection-covers">
              {collection.colors.map((color) => (
                <span key={color} style={{ background: color }} />
              ))}
            </div>
            <div className="collection-card-copy">
              <div>
                <h2>{collection.name}</h2>
                <button className="icon-button" type="button" aria-label="Collection options">
                  <MoreHorizontal size={17} />
                </button>
              </div>
              <p>{collection.description}</p>
              <span>
                {collection.itemCount} moments · Updated {collection.updatedAt}
              </span>
            </div>
          </article>
        ))}
      </section>
    </>
  );
};

export const TeamPage = () => {
  const [members, setMembers] = useState(teamMembers);
  const [inviteOpen, setInviteOpen] = useState(false);

  const invite = (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const email = data.get("email");
    const role = data.get("role");
    if (!email) return;
    setMembers((current) => [
      ...current,
      {
        id: Date.now(),
        name: "Invitation pending",
        email,
        role,
        initials: email.slice(0, 2).toUpperCase(),
      },
    ]);
    setInviteOpen(false);
  };

  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">Workspace settings</span>
          <h1>Team access</h1>
          <p>Invite collaborators and choose what they can manage.</p>
        </div>
        <button className="button primary" type="button" onClick={() => setInviteOpen(true)}>
          <UserPlus size={17} /> Invite member
        </button>
      </div>

      <section className="settings-card">
        <div className="settings-card-heading">
          <div>
            <h2>Northstar Studio</h2>
            <p>{members.length} members · JWT-protected organization access</p>
          </div>
          <span className="plan-badge">Pilot plan</span>
        </div>
        <div className="member-list">
          {members.map((member) => (
            <div className="member-row" key={member.id}>
              <span className="member-avatar">{member.initials}</span>
              <span className="member-identity">
                <strong>{member.name}</strong>
                <small>{member.email}</small>
              </span>
              <button className="role-select" type="button">
                {member.role} <ChevronDown size={14} />
              </button>
              <button className="icon-button" type="button" aria-label={`Options for ${member.name}`}>
                <MoreHorizontal size={18} />
              </button>
            </div>
          ))}
        </div>
      </section>

      <section className="permission-note">
        <ShieldCheck size={19} />
        <div>
          <strong>Organization isolation is enforced by the API</strong>
          <p>
            The frontend sends the JWT with each request. The Express API must verify membership
            before querying Postgres through Knex.
          </p>
        </div>
      </section>

      {inviteOpen && (
        <div className="dialog-backdrop" role="presentation" onMouseDown={() => setInviteOpen(false)}>
          <form
            className="invite-dialog"
            onSubmit={invite}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="dialog-heading">
              <div>
                <span className="eyebrow">Team access</span>
                <h2>Invite a collaborator</h2>
              </div>
              <button
                className="icon-button"
                type="button"
                aria-label="Close"
                onClick={() => setInviteOpen(false)}
              >
                <X size={18} />
              </button>
            </div>
            <label>
              Email address
              <input name="email" type="email" placeholder="name@company.com" required autoFocus />
            </label>
            <label>
              Role
              <select name="role" defaultValue="Member">
                <option>Admin</option>
                <option>Member</option>
                <option>Viewer</option>
              </select>
            </label>
            <div className="dialog-actions">
              <button className="button secondary" type="button" onClick={() => setInviteOpen(false)}>
                Cancel
              </button>
              <button className="button primary" type="submit">
                Send invitation
              </button>
            </div>
          </form>
        </div>
      )}
    </>
  );
};
