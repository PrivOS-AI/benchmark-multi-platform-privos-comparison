#!/usr/bin/env python3
"""Copy result JSON for publication: redact identifiers, replace private skill docs by a hash, then verify.

  python3 publish.py results/ OUT_DIR [--batch BATCH]
  python3 publish.py --check OUT_DIR          # verify only (exit 1 on any known leak pattern; not a guarantee)

Token counts (`usage`) are never touched; transcript text is redacted, so it differs from what the model saw.
"""
import argparse, glob, hashlib, json, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# Anything matching these must not survive into a published file.
LEAK_PATTERNS = {
    "uuid": r"\b[0-9a-f]{8}(?:-?[0-9a-f]{4}){1,3}-?[0-9a-f]{12}\b|\b[0-9a-f]{32}\b",
    "mongo_id": r"\b[0-9a-f]{24}\b",
    "uuid_fragment": r"\b[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}\b",  # middle of a UUID whose ends were already replaced
    "meteor_id": r"\b(?=[A-Za-z0-9]{17}\b)(?=[^\W_]*\d)(?=[^\W_]*[a-z])(?=[^\W_]*[A-Z])[A-Za-z0-9]{17}\b"
                 # digit-less ids: Meteor's alphabet, >=4 upper and >=4 lower (camelCase names like associationTypeId have fewer)
                 r"|\b(?=[2-9A-HJ-NP-TW-Za-km-z]{17}\b)(?=(?:[a-z0-9]*[A-Z]){4})(?=(?:[A-Z0-9]*[a-z]){4})[A-Za-z0-9]{17}\b",
    "slack_id": r"\b[UBATCWG]0[A-Z0-9]{8,10}\b",
    "long_number": r"(?<![\d.:-])-?\d{8,}(?![\d.])",
    "room_slug": r"group/[a-z0-9-]+",
    "email": r"[\w.+-]+@(?!(?:[\w-]+\.)*example\.com\b)(?:[\w-]+\.)+[A-Za-z]{2,}\b",  # letter TLD: not pkg@1.2.3
    "ip": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",  # any IPv4 (private and public)
    "local_path": r"/(?:Users|home)/[^\s\"']+",
    # credentials (the run never logs them; this is a safety net)
    "telegram_token": r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b",
    "notion_token": r"\b(?:secret|ntn)_[A-Za-z0-9]{30,}\b",
    "hubspot_token": r"\bpat-[a-z]{2}\d-[0-9a-f]{8}-[0-9a-f-]{27}\b",
    "slack_token": r"\bxox[abposr]-[A-Za-z0-9-]{10,}",
    "api_key": r"\b(?:sk|pk)-[A-Za-z0-9_-]{20,}",
    "bearer": r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}",
    "privos_host": r"[\w-]+\.privos\.(?:io|ai)",
}
# hex pieces right after a placeholder = the rest of a partly quoted id ("<env-3>-766b-8…", "<env-3> 766b 8076")
TAIL_RE = r"(<[a-z_]+-\d+>)(?:-[0-9a-f]{1,12}\b|[ |]{1,3}(?=[0-9a-f]*\d)[0-9a-f]{4,12}\b)+"
SECRET_KEY_RE = re.compile(r"TOKEN|KEY|SECRET|PASS|_ID$|URL$")  # .env entries treated as secret/identifying
# Only the model endpoint is public; every other URL (hub, workspaces) is redacted.
PUBLIC_ENV = {"ZAI_BASE_URL", "ZAI_MODEL"}
# Fields that must survive redaction verbatim (they document the harness, not the user).
KEEP_FIELDS = ("model", "endpoint")
SENSITIVE_KEYS = {"username", "first_name", "last_name", "author", "name_of_user", "real_name", "display_name"}


def env_values():
    vals = set()
    for f in (os.path.join(ROOT, ".env"),):
        if os.path.exists(f):
            for line in open(f):
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.split("   #")[0].strip()
                    if len(v) >= 6 and SECRET_KEY_RE.search(k) and k not in PUBLIC_ENV:
                        vals.add(v)
    st = os.path.join(ROOT, "seed_state.json")
    if os.path.exists(st):
        def walk(x):
            if isinstance(x, dict):
                for v in x.values(): yield from walk(v)
            elif isinstance(x, list):
                for v in x: yield from walk(v)
            elif isinstance(x, str) and len(x) >= 8 and re.fullmatch(r"[\w.:/-]+", x): yield x  # ids, not seed text
        vals |= set(walk(json.load(open(st))))
    return vals


# Person-like keys; their values are collected from the raw text (also inside escaped, nested JSON strings)
# and then replaced wherever they appear — e.g. a username that also shows up as a display "name".
PERSON_KEY_RE = re.compile(r'\\*"(?:username|first_name|last_name|author|real_name|display_name|user_name)\\*"\s*:\s*\\*"([^"\\]{2,64})\\*"')
# "name" is too generic to redact everywhere (task names), but next to a username/author it is a person's display name.
_SEP = r'\\*"\s*,(?:\\+n|\s)*\\*"'
_VAL = r'\\*"\s*:\s*\\*"([^"\\]{2,64})'
USER_NAME_RE = re.compile(r'(?:username|author)' + _VAL + _SEP + r'name' + _VAL + r'|\\*"name' + _VAL + _SEP + r'(?:username|author)\\*"')


