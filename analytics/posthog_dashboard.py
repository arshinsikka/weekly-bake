"""Builds the "Counter: pitch view" dashboard in PostHog from the events the site and app already send.

Run once, after the site has POSTHOG_KEY set:
    export POSTHOG_PERSONAL_API_KEY=phx_...   # PostHog > Settings > Personal API keys (scopes: insight:write, dashboard:write)
    export POSTHOG_HOST=https://us.posthog.com # or https://eu.posthog.com
    python3 analytics/posthog_dashboard.py

The key is read from the environment only. Never commit it.
"""
import json
import os
import sys
import urllib.request

KEY = os.environ.get("POSTHOG_PERSONAL_API_KEY")
HOST = os.environ.get("POSTHOG_HOST", "https://us.posthog.com").rstrip("/")
PROJECT = os.environ.get("POSTHOG_PROJECT_ID", "@current")
if not KEY:
    sys.exit("Set POSTHOG_PERSONAL_API_KEY first.")

# the app shown inside the landing page's laptop says from=demo; owners' real use is everything else
REAL = [{"type": "event", "key": "from", "operator": "is_not", "value": ["demo"]}]
LAST_30 = {"date_from": "-30d"}


def api(path, body):
    req = urllib.request.Request(f"{HOST}/api/projects/{PROJECT}/{path}", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def ev(name, **extra):
    return {"kind": "EventsNode", "event": name, "name": name, **extra}


def funnel(steps, props=None):
    return {"kind": "FunnelsQuery", "series": [ev(s) for s in steps], "dateRange": LAST_30,
            "properties": props or [], "funnelsFilter": {"funnelWindowInterval": 1, "funnelWindowIntervalUnit": "day"}}


def trend(series, interval="day", display="ActionsLineGraph", breakdown=None, props=None):
    q = {"kind": "TrendsQuery", "series": series, "interval": interval, "dateRange": LAST_30,
         "properties": props or [], "trendsFilter": {"display": display}}
    if breakdown:
        q["breakdownFilter"] = {"breakdown": breakdown, "breakdown_type": "event"}
    return q


INSIGHTS = [
    ("Owner activation: open to signed up",
     "Of the cafe owners who open Counter, how many find their shop, pick competitors, try a price and ask for alerts.",
     funnel(["app_opened", "shop_picked", "competitors_done", "price_tried", "alerts_signup"], REAL)),
    ("Landing: visit to waitlist",
     "Landing visitors who joined the S$20 waitlist.",
     funnel(["page_view", "waitlist_submitted"])),
    ("Landing: visit to trying Counter",
     "Landing visitors who clicked Try Counter.",
     funnel(["page_view", "landing_cta_clicked"])),
    ("Weekly active cafes",
     "Distinct cafes that used Counter each week, not counting the landing page demo.",
     trend([ev("shop_picked", math="hogql", math_hogql="count(distinct properties.shop_id)")], "week", props=REAL)),
    ("Waitlist and alert sign-ups",
     "Total people who asked to pay or to be told about price changes.",
     trend([ev("waitlist_submitted"), ev("alerts_signup")], display="BoldNumber")),
    ("Prices tried",
     "Times an owner tried a new price before changing it.",
     trend([ev("price_tried")], display="BoldNumber", props=REAL)),
    ("Visitors per day",
     "Unique visitors to the landing page and the app.",
     trend([ev("$pageview", math="dau")], breakdown="surface")),
    ("Where visits come from",
     "Our own link tags (outreach, newsletter, ...) and referring sites.",
     trend([ev("page_view")], display="ActionsBarValue", breakdown="src")),
]

dash = api("dashboards/", {"name": "Counter: pitch view",
                           "description": "Activation, demand and growth, from the events the site and app send."})
for name, desc, query in INSIGHTS:
    api("insights/", {"name": name, "description": desc, "dashboards": [dash["id"]],
                      "query": {"kind": "InsightVizNode", "source": query}})
    print("added", name)
print(f"Done: {HOST}/dashboard/{dash['id']}")
