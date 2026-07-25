import { AlertCircle, Check, Clock3, LoaderCircle } from "lucide-react";

const statusConfig = {
  ready: { label: "Ready", icon: Check },
  processing: { label: "Analyzing", icon: LoaderCircle },
  uploading: { label: "Uploading", icon: LoaderCircle },
  pending: { label: "Queued", icon: Clock3 },
  failed: { label: "Needs attention", icon: AlertCircle },
};

export const StatusPill = ({ status, compact = false }) => {
  const config = statusConfig[status] ?? statusConfig.pending;
  const Icon = config.icon;

  return (
    <span className={`status-pill status-${status} ${compact ? "compact" : ""}`}>
      <Icon size={compact ? 12 : 13} aria-hidden="true" />
      {config.label}
    </span>
  );
};
