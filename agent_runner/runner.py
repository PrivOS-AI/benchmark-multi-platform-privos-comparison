#!/usr/bin/env python3
"""Token benchmark runner: same agent loop, same model, same 3 jobs, per stack.

Tokens come only from the `usage` object GLM returns. Every call asserts the
returned model equals ZAI_MODEL (the coding endpoint silently substitutes models).

  python runner.py --list-tools stack_a|stack_b|stack_c|privos
  python runner.py --stack privos --job lead_intake
  python runner.py --all-jobs --runs 3 --reset-between-runs
  python runner.py --reset            # put seed data back
  python runner.py --report           # table from results/

Fairness (declared, see results JSON): both sides get thin wrappers over the real
vendor APIs with container IDs bound (Telegram chat, Notion DB, PrivOS room/lists)
and the same kind of workspace notes (stage names -> ids). Field schemas are NOT
given: each side discovers them through its own API. Raw API responses go to the
model unmodified.
"""
import argparse, glob, json, os, statistics, sys, time
from seed import E, http, need, STATE_FILE, P_BASE, P_H, N_H, H_H, HS, MESSAGES, TASK, DEAL

ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(ROOT, "results")
MODEL = E["ZAI_MODEL"]
MAX_TOKENS_PER_RUN = 3_000_000  # MCP catalogs (~30k tokens) are resent on every call
MAX_CALLS_PER_JOB = 20
SEED = json.load(open(STATE_FILE))
PS = SEED.get("privos") or {}
HAS_PRIVOS = bool(PS and E.get("PRIVOS_URL") and E.get("PRIVOS_TOKEN"))  # MCP and control arms run without PrivOS
PRIVOS_URL = E.get("PRIVOS_URL", "").rstrip("/")
TG = f"https://api.telegram.org/bot{E['TELEGRAM_BOT_TOKEN']}"
NOTION_DB = E["NOTION_DATABASE_ID"]
PRIVOS_LISTS = {k: PS.get(f"{k}_list", "") for k in ("tasks", "deals", "contacts")}

JOBS = {
    "lead_intake": (
        "Read the recent messages in the sales chat. Linh Tran is asking for a quote. "
        "1) Create a follow-up task in the task tracker: name 'Send Pro plan quote to Linh Tran (5 seats) + schedule onboarding', "
        "priority High, due 2026-10-08, customer 'Linh Tran (@linhtran)', status To Do. "
        "2) In the CRM, move the deal 'Linh Tran - Pro Upgrade' to the Quote Sent stage, keep amount 1188, and set its next step to "
        "'Send Pro plan quote (5 seats), schedule onboarding 2026-10-08'."),
    "pipeline_sync": (
        "Find the tasks in the task tracker whose status is Done. For each one, find the related customer's deal in the CRM "
        "and move it to Closed Won. Then post one notice in the sales chat in exactly this form: "
        "Deal '<deal name>' ($<amount>) moved to Closed Won."),
    "daily_digest": (
        "Read the recent messages in the sales chat. For the requests from Minh and from Hoa, create one task each in the task "
        "tracker (priority Medium, status To Do, due 2026-10-03, customer = the customer mentioned in the message). "
        "Then in the CRM set the lifecycle stage of the Acme contact (buyer@acme.example.com) to Sales Qualified Lead."),
}

SYSTEM = ("You are a sales-ops assistant. Use the tools to do the job, then reply with one short sentence saying what you did. "
          "Do not ask questions; do not invent IDs - look them up with the tools.\n\nWorkspace notes:\n")


# ------------------------------------------------------------------ tool helpers
def tool(name, desc, props=None, required=()):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": list(required)}}}


CREATED = []  # (kind, id) created during the current run, removed by reset


# ------------------------------------------------------------------ Stack A: Telegram + Notion + HubSpot
def tg_get_updates(a):
    # no offset: reading must never acknowledge updates, every run sees the same messages
    return http("GET", f"{TG}/getUpdates?limit={int(a.get('limit', 20))}")[1]


def tg_send(a):
    return http("POST", f"{TG}/sendMessage", body={"chat_id": E["TELEGRAM_CHAT_ID"], "text": a["text"]})[1]


def notion_retrieve(a):
    return http("GET", f"https://api.notion.com/v1/databases/{NOTION_DB}", N_H)[1]


def notion_query(a):
    body = {"page_size": int(a.get("page_size", 20))}
    if a.get("filter"):
        body["filter"] = a["filter"]
    return http("POST", f"https://api.notion.com/v1/databases/{NOTION_DB}/query", N_H, body)[1]


def notion_create(a):
    s, b = http("POST", "https://api.notion.com/v1/pages", N_H, {"parent": {"database_id": NOTION_DB}, "properties": a["properties"]})
    if s < 300:
        CREATED.append(("notion_page", b["id"]))
    return b


def notion_update(a):
    return http("PATCH", f"https://api.notion.com/v1/pages/{a['page_id']}", N_H, {"properties": a["properties"]})[1]


