#!/usr/bin/env python3
"""Entry point: discover every stack under fixtures/, compute cost, compare to baseline.

A "stack" is any fixtures/<name>/ folder containing tools.json + scenarios.json.
The stack whose tools.json has "baseline": true (PrivOS) is the reference; every
other stack is compared against it.

Usage:
    python run_benchmark.py

Outputs:
    stdout                          per-comparison console tables
    results/benchmark_result.json   full machine-readable result
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE / "src"))

from cost_model import stack_cost, PER_CALL_FRAMING_TOKENS  # noqa: E402
from benchmark_report import build_summary, render_all  # noqa: E402
from token_counter import BACKEND  # noqa: E402


def _load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text())


def discover_stacks() -> dict:
    """Return {folder_name: stack_cost} for every valid fixtures/<name>/ stack."""
    stacks = {}
    for d in sorted((HERE / "fixtures").iterdir()):
        tools, scen = d / "tools.json", d / "scenarios.json"
        if tools.is_file() and scen.is_file():
            stacks[d.name] = stack_cost(_load(tools), _load(scen))
    return stacks


def main() -> None:
    stacks = discover_stacks()
    baselines = [s for s in stacks.values() if s.get("baseline")]
    if len(baselines) != 1:
        raise SystemExit(f"expected exactly one baseline stack, found {len(baselines)}")
    baseline = baselines[0]
    others = [s for name, s in stacks.items() if not s.get("baseline")]

    result = {
        "tokenizer_backend": BACKEND,
        "per_call_framing_tokens": PER_CALL_FRAMING_TOKENS,
        "workflows": ["s1_lead_intake", "s2_pipeline_sync", "s3_daily_digest"],
        "baseline": baseline,
        "comparisons": [
            {"label": o["label"], "cost": o, "summary_vs_baseline": build_summary(o, baseline)}
            for o in others
        ],
    }

    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "benchmark_result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))

    print(render_all(baseline, others, BACKEND))
    print("Full result written to results/benchmark_result.json")


if __name__ == "__main__":
    main()
