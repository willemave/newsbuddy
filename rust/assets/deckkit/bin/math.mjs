// Render <span class="tex">...</span> and <div class="tex-block">...</div> with KaTeX, in place. Idempotent.
import fs from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const katex = require(process.env.DECKKIT_HOME ? `${process.env.DECKKIT_HOME}/node_modules/katex` : "/opt/deckkit/node_modules/katex");
const file = process.argv[2];
let html = fs.readFileSync(file, "utf8");
const decode = s => s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
let n = 0, errors = [];
html = html.replace(/<(span|div) class="(tex|tex-block)"(?![^>]*data-k)([^>]*)>([\s\S]*?)<\/\1>/g, (m, tag, cls, rest, src) => {
  try {
    const out = katex.renderToString(decode(src.trim()), { displayMode: cls === "tex-block", throwOnError: true, output: "html" });
    n++;
    return `<${tag} class="${cls}" data-k="1"${rest} data-tex="${src.trim().replace(/"/g, "&quot;")}">${out}</${tag}>`;
  } catch (e) { errors.push(`${src.trim().slice(0, 60)}: ${e.message}`); return m; }
});
fs.writeFileSync(file, html);
console.log(`rendered ${n} math expressions`);
if (errors.length) { console.log("errors:\n" + errors.join("\n")); process.exitCode = 1; }
