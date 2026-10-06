#!/usr/bin/env python3
"""Seed identical benchmark data on PrivOS, Notion, HubSpot, Slack, Trello and Discord.

Idempotent: everything is looked up by name/email first and only created when
missing. IDs are written to seed_state.json (no secrets) for the runner's reset.
Data mirrors fixtures/{privos,multiplatform}/scenarios.json of the benchmark repo.

Telegram is NOT seeded here: a bot never receives its own messages through
getUpdates, so the 3 chat messages must be posted by a human (printed at the end).
"""
import json, os, sys, time, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.environ.get("BM_ENV", os.path.join(ROOT, ".env"))  # see .env.example
STATE_FILE = os.path.join(ROOT, "seed_state.json")

MESSAGES = [
    "Linh Tran: Hi, we want to upgrade to the Pro plan for 5 seats. Can you send a quote and set up onboarding next week?",
    "Minh: Customer Acme asked for SSO pricing.",
    "Hoa: Beta customer wants a renewal call.",
]
TASK = {"name": "Demo call with Linh Tran", "priority": "High", "customer": "Linh Tran (@linhtran)", "status": "Done"}
DEAL = {"name": "Linh Tran - Pro Upgrade", "amount": 1188, "customer": "Linh Tran (@linhtran)"}
CONTACTS = [
    {"name": "Linh Tran", "first": "Linh", "last": "Tran", "email": "linh.tran@example.com", "company": "", "lifecycle": "opportunity"},
    {"name": "Acme buyer", "first": "", "last": "", "email": "buyer@acme.example.com", "company": "Acme", "lifecycle": "lead"},
]
LIFECYCLE_LABEL = {"lead": "Lead", "salesqualifiedlead": "Sales Qualified Lead", "opportunity": "Opportunity", "customer": "Customer"}


def load_env():
    env = {}
    for line in open(ENV_FILE):
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            env[k.strip()] = v.split("   #")[0].strip()
    return env


E = load_env()
STATE = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}


