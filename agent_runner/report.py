#!/usr/bin/env python3
"""Recompute every table from the per-call JSON in a results folder. No .env, no network.

  python3 report.py results/            # all batches found
  python3 report.py results/ --batch 20261002-104030
  python3 report.py results/ --base privos_skill --md summary.md

Per (stack, job): median and min–max of total tokens, plus uncached input, cached input, output and the
fixed prompt (first call's prompt tokens = tools + system prompt). Cost is shown as a weighted token count
(uncached input = 1, output = OUT_W, cached input = c for c in CACHE_WEIGHTS) and, if PRICE_* env vars are set
(USD per 1M tokens), as dollars. Ratios are stack ÷ base on the totals of the per-job medians.
"""
import argparse, glob, json, os, statistics, sys

CACHE_WEIGHTS = (0.1, 0.2, 0.5)
OUT_W = 4.0
PRICE = {k: float(os.environ[f"PRICE_{k.upper()}"]) for k in ("input", "cached", "output") if os.environ.get(f"PRICE_{k.upper()}")}


def usage(call):
    u = call.get("usage") or {}
    p, o = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    c = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    return p - c, c, o


def run_stats(r):
    unc = cac = out = 0
    for call in r["calls"]:
        a, b, c = usage(call)
        unc += a; cac += b; out += c
    first = (r["calls"][0].get("usage") or {}).get("prompt_tokens", 0) if r["calls"] else 0
    return {"total": unc + cac + out, "uncached": unc, "cached": cac, "output": out, "fixed": first,
            "calls": len(r["calls"]), "models": {c.get("model") for c in r["calls"]}}


def weighted(s, c):
    return s["uncached"] + c * s["cached"] + OUT_W * s["output"]


def dollars(s):
    if len(PRICE) < 3:
        return None
    return (s["uncached"] * PRICE["input"] + s["cached"] * PRICE["cached"] + s["output"] * PRICE["output"]) / 1e6


def load(folder, batch):
    rows = {}
    for f in sorted(glob.glob(os.path.join(folder, "**", "agent_run_*.json"), recursive=True)):
        if batch and not f.endswith(f"_{batch}.json"):
            continue
        r = json.load(open(f))
        rows.setdefault((r["stack"], r["job"]), []).append(run_stats(r))
    return rows


def aligned(text):
    """Markdown pipe tables -> space-aligned columns for a terminal (first column left, the rest right)."""
    out, block = [], []

    def flush():
        if block:
            rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in block if not set(l) <= set("|-: ")]
            w = [max(len(r[i]) for r in rows if i < len(r)) for i in range(max(map(len, rows)))]
            for r in rows:
                out.append("  " + "  ".join(c.ljust(w[i]) if i == 0 else c.rjust(w[i]) for i, c in enumerate(r)).rstrip())
            block.clear()

    for line in text.splitlines():
        if line.startswith("|"):
            block.append(line)
        else:
            flush()
            out.append(line.lstrip("# ").replace("`", "") if line.startswith("#") else line)
    flush()
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--batch")
    ap.add_argument("--base", default="privos_skill")
    ap.add_argument("--md")
    ap.add_argument("--pretty", action="store_true", help="align tables for a terminal (default when stdout is a tty)")
    a = ap.parse_args()
    rows = load(a.folder, a.batch)
    if not rows:
        raise SystemExit("no agent_run_*.json found")
    stacks = sorted({s for s, _ in rows}, key=lambda s: (s.startswith("privos"), s))
    jobs = sorted({j for _, j in rows}, key=["lead_intake", "pipeline_sync", "daily_digest"].index)
    med = lambda s, j, k: statistics.median(x[k] for x in rows[(s, j)]) if (s, j) in rows else 0
    out = []
    n_runs = sum(len(v) for v in rows.values())
    n_calls = sum(x["calls"] for v in rows.values() for x in v)
    models = set().union(*(x["models"] for v in rows.values() for x in v))
    out.append(f"Batch: {a.batch or 'all'} · {n_runs} job runs · {n_calls} model calls · models returned: {', '.join(sorted(map(str, models)))}\n")

    out.append("## Total tokens per job — median [min–max] (n)\n")
    out.append("| Job | " + " | ".join(stacks) + " |\n|---" * 1 + "|---" * len(stacks) + "|")
    for j in jobs:
        cells = []
        for s in stacks:
            v = [x["total"] for x in rows.get((s, j), [])]
            cells.append(f"{statistics.median(v):,.0f} [{min(v):,}–{max(v):,}] ({len(v)})" if v else "–")
        out.append(f"| {j} | " + " | ".join(cells) + " |")

    tot = {s: {k: sum(med(s, j, k) for j in jobs) for k in ("total", "uncached", "cached", "output", "fixed", "calls")} for s in stacks}
    base = tot.get(a.base)
    out.append("\n## Sum of per-job medians\n")
    hdr = ["Stack", "total", "uncached in", "cached in", "output", "fixed prompt/job", "calls"] + \
          [f"weighted c={c}" for c in CACHE_WEIGHTS] + (["est. $"] if len(PRICE) == 3 else [])
    out.append("| " + " | ".join(hdr) + " |\n" + "|---" * len(hdr) + "|")
    for s in stacks:
        t = tot[s]
        cells = [s, f"{t['total']:,.0f}", f"{t['uncached']:,.0f}", f"{t['cached']:,.0f}", f"{t['output']:,.0f}",
                 f"{t['fixed'] / len(jobs):,.0f}", f"{t['calls']:,.0f}"] + [f"{weighted(t, c):,.0f}" for c in CACHE_WEIGHTS]
        if len(PRICE) == 3:
            cells.append(f"{dollars(t):.4f}")
        out.append("| " + " | ".join(cells) + " |")

    if base:
        out.append(f"\n## Ratios vs `{a.base}` (sum of per-job medians)\n")
        hdr = ["Stack", "total", "uncached input"] + [f"weighted c={c}" for c in CACHE_WEIGHTS] + (["USD"] if len(PRICE) == 3 else [])
        out.append("| " + " | ".join(hdr) + " |\n" + "|---" * len(hdr) + "|")
        r = lambda x, y: f"{x / y:.2f}×" if y else "–"
        for s in stacks:
            if s == a.base:
                continue
            t = tot[s]
            out.append("| " + " | ".join([s, r(t["total"], base["total"]), r(t["uncached"], base["uncached"])]
                                         + [r(weighted(t, c), weighted(base, c)) for c in CACHE_WEIGHTS]
                                         + ([r(dollars(t), dollars(base))] if len(PRICE) == 3 else [])) + " |")
        out.append(f"\nWeights: uncached input 1, output {OUT_W}, cached input c. Set PRICE_INPUT/PRICE_CACHED/PRICE_OUTPUT (USD per 1M) for dollars.")
    text = "\n".join(out)
    print(aligned(text) if a.pretty or sys.stdout.isatty() else text)
    if a.md:
        open(a.md, "w").write(text + "\n")


if __name__ == "__main__":
    main()
