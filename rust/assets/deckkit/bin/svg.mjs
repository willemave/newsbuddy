// deckkit svg <entry.jsx|.js> <out.svg>
// Renders a D3/visx diagram to static SVG inside the VM. The entry either default-exports a
// React component (visx, or plain SVG JSX) rendered with props { theme }, or exports
// `svg({ theme })` returning an SVG string (for example built from d3 scales and shapes).
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const KIT = process.env.DECKKIT_HOME || "/opt/deckkit";
const require = createRequire(`${KIT}/package.json`);
const esbuild = require("esbuild");
const [entry, out] = process.argv.slice(2);
if (!entry || !out) {
  console.error("usage: deckkit svg <entry.jsx|.js> <out.svg>");
  process.exit(2);
}
const absolute = path.resolve(entry);
const wrapper = `
import * as entry from ${JSON.stringify(absolute)};
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { theme } from "@deckkit/theme";
export default async function render() {
  if (typeof entry.svg === "function") return String(await entry.svg({ theme }));
  if (typeof entry.svg === "string") return entry.svg;
  if (entry.default) return renderToStaticMarkup(createElement(entry.default, { theme }));
  throw new Error("export a default React component or an svg({ theme }) function");
}`;
const bundle = `/tmp/deckkit-svg-${process.pid}.mjs`;
await esbuild.build({
  stdin: { contents: wrapper, resolveDir: path.dirname(absolute), loader: "jsx" },
  bundle: true,
  platform: "node",
  format: "esm",
  jsx: "automatic",
  loader: { ".js": "jsx" },
  nodePaths: [`${KIT}/node_modules`],
  alias: { "@deckkit/theme": `${KIT}/theme.mjs` },
  banner: { js: "import { createRequire as __deckkitRequire } from 'node:module'; const require = __deckkitRequire(import.meta.url);" },
  outfile: bundle,
  logLevel: "error",
});
try {
  let svg = (await (await import(pathToFileURL(bundle).href)).default()).trim();
  if (!svg.startsWith("<svg")) throw new Error("the entry did not produce an <svg> root");
  if (!/^<svg[^>]*\sxmlns=/.test(svg)) svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"');
  fs.mkdirSync(path.dirname(path.resolve(out)), { recursive: true });
  fs.writeFileSync(out, svg);
  console.log(`wrote ${out} (${svg.length} bytes)`);
} finally {
  fs.rmSync(bundle, { force: true });
}
