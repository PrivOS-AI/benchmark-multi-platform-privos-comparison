# benchmark-multi-platform-privos-comparison

How many tokens does an AI agent spend to run the same sales-ops work when chat, tasks and CRM live in separate tools
connected over MCP, versus in PrivOS (one workspace)?

## Live result (2026-10-06)

A real agent loop (`glm-5.3`, temperature 0) ran three jobs — lead intake, pipeline sync, daily digest — 5 times per
stack against real test workspaces; tokens are the provider's own `usage` counts. Full write-up, tables and limits:
**[docs/live-agent-results.md](docs/live-agent-results.md)**.

| vs PrivOS (sum of per-job medians) | Total tokens | Uncached input | Est. cost (cached = 0.2×) | USD at GLM-5.3 list price |
|---|--:|--:|--:|--:|
| Telegram + Notion + HubSpot, MCP servers as installed (47 tools = 45 MCP + 2 Telegram) | **3.64×** | 1.09× | 2.16× | 2.22× |
| Slack + Notion + HubSpot, MCP servers as installed (53 tools) | **3.49×** | 1.13× | 2.23× | 2.29× |
| Minimal hand-written tools on every side (control) | 1.12× | | | |

**Multi-tool stacks vs PrivOS: ~3.5–3.6× the tokens with MCP servers as installed (~2.2–2.3× estimated cost with prompt
caching).**

Headline ratios are sums of per-job medians over 5 runs (A 3.64×, C 3.49×); using means instead gives ~3.2× (A 3.24×,
C 3.16×), because a few long PrivOS runs raise its mean. The video's on-screen 4.1–4.6× comes from its own 3-run
recording; its end card quotes this 5-run headline.

