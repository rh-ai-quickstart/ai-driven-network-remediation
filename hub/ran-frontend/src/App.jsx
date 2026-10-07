import { useMemo, useState } from "react";
import { usePolling } from "./hooks/usePolling";
import { DegradedBanner } from "./components/DegradedBanner";
import { HeaderMetrics } from "./components/HeaderMetrics";
import { AnomalyTable } from "./components/AnomalyTable";
import { DemoTrigger } from "./components/DemoTrigger";
import { ChatPanel } from "./components/ChatPanel";
import { CellBandGrid } from "./components/CellBandGrid";

function getBaseUrl() {
  if (
    typeof import.meta !== "undefined" &&
    import.meta.env &&
    import.meta.env.VITE_RAN_CHATBOT_URL
  ) {
    return import.meta.env.VITE_RAN_CHATBOT_URL.replace(/\/+$/, "");
  }
  return "";
}

const VIEWS = { ANOMALIES: "anomalies", GRID: "grid" };

export default function App() {
  const baseUrl = useMemo(getBaseUrl, []);
  const { anomalies, count, deps, lastUpdated, speedUpPolling, refetchNow } = usePolling(baseUrl);
  const [view, setView] = useState(VIEWS.ANOMALIES);

  const showNetworkTab = import.meta.env.VITE_ENABLE_NETWORK_REMEDIATION !== "false";
  const networkUrl = window.location.origin.replace("hub-ran-frontend", "hub-frontend");

  return (
    <main className="page">
      {showNetworkTab && (
        <nav className="tab-bar">
          <a href={networkUrl} className="tab-btn">Network</a>
          <span className="tab-btn active">Telco ORAN</span>
        </nav>
      )}
      <DegradedBanner deps={deps} />
      <HeaderMetrics anomalies={anomalies} count={count} deps={deps} lastUpdated={lastUpdated} />

      <nav className="view-bar">
        <button
          type="button"
          className={`view-btn ${view === VIEWS.ANOMALIES ? "active" : ""}`}
          onClick={() => setView(VIEWS.ANOMALIES)}
        >
          Anomaly List
        </button>
        <button
          type="button"
          className={`view-btn ${view === VIEWS.GRID ? "active" : ""}`}
          onClick={() => setView(VIEWS.GRID)}
        >
          Cell &amp; Band Grid
        </button>
      </nav>

      {view === VIEWS.GRID ? (
        <CellBandGrid anomalies={anomalies} />
      ) : (
        <>
          <DemoTrigger baseUrl={baseUrl} onTriggered={speedUpPolling} />
          <AnomalyTable anomalies={anomalies} baseUrl={baseUrl} onCleared={refetchNow} />
          <ChatPanel baseUrl={baseUrl} />
        </>
      )}
    </main>
  );
}
