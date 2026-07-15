"""Live-capture harness: measure REAL API response sizes when credentials exist.

Purpose: upgrade the benchmark from modeled fixtures to captured evidence. For
each platform, if the required environment credentials are present, this performs
ONE read-only GET, tokenizes the real response, and records its token size. When
credentials are absent, the platform is skipped with a reason — so the harness is
safe to run anywhere and never mutates remote data.

Read-only by design: only GET/list operations are issued. Write shapes
(create/update) stay modeled in fixtures because capturing them would mutate real
accounts; documented in docs/live-capture.md.

Zero third-party deps: uses urllib from the standard library.
"""
import json
import os
import urllib.request
import urllib.error

from token_counter import count_text

# Each recipe: env vars it needs, and a builder(env) -> (url, headers) for a
# benign read-only request. `note` documents what is fetched.
RECIPES = {
    "telegram": {
        "env": ["TELEGRAM_BOT_TOKEN"],
        "note": "getUpdates (recent bot updates)",
        "build": lambda e: (
            f"https://api.telegram.org/bot{e['TELEGRAM_BOT_TOKEN']}/getUpdates?limit=5",
            {},
        ),
    },
    "notion": {
        "env": ["NOTION_TOKEN", "NOTION_DATABASE_ID"],
        "note": "retrieve database schema",
        "build": lambda e: (
            f"https://api.notion.com/v1/databases/{e['NOTION_DATABASE_ID']}",
            {"Authorization": f"Bearer {e['NOTION_TOKEN']}", "Notion-Version": "2022-06-28"},
        ),
    },
    "hubspot": {
        "env": ["HUBSPOT_TOKEN"],
        "note": "list contacts (1 record, default properties)",
        "build": lambda e: (
            "https://api.hubapi.com/crm/v3/objects/contacts?limit=1",
            {"Authorization": f"Bearer {e['HUBSPOT_TOKEN']}"},
        ),
    },
    "discord": {
        "env": ["DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID"],
        "note": "get channel messages (5 recent)",
        "build": lambda e: (
            f"https://discord.com/api/v10/channels/{e['DISCORD_CHANNEL_ID']}/messages?limit=5",
            {"Authorization": f"Bot {e['DISCORD_BOT_TOKEN']}"},
        ),
    },
    "trello": {
        "env": ["TRELLO_KEY", "TRELLO_TOKEN", "TRELLO_LIST_ID"],
        "note": "get cards in a list",
        "build": lambda e: (
            f"https://api.trello.com/1/lists/{e['TRELLO_LIST_ID']}/cards"
            f"?key={e['TRELLO_KEY']}&token={e['TRELLO_TOKEN']}",
            {},
        ),
    },
    "privos": {
        "env": ["PRIVOS_URL", "PRIVOS_TOKEN", "PRIVOS_ROOM_ID"],
        "note": "get room messages",
        "build": lambda e: (
            f"{e.get('PRIVOS_URL', '').rstrip('/')}/api/v1/rooms/{e.get('PRIVOS_ROOM_ID')}/messages?limit=10",
            {"Authorization": f"Bearer {e['PRIVOS_TOKEN']}"},
        ),
    },
}


def _fetch(url: str, headers: dict, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def capture_platform(name: str, recipe: dict) -> dict:
    """Attempt one read-only capture; return a record with tokens or a skip reason."""
    missing = [v for v in recipe["env"] if not os.environ.get(v)]
    if missing:
        return {"platform": name, "captured": False,
                "reason": f"missing env: {', '.join(missing)}", "note": recipe["note"]}
    env = {v: os.environ[v] for v in recipe["env"]}
    try:
        url, headers = recipe["build"](env)
        body = _fetch(url, headers)
        return {"platform": name, "captured": True, "note": recipe["note"],
                "response_tokens": count_text(body), "response_bytes": len(body.encode("utf-8"))}
    except urllib.error.HTTPError as e:
        return {"platform": name, "captured": False, "reason": f"HTTP {e.code}", "note": recipe["note"]}
    except Exception as e:  # network/timeout/parse
        return {"platform": name, "captured": False, "reason": f"{type(e).__name__}: {e}", "note": recipe["note"]}


def run() -> dict:
    records = [capture_platform(n, r) for n, r in RECIPES.items()]
    captured = [r for r in records if r["captured"]]
    return {
        "mode": "live-capture (read-only)",
        "captured_count": len(captured),
        "total_platforms": len(records),
        "records": records,
    }


if __name__ == "__main__":
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    result = run()
    out = pathlib.Path(__file__).parent.parent / "results" / "live_capture.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nWritten to {out}")
