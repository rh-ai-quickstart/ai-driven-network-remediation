import assert from "node:assert/strict";
import { test } from "node:test";
import { renderDependencyBanner } from "./render-dependency-banner.mjs";

test("RCA outage appears in the dependency banner and clears on recovery", () => {
  const degraded = renderDependencyBanner({ status: "degraded", unavailable: ["rca_service"] });
  const recovered = renderDependencyBanner({ status: "ok" });

  assert.match(degraded, /role="alert"/);
  assert.match(degraded, /rca_service/);
  assert.equal(recovered, "");
});
