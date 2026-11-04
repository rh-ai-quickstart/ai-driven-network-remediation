import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { transformWithEsbuild } from "vite";

const source = readFileSync(new URL("../src/components/AnomalyTable.jsx", import.meta.url), "utf8");
const { code } = await transformWithEsbuild(source, "AnomalyTable.jsx", { loader: "jsx", format: "cjs", jsx: "automatic" });
const module = { exports: {} };
new Function("require", "module", "exports", code)(createRequire(import.meta.url), module, module.exports);
const { AnomalyTable } = module.exports;

export function renderIncidentCard(anomaly) {
  return renderToStaticMarkup(createElement(AnomalyTable, { anomalies: [anomaly] }));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  process.stdout.write(renderIncidentCard(JSON.parse(input)));
}
