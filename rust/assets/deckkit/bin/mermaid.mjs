// deckkit mermaid <in.mmd> <out.svg>
// Renders Mermaid text to static SVG in the house style, with SVG text labels (no HTML) so the
// result works inline or as an image and needs no runtime script.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const KIT = process.env.DECKKIT_HOME || "/opt/deckkit";
const require = createRequire(import.meta.url);
const { chromium } = require("/opt/newsly-agent/node_modules/playwright");
const [input, out] = process.argv.slice(2);
if (!input || !out) {
  console.error("usage: deckkit mermaid <in.mmd> <out.svg>");
  process.exit(2);
}
const source = fs.readFileSync(input, "utf8");
const id = `mmd-${path.basename(out, ".svg").replace(/[^a-zA-Z0-9_-]/g, "-")}`;
const font = (file) => `url(data:font/woff2;base64,${fs.readFileSync(`${KIT}/fonts/${file}`).toString("base64")})`;
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.setContent(
    `<style>@font-face{font-family:Inter;src:${font("inter-400.woff2")};font-weight:400}` +
      `@font-face{font-family:Inter;src:${font("inter-600.woff2")};font-weight:600}</style><body></body>`,
  );
  await page.addScriptTag({ path: `${KIT}/node_modules/mermaid/dist/mermaid.min.js` });
  await page.evaluate(() => document.fonts.ready);
  const svg = await page.evaluate(
    async ({ source, id }) => {
      window.mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "base",
        fontFamily: "Inter, sans-serif",
        htmlLabels: false,
        flowchart: { htmlLabels: false, useMaxWidth: true, curve: "basis", nodeSpacing: 36, rankSpacing: 48, padding: 14 },
        // Portrait-sized sequence diagrams: narrow actors, tight margins, no mirrored footer row.
        sequence: {
          useMaxWidth: true,
          mirrorActors: false,
          width: 120,
          actorMargin: 24,
          boxMargin: 6,
          diagramMarginX: 4,
          diagramMarginY: 4,
          actorFontSize: 22,
          messageFontSize: 22,
          noteFontSize: 20,
          messageFontWeight: 400,
        },
        themeVariables: {
          fontFamily: "Inter, sans-serif",
          fontSize: "22px",
          background: "#faf8f3",
          primaryColor: "#ffffff",
          primaryBorderColor: "#1c1b18",
          primaryTextColor: "#1c1b18",
          secondaryColor: "#dcefe7",
          secondaryBorderColor: "#0e7a5c",
          tertiaryColor: "#f2eee4",
          tertiaryBorderColor: "#e2dccf",
          lineColor: "#4a4740",
          textColor: "#1c1b18",
          noteBkgColor: "#f2eee4",
          noteBorderColor: "#e2dccf",
          clusterBkg: "#f2eee4",
          clusterBorder: "#e2dccf",
          edgeLabelBackground: "#faf8f3",
          actorBkg: "#ffffff",
          actorBorder: "#1c1b18",
          signalColor: "#1c1b18",
        },
      });
      const { svg } = await window.mermaid.render(id, source);
      return svg;
    },
    { source, id },
  );
  // Mermaid pins max-width to the natural size; drop it so the diagram scales to the figure.
  const scalable = svg
    .replace(/^(<svg[^>]*?)\sstyle="[^"]*max-width:[^"]*"/, "$1")
    .replace(/^(<svg[^>]*?)\swidth="[^"]*"/, '$1 width="100%"');
  fs.mkdirSync(path.dirname(path.resolve(out)), { recursive: true });
  fs.writeFileSync(out, scalable);
  console.log(`wrote ${out} (${scalable.length} bytes)`);
} finally {
  await browser.close();
}
