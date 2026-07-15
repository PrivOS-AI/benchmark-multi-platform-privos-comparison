# benchmark-multi-platform-privos-comparison

A reproducible token-cost benchmark comparing how many tokens an AI agent spends
to run the same operations workflows on:

- **Multi-platform stack** — Telegram (chat) + Notion (tasks) + HubSpot (CRM),
  each connected as a separate MCP server; versus
- **PrivOS unified stack** — chat, tasks, and CRM as rooms + list items over one
  hub `/api/v1` surface.

Tokens are what an LLM agent actually pays for (price, latency, context pressure),
so "which stack is cheaper to operate as an agent" reduces to "which stack makes
the model read fewer tokens for the same work."

## Result

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

### Why the gap exists (all three confirmed by the numbers)

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
pip install -r requirements.txt        # or: ~/.claude/skills/.venv/bin/python3
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
