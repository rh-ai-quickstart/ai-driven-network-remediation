import { useMemo } from "react";
import { usePolling } from "./hooks/usePolling";
import { DegradedBanner } from "./components/DegradedBanner";
import { HeaderMetrics } from "./components/HeaderMetrics";
import { AnomalyTable } from "./components/AnomalyTable";
import { DemoTrigger } from "./components/DemoTrigger";
import { ChatPanel } from "./components/ChatPanel";

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

export default function App() {
  const baseUrl = useMemo(getBaseUrl, []);
  const { anomalies, count, deps, lastUpdated, speedUpPolling, refetchNow } = usePolling(baseUrl);

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
      <DemoTrigger baseUrl={baseUrl} onTriggered={speedUpPolling} />
      <AnomalyTable anomalies={anomalies} baseUrl={baseUrl} onCleared={refetchNow} />
      <ChatPanel baseUrl={baseUrl} />
    </main>
  );
}