The gap comes from loading full MCP tool catalogs (~31k tokens re-sent on every call); most of it hits the prompt
cache. On larger lists PrivOS currently uses more tokens (~1.4×). Limits, including that and a hand-filtered MCP best
case: [docs/live-agent-results.md#limits](docs/live-agent-results.md#limits).

```bash
cd agent_runner && cp .env.example .env   # TEST workspaces only; needs Python 3.9+ and Node.js (npx runs the MCP servers)
python3 seed.py                           # seeds every platform configured in .env (or name them: seed.py notion hubspot slack)
# post the 3 printed messages in the Telegram test group yourself, as an anonymous admin (bots can't see other bots)
./run_all.sh && python3 report.py results/
```

See [Reproduce](docs/live-agent-results.md#reproduce) for the full steps.

## Historical: modelled estimate (superseded)

> The section below is the original **modelled** benchmark (fixtures + tokenizer, no live agent). Its 6.0–6.4× figure
> assumed every tool schema and payload is counted once per session; the live run above replaces it. Kept for the
> method and the sensitivity knobs.

### Modelled result

Three workflows (lead intake, pipeline sync, daily digest), measured with a real
tokenizer (`tiktoken/cl100k_base`), against **two** multi-platform stacks to show
the gap is not vendor-specific. PrivOS unified is the baseline.

**Telegram + Notion + HubSpot vs PrivOS**

| Component | Multi-platform | PrivOS | Ratio |
|---|--:|--:|--:|
| Fixed: tool schemas loaded into context | 9,056 | 558 | **16.2×** |
| Variable: request + response payloads | 4,563 | 1,186 | **3.9×** |
| Orchestration: per-call framing | 700 | 500 | 1.4× |
| **Session total** | **14,319** | **2,244** | **6.4×** |
| Workflow-only (schemas amortized) | 5,263 | 1,686 | **3.1×** |
| API calls across 3 workflows | 14 | 10 | — |

**Discord + Trello + HubSpot vs PrivOS**

| Component | Multi-platform | PrivOS | Ratio |
|---|--:|--:|--:|
| Fixed: tool schemas | 9,241 | 558 | **16.6×** |
| Variable: payloads | 3,508 | 1,186 | **3.0×** |
| Orchestration framing | 650 | 500 | 1.3× |
| **Session total** | **13,399** | **2,244** | **6.0×** |
| Workflow-only (amortized) | 4,158 | 1,686 | **2.5×** |
| API calls | 13 | 10 | — |

Both stacks land in the same band: **6.0–6.4× per short session**, **2.5–3.1×
amortized** across a long session where tool schemas are cached. (Discord+Trello
is slightly lighter: Trello basic cards need no schema pre-fetch, unlike Notion —
but its card responses are verbose, so the gap persists.)

**Read it as a range, not a point.** Both are lower bounds — model reasoning
tokens (excluded here) grow with hop count and would widen the gap.

### Why the model predicted a gap

1. **16× on fixed cost** — three MCP catalogs (~49 tools) vs one (~6 tools) sit in
   context all session.
2. **~4× on variable cost** — the multi-platform flow needs **pre-fetch calls**
   (Notion `retrieveDatabase` before create; HubSpot `search` before update) that
   don't exist in a unified model, and its **responses are far more verbose**
   (Notion page echo, HubSpot property objects). 14 calls vs 10.
3. **Framing** scales with call count, so more hops cost more even before payload.

## The condition for the result to hold

PrivOS wins **when data is native to PrivOS**. If PrivOS merely proxies back out
to HubSpot/Notion over MCP, it re-inherits their verbosity. The benchmark assumes
native rooms/lists — the actual PrivOS design.

## Run it

```bash
pip install -r requirements.txt
python run_benchmark.py                 # modeled benchmark (all stacks)
python src/live_capture.py              # optional: real API sizes, read-only
```

`run_benchmark.py` auto-discovers every `fixtures/<name>/` stack and compares each
non-baseline stack to the baseline (PrivOS). Outputs console tables +
`results/benchmark_result.json` (full per-call, per-scenario, per-server breakdown).

## Live-capture (real evidence)

Turn modeled fixtures into captured measurements. With credentials in `.env`,
`src/live_capture.py` performs one **read-only** GET per platform and records the
real response's token size (`results/live_capture.json`). No credentials → each
platform skips cleanly; nothing remote is mutated. See `docs/live-capture.md`.

## Layout

```
run_benchmark.py                       entry point (multi-stack discovery)
src/token_counter.py                   tiktoken-backed token counting (+ fallback)
src/cost_model.py                      fixed + variable + orchestration cost model
src/benchmark_report.py                per-comparison table + ratio rendering
src/live_capture.py                    read-only real-API capture (credential-gated)
fixtures/privos/                       baseline: tools.json + scenarios.json
fixtures/multiplatform/                Telegram + Notion + HubSpot
fixtures/multiplatform-discord-trello/ Discord + Trello + HubSpot
docs/methodology.md                    rationale, controls, limitations
docs/references.md                     API-doc citations grounding the fixtures
docs/live-capture.md                   live-capture usage + safety
docs/live-agent-results.md             live agent benchmark: results, limits, reproduce
agent_runner/                          live agent loop, seeding, report, redaction, demo
results/agent_runs/                    redacted per-call JSON of the live runs
.env.example                           credential template
results/                               generated output
```

## Adjust the assumptions (sensitivity)

Every assumption is a data value, not buried in code:

- **Tool counts** → `fixtures/*/tools.json` (`tool_count`).
- **Per-call framing** → `PER_CALL_FRAMING_TOKENS` in `src/cost_model.py`.
- **Add a platform stack** → drop a `fixtures/<name>/` folder with `tools.json`
  (`"baseline": false`) + `scenarios.json`; the runner picks it up automatically.

Change any, re-run, compare.

## Scope & honesty

Payloads mirror documented API object shapes (`docs/references.md`) but are **not
live captures** (until you run live-capture); tool counts and framing are
estimates; the tokenizer is a BPE proxy whose *ratios* are robust. This measures
**token cost only** — not feature depth. See `docs/methodology.md` → Limitations
for the full list, and `docs/live-capture.md` to upgrade to recorded API
responses (needs platform credentials + a PrivOS bot token).

## License

MIT — see [LICENSE](LICENSE). The licence covers the benchmark code and docs; tool schemas and API responses
recorded in `results/agent_runs/` belong to their respective vendors.

The installer is source-available today; we plan to open the full PrivOS source in the near future (no date yet).