def http(method, url, headers=None, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": "curl/8.7.1", "Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    for attempt in range(4):  # retry timeouts, dropped connections, 429 and 5xx (Notion/HubSpot hiccups during reset)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            if (e.code == 429 or e.code >= 500) and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, raw.decode(errors="ignore")[:200]
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if attempt == 3 or method == "POST":  # a timed-out POST may have created something: never repeat it
                raise
            time.sleep(2 * (attempt + 1))


def need(status, body, what):
    if status >= 300:
        raise SystemExit(f"FAIL {what}: HTTP {status} {str(body)[:200]}")
    return body


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- PrivOS
P_BASE = E.get("PRIVOS_URL", "").rstrip("/") + "/api/v1/internal/rooms/" + E.get("PRIVOS_ROOM_ID", "")
P_H = {"Authorization": "Bearer " + E.get("PRIVOS_TOKEN", "")}


def p_get(path):
    return need(*http("GET", P_BASE + path, P_H), "GET " + path)


def p_post(path, body):
    return need(*http("POST", P_BASE + path, P_H, body), "POST " + path)


def p_ensure_fields(list_id, fields):
    have = {f["name"]: f for f in p_get(f"/lists/{list_id}/fields").get("fieldDefinitions", [])}
    for f in fields:
        if f["name"] not in have:
            p_post(f"/lists/{list_id}/fields", {"field": f})
            log(f"  + field {f['name']}")
    return {f["name"]: f for f in p_get(f"/lists/{list_id}/fields").get("fieldDefinitions", [])}


def p_ensure_stages(list_id, names):
    have = {s["name"]: s["_id"] for s in p_get(f"/stages?listId={list_id}").get("stages", [])}
    for i, n in enumerate(names):
        if n not in have:
            s = p_post("/stages", {"name": n, "listId": list_id, "order": i})
            have[n] = (s.get("stage") or s).get("_id")
            log(f"  + stage {n}")
    return have


def p_ensure_item(list_id, stage_id, name, custom):
    items = p_get(f"/items?listId={list_id}&count=200").get("items", [])
    for it in items:
        if it.get("name") == name:
            return it["_id"], False
    r = p_post("/items", {"name": name, "listId": list_id, "stageId": stage_id, "customFields": custom})
    return (r.get("item") or r).get("_id"), True


def opts(prefix, labels):
    return [{"_id": f"{prefix}_{l.lower().replace(' ', '_')}", "value": l, "order": i} for i, l in enumerate(labels)]


def seed_privos():
    log("== PrivOS")
    st = STATE.setdefault("privos", {})
    # Tasks
    tl = E["PRIVOS_LIST_ID"]
    f = p_ensure_fields(tl, [
        {"name": "Priority", "type": "SELECT", "options": opts("pri", ["High", "Medium", "Low"])},
        {"name": "Customer", "type": "TEXT"},
        {"name": "Due", "type": "DATE"},
    ])
    stages = p_ensure_stages(tl, ["To Do", "Done"])
    iid, new = p_ensure_item(tl, stages["Done"], TASK["name"], [
        {"fieldId": f["Priority"]["_id"], "value": "pri_high"},
        {"fieldId": f["Customer"]["_id"], "value": TASK["customer"]},
    ])
    log(f"  {'+' if new else '='} task '{TASK['name']}' (Done)")
    st.update(tasks_list=tl, task_stages=stages, task_demo=iid)
    # Deals (the existing "CRM" list)
    dl = E["PRIVOS_CRM_LIST_ID"]
    f = p_ensure_fields(dl, [
        {"name": "Amount", "type": "NUMBER"},
        {"name": "Next Step", "type": "TEXT"},
        {"name": "Customer", "type": "TEXT"},
    ])
    dstages = p_ensure_stages(dl, ["Proposal", "Quote Sent", "Closed Won"])
    did, new = p_ensure_item(dl, dstages["Proposal"], DEAL["name"], [
        {"fieldId": f["Amount"]["_id"], "value": DEAL["amount"]},
        {"fieldId": f["Customer"]["_id"], "value": DEAL["customer"]},
    ])
    log(f"  {'+' if new else '='} deal '{DEAL['name']}' (Proposal)")
    st.update(deals_list=dl, deal_stages=dstages, deal_linh=did)
    # Contacts (third list, mirrors HubSpot contacts)
    lists = p_get("/lists").get("lists", [])
    cl = next((l["_id"] for l in lists if l.get("name") == "Contacts"), None)
    if not cl:
        r = p_post("/lists", {"name": "Contacts"})
        cl = (r.get("list") or r).get("_id")
        log("  + list Contacts")
    f = p_ensure_fields(cl, [
        {"name": "Email", "type": "TEXT"},
        {"name": "Company", "type": "TEXT"},
        {"name": "Lifecycle", "type": "SELECT", "options": opts("lc", list(LIFECYCLE_LABEL.values()))},
    ])
    cst = p_ensure_stages(cl, ["Contacts"])
    st.update(contacts_list=cl, contact_items={})
    for c in CONTACTS:
        lc_id = "lc_" + LIFECYCLE_LABEL[c["lifecycle"]].lower().replace(" ", "_")
        cid, new = p_ensure_item(cl, cst["Contacts"], c["name"], [
            {"fieldId": f["Email"]["_id"], "value": c["email"]},
            {"fieldId": f["Company"]["_id"], "value": c["company"]},
            {"fieldId": f["Lifecycle"]["_id"], "value": lc_id},
        ])
        st["contact_items"][c["email"]] = cid
        log(f"  {'+' if new else '='} contact '{c['name']}' ({LIFECYCLE_LABEL[c['lifecycle']]})")
    # Messages (bot posts; prefix carries the customer's name)
    sent = st.setdefault("messages", [])
    for m in MESSAGES:
        if m in sent:
            continue
        need(*http("POST", E["PRIVOS_URL"].rstrip("/") + "/api/v1/bot/sendMessage", P_H,
                   {"roomId": E["PRIVOS_ROOM_ID"], "text": m}), "bot/sendMessage")
        sent.append(m)
        log(f"  + message '{m[:40]}…'")


# ---------------------------------------------------------------- Notion
N_H = {"Authorization": "Bearer " + E["NOTION_TOKEN"], "Notion-Version": "2022-06-28"}


def seed_notion():
    log("== Notion")
    db = E["NOTION_DATABASE_ID"]
    need(*http("PATCH", f"https://api.notion.com/v1/databases/{db}", N_H, {"properties": {
        "Priority": {"select": {"options": [{"name": "High"}, {"name": "Medium"}, {"name": "Low"}]}},
        "Customer": {"rich_text": {}},
        "Due": {"date": {}},
        "Status": {"select": {"options": [{"name": "To Do"}, {"name": "Done"}]}},
    }}), "Notion add properties")
    log("  = properties Priority / Customer / Due / Status")
    q = need(*http("POST", f"https://api.notion.com/v1/databases/{db}/query", N_H,
                   {"filter": {"property": "Name", "title": {"equals": TASK["name"]}}}), "Notion query")
    if q["results"]:
        pid = q["results"][0]["id"]
        log(f"  = task '{TASK['name']}'")
    else:
        pid = need(*http("POST", "https://api.notion.com/v1/pages", N_H, {
            "parent": {"database_id": db},
            "properties": {
                "Name": {"title": [{"text": {"content": TASK["name"]}}]},
                "Priority": {"select": {"name": TASK["priority"]}},
                "Customer": {"rich_text": [{"text": {"content": TASK["customer"]}}]},
                "Status": {"select": {"name": TASK["status"]}},
            }}), "Notion create page")["id"]
        log(f"  + task '{TASK['name']}' (Done)")
    STATE.setdefault("notion", {}).update(database=db, task_demo=pid)


# ---------------------------------------------------------------- HubSpot
H_H = {"Authorization": "Bearer " + E["HUBSPOT_TOKEN"]}
HS = "https://api.hubapi.com"


def hs_find(obj, prop, value):
    r = need(*http("POST", f"{HS}/crm/v3/objects/{obj}/search", H_H, {
        "filterGroups": [{"filters": [{"propertyName": prop, "operator": "EQ", "value": value}]}], "limit": 1}),
        f"HubSpot search {obj}")
    return r["results"][0]["id"] if r.get("results") else None


def seed_hubspot():
    log("== HubSpot")
    st = STATE.setdefault("hubspot", {"contacts": {}})
    for c in CONTACTS:
        cid = hs_find("contacts", "email", c["email"])
        if not cid:
            props = {"email": c["email"], "lifecyclestage": c["lifecycle"]}
            for k, v in (("firstname", c["first"]), ("lastname", c["last"]), ("company", c["company"])):
                if v:
                    props[k] = v
            cid = need(*http("POST", f"{HS}/crm/v3/objects/contacts", H_H, {"properties": props}), "HubSpot create contact")["id"]
            log(f"  + contact {c['email']} ({c['lifecycle']})")
        else:
            log(f"  = contact {c['email']}")
        st["contacts"][c["email"]] = cid
    did = hs_find("deals", "dealname", DEAL["name"])
    if not did:
        did = need(*http("POST", f"{HS}/crm/v3/objects/deals", H_H, {"properties": {
            "dealname": DEAL["name"], "amount": str(DEAL["amount"]), "pipeline": "default",
            "dealstage": "qualifiedtobuy", "closedate": "2026-10-31T00:00:00.000Z"}}), "HubSpot create deal")["id"]
        log(f"  + deal '{DEAL['name']}' (qualifiedtobuy = 'Proposal' stand-in)")
        need(*http("PUT", f"{HS}/crm/v4/objects/deal/{did}/associations/default/contact/{st['contacts']['linh.tran@example.com']}",
                   H_H), "HubSpot associate deal→contact")
        log("  + association deal → Linh Tran")
    else:
        log(f"  = deal '{DEAL['name']}'")
    st["deal_linh"] = did


# ---------------------------------------------------------------- Slack
def seed_slack():
    log("== Slack")
    sent = STATE.setdefault("slack", {}).setdefault("messages", [])
    for m in MESSAGES:
        if m in sent:
            continue
        r = need(*http("POST", "https://slack.com/api/chat.postMessage", {"Authorization": "Bearer " + E["SLACK_BOT_TOKEN"]},
                       {"channel": E["SLACK_CHANNEL_ID"], "text": m}), "Slack postMessage")
        if not r.get("ok"):
            raise SystemExit(f"FAIL Slack postMessage: {r.get('error')}")
        sent.append(m)
        log(f"  + message '{m[:40]}…'")


# ---------------------------------------------------------------- Trello (Stack B tasks)
TK = lambda: f"key={E['TRELLO_KEY']}&token={E['TRELLO_TOKEN']}"  # secret in URL: never print URLs
TR = "https://api.trello.com/1"
PRIORITY_COLOR = {"High": "red", "Medium": "yellow", "Low": "green"}


def seed_trello():
    log("== Trello")
    board = need(*http("GET", f"{TR}/lists/{E['TRELLO_LIST_ID']}?fields=idBoard&{TK()}"), "Trello list")["idBoard"]
    lists = {l["name"]: l["id"] for l in need(*http("GET", f"{TR}/boards/{board}/lists?fields=name&{TK()}"), "Trello lists")}
    labels = {}
    for lb in need(*http("GET", f"{TR}/boards/{board}/labels?fields=name,color&{TK()}"), "Trello labels"):
        for name, color in PRIORITY_COLOR.items():
            if lb["color"] == color and name not in labels:
                if lb["name"] != name:
                    http("PUT", f"{TR}/labels/{lb['id']}?name={name}&{TK()}")
                    log(f"  = label {color} -> {name}")
                labels[name] = lb["id"]
    cards = need(*http("GET", f"{TR}/boards/{board}/cards?fields=name,idList&{TK()}"), "Trello cards")
    for c in cards:
        if c["name"].startswith("benchmark-"):
            http("DELETE", f"{TR}/cards/{c['id']}?{TK()}")
            log(f"  - removed test card {c['name']}")
    demo = next((c["id"] for c in cards if c["name"] == TASK["name"]), None)
    if not demo:
        demo = need(*http("POST", f"{TR}/cards?{TK()}", body={
            "idList": lists["Done"], "name": TASK["name"], "idLabels": [labels[TASK["priority"]]],
            "desc": f"Customer: {TASK['customer']}"}), "Trello create card")["id"]
        log(f"  + card '{TASK['name']}' (Done, High)")
    else:
        log(f"  = card '{TASK['name']}'")
    STATE["trello"] = {"board": board, "lists": {k: lists[k] for k in ("To Do", "Done")}, "labels": labels, "card_demo": demo}


# ---------------------------------------------------------------- Discord (Stack B chat)
def seed_discord():
    log("== Discord")
    sent = STATE.setdefault("discord", {}).setdefault("messages", [])
    for m in MESSAGES:
        if m in sent:
            continue
        need(*http("POST", f"https://discord.com/api/v10/channels/{E['DISCORD_CHANNEL_ID']}/messages",
                   {"Authorization": "Bot " + E["DISCORD_BOT_TOKEN"]}, {"content": m}), "Discord send")
        sent.append(m)
        log(f"  + message '{m[:40]}…'")


# ---------------------------------------------------------------- Variant 3: larger, more realistic dataset
# Same extra records on every side that bots can write (Telegram needs a human, so Stack A is excluded).
_CUST = ["Acme", "Beta", "Globex", "Initech", "Umbrella", "Hooli", "Stark", "Wayne", "Wonka", "Soylent", "Tyrell", "Cyberdyne"]
EXTRA_TASKS = [{"name": f"{v} {c}", "priority": ["High", "Medium", "Low"][i % 3], "customer": c, "due": f"2026-10-{10 + i % 18:02d}"}
               for i, (v, c) in enumerate((v, c) for v in ("Follow up with", "Prepare contract for") for c in _CUST)]
EXTRA_DEALS = [{"name": f"{c} - {p}", "amount": a, "stage": s, "customer": c}
               for c, p, a, s in zip(_CUST[2:], ["Enterprise", "Team Plan", "Add-on Seats", "Renewal", "Pilot", "Upsell",
                                                  "Enterprise", "Team Plan", "Renewal", "Pilot"],
                                     [24000, 3600, 900, 12000, 1500, 4800, 30000, 2400, 9600, 1200],
                                     ["Proposal", "Quote Sent"] * 5)]
EXTRA_CONTACTS = [{"name": f"{c} buyer", "email": f"buyer@{c.lower()}.example.com", "company": c, "lifecycle": ["lead", "opportunity"][i % 2]}
                  for i, c in enumerate(_CUST[2:])]
EXTRA_MESSAGES = [
    "Tuan: Globex asked to move the kickoff to Thursday.", "Mai: Initech invoice #1042 was paid this morning.",
    "Khoa: Reminder - pipeline review at 4pm today.", "Lan: Umbrella wants a security questionnaire before signing.",
    "Tuan: Hooli trial ends Friday, they seem happy so far.", "Mai: Stark legal sent back redlines on the MSA.",
    "Khoa: Wayne asked whether we support SAML.", "Lan: Wonka renewal is due next month, no action yet.",
    "Tuan: Soylent cancelled tomorrow's demo, will reschedule.", "Mai: Tyrell needs an updated price list in EUR.",
    "Khoa: Cyberdyne signed the pilot order form.", "Lan: Office will be closed on Monday for maintenance.",
]
HS_STAGE = {"Proposal": "qualifiedtobuy", "Quote Sent": "presentationscheduled"}


def seed_large():
    L = STATE.setdefault("large", {"privos": [], "notion": [], "trello": [], "hubspot_deals": [], "hubspot_contacts": [], "messages": []})
    if L["privos"]:
        return log("== large dataset already seeded")
    log("== large dataset")
    ps, tr = STATE["privos"], STATE["trello"]
    tf = {f["name"]: f["_id"] for f in p_get(f"/lists/{ps['tasks_list']}/fields")["fieldDefinitions"]}
    df = {f["name"]: f["_id"] for f in p_get(f"/lists/{ps['deals_list']}/fields")["fieldDefinitions"]}
    cf = {f["name"]: f["_id"] for f in p_get(f"/lists/{ps['contacts_list']}/fields")["fieldDefinitions"]}
    cst = {s["name"]: s["_id"] for s in p_get(f"/stages?listId={ps['contacts_list']}")["stages"]}["Contacts"]
    for x in EXTRA_TASKS:
        r = p_post("/items", {"name": x["name"], "listId": ps["tasks_list"], "stageId": ps["task_stages"]["To Do"], "customFields": [
            {"fieldId": tf["Priority"], "value": "pri_" + x["priority"].lower()}, {"fieldId": tf["Customer"], "value": x["customer"]},
            {"fieldId": tf["Due"], "value": x["due"]}]})
        L["privos"].append((r.get("item") or r)["_id"])
        n = need(*http("POST", "https://api.notion.com/v1/pages", N_H, {"parent": {"database_id": E["NOTION_DATABASE_ID"]}, "properties": {
            "Name": {"title": [{"text": {"content": x["name"]}}]}, "Priority": {"select": {"name": x["priority"]}},
            "Customer": {"rich_text": [{"text": {"content": x["customer"]}}]}, "Due": {"date": {"start": x["due"]}},
            "Status": {"select": {"name": "To Do"}}}}), "Notion page")
        L["notion"].append(n["id"])
        c = need(*http("POST", f"{TR}/cards?{TK()}", body={"idList": tr["lists"]["To Do"], "name": x["name"], "due": x["due"],
                                                           "idLabels": [tr["labels"][x["priority"]]], "desc": f"Customer: {x['customer']}"}), "Trello card")
        L["trello"].append(c["id"])
    log(f"  + {len(EXTRA_TASKS)} tasks (PrivOS, Notion, Trello)")
    for x in EXTRA_DEALS:
        r = p_post("/items", {"name": x["name"], "listId": ps["deals_list"], "stageId": ps["deal_stages"][x["stage"]], "customFields": [
            {"fieldId": df["Amount"], "value": x["amount"]}, {"fieldId": df["Customer"], "value": x["customer"]}]})
        L["privos"].append((r.get("item") or r)["_id"])
        h = need(*http("POST", f"{HS}/crm/v3/objects/deals", H_H, {"properties": {"dealname": x["name"], "amount": str(x["amount"]),
                 "pipeline": "default", "dealstage": HS_STAGE[x["stage"]]}}), "HubSpot deal")
        L["hubspot_deals"].append(h["id"])
    log(f"  + {len(EXTRA_DEALS)} deals (PrivOS, HubSpot)")
    for x in EXTRA_CONTACTS:
        r = p_post("/items", {"name": x["name"], "listId": ps["contacts_list"], "stageId": cst, "customFields": [
            {"fieldId": cf["Email"], "value": x["email"]}, {"fieldId": cf["Company"], "value": x["company"]},
            {"fieldId": cf["Lifecycle"], "value": "lc_" + LIFECYCLE_LABEL[x["lifecycle"]].lower().replace(" ", "_")}]})
        L["privos"].append((r.get("item") or r)["_id"])
        h = need(*http("POST", f"{HS}/crm/v3/objects/contacts", H_H, {"properties": {"email": x["email"], "company": x["company"],
                 "lifecyclestage": x["lifecycle"]}}), "HubSpot contact")
        L["hubspot_contacts"].append(h["id"])
    log(f"  + {len(EXTRA_CONTACTS)} contacts (PrivOS, HubSpot)")
    for m in EXTRA_MESSAGES:
        need(*http("POST", E["PRIVOS_URL"].rstrip("/") + "/api/v1/bot/sendMessage", P_H, {"roomId": E["PRIVOS_ROOM_ID"], "text": m}), "PrivOS msg")
        need(*http("POST", "https://slack.com/api/chat.postMessage", {"Authorization": "Bearer " + E["SLACK_BOT_TOKEN"]},
                   {"channel": E["SLACK_CHANNEL_ID"], "text": m}), "Slack msg")
        need(*http("POST", f"https://discord.com/api/v10/channels/{E['DISCORD_CHANNEL_ID']}/messages",
                   {"Authorization": "Bot " + E["DISCORD_BOT_TOKEN"]}, {"content": m}), "Discord msg")
        L["messages"].append(m)
    log(f"  + {len(EXTRA_MESSAGES)} chat messages (PrivOS, Slack, Discord)")


def unseed_large():
    """Remove the large dataset; runner.reset() then deletes the chat messages (no longer in the keep set)."""
    L = STATE.pop("large", None)
    if not L:
        return log("== no large dataset")
    for i in L["privos"]:
        http("DELETE", f"{P_BASE}/items/{i}", P_H)
    for i in L["notion"]:
        http("PATCH", f"https://api.notion.com/v1/pages/{i}", N_H, {"archived": True})
    for i in L["trello"]:
        http("DELETE", f"{TR}/cards/{i}?{TK()}")
    for i in L["hubspot_deals"]:
        http("DELETE", f"{HS}/crm/v3/objects/deals/{i}", H_H)
    for i in L["hubspot_contacts"]:
        http("DELETE", f"{HS}/crm/v3/objects/contacts/{i}", H_H)
    log("== large dataset removed (run `runner.py --reset` to drop its chat messages)")


if __name__ == "__main__":
    # default: every platform whose credentials are set in .env; or name them, e.g. `seed.py notion hubspot slack`
    configured = {"privos": ("PRIVOS_URL", "PRIVOS_TOKEN", "PRIVOS_ROOM_ID", "PRIVOS_LIST_ID", "PRIVOS_CRM_LIST_ID"),
                  "notion": ("NOTION_TOKEN", "NOTION_DATABASE_ID"), "hubspot": ("HUBSPOT_TOKEN",),
                  "slack": ("SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID"), "trello": ("TRELLO_KEY", "TRELLO_TOKEN", "TRELLO_LIST_ID"),
                  "discord": ("DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID")}
    only = set(sys.argv[1:]) or {k for k, keys in configured.items() if all(E.get(x) for x in keys)}  # + "large" / "unlarge"
    log("seeding: " + (", ".join(sorted(only)) or "nothing — fill .env first"))
    try:
        for name, fn in (("privos", seed_privos), ("notion", seed_notion), ("hubspot", seed_hubspot), ("slack", seed_slack), ("trello", seed_trello), ("discord", seed_discord),
                         ("large", seed_large), ("unlarge", unseed_large)):
            if name in only:
                fn()
    finally:
        json.dump(STATE, open(STATE_FILE, "w"), indent=2)
    log("\n== Telegram — post these 3 messages YOURSELF in the test group (a bot never sees its own messages):")
    for m in MESSAGES:
        log("   " + m)
    log("   Telegram keeps updates only 24h → post them within 24h before each benchmark session.")
