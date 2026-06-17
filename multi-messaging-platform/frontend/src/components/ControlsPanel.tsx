import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  getAccountDelay,
  getKillSwitch,
  setAccountDelay,
  setKillSwitch,
} from "../api";
import type { AccountDelayStatus } from "../types";

const MIN_DELAY = 1;
const MAX_DELAY = 3600;

interface ControlsPanelProps {
  wsKillSwitchEnabled?: boolean;
}

type FeedbackKind = "success" | "error" | "info";

interface FeedbackMessage {
  kind: FeedbackKind;
  text: string;
}

function validateAccountId(value: string): number | null {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return null;
  }
  return parsed;
}

function validateDelaySeconds(value: string): number | null {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < MIN_DELAY || parsed > MAX_DELAY) {
    return null;
  }
  return parsed;
}

export function ControlsPanel({ wsKillSwitchEnabled }: ControlsPanelProps) {
  const [killSwitchEnabled, setKillSwitchEnabled] = useState(false);
  const [accountIdInput, setAccountIdInput] = useState("1");
  const [delayInput, setDelayInput] = useState("30");
  const [loadedDelay, setLoadedDelay] = useState<AccountDelayStatus | null>(null);
  const [feedback, setFeedback] = useState<FeedbackMessage | null>(null);
  const [busy, setBusy] = useState(false);

  const showFeedback = useCallback((kind: FeedbackKind, text: string) => {
    setFeedback({ kind, text });
  }, []);

  const refreshKillSwitch = useCallback(async () => {
    const status = await getKillSwitch();
    setKillSwitchEnabled(status.enabled);
    return status;
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        await refreshKillSwitch();
        showFeedback("info", "Controls loaded from API.");
      } catch (error) {
        const message =
          error instanceof ApiError
            ? error.message
            : error instanceof Error
              ? error.message
              : "Failed to load kill switch status.";
        showFeedback("error", message);
      }
    })();
  }, [refreshKillSwitch, showFeedback]);

  useEffect(() => {
    if (wsKillSwitchEnabled !== undefined) {
      setKillSwitchEnabled(wsKillSwitchEnabled);
    }
  }, [wsKillSwitchEnabled]);

  const handleEnableKillSwitch = async () => {
    const confirmed = window.confirm(
      "Are you sure you want to enable Kill Switch?",
    );
    if (!confirmed) return;

    setBusy(true);
    try {
      const result = await setKillSwitch(true);
      setKillSwitchEnabled(result.enabled);
      await refreshKillSwitch();
      showFeedback("success", "Kill Switch enabled. SYSTEM PAUSED.");
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : error instanceof Error
            ? error.message
            : "Failed to enable Kill Switch.";
      showFeedback("error", message);
    } finally {
      setBusy(false);
    }
  };

  const handleDisableKillSwitch = async () => {
    setBusy(true);
    try {
      const result = await setKillSwitch(false);
      setKillSwitchEnabled(result.enabled);
      await refreshKillSwitch();
      showFeedback("success", "Kill Switch disabled.");
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : error instanceof Error
            ? error.message
            : "Failed to disable Kill Switch.";
      showFeedback("error", message);
    } finally {
      setBusy(false);
    }
  };

  const handleLoadDelay = async () => {
    const accountId = validateAccountId(accountIdInput);
    if (accountId === null) {
      showFeedback("error", "account_id must be a positive integer.");
      return;
    }

    setBusy(true);
    try {
      const status = await getAccountDelay(accountId);
      setLoadedDelay(status);
      setDelayInput(String(status.delay_seconds));
      showFeedback(
        "success",
        `Loaded delay for account ${accountId}: ${status.delay_seconds}s (${status.source}).`,
      );
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : error instanceof Error
            ? error.message
            : "Failed to load account delay.";
      showFeedback("error", message);
    } finally {
      setBusy(false);
    }
  };

  const handleSaveDelay = async () => {
    const accountId = validateAccountId(accountIdInput);
    if (accountId === null) {
      showFeedback("error", "account_id must be a positive integer.");
      return;
    }

    const delaySeconds = validateDelaySeconds(delayInput);
    if (delaySeconds === null) {
      showFeedback(
        "error",
        `delay_seconds must be an integer between ${MIN_DELAY} and ${MAX_DELAY}.`,
      );
      return;
    }

    setBusy(true);
    try {
      const result = await setAccountDelay(accountId, delaySeconds);
      setLoadedDelay({
        account_id: result.account_id,
        delay_seconds: result.delay_seconds,
        redis_key: result.redis_key,
        source: "redis",
      });
      setDelayInput(String(result.delay_seconds));
      showFeedback(
        "success",
        `Saved delay for account ${result.account_id}: ${result.delay_seconds}s.`,
      );
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : error instanceof Error
            ? error.message
            : "Failed to save account delay.";
      showFeedback("error", message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="controls-panel">
      <div
        className={`controls-card kill-switch-card ${
          killSwitchEnabled ? "kill-switch-card--active" : ""
        }`}
      >
        <h3>Kill Switch</h3>
        {killSwitchEnabled ? (
          <div className="kill-switch-alert" role="alert">
            SYSTEM PAUSED / KILL SWITCH ENABLED
          </div>
        ) : null}
        <div className="controls-row">
          <span className="controls-label">Current status</span>
          <span
            className={`controls-status ${
              killSwitchEnabled ? "controls-status--danger" : "controls-status--ok"
            }`}
          >
            {killSwitchEnabled ? "Enabled" : "Disabled"}
          </span>
        </div>
        <div className="controls-actions">
          <button
            type="button"
            className="btn btn--danger"
            disabled={busy || killSwitchEnabled}
            onClick={() => void handleEnableKillSwitch()}
          >
            Enable Kill Switch
          </button>
          <button
            type="button"
            className="btn btn--secondary"
            disabled={busy || !killSwitchEnabled}
            onClick={() => void handleDisableKillSwitch()}
          >
            Disable Kill Switch
          </button>
        </div>
      </div>

      <div className="controls-card">
        <h3>Account Delay</h3>
        <div className="controls-form">
          <label className="field">
            <span className="field__label">account_id</span>
            <input
              className="field__input"
              type="number"
              min={1}
              step={1}
              value={accountIdInput}
              onChange={(event) => setAccountIdInput(event.target.value)}
            />
          </label>
          <label className="field">
            <span className="field__label">delay_seconds (1–3600)</span>
            <input
              className="field__input"
              type="number"
              min={MIN_DELAY}
              max={MAX_DELAY}
              step={1}
              value={delayInput}
              onChange={(event) => setDelayInput(event.target.value)}
            />
          </label>
        </div>
        <div className="controls-actions">
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy}
            onClick={() => void handleLoadDelay()}
          >
            Load Delay
          </button>
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy}
            onClick={() => void handleSaveDelay()}
          >
            Save Delay
          </button>
        </div>
        <div className="controls-row">
          <span className="controls-label">Current loaded delay</span>
          <span>
            {loadedDelay
              ? `${loadedDelay.delay_seconds}s (${loadedDelay.source})`
              : "—"}
          </span>
        </div>
      </div>

      <div
        className={`feedback-banner ${
          feedback
            ? feedback.kind === "success"
              ? "feedback-banner--success"
              : feedback.kind === "error"
                ? "feedback-banner--error"
                : "feedback-banner--info"
            : "feedback-banner--hidden"
        }`}
        role="status"
      >
        {feedback?.text ?? "No messages yet."}
      </div>
    </div>
  );
}
