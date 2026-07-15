# Live-capture mode

Upgrades the benchmark from modeled fixtures to **captured evidence**: it hits
the real vendor APIs and records the token size of actual responses.

## What it does

For each platform with credentials present, `src/live_capture.py` performs **one
read-only GET** and tokenizes the real response body:

| Platform | Read-only call | Env required |
|---|---|---|
| Telegram | `getUpdates` | `TELEGRAM_BOT_TOKEN` |
| Notion | retrieve database schema | `NOTION_TOKEN`, `NOTION_DATABASE_ID` |
| HubSpot | list 1 contact | `HUBSPOT_TOKEN` |
| Discord | get channel messages | `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID` |
| Trello | get list cards | `TRELLO_KEY`, `TRELLO_TOKEN`, `TRELLO_LIST_ID` |
| PrivOS | get room messages | `PRIVOS_URL`, `PRIVOS_TOKEN`, `PRIVOS_ROOM_ID` |

Platforms without credentials are **skipped with a reason** — the harness is safe
to run anywhere and, if partially configured, captures what it can.

## Safety

- **Read-only.** Only GET/list requests are issued. Nothing on any remote account
  is created, updated, or deleted.
- **Write shapes stay modeled.** Create/update payloads (the parts that most favor
  the unified stack) remain in `fixtures/` because capturing them live would
  mutate real data. This is conservative: real write responses (e.g. Notion's
  full page echo) are typically *larger* than reads, so the modeled fixtures do
  not overstate the multi-platform cost.
- **Zero third-party deps.** Uses `urllib` from the standard library.

## Run

```bash
cp .env.example .env      # fill only the platforms you want
set -a; source .env; set +a
python src/live_capture.py
```

Writes `results/live_capture.json`:

```json
{
  "mode": "live-capture (read-only)",
  "captured_count": 2,
  "records": [
    {"platform": "notion", "captured": true, "response_tokens": 1180, "response_bytes": 4620, "note": "retrieve database schema"},
    {"platform": "privos", "captured": true, "response_tokens": 96,   "response_bytes": 380,  "note": "get room messages"}
  ]
}
```

## Interpreting results

Compare `response_tokens` for equivalent reads across stacks (e.g. Notion
`retrieve database` vs PrivOS `get room messages`, or HubSpot `list contact` vs a
PrivOS CRM list item). A live capture confirms — or corrects — the modeled
fixture sizes. If captured numbers diverge from the fixtures, update the fixture
payloads to match and re-run `run_benchmark.py`; the modeled scenarios then rest
on measured per-object sizes.

## Status

The credential-absent path is tested (all platforms skip cleanly). The live HTTP
path has **not** been exercised against real APIs in this repo — no credentials
were available at authoring time. Endpoints/headers follow each vendor's current
API docs (`references.md`) but should be smoke-tested once per platform when
credentials are first supplied.
