#!/usr/bin/env python3
"""Terminal-only recording of the token benchmark: one command tells the whole story.

  python3 demo.py                  # A full, C full and PrivOS (MCP servers as installed), 3 runs (~25 min)
  python3 demo.py --runs 1         # quick rehearsal (~8 min)
  python3 demo.py --resume BATCH   # continue after an interruption (finished stack-runs are kept)
  python3 demo.py --with-filtered  # also the tool-filtered MCP arms (best case, see Limits)

Before every step it prints what is about to run; after every job it prints the model used, the tools loaded,
calls, tokens and a live check of the real data (✓/✗); after every run a comparison table; at the end the
mean table with "× PrivOS" ratios. Per-call JSON goes to results/ exactly like runner.py.
"""
import argparse, contextlib, io, json, os, re, statistics, time
import runner as R
import report as RP
from seed import http, P_BASE, P_H, N_H, H_H, HS, MESSAGES

STACKS = ["stack_a_mcp", "stack_a_mcp_min", "stack_c_mcp", "stack_c_mcp_min", "privos_skill"]
BASE = "privos_skill"
NAME = {"stack_a_mcp": "A full", "stack_a_mcp_min": "A filtered", "stack_c_mcp": "C full", "stack_c_mcp_min": "C filtered",
        "privos_skill": "PrivOS"}
WHAT = {
    "stack_a_mcp": "Telegram + Notion + HubSpot — official Notion & HubSpot MCP servers, all their tools loaded (as installed)",
    "stack_a_mcp_min": "Telegram + Notion + HubSpot — same servers, filtered to only the 10 tools these jobs need",
    "stack_c_mcp": "Slack + Notion + HubSpot — reference Slack MCP server + official Notion & HubSpot servers, all tools loaded",
    "stack_c_mcp_min": "Slack + Notion + HubSpot — same servers, filtered to only the 10 tools these jobs need",
    "privos_skill": "chat, tasks and CRM in one workspace, through PrivOS's own agent skills (one bash tool + skill docs)",
}
JOB_TITLE = {"lead_intake": "Job 1 · Lead intake", "pipeline_sync": "Job 2 · Pipeline sync", "daily_digest": "Job 3 · Daily digest"}
JOB_WHAT = {
    "lead_intake": "read Linh Tran's chat message → create a follow-up task → move her deal to Quote Sent",
    "pipeline_sync": "find tasks marked Done → move that customer's deal to Closed Won → post a notice in chat",
    "daily_digest": "read the chat → create a task for Minh's and Hoa's requests → mark the Acme contact Sales Qualified Lead",
}

