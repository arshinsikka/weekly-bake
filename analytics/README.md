# PostHog for Counter

## What it records
| Where | What | Why it matters in a pitch |
|---|---|---|
| Landing page | Every existing event (sections seen, scroll depth, Try clicks, café searches, waitlist), plus **seconds spent on each section** | Which part of the story sells Counter |
| App | Every existing event (shop picked, competitors, prices tried, alerts sign-up), with the café attached | Activation and **weekly active cafés** |
| Landing → app | One anonymous id passed in the link (`?ph=`) | One funnel from first visit to sign-up |
| Both | Session replays (anything typed is hidden), heatmaps, rage and dead clicks, JS errors, page speed | Show real owners using it |
| Menu reader (server) | Each AI call: model, tokens, cost, speed, failure | "Reading a menu costs us $0.000X" |
| App survey | Sean Ellis PMF question after an owner tries a price | The "% very disappointed" number YC asks about |

Nothing is kept in the browser: no cookies, no storage. A visitor is new on each visit, so retention is counted **by café** (`shop_id`), which is the customer anyway. Visits from the demo inside the landing page are tagged `from=demo` and left out of owner numbers.

## Turn it on (once)
The project key is already in `site/index.html`, `counter/index.html` and `counter-landing/index.html` (PostHog US, project 662311), so tracking starts as soon as this is deployed.

1. Optional, for AI cost tracking: in Vercel, on the app's project (`counter/`), add the environment variable `POSTHOG_KEY` with the same `phc_` key.
2. In PostHog > Settings, turn on **Session replay**, **Heatmaps** and **Error tracking**. In **Authorized URLs**, add `https://countr.site`, `https://app.countr.site` and `https://demoweeklybake.vercel.app`, so heatmaps can be opened on the live pages.
3. Build the dashboard and the survey with a personal API key (scopes `insight:write`, `dashboard:write`, `survey:write`), kept out of files:
   `POSTHOG_PERSONAL_API_KEY=phx_... POSTHOG_HOST=https://us.posthog.com python3 analytics/posthog_dashboard.py --launch-survey`

## In the room
1. **Traction first**: "Cafés using Counter, per week", and "Demand: waitlist and alert sign-ups".
2. **Retention**: "Cafés that came back", listing cafés by name that used it on more than one day.
3. **Activation**: "Owner activation in the app". Name the biggest drop-off and what you are changing.
4. **PMF**: the survey's share of "Very disappointed". Above 40% is the usual bar.
5. **Proof**: play a 30-second replay of a real owner finding their café and trying a price.
6. **Unit cost**: "AI menu reads: cost". A menu costs fractions of a cent to read.

Only quote numbers that are on the dashboard that day.