def hs_search(a):
    body = {"filterGroups": a.get("filter_groups", []), "properties": a.get("properties", []), "limit": 10}
    return http("POST", f"{HS}/crm/v3/objects/{a['object_type']}/search", H_H, body)[1]


def hs_update(a):
    return http("PATCH", f"{HS}/crm/v3/objects/{a['object_type']}/{a['object_id']}", H_H, {"properties": a["properties"]})[1]


OBJ = {"type": "string", "enum": ["contacts", "deals"]}
STACK_A = {
    "notes": ("- Sales chat = a Telegram group (read with getUpdates, post with sendMessage).\n"
              "- Task tracker = a Notion database.\n"
              "- CRM = HubSpot. Deal pipeline 'default'; stage ids: Proposal=qualifiedtobuy, Quote Sent=presentationscheduled, "
              "Closed Won=closedwon. Deal properties: dealname, dealstage, amount, hs_next_step. "
              "Contact properties: email, company, lifecyclestage (ids: lead, salesqualifiedlead, opportunity, customer)."),
    "tools": [
        (tool("telegram_get_updates", "Telegram Bot API getUpdates for the sales chat. Returns raw Update objects.",
              {"limit": {"type": "integer"}}), tg_get_updates),
        (tool("telegram_send_message", "Telegram Bot API sendMessage to the sales chat.",
              {"text": {"type": "string"}}, ["text"]), tg_send),
        (tool("notion_retrieve_database", "Notion: retrieve the task database (schema/properties)."), notion_retrieve),
        (tool("notion_query_database", "Notion: query the task database. `filter` is a Notion filter object.",
              {"filter": {"type": "object"}, "page_size": {"type": "integer"}}), notion_query),
        (tool("notion_create_page", "Notion: create a page (task) in the task database. `properties` is a Notion properties object.",
              {"properties": {"type": "object"}}, ["properties"]), notion_create),
        (tool("notion_update_page", "Notion: update a page's properties.",
              {"page_id": {"type": "string"}, "properties": {"type": "object"}}, ["page_id", "properties"]), notion_update),
        (tool("hubspot_search", "HubSpot CRM v3 search. `filter_groups` uses HubSpot filterGroups syntax; `properties` lists properties to return.",
              {"object_type": OBJ, "filter_groups": {"type": "array", "items": {"type": "object"}},
               "properties": {"type": "array", "items": {"type": "string"}}}, ["object_type", "filter_groups"]), hs_search),
        (tool("hubspot_update", "HubSpot CRM v3: update an object's properties.",
              {"object_type": OBJ, "object_id": {"type": "string"}, "properties": {"type": "object"}},
              ["object_type", "object_id", "properties"]), hs_update),
    ],
}


# ------------------------------------------------------------------ PrivOS
def pv_messages(a):
    return http("GET", f"{PRIVOS_URL}/api/v1/groups.history?roomId={E['PRIVOS_ROOM_ID']}&count={int(a.get('count', 20))}", P_H)[1]


def pv_send(a):
    s, b = http("POST", f"{PRIVOS_URL}/api/v1/bot/sendMessage", P_H, {"roomId": E["PRIVOS_ROOM_ID"], "text": a["text"]})
    return b


def pv_get_list(a):
    return http("GET", f"{P_BASE}/lists/{PRIVOS_LISTS[a['list']]}/fields", P_H)[1]


def pv_items(a):
    q = f"?listId={PRIVOS_LISTS[a['list']]}" + (f"&stageId={a['stage_id']}" if a.get("stage_id") else "")
    return http("GET", f"{P_BASE}/items{q}", P_H)[1]


def pv_create(a):
    s, b = http("POST", f"{P_BASE}/items", P_H, {"name": a["name"], "listId": PRIVOS_LISTS[a["list"]],
                                                   "stageId": a["stage_id"], "customFields": a.get("custom_fields", [])})
    if s < 300:
        CREATED.append(("privos_item", (b.get("item") or b).get("_id")))
    return b


def pv_update(a):
    out = {}
    if a.get("stage_id"):
        out["stage"] = http("PUT", f"{P_BASE}/items/{a['item_id']}", P_H, {"stageId": a["stage_id"]})[1]
    if a.get("custom_fields"):
        out["fields"] = http("PUT", f"{P_BASE}/items/{a['item_id']}/customFields", P_H, {"fields": a["custom_fields"]})[1]
    return out or {"error": "nothing to update"}


LIST = {"type": "string", "enum": list(PRIVOS_LISTS)}
CF = {"type": "array", "items": {"type": "object", "properties": {"fieldId": {"type": "string"}, "value": {}}}}
st = PS.get("task_stages", {}); ds = PS.get("deal_stages", {})


def privos_notes():  # built lazily: needs the seeded PrivOS state
    return ("- Sales chat = the PrivOS room (read messages, post as the bot).\n"
            "- Task tracker = PrivOS list 'tasks'; stage ids: To Do=" + st["To Do"] + ", Done=" + st["Done"] + ".\n"
            "- CRM = PrivOS lists 'deals' and 'contacts'. Deal stage ids: Proposal=" + ds["Proposal"] + ", Quote Sent="
            + ds["Quote Sent"] + ", Closed Won=" + ds["Closed Won"] + ".")


