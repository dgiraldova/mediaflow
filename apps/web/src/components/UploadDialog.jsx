import { CheckCircle2, FileVideo, UploadCloud, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

const mediaTypeForFile = (file) => {
  if (file.type.startsWith("image/")) return "image";
  if (file.type.startsWith("audio/")) return "audio";
  return "video";
};

export const UploadDialog = ({
  open,
  onClose,
  onUploaded,
  liveApi = false,
  organizationId = "demo-org",
}) => {
  const [file, setFile] = useState(null);
  const [progress, setProgress] = useState(0);
  const [complete, setComplete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [uploadSession, setUploadSession] = useState(null);
  const timerRef = useRef(null);

  useEffect(
    () => () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    },
    [],
  );

  if (!open) return null;

  const selectFile = (selectedFile) => {
    if (!selectedFile) return;
    setFile(selectedFile);
    setProgress(0);
    setComplete(false);
    setBusy(false);
    setError("");
    setUploadSession(null);
  };

  const beginDemoUpload = () => {
    setBusy(true);
    timerRef.current = window.setInterval(() => {
      setProgress((current) => {
        const next = Math.min(current + 8, 100);
        if (next === 100) {
          window.clearInterval(timerRef.current);
          timerRef.current = null;
          setComplete(true);
          setBusy(false);
        }
        return next;
      });
    }, 150);
  };

  const beginUpload = async () => {
    if (!file || busy || timerRef.current) return;
    setError("");

    if (!liveApi) {
      beginDemoUpload();
      return;
    }

    setBusy(true);
    setProgress((current) => Math.max(current, 10));

    try {
      let session = uploadSession;
      if (!session) {
        session = await api.uploads.initiate({
          organization_id: organizationId,
          original_filename: file.name,
          media_type: mediaTypeForFile(file),
        });
        setUploadSession(session);
      }

      setProgress(65);
      const completedUpload = await api.uploads.complete(session.upload_id, {
        byte_size: file.size,
      });
      setProgress(100);
      setComplete(true);
      onUploaded?.({ ...completedUpload, file });
    } catch (uploadError) {
      setError(uploadError.message);
    } finally {
      setBusy(false);
    }
  };

  const resetAndClose = () => {
    if (busy) return;
    if (timerRef.current) window.clearInterval(timerRef.current);
    timerRef.current = null;
    if (liveApi && uploadSession && !complete) {
      void api.uploads.abort(uploadSession.upload_id).catch(() => {});
    }
    setFile(null);
    setProgress(0);
    setComplete(false);
    setBusy(false);
    setError("");
    setUploadSession(null);
    onClose();
  };

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={resetAndClose}>
      <section
        className="upload-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="upload-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog-heading">
          <div>
            <span className="eyebrow">Add to your library</span>
            <h2 id="upload-title">Upload media</h2>
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label="Close"
            onClick={resetAndClose}
            disabled={busy}
          >
            <X size={19} />
          </button>
        </div>

        {!file ? (
          <label className="drop-zone">
            <UploadCloud size={30} strokeWidth={1.5} aria-hidden="true" />
            <strong>Drop video, images, or audio here</strong>
            <span>or choose files from your computer</span>
            <small>MP4, MOV, JPG, PNG, MP3, WAV · up to 10 GB</small>
            <input
              type="file"
              accept="video/*,image/*,audio/*"
              onChange={(event) => selectFile(event.target.files?.[0])}
            />
          </label>
        ) : (
          <div className="upload-file">
            <span className={`upload-file-icon ${complete ? "done" : ""}`}>
              {complete ? <CheckCircle2 size={24} /> : <FileVideo size={24} />}
            </span>
            <div className="upload-file-copy">
              <strong>{file.name}</strong>
              <span>
                {complete
                  ? liveApi
                    ? "Upload registered — analysis queued"
                    : "Upload complete — ready for analysis"
                  : busy
                    ? "Registering with MediaFlow..."
                    : "Ready to upload"}
              </span>
              <div className="progress-track" aria-label={`Upload ${progress}% complete`}>
                <span style={{ width: `${progress}%` }} />
              </div>
            </div>
            <span className="progress-value">{progress}%</span>
          </div>
        )}

        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}

        <div className="dialog-note">
          {liveApi
            ? "The live API registers this file and queues it for analysis using its secure storage key."
            : "Media uploads directly to secure object storage. It never passes through the web server."}
        </div>

        <div className="dialog-actions">
          <button className="button secondary" type="button" onClick={resetAndClose} disabled={busy}>
            Cancel
          </button>
          <button
            className="button primary"
            type="button"
            disabled={!file || busy}
            onClick={complete ? resetAndClose : beginUpload}
          >
            {complete ? "Done" : busy ? "Uploading..." : error ? "Try again" : "Start upload"}
          </button>
        </div>
      </section>
    </div>
  );
};
