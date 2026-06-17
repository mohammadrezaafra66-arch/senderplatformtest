import type { WsConnectionState } from "../types";

interface RealtimePanelProps {
  connectionState: WsConnectionState;
  lastTimestamp: string | null;
  warnings: string[];
  restError: string | null;
}

const STATE_LABELS: Record<WsConnectionState, string> = {
  connecting: "Connecting",
  connected: "Connected",
  disconnected: "Disconnected",
  error: "Error",
};

export function RealtimePanel({
  connectionState,
  lastTimestamp,
  warnings,
  restError,
}: RealtimePanelProps) {
  return (
    <div className="realtime-panel">
      <div className="realtime-panel__row">
        <span className="realtime-panel__label">WebSocket</span>
        <span className={`ws-badge ws-badge--${connectionState}`}>
          {STATE_LABELS[connectionState]}
        </span>
      </div>
      <div className="realtime-panel__row">
        <span className="realtime-panel__label">Last snapshot</span>
        <span>{lastTimestamp ?? "—"}</span>
      </div>
      <div className="realtime-panel__row realtime-panel__row--stack">
        <span className="realtime-panel__label">Warnings</span>
        {warnings.length === 0 ? (
          <span className="muted">No warnings</span>
        ) : (
          <ul className="warning-list">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        )}
      </div>
      {restError ? (
        <div className="error-banner" role="alert">
          REST load error: {restError}
        </div>
      ) : null}
    </div>
  );
}