PRIVOS = {
    "notes": privos_notes,
    "tools": [
        (tool("privos_get_messages", "PrivOS: read recent messages in the sales room. Returns raw message objects.",
              {"count": {"type": "integer"}}), pv_messages),
        (tool("privos_send_message", "PrivOS: post a message to the sales room.",
              {"text": {"type": "string"}}, ["text"]), pv_send),
        (tool("privos_get_list", "PrivOS: get a list's field definitions (ids, types, SELECT options).",
              {"list": LIST}, ["list"]), pv_get_list),
        (tool("privos_list_items", "PrivOS: list items in a list, optionally filtered by stage id.",
              {"list": LIST, "stage_id": {"type": "string"}}, ["list"]), pv_items),
        (tool("privos_create_item", "PrivOS: create an item. custom_fields = [{fieldId, value}]; SELECT values are option ids.",
              {"list": LIST, "name": {"type": "string"}, "stage_id": {"type": "string"}, "custom_fields": CF},
              ["list", "name", "stage_id"]), pv_create),
        (tool("privos_update_item", "PrivOS: move an item to a stage and/or merge custom field values ([{fieldId, value}]).",
              {"item_id": {"type": "string"}, "stage_id": {"type": "string"}, "custom_fields": CF}, ["item_id"]), pv_update),
    ],
}

# ------------------------------------------------------------------ Stack B: Discord + Trello + HubSpot
from seed import TK, TR
HAS_STACK_B = bool(SEED.get("trello") and E.get("DISCORD_BOT_TOKEN") and E.get("TRELLO_KEY"))  # optional stack
TRL = SEED.get("trello") or {"lists": {"To Do": "", "Done": ""}, "board": "", "card_demo": ""}
DC = f"https://discord.com/api/v10/channels/{E.get('DISCORD_CHANNEL_ID', '')}/messages"
DC_H = {"Authorization": "Bot " + E.get("DISCORD_BOT_TOKEN", "")}


def dc_get(a):
    return http("GET", f"{DC}?limit={int(a.get('limit', 20))}", DC_H)[1]


def dc_send(a):
    return http("POST", DC, DC_H, {"content": a["content"]})[1]


def tr_board(a):
    return http("GET", f"{TR}/boards/{TRL['board']}?fields=name&lists=open&labels=all&{TK()}")[1]


def tr_cards(a):
    return http("GET", f"{TR}/lists/{a['list_id']}/cards?{TK()}")[1]


def tr_body(a):
    b = {k: a[k] for k in ("name", "desc", "due") if a.get(k)}
    if a.get("list_id"):
        b["idList"] = a["list_id"]
    if a.get("label_ids"):
        b["idLabels"] = a["label_ids"]
    return b


def tr_create(a):
    return http("POST", f"{TR}/cards?{TK()}", body=tr_body(a))[1]


def tr_update(a):
    return http("PUT", f"{TR}/cards/{a['card_id']}?{TK()}", body=tr_body(a))[1]


CARD = {"name": {"type": "string"}, "desc": {"type": "string"}, "due": {"type": "string", "description": "ISO date"},
        "list_id": {"type": "string"}, "label_ids": {"type": "array", "items": {"type": "string"}}}
HUBSPOT_TOOLS = STACK_A["tools"][6:8]
STACK_B = {
    "notes": ("- Sales chat = a Discord channel (read recent messages, post as the bot).\n"
              "- Task tracker = a Trello board; a card's list is its status: To Do=" + TRL["lists"]["To Do"] + ", Done="
              + TRL["lists"]["Done"] + ". Priority is a label; put 'Customer: <name>' in the card description.\n"
              + STACK_A["notes"].split("\n")[2]),
    "tools": [
        (tool("discord_get_messages", "Discord API: get recent messages in the sales channel. Returns raw Message objects.",
              {"limit": {"type": "integer"}}), dc_get),
        (tool("discord_send_message", "Discord API: post a message to the sales channel.",
              {"content": {"type": "string"}}, ["content"]), dc_send),
        (tool("trello_get_board", "Trello: get the task board with its lists and labels."), tr_board),
        (tool("trello_list_cards", "Trello: get the cards in a list.", {"list_id": {"type": "string"}}, ["list_id"]), tr_cards),
        (tool("trello_create_card", "Trello: create a card.", CARD, ["name", "list_id"]), tr_create),
        (tool("trello_update_card", "Trello: update a card (move list, name, desc, due, labels).",
              {"card_id": {"type": "string"}, **CARD}, ["card_id"]), tr_update),
        *HUBSPOT_TOOLS,
    ],
}


# ------------------------------------------------------------------ Stack C: Slack + Notion + HubSpot
SL = "https://slack.com/api"
SL_H = {"Authorization": "Bearer " + E["SLACK_BOT_TOKEN"]}


def sl_get(a):
    return http("GET", f"{SL}/conversations.history?channel={E['SLACK_CHANNEL_ID']}&limit={int(a.get('limit', 20))}", SL_H)[1]


