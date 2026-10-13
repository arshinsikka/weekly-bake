"""Sets PostHog up for Counter in one run: the "Counter: pitch view" dashboard and the in-app PMF survey,
built from the events the site, the app and the menu reader already send.

Run once, after the site has POSTHOG_KEY set:
    export POSTHOG_PERSONAL_API_KEY=phx_...   # PostHog > Settings > Personal API keys
                                              # scopes: insight:write, dashboard:write, survey:write
    export POSTHOG_HOST=https://us.posthog.com # or https://eu.posthog.com
    python3 analytics/posthog_dashboard.py

The key is read from the environment only. Never commit it.
"""
import datetime
import json
import os
import sys
import urllib.error
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
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"PostHog said {e.code} for {path}: {e.read().decode()[:500]}")


def ev(name, **extra):
    return {"kind": "EventsNode", "event": name, "name": name, **extra}


def funnel(steps, props=None, days=1):
    return {"kind": "FunnelsQuery", "series": [ev(s) for s in steps], "dateRange": LAST_30,
            "properties": props or [],
            "funnelsFilter": {"funnelWindowInterval": days, "funnelWindowIntervalUnit": "day"}}


def trend(series, interval="day", display="ActionsLineGraph", breakdown=None, props=None):
    q = {"kind": "TrendsQuery", "series": series, "interval": interval, "dateRange": LAST_30,
         "properties": props or [], "trendsFilter": {"display": display}}
    if breakdown:
        q["breakdownFilter"] = {"breakdown": breakdown, "breakdown_type": "event"}
    return q


def viz(source):
    return {"kind": "InsightVizNode", "source": source}


def table(sql):
    return {"kind": "DataTableNode", "source": {"kind": "HogQLQuery", "query": sql}}


INSIGHTS = [
    # traction: the numbers a partner asks for first
    ("Cafes using Counter, per week",
     "Distinct cafes that opened their prices in Counter each week. The landing page demo is not counted.",
     viz(trend([ev("shop_picked", math="hogql", math_hogql="count(distinct properties.shop_id)")], "week", props=REAL))),
    ("Cafes that came back",
     "Cafes seen on two or more different days: retention, counted by cafe rather than by browser.",
     table("""select properties.shop_id as cafe, count(distinct toDate(timestamp)) as days_used,
              min(timestamp) as first_seen, max(timestamp) as last_seen
              from events
              where event = 'shop_picked' and properties.shop_id is not null and properties.from != 'demo'
              group by cafe having days_used >= 2 order by days_used desc limit 100""")),
    ("Demand: waitlist and alert sign-ups",
     "People who asked to pay S$20 a month, and owners who asked to be told when a competitor changes a price.",
     viz(trend([ev("waitlist_submitted"), ev("alerts_signup")], display="BoldNumber"))),

    # activation: where people drop out
    ("Whole journey: landing to alerts",
     "One visitor from the landing page into the app, finding their cafe, trying a price and signing up.",
     viz(funnel(["page_view", "app_opened", "shop_picked", "competitors_done", "price_tried", "alerts_signup"], days=1))),
    ("Owner activation in the app",
     "Of everyone who opens the app: found their shop, picked competitors, tried a price, asked for alerts.",
     viz(funnel(["app_opened", "shop_picked", "competitors_done", "price_tried", "alerts_signup"], REAL))),
    ("Landing: visit to waitlist",
     "Landing visitors who joined the S$20 waitlist.",
     viz(funnel(["page_view", "waitlist_submitted"]))),

    # engagement: what people actually look at
    ("Time on each landing section",
     "Average seconds each section was the main thing on screen.",
     viz(trend([ev("section_time", math="avg", math_property="seconds")], display="ActionsBarValue", breakdown="section"))),
    ("How far down the landing page people get",
     "Visitors who saw each section.",
     viz(trend([ev("landing_section_seen", math="dau")], display="ActionsBarValue", breakdown="section"))),
    ("Prices tried",
     "Times an owner tried a new price before changing it.",
     viz(trend([ev("price_tried")], display="BoldNumber", props=REAL))),

    # growth: where visits come from
    ("Visitors per day",
     "Unique visitors to the landing page and to the app.",
     viz(trend([ev("$pageview", math="dau")], breakdown="surface"))),
    ("Where visits come from",
     "Our own link tags (outreach, newsletter, ...) on landing visits.",
     viz(trend([ev("page_view")], display="ActionsBarValue", breakdown="src"))),

    # the machine: AI cost and reliability
    ("AI menu reads: cost",
     "What reading menus with AI has cost, in US dollars.",
     viz(trend([ev("$ai_generation", math="sum", math_property="$ai_total_cost_usd")], display="BoldNumber"))),
    ("AI menu reads: speed and failures",
     "Average seconds per AI call, and calls that failed.",
     viz(trend([ev("$ai_generation", math="avg", math_property="$ai_latency"),
                ev("$ai_generation", custom_name="Failed calls",
                   properties=[{"type": "event", "key": "$ai_is_error", "operator": "exact", "value": ["true"]}])]))),
    ("Errors visitors hit",
     "JavaScript errors on the landing page and in the app.",
     viz(trend([ev("$exception")], breakdown="surface"))),
]

# Sean Ellis's product-market-fit question, asked in the app right after an owner tries a new price
SURVEY = {
    "name": "PMF: how disappointed without Counter?",
    "type": "popover",
    "questions": [
        {"type": "single_choice", "question": "How would you feel if you could no longer use Counter?",
         "choices": ["Very disappointed", "Somewhat disappointed", "Not disappointed"]},
        {"type": "open", "question": "What is the main thing Counter helps you with?", "optional": True},
    ],
    "conditions": {"url": "app.countr.site", "events": {"values": [{"name": "price_tried"}]}},
    "appearance": {"backgroundColor": "#FFFCF5", "submitButtonColor": "#2B1A10", "position": "right",
                   "thankYouMessageHeader": "Thank you. Our team reads every answer."},
}

dash = api("dashboards/", {"name": "Counter: pitch view",
                           "description": "Traction, activation, engagement, growth and AI cost, from the events the site, app and menu reader send."})
for name, desc, query in INSIGHTS:
    api("insights/", {"name": name, "description": desc, "dashboards": [dash["id"]], "query": query})
    print("added", name)

if "--launch-survey" in sys.argv:
    SURVEY["start_date"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
survey = api("surveys/", SURVEY)
print("survey", survey["name"], "launched" if SURVEY.get("start_date") else "saved as a draft (run with --launch-survey, or launch it in PostHog)")
print(f"Dashboard: {HOST}/dashboard/{dash['id']}")