class Redactor:
    def __init__(self, extra):
        self.map, self.extra = {}, sorted(extra, key=len, reverse=True)

    def collect_people(self, raw):
        found = set(PERSON_KEY_RE.findall(raw)) | {v for m in USER_NAME_RE.findall(raw) for v in m[1:] if v}
        # Models sometimes quote IDs in pieces ("1a2b3c4d | 5e6f | ..."): also redact every 8+ hex fragment of any id.
        ids = set(re.findall(LEAK_PATTERNS["uuid"], raw)) | set(re.findall(LEAK_PATTERNS["mongo_id"], raw)) | \
            {v for v in self.extra if re.fullmatch(r"[0-9a-f-]{24,}", v)}
        for i in ids:
            h = i.replace("-", "")
            # whole id first (dashed + undashed; longest-first replacement), then the pieces models quote separately
            found |= {i, h} | {s for s in i.split("-") if len(s) >= 8} | {h[:8], h[-12:]}
        self.extra = sorted(set(self.extra) | found, key=len, reverse=True)

    def tag(self, kind, value):
        key = (kind, value)
        if key not in self.map:
            self.map[key] = f"<{kind}-{sum(1 for k in self.map if k[0] == kind) + 1}>"
        return self.map[key]

    def text(self, s):
        for v in self.extra:
            if v in s:
                s = s.replace(v, self.tag("env", v))
        for kind, pat in LEAK_PATTERNS.items():
            s = re.sub(pat, lambda m, k=kind: self.tag(k, m.group(0)), s)
        return re.sub(TAIL_RE, r"\1", s)  # "<env-3>-766b-8…": the rest of a partly quoted id goes too

    def walk(self, x, key=None):
        if isinstance(x, dict):
            keep = lambda k: k in ("usage", "mcp_servers") or (key is None and k in KEEP_FIELDS)  # top-level model/endpoint
            return {k: (v if keep(k) else self.walk(v, k)) for k, v in x.items()}
        if isinstance(x, list):
            return [self.walk(v, key) for v in x]
        if isinstance(x, str):
            if key in SENSITIVE_KEYS:
                return self.tag("name", x)
            return self.text(x)
        if isinstance(x, int) and not isinstance(x, bool) and abs(x) >= 10 ** 8:
            return self.tag("long_number", str(x))
        return x


def strip_skill_docs(r, sandbox_commit):
    # The PrivOS skill docs (SKILL.md) live in a private repo: publish their hash, not their text.
    for m in r.get("transcript", []):
        c = m.get("content")
        if m.get("role") == "system" and isinstance(c, str) and "=== SKILL " in c:
            head, docs = c.split("You have these skills", 1)
            m["content"] = head + json.dumps({"skill_docs_omitted": True, "sha256": hashlib.sha256(docs.encode()).hexdigest(),
                                              "chars": len(docs), "source": f"privos-sandbox@{sandbox_commit}"})
    return r


def check(folder):
    bad = 0
    for f in sorted(glob.glob(os.path.join(folder, "**", "*"), recursive=True)):
        if not os.path.isfile(f) or not f.endswith((".json", ".md")):
            continue
        t = open(f, errors="ignore").read()
        tails = len(re.findall(TAIL_RE, t))
        t = re.sub(r"<[a-z_]+-\d+>", "", t)  # our own placeholders
        hits = {k: len(re.findall(p, t)) for k, p in LEAK_PATTERNS.items()}
        hits.update({"env_value": sum(v in t for v in env_values())})
        hits.update({"person_field": len(PERSON_KEY_RE.findall(t)) + len(USER_NAME_RE.findall(t))})
        hits.update({"skill_doc": t.count("=== SKILL "), "id_tail": tails})
        if f.endswith(".json"):
            r = json.loads(open(f).read())
            kept = [r.get(k) for k in KEEP_FIELDS] + list(((r.get("harness") or {}).get("mcp_servers") or {}).values())
            hits.update({"harness_redacted": sum(bool(re.search(r"<[a-z_]+-\d+>", str(v))) for v in kept)})
        hits = {k: v for k, v in hits.items() if v}
        if hits:
            bad += 1
            print(f"LEAK {os.path.relpath(f, folder)}: {hits}")
    print("scrub check:", "FAILED" if bad else "clean", f"({bad} files with leaks)" if bad else "")
    return bad == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?")
    ap.add_argument("out")
    ap.add_argument("--batch")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        sys.exit(0 if check(a.out) else 1)
    red = Redactor(env_values())
    os.makedirs(a.out, exist_ok=True)
    n = 0
    for f in sorted(glob.glob(os.path.join(a.src, "agent_run_*.json"))):
        if a.batch and not f.endswith(f"_{a.batch}.json"):
            continue
        r = json.load(open(f))
        r = strip_skill_docs(r, (r.get("harness") or {}).get("privos_sandbox_commit") or "unknown")
        red.collect_people(json.dumps(r))
        json.dump(red.walk(r), open(os.path.join(a.out, os.path.basename(f)), "w"), indent=1, ensure_ascii=False)
        n += 1
    print(f"published {n} files to {a.out}")
    sys.exit(0 if check(a.out) else 1)


if __name__ == "__main__":
    main()