def sl_send(a):
    return http("POST", f"{SL}/chat.postMessage", SL_H, {"channel": E["SLACK_CHANNEL_ID"], "text": a["text"]})[1]


STACK_C = {
    "notes": "- Sales chat = a Slack channel (read history, post as the bot).\n" + "\n".join(STACK_A["notes"].split("\n")[1:]),
    "tools": [
        (tool("slack_get_messages", "Slack Web API conversations.history for the sales channel. Returns raw message objects.",
              {"limit": {"type": "integer"}}), sl_get),
        (tool("slack_post_message", "Slack Web API chat.postMessage to the sales channel.",
              {"text": {"type": "string"}}, ["text"]), sl_send),
        *STACK_A["tools"][2:8],
    ],
}


# ------------------------------------------------------------------ Variant 1: real MCP servers (stdio) + real PrivOS skill
import atexit, select, shlex, subprocess


class MCP:
    """Minimal MCP stdio client: initialize, tools/list, tools/call (newline-delimited JSON-RPC)."""

    def __init__(self, cmd, env):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, env={**os.environ, **env})
        atexit.register(self.p.kill)
        self.n = 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "bm", "version": "1"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"); self.p.stdin.flush()
        self.tools = self.rpc("tools/list", {})["tools"]

    def rpc(self, method, params, timeout=180):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        end = time.time() + timeout
        while time.time() < end:
            if select.select([self.p.stdout], [], [], 1)[0]:
                line = self.p.stdout.readline()
                if not line:
                    break
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("id") == self.n:
                    if "error" in m:
                        return {"isError": True, "content": [{"type": "text", "text": json.dumps(m["error"])}]}
                    return m["result"]
        raise RuntimeError(f"MCP {method} timeout")

    def as_tools(self):
        out = []
        for t in self.tools:
            spec = {"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                      "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}}}
            out.append((spec, lambda a, name=t["name"]: "\n".join(
                c.get("text", "") for c in self.rpc("tools/call", {"name": name, "arguments": a}).get("content", []))))
        return out


_MCP = {}


# Pinned server versions: the tool catalog (and therefore the result) changes between releases.
MCP_SERVERS = {
    "notion": ("@notionhq/notion-mcp-server@2.5.2", lambda: {"NOTION_TOKEN": E["NOTION_TOKEN"]}),       # Notion's official server
    "hubspot": ("@hubspot/mcp-server@0.4.0", lambda: {"PRIVATE_APP_ACCESS_TOKEN": E["HUBSPOT_TOKEN"]}),  # HubSpot's official server
    # Reference server from modelcontextprotocol/servers (archived 2025-05); Slack's own MCP is hosted and needs OAuth.
    "slack": ("@modelcontextprotocol/server-slack@2025.4.25",
              lambda: {"SLACK_BOT_TOKEN": E["SLACK_BOT_TOKEN"], "SLACK_TEAM_ID": E["SLACK_TEAM_ID"]}),
}
# Allow-list arm: only the tools the three jobs need (the "filter your MCP tools" rebuttal).
MCP_ALLOW = {
    "notion": {"API-retrieve-a-data-source", "API-query-data-source", "API-post-page", "API-patch-page"},
    "hubspot": {"hubspot-search-objects", "hubspot-batch-read-objects", "hubspot-batch-update-objects", "hubspot-list-associations"},
    "slack": {"slack_get_channel_history", "slack_post_message"},
}


def mcp(name, allow=False):
    if name not in _MCP:
        pkg, env = MCP_SERVERS[name]
        _MCP[name] = MCP(["npx", "-y", pkg], env())
    tools = _MCP[name].as_tools()
    return [p for p in tools if p[0]["function"]["name"] in MCP_ALLOW[name]] if allow else tools


_DS = {}


def notion_data_source():
    # notion-mcp-server >= 2 queries by data-source id; giving only the database id cost a 404 + lookup per run.
    if not _DS:
        b = http("GET", f"https://api.notion.com/v1/databases/{NOTION_DB}", {**N_H, "Notion-Version": "2025-09-03"})[1]
        _DS["id"] = b["data_sources"][0]["id"]
    return _DS["id"]


def mcp_notes(chat_line):
    return (chat_line + f"- Task tracker = a Notion database, id {NOTION_DB}, data_source_id {notion_data_source()}.\n"
            + STACK_A["notes"].split("\n")[2])


TELEGRAM_LINE = STACK_A["notes"].split("\n")[0] + "\n"
SLACK_LINE = f"- Sales chat = Slack channel {E['SLACK_CHANNEL_ID']}.\n"
# Telegram has no MCP server that can read messages: Stack A keeps the 2 thin Telegram tools in every arm.
STACK_A_MCP = {"notes": lambda: mcp_notes(TELEGRAM_LINE),
               "tools": lambda: STACK_A["tools"][0:2] + mcp("notion") + mcp("hubspot")}
STACK_C_MCP = {"notes": lambda: mcp_notes(SLACK_LINE),
               "tools": lambda: mcp("slack") + mcp("notion") + mcp("hubspot")}
