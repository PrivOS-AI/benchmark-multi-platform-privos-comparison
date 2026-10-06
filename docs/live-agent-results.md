# Live agent benchmark — results (2026-10-06)

A real agent loop runs the same three sales-ops jobs against real test workspaces and counts the tokens the model
provider reports. Code: [`agent_runner/`](../agent_runner) · per-call JSON (redacted):
[`results/agent_runs/official-20261006/`](../results/agent_runs/official-20261006) · tables recomputed offline by
`agent_runner/report.py`.

## Setup

- One agent loop (OpenAI-compatible tool calling), model **`glm-5.3`** via the Z.ai coding endpoint, temperature 0.
  The returned model is asserted on every call (584/584 returned `glm-5.3`).
- Same seed data on every side, data reset before every stack-run, jobs run in order 1 → 2 → 3 within a run,
  **5 runs** per stack (120 job runs, 584 model calls, including the tool-filtered arms described under Limits).
- Batches: `20261006-100213` (Stack C and PrivOS), `20261006-103305` (Stack C and PrivOS controls),
  `20261006-132724` (all Stack A arms).
- Tokens = `prompt_tokens + completion_tokens` from the provider's `usage`; cached prompt tokens are tracked separately.
- Every result records its harness: MCP package versions, tool-schema hash, system-prompt hash, PrivOS skill commit.

| Stack | Integration | Tools in context | Fixed prompt / call |
|---|---|--:|--:|
| **A full** — Telegram + Notion + HubSpot, as installed | `@notionhq/notion-mcp-server@2.5.2` (24 tools) + `@hubspot/mcp-server@0.4.0` (21) + 2 thin Telegram tools¹ | 47 | ~31.3k |
| **C full** — Slack + Notion + HubSpot, as installed | `@modelcontextprotocol/server-slack@2025.4.25` (8, archived MCP reference server) + the same Notion and HubSpot servers | 53 | ~32.0k |
| **PrivOS** | PrivOS agent skills (`privos-chat`, `privos-list`) run through one restricted `bash` tool; skill docs in the system prompt² | 1 | ~9.1k |
| Control — A / C / PrivOS | hand-written minimal tools on every side | 8 / 8 / 6 | ~0.9k |

¹ No MCP server can read Telegram group messages, so Stack A keeps two minimal Bot API tools (47 = 45 MCP + 2).
² The skill docs are not public yet; result files carry their SHA-256 and length instead of the text.

**Jobs**

1. *Lead intake* — read a customer's chat message → create a follow-up task → move her deal to Quote Sent.
2. *Pipeline sync* — find tasks marked Done → move that customer's deal to Closed Won → post a notice in chat.
3. *Daily digest* — read the chat → create a task per request → mark a contact Sales Qualified Lead.

All job runs ended with a final answer; in **119/120 the agent reported the job done** (agent-reported — the runner
does not re-read the target systems; the recorded demo runs do, see Video). The one failure is in a tool-filtered arm
(see Limits). Max 9 model calls per run (cap 20); mean 4.5–5.3 calls per job per stack.

## Results — sum of per-job medians

| Stack | Total tokens | Uncached input | Cached input | Output | Cost units (c=0.2)³ | USD⁴ |
|---|--:|--:|--:|--:|--:|--:|
| A full | 536,773 | 12,911 | 487,680 | 2,535 | 120,587 | 0.1560 |
| C full | 514,334 | 13,406 | 498,304 | 2,791 | 124,231 | 0.1606 |
| **PrivOS** | **147,438** | 11,872 | 131,968 | 4,370 | 55,746 | 0.0702 |
| Control A / C / PrivOS | 45,065 / 44,821 / 40,170 | | | | 26,417 / 27,118 / 24,667 | 0.0338 / 0.0350 / 0.0311 |

³ uncached input × 1 + cached input × c + output × 4. Share of prompt tokens served from the provider's cache:
~92–97% on A full, C full and PrivOS, ~70% on the controls.
⁴ At GLM-5.3 list prices, USD per 1M tokens: input 1.40 · cached input 0.26 · output 4.40
(`PRICE_INPUT=1.4 PRICE_CACHED=0.26 PRICE_OUTPUT=4.4 python3 report.py …`). Sum of per-job medians.

### Ratios vs PrivOS

| Stack | Total tokens | Uncached input | Cost c=0.1 | c=0.2 | c=0.5 | USD |
|---|--:|--:|--:|--:|--:|--:|
| **A full** | **3.64×** | 1.09× | 1.69× | 2.16× | 2.80× | **2.22×** |
| **C full** | **3.49×** | 1.13× | 1.75× | 2.23× | 2.87× | **2.29×** |
| Control A / C vs control PrivOS | 1.12× / 1.12× | 1.18× / 1.36× | | 1.07× / 1.10× | | 1.09× / 1.13× |

### Per job — total tokens, median [min–max] over 5 runs

| Job | A full | C full | PrivOS |
|---|--:|--:|--:|
| Lead intake | 134,693 [100k–168k] | 104,346 [103k–140k] | 34,724 [34k–45k] |
| Pipeline sync | 235,488 [200k–268k] | 235,265 [201k–275k] | 54,931 [44k–110k] |
| Daily digest | 166,592 [133k–202k] | 174,723 [139k–177k] | 57,783 [48k–90k] |

## What it means

**Multi-tool stacks vs PrivOS: ~3.5–3.6× the tokens with MCP servers as installed (~2.2–2.3× estimated cost with prompt
caching).**

