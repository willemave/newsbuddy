# Onboarding Fast-Model Evaluation

Use `scripts/run_onboarding_audio_plan_evals.py` to compare strict structured tool-call
performance for the latency-sensitive audio lane-planning step. The evaluator reuses the
production prompt and `_AudioPlanOutput` schema.

The default candidates are Kimi K2.6 pinned to DeepInfra through OpenRouter and DeepSeek V4 Flash
0731 pinned separately to CoreWeave FP8, Baseten FP8, Wafer Fast, and Reka FP4.
OpenRouter fallbacks are disabled, required parameters are enforced, reasoning is disabled, and
each request requires zero data retention and denies provider data collection.

GLM 5.3 Fireworks, GLM 5.3 Baseten, and GLM 5.3 Flash DeepInfra are available as explicit,
non-default low-reasoning probes: `glm_5_3_fireworks_low`, `glm_5_3_baseten_low`, and
`glm_5_3_flash_deepinfra_low`. GLM reasoning cannot be disabled, so these routes request low effort
and exclude the reasoning trace.

The 2026-08-29 streaming benchmark used the same technical-and-business narration and strict tool
schema for three calls per route. TTFT is the first emitted tool-argument token; total latency ends
after the complete validated tool call. All routes were pinned with fallbacks disabled, required
parameters, denied data collection, and ZDR:

| Model | Provider | Valid | Median TTFT | Median total |
| --- | --- | ---: | ---: | ---: |
| GLM 5.3 | Baseten FP4 | 3/3 | 880 ms | 3,339 ms |
| GLM 5.3 | Fireworks | 3/3 | 687 ms | 5,586 ms |
| GLM 5.3 | Novita FP8 | 0/3 | n/a | n/a |
| GLM 5.3 Flash | DeepInfra FP8 | 3/3 | 9,938 ms | 9,950 ms |
| GLM 5.3 Flash | Wafer | 3/3 | 6,081 ms | 9,956 ms |
| GLM 5.3 Flash | Reka FP8 | 3/3 | 14,811 ms | 14,813 ms |

Reka had a 73-second outlier. DeepInfra and Reka largely buffered the structured call before the
first visible tool token. The 18 attempted calls cost $0.0146 in total. Baseten is the only tested
GLM route competitive enough for the onboarding path; its perceived-link quality still needs the
same corrected judge comparison before selection.

An apples-to-apples three-run shortlist benchmark used the same narration, schema, and streaming
TTFT definition:

| Candidate | Median TTFT | Median total | Median request cost | Link-quality score |
| --- | ---: | ---: | ---: | ---: |
| GLM 5.3 / Baseten FP4 | 880 ms | 3,339 ms | $0.00159 | pending |
| DeepSeek V4 Flash 0731 / Wafer Fast | 1,104 ms | 2,449 ms | $0.00047 | 0.70 |

OpenRouter returned exact per-request costs for GLM and DeepSeek. GLM's larger median cost includes
a cached prompt on the median run; the uncached first run cost $0.00245.

The current production trial uses DeepSeek V4 Flash 0731 pinned to Wafer Fast throughout onboarding:
profile generation, voice parsing, audio planning, and final source selection. A live audio-plan call
completed in 3,410 ms without heuristic fallback, and a live final-selector canary completed in
1,857 ms with grounded suggestions. Unrelated product model routes remain unchanged.

The follow-up three-run production-shape comparison measured DeepSeek/Wafer and GLM Flash/Modal
with native strict JSON output. GLM Flash/Baseten and Nitro used strict tool output because Baseten
does not advertise native JSON-schema output:

| Route | Valid | Median TTFT | Median total | Median request cost |
| --- | ---: | ---: | ---: | ---: |
| DeepSeek V4 Flash 0731 / Wafer | 3/3 | 1,401 ms | 2,310 ms | $0.00026 |
| GLM 5.3 Flash / Modal | 3/3 | 429 ms | 5,709 ms | $0.00025 |
| GLM 5.3 Flash / Baseten | 0/3 | n/a | n/a | n/a |
| GLM 5.3 Flash Nitro | 3/3 | 7,066 ms | 12,051 ms | $0.00050 |

Baseten is ZDR-compatible, but its GLM Flash endpoint supports `tool_choice` only as `none` or
`auto`, not `required` or a named function. The benchmark's `tool_choice=required` and
`require_parameters=true` therefore filtered it out before inference. Nitro selected Wafer on all
three calls. Modal delivered its first JSON token quickly but took more than twice as long as
DeepSeek to finish the validated object. The nine successful calls cost $0.00316 total.

The command is a dry run unless `--execute` is supplied:

```bash
uv run python scripts/run_onboarding_audio_plan_evals.py
```

The default dataset makes 30 model calls when executed: 15 candidate-provider calls plus 15
perceived-link-quality judge calls through the locally authenticated Codex CLI using
`gpt-5.6-sol` with high reasoning. The judge considers relevance to the narration, diversity of
likely links, and practical search quality. It does not use a generated reference answer; score
0.70 passes deterministically. No live calls occur during a dry run.
Reduce the first experiment to Kimi and CoreWeave with:

```bash
uv run python scripts/run_onboarding_audio_plan_evals.py \
  --candidates kimi_deepinfra deepseek_coreweave \
  --execute
```

Each result records full structured-call latency, schema validity, whether a tool call was present,
normalization fallback, deterministic prompt-contract checks, normalized output, and the semantic
judge verdict. Edit `tests/evals/onboarding_audio_plan.yaml` to add a transcript.