STACK_A_MCP_MIN = {"notes": lambda: mcp_notes(TELEGRAM_LINE),
                   "tools": lambda: STACK_A["tools"][0:2] + mcp("notion", True) + mcp("hubspot", True)}
STACK_C_MCP_MIN = {"notes": lambda: mcp_notes(SLACK_LINE),
                   "tools": lambda: mcp("slack", True) + mcp("notion", True) + mcp("hubspot", True)}

SANDBOX = E.get("PRIVOS_SANDBOX_DIR", os.path.join(ROOT, "..", "..", "privos-sandbox"))  # checkout of PrivOS-AI/privos-sandbox
SKILLS = os.path.join(SANDBOX, "src/hooks/template/skills")
SKILL_SCRIPTS = {"api.py": f"{SKILLS}/privos-list/scripts/api.py", "get_info.py": f"{SKILLS}/privos-list/scripts/get_info.py",
                 "comments.py": f"{SKILLS}/privos-list/scripts/comments.py", "chat.py": f"{SKILLS}/privos-chat/scripts/chat.py"}
SKILL_ENV = {"PYTHONPATH": os.path.join(SANDBOX, "packages/skill-sdk/lib"), "PYTHONWARNINGS": "ignore",
             "PRIVOS_URL": PRIVOS_URL, "PRIVOS_BOT_KEY": E.get("PRIVOS_TOKEN", ""), "PRIVOS_ROOM_ID": E.get("PRIVOS_ROOM_ID", "")}
SHELL_STOP = {"|", "||", "&&", ";", "2>&1", "2>/dev/null", ">", "2>"}


def run_skill(a):
    # What the sandbox agent runs through Bash (`python3 scripts/<x>.py ...` from the skill dir), without a real shell:
    # only the skill scripts are executable; shell decorations around them (cd, pipes, redirects) are ignored.
    try:
        argv = shlex.split(a["command"])
    except ValueError as e:
        return {"error": f"bad quoting: {e}"}
    i = next((k for k, w in enumerate(argv) if os.path.basename(w) in SKILL_SCRIPTS), None)
    if i is None:
        if argv and argv[0] in ("ls", "pwd", "find", "cat", "cd"):  # what an agent probes first in a real sandbox
            return "skill dir contents:\n  scripts/api.py\n  scripts/get_info.py\n  scripts/comments.py\n  scripts/chat.py\n  SKILL.md"
        return {"error": "only the skill scripts can run here: python3 scripts/{api,get_info,comments,chat}.py ..."}
    args = []
    for w in argv[i + 1:]:
        if w in SHELL_STOP:
            break
        args.append(w)
    r = subprocess.run(["python3", SKILL_SCRIPTS[os.path.basename(argv[i])], *args], capture_output=True, text=True,
                       timeout=120, env={**os.environ, **SKILL_ENV})
    return (r.stdout + r.stderr)[-20000:]


def skill_doc(name):
    return open(f"{SKILLS}/{name}/SKILL.md").read()


PRIVOS_SKILL = {
    "notes": lambda: (privos_notes() + f"\n- roomId = {E['PRIVOS_ROOM_ID']}; list ids: " + ", ".join(f"{k}={v}" for k, v in PRIVOS_LISTS.items())
              + "\n\nYou have these skills. Run their scripts with the `bash` tool exactly as each skill documents (working directory = the skill dir):\n\n"
              + "=== SKILL privos-chat ===\n" + skill_doc("privos-chat") + "\n\n=== SKILL privos-list ===\n" + skill_doc("privos-list")),
    "tools": [(tool("bash", "Run a shell command in the skill directory. Available scripts: scripts/api.py, scripts/get_info.py, "
                    "scripts/comments.py (privos-list) and scripts/chat.py (privos-chat), e.g. python3 scripts/get_info.py --context '{...}' --type items --list_id ID",
                    {"command": {"type": "string"}}, ["command"]), run_skill)],
}


# ------------------------------------------------------------------ Variant 2: PrivOS with list schemas pre-loaded in context
def _schema_notes():
    out = []
    for name, lid in PRIVOS_LISTS.items():
        fields = http("GET", f"{P_BASE}/lists/{lid}/fields", P_H)[1]["fieldDefinitions"]
        out.append(f"  {name}: " + "; ".join(
            f"{f['name']}={f['_id']} ({f['type']}" + (", options " + ", ".join(f"{o['value']}={o['_id']}" for o in f.get("options", [])) if f.get("options") else "") + ")"
            for f in fields))
    return "- List fields (fieldId, type, SELECT option ids):\n" + "\n".join(out)


PRIVOS_SCHEMA = {"notes": None, "tools": PRIVOS["tools"][:2] + PRIVOS["tools"][3:]}  # get_list not needed: schema is in context

STACKS = {"stack_a": STACK_A, "stack_b": STACK_B, "stack_c": STACK_C, "privos": PRIVOS, "stack_a_mcp": STACK_A_MCP, "stack_c_mcp": STACK_C_MCP,
          "stack_a_mcp_min": STACK_A_MCP_MIN, "stack_c_mcp_min": STACK_C_MCP_MIN, "privos_skill": PRIVOS_SKILL, "privos_schema": PRIVOS_SCHEMA}
