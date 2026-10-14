#!/usr/bin/env python3
"""Copy the analytics history from Supabase into PostHog, so PostHog shows visits from 20 Sept.

Reads the events and signups tables the same way export_dashboard_data.py does (SUPABASE_URL and
SUPABASE_SERVICE_KEY from .env, nothing printed) and sends every row from before PostHog started
to PostHog's batch endpoint (US cloud, the project key in site/index.html).

  event       = the row's name; page_view becomes $pageview
  distinct_id = the row's session_id
  timestamp   = the row's created_at
  properties  = the row's props, plus source: "supabase_import"; page views also get $current_url
Each real signup becomes one waitlist_submitted or newsletter_submitted event at its created_at,
with no email in it.

Skipped:
  - everything from PostHog's first event on (2026-10-13 19:26:07 Singapore time, or --before): it is there already
  - waitlist_submitted and newsletter_submitted rows of the events table: each sign-up comes once, from the signups table
  - test traffic: any event whose host is localhost, 127.0.0.1 or a vercel.app address other than the four
    that were real sites (REAL_VERCEL), and every event of a session with such a page view
  - connectivity_check rows, any row with "@example.com" in it, and every event of a session whose sign-up used an
    @example.com address (its *_submitted event within a minute of the sign-up)

Run:  python3 analytics/import_history_to_posthog.py            (dry run: counts, 3 payloads, sends nothing)
      python3 analytics/import_history_to_posthog.py --send     (sends)
"""
import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from export_dashboard_data import load_env, fetch_all, as_props  # noqa: E402

SGT = timezone(timedelta(hours=8))
# PostHog's first event: everything from here on is in PostHog already
DEFAULT_BEFORE = datetime(2026, 10, 13, 19, 26, 7, tzinfo=SGT)
POSTHOG_BATCH = "https://us.i.posthog.com/batch/"
BATCH = 500
TEST_DOMAIN = "@example.com"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "[::1]"}
SIGNUP_EVENTS = {"waitlist_submitted", "newsletter_submitted"}
# vercel.app addresses that were real, public sites; every other vercel.app address is a per-deploy preview
REAL_VERCEL = {"weekly-bake.vercel.app", "counter-psi-khaki.vercel.app", "demoweeklybake.vercel.app", "counter-cafe-v2.vercel.app"}
# a fixed namespace, so a row always gets the same uuid and a second run cannot double it
NS = uuid.UUID("6f1d2c3a-5b4e-4c7d-9a8b-0e1f2a3b4c5d")


def when(raw):
    t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def project_key():
    """The PostHog project key the landing page already uses. Never printed."""
    text = (ROOT / "site" / "index.html").read_text()
    m = re.search(r'POSTHOG_KEY\s*=\s*"(phc_[A-Za-z0-9]+)"', text)
    if not m:
        sys.exit("No PostHog project key found in site/index.html")
    return m.group(1)


def test_host(host):
    """localhost, 127.0.0.1 and per-deploy preview links on vercel.app are our own testing."""
    h = str(host or "").strip().lower().split(":")[0]
    return h in LOCAL_HOSTS or (h.endswith("vercel.app") and h not in REAL_VERCEL)


def test_sessions(events, test_signups):
    """Sessions whose *_submitted event is within a minute of an @example.com sign-up."""
    out = set()
    for s in test_signups:
        t = when(s["created_at"])
        for e in events:
            if e["name"].endswith("_submitted") and abs((when(e["created_at"]) - t).total_seconds()) <= 60:
                out.add(e["session_id"])
    return out


def event_payload(e, seen):
    props = dict(as_props(e.get("props")))
    name = "$pageview" if e["name"] == "page_view" else e["name"]
    if name == "$pageview" and props.get("host"):
        h = str(props["host"])
        props["$current_url"] = ("http://" if h.split(":")[0] in LOCAL_HOSTS else "https://") + h + "/"
    props.update({"source": "supabase_import", "$process_person_profile": False})
    base = f"{e['name']}|{e['session_id']}|{e['created_at']}|{json.dumps(props, sort_keys=True)}"
    seen[base] += 1          # two identical rows stay two events
    return {"event": name, "distinct_id": str(e["session_id"]), "timestamp": when(e["created_at"]).isoformat(),
            "uuid": str(uuid.uuid5(NS, base + f"|{seen[base]}")), "properties": props}


def signup_payload(s, seen):
    source = str(s.get("source") or "")
    kind = source.split(" ")[0]          # "counter_app shop=..." -> counter_app
    name = "waitlist_submitted" if kind == "waitlist" else "newsletter_submitted"
    base = f"signup|{source}|{s['created_at']}"
    seen[base] += 1
    sid = "signup-" + hashlib.sha256(base.encode()).hexdigest()[:16]
    return {"event": name, "distinct_id": sid, "timestamp": when(s["created_at"]).isoformat(),
            "uuid": str(uuid.uuid5(NS, base + f"|{seen[base]}")),
            "properties": {"signup_source": kind, "from_table": "signups", "source": "supabase_import",
                           "$process_person_profile": False}}


def short(p):
    q = json.loads(json.dumps(p))
    q["distinct_id"] = q["distinct_id"][:6] + "..."
    q["uuid"] = q["uuid"][:6] + "..."
    return q