- **The gap is the size of the tool catalog, not the work.** Uncached input (the new information each job processes) is
  ≈1.0–1.1× on every stack. Full MCP catalogs (~31–32k tokens) are re-sent on every call.
- **Where PrivOS is heavier:** its skill docs (~9k tokens) are re-sent on every call, it writes more output tokens, and
  on larger lists it reads more (see Limits).

## Limits

- **Larger lists favour the multi-tool stack.** In an earlier run (2026-10-01: +24 tasks, +10 deals, +10 contacts, +12 chat
  messages; minimal tools; Slack stack only; 3 runs) PrivOS used **~1.4× the tokens** of Slack + Notion + HubSpot
  (stack ÷ PrivOS = 0.7×), because PrivOS list reads return every item in full while Notion and HubSpot filter
  server-side (no name search or field projection on PrivOS item reads yet). Not re-measured at n=5.
- **Tool-filtered MCP (best case).** We also ran the same MCP servers restricted to exactly the 10 tools these three
  jobs use (Stack A: 8 of 45 vendor tools + 2 Telegram tools; Stack C: 10 of 53). Filtered, MCP used 0.80× (A) and
  0.95× (C) the tokens of PrivOS (USD 0.77× / 0.89×). The filtered setup is a best case: we hand-picked exactly the 10
  tools those three jobs need. In real operations you can't know that in advance. Agents handle mixed, changing
  requests, so teams end up loading a broad set of tools across several servers, much closer to the as-installed numbers
  (~3.5–3.6×). And if you do want a hand-tuned ideal setup, PrivOS can be trimmed the same way, so best case vs. best
  case isn't a win for MCP either. Per-arm tables:
  [`REPORT.md`](../results/agent_runs/official-20261006/REPORT.md) (`stack_*_mcp_min`).
- **1/120 runs failed and is included:** A filtered, pipeline sync, run 1 — an agent error on the MCP side: the model
  mistyped one 4-character group of the Notion data-source id, got "object not found" and ended with "I couldn't
  complete the task". Excluding it moves A filtered from 0.80× to 0.82× (USD 0.77× → 0.78×).
- One model, one small business scenario, 5 runs. Single runs reach up to ~2× the median (e.g. PrivOS pipeline sync
  110k vs 55k); see min–max. Using means instead of medians gives ~3.2× (A full 3.24×, C full 3.16×), because a few
  long PrivOS runs pull its mean up.
- **Discarded runs:** an earlier Stack A measurement was split over three batches and its pipeline-sync re-run started
  from a different deal stage than Stack C; 15 of its runs also failed to post to Telegram after the test group was
  migrated to a supergroup. All of it was discarded and Stack A was re-run in one batch (`20261006-132724`). No other
  runs were dropped.
- Cost units are a weighting; the USD column uses one provider's list prices and ignores plan pricing.
- The Slack server is the archived MCP reference implementation.
- The PrivOS skills ran outside the PrivOS sandbox (same scripts, bot key), through a `bash` tool limited to those scripts.
- Transcripts in the published JSON are redacted (IDs, names, hosts, paths); token counts, model, endpoint and MCP
  versions are untouched. `publish.py --check` is a pattern-based check (IDs, names, hosts, IPs, paths, common credential
  formats), not a guarantee.

## Reproduce

Needs Python 3.9+, **Node.js with `npx`** (the runner starts the MCP servers with `npx -y <package>`), test accounts for
Telegram (bot + group), Slack, Notion and HubSpot, and an OpenAI-compatible model endpoint. The `privos_skill` and
`privos` arms also need **a PrivOS test workspace (hub URL, bot key, room with Tasks/CRM lists) and a checkout of the
PrivOS agent skills, which are not public yet** (`PRIVOS_SANDBOX_DIR`). The MCP and control arms run without PrivOS:
leave the `PRIVOS_*` variables empty and those arms are skipped.

```bash
cd agent_runner
cp .env.example .env            # fill with TEST workspace credentials only
python3 seed.py                 # seeds every platform configured in .env, idempotent; or name them:
                                #   python3 seed.py notion hubspot slack
# Telegram can't be seeded by a bot: post the 3 printed messages in the test group yourself, as an anonymous admin
# (bots can't see other bots; Telegram keeps them for the bot for 24 h)
python3 runner.py --list-tools stack_c_mcp        # inspect what each stack loads
./run_all.sh                    # 5 runs × all configured stacks, reset between runs (~1.5 h)
python3 report.py results/      # recompute every table from the per-call JSON
python3 publish.py results/ OUT # redacted copy for sharing; exits 1 on any leak pattern it knows
```

`python3 demo.py --runs 1` (needs PrivOS) runs the same benchmark as a narrated terminal session (A full, C full and PrivOS; add
`--with-filtered` for the tool-filtered arms).

## Video

The published video is a separate narrated recording of the as-installed setups (`demo.py --runs 3`, batch
`20261006-172540`): 27/27 job runs, 126/126 model calls returned `glm-5.3`, 54/54 results verified in the real apps.
Its per-job table shows **means** (A full 4.1×, C full 4.2× the tokens of PrivOS); the summary below it shows sums of
per-job medians (4.2× / 4.6×). It is a single small sample, so the headline figures are the 5-run tables above.
Redacted per-call JSON: [`results/agent_runs/video-20261006-172540/`](../results/agent_runs/video-20261006-172540).
