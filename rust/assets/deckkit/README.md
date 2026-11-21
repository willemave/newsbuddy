# Newsly Deck Kit

The kit owns the visual system (Daylight: warm paper, dark ink, one emerald accent, Newsreader /
Inter / JetBrains Mono). You own the teaching. Do not write theme CSS, fonts, colors, or Reveal
configuration; add a small `<style>` block only for deck-specific diagram geometry.

Commands (run from the workspace root):

- `deckkit new "Title"`: copy kit assets into `output/assets/` and write a skeleton
  `output/index.html` (Reveal 6.0.1 + kit). Run once, first.
- `deckkit math`: render `<span class="tex">…</span>` / `<div class="tex-block">…</div>` with KaTeX
  in place and add the KaTeX stylesheet and fonts. Run after every edit that adds math.
- Local files under `output/assets/` are limited to 40 per deck. The kit uses 8, and math adds 21
  more (KaTeX), leaving 11 for figures in a deck with math. Prefer one SVG per figure; keep data
  files and scripts in `lab/`, not `output/assets/`.
- `deckkit svg <entry> <out.svg>`, `deckkit mermaid <in.mmd> <out.svg>`, and `deckkit inline`:
  render D3/visx and Mermaid diagrams to static SVG and place them in slides (see Visual tools).
- Charts: `plt.style.use("/opt/deckkit/deck.mplstyle")`; portrait figures `figsize=(6.24, 5)`,
  save SVG (or PNG at dpi 200) under `output/assets/`, label axes in plain words, annotate the
  point the reader should notice directly on the chart.

## Visual tools

Render every diagram and chart to static SVG in the VM; decks need no runtime chart scripts.
Write D3/visx/Mermaid output to `lab/*.svg` and place it with an element such as
`<figure class="k-figure k-grow" data-deckkit-svg="lab/flow.svg"></figure>`, then run
`deckkit inline` (again after every re-render). Inlined diagrams use the kit fonts and classes and
do not count against the asset limit. Use `<img>` for matplotlib charts under `output/assets/`. Pick the tool by what the visual must explain:

| Explains | Tool |
| --- | --- |
| Results from data you computed in `lab/` | matplotlib with `deck.mplstyle` |
| Bespoke quantitative or geometric graphics: annotated scales, vector geometry, distributions, trees, networks, Sankey-like flows, timelines with real dates | `deckkit svg` with D3 (scales, shapes, `d3-hierarchy`, `d3-force`) or visx (`@visx/shape`, `scale`, `axis`, `group`, `hierarchy`, `network`, `text`) |
| Structure and process: pipelines, architectures, sequence of calls, state machines, class or entity relations | `deckkit mermaid` (`flowchart TD` for portrait, `sequenceDiagram`, `stateDiagram-v2`, `classDiagram`, `erDiagram`, `timeline`) |
| A mechanism built step by step | inline SVG with `.k-svg` classes and `class="fragment"` layers, or `k-steps` / `k-trace` |
| Equations | `deckkit math` |

`deckkit svg lab/fig.jsx lab/fig.svg` bundles the entry with esbuild and renders it
with React on the server. Default-export a component (receives `{ theme }`), or export
`svg({ theme })` returning an SVG string. `@deckkit/theme` exports the Daylight colors, fonts,
`width` (628, the portrait content width) and label sizes (24/20). Draw at that width with a
`viewBox`, keep labels at least 20px, and put one idea per diagram.

```jsx
import { scaleLinear } from "d3";
import { Group } from "@visx/group";
import { LinePath } from "@visx/shape";
export default function Fig({ theme }) {
  const x = scaleLinear([0, 10], [40, theme.width - 20]);
  const y = scaleLinear([0, 1], [380, 20]);
  const points = Array.from({ length: 11 }, (_, i) => [i, 1 - Math.exp(-i / 3)]);
  return (
    <svg viewBox={`0 0 ${theme.width} 420`} className="k-svg">
      <Group>
        <LinePath data={points} x={(d) => x(d[0])} y={(d) => y(d[1])} stroke={theme.accent} strokeWidth={4} />
        <text x={x(6)} y={y(0.8)} fontSize={theme.label} fill={theme.ink}>saturates near 1</text>
      </Group>
    </svg>
  );
}
```

