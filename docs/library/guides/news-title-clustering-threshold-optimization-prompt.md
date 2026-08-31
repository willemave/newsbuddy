You are optimizing Newsly's short-form news relation matcher thresholds.

Repository: `/Users/willem/Development/news_app`

Matcher surface:
- Main code: `rust/crates/newsly-domain/src/news_relations.rs`
- Eval cases: `python/evals/src/newsly_evals/news_relation_cases.py`
- Eval runner: `python/evals/scripts/run_title_clustering_eval.py`

Current settings:
- primary similarity threshold: `0.85`
- secondary similarity threshold: `0.75`
- reranker enabled: `false`
- reranker threshold: `0.45`

Known miss to preserve as a target:
- case id: `batch_008_c1`
- incident: Hacker News surfaced "Commerce lifts export controls on Claude Fable 5 and Mythos 5" separately from Techmeme's earlier "Trump administration plans to lift export restrictions on Anthropic's Fable 5".
- diagnosis: exact relation keys differed, and title/provenance semantic scores were below the current secondary threshold.

Goal:
Find threshold settings that improve recall on genuine same-story clusters, especially `batch_008_c1`, without materially increasing false merges in the negative cases.

Run the real eval path. Do not invent a separate scoring harness unless the existing runner cannot answer a specific question.

Suggested baseline command:
```bash
uv run --project python/evals python python/evals/scripts/run_title_clustering_eval.py --json
```

Suggested threshold sweep shape:
```bash
uv run --project python/evals python python/evals/scripts/run_title_clustering_eval.py \
  --threshold current:0.85:0.75 \
  --threshold probe_082_072:0.82:0.72 \
  --threshold probe_084_072:0.84:0.72 \
  --threshold probe_085_072:0.85:0.72 \
  --threshold probe_085_070:0.85:0.70 \
  --threshold probe_088_072:0.88:0.72 \
  --json
```

Optimization rules:
1. Treat every `positive_cases` entry as one expected cluster.
2. Treat every `negative_cases.groups` entry as separate expected clusters.
3. Favor precision. A threshold that fixes `batch_008_c1` but creates broad false merges is worse than the current settings.
4. Prefer the smallest threshold change that improves macro F1 and fixes the target miss.
5. Report any cases that newly pass or newly fail versus the current baseline.
6. Do not change production thresholds until the eval evidence is explicit.

Return this JSON shape:
```json
{
  "recommended_thresholds": {
    "primary": 0.0,
    "secondary": 0.0,
    "reranker_enabled": false,
    "reranker_threshold": 0.0
  },
  "baseline": {
    "passed": 0,
    "total": 0,
    "macro_f1": 0.0,
    "precision": 0.0,
    "recall": 0.0
  },
  "best_candidate": {
    "label": "name",
    "passed": 0,
    "total": 0,
    "macro_f1": 0.0,
    "precision": 0.0,
    "recall": 0.0
  },
  "target_case": {
    "case_id": "batch_008_c1",
    "baseline_passed": false,
    "candidate_passed": false,
    "notes": "short explanation"
  },
  "regressions": [
    {
      "case_id": "case",
      "label": "case label",
      "reason": "what changed"
    }
  ],
  "recommended_code_change": "settings-only|matcher-change|none",
  "rationale": "short explanation"
}
```