B, D, G, Y, C, RED, X = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[36m", "\033[31m", "\033[0m"


def banner(title, sub=""):
    print(f"\n{C}{'━' * 100}{X}\n{B}{title}{X}" + (f"\n{D}{sub}{X}" if sub else "") + f"\n{C}{'━' * 100}{X}", flush=True)


def step(text):
    print(f"\n{Y}▶ {text}{X}", flush=True)
    time.sleep(1.5)  # give the viewer a moment to read before output scrolls


# ------------------------------------------------------------------ live state of the real systems
def notion_tasks():
    out = []
    for p in http("POST", f"https://api.notion.com/v1/databases/{R.NOTION_DB}/query", N_H, {"page_size": 50})[1]["results"]:
        pr = p["properties"]
        name = "".join(t["plain_text"] for t in pr["Name"]["title"])
        out.append((name, ((pr.get("Status") or {}).get("select") or {}).get("name")))
    return out


def privos_items(key):
    return http("GET", f"{P_BASE}/items?listId={R.PRIVOS_LISTS[key]}", P_H)[1]["items"]


def privos_tasks():
    st = {v: k for k, v in R.PS["task_stages"].items()}
    return [(i["name"], st.get(i["stageId"])) for i in privos_items("tasks")]


def hubspot_deal_stage():
    s = http("GET", f"{HS}/crm/v3/objects/deals/{R.SEED['hubspot']['deal_linh']}?properties=dealstage,amount", H_H)[1]["properties"]
    return {"qualifiedtobuy": "Proposal", "presentationscheduled": "Quote Sent", "closedwon": "Closed Won"}.get(s["dealstage"], s["dealstage"])


def hubspot_acme():
    return http("GET", f"{HS}/crm/v3/objects/contacts/{R.SEED['hubspot']['contacts']['buyer@acme.example.com']}?properties=lifecyclestage", H_H)[1]["properties"]["lifecyclestage"]


def privos_deal_stage():
    st = {v: k for k, v in R.PS["deal_stages"].items()}
    return next((st.get(i["stageId"]) for i in privos_items("deals") if i["_id"] == R.PS["deal_linh"]), None)


def privos_acme():
    lc = next(f["_id"] for f in http("GET", f"{P_BASE}/lists/{R.PRIVOS_LISTS['contacts']}/fields", P_H)[1]["fieldDefinitions"] if f["name"] == "Lifecycle")
    item = next(i for i in privos_items("contacts") if i["_id"] == R.PS["contact_items"]["buyer@acme.example.com"])
    return next((c["value"] for c in item.get("customFields", []) if c["fieldId"] == lc), None)


def verify(stack, job):
    """Read the real systems back and say whether the agent actually did the job."""
    privos = stack == BASE
    tasks = privos_tasks() if privos else notion_tasks()
    deal = privos_deal_stage() if privos else hubspot_deal_stage()
    where_t, where_d = ("PrivOS Tasks", "PrivOS CRM") if privos else ("Notion", "HubSpot")
    has = lambda *words: any(all(w.lower() in n.lower() for w in words) for n, _ in tasks)
    if job == "lead_intake":
        checks = [(has("Linh", "quote"), f"{where_t}: follow-up task for Linh Tran created"),
                  (deal == "Quote Sent", f"{where_d}: deal 'Linh Tran - Pro Upgrade' is now {deal}")]
    elif job == "pipeline_sync":
        checks = [(deal == "Closed Won", f"{where_d}: deal 'Linh Tran - Pro Upgrade' is now {deal}")]
    else:
        acme = privos_acme() if privos else hubspot_acme()
        ok = acme in ("lc_sales_qualified_lead", "salesqualifiedlead")
        checks = [(any(re.search("sso|acme", n, re.I) for n, _ in tasks if "Linh" not in n), f"{where_t}: task for Minh's Acme SSO request created"),
                  (any(re.search("renewal|beta", n, re.I) for n, _ in tasks), f"{where_t}: task for Hoa's Beta renewal call created"),
                  (ok, f"{'PrivOS Contacts' if privos else 'HubSpot'}: Acme contact lifecycle = {'Sales Qualified Lead' if ok else acme}")]
    return checks


# ------------------------------------------------------------------ story sections
def show_seed():
    step("Same starting data on every side (read live from each system's API)")
    tg = [u["message"]["text"] for u in R.tg_get_updates({}).get("result", []) if "message" in u]
    sl = [m["text"] for m in http("GET", f"{R.SL}/conversations.history?channel={R.E['SLACK_CHANNEL_ID']}&limit=20", R.SL_H)[1]["messages"] if m.get("text") in MESSAGES]
    pv = [m["msg"] for m in http("GET", f"{R.PRIVOS_URL}/api/v1/groups.history?roomId={R.E['PRIVOS_ROOM_ID']}&count=20", P_H)[1]["messages"] if m.get("msg") in MESSAGES]
    print(f"  {B}Chat messages{X}  Telegram: {len(tg)}/3 · Slack: {len(sl)}/3 · PrivOS room: {len(pv)}/3")
    for m in MESSAGES:
        print(f"    “{m}”")
    nt, pt = notion_tasks(), privos_tasks()
    print(f"  {B}Tasks{X}          Notion: {', '.join(f'{n} [{s}]' for n, s in nt)}")
    print(f"                 PrivOS: {', '.join(f'{n} [{s}]' for n, s in pt)}")
    print(f"  {B}CRM deal{X}       HubSpot: Linh Tran - Pro Upgrade [{hubspot_deal_stage()}] · PrivOS: Linh Tran - Pro Upgrade [{privos_deal_stage()}]")
    pretty = lambda v: {"lead": "Lead", "salesqualifiedlead": "Sales Qualified Lead", "lc_lead": "Lead",
                        "lc_sales_qualified_lead": "Sales Qualified Lead"}.get(v, v)
    print(f"  {B}CRM contact{X}    HubSpot: buyer@acme.example.com [{pretty(hubspot_acme())}] · PrivOS: Acme buyer [{pretty(privos_acme())}]")


def show_tools():
    step("What each agent has to load into the model before it can do anything")
    for s in STACKS:
        pairs = R.tools_of(s)
        size = len(json.dumps([t for t, _ in pairs])) + len(R.STACKS[s]["notes"])
        src = {}
        for t, _ in pairs:
            n = t["function"]["name"]
            k = ("Notion MCP" if n.startswith("API-") else "HubSpot MCP" if n.startswith("hubspot-") else "Slack MCP" if n.startswith("slack_")
                 else "Telegram (2 thin tools — no MCP can read messages)" if n.startswith("telegram_") else "bash → PrivOS skills privos-chat + privos-list")
            src[k] = src.get(k, 0) + 1
        print(f"  {B}{NAME[s]:<11}{X} ≈{size // 4:>6,} tokens of tool definitions + instructions  "
              f"{D}({len(pairs)} tools: {' + '.join(f'{k} {v}' for k, v in src.items())}){X}")
    print(f"  {D}This block is re-sent to the model on EVERY call. Most of it is served from the provider's prompt cache,\n"
          f"  so the final table shows uncached input and an estimated cost next to raw tokens.{X}")


def table(rows, title):
    jobs = list(R.JOBS)
    mean = lambda j, s: statistics.mean(rows[(j, s)]) if rows.get((j, s)) else 0
    others = [s for s in STACKS if s != BASE]
    head = f"  {'Job':<22}" + "".join(f"{NAME[s]:>12}" for s in STACKS) + "".join(f"{NAME[s] + ' ÷P':>14}" for s in others)
    print(f"\n{B}{title}{X}\n{head}\n  " + "─" * (len(head) - 2))
    tot = {s: 0 for s in STACKS}
    for j in jobs:
        v = {s: mean(j, s) for s in STACKS}
        for s in STACKS:
            tot[s] += v[s]
        print(f"  {JOB_TITLE[j]:<22}" + "".join(f"{v[s]:>12,.0f}" for s in STACKS)
              + "".join(f"{(v[s] / v[BASE] if v[BASE] else 0):>13.1f}×" for s in others))
    print("  " + "─" * (len(head) - 2))
    print(f"  {B}{'Total (3 jobs)':<22}" + "".join(f"{tot[s]:>12,.0f}" for s in STACKS)
          + "".join(f"{(tot[s] / tot[BASE] if tot[BASE] else 0):>13.1f}×" for s in others) + X)
    return tot


def cost_view(stamp, runs):
    """Readable end table (same numbers as report.py): metrics as rows, setups as columns, then ratios vs PrivOS."""
    rows = RP.load(R.RESULTS, stamp)
    med = lambda s, k: sum(statistics.median(x[k] for x in rows[(s, j)]) for j in R.JOBS if (s, j) in rows)
    t = {s: {k: med(s, k) for k in ("total", "uncached", "cached", "output", "calls")} for s in STACKS}
    cost = {s: RP.weighted(t[s], 0.2) for s in STACKS}
    others = [s for s in STACKS if s != BASE]
    line = lambda label, vals, bold=False: print(f"  {B if bold else ''}{label:<48}" + "".join(f"{v:>13}" for v in vals) + (X if bold else ""))
    print(f"\n{B}Where the tokens go — sum of per-job medians ({runs} run{'s' if runs > 1 else ''}){X}")
    line("", [NAME[s] for s in STACKS], True)
    line("Total tokens", [f"{t[s]['total']:,.0f}" for s in STACKS], True)
    line("  uncached input (new information per job)", [f"{t[s]['uncached']:,.0f}" for s in STACKS])
    line("  cached input (re-sent tool definitions etc.)", [f"{t[s]['cached']:,.0f}" for s in STACKS])
    line("  output", [f"{t[s]['output']:,.0f}" for s in STACKS])
    line("Model calls", [f"{t[s]['calls']:,.0f}" for s in STACKS])
    line("Est. cost units (cached input 0.2×, output 4×)", [f"{cost[s]:,.0f}" for s in STACKS])
    print(f"\n{B}× PrivOS{X}")
    line("", [NAME[s] for s in others], True)
    for label, k in (("Total tokens", "total"), ("Uncached input", "uncached")):
        line(label, [f"{t[s][k] / t[BASE][k]:.1f}×" for s in others], k == "total")
    line("Est. cost", [f"{cost[s] / cost[BASE]:.1f}×" for s in others], True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--resume", metavar="BATCH")
    ap.add_argument("--with-filtered", action="store_true",
                    help="also run the tool-filtered MCP arms (A/C filtered, the best case reported in Limits)")
    a = ap.parse_args()
    global STACKS
    a.as_installed = not a.with_filtered
    if BASE not in R.STACKS:
        raise SystemExit("demo.py needs PrivOS configured (PRIVOS_* in .env); use runner.py for the MCP and control arms")
    if a.as_installed:
        STACKS = [s for s in STACKS if not s.endswith("_min")]
    stamp = a.resume or time.strftime("%Y%m%d-%H%M%S")

    banner("TOKEN BENCHMARK · same AI agent, same model, same 3 sales-ops jobs, same data",
           f"Model: {R.MODEL} via Z.ai (checked on every call) · Metric: total tokens reported by the provider · Batch {stamp}")
    print("  The question: how many tokens does an AI agent spend to run the same work when chat, tasks and CRM live in")
    print("  3 separate tools connected through MCP servers — as installed — vs in PrivOS?" if a.as_installed else
          "  3 separate tools connected through MCP servers — as installed, and filtered to the tools the jobs need — vs in PrivOS?")
    for s in STACKS:
        print(f"   • {B}{NAME[s]}{X}: {WHAT[s]}")
    print("  Jobs:")
    for j in R.JOBS:
        print(f"   • {B}{JOB_TITLE[j]}{X}: {JOB_WHAT[j]}")

    step("Resetting the test workspaces to the starting data")
    R.reset()
    show_seed()
    show_tools()

    rows, checks_ok, checks_all, n_calls, wrong_model = {}, 0, 0, 0, 0
    for run_no in range(1, a.runs + 1):
        banner(f"RUN {run_no} of {a.runs}", "Each stack starts from a fresh reset; jobs run in order 1 → 2 → 3")
        for s in STACKS:
            files = [os.path.join(R.RESULTS, f"agent_run_{s}_{j}_{run_no}_{stamp}.json") for j in R.JOBS]
            if a.resume and all(map(os.path.exists, files)):
                for f in files:
                    r = json.load(open(f))
                    rows.setdefault((r["job"], s), []).append(r["totals"]["total"]); n_calls += len(r["calls"])
                print(f"\n{D}[{NAME[s]} · run {run_no}] already measured before the interruption — kept{X}")
                continue
            step(f"{NAME[s]} — {WHAT[s]}  ·  resetting data first")
            R.reset()
            run_tokens = 0
            for j in R.JOBS:
                step(f"{NAME[s]} · {JOB_TITLE[j]}: the agent must {JOB_WHAT[j]}")
                res = R.run_job(s, j, run_no, run_tokens)
                R.save(res, stamp)
                t = res["totals"]
                run_tokens += t["total"]
                rows.setdefault((j, s), []).append(t["total"]); n_calls += t["calls"]
                wrong_model += sum(c["model"] != R.MODEL for c in res["calls"])
                checks = verify(s, j)
                checks_all += len(checks); checks_ok += sum(ok for ok, _ in checks)
                fixed = (res["calls"][0].get("usage") or {}).get("prompt_tokens", 0)
                print(f"  {B}Result{X}  model {R.MODEL} ✓ on all {t['calls']} calls · first-call prompt {fixed:,} · "
                      f"{B}{t['total']:,} tokens{X} (uncached in {t['in'] - t['cached']:,} · cached in {t['cached']:,} · out {t['out']:,})")
                for ok, txt in checks:
                    print(f"    {G + '✓' if ok else RED + '✗'}{X} {txt}")
                print(f"    {D}Agent: {(res['final'] or '').strip()[:160]}{X}")
        table({k: v[-1:] for k, v in rows.items()}, f"Run {run_no} — total tokens per job")

    banner("RESULT", f"{a.runs} run(s) · data reset before every stack-run · recomputed offline by report.py")
    table(rows, f"Mean total tokens per job ({a.runs} run{'s' if a.runs > 1 else ''})")
    print()
    cost_view(stamp, a.runs)
    print(f"\n  Model check: {n_calls - wrong_model}/{n_calls} calls returned {R.MODEL}. "
          f"Outcome check: {checks_ok}/{checks_all} verified on the real systems.")
    print(f"  {D}Read it as: tokens re-sent per call (context-window pressure, latency) vs. what you pay after prompt caching.\n"
          f"  Official n=5 batch (2026-10-06): ~3.5–3.6× tokens with MCP as installed (~2.2–2.3× est. cost with caching).\n"
          f"  Limits: one model, one small scenario; on larger lists PrivOS used ~1.4× the tokens (2026-10-01 run).{X}")
    with contextlib.redirect_stdout(io.StringIO()):  # write results/summary_<batch>.md in runner.py's format
        R.report(stamp)
    print(f"\n  {D}Per-call JSON: results/agent_run_*_{stamp}.json · summary: results/summary_{stamp}.md{X}")
    step("Restoring the test workspaces")
    R.reset()


if __name__ == "__main__":
    main()
