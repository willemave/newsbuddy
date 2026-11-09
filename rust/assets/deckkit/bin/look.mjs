// Screenshot slides as an upright iPhone reader sees them and build JPEG contact sheets.
// The host `look` tool runs this; it is not an agent command.
// usage: node look.mjs <viewer.html> <max-sheets> [from-to]
// Without a range, only slides whose content (authored HTML without Reveal's runtime state,
// plus referenced local files) has not been shown before are captured. Prints one JSON line
// describing the sheets written under .deckkit/look/. The seen set is written to
// .deckkit/look-state.pending.json; the host commits it only after it has read every sheet.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("/opt/newsly-agent/node_modules/playwright");

// 4x2 cells of 240x519 keep each sheet near 1.1 MP, under the ~1.15 MP size at which the model
// provider downscales images, so slide text stays as legible as the pixels allow.
const PER_SHEET = 8;
const COLS = 4;
const THUMB = { width: 240, height: 519 };
const [viewer, maxSheetsArg, range] = process.argv.slice(2);
const maxSheets = Math.max(0, Number.parseInt(maxSheetsArg, 10) || 0);
const outDir = ".deckkit/look";
const statePath = ".deckkit/look-state.json";
const pendingPath = ".deckkit/look-state.pending.json";

function localFileDigest(src) {
  if (!src || /^(https?:|data:)/.test(src)) return "";
  const file = path.resolve(path.dirname(viewer), src.split(/[?#]/)[0]);
  try {
    return crypto.createHash("sha1").update(fs.readFileSync(file)).digest("hex");
  } catch {
    return "missing";
  }
}

const seenBefore = (() => {
  try {
    return new Set(JSON.parse(fs.readFileSync(statePath, "utf8")).seen || []);
  } catch {
    return new Set();
  }
})();
fs.rmSync(outDir, { recursive: true, force: true });
fs.mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const errors = [];
try {
  const page = await browser.newPage({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 1,
    isMobile: true,
    hasTouch: true,
  });
  page.setDefaultTimeout(15000);
  page.on("pageerror", (error) => errors.push(String(error).slice(0, 300)));
  page.on("requestfailed", (request) => errors.push(`failed: ${request.url()}`.slice(0, 300)));
  await page.goto(pathToFileURL(path.resolve(viewer)).href, { waitUntil: "load" });
  await page.waitForFunction(() => window.Reveal && Reveal.isReady());
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(500);

  // Hash authored content only: Reveal adds position-dependent state (present/past/future,
  // display styles, hidden, aria-hidden, index attributes) that would mark unchanged slides.
  const slides = await page.evaluate(() => {
    const runtimeClasses = ["present", "past", "future", "visible", "current-fragment", "stack"];
    const slideRuntimeAttributes = ["style", "hidden", "aria-hidden", "data-index-h", "data-index-v", "data-previous-indexv"];
    return Reveal.getSlides().map((slide) => {
      const clone = slide.cloneNode(true);
      clone.querySelectorAll("section").forEach((nested) => nested.remove());
      slideRuntimeAttributes.forEach((name) => clone.removeAttribute(name));
      for (const node of [clone, ...clone.querySelectorAll("*")]) {
        runtimeClasses.forEach((name) => node.classList.remove(name));
        if (node.getAttribute("class") === "") node.removeAttribute("class");
        if (node.hasAttribute("data-src")) node.removeAttribute("src");
        if (node.classList.contains("fragment")) node.removeAttribute("style");
      }
      return {
        id: slide.id || "",
        html: clone.outerHTML,
        srcs: Array.from(clone.querySelectorAll("[src],[data-src]"), (node) => node.getAttribute("src") || node.getAttribute("data-src")),
      };
    });
  });
  const hashes = slides.map((slide) => {
    const digest = crypto.createHash("sha1").update(slide.html);
    slide.srcs.forEach((src) => digest.update(localFileDigest(src)));
    return digest.digest("hex");
  });

  let wanted;
  if (range) {
    const [from, to] = range.split("-").map(Number);
    wanted = slides.map((_, i) => i + 1).filter((n) => n >= from && n <= (to || from));
  } else {
    wanted = slides.map((_, i) => i + 1).filter((n) => !seenBefore.has(hashes[n - 1]));
  }
  const limit = maxSheets * PER_SHEET;
  const captured = wanted.slice(0, limit);
  const deferred = wanted.slice(limit);

  const shots = [];
  for (const n of captured) {
    const frags = await page.evaluate(async (index) => {
      const slide = Reveal.getSlides()[index];
      const at = Reveal.getIndices(slide);
      const count = slide.querySelectorAll(".fragment").length;
      Reveal.slide(at.h, at.v, count ? 999 : undefined);
      await new Promise((resolve) => setTimeout(resolve, 150));
      return count;
    }, n - 1);
    const png = await page.screenshot({ type: "png" });
    shots.push({ n, id: slides[n - 1].id, frags, data: png.toString("base64") });
  }

  const sheets = [];
  const sheetPage = await browser.newPage({ deviceScaleFactor: 1 });
  for (let start = 0; start < shots.length; start += PER_SHEET) {
    const batch = shots.slice(start, start + PER_SHEET);
    const rows = Math.ceil(batch.length / COLS);
    const width = COLS * (THUMB.width + 12) + 12;
    const height = rows * (THUMB.height + 36) + 12;
    const cells = batch
      .map(
        (shot) =>
          `<figure><figcaption>${shot.n} #${shot.id.replace(/[<&]/g, "")}</figcaption>` +
          `<img src="data:image/png;base64,${shot.data}"></figure>`,
      )
      .join("");
    await sheetPage.setViewportSize({ width, height });
    await sheetPage.setContent(
      `<style>body{margin:0;padding:12px 0 0 12px;background:#d9d4c8;display:grid;` +
        `grid-template-columns:repeat(${COLS},${THUMB.width}px);gap:0 12px;font:13px monospace}` +
        `figure{margin:0 0 12px}figcaption{height:22px;color:#1c1b18}` +
        `img{width:${THUMB.width}px;height:${THUMB.height}px;display:block}</style>${cells}`,
      { waitUntil: "load" },
    );
    const file = `${outDir}/sheet-${sheets.length + 1}.jpg`;
    await sheetPage.screenshot({ path: file, type: "jpeg", quality: 72 });
    sheets.push({ file, slides: batch.map((shot) => shot.n) });
  }

  // Propose only what the model is about to see; deferred slides stay unseen. Keep the set
  // bounded to hashes still present in the deck.
  const current = new Set(hashes);
  const seen = [...seenBefore].filter((hash) => current.has(hash));
  captured.forEach((n) => seen.push(hashes[n - 1]));
  fs.writeFileSync(pendingPath, JSON.stringify({ seen: [...new Set(seen)] }));

  console.log(
    JSON.stringify({
      slide_count: slides.length,
      captured,
      unchanged: range ? 0 : slides.length - wanted.length,
      deferred,
      sheets,
      page_errors: errors.slice(0, 10),
    }),
  );
} finally {
  await browser.close();
}
