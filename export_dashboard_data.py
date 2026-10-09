#!/usr/bin/env python3
"""Export the visit list for the dashboard.

Reads the events and signups tables from Supabase and writes
dashboard/dashboard_data.js as:  window.DASHBOARD_DATA = {...};

One small record per visit, plus sign-up counts. No email, shop name, shop id,
area name, referrer text or IP address is ever written to the file.

Run:  python3 export_dashboard_data.py
Needs SUPABASE_URL and SUPABASE_SERVICE_KEY in .env (python-dotenv).
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DASH = ROOT / "dashboard"
OUT = DASH / "dashboard_data.js"
MARKERS = DASH / "markers.json"

SGT = timezone(timedelta(hours=8))          # days are Singapore days
SITE_HOSTS = {"theweeklybake.online", "www.theweeklybake.online"}
PAGE = 1000
DATA_VERSION = 2

# the landing page's sections, top to bottom. Older rows used other names
# (board, number, numbers, strip); those are ignored.
LANDING_SECTIONS = ["hero", "price_line", "how", "quotes", "full_version", "questions", "closing"]
LOOKUP_EVENTS = {"shop_picked", "area_picked", "not_listed_picked", "rank_result"}


# ---------- reading ----------

def load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        # same job without the package: KEY=value lines, nothing printed
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        sys.exit("SUPABASE_URL and SUPABASE_SERVICE_KEY must both be set in .env")
    return url, key


def get_page(base, key, table, query):
    req = urllib.request.Request(
        base + "/rest/v1/" + table + "?" + urllib.parse.urlencode(query),
        headers={"apikey": key, "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.load(res)


def fetch_all(base, key, table, select):
    """Every row of a table, 1000 at a time, oldest first."""
    rows, offset, order = [], 0, "created_at.asc"
    while True:
        query = {"select": select, "limit": PAGE, "offset": offset}
        if order:
            query["order"] = order
        try:
            batch = get_page(base, key, table, query)
        except urllib.error.HTTPError as err:
            if err.code == 400 and order and offset == 0:
                order = None        # no created_at column to sort on
                continue
            body = err.read().decode("utf-8", "replace")[:300]
            sys.exit(f"Could not read {table}: HTTP {err.code} {body}")
        except urllib.error.URLError as err:
            sys.exit(f"Could not reach Supabase: {err.reason}")
        rows += batch
        if len(batch) < PAGE:
            return rows
        offset += PAGE


def row_day(row):
    """The Singapore day a row was written, as YYYY-MM-DD, or None."""
    for col in ("created_at", "inserted_at", "timestamp", "ts"):
        raw = row.get(col)
        if raw:
            try:
                when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                return None
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return when.astimezone(SGT).date().isoformat()
    return None


def read_markers():
    if not MARKERS.exists():
        return []
    out = []
    for m in json.loads(MARKERS.read_text()):
        if m.get("date") and m.get("label"):
            out.append({"date": str(m["date"]), "label": str(m["label"])})
    return sorted(out, key=lambda m: m["date"])



# ---------- counting ----------

def source_group(src):
    """Where a visit came from, read off the src tag only."""
    src = str(src or "").strip().lower()
    if not src:
        return "direct"
    if src == "team":
        return "team"
    if "news" in src:
        return "newsletter"
    if "thank" in src or src in ("ty", "email", "reply"):
        return "thanks"
    return "other"


def as_props(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def visit_record(day, events):
    """One visit from one session's (name, props) pairs, oldest first.

    Returns None when the session is not a visit to the site: no page_view,
    or a page_view from some other host.
    """
    pv = next((p for n, p in events if n == "page_view"), None)
    if pv is None:
        return None
    host = str(pv.get("host") or "").lower()
    if host and host not in SITE_HOSTS:
        return None
    names = {n for n, _ in events}

    entry = "none"
    for name, props in events:
        if name == "shop_picked":
            entry = "board" if props.get("from") == "board" else "box"
        elif name == "area_picked":
            entry = "area"
        elif name == "not_listed_picked":
            entry = "not_listed"
        else:
            continue
        break
    if entry == "none" and any(n == "preview_opened" and p.get("via") == "hash" for n, p in events):
        entry = "link"

    seen = {p.get("section") for n, p in events if n == "landing_section_seen"}
    secs = None
    for name, props in events:
        v = props.get("seconds_visible")
        if name == "visit_end" and isinstance(v, (int, float)) and not isinstance(v, bool) \
                and 0 <= v <= 86400:
            secs = max(secs or 0, int(v))

    source = source_group(pv.get("src"))
    return {
        "day": day,
        "source": source,
        "team": source == "team",
        "entry": entry,
        "scrolled": any(s in LANDING_SECTIONS and s != "hero" for s in seen),
        "looked_up": bool(names & LOOKUP_EVENTS),
        "saw_offer": "wall_seen" in names,
        "tapped_join": "lock_clicked" in names or any(
            n == "wall_clicked" and p.get("target") == "waitlist" for n, p in events),
        "joined": "waitlist_submitted" in names,
        "emailed": "newsletter_submitted" in names,
        "sections": [s for s in LANDING_SECTIONS if s in seen],
        "used": {
            "map": bool(names & {"map_opened_full", "pin_opened"}),
            "menu_link": "menu_link_clicked" in names,
            "locked_item": "lock_clicked" in names,
            "area_filter": "area_filter_used" in names,
            "menu_sent": "menu_uploaded" in names,
        },
        "seconds": secs,
    }


def build(events, signups, markers):
    """Turn raw rows into the visit list and sign-up counts the dashboard reads."""
    sessions, event_counts = {}, Counter()
    for e in events:
        name = e.get("name")
        if not name:
            continue
        event_counts[str(name)] += 1
        day, sid = row_day(e), e.get("session_id")
        if not day or not sid:
            continue
        s = sessions.setdefault(sid, {"day": day, "events": []})
        s["day"] = min(s["day"], day)
        s["events"].append((str(e.get("created_at") or ""), str(name), as_props(e.get("props"))))

    visits = []
    for s in sessions.values():
        s["events"].sort(key=lambda ev: ev[0])      # stable, so ties keep table order
        rec = visit_record(s["day"], [(n, p) for _, n, p in s["events"]])
        if rec:
            visits.append(rec)
    visits.sort(key=lambda v: v["day"])

    waitlist = sum(1 for r in signups if r.get("source") == "waitlist")
    return {
        "data_version": DATA_VERSION,
        "generated_at": datetime.now(SGT).strftime("%Y-%m-%d %H:%M"),
        "sample": False,
        "markers": markers,
        "totals": {"free_signups": len(signups) - waitlist, "waitlist_signups": waitlist},
        "visits": visits,
    }, event_counts


def write(data):
    text = json.dumps(data, separators=(",", ":"))
    # last line of defence: an address or a web link in here means a leak
    for bad in ("@", "http://", "https://"):
        if bad in text:
            sys.exit(f"Refusing to write: found {bad!r} in the output. Nothing was saved.")
    DASH.mkdir(exist_ok=True)
    OUT.write_text("window.DASHBOARD_DATA = " + text + ";\n")


def main():
    base, key = load_env()
    events = fetch_all(base, key, "events", "name,props,session_id,created_at")
    # only the columns that are counted: the address stays behind
    signups = fetch_all(base, key, "signups", "source,created_at")
    data, event_counts = build(events, signups, read_markers())
    write(data)

    v, t = data["visits"], data["totals"]
    count = lambda k: sum(1 for x in v if x[k])
    print(f"Read {len(events)} events, {len(signups)} signups.")
    print(f"Visits {len(v)}, team visits {count('team')}, looked up {count('looked_up')}, "
          f"saw offer {count('saw_offer')}, joined {count('joined')}.")
    print(f"Free sign-ups {t['free_signups']}, waitlist {t['waitlist_signups']}.")
    print("Events in the table:")
    for name, n in sorted(event_counts.items()):
        print(f"  {name[:28]:28} {n:6}")
    print(f"Wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