def send(batch, key):
    body = json.dumps({"api_key": key, "historical_migration": True, "batch": batch}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(POSTHOG_BATCH, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as res:
                if res.status == 200:
                    return
        except urllib.error.HTTPError as err:
            if err.code < 500:
                sys.exit(f"PostHog refused a batch: HTTP {err.code} {err.read().decode('utf-8', 'replace')[:200]}")
        except urllib.error.URLError:
            pass
        time.sleep(2 * (attempt + 1))
    sys.exit("PostHog did not take a batch after 3 tries. Rows already sent keep their uuids, so a rerun does not double them.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--before", help="PostHog's first event, e.g. 2026-10-13T19:26:07+08:00 (the default)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="send", action="store_false", default=False, help="the default: count and show 3 payloads, send nothing")
    mode.add_argument("--send", dest="send", action="store_true", help="send to PostHog")
    args = ap.parse_args()

    base, key = load_env()
    events = fetch_all(base, key, "events", "name,props,session_id,created_at")
    # the address is read only to drop test sign-ups; it never leaves this function's rows
    signups = fetch_all(base, key, "signups", "email,source,created_at")

    cut, why = (when(args.before), "from --before") if args.before else (DEFAULT_BEFORE, "default")
    print(f"Import rows before: {cut.astimezone(SGT):%Y-%m-%d %H:%M:%S} Singapore time ({why})")

    test_signups = [s for s in signups if str(s.get("email") or "").lower().endswith(TEST_DOMAIN)]
    test_sids = test_sessions(events, test_signups)
    # sessions with a page view on a test host: all of their events are testing
    test_host_sids = {e["session_id"] for e in events
                      if e.get("name") == "page_view" and test_host(as_props(e.get("props")).get("host"))}

    # each session's host, from its first page view; the app's events have none
    session_host = {}
    for e in events:
        if e.get("name") == "page_view":
            session_host.setdefault(e["session_id"], str(as_props(e.get("props")).get("host") or "").strip().lower().split(":")[0])

    skipped, payloads, seen = Counter(), [], Counter()
    by_host = {}             # host -> Counter of kept, test traffic, other skips
    for e in events:
        if not e.get("name") or not e.get("session_id"):
            why = "no name or session"
        elif when(e["created_at"]) >= cut:
            why = "after PostHog start"
        elif e["name"] in SIGNUP_EVENTS:
            why = "sign-up event in the events table"
        elif test_host(as_props(e.get("props")).get("host")):
            why = "test host (localhost, 127.0.0.1, preview)"
        elif e["session_id"] in test_host_sids:
            why = "session with a test-host page view"
        elif e["name"] == "connectivity_check":
            why = "connectivity_check"
        elif TEST_DOMAIN in json.dumps(e.get("props") or {}).lower():
            why = "example.com in props"
        elif e["session_id"] in test_sids:
            why = "session of an example.com sign-up"
        else:
            why = None
            payloads.append(event_payload(e, seen))
        if why:
            skipped[why] += 1
        host = session_host.get(e.get("session_id")) or "(no page view: the app)"
        col = "kept" if not why else "test" if why.startswith(("test host", "session with a test-host")) else "other"
        by_host.setdefault(host, Counter())[col] += 1
    for s in signups:
        if s in test_signups:
            skipped["sign-up with an example.com address"] += 1
        elif when(s["created_at"]) >= cut:
            skipped["sign-up after PostHog start"] += 1
        else:
            payloads.append(signup_payload(s, seen))
    payloads.sort(key=lambda p: p["timestamp"])

    counts = Counter(p["event"] for p in payloads)
    from_signups = Counter(p["event"] for p in payloads if p["properties"].get("from_table") == "signups")
    print(f"Read {len(events)} events and {len(signups)} sign-ups. To send: {len(payloads)} in {-(-len(payloads) // BATCH)} batches of up to {BATCH}.")
    if payloads:
        print(f"Date range: {when(payloads[0]['timestamp']).astimezone(SGT):%Y-%m-%d %H:%M} to "
              f"{when(payloads[-1]['timestamp']).astimezone(SGT):%Y-%m-%d %H:%M} Singapore time")
    print("Per event name:")
    for name, n in sorted(counts.items()):
        extra = f"  ({from_signups[name]} from the signups table)" if from_signups[name] else ""
        print(f"  {name[:28]:28} {n:6}{extra}")
    print("Skipped:")
    for why_, n in sorted(skipped.items()):
        print(f"  {why_:40} {n:6}")

    print("Events table rows per host (kept, skipped as test traffic, skipped for another reason):")
    for host, c in sorted(by_host.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  {host[:52]:52} {c['kept']:6} {c['test']:6} {c['other']:6}")

    if not args.send:
        print("First 3 payloads (ids shortened):")
        for p in payloads[:3]:
            print("  " + json.dumps(short(p)))
        print("Dry run: nothing was sent. Add --send to send.")
        return

    ph = project_key()
    for i in range(0, len(payloads), BATCH):
        send(payloads[i:i + BATCH], ph)
        print(f"  sent {min(i + BATCH, len(payloads))} of {len(payloads)}")
    print("Done.")


if __name__ == "__main__":
    main()
