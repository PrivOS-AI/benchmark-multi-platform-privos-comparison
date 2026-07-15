"""Render benchmark results: per-comparison console tables and cross-stack ratios.

The baseline stack (PrivOS) is compared against every other stack. Each comparison
reports the same three cost buckets plus session and amortized totals.
"""


def _ratio(a: int, b: int) -> float:
    return round(a / b, 2) if b else float("inf")


def build_summary(other: dict, baseline: dict) -> dict:
    """Cross-stack ratios (other / baseline) for the headline numbers."""
    ot, bt = other["totals"], baseline["totals"]
    return {
        "fixed_schema_x": _ratio(other["fixed_schema_tokens"], baseline["fixed_schema_tokens"]),
        "variable_io_x": _ratio(ot["variable_io_tokens"], bt["variable_io_tokens"]),
        "orchestration_x": _ratio(ot["orchestration_tokens"], bt["orchestration_tokens"]),
        "session_total_x": _ratio(ot["session_total_tokens"], bt["session_total_tokens"]),
        "workflow_only_x": _ratio(ot["workflow_only_tokens"], bt["workflow_only_tokens"]),
        "num_calls_other": ot["num_calls"],
        "num_calls_baseline": bt["num_calls"],
    }


def render_comparison(other: dict, baseline: dict) -> str:
    """One comparison block: `other` (multi-platform variant) vs `baseline` (PrivOS)."""
    ol, bl = other["label"], baseline["label"]
    L = []
    L.append("=" * 76)
    L.append(f"{ol}  vs  {bl}")
    L.append("=" * 76)
    L.append(f"{'component':<28}{ol[:14]:>15}{bl[:12]:>13}{'ratio':>12}")
    L.append("-" * 76)
    rows = [
        ("fixed: tool schemas", other["fixed_schema_tokens"], baseline["fixed_schema_tokens"]),
        ("variable: workflow I/O", other["totals"]["variable_io_tokens"], baseline["totals"]["variable_io_tokens"]),
        ("orchestration framing", other["totals"]["orchestration_tokens"], baseline["totals"]["orchestration_tokens"]),
    ]
    for label, a, b in rows:
        L.append(f"{label:<28}{a:>15,}{b:>13,}{_ratio(a,b):>11.1f}x")
    L.append("-" * 76)
    a, b = other["totals"]["session_total_tokens"], baseline["totals"]["session_total_tokens"]
    L.append(f"{'SESSION TOTAL':<28}{a:>15,}{b:>13,}{_ratio(a,b):>11.1f}x")
    a, b = other["totals"]["workflow_only_tokens"], baseline["totals"]["workflow_only_tokens"]
    L.append(f"{'workflow-only (amortized)':<28}{a:>15,}{b:>13,}{_ratio(a,b):>11.1f}x")
    L.append(f"{'API calls (3 scenarios)':<28}{other['totals']['num_calls']:>15}{baseline['totals']['num_calls']:>13}")
    L.append("")
    for m, p in zip(other["scenarios"], baseline["scenarios"]):
        L.append(f"  {m['id']:<18} {ol[:8]}={m['workflow_tokens']:>6,} "
                 f"({m['num_calls']}c/{m['overhead_calls']}pf)  "
                 f"{bl[:8]}={p['workflow_tokens']:>6,} ({p['num_calls']}c)  "
                 f"= {_ratio(m['workflow_tokens'], p['workflow_tokens'])}x")
    return "\n".join(L)


def render_all(baseline: dict, others: list, backend: str) -> str:
    L = [f"Tokenizer backend: {backend}", ""]
    for other in others:
        L.append(render_comparison(other, baseline))
        L.append("")
    return "\n".join(L)
