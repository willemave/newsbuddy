// deckkit inline: replace the contents of every element carrying data-deckkit-svg="lab/x.svg"
// in output/index.html with that SVG file. Idempotent, so re-run it after re-rendering.
import fs from "node:fs";

const file = "output/index.html";
let html = fs.readFileSync(file, "utf8");
let count = 0;
const missing = [];
html = html.replace(
  /<(div|figure|span)([^>]*\sdata-deckkit-svg="([^"]+)"[^>]*)>([\s\S]*?)<\/\1>/g,
  (match, tag, attributes, source) => {
    if (!/^lab\/[\w./-]+\.svg$/.test(source) || source.includes("..") || !fs.existsSync(source)) {
      missing.push(source);
      return match;
    }
    const svg = fs.readFileSync(source, "utf8").replace(/^<\?xml[^>]*>\s*/, "").trim();
    count += 1;
    return `<${tag}${attributes}>${svg}</${tag}>`;
  },
);
fs.writeFileSync(file, html);
console.log(`inlined ${count} SVG diagrams`);
if (missing.length) {
  console.log(`missing or invalid sources (must be lab/*.svg): ${missing.join(", ")}`);
  process.exitCode = 1;
}
