#!/usr/bin/env python3
"""Wave 3, stage 1 (groups a and b): capture raw menu lines from each shop's
own website or own online ordering page.

Stage 1 of 2. This script only reads pages and writes wave3_raw.json: one entry
per priced line exactly as the page shows it (section, wording, price, the
page it was read from). It makes no judgement about what an item is. Tagging,
conventions and the CSVs are done by build_wave3.py, and every row is checked
again by verify_wave3.py, which shares no code with this file.

Needs Playwright:  pip install playwright && playwright install chromium
Never touches shops_all.csv, menu_items_all.csv, launches.csv or promos.csv.
"""

import datetime
import html
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

FOLDER = Path(__file__).resolve().parent
OUT = FOLDER / "wave3_raw.json"
TODAY = datetime.date.today().isoformat()
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

PRICE = re.compile(r"^(From )?S?\$\s?([\d,]+(?:\.\d{1,2})?)$")


def get_json(page, url):
    """JSON through the browser's own network stack. The python.org build on
    macOS ships without a CA bundle, so urllib cannot verify these sites."""
    resp = page.request.get(url, timeout=60000)
    assert resp.ok, f"{resp.status} {url}"
    return resp.json()


def render(page, url, steps=()):
    """Visible text of a page once its JavaScript has loaded the prices."""
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    for label in steps:                     # outlet pickers, never a login
        page.get_by_text(label, exact=False).first.click()
        page.wait_for_timeout(3000)
    for _ in range(15):
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(300)
    return [l.strip() for l in page.evaluate("document.body.innerText").splitlines() if l.strip()]


# ------------------------------------------------------------------ Oddle
ODDLE_JS = """() => {
  const out = []; let section = '';
  for (const el of document.querySelectorAll('h1, h2, h3, a')) {
    if (el.tagName !== 'A') { section = el.innerText.trim(); continue; }
    const h4 = [...el.querySelectorAll('h4')].map(h => h.innerText.trim());
    const price = h4.find(t => /^(From )?S\\$[\\d,.]+$/.test(t));
    if (!price || h4.length < 2 || h4[0] === price) continue;
    out.push({section, wording: h4[0], price,
              sold_out: /out of stock|sold out/i.test(el.innerText)});
  }
  return out;
}"""


def oddle(page, shop_id, url):
    """Oddle storefront. Each product is a card: an <a> holding the name in its
    first <h4> and the price in a later <h4>. Section titles are h2/h3."""
    render(page, url)
    rows, seen = [], set()
    for c in reversed(page.evaluate(ODDLE_JS)):     # keep the copy in its real
        m = PRICE.match(c["price"])                 # section, not "Best Seller"
        k = (c["wording"], m.group(2))
        if k in seen:
            continue
        seen.add(k)
        rows.append(dict(shop_id=shop_id, section=c["section"], wording=c["wording"],
                         price=m.group(2).replace(",", ""), from_price=bool(m.group(1)),
                         variant="", in_stock=not c["sold_out"], url=url))
    return rows[::-1]


# ---------------------------------------------------------------- Shopify
def shopify(page, shop_id, base):
    rows = []
    for p in get_json(page, f"{base}/products.json?limit=250")["products"]:
        many = len(p["variants"]) > 1
        for v in p["variants"]:
            url = f"{base}/products/{p['handle']}"
            if many:
                url += f"?variant={v['id']}"
            rows.append(dict(shop_id=shop_id, section=p.get("product_type", ""),
                             wording=p["title"].strip(), price=v["price"], from_price=False,
                             variant=v["title"] if many else "", in_stock=bool(v["available"]),
                             url=url))
    return rows


# ------------------------------------------------------------ WooCommerce
def woo(page, shop_id, base):
    rows = []
    for p in get_json(page, f"{base}/wp-json/wc/store/products?per_page=100"):
        pr = p["prices"]
        rng = pr.get("price_range")
        low = int(rng["min_amount"]) if rng else int(pr["price"])
        high = int(rng["max_amount"]) if rng else low
        rows.append(dict(shop_id=shop_id,
                         section=" / ".join(html.unescape(c["name"]) for c in p["categories"]),
                         wording=html.unescape(p["name"]).strip(), price=f"{low / 100:.2f}",
                         from_price=high != low, range_high=f"{high / 100:.2f}",
                         regular_price=f"{int(pr['regular_price']) / 100:.2f}",
                         variant="", in_stock=bool(p["is_in_stock"]), url=p["permalink"]))
    return rows


# ------------------------------------------------- Lunar Rabbit own store
def lunar_rabbit(page, shop_id):
    """Catalogue only renders after an outlet is picked (no login involved)."""
    url = "https://lunar-rabbit.com/catalog"
    lines = render(page, url, steps=("Pick up", "The Lunar Rabbit Hamilton"))
    nav = next(l for l in lines if l.startswith("Sandwiches "))
    rows, section = [], ""
    for prev, l in zip(lines, lines[1:]):
        if l in nav and len(l) > 3 and not l.startswith("$"):
            section = l
        m = re.match(r"^\$([\d.]+)\b", l)
        if m:
            rows.append(dict(shop_id=shop_id, section=section, wording=prev, price=m.group(1),
                             from_price=False, variant="", in_stock=True, url=url))
    return rows


# -------------------------------------------------------------- Take App
def take_app(page, shop_id, url):
    """take.app storefront: name / [description] / S$price."""
    lines = render(page, url)
    rows = []
    for i, l in enumerate(lines):
        m = re.match(r"^(?:From )?S?\$\s?([\d.]+)$", l)
        if m:
            rows.append(dict(shop_id=shop_id, section="", wording="", price=m.group(1),
                             from_price=l.startswith("From"), variant="", in_stock=True,
                             url=url, context=lines[max(0, i - 4):i]))
    return rows


def main():
    raw = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_context(user_agent=UA, locale="en-SG",
                                   viewport={"width": 1280, "height": 2000}).new_page()
        raw += oddle(page, "flock_cafe", "https://flockcafe.oddle.me/en_SG")
        raw += oddle(page, "cake_inspiration_joo_chiat", "https://cake-inspiration.oddle.me/en_SG")
        raw += oddle(page, "drips_bakery_cafe", "https://dripsbakery.oddle.me/en_SG")
        raw += lunar_rabbit(page, "lunar_rabbit_boulangerie_hamilton")
        raw += take_app(page, "janie_the_bakery", "https://take.app/janiethebakery")
        raw += shopify(page, "caro_patisserie_joo_chiat", "https://caro-patisserie.com")
        raw += shopify(page, "boms_and_buns", "https://bomsnbuns.com")
        raw += shopify(page, "windowsill_pies_horne_rd", "https://windowsillpies.sg")
        raw += woo(page, "la_levain", "https://www.lalevain.com")
        browser.close()

    OUT.write_text(json.dumps({"observed_date": TODAY, "rows": raw}, indent=1, ensure_ascii=False))
    by = {}
    for r in raw:
        by[r["shop_id"]] = by.get(r["shop_id"], 0) + 1
    for k, v in by.items():
        print(f"  {k:<36}{v:>4}")
    print(f"  total raw lines: {len(raw)} -> {OUT.name}")


if __name__ == "__main__":
    main()
