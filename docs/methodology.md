# Methodology

> This page describes the **modelled** benchmark (fixtures + tokenizer). The live agent benchmark — real model,
> real MCP servers, provider-reported tokens, n=5 — and its method are in [live-agent-results.md](live-agent-results.md).

## Question

When an AI agent runs an operations workflow (read chat → create task → update
CRM), does routing everything through a **single unified platform (PrivOS)** cost
fewer tokens than stitching together **several specialized SaaS platforms**
(Telegram for chat, Notion for tasks, HubSpot for CRM) over MCP?

"Cost" here means **tokens the model must process** — the thing that drives price,
latency, and context-window pressure for an LLM agent.

## Theoretical basis — where the tokens go

An agent pays token cost in three buckets. The benchmark measures all three.

### 1. Fixed cost — tool-schema loading

To call a tool, the model must have that tool's JSON schema in its context. When
you connect an MCP server, the runtime injects the schema for **every tool that
server exposes**, whether or not the workflow uses it. Three servers means the
union of three catalogs sits in context for the whole session.

- Telegram Bot API surfaced via MCP: ~9 tools.
- Notion API via the official MCP server: ~15 tools.
- HubSpot MCP server: ~25 tools.
- **PrivOS unified surface: ~6 tools** — one data model (rooms + list items)
  covers chat, tasks, and CRM, so a handful of verbs suffice.

This is a fixed, up-front tax, paid once per session regardless of how much work
is done. It is the single largest term (see Results).

### 2. Variable cost — request/response payloads

Every API call sends a request and receives a response, both as JSON the model
reads. Two structural facts make the multi-platform stack heavier here:

**(a) Pre-fetch calls.** Specialized APIs force lookups before a write:
  - Notion `createPage` requires the target database's property schema, so the
    agent first calls `retrieveDatabase` (a large object: every property with its
    type config, option lists, groups).
  - HubSpot updates need the record **id**, so the agent first `search`es by
    email/company.

  In the unified model these vanish: list column names are stable and known, and
  records are addressed by a known id, so the write is a single call.

**(b) Response verbosity.** Notion echoes a fully-hydrated page object on every
create (nested type-wrappers, per-run `annotations`, `created_by`,
`last_edited_by`, `url`, `public_url`, `archived`, `in_trash`…). HubSpot returns
default plus updated properties with string-typed values and multiple
timestamps. PrivOS list-item and message objects are comparatively flat.

### 3. Orchestration cost — per-call framing

The harness wraps each tool call in a `tool_use` block (name + arguments) and a
`tool_result` block (id + content). This framing is roughly constant per call
(modeled at `PER_CALL_FRAMING_TOKENS = 50`, mid-range of observed MCP round
trips) and therefore scales with the **number of calls** — again penalizing the
pre-fetch-heavy stack. This term is modeled, not measured live; it is the least
load-bearing of the three and is reported separately so a skeptical reader can
discount it.

## Measurement

- **Tokenizer:** `tiktoken/cl100k_base`, a real BPE encoder (`src/token_counter.py`).
  Both stacks are tokenized with the identical encoder on identical business
  text, so the reported **ratios** are near-invariant to the tokenizer choice
  even though absolute counts shift a few percent across model families.
- **Payloads:** representative objects whose shapes mirror each vendor's
  documented API (`fixtures/*/scenarios.json`). See `references.md` for sources.
  They are **not** live captures — see Limitations.
- **Fixed schema:** per server, average tokens of its sample tool schemas ×
  documented tool count (`src/cost_model.py::fixed_schema_tokens`).
- **Workloads:** three independent workflows (lead intake, pipeline sync, daily
  digest), not one, so the result is not a single cherry-picked flow.
- **Two multi-platform stacks:** Telegram+Notion+HubSpot and Discord+Trello+HubSpot,
  so the gap is shown not to be vendor-specific. Both land in the same band.
- **Live-capture path** (`src/live_capture.py`, `docs/live-capture.md`) replaces
  modeled read payloads with real read-only API responses when credentials exist,
  so fixtures can be validated against measured per-object sizes.

## Controls / fairness

- **Identical business content** on both sides: same customer names, same task
  titles, same message text. Only the *envelope* (schema + protocol overhead)
  differs, which is exactly the variable under test.
- **Same tokenizer, same code path** for both stacks.
- **Assumptions are data, not code:** tool counts live in `tools.json`, framing
  constant in one named variable — change them and re-run to test sensitivity.
- **Three buckets reported separately** so each can be independently accepted or
  discounted; the amortized "workflow-only" view removes the fixed term entirely
  for readers who assume long sessions.

## Limitations (honest scope)

1. **Not live API captures.** Payloads are faithful to documented object shapes
   but were authored, not recorded. Real responses vary with configured
   properties/fields; a HubSpot portal with many custom properties would make the
   multi-platform side *heavier*, not lighter.
2. **Tool counts are estimates.** Actual MCP servers expose different counts;
   they are parameterized for exactly this reason.
3. **Orchestration framing is modeled** with a constant, not captured from a live
   transcript.
4. **BPE proxy tokenizer.** Anthropic's tokenizer differs from cl100k_base in
   absolute counts; ratios are robust because the comparison is within-tokenizer.
5. **Excludes model reasoning tokens.** More hops generally mean more intermediate
   reasoning, which would widen the gap further — so the reported multiple is a
   **lower bound** on the real-world difference, not an upper bound.
6. **Feature parity not modeled.** This measures token cost only. Specialized
   SaaS tools offer depth (HubSpot automation/reporting) a unified platform may
   not match; that is a product trade-off, out of scope here.
