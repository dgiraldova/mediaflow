import { CheckCircle2, FileVideo, UploadCloud, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export const UploadDialog = ({ open, onClose }) => {
  const [file, setFile] = useState(null);
  const [progress, setProgress] = useState(0);
  const [complete, setComplete] = useState(false);
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
  };

  const beginUpload = () => {
    if (!file || timerRef.current) return;
    timerRef.current = window.setInterval(() => {
      setProgress((current) => {
        const next = Math.min(current + 8, 100);
        if (next === 100) {
          window.clearInterval(timerRef.current);
          timerRef.current = null;
          setComplete(true);
        }
        return next;
      });
    }, 150);
  };

  const resetAndClose = () => {
    if (timerRef.current) window.clearInterval(timerRef.current);
    timerRef.current = null;
    setFile(null);
    setProgress(0);
    setComplete(false);
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
          <button className="icon-button" type="button" aria-label="Close" onClick={resetAndClose}>
            <X size={19} />
          </button>
        </div>

        {!file ? (
          <label className="drop-zone">
            <UploadCloud size={30} strokeWidth={1.5} aria-hidden="true" />
            <strong>Drop video or images here</strong>
            <span>or choose files from your computer</span>
            <small>MP4, MOV, JPG, PNG · up to 10 GB</small>
            <input
              type="file"
              accept="video/*,image/*"
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
              <span>{complete ? "Upload complete — ready for analysis" : "Ready to upload"}</span>
              <div className="progress-track" aria-label={`Upload ${progress}% complete`}>
                <span style={{ width: `${progress}%` }} />
              </div>
            </div>
            <span className="progress-value">{progress}%</span>
          </div>
        )}

        <div className="dialog-note">
          Media uploads directly to secure object storage. It never passes through the web server.
        </div>

        <div className="dialog-actions">
          <button className="button secondary" type="button" onClick={resetAndClose}>
            Cancel
          </button>
          <button
            className="button primary"
            type="button"
            disabled={!file || (progress > 0 && !complete)}
            onClick={complete ? resetAndClose : beginUpload}
          >
            {complete ? "Done" : progress > 0 ? "Uploading..." : "Start upload"}
          </button>
        </div>
      </section>
    </div>
  );
};