if not HAS_STACK_B:
    STACKS.pop("stack_b")
if not HAS_PRIVOS:
    for _s in ("privos", "privos_skill", "privos_schema"):
        STACKS.pop(_s)


def tools_of(stack):
    cfg = STACKS[stack]
    if stack == "privos_schema" and cfg["notes"] is None:
        cfg["notes"] = privos_notes() + "\n" + _schema_notes()
    if callable(cfg["notes"]):
        cfg["notes"] = cfg["notes"]()
    return cfg["tools"]() if callable(cfg["tools"]) else cfg["tools"]


# ------------------------------------------------------------------ reset to seed state
def reset():
    # PrivOS: drop non-seed items, restore deal/contact/task, delete bot messages that are not seed messages
    seed = json.load(open(STATE_FILE))  # re-read: the large dataset may have been added after import
    L = seed.get("large", {})
    keep_msgs = set(MESSAGES) | set(L.get("messages", []))
    if HAS_PRIVOS:
        keep = {PS["task_demo"], PS["deal_linh"], *PS["contact_items"].values(), *L.get("privos", [])}
        for lid in PRIVOS_LISTS.values():
            for it in need(*http("GET", f"{P_BASE}/items?listId={lid}&count=200", P_H), "privos items")["items"]:
                if it["_id"] not in keep:
                    http("DELETE", f"{P_BASE}/items/{it['_id']}", P_H)
        fields = lambda lid: {f["name"]: f["_id"] for f in http("GET", f"{P_BASE}/lists/{lid}/fields", P_H)[1]["fieldDefinitions"]}
        df, cf = fields(PS["deals_list"]), fields(PS["contacts_list"])
        http("PUT", f"{P_BASE}/items/{PS['deal_linh']}", P_H, {"stageId": ds["Proposal"]})
        http("PUT", f"{P_BASE}/items/{PS['deal_linh']}/customFields", P_H,
             {"fields": [{"fieldId": df["Amount"], "value": DEAL["amount"]}, {"fieldId": df["Next Step"], "value": ""}]})
        http("PUT", f"{P_BASE}/items/{PS['task_demo']}", P_H, {"stageId": st["Done"]})
        http("PUT", f"{P_BASE}/items/{PS['contact_items']['buyer@acme.example.com']}/customFields", P_H,
             {"fields": [{"fieldId": cf["Lifecycle"], "value": "lc_lead"}]})
        for m in http("GET", f"{PRIVOS_URL}/api/v1/groups.history?roomId={E['PRIVOS_ROOM_ID']}&count=100", P_H)[1]["messages"]:
            if m.get("msg") and m["msg"] not in keep_msgs and not m.get("t"):
                http("POST", f"{PRIVOS_URL}/api/v1/chat.delete", P_H, {"roomId": E["PRIVOS_ROOM_ID"], "msgId": m["_id"]})
    # Notion: archive non-seed pages, restore demo task
    demo = SEED["notion"]["task_demo"]
    for p in http("POST", f"https://api.notion.com/v1/databases/{NOTION_DB}/query", N_H, {"page_size": 100})[1]["results"]:
        if p["id"] != demo and p["id"] not in L.get("notion", []):
            http("PATCH", f"https://api.notion.com/v1/pages/{p['id']}", N_H, {"archived": True})
    http("PATCH", f"https://api.notion.com/v1/pages/{demo}", N_H, {"properties": {"Status": {"select": {"name": "Done"}}}})
    # HubSpot: deal back to Proposal, Acme back to lead (lifecycle can only move backwards after clearing)
    hs = SEED["hubspot"]
    http("PATCH", f"{HS}/crm/v3/objects/deals/{hs['deal_linh']}", H_H,
         {"properties": {"dealstage": "qualifiedtobuy", "amount": str(DEAL["amount"]), "hs_next_step": ""}})
    acme = hs["contacts"]["buyer@acme.example.com"]
    http("PATCH", f"{HS}/crm/v3/objects/contacts/{acme}", H_H, {"properties": {"lifecyclestage": ""}})
    http("PATCH", f"{HS}/crm/v3/objects/contacts/{acme}", H_H, {"properties": {"lifecyclestage": "lead"}})
    if HAS_STACK_B:  # optional Stack B: Trello cards back to seed, Discord bot notices removed
        for c in http("GET", f"{TR}/boards/{TRL['board']}/cards?fields=name&{TK()}")[1]:
            if c["id"] != TRL["card_demo"] and c["id"] not in L.get("trello", []):
                http("DELETE", f"{TR}/cards/{c['id']}?{TK()}")
        http("PUT", f"{TR}/cards/{TRL['card_demo']}?{TK()}", body={"idList": TRL["lists"]["Done"]})
        for m in http("GET", f"{DC}?limit=100", DC_H)[1]:
            if m.get("author", {}).get("bot") and m.get("content") not in keep_msgs:
                http("DELETE", f"{DC}/{m['id']}", DC_H)
                time.sleep(0.5)  # Discord delete rate limit
    # Slack: delete the bot's own non-seed messages (agent notices); SLACK_IGNORE_APP_ID keeps another app's posts
    ignore = E.get("SLACK_IGNORE_APP_ID")
    for m in http("GET", f"{SL}/conversations.history?channel={E['SLACK_CHANNEL_ID']}&limit=100", SL_H)[1].get("messages", []):
        if m.get("bot_id") and not (ignore and m.get("app_id") == ignore) and m.get("text") not in keep_msgs:
            http("POST", f"{SL}/chat.delete", SL_H, {"channel": E["SLACK_CHANNEL_ID"], "ts": m["ts"]})
    # Telegram: nothing to do — the bot's own messages never show up in getUpdates
    CREATED.clear()
    print("reset: seed state restored" + (" (incl. Stack B)" if HAS_STACK_B else ""))


