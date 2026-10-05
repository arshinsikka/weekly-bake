#!/usr/bin/env python3
"""Export aggregate numbers for the class dashboard.

Reads the events, signups and menu_uploads tables from Supabase and writes
dashboard/dashboard_data.js as:  window.DASHBOARD_DATA = {...};

Only counts leave this script. No email, shop name, shop id, searched text,
typed price or IP address is ever written to the file.

Run:  python3 export_dashboard_data.py
Needs SUPABASE_URL and SUPABASE_SERVICE_KEY in .env (python-dotenv).
"""
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DASH = ROOT / "dashboard"
OUT = DASH / "dashboard_data.js"
MARKERS = DASH / "markers.json"
FIELDWORK = DASH / "fieldwork.csv"

SGT = timezone(timedelta(hours=8))          # days are Singapore days
LAUNCH = "2026-09-20"
SITE_HOSTS = {"theweeklybake.online", "www.theweeklybake.online"}
PAGE = 1000

# The day each event went into the page. Used when the data has no earlier
# sighting of it; an earlier sighting in the data always wins.
TRACKING_START = {
    "visit_end": "2026-10-05",
    "source_device": "2026-10-05",
    "menu_link_clicked": "2026-10-05",
    "item_selected": "2026-10-05",
    "teaser_clicked": "2026-10-05",
    "menu_uploaded": "2026-10-05",
    "pin_opened": "2026-10-03",
    "list_view_toggled": "2026-10-03",
}

FEATURES = ["menu_link_clicked", "item_selected", "pin_opened",
            "list_view_toggled", "teaser_clicked"]
RANK_EVENTS = {"rank_result", "rank_finder_result"}
# sent by the page on its own, so they do not show the visitor did anything
PASSIVE = {"page_view", "visit_end", "time_on_page"}
SOURCES = ["Newsletter", "Thank-you email", "WhatsApp", "Search", "Direct", "Other", "Not tracked"]
DEVICES = ["phone", "tablet", "desktop"]
SECTIONS = ["map", "prices", "waitlist"]
ITEM_NAMES = {
    "flat_white": "Flat white", "latte": "Latte", "cappuccino": "Cappuccino",
    "americano": "Americano", "espresso": "Espresso", "matcha_latte": "Matcha latte",
    "croissant_plain": "Croissant", "croissant_filled": "Filled croissant",
    "kouign_amann": "Kouign-amann",
}
SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
FIELD_COLS = ["teammate", "district", "shops_visited", "conversations", "signups",
              "mailing_lists_joined", "menu_photos", "wtp_yes", "wtp_no"]


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


def read_fieldwork():
    if not FIELDWORK.exists():
        return []
    rows = []
    with FIELDWORK.open(newline="") as f:
        for r in csv.DictReader(f):
            if not (r.get("teammate") or "").strip():
                continue
            row = {"teammate": r["teammate"].strip(), "district": (r.get("district") or "").strip()}
            for col in FIELD_COLS[2:]:
                try:
                    row[col] = int(float((r.get(col) or "0").strip() or 0))
                except ValueError:
                    row[col] = 0
            rows.append(row)
    return rows


# ---------- counting ----------

def source_group(props):
    if not any(k in props for k in ("src", "ref", "device", "host")):
        return "Not tracked"
    src = (props.get("src") or "").lower()
    ref = (props.get("ref") or "").lower()
    if src:
        if "news" in src:
            return "Newsletter"
        if "thank" in src or src in ("ty", "email", "reply"):
            return "Thank-you email"
        if src in ("wa", "whatsapp") or "whatsapp" in src:
            return "WhatsApp"
        return "Other"
    if not ref or ref in SITE_HOSTS:
        return "Direct"
    if "whatsapp" in ref or ref == "wa.me":
        return "WhatsApp"
    if any(s in ref for s in ("google.", "bing.", "duckduckgo.", "yahoo.", "ecosia.", "brave.")):
        return "Search"
    return "Other"


def earliest(*days):
    days = [d for d in days if d]
    return min(days) if days else None


