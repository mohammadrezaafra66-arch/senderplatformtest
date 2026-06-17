import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchDashboardSummary,
  fetchQueuesStatus,
  fetchWorkersStatus,
  WS_URL,
} from "./api";
import { ControlsPanel } from "./components/ControlsPanel";
import { QueueTable } from "./components/QueueTable";
import { RealtimePanel } from "./components/RealtimePanel";
import { StatCard, StatGrid } from "./components/StatCard";
import { WorkerTable } from "./components/WorkerTable";
import type {
  DashboardSnapshot,
  DashboardSummary,
  QueueItem,
  WorkerItem,
  WsConnectionState,
} from "./types";
import "./styles.css";

const EMPTY_SUMMARY: DashboardSummary = {
  campaigns_total: 0,
  campaigns_running: 0,
  campaigns_paused: 0,
  messages_total: 0,
  messages_sent: 0,
  messages_failed: 0,
  accounts_total: 0,
  accounts_active: 0,
  accounts_banned: 0,
};

const RECONNECT_DELAY_MS = 5000;

function applySnapshot(
  snapshot: DashboardSnapshot,
  setSummary: (s: DashboardSummary) => void,
  setQueues: (q: QueueItem[]) => void,
  setWorkers: (w: WorkerItem[]) => void,
  setLastTimestamp: (t: string) => void,
  setWarnings: (w: string[]) => void,
  setWsKillSwitchEnabled?: (enabled: boolean) => void,
) {
  setSummary(snapshot.summary);
  setQueues(snapshot.queues);
  setWorkers(snapshot.workers);
  setLastTimestamp(snapshot.timestamp);
  setWarnings(snapshot.warnings ?? []);
  if (
    setWsKillSwitchEnabled &&
    snapshot.controls?.kill_switch_enabled !== undefined
  ) {
    setWsKillSwitchEnabled(snapshot.controls.kill_switch_enabled);
  }
}

export default function App() {
  const [summary, setSummary] = useState<DashboardSummary>(EMPTY_SUMMARY);
  const [queues, setQueues] = useState<QueueItem[]>([]);
  const [workers, setWorkers] = useState<WorkerItem[]>([]);
  const [lastTimestamp, setLastTimestamp] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [wsState, setWsState] = useState<WsConnectionState>("connecting");
  const [restError, setRestError] = useState<string | null>(null);
  const [wsKillSwitchEnabled, setWsKillSwitchEnabled] = useState<
    boolean | undefined
  >(undefined);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const unmountedRef = useRef(false);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const scheduleReconnect = useCallback(
    (connect: () => void) => {
      clearReconnectTimer();
      if (unmountedRef.current) return;
      reconnectTimerRef.current = window.setTimeout(connect, RECONNECT_DELAY_MS);
    },
    [clearReconnectTimer],
  );

  const connectWebSocket = useCallback(() => {
    if (unmountedRef.current) return;

    if (wsRef.current) {
      wsRef.current.onopen = null;
      wsRef.current.onmessage = null;
      wsRef.current.onerror = null;
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }

    setWsState("connecting");

    try {
      const socket = new WebSocket(WS_URL);
      wsRef.current = socket;

      socket.onopen = () => {
        if (unmountedRef.current) return;
        setWsState("connected");
        clearReconnectTimer();
      };

      socket.onmessage = (event) => {
        if (unmountedRef.current) return;
        try {
          const payload = JSON.parse(event.data as string) as DashboardSnapshot;
          if (payload.type !== "dashboard_snapshot") return;
          applySnapshot(
            payload,
            setSummary,
            setQueues,
            setWorkers,
            setLastTimestamp,
            setWarnings,
            setWsKillSwitchEnabled,
          );
          setRestError(null);
        } catch {
          setWsState("error");
        }
      };

      socket.onerror = () => {
        if (unmountedRef.current) return;
        setWsState("error");
      };

      socket.onclose = () => {
        if (unmountedRef.current) return;
        wsRef.current = null;
        setWsState("disconnected");
        scheduleReconnect(connectWebSocket);
      };
    } catch {
      setWsState("error");
      scheduleReconnect(connectWebSocket);
    }
  }, [clearReconnectTimer, scheduleReconnect]);

  const loadRestData = useCallback(async () => {
    try {
      const [summaryData, queuesData, workersData] = await Promise.all([
        fetchDashboardSummary(),
        fetchQueuesStatus(),
        fetchWorkersStatus(),
      ]);
      if (unmountedRef.current) return;
      setSummary(summaryData);
      setQueues(queuesData.queues);
      setWorkers(workersData.workers);
      setRestError(null);
    } catch (error) {
      if (unmountedRef.current) return;
      setRestError(error instanceof Error ? error.message : "Unknown error");
    }
  }, []);

  useEffect(() => {
    unmountedRef.current = false;
    void loadRestData();
    connectWebSocket();

    return () => {
      unmountedRef.current = true;
      clearReconnectTimer();
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [clearReconnectTimer, connectWebSocket, loadRestData]);

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>Multi Messaging Platform Dashboard</h1>
          <p className="subtitle">Monitoring & operational controls — Phase 5</p>
        </div>
        <span className={`ws-badge ws-badge--${wsState}`}>
          {wsState === "connecting"
            ? "Connecting"
            : wsState === "connected"
              ? "Connected"
              : wsState === "disconnected"
                ? "Disconnected"
                : "Error"}
        </span>
      </header>

      <main className="layout">
        <section className="panel">
          <h2>Summary</h2>
          <StatGrid>
            <StatCard label="Campaigns total" value={summary.campaigns_total} />
            <StatCard label="Campaigns running" value={summary.campaigns_running} />
            <StatCard label="Campaigns paused" value={summary.campaigns_paused} />
            <StatCard label="Messages total" value={summary.messages_total} />
            <StatCard label="Messages sent" value={summary.messages_sent} />
            <StatCard label="Messages failed" value={summary.messages_failed} />
            <StatCard label="Accounts total" value={summary.accounts_total} />
            <StatCard label="Accounts active" value={summary.accounts_active} />
            <StatCard label="Accounts banned" value={summary.accounts_banned} />
          </StatGrid>
        </section>

        <section className="panel">
          <h2>Queue status</h2>
          <QueueTable queues={queues} />
        </section>

        <section className="panel">
          <h2>Worker status</h2>
          <WorkerTable workers={workers} />
        </section>

        <section className="panel">
          <h2>Realtime</h2>
          <RealtimePanel
            connectionState={wsState}
            lastTimestamp={lastTimestamp}
            warnings={warnings}
            restError={restError}
          />
        </section>

        <section className="panel">
          <h2>Operational controls</h2>
          <ControlsPanel wsKillSwitchEnabled={wsKillSwitchEnabled} />
        </section>
      </main>
    </div>
  );
}
