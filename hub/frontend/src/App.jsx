import { useMemo } from "react";
import { usePolling } from "./hooks/usePolling";
import { DegradedBanner } from "./components/DegradedBanner";
import { HeaderMetrics } from "./components/HeaderMetrics";
import { StatusCards } from "./components/StatusCards";
import { IntegrationMatrix } from "./components/IntegrationMatrix";
import { SloPanel } from "./components/SloPanel";
import { BusinessImpact } from "./components/BusinessImpact";
import { IncidentTimeline } from "./components/IncidentTimeline";
import { DemoTrigger } from "./components/DemoTrigger";
import { ChatPanel } from "./components/ChatPanel";

function getBaseUrl() {
  if (
    typeof import.meta !== "undefined" &&
    import.meta.env &&
    import.meta.env.VITE_CHATBOT_URL
  ) {
    return import.meta.env.VITE_CHATBOT_URL.replace(/\/+$/, "");
  }
  return "";
}

export default function App() {
  const baseUrl = useMemo(getBaseUrl, []);
  const { summary, integrations, deps, lastUpdated } = usePolling(baseUrl);

  const showTelcoTab = import.meta.env.VITE_ENABLE_TELCO_ORAN !== "false";
  const telcoUrl = window.location.origin.replace("hub-frontend", "hub-ran-frontend");

  return (
    <main className="page">
      {showTelcoTab && (
        <nav className="tab-bar">
          <span className="tab-btn active">Network</span>
          <a href={telcoUrl} className="tab-btn">Telco ORAN</a>
        </nav>
      )}
      <DegradedBanner deps={deps} />
      <HeaderMetrics
        integrations={integrations}
        summary={summary}
        lastUpdated={lastUpdated}
      />
      <StatusCards summary={summary} integrations={integrations} deps={deps} />
      <IntegrationMatrix integrations={integrations} summary={summary} />
      <SloPanel slo={integrations.slo} integrations={integrations} />
      <BusinessImpact impact={integrations.business_impact} />
      <IncidentTimeline movie={integrations.incident_movie} />
      <DemoTrigger baseUrl={baseUrl} />
      <ChatPanel baseUrl={baseUrl} />
    </main>
  );
}