def build(events, signups, uploads, markers, fieldwork, today=None):
    """Turn raw rows into the aggregate-only structure the dashboard reads."""
    today = today or datetime.now(SGT).date().isoformat()

    # one record per session
    sessions, first_seen, no_session = {}, {}, 0
    # event names and prop names as found in the table (names only, never values):
    # the quickest way to see that an event is not arriving
    event_names = {}
    for e in events:
        day = row_day(e)
        name = e.get("name")
        if not day or not name:
            continue
        props = e.get("props") or {}
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except ValueError:
                props = {}
        if not isinstance(props, dict):
            props = {}
        first_seen[name] = earliest(first_seen.get(name), day)
        if SAFE_NAME.match(str(name)):
            seen = event_names.setdefault(name, {"rows": 0, "first": day, "last": day, "props": set()})
            seen["rows"] += 1
            seen["last"] = max(seen["last"], day)
            seen["props"] |= {k for k in props if SAFE_NAME.match(str(k))}
        if name == "page_view" and "device" in props:
            first_seen["source_device"] = earliest(first_seen.get("source_device"), day)
        sid = e.get("session_id")
        if not sid:
            no_session += 1
            continue
        s = sessions.setdefault(sid, {"day": day, "pv": None, "events": []})
        s["day"] = min(s["day"], day)
        if name == "page_view" and s["pv"] is None:
            s["pv"] = props
        s["events"].append((name, props))

    all_days = [LAUNCH, today] + [s["day"] for s in sessions.values()]
    all_days += [d for d in map(row_day, signups + uploads) if d]
    start, end = date.fromisoformat(min(all_days)), date.fromisoformat(max(all_days))
    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    at = {d: i for i, d in enumerate(days)}
    n = len(days)
    zeros = lambda: [0] * n

    since = {
        "visits": earliest(first_seen.get("page_view")) or LAUNCH,
        "time": earliest(first_seen.get("visit_end"), TRACKING_START["visit_end"]),
        "price_checks": earliest(*[first_seen.get(k) for k in RANK_EVENTS]),
        "sources": earliest(first_seen.get("source_device"), TRACKING_START["source_device"]),
        "device": earliest(first_seen.get("source_device"), TRACKING_START["source_device"]),
        "sections": earliest(first_seen.get("section_seen"), first_seen.get("scroll_depth")),
        "items": earliest(first_seen.get("item_selected"), *[first_seen.get(k) for k in RANK_EVENTS]),
        "signups": earliest(*[row_day(r) for r in signups if r.get("source") != "waitlist"]),
        "waitlist": earliest(*[row_day(r) for r in signups if r.get("source") == "waitlist"]),
        "menus": earliest(*[row_day(r) for r in uploads], TRACKING_START["menu_uploaded"]),
    }
    for k in FEATURES:
        since[k] = earliest(first_seen.get(k), TRACKING_START.get(k))
    sections_exact = "section_seen" in first_seen

    daily = {
        "visits": zeros(), "counter_demos": zeros(), "bounces": zeros(), "price_checks": zeros(),
        "signups": zeros(), "waitlist": zeros(), "menus": zeros(),
        "seconds": [[] for _ in range(n)],
        "sources": {k: zeros() for k in SOURCES},
        "device": {k: zeros() for k in DEVICES}, "device_base": zeros(),
        "sections": {k: zeros() for k in SECTIONS}, "sections_base": zeros(),
        "features": {k: {"events": zeros(), "sessions": zeros()} for k in FEATURES},
        "items": {},
    }
    left_out = {"other_host": 0, "no_page_view": 0, "events_without_session": no_session}

    for s in sessions.values():
        i, pv = at[s["day"]], s["pv"]
        if pv is None:
            left_out["no_page_view"] += 1
            continue
        host = (pv.get("host") or "").lower()
        if host and host not in SITE_HOSTS:
            left_out["other_host"] += 1
            continue
        if (pv.get("src") or "").lower() == "team":
            daily["counter_demos"][i] += 1
            continue

        names = {name for name, _ in s["events"]}
        daily["visits"][i] += 1
        if names <= PASSIVE:
            daily["bounces"][i] += 1
        if names & RANK_EVENTS:
            daily["price_checks"][i] += 1
        daily["sources"][source_group(pv)][i] += 1
        if pv.get("device") in DEVICES:
            daily["device"][pv["device"]][i] += 1
            daily["device_base"][i] += 1

        secs, depth, seen, items = None, 0, set(), set()
        for name, props in s["events"]:
            if name == "visit_end":
                v = props.get("seconds_visible")
                if isinstance(v, (int, float)) and 0 <= v <= 86400:
                    secs = max(secs or 0, int(v))
            elif name == "scroll_depth":
                v = props.get("percent")
                if isinstance(v, (int, float)):
                    depth = max(depth, v)
            elif name == "section_seen" and props.get("section") in SECTIONS:
                seen.add(props["section"])
            if name in daily["features"]:
                daily["features"][name]["events"][i] += 1
            if name == "item_selected" or name in RANK_EVENTS:
                if props.get("item") in ITEM_NAMES:
                    items.add(props["item"])
        if secs is not None:
            daily["seconds"][i].append(secs)
        for name in names & set(FEATURES):
            daily["features"][name]["sessions"][i] += 1
        for item in items:
            daily["items"].setdefault(item, zeros())[i] += 1

        if not sections_exact:
            # the page has no "section seen" event, so this is read off what
            # the visitor did: taps on the map, and how far down they scrolled
            if names & {"pin_opened", "list_view_toggled"}:
                seen.add("map")
            if depth >= 50 or names & {"table_sorted", "rank_finder_to_table"} or \
               any(nm == "tab_changed" and "tab" in p for nm, p in s["events"]):
                seen.add("prices")
            if depth >= 75 or names & {"teaser_clicked", "waitlist_submitted"}:
                seen.add("waitlist")
        if since["sections"] and s["day"] >= since["sections"]:
            daily["sections_base"][i] += 1
            for k in seen:
                daily["sections"][k][i] += 1

    for r in signups:
        d = row_day(r)
        if d in at:
            daily["waitlist" if r.get("source") == "waitlist" else "signups"][at[d]] += 1
    for r in uploads:
        d = row_day(r)
        if d in at:
            daily["menus"][at[d]] += 1
    for row in daily["seconds"]:
        row.sort()

    redesign = next((m["date"] for m in markers if "redesign" in m["label"].lower()), "2026-10-03")
    return {
        "generated_at": datetime.now(SGT).strftime("%Y-%m-%d %H:%M"),
        "sample": False,
        "launch": LAUNCH,
        "range": {"from": days[0], "to": days[-1]},
        "map_redesign": redesign,
        "days": days,
        "markers": markers,
        "since": since,
        "sections_exact": sections_exact,
        "daily": daily,
        "item_names": ITEM_NAMES,
        "fieldwork": fieldwork,
        "left_out": left_out,
        "event_names": {k: dict(v, props=sorted(v["props"])) for k, v in sorted(event_names.items())},
    }


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
    # only the columns that are counted: the address and shop name stay behind
    signups = fetch_all(base, key, "signups", "source,created_at")
    uploads = fetch_all(base, key, "menu_uploads", "created_at")
    data = build(events, signups, uploads, read_markers(), read_fieldwork())
    write(data)

    d = data["daily"]
    print(f"Read {len(events)} events, {len(signups)} signups, {len(uploads)} menu uploads.")
    print(f"Days {data['range']['from']} to {data['range']['to']}.")
    print(f"Visits {sum(d['visits'])}, price checks {sum(d['price_checks'])}, "
          f"newsletter {sum(d['signups'])}, waitlist {sum(d['waitlist'])}, "
          f"menus {sum(d['menus'])}, counter demos {sum(d['counter_demos'])}.")
    print(f"Left out: {data['left_out']}")
    print("Events in the table:")
    for name, v in data["event_names"].items():
        print(f"  {name:24} {v['rows']:5}  {v['first']} to {v['last']}  props: {', '.join(v['props'])}")
    print(f"Wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