`deckkit mermaid lab/flow.mmd lab/flow.svg` renders Mermaid text with house colors and
SVG text labels. Keep node labels short (two to five words), prefer top-down layouts in portrait,
and split diagrams with more than about eight nodes across slides. Sequence diagrams fit
portrait with at most three or four participants and short messages. Style an emphasized node with
`classDef on fill:#dcefe7,stroke:#0e7a5c,stroke-width:3px` and `class NodeId on`.

Every `<section>` is a flex column (top-aligned, consistent gap) inside the reader's safe area.
Give each one a stable `id`. Content width is about 628px in portrait and 1136px in landscape.
Portrait type is already phone-sized; if content does not fit, split the slide.

## Slide shapes

```html
<section id="cover" class="k-cover"> <div class="k-eyebrow">…</div> <h1>…</h1> <p class="k-lede">…</p> <div class="k-meta">…</div> </section>
<section id="part-2" class="k-section"> <div class="k-num">02</div> <h2>…</h2> <p class="k-lede">…</p> </section>
<section id="x"> <div class="k-head"><div class="k-eyebrow">Mechanism</div><h2>Title that states the point</h2></div> … <div class="k-cite">Author, Title §3</div> </section>
<p class="k-statement">One-sentence claim set large.</p>
```

## Components

```html
<!-- Figure: computed chart, SVG diagram, or source image. k-grow lets it take the remaining height. -->
<figure class="k-figure k-grow"><img src="assets/loss.svg" alt="…"><figcaption><b>What to notice:</b> …</figcaption></figure>

<!-- Inline SVG diagram vocabulary: .node(.soft|.on|.bad), .edge(.on|.bad), .lbl, .fill-accent.
     Use viewBox="0 0 628 H" in portrait-sized diagrams; text 24-28px; add class="fragment" to <g> layers. -->
<svg class="k-svg" viewBox="0 0 628 420"> <g><rect class="node" x="0" y="0" width="300" height="90" rx="6"/><text x="20" y="55">Input</text></g> <g class="fragment"><path class="edge on" d="M150 90 V170"/></g> </svg>

<!-- Steps: a mechanism as ordered beats; k-focus dims earlier beats as later ones appear. -->
<ol class="k-steps k-focus"> <li class="fragment"><b>Hash the key</b><span class="k-small">h("cat") = 0x3a… → bucket 2</span></li> … </ol>

<!-- Trace: one concrete input moving through the system with real intermediate values. -->
<div class="k-trace"> <div class="k-k">input</div><div class="k-v">"refund 4 orders"</div> <div class="k-k">step 1</div><div class="k-v on">…</div> </div>

<!-- Flow: chain of stages; vertical in portrait, horizontal in landscape. Max ~5 nodes. -->
<div class="k-flow"> <div class="k-node"><b>Rules</b>blocks 92%</div> <div class="k-node on"><b>LLM</b>reviews the rest</div> </div>

<!-- Compare: two (or three) columns, stacked in portrait. .on = preferred, .bad = rejected. -->
<div class="k-compare"> <div class="k-col bad"><h3>Naive</h3><p>…</p></div> <div class="k-col on"><h3>Actual</h3><p>…</p></div> </div>

<!-- Callouts: k-insight, k-myth (misreading), k-why, k-limit, k-try (predict before advancing). -->
<div class="k-callout k-myth"><p>You might think … Actually … because …</p></div>

<!-- Source excerpt -->
<blockquote class="k-quote">“…exact words…”<footer>Author · section</footer></blockquote>

<!-- Code: wrap lines to emphasize in <span class="hl">, asides in <span class="note">. -->
<pre class="k-code">def step(x):
<span class="hl">    return x @ W</span>  <span class="note"># the only learned op</span></pre>

<!-- Math -->
<div class="tex-block">p(y\mid x) = \frac{e^{z_y}}{\sum_k e^{z_k}}</div>   inline: <span class="tex">O(n \log n)</span>

<!-- Table (td.num right-aligns numbers; tr.on / tr.bad highlight) -->
<table class="k-table"><tr><th>Method</th><th class="num">Recall</th></tr><tr class="on"><td>…</td><td class="num">0.91</td></tr></table>

<!-- Real numbers only -->
<div class="k-stats"><div class="k-stat"><div class="k-n">38 days</div><div class="k-l">training run</div></div></div>

<!-- Self-check: answer revealed on advance -->
<div class="k-check"><p class="k-q">Q: …</p><p class="k-a fragment">…</p></div>
```

Text utilities: `k-lede`, `k-small`, `k-muted`, `k-accent`, `k-warn`, `k-mark` (highlighter),
`k-frame` (white panel), `k-cite` (pins to the slide bottom).
