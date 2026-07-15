"""Cost model: translate fixtures into per-session token costs.

An agent session pays token cost in three buckets:

  1. fixed_schema      One-time cost of loading MCP tool catalogs into context.
                       Estimated per server as (avg tokens per sample tool) x
                       (documented tool_count). Amortized across a session: paid
                       once no matter how many workflows run.

  2. variable_io       Request + response JSON for every API call the workflow
                       makes. Scales with number of calls and payload verbosity.

  3. orchestration     Model-side framing the runtime adds around each tool call
                       (the assistant tool_use block wrapper + the tool_result
                       wrapper), independent of payload. Modeled as a constant
                       per call; see PER_CALL_FRAMING_TOKENS. Scales with the
                       NUMBER of calls, so it penalizes pre-fetch-heavy flows.

All three use the same tokenizer (src/token_counter). Ratios between stacks are
the headline metric and are robust to the exact constants chosen.
"""
from token_counter import count_json

# Framing the harness wraps around each tool call: a tool_use block (name +
# arguments envelope) and a tool_result block (id + content envelope). Measured
# empirically at ~40-60 tokens for typical MCP round-trips; 50 is a mid estimate.
PER_CALL_FRAMING_TOKENS = 50


def fixed_schema_tokens(tools_fixture: dict) -> tuple[int, dict]:
    """Estimate loaded tool-catalog tokens across all servers in the fixture."""
    total = 0
    breakdown = {}
    for server, spec in tools_fixture["servers"].items():
        samples = spec["sample_tools"]
        avg = sum(count_json(t) for t in samples.values()) / len(samples)
        tokens = round(avg * spec["tool_count"])
        breakdown[server] = {
            "tool_count": spec["tool_count"],
            "avg_tokens_per_tool": round(avg),
            "tokens": tokens,
        }
        total += tokens
    return total, breakdown


def scenario_cost(scenario: dict) -> dict:
    """Per-scenario variable + orchestration cost, with per-call detail."""
    calls = scenario["calls"]
    io_total = 0
    overhead_calls = 0
    call_detail = []
    for c in calls:
        req = count_json(c["request"])
        resp = count_json(c["response"])
        io_total += req + resp
        if c.get("overhead"):
            overhead_calls += 1
        call_detail.append({
            "op": c["op"], "platform": c["platform"], "overhead": c.get("overhead", False),
            "request_tokens": req, "response_tokens": resp, "call_tokens": req + resp,
        })
    orchestration = len(calls) * PER_CALL_FRAMING_TOKENS
    return {
        "id": scenario["id"], "title": scenario["title"],
        "num_calls": len(calls), "overhead_calls": overhead_calls,
        "variable_io_tokens": io_total,
        "orchestration_tokens": orchestration,
        "workflow_tokens": io_total + orchestration,
        "calls": call_detail,
    }


def stack_cost(tools_fixture: dict, scenarios_fixture: dict) -> dict:
    """Full per-stack cost: fixed schema + every scenario, plus a session roll-up."""
    fixed, fixed_bd = fixed_schema_tokens(tools_fixture)
    scenarios = [scenario_cost(s) for s in scenarios_fixture["scenarios"]]
    var_total = sum(s["variable_io_tokens"] for s in scenarios)
    orch_total = sum(s["orchestration_tokens"] for s in scenarios)
    calls_total = sum(s["num_calls"] for s in scenarios)
    return {
        "label": tools_fixture.get("label", "unknown"),
        "baseline": tools_fixture.get("baseline", False),
        "fixed_schema_tokens": fixed,
        "fixed_breakdown": fixed_bd,
        "scenarios": scenarios,
        "totals": {
            "variable_io_tokens": var_total,
            "orchestration_tokens": orch_total,
            "num_calls": calls_total,
            # session total = pay schema once, then all workflow I/O + framing
            "session_total_tokens": fixed + var_total + orch_total,
            # per-workflow amortized view (schema already cached)
            "workflow_only_tokens": var_total + orch_total,
        },
    }