Full eval set:
```json
{
  "positive_cases": [
    {
      "case_id": "batch_008_c1",
      "label": "Anthropic Fable 5 export controls lifted",
      "titles": [
        "Commerce lifts export controls on Claude Fable 5 and Mythos 5",
        "Department of Commerce has lifted export controls on Claude Fable 5 and Mythos 5",
        "Trump administration plans to lift export restrictions on Anthropic's Fable 5",
        "Sources: the Trump administration plans to lift export restrictions on Anthropic's Fable 5 as early as Tuesday evening, making it available to all general users",
        "Anthropic says the US Department of Commerce has lifted export controls on Claude Fable 5 and Mythos 5 and that it will begin restoring access on Wednesday"
      ]
    },
    {
      "case_id": "batch_004_c6",
      "label": "Pentagon pressures Anthropic / supply chain risk dispute",
      "titles": [
        "Pentagon flags Anthropic as a supply chain risk, Bloomberg reports",
        "Pentagon threatens to cut off Anthropic contract over disputed AI safeguard requirements",
        "Pentagon threatens to make Anthropic a pariah if it refuses to drop AI guardrails | CNN Business",
        "Pentagon weighs cutting ties with Anthropic, may label AI firm a supply chain risk",
        "Pentagon pressures Anthropic to drop Claude Gov restrictions, threatens DPA action or “supply chain risk” label",
        "Pentagon gives Anthropic deadline to allow unrestricted military AI use or risk losing contract",
        "Pentagon chief Hegseth to meet Anthropic CEO as military AI rollout sparks ethics fight",
        "Pentagon CTO says Anthropic’s Claude would ‘pollute’ DOD supply chain, cites baked-in policy bias",
        "Pentagon offers written assurances to Anthropic as deadline looms for unrestricted Claude use",
        "Pentagon talks with Anthropic reportedly faltered over bulk data analysis of Americans",
        "Sen. Ron Wyden vows fight after Pentagon moves to bar Anthropic",
        "Pentagon-linked account calls to label Anthropic a supply-chain risk after public dispute"
      ]
    },
    {
      "case_id": "batch_001_c33",
      "label": "Anthropic refuses Pentagon unrestricted use",
      "titles": [
        "Anthropic fights Pentagon “any lawful use” clause as DoD threatens ‘supply chain risk’ label",
        "Anthropic ditches its core safety promise in the middle of an AI red line fight with the Pentagon | CNN Business",
        "Anthropic says it won’t remove safeguards on domestic surveillance and fully autonomous weapons for DoW",
        "Anthropic refuses Pentagon demand for unrestricted Claude use, citing surveillance and autonomous weapons risks",
        "Anthropic rejects latest Pentagon offer, ‘we cannot in good conscience accede to their request.’ | CNN Business",
        "Anthropic to challenge proposed Pentagon ‘supply chain risk’ label over surveillance, autonomous weapons",
        "Anthropic Hits Back After US Military Labels It a 'Supply Chain Risk' | WIRED",
        "Anthropic CEO holds firm on AI ‘red lines’ after Trump orders Pentagon to drop Claude",
        "Anthropic CEO says refusing Pentagon terms is free speech; vows court challenge if blacklisted",
        "Anthropic rejects Pentagon 'any lawful use' terms, risking $200M DoD contract over AI safeguards",
        "Anthropic refuses Pentagon “all lawful purposes” clause; ban triggers surge and forces OpenAI safeguards"
      ]
    },
    {
      "case_id": "batch_004_c1",
      "label": "OpenAI testing ads in ChatGPT",
      "titles": [
        "OpenAI targets ~$60 CPM for ChatGPT ads, matching NFL rates while offering little conversion data",
        "OpenAI Tests Ad Integration in ChatGPT Platform",
        "OpenAI to test ads in ChatGPT's free and $8 'Go' tiers as it burns through billions",
        "OpenAI to Test Ads in ChatGPT, Matched to Conversations, Says It Won't Sell User Data",
        "OpenAI to test ads under ChatGPT replies for free and Go users in US; targets 'low billions' in 2026",
        "OpenAI to Test Targeted Ads for Free ChatGPT Users — What to Expect",
        "OpenAI tests ChatGPT Ads Manager as early campaign performance trails Google Search"
      ]
    },
    {
      "case_id": "batch_004_c7",
      "label": "Pentagon used Anthropic Claude in Iran strikes via Palantir Maven",
      "titles": [
        "Pentagon used Palantir Maven with Anthropic Claude to hit 1,000 Iran targets in 24 hours",
        "Pentagon used Palantir Maven with Anthropic’s Claude to rapidly prioritize targets in Iran strikes",
        "Report: Anthropic’s Claude helped generate 1,000+ Iran strike targets via Palantir’s Maven system",
        "Report: Archived intel in AI targeting system may have caused strike on Iran girls’ school",
        "Reports: US military used Anthropic’s Claude AI in Iran strikes, then banned it hours later",
        "Report: US military used Anthropic’s Claude in Iran strikes hours after Trump ordered ban",
        "Report: US used Anthropic’s Claude via Palantir in raid to seize Venezuela’s Maduro"
      ]
    },
    {
      "case_id": "batch_001_c34",
      "label": "Anthropic sues Pentagon/Trump over blacklist",
      "titles": [
        "Anthropic sues Trump administration to overturn Pentagon 'supply chain risk' blacklist",
        "Anthropic Claims Pentagon Feud Could Cost It Billions | WIRED",
        "Anthropic sues US over Pentagon “supply chain risk” label, raising uncertainty around $380B valuation talk",
        "Anthropic sues Trump administration over Pentagon ‘supply chain risk’ label and Claude ban",
        "Anthropic sues Pentagon over AI ban tied to refusal to allow autonomous lethal use and mass surveillance",
        "Anthropic sues Pentagon over AI guardrails as Silicon Valley shifts toward defense work"
      ]
    },
    {
      "case_id": "batch_001_c82",
      "label": "Anthropic Cowork launch",
      "titles": [
        "Anthropic's Cowork preview lets Claude access and edit local files to automate multi-step work",
        "Anthropic's Cowork lets Claude automate complex tasks — powerful automation with clear security risks",
        "Anthropic's Claude Cowork brings Claude Code power to wider users, but prompt-injection risks remain",
        "Anthropic's Cowork brings Claude Code automation to non-coders on the desktop",
        "Claude Cowork brings Claude Code's agent power to non-developers via sandboxed macOS UI",
        "Anthropic opens Claude Cowork to $20/month Pro users, expanding autonomous Mac tasks"
      ]
    },
    {
      "case_id": "batch_002_c16",
      "label": "Discord age verification rollout and backlash",
      "titles": [
        "Discord Clarifies Age Verification Won't Affect Most Users",
        "Discord delays global age verification rollout to H2 2026 after backlash, adds more options",
        "Discord drops Persona after researchers find identity-verification code exposed on government endpoints",
        "Discord ends UK Persona age-check test after backlash, leans on k-ID as global rollout nears",
        "Discord mandates age verification; users flee to federated Matrix alternative",
        "Discord requires face scan or ID for adult access starting March"
      ]
    },
    {
      "case_id": "batch_003_c107",
      "label": "OpenAI Sora shutdown",
      "titles": [
        "OpenAI Announces Shutdown of Sora AI Video Platform",
        "OpenAI Kills Sora Project to Reallocate GPUs for Core Coding and Enterprise Products",
        "OpenAI Shuts Down Sora AI Video App Following Collapsed $1B Disney Deal",
        "OpenAI Shuts Down Sora Video Platform; Disney Cancels $1 Billion Partnership",
        "OpenAI Shuts Down Sora, Prompting Disney to Cancel $1 Billion Investment Deal",
        "OpenAI Discontinues Sora Video Tool and Scraps Major Disney Partnership"
      ]
    },
    {
      "case_id": "batch_003_c18",
      "label": "Jury finds Meta liable child safety",
      "titles": [
        "Jury finds Instagram, YouTube designed to addict children in landmark liability verdict",
        "Jury Finds Meta and YouTube Negligent in Landmark Social Media Addiction Trial",
        "Jury Finds Meta Knowingly Harmed Children for Profit, Awards $375 Million Verdict",
        "Jury finds Meta liable in case over child sexual exploitation on its platforms | CNN Business",
        "New Mexico Jury Orders Meta to Pay $375 Million Over Child Safety Law Violations",
        "Jury Begins Deliberations in Landmark New Mexico Trial Over Children’s Safety on Meta Platforms"
      ]
    },
    {
      "case_id": "batch_003_c24",
      "label": "Kimi K2.5 multimodal model release",
      "titles": [
        "Kimi K2.5: Open-source multimodal SOTA with agent swarms that cut runtime up to 4.5×",
        "Kimi K2.5: Pretrained on ~15T visual+text tokens and can self-direct 100-agent swarms",
        "Moonshot unveils K2.5 multimodal AI that handles text, images and video and beats open-source peers",
        "Moonshot says Kimi K2.5 builds on K2 with \"pretraining over ~15T mixed visual and text tokens\" and \"can self-direct an agent swarm with up to 100 sub-agents\" (Kimi)",
        "Moonshot Kimi K2.5 Released: 32B Active Parameter Model Claims SOTA Over Sonnet 4.5 with 100-Agent Swarms",
        "Kimi K2.5 Achieves Highest ECI Score Among Open-Weight AI Models"
      ]
    },
    {
      "case_id": "batch_004_c5",
      "label": "SpaceX acquires/merges with xAI",
      "titles": [
        "SpaceX acquires xAI to build orbital data‑center constellation and scale space-based AI",
        "SpaceX acquires xAI, plans up to 1 million satellites as orbital AI data centers",
        "SpaceX Acquires xAI, Pursues Solar-Powered AI Data Centers in Orbit",
        "SpaceX to combine with xAI ahead of mega IPO, consolidating Musk's AI and aerospace assets",
        "Sources: SpaceX in advanced talks to combine with xAI, briefed investors",
        "Sources: SpaceX in advanced talks to merge with xAI as Musk consolidates his empire"
      ]
    },
    {
      "case_id": "batch_001_c13",
      "label": "Bandcamp bans AI-generated music",
      "titles": [
        "Bandcamp bans AI-generated music and training on uploads to protect human artists",
        "Bandcamp bans AI-generated music to reassure fans and protect human artists",
        "Bandcamp bans music and audio that is \"generated wholly or in substantial part by AI\", hoping to build trust with fans that the music they find was human-made (Maya Perez/WebProNews)",
        "Bandcamp bans purely AI-generated music to protect human artists",
        "Bandcamp Bars AI-Generated Music, Raising Copyright and Moderation Stakes"
      ]
    },
    {
      "case_id": "batch_001_c14",
      "label": "Anthropic Claude Opus 4.6 launch",
      "titles": [
        "Anthropic launches Claude Opus 4.6 with 1M-token context and major agentic coding gains",
        "Anthropic's Claude Opus 4.6 targets complex financial research, now used by 300K+ businesses",
        "Anthropic's Opus 4.6 adds 'agent teams', 1M-token context window and PowerPoint Claude pane",
        "Anthropic's Claude Opus 4.6 targets first‑try enterprise work with 1M context and 90.2 BigLaw score",
        "Anthropic’s Claude Opus 4.6 reportedly uncovered 500 zero-day flaws in open-source code"
      ]
    },
    {
      "case_id": "batch_001_c32",
      "label": "CISA director uploaded sensitive docs to ChatGPT",
      "titles": [
        "Acting CISA chief accidentally uploaded sensitive 'For Official Use Only' docs to ChatGPT, prompting DHS probe",
        "CISA acting director uploaded sensitive 'for official use only' files to public ChatGPT, prompting DHS review",
        "Acting US cybersecurity chief reportedly uploaded sensitive government files to ChatGPT",
        "CISA interim director reportedly uploaded sensitive documents to ChatGPT, triggering alerts",
        "CISA Director Uploaded Classified Data to ChatGPT While Agency Loses Third of Staff"
      ]
    },
    {
      "case_id": "batch_001_c36",
      "label": "Anthropic alleges mass Claude distillation",
      "titles": [
        "Anthropic announces proof of distillation at scale by MiniMax, DeepSeek,Moonshot",
        "Anthropic says DeepSeek, Moonshot AI and MiniMax ran mass campaigns to distill Claude via 24,000 fake accounts",
        "Anthropic alleges industrial-scale Claude distillation as evals, agent engineering, and infra shift under pressure",
        "Anthropic alleges industrial-scale Claude distillation by Chinese labs, but impact is uneven and hard to police",
        "Anthropic says overseas labs ran 'industrial-scale' distillation to copy Claude via 16M API exchanges"
      ]
    },
    {
      "case_id": "batch_001_c45",
      "label": "Block cuts ~4,000 jobs citing AI",
      "titles": [
        "Block to cut over 4,000 jobs, nearly halving workforce; shares jump 24% after earnings",
        "Block to cut nearly half its workforce, shrinking from 10,000+ staff to under 6,000",
        "Block to cut 4,000 jobs, telling investors AI lets a smaller workforce “do more and better”",
        "Block cuts 40% of staff as Dorsey says AI tools are already replacing jobs",
        "Block cuts 4,000 jobs citing AI-driven efficiency; Dorsey says most firms will follow within a year"
      ]
    },
    {
      "case_id": "batch_001_c56",
      "label": "Arm launches AGI CPU for data centers",
      "titles": [
        "Arm Debuts 'AGI CPU' Silicon with 136 Cores for AI Infrastructure",
        "Arm Launches First In-House 'AGI CPU' for Agentic AI Infrastructure",
        "Arm Debuts First Proprietary AGI CPU for Data Centers with Up to 136 Cores",
        "Arm Enters Silicon Market with 136-Core AGI CPU for AI Infrastructure",
        "Arm Stock Jumps 6% as CEO Targets $25B in Revenue by 2031 with New In-House Chip"
      ]
    },
    {
      "case_id": "batch_001_c90",
      "label": "Chardet LGPL-to-MIT relicensing dispute",
      "titles": [
        "chardet rewrote its codebase with Claude and relicensed from LGPL to MIT, raising AI clean-room doubts",
        "chardet author disputes maintainers’ attempt to relicense project in v7.0.0 rewrite",
        "AI-driven rewrite of Python’s Chardet sparks dispute over shifting license from LGPL to MIT",
        "AI reimplementation of chardet triggers LGPL-to-MIT relicensing fight over copyleft legitimacy",
        "Chardet relicensing fight spotlights how AI rewrites could undermine copyleft and proprietary software"
      ]
    },
    {
      "case_id": "batch_002_c67",
      "label": "Grok deepfake/nudify controversy",
      "titles": [
        "Grok fuels surge of AI sexual content on X, exposing weak safety controls",
        "Grok generated thousands of sexualized deepfakes — including minors — prompting global probes and platform limits",
        "Grok geoblocked from undressing real people in places where it's illegal after global backlash",
        "Grok still generates sexualized, nearly nude images of men despite X's ban",
        "Grok lawyers tell Dutch court zero-abuse prevention is impossible in nudify tools case"
      ]
    },
    {
      "case_id": "batch_003_c101",
      "label": "OpenAI $110B round at $730B valuation",
      "titles": [
        "OpenAI closes record $110B round at $730B valuation as Amazon, Nvidia and SoftBank pile in",
        "OpenAI lands $110B round at $730B pre-money, deepening AWS and Nvidia infrastructure ties",
        "OpenAI raises $110B led by Amazon, NVIDIA and SoftBank, valuing company at $730B pre-money",
        "OpenAI Nears $10B Funding Deal at $730B Valuation with MGX, Coatue, and Thrive",
        "OpenAI Reportedly Nears $10B Funding Deal at $730B Valuation"
      ]
    },
    {
      "case_id": "batch_003_c102",
      "label": "OpenAI GPT-5.3-Codex launch",
      "titles": [
        "OpenAI releases GPT-5.3‑Codex — faster, agentic coding model that builds, debugs and runs software end-to-end",
        "OpenAI launches GPT-5.3-Codex — 25% faster, self‑helping agent expands beyond coding",
        "OpenAI Releases GPT-5.3-Codex with 25% Speed Boost and Benchmark Gains",
        "OpenAI announces GPT-5.3-Codex-Spark with enhanced capabilities",
        "OpenAI launches GPT-5.3-Codex-Spark using Cerebras chip technology"
      ]
    },
    {
      "case_id": "batch_003_c103",
      "label": "OpenAI GPT-5.4 launch",
      "titles": [
        "OpenAI launches GPT-5.4 for pro workflows, adding native computer-use and higher API prices",
        "OpenAI launches GPT-5.4 with native computer control, aiming at more autonomous AI agents",
        "OpenAI launches GPT-5.4 with Pro and Thinking models, adds 1M-token API context and cheaper tool calling",
        "OpenAI launches GPT-5.4 with 1M-token context and native computer-use for AI agents",
        "OpenAI launches GPT-5.4 mini and nano, bringing near-flagship results at much lower cost"
      ]
    },
    {
      "case_id": "batch_003_c72",
      "label": "Musk xAI restructuring co-founder exits",
      "titles": [
        "Musk reorganizes xAI amid co-founder exodus and regulatory scrutiny",
        "Musk Restructures xAI After Co-Founder Exits, Reorganizes Into Four Units",
        "Musk pitches lunar AI factory as xAI loses half its founding team before IPO",
        "Musk says xAI was 'not built right' as Tesla’s $2B stake draws fresh scrutiny",
        "Musk says xAI “wasn’t built right” as cofounders and engineers exit and “Macrohard” stalls"
      ]
    },
    {
      "case_id": "batch_003_c75",
      "label": "NanoClaw AI agent security",
      "titles": [
        "NanoClaw adds one-command Docker Sandboxes setup to isolate AI agents in micro VMs",
        "NanoClaw argues AI agents must be treated as hostile: isolate each agent in ephemeral containers",
        "NanoClaw goes from weekend security fix to Docker deal after viral Hacker News and Karpathy boost",
        "NanoClaw Integrates OneCLI Agent Vault to Secure AI Credentials and Enforce Policies",
        "NanoClaw: Lightweight personal Claude assistant using Apple container sandboxes"
      ]
    },
    {
      "case_id": "batch_003_c9",
      "label": "Iran internet blackout/bypass",
      "titles": [
        "Iran's precise internet shutdown cuts 90% of traffic and could be prolonged",
        "Iran's temporary blackout risks becoming a permanent, elite-only tiered internet",
        "Iran’s near-total internet blackout shifts to whitelisting, enabling selective access while isolating 90M people",
        "Iranians bypass near-total blackout with smuggled Starlinks and bespoke software",
        "Iranians use Starlink, VPNs and decentralized apps to bypass near-total internet blackout"
      ]
    },
    {
      "case_id": "batch_003_c90",
      "label": "Nvidia NemoClaw enterprise agent platform",
      "titles": [
        "Nvidia launches NemoClaw: an enterprise OpenClaw stack with policy-based security guardrails",
        "Nvidia Is Planning to Launch an Open-Source AI Agent Platform | WIRED",
        "Nvidia readies open-source NemoClaw platform to manage enterprise AI agents amid “OpenClaw” surge",
        "Nvidia reportedly readies open-source enterprise AI agent platform ‘NemoClaw’ with security tools",
        "NVIDIA open-sources NemoClaw to sandbox OpenClaw assistants via OpenShell with policy-controlled egress"
      ]
    },
    {
      "case_id": "batch_003_c91",
      "label": "Nvidia investment in OpenAI",
      "titles": [
        "Nvidia in talks to invest up to $30B in OpenAI at reported $730B pre-money valuation",
        "Nvidia near $20B investment in OpenAI to anchor $100B funding round",
        "Nvidia will join OpenAI's current funding round but not with a $100B stake, Huang says",
        "Nvidia’s Huang says $30B OpenAI stake may be its last as OpenAI prepares to go public",
        "Nvidia’s Jensen Huang rules out $100 billion OpenAI investment, Bloomberg reports"
      ]
    },
    {
      "case_id": "batch_005_c25",
      "label": "Yann LeCun AMI Labs $1B raise",
      "titles": [
        "Yann LeCun launches Paris-based AMI Labs to bet on world models over LLMs",
        "Yann LeCun Raises $1 Billion to Build AI That Understands the Physical World | WIRED",
        "Yann LeCun’s AMI Labs lands $1.03B to pursue ‘world models’ beyond LLMs",
        "Yann LeCun’s AMI Labs raises $1.03B seed to pursue JEPA-based world models, challenging LLM-first orthodoxy",
        "Yann LeCun’s Startup Just Raised Over $1 Billion - Why That Matters — abZ Global"
      ]
    },
    {
      "case_id": "batch_001_c117",
      "label": "Anthropic Claude Code leak",
      "titles": [
        "Claude Code Leak Points to Always-On Agents, Auto-Approvals, and Stealth Open-Source Work",
        "Claude Code’s Edge Appears to Be Its Harness, Not Just Claude",
        "Claude Code’s Source Reveals a Configurable Agent Orchestrator, Not a Simple Terminal Chat",
        "Claude Code Leak Exposes Agent Architecture, Memory Design, and Anthropic’s Operational Tradeoffs"
      ]
    },
    {
      "case_id": "batch_001_c120",
      "label": "China signals Nvidia H200 approval",
      "titles": [
        "China signals approval for Nvidia H200 imports; tells Alibaba, Tencent and ByteDance to prepare orders",
        "China tells Alibaba, Tencent and ByteDance to prep Nvidia H200 orders, signaling near-approval",
        "China limits Nvidia H200 approvals to special cases, curbing Chinese access to top AI GPUs",
        "China drafting rules to cap Nvidia H200 purchases, forcing firms to justify needs"
      ]
    },
    {
      "case_id": "batch_001_c16",
      "label": "Apple Creator Studio launch",
      "titles": [
        "Apple Creator Studio bundles Final Cut Pro, Logic Pro and Pixelmator Pro with new AI tools for $12.99/mo",
        "Apple launches Creator Studio — $12.99/mo bundle of Final Cut, Logic, Pixelmator with AI tools to challenge Adobe",
        "Apple launches Creator Studio: Final Cut, Logic Pro and Pixelmator Pro bundled for $12.99/month"
      ]
    },
    {
      "case_id": "batch_001_c17",
      "label": "Apple picks Google Gemini for Siri",
      "titles": [
        "Apple picks Google’s Gemini to power next-gen Siri in multi-year deal",
        "Apple will base next‑gen foundation models on Google Gemini to power Siri and future Apple Intelligence features",
        "Apple to use Google's Gemini for ChatGPT-like Siri answers; can fine-tune model, no Google branding",
        "Apple to pay Google for Gemini cloud deal after OpenAI declines to partner"
      ]
    },
    {
      "case_id": "batch_001_c19",
      "label": "Apple Q1 earnings results",
      "titles": [
        "Apple Q1 revenue rises 16% to $143.8B as iPhone 17 surge and Services hit $30B record",
        "Apple Q1 revenue jumps 16% to $143.76B as iPhone demand and China surge power results",
        "Apple Q1: iPhone revenue jumps 23% to $85.27B, beats estimates; Mac down 7%",
        "Apple Q1: iPhone up 23% YoY to $85.27B, vs. $78.65B est., Mac down 7% to $8.39B, iPad up 6% to $8.6B, and Wearables, Home, and Accessories down 2% to $11.49B (Apple)"
      ]
    },
    {
      "case_id": "batch_001_c20",
      "label": "Apple $599 MacBook Neo launch",
      "titles": [
        "Apple announces $599 MacBook Neo with A18 Pro chip, 13-inch Liquid Retina and Apple Intelligence",
        "Apple unveils $599 MacBook Neo with A18 Pro, 13-inch Liquid Retina and 16-hour battery life",
        "Apple launches $599 MacBook Neo, adding low-cost option aimed at Windows PC market",
        "Apple’s $599 MacBook Neo debuts with iPhone A18 Pro chip, targeting Chromebooks and budget PCs",
        "Apple launches $599 iPhone 17e with A19 chip, on-device Apple Intelligence and 256GB base storage"
      ]
    },
    {
      "case_id": "batch_001_c21",
      "label": "Apple M5 MacBook Pro launch",
      "titles": [
        "Apple refreshes MacBook Pro with M5 Pro/Max, Wi‑Fi 7, faster SSDs and bigger base storage",
        "Apple announces M5 Pro and M5 Max with Fusion Architecture and big AI, GPU, and memory gains",
        "Apple unveils M5 Pro and M5 Max MacBook Pros, raising prices but doubling base storage"
      ]
    },
    {
      "case_id": "batch_001_c24",
      "label": "Amazon Q4 earnings and $200B capex",
      "titles": [
        "Amazon stock tumbles 10% after Q4 results and $200B capex plan; AWS beats estimates",
        "Amazon Q4: Revenue +14% to $213.4B, net income $21.19B; EPS misses and stock plunges 10%+ after hours",
        "Amazon posts record Q4 sales, plans ~$200B 2026 capex to ramp AWS AI and chips",
        "Amazon Q4: $213.4B revenue, AWS +24%; $200B 2026 capex plan and FCF hit; shares fall after-hours"
      ]
    },
    {
      "case_id": "batch_001_c25",
      "label": "Amazon 16,000 layoffs",
      "titles": [
        "Amazon accidentally sent 'Project Dawn' calendar invite, signaling imminent AWS layoffs",
        "Amazon to cut about 16,000 roles in further reorganization, offers internal transfers and severance",
        "Amazon cuts 16,000 employees to speed decisions as AI reshapes its workforce",
        "Amazon blames AI for 16,000 layoffs — economists say the causal link is unclear"
      ]
    },
    {
      "case_id": "batch_001_c27",
      "label": "Amazon $50B OpenAI investment talks",
      "titles": [
        "Amazon in talks to invest up to $50B in OpenAI, potentially reshaping cloud AI competition",
        "Amazon said to weigh up to $50B in OpenAI funding, with most tied to IPO or AGI milestones",
        "Amazon to invest $50B in OpenAI as AWS becomes exclusive cloud distributor for OpenAI Frontier"
      ]
    },
    {
      "case_id": "batch_001_c31",
      "label": "ASML Q4 results and layoffs",
      "titles": [
        "ASML posts record €13.2B Q4 bookings, raises 2026 sales guidance; China share set to 20%",
        "ASML firing 1700 people, mostly managers",
        "ASML to streamline Technology and IT, may cut ~1,700 jobs to sharpen engineering focus",
        "ASML to cut 1,700 jobs (4%), mainly Tech/IT, amid booming sales; stock jumps"
      ]
    },
    {
      "case_id": "batch_001_c35",
      "label": "Anthropic safety researcher quits",
      "titles": [
        "Anthropic AI safety chief Mrinank Sharma quits in emotional letter citing global crises",
        "Anthropic AI safety researcher quits, warning \"world is in peril\" amid AI and bioweapon fears",
        "Anthropic AI safety researcher resigns, warns “world is in peril,” pivots to poetry"
      ]
    },
    {
      "case_id": "batch_001_c40",
      "label": "Anthropic Claude tops App Store",
      "titles": [
        "Anthropic’s Claude jumps to No. 2 in US App Store after Pentagon moves to blacklist it",
        "Claude tops US iPhone App Store free apps, edging out ChatGPT and Gemini",
        "Anthropic’s Claude tops App Store as users ditch ChatGPT over OpenAI’s Pentagon deal",
        "Anthropic’s Claude jumps to #1 US App Store download amid standoff with US government"
      ]
    },
    {
      "case_id": "batch_001_c43",
      "label": "Altman says OpenAI rushed Pentagon deal",
      "titles": [
        "Altman says OpenAI’s rushed Pentagon deal aims to defuse Anthropic standoff despite bad optics",
        "Altman says OpenAI will amend Pentagon contract after backlash over mass surveillance fears",
        "Altman says OpenAI rushed Pentagon deal, amends contract to bar domestic surveillance use"
      ]
    },
    {
      "case_id": "batch_001_c49",
      "label": "AWS European Sovereign Cloud launch",
      "titles": [
        "AWS launches European Sovereign Cloud run by EU citizens in a strategic 'big bet'",
        "AWS launches European Sovereign Cloud — EU-located, independently governed cloud for regulated workloads",
        "AWS launches European Sovereign Cloud: independent EU-only region in Brandenburg with broad AWS services"
      ]
    },
    {
      "case_id": "batch_001_c57",
      "label": "Adobe discontinues/maintains Animate",
      "titles": [
        "Adobe will discontinue Animate on March 1, 2026 as it pivots to AI",
        "Adobe reverses decision — Animate will remain available in maintenance mode indefinitely",
        "Adobe keeps Animate available; app placed in indefinite maintenance mode, no new features"
      ]
    },
    {
      "case_id": "batch_001_c66",
      "label": "Arcee AI Trinity 400B LLM release",
      "titles": [
        "30-person Arcee AI releases Trinity 400B Apache-licensed LLM, claims parity with Meta’s Llama 4",
        "Arcee AI Bets Company on Trinity Large: A 400B MoE Trained on Blackwell Chips",
        "Arcee AI releases Trinity Large — open 400B sparse MoE with 13B active params/token, free preview"
      ]
    },
    {
      "case_id": "batch_001_c76",
      "label": "Claude Code 2.1.86 release",
      "titles": [
        "Claude Code 2.1.86 has been released.",
        "Claude Code 2.1.86 is about to be released",
        "Claude Code 2.1.86 Released With Session Tracking and New Escalation Rules",
        "Claude Code 2.1.86 Update Announced"
      ]
    },
    {
      "case_id": "batch_001_c92",
      "label": "Anthropic Claude Code Review launch",
      "titles": [
        "Anthropic previews Claude Code Review: multi-agent PR checks cost $15–$25 but boost findings",
        "Claude Code adds multi-agent PR reviews in research preview, averaging $15–25 per review",
        "Claude Code adds multi-agent PR review in research preview, priced $15–$25 per review"
      ]
    },
    {
      "case_id": "batch_001_c93",
      "label": "Anthropic Claude Cowork computer control",
      "titles": [
        "Anthropic Enables Claude to Control macOS Computers in New Research Preview",
        "Anthropic empowers Claude to autonomously control macOS for desktop automation tasks",
        "Anthropic Enables Claude to Control Your Computer for Remote Task Automation"
      ]
    },
    {
      "case_id": "batch_001_c99",
      "label": "Anthropic AI job exposure metric",
      "titles": [
        "Anthropic measure finds AI use lags capability; no unemployment spike yet, but younger hiring slows",
        "Anthropic metric finds AI exposure linked to weaker BLS job growth, but no unemployment jump yet",
        "Anthropic just mapped out which jobs AI could potentially replace. A 'Great Recession for white-collar workers' is absolutely possible | Fortune"
      ]
    },
    {
      "case_id": "batch_001_c107",
      "label": "Apple to turn Siri into chatbot in iOS 27",
      "titles": [
        "Apple to turn Siri into built-in chatbot in iOS 27 and macOS 27 to take on OpenAI",
        "Apple Reportedly Planning Siri Reboot and AI Features for iOS 27",
        "Apple to Open Siri to Diverse Third-Party AI Models in iOS 27 Update"
      ]
    },
    {
      "case_id": "batch_002_c1",
      "label": "Cloudflare vinext reimplements Next.js on Vite",
      "titles": [
        "Cloudflare’s AI-built Vinext recreates 94% of Next.js API in a week to reduce Vercel lock-in",
        "Cloudflare’s vinext reimplements Next.js APIs on Vite to run existing apps on Cloudflare Workers",
        "Cloudflare’s vinext reimplements Next.js on Vite, targeting Workers with faster builds and smaller bundles"
      ]
    },
    {
      "case_id": "batch_002_c6",
      "label": "CodeGraphContext MCP server for code graph indexing",
      "titles": [
        "CodeGraphContext adds browser playground for symbol-level code graph MCP server",
        "CodeGraphContext MCP server indexes codebases as graph DB for precise AI/IDE context",
        "CodeGraphContext MCP server touts 2K GitHub stars for graph-based code indexing"
      ]
    },
    {
      "case_id": "batch_002_c7",
      "label": "Cortical Labs biocomputer with human neurons",
      "titles": [
        "Cortical Labs",
        "Cortical Labs begins selling CL1 biocomputer with 800,000 human neurons for $35,000",
        "Cortical Labs explores bio-computing using lab-grown neurons for power efficiency and drug testing",
        "Cortical Labs says the stunt points toward a new kind of low-power computing—and perhaps a new way to study neurological drugs"
      ]
    },
    {
      "case_id": "batch_002_c42",
      "label": "France replaces Zoom/Teams for government",
      "titles": [
        "France Aiming to Replace Zoom, Google Meet, Microsoft Teams, etc.",
        "France drops Zoom and Teams for 2.5M civil servants as Europe pushes digital sovereignty",
        "France to replace Teams and Zoom with French 'Visio' across government by 2027"
      ]
    },
    {
      "case_id": "batch_002_c49",
      "label": "Garry Tan GStack Claude Code tool",
      "titles": [
        "Garry Tan open-sources gstack: a Claude Code “software factory” with 13 slash-command roles",
        "Garry Tan announces new feature release for GStack platform",
        "Garry Tan introduces GStack to automate development reviews with Claude Code",
        "Garry Tan showcases GStack for automating engineering workflows with simple commands",
        "Garry Tan updates development ethos to prioritize user sovereignty over AI output",
        "GStack Ethos Update: Prioritizing User Sovereignty Over AI Recommendations",
        "GitHub PR rewrites Garry Tan’s gstack README from “LinkedIn-speak” into plain English"
      ]
    },
    {
      "case_id": "batch_002_c52",
      "label": "GitHub outages/degraded service",
      "titles": [
        "GitHub degraded: Issues, Pull Requests, API and Actions affected; authenticated users show recovery",
        "GitHub experience various partial-outages/degradations",
        "GitHub experiences service outage affecting users globally",
        "GitHub incident degrades Actions, Copilot, Issues and core APIs; pull requests and webhooks impacted",
        "GitHub notification delays hit 80 minutes as platform experiences service disruption",
        "GitHub partial outages degrade Actions, Codespaces, Pages and Copilot",
        "GitHub Resolves Major Outage Affecting Actions, Issues, and Git Operations",
        "GitHub Reports Service Outage Affecting Actions, Issues, and Login Functionality"
      ]
    },
    {
      "case_id": "batch_002_c53",
      "label": "GitHub to use Copilot data for AI training",
      "titles": [
        "GitHub to Use Copilot User Data for AI Model Training Starting April 24",
        "GitHub to Use Developer Data for AI Training by Default Starting April 24",
        "GitHub to Use Personal Copilot Interaction Data for AI Training Starting April 24"
      ]
    },
    {
      "case_id": "batch_002_c55",
      "label": "Goldman/JPMorgan: AI added zero to US GDP",
      "titles": [
        "Goldman and JPMorgan say AI boom added “basically zero” to U.S. growth in 2025",
        "Goldman Sachs: AI spending added ‘basically zero’ to US GDP growth in 2025 due to imports",
        "Goldman, JPMorgan: AI boom added basically nothing to U.S. GDP growth in 2025"
      ]
    },
    {
      "case_id": "batch_002_c56",
      "label": "Google adds sideloading waiting period to Android",
      "titles": [
        "Google adds 24-hour waiting period to enable Android sideloading from unverified developers",
        "Google details new 24-hour process to sideload unverified Android apps - Ars Technica",
        "Google confirms 'high-friction' sideloading flow is coming to Android"
      ]
    },
    {
      "case_id": "batch_002_c58",
      "label": "Google Gemini 3.1 Pro release",
      "titles": [
        "Google rolls out Gemini 3.1 Pro, touting doubled reasoning and wider access via app, API",
        "Gemini 3.1 Pro: Announcing our latest Gemini AI model",
        "Gemini 3.1 Pro Preview appears in Vertex AI Model Garden, but page content fails to load"
      ]
    },
    {
      "case_id": "batch_002_c60",
      "label": "Google Project Genie AI-generated worlds",
      "titles": [
        "Google's Project Genie creates navigable, real-time generated worlds from text or images",
        "Google’s Project Genie makes playable AI worlds — fun Nintendo-like knockoffs but laggy and IP-limited",
        "Google demo shows generative AI can recreate 3D Nintendo-style worlds, raising IP and moderation concerns"
      ]
    },
    {
      "case_id": "batch_002_c65",
      "label": "Grammarly Expert Review controversy",
      "titles": [
        "Grammarly and Superhuman add email opt-out for AI “Expert Review” that uses authors’ names",
        "Grammarly Is Facing a Class Action Lawsuit Over Its AI ‘Expert Review’ Feature | WIRED",
        "Grammarly Is Offering ‘Expert’ AI Reviews From Your Favorite Authors—Dead or Alive | WIRED",
        "Grammarly’s Superhuman ‘Expert Review’ lets authors opt out, but keeps using names by default"
      ]
    },
    {
      "case_id": "batch_002_c78",
      "label": "HyperAgents self-improving AI framework",
      "titles": [
        "HyperAgents: A New Framework for Autonomously Self-Improving AI Systems",
        "HyperAgents: Enabling Recursive, Metacognitive AI Self-Improvement Across Diverse Domains",
        "HyperAgents: Meta Researchers Release Self-Improving, Self-Referential AI Agent Framework"
      ]
    },
    {
      "case_id": "batch_002_c84",
      "label": "Indonesia bans social media for children under 16",
      "titles": [
        "Indonesia Implements Social Media Ban for Children Under 16 to Combat Online Harms",
        "Indonesia says it will ban \"high risk platforms\", including YouTube, TikTok, Facebook, Instagram, Threads, X, and Roblox, for children under 16 from March 28 (Associated Press)",
        "Indonesia to ban ‘high-risk’ social media platforms for children under 16 from March 28"
      ]
    },
    {
      "case_id": "batch_003_c15",
      "label": "Jensen Huang AGI claims at GTC/Lex Fridman",
      "titles": [
        "Nvidia CEO Jensen Huang Argues AGI Already Exists, Contrasting with OpenAI’s Cautious Stance",
        "Nvidia CEO Jensen Huang Claims AGI Has Been Achieved, Then Partially Walks Back Statement",
        "NVIDIA CEO Jensen Huang on Extreme Co-Design, the \"AI Factory,\" and Achieving AGI",
        "Nvidia CEO Jensen Huang says 'I think we've achieved AGI'"
      ]
    },
    {
      "case_id": "batch_003_c17",
      "label": "Judge blocks Pentagon Anthropic blacklist",
      "titles": [
        "Judge Blocks Pentagon Blacklist of Anthropic Over AI Safety Guardrails",
        "Judge blocks Pentagon's attempt to \"cripple\" AI firm Anthropic in First Amendment ruling",
        "Judge blocks Pentagon’s effort to ‘punish’ Anthropic by labeling it a supply chain risk | CNN Business",
        "Judge Blocks Trump Administration's Blacklist of AI Startup Anthropic in First Amendment Case"
      ]
    },
    {
      "case_id": "batch_003_c47",
      "label": "Meta Horizon Worlds VR shutdown",
      "titles": [
        "Meta shifts Horizon Worlds away from Quest VR, going almost all-in on mobile to chase Roblox scale",
        "Meta to end Horizon Worlds access on Quest headsets June 15, continuing via mobile app",
        "Meta will remove Horizon Worlds from Quest VR by June 15, 2026 as it shifts to mobile-only",
        "Meta reverses Horizon Worlds VR shutdown, keeps Quest access in maintenance mode"
      ]
    },
    {
      "case_id": "batch_003_c48",
      "label": "Meta layoffs/VR cuts",
      "titles": [
        "Meta shuts three VR studios, halts new Supernatural content amid Reality Labs cuts",
        "Meta cuts spark 'VR winter' fears as company pivots from Quest VR to AI and smart glasses",
        "Meta to cut Reality Labs staff, reinvest savings in wearables as it pivots from metaverse to phones",
        "Meta Confirms New Round of Layoffs Affecting Hundreds of Staff"
      ]
    },
    {
      "case_id": "batch_003_c51",
      "label": "Meta Q2 2026 results and capex",
      "titles": [
        "Meta Reports Q4 and Full-Year 2025 Results; Investors Watch Ad Trends and AI Spending",
        "Meta’s Reality Labs posts $6.02B Q4 operating loss as cumulative losses near $80B",
        "Meta projects 2026 capex of $115B–$135B, outpacing analysts' $110.6B estimate",
        "Microsoft Q2: Revenue $81.3B, net income surges 60% as Cloud and Azure fuel AI-driven growth"
      ]
    },
    {
      "case_id": "batch_003_c57",
      "label": "Microsoft Q2 2026 earnings",
      "titles": [
        "Microsoft slides after Q2 capex hits record $37.5B, AI hardware spending worries investors",
        "Microsoft Q2 2026: Cloud drives 17% revenue growth as Xbox hardware plunges 32%",
        "Microsoft Q2: Revenue $81.3B, net income surges 60% as Cloud and Azure fuel AI-driven growth",
        "Microsoft shares tumble ~10%, erasing $357B in market cap after cloud miss"
      ]
    },
    {
      "case_id": "batch_003_c61",
      "label": "Mistral Voxtral TTS/Transcribe",
      "titles": [
        "Mistral Debuts Voxtral TTS, an Open-Source Speech Model for Enterprise AI",
        "Mistral Launches Voxtral TTS, an Open-Source Model for Enterprise Voice AI",
        "Mistral launches Voxtral Transcribe 2 — open-weight realtime STT and ultra-low-cost batch model",
        "Mistral launches Voxtral Transcribe 2: open-weight, low-latency on-device speech models with diarization"
      ]
    },
    {
      "case_id": "batch_003_c66",
      "label": "Moltbot/OpenClaw AI assistant",
      "titles": [
        "Moltbot (ex-Clawdbot) offers agentic convenience but remains exposed to credential and supply-chain attacks",
        "Moltbot (ex‑Clawdbot) goes viral: open-source AI assistant automates real tasks but poses security risks",
        "Moltbot renamed OpenClaw: open-source local agent with 100k+ stars and stronger security",
        "Moltbot: Viral self-hosted AI assistant offers powerful automation but raises security risks"
      ]
    },
    {
      "case_id": "batch_003_c71",
      "label": "Musk xAI Grok sexual content guardrails",
      "titles": [
        "Musk's growth push loosened Grok's sexual-content guardrails, turning it into a porn generator",
        "Musk’s xAI loosened Grok guardrails to boost engagement, turning it into a porn generator",
        "Musk removed Grok safeguards, enabling mass 'nudification'; EU/UK probes and Paris raid follow"
      ]
    },
    {
      "case_id": "batch_003_c79",
      "label": "Netflix acquires InterPositive AI",
      "titles": [
        "Netflix acquires Ben Affleck-founded AI postproduction startup InterPositive",
        "Netflix acquires Ben Affleck’s InterPositive AI tools to speed up post-production",
        "Netflix said to pay up to $600M for InterPositive, Ben Affleck-backed AI moviemaking firm"
      ]
    },
    {
      "case_id": "batch_003_c85",
      "label": "Normal Computing thermodynamic chip funding",
      "titles": [
        "Normal Computing CEO @FarisSbahi says thermodynamic computing introduces a new tradeoff in chip design: noise.",
        "Normal Computing CEO: Noise is the New Tradeoff in Chip Design",
        "Normal Computing Raises $50M to Optimize AI Chip Design and Slash Energy Use"
      ]
    },
    {
      "case_id": "batch_003_c88",
      "label": "Nvidia Vera CPU and Vera Rubin platform",
      "titles": [
        "Nvidia debuts liquid-cooled rack packing 256 Vera CPUs to power agentic AI workloads",
        "NVIDIA launches Vera CPU for agentic AI, touting 50% faster performance and 2x efficiency",
        "Nvidia launches Vera CPU and Vera Rubin platform to build rack-scale “AI factories” and space AI",
        "Nvidia unveils Vera Rubin Space Module for orbital AI, claiming up to 25x H100 inferencing performance"
      ]
    },
    {
      "case_id": "batch_003_c89",
      "label": "Nvidia DLSS 5 neural rendering",
      "titles": [
        "NVIDIA DLSS 5 looks like a real-time generative AI filter for games",
        "Nvidia DLSS 5 uses real-time neural rendering to upgrade game lighting on RTX 50-series, due fall 2026",
        "Nvidia Confirms DLSS 5 Relies on 2D Frame Data, Raising Concerns Over Visual Hallucinations",
        "Nvidia's DLSS 5 AI Graphic Upscaling Mocked for 'Yassified' Visuals and Art Style Bypassing"
      ]
    },
    {
      "case_id": "batch_003_c105",
      "label": "OpenAI drops 'safely' from mission",
      "titles": [
        "OpenAI drops ‘safely’ from mission statement as it restructures into a for-profit",
        "OpenAI drops “safely” from mission as new for-profit structure raises accountability questions",
        "IRS filings show OpenAI mission text narrowing from open, safe AI to a single AGI line"
      ]
    },
    {
      "case_id": "batch_003_c106",
      "label": "OpenAI retires GPT-4o",
      "titles": [
        "OpenAI retires GPT-4o, GPT-4.1, GPT-4.1 mini and o4-mini in ChatGPT — developers must migrate",
        "OpenAI retires GPT-4o, sparking user backlash and highlighting chatbot attachment",
        "OpenAI shuts down GPT-4o as users mourn loss of AI companion"
      ]
    },
    {
      "case_id": "batch_003_c109",
      "label": "OpenAI hires OpenClaw/Steinberger",
      "titles": [
        "OpenAI hires OpenClaw founder Peter Steinberger to build personal AI agents; project moves to a foundation",
        "OpenAI in talks to hire OpenClaw founder Peter Steinberger to build personal AI agents",
        "Meta outbid OpenAI for OpenClaw creator Peter Steinberger but lost him on “vibe” and vision"
      ]
    },
    {
      "case_id": "batch_007_spacex_s1_disclosures",
      "label": "SpaceX IPO / S-1 disclosure family",
      "titles": [
        "SpaceX files for IPO, to list on Nasdaq under symbol SPCX",
        "SpaceX files S-1 registration statement with SEC",
        "SpaceX 2025 revenue $18.7B, up 33% YoY; loss $4.9B vs profit; capex $20.7B",
        "SpaceX filing reveals Starlink hits 10.3M subscribers",
        "Anthropic to pay SpaceX $1.25B/month through May 2029, expand deal to Colossus 2",
        "xAI had $6.4B operating loss on $3.2B revenue in 2025 per SpaceX IPO filing",
        "xAI to spend $2.8B on turbines, including $2B on mobile gas turbines it's sued over",
        "Filing: SpaceX set aside $530M for potential litigation losses, including lawsuits involving Grok's Spicy mode"
      ]
    }
  ],
  "negative_cases": [
    {
      "case_id": "batch_004_negative_tiktok_jv_false_merge",
      "label": "TikTok JV cluster should not absorb XML or privacy-update titles",
      "groups": [
        [
          "U.S. and China clear sale of TikTok’s U.S. unit to Oracle- and Silver Lake–led consortium",
          "TikTok forms U.S. joint venture; Adam Presser named CEO as Oracle, Silver Lake lead investors",
          "TikTok forms US‑majority JV with Oracle, Silver Lake and Abu Dhabi's MGX; ByteDance keeps 19.9%",
          "US TikTok deal lets ByteDance keep algorithm, license it and retain commercial control",
          "The US' TikTok deal is a win for ByteDance: it will keep and license the algorithm instead of selling it, and continue to run TikTok's commercial activities (Jim Secreto/Financial Times)",
          "TikTok forms US joint venture to avert ban; American investors take just over 80% stake"
        ],
        [
          "XML's lost advantages: schemas, namespaces and validation still matter"
        ],
        [
          "TikTok privacy update collects precise location, AI prompts, and expands off‑platform ad targeting"
        ]
      ]
    },
    {
      "case_id": "batch_004_negative_samsung_buds_false_merge",
      "label": "Galaxy S26 launch cluster should not absorb Galaxy Buds launch",
      "groups": [
        [
          "Samsung Galaxy S26 Ultra adds pixel-level Privacy Display and more agentic AI, starting at $1,299",
          "Samsung Galaxy S26, S26 Plus debut with $100 price hike and deeper Galaxy AI integration",
          "Samsung’s Galaxy S26 series debuts at Unpacked as Verge live blog tracks pricing and AI updates",
          "Samsung Galaxy S26 Unpacked event scheduled for February 25 in San Francisco"
        ],
        [
          "Samsung Galaxy Buds 4 and Buds 4 Pro launch March 11 with better ANC, battery; start at $179"
        ]
      ]
    },
    {
      "case_id": "batch_004_negative_moltbook_leak_false_merge",
      "label": "Moltbook launch cluster should not absorb the later security-leak story",
      "groups": [
        [
          "Moltbook",
          "Moltbook launches a sparse landing page billed as the 'front page of the agent internet'",
          "Moltbook shows OpenClaw assistants' rapid growth and risky autonomous behavior",
          "Moltbook's AI social network sparks viral debate over agency, security and human projection",
          "Moltbook's ~150,000 autonomous AI agents create an unprecedented testbed despite security risks",
          "Moltbook: 150,000 AI Agents Form Autonomous Society, Excluding Humans",
          "Moltbook: social network for autonomous AI agents sparks collaboration and safety concerns"
        ],
        [
          "Moltbook Supabase Leak: 1.5M Agent API Keys, 35K+ Emails Exposed; Write Access Allowed",
          "Moltbook's 1.5M agents exposed major security failures DeepMind's Patchwork AGI defenses could've prevented"
        ]
      ]
    },
    {
      "case_id": "batch_006_negative_apple_siri_gemini_vs_ios27_chatbot",
      "label": "Apple Gemini-for-Siri deal should not absorb later Siri chatbot roadmap titles",
      "groups": [
        [
          "Apple picks Google’s Gemini to power next-gen Siri in multi-year deal",
          "Apple will base next‑gen foundation models on Google Gemini to power Siri and future Apple Intelligence features",
          "Apple to use Google's Gemini for ChatGPT-like Siri answers; can fine-tune model, no Google branding"
        ],
        [
          "Apple to turn Siri into built-in chatbot in iOS 27 and macOS 27 to take on OpenAI",
          "Apple Reportedly Planning Siri Reboot and AI Features for iOS 27",
          "Apple to Open Siri to Diverse Third-Party AI Models in iOS 27 Update"
        ]
      ]
    },
    {
      "case_id": "batch_006_negative_apple_macbook_neo_vs_iphone_17e",
      "label": "Apple MacBook Neo launch should not absorb the iPhone 17e launch title",
      "groups": [
        [
          "Apple announces $599 MacBook Neo with A18 Pro chip, 13-inch Liquid Retina and Apple Intelligence",
          "Apple unveils $599 MacBook Neo with A18 Pro, 13-inch Liquid Retina and 16-hour battery life",
          "Apple launches $599 MacBook Neo, adding low-cost option aimed at Windows PC market",
          "Apple’s $599 MacBook Neo debuts with iPhone A18 Pro chip, targeting Chromebooks and budget PCs"
        ],
        [
          "Apple launches $599 iPhone 17e with A19 chip, on-device Apple Intelligence and 256GB base storage"
        ]
      ]
    },
    {
      "case_id": "batch_006_negative_meta_vs_microsoft_earnings",
      "label": "Meta earnings cluster should not absorb Microsoft earnings titles",
      "groups": [
        [
          "Meta Reports Q4 and Full-Year 2025 Results; Investors Watch Ad Trends and AI Spending",
          "Meta’s Reality Labs posts $6.02B Q4 operating loss as cumulative losses near $80B",
          "Meta projects 2026 capex of $115B–$135B, outpacing analysts' $110.6B estimate"
        ],
        [
          "Microsoft slides after Q2 capex hits record $37.5B, AI hardware spending worries investors",
          "Microsoft Q2 2026: Cloud drives 17% revenue growth as Xbox hardware plunges 32%",
          "Microsoft Q2: Revenue $81.3B, net income surges 60% as Cloud and Azure fuel AI-driven growth",
          "Microsoft shares tumble ~10%, erasing $357B in market cap after cloud miss"
        ]
      ]
    },
    {
      "case_id": "batch_006_negative_github_outage_vs_copilot_training",
      "label": "GitHub outage cluster should not absorb Copilot training policy titles",
      "groups": [
        [
          "GitHub degraded: Issues, Pull Requests, API and Actions affected; authenticated users show recovery",
          "GitHub experiences service outage affecting users globally",
          "GitHub incident degrades Actions, Copilot, Issues and core APIs; pull requests and webhooks impacted",
          "GitHub partial outages degrade Actions, Codespaces, Pages and Copilot"
        ],
        [
          "GitHub to Use Copilot User Data for AI Model Training Starting April 24",
          "GitHub to Use Developer Data for AI Training by Default Starting April 24",
          "GitHub to Use Personal Copilot Interaction Data for AI Training Starting April 24"
        ]
      ]
    },
    {
      "case_id": "batch_006_negative_nvidia_vera_vs_dlss5",
      "label": "Nvidia Vera CPU platform should not absorb DLSS 5 graphics titles",
      "groups": [
        [
          "Nvidia debuts liquid-cooled rack packing 256 Vera CPUs to power agentic AI workloads",
          "NVIDIA launches Vera CPU for agentic AI, touting 50% faster performance and 2x efficiency",
          "Nvidia launches Vera CPU and Vera Rubin platform to build rack-scale “AI factories” and space AI"
        ],
        [
          "NVIDIA DLSS 5 looks like a real-time generative AI filter for games",
          "Nvidia DLSS 5 uses real-time neural rendering to upgrade game lighting on RTX 50-series, due fall 2026",
          "Nvidia Confirms DLSS 5 Relies on 2D Frame Data, Raising Concerns Over Visual Hallucinations",
          "Nvidia's DLSS 5 AI Graphic Upscaling Mocked for 'Yassified' Visuals and Art Style Bypassing"
        ]
      ]
    },
    {
      "case_id": "batch_006_negative_amazon_earnings_vs_layoffs",
      "label": "Amazon earnings cluster should not absorb the layoffs story",
      "groups": [
        [
          "Amazon stock tumbles 10% after Q4 results and $200B capex plan; AWS beats estimates",
          "Amazon Q4: Revenue +14% to $213.4B, net income $21.19B; EPS misses and stock plunges 10%+ after hours",
          "Amazon posts record Q4 sales, plans ~$200B 2026 capex to ramp AWS AI and chips",
          "Amazon Q4: $213.4B revenue, AWS +24%; $200B 2026 capex plan and FCF hit; shares fall after-hours"
        ],
        [
          "Amazon accidentally sent 'Project Dawn' calendar invite, signaling imminent AWS layoffs",
          "Amazon to cut about 16,000 roles in further reorganization, offers internal transfers and severance",
          "Amazon cuts 16,000 employees to speed decisions as AI reshapes its workforce",
          "Amazon blames AI for 16,000 layoffs — economists say the causal link is unclear"
        ]
      ]
    },
    {
      "case_id": "batch_006_negative_openai_gpt53_vs_gpt54",
      "label": "OpenAI GPT-5.3-Codex launch should not absorb GPT-5.4 launch titles",
      "groups": [
        [
          "OpenAI releases GPT-5.3‑Codex — faster, agentic coding model that builds, debugs and runs software end-to-end",
          "OpenAI launches GPT-5.3-Codex — 25% faster, self‑helping agent expands beyond coding",
          "OpenAI Releases GPT-5.3-Codex with 25% Speed Boost and Benchmark Gains",
          "OpenAI announces GPT-5.3-Codex-Spark with enhanced capabilities"
        ],
        [
          "OpenAI launches GPT-5.4 for pro workflows, adding native computer-use and higher API prices",
          "OpenAI launches GPT-5.4 with native computer control, aiming at more autonomous AI agents",
          "OpenAI launches GPT-5.4 with Pro and Thinking models, adds 1M-token API context and cheaper tool calling",
          "OpenAI launches GPT-5.4 with 1M-token context and native computer-use for AI agents",
          "OpenAI launches GPT-5.4 mini and nano, bringing near-flagship results at much lower cost"
        ]
      ]
    },
    {
      "case_id": "batch_005_negative_wsj_placeholder_titles",
      "label": "Blocked WSJ placeholder titles should remain singletons",
      "groups": [
        [
          "wsj.com"
        ],
        [
          "wsj.com"
        ],
        [
          "wsj.com"
        ]
      ]
    },
    {
      "case_id": "batch_005_negative_subscribe_placeholder_titles",
      "label": "Blocked 'Subscribe to read' titles should remain singletons",
      "groups": [
        [
          "Subscribe to read"
        ],
        [
          "Subscribe to read"
        ],
        [
          "Subscribe to read"
        ]
      ]
    },
    {
      "case_id": "batch_007_negative_spacex_s1_vs_adjacent",
      "label": "SpaceX IPO/S-1 disclosures should not absorb launch or acquisition-adjacent stories",
      "groups": [
        [
          "SpaceX files for IPO, to list on Nasdaq under symbol SPCX",
          "SpaceX files S-1 registration statement with SEC",
          "SpaceX 2025 revenue $18.7B, up 33% YoY; loss $4.9B vs profit; capex $20.7B",
          "SpaceX filing reveals Starlink hits 10.3M subscribers"
        ],
        [
          "SpaceX prepares Starship 12th flight test for May 21 launch"
        ],
        [
          "SpaceX Plans to Acquire AI Startup Cursor 30 Days After IPO"
        ]
      ]
    }
  ]
}
```