# ------------------------------------------------------------------ provenance recorded in every result file
import hashlib


def _sha(x):
    return hashlib.sha256(x.encode()).hexdigest()[:16]


def harness_meta(tools, system_prompt):
    try:
        sandbox = subprocess.run(["git", "-C", SANDBOX, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except Exception:
        sandbox = None
    return {"temperature": 0, "seed": None, "mcp_servers": {k: v[0] for k, v in MCP_SERVERS.items()},
            "tools_sha256": _sha(json.dumps(tools, sort_keys=True)), "system_prompt_sha256": _sha(system_prompt),
            "system_prompt_chars": len(system_prompt), "privos_sandbox_commit": sandbox}


# ------------------------------------------------------------------ agent loop
def chat(messages, tools):
    for attempt in range(8):  # network/5xx retries (~3 min total) only; a retried call is not counted twice
        try:
            s, b = http("POST", E["ZAI_BASE_URL"].rstrip("/") + "/chat/completions",
                        {"Authorization": "Bearer " + E["ZAI_API_KEY"]},
                        {"model": MODEL, "messages": messages, "tools": tools, "tool_choice": "auto", "temperature": 0},
                        timeout=300)
        except OSError as e:
            s, b = 599, str(e)
        if s < 500 and s != 429:
            break
        print(f"   … LLM unreachable ({str(b)[:60]}), retry {attempt + 1}/8", flush=True)
        time.sleep(min(5 * (attempt + 1), 40))
    if s >= 300:
        raise SystemExit(f"LLM HTTP {s}: {str(b)[:300]}")
    return b


def run_job(stack, job, run_no, run_tokens):
    cfg = STACKS[stack]
    pairs = tools_of(stack)
    tools = [t for t, _ in pairs]
    fns = {t["function"]["name"]: f for t, f in pairs}
    messages = [{"role": "system", "content": SYSTEM + cfg["notes"]}, {"role": "user", "content": JOBS[job]}]
    calls, tot = [], {"in": 0, "out": 0, "cached": 0, "reasoning": 0}
    print(f"\n[{stack} | {job} | run {run_no}]", flush=True)
    for n in range(1, MAX_CALLS_PER_JOB + 1):
        t0 = time.time()
        r = chat(messages, tools)
        u = r.get("usage", {})
        model_ok = r.get("model") == MODEL
        msg = r["choices"][0]["message"]
        tcs = msg.get("tool_calls") or []
        rec = {"n": n, "model": r.get("model"), "usage": u, "latency_s": round(time.time() - t0, 2),
               "tools": [tc["function"]["name"] for tc in tcs]}
        calls.append(rec)
        i, o = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        c = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        rz = (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
        tot["in"] += i; tot["out"] += o; tot["cached"] += c; tot["reasoning"] += rz
        label = ", ".join(rec["tools"]) or "(final answer)"
        print(f" #{n:<2} model={r.get('model')} {'✓' if model_ok else '✗'}  → {label:<40} in={i:>7,}  out={o:>5,}  cached={c:>7,}", flush=True)
        if not model_ok:
            raise SystemExit(f"ABORT: model {r.get('model')!r} != {MODEL!r} — results invalid")
        if run_tokens + tot["in"] + tot["out"] > MAX_TOKENS_PER_RUN:
            raise SystemExit(f"ABORT: run exceeded {MAX_TOKENS_PER_RUN:,} tokens")
        clean = {k: v for k, v in msg.items() if v is not None}
        messages.append(clean)
        if not tcs:
            rec["final"] = msg.get("content")
            break
        for tc in tcs:
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
                out = fns[tc["function"]["name"]](args)
            except Exception as e:  # tool errors go back to the model, like a real agent harness
                out = {"error": str(e)}
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(out, ensure_ascii=False)})
    total = tot["in"] + tot["out"]
    print(f" TOTAL  calls={len(calls)}  in={tot['in']:,}  out={tot['out']:,}  cached={tot['cached']:,}  "
          f"total={total:,}  tools_loaded={len(tools)}", flush=True)
    return {"stack": stack, "job": job, "run": run_no, "model": MODEL, "endpoint": E["ZAI_BASE_URL"],
            "harness": harness_meta(tools, messages[0]["content"]),
            "tools_loaded": [t["function"]["name"] for t in tools], "tool_count": len(tools),
            "calls": calls, "totals": {**tot, "total": total, "calls": len(calls)},
            "final": calls[-1].get("final"), "transcript": messages}


def save(res, stamp):
    os.makedirs(RESULTS, exist_ok=True)
    p = os.path.join(RESULTS, f"agent_run_{res['stack']}_{res['job']}_{res['run']}_{stamp}.json")
    json.dump(res, open(p, "w"), indent=2, ensure_ascii=False)


def report(stamp=None):
    files = sorted(glob.glob(os.path.join(RESULTS, "agent_run_*.json")))
    stamp = stamp or (files[-1].rsplit("_", 1)[1][:-5] if files else None)
    files = [f for f in files if f.endswith(f"_{stamp}.json")]
    rows, n_calls, tc = {}, 0, {}
    for f in files:
        r = json.load(open(f))
        rows.setdefault((r["job"], r["stack"]), []).append(r["totals"]["total"])
        n_calls += len(r["calls"])
        tc[r["stack"]] = r["tool_count"]
    stacks = [s for s in STACKS if any(k[1] == s for k in rows)]
    mean = lambda j, s: statistics.mean(rows[(j, s)]) if rows.get((j, s)) else 0
    base = next((s for s in stacks if s.startswith("privos")), None)  # ratio baseline: privos / privos_skill / privos_schema
    others = [s for s in stacks if s != base] if base else []
    ratio = lambda a, b: f"{a / b:.1f}×" if b else "–"
    lines = [f"Batch {stamp} · model {MODEL} · mean total tokens per job (n = runs)", "",
             "| Job | " + " | ".join(stacks) + "".join(f" | {s} ÷ {base}" for s in others) + " |",
             "|---" * (1 + len(stacks) + len(others)) + "|"]
    tot = {s: 0 for s in stacks}
    for j in list(JOBS) + ["Total"]:
        vals = tot if j == "Total" else {s: mean(j, s) for s in stacks}
        if j != "Total":
            for s in stacks:
                tot[s] += vals[s]
        cells = [f"{vals[s]:,.0f}" + ("" if j == "Total" else f" (n={len(rows.get((j, s), []))})") for s in stacks]
        cells += [ratio(vals[s], vals[base]) for s in others]
        b = "**" if j == "Total" else ""
        lines.append(f"| {b}{j}{b} | " + " | ".join(f"{b}{c}{b}" for c in cells) + " |")
    lines += ["", f"All {n_calls} model calls returned {MODEL}. Tool counts: "
              + ", ".join(f"{s}={tc[s]}" for s in stacks) + "."]
    out = "\n".join(lines)
    print(out)
    if stamp:
        open(os.path.join(RESULTS, f"summary_{stamp}.md"), "w").write(out + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-tools", choices=list(STACKS))
    ap.add_argument("--stack", choices=list(STACKS))
    ap.add_argument("--job", choices=list(JOBS))
    ap.add_argument("--all-jobs", action="store_true")
    ap.add_argument("--stacks", default="stack_a,stack_b,stack_c,privos")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--reset-between-runs", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--resume", metavar="BATCH", help="continue an interrupted batch (e.g. 20261002-094204)")
    a = ap.parse_args()
    if a.list_tools:
        pairs = tools_of(a.list_tools)
        names = [t["function"]["name"] for t, _ in pairs]
        size = len(json.dumps([t for t, _ in pairs])) + len(STACKS[a.list_tools]["notes"])
        print("\n".join(f"  {n}" for n in names) + f"\n{a.list_tools}: {len(names)} tools, {size:,} chars of tool definitions + workspace notes")
        return
    if a.reset:
        return reset()
    if a.report:
        return report()
    stamp = a.resume or time.strftime("%Y%m%d-%H%M%S")
    stacks = [a.stack] if a.stack else a.stacks.split(",")
    skipped = [s for s in stacks if s not in STACKS]
    if skipped:  # e.g. PrivOS or Stack B not configured in .env
        print(f"skipping stacks that are not configured: {', '.join(skipped)}")
        stacks = [s for s in stacks if s in STACKS]
    jobs = list(JOBS) if a.all_jobs or not a.job else [a.job]
    print(f"model={MODEL}  endpoint={E['ZAI_BASE_URL']}  stacks={stacks}  jobs={jobs}  runs={a.runs}")
    for run_no in range(1, a.runs + 1):
        for s in stacks:
            # resume: a (run, stack) group is kept only if all its jobs finished; otherwise it is redone from a reset
            done = [os.path.join(RESULTS, f"agent_run_{s}_{j}_{run_no}_{stamp}.json") for j in jobs]
            if a.resume and all(map(os.path.exists, done)):
                print(f"[{s} | run {run_no}] already done, skipped")
                continue
            if a.reset_between_runs:
                reset()
            run_tokens = 0
            for j in jobs:
                res = run_job(s, j, run_no, run_tokens)
                run_tokens += res["totals"]["total"]
                save(res, stamp)
    if a.reset_between_runs:
        reset()
    report(stamp)


if __name__ == "__main__":
    main()
