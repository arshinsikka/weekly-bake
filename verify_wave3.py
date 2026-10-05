#!/usr/bin/env python3
"""Wave 3 verification. Independent of extract_wave3.py and build_wave3.py:
it imports nothing from them and reads only the CSVs they produced.

Layer 1  Every row of menu_items_wave3.csv: open its source_url in headless
         Chromium, let the page finish loading, and check that the item wording
         and the price both appear in the visible text, close together.
         -> verification_wave3.csv  (passed / failed / needs_review + reason)
Layer 2  review_wave3.csv: every failed or needs_review row, plus every row
         tagged to one of the 9 compared items whose price is more than 40%
         from that item's current median in menu_items_all.csv, even if it
         passed. Failed and outlier web rows get a screenshot in
         review_screenshots/.
Layer 3  sample_wave3_stage2.csv: 30 passed rows from group c and 30 from
         group d, picked with a fixed random seed, for checking by hand.
         (sample_wave3.csv is the stage 1 sample, already hand-checked; it is
         left as it is.)
Shops    Every new or changed shop: address and postal code appear on the
         source page, the page does not say the shop is closed, and an outlet
         count is recorded with its source.

Run with --resume to keep earlier "passed" results for rows whose price and
source have not changed, and only open pages for the rest.

Needs Playwright:  pip install playwright && playwright install chromium
Reads menu_items_all.csv and shops_all.csv; writes to neither.
"""

import asyncio
import csv
import random
import sys
import re
import statistics
import unicodedata
from collections import defaultdict
from pathlib import Path

from playwright.async_api import async_playwright

FOLDER = Path(__file__).resolve().parent
SHOTS = FOLDER / "review_screenshots"
SEED = 20261004
SAMPLE_SIZE = 30
OUTLIER_LIMIT = 0.40
NEAR = 600            # characters allowed between the wording and its price
PARALLEL = 8
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

NINE = ["croissant_plain", "flat_white", "latte", "matcha_latte", "cappuccino",
        "americano", "espresso", "croissant_filled", "kouign_amann"]
BASIS_PREFERENCE = ["takeaway", "dine_in", "online_order"]
IMAGE = re.compile(r"\.(jpe?g|png|webp|gif|pdf)(\?|$)", re.I)

# Pages that only show their catalogue after an outlet is chosen. This is a
# click-through, not a login: nothing behind a login is ever opened.
CLICK_THROUGH = {
    "https://lunar-rabbit.com/catalog": ["Pick up", "The Lunar Rabbit Hamilton"],
}
CLOSED = re.compile(r"permanently closed|closed for now|currently closed|has closed down|"
                    r"we have closed", re.I)


def read(name):
    with open(FOLDER / name, newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        return r.fieldnames[:], list(r)


def write(name, fields, rows):
    with open(FOLDER / name, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


_reg = FOLDER / "wave3_parts/_discovery/nea_eating_establishments.csv"
REGISTER_POSTALS = set(re.findall(r"SINGAPORE (\d{6})", _reg.read_text(errors="replace"))) \
    if _reg.exists() else set()


def norm(text):
    """Lower-case, straight quotes, plain dashes, single spaces."""
    t = unicodedata.normalize("NFKC", text or "")
    t = t.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"',
                                   "–": "-", "—": "-", " ": " "}))
    return re.sub(r"\s+", " ", t).strip().casefold()


def price_pattern(price):
    """4.80 matches '$4.80', 'S$4.80', '4.80'. 16.00 also matches '$16'."""
    whole, cents = f"{float(price):.2f}".split(".")
    whole = re.sub(r"(?<=\d)(?=(\d{3})+$)", ",?", whole)
    tail = r"(?:\.00)?" if cents == "00" else rf"\.{cents}"
    return re.compile(rf"(?<![\d.,]){whole}{tail}(?![\d])(?!\.\d)")


def row_key(r):
    """shop + wording + size + hot/iced + date, plus the menu section where a
    shop lists the same item twice (the section is then in price_note)."""
    sec = re.search(r"section: ([^;]+)", r["price_note"])
    return "|".join([r["shop_id"], r["menu_wording"], r["size"], r["hot_or_iced"],
                     r["observed_date"]] + ([sec.group(1)] if sec else []))


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:80]


# ------------------------------------------------------------------ browser
async def load(context, url):
    """Return (page, visible text, http status). Caller closes the page."""
    page = await context.new_page()
    resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        await page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass                                  # some storefronts never go idle
    for label in CLICK_THROUGH.get(url, []):
        await page.get_by_text(label, exact=False).first.click()
        await page.wait_for_timeout(3000)
    for _ in range(15):                       # trigger lazy-loaded sections
        await page.mouse.wheel(0, 3000)
        await page.wait_for_timeout(300)
    for _ in range(40):                       # paged catalogues
        more = page.get_by_text(re.compile(r"^(load|show) more$", re.I))
        if not await more.count() or not await more.first.is_visible():
            break
        await more.first.click()
        await page.wait_for_timeout(1500)
    text = await page.evaluate("document.body ? document.body.innerText : ''")
    return page, text, (resp.status if resp else None)


def judge(text, wording, price):
    """passed only when the wording is on the page with the price near it."""
    t, w, p = norm(text), norm(wording), price_pattern(price)
    spots = [m.start() for m in re.finditer(re.escape(w), t)]
    has_price = bool(p.search(t))
    if not spots and not has_price:
        return "failed", "neither the wording nor the price is on the page"
    if not spots:
        return "failed", "price is on the page but the wording is not"
    if not has_price:
        return "failed", "wording is on the page but the price is not"
    for s in spots:
        if p.search(t[s:s + len(w) + NEAR]):
            return "passed", "wording on page with the price next to it"
    return "needs_review", "wording and price are both on the page but not next to each other"


async def check_url(context, sem, url, rows, results, shot_wanted):
    async with sem:
        page = None
        try:
            page, text, status = await load(context, url)
            blocked = "not allowed to visit this page" in text
            for r in rows:
                if blocked:
                    verdict = ("needs_review", "the site blocked automated access; not re-checked")
                elif status and status >= 400:
                    verdict = ("failed", f"page returned HTTP {status}")
                else:
                    verdict = judge(text, r["menu_wording"], r["price_sgd"])
                results[row_key(r)] = verdict
            if any(results[row_key(r)][0] == "failed" or row_key(r) in shot_wanted
                   for r in rows):
                path = SHOTS / f"{slug(url)}.png"
                try:
                    await page.screenshot(path=str(path), full_page=True, timeout=30000)
                except Exception:
                    await page.screenshot(path=str(path), timeout=30000)
                for r in rows:
                    r["_screenshot"] = str(path.relative_to(FOLDER))
        except Exception as e:
            for r in rows:
                results[row_key(r)] = ("failed", f"page did not load: {str(e).splitlines()[0][:120]}")
        finally:
            if page:
                await page.close()


async def check_shop(context, sem, shop, src, out):
    """Address, postal code and closure, read from the page the address came from."""
    sid = shop["shop_id"]

    def rec(check, status, reason):
        out.append(dict(row_key=f"SHOP|{sid}|{check}", shop_id=sid, check=check, status=status,
                        reason=reason, source_url=src))

    oc, ocs = shop["outlet_count"].strip(), shop["outlet_count_source"].strip()
    if oc.isdigit() and ocs.startswith("http"):
        rec("outlet_count", "passed", f"outlet_count {oc}, source {ocs}")
    else:
        rec("outlet_count", "needs_review",
            f"outlet_count '{oc}' has no traceable source ('{ocs}')")
    if "data.gov.sg" in src:
        # address came from the NEA licensed eating establishments register
        hit = shop["postal_code"] in REGISTER_POSTALS
        rec("address", "passed" if hit else "failed",
            "postal code is in the NEA register download" if hit else
            "postal code is not in the NEA register download")
        rec("not_closed", "passed" if hit else "needs_review",
            "holds a current NEA licence at this address" if hit else "not found in the register")
        return
    if not src:
        rec("address", "needs_review", "no page was opened for this shop in wave 3")
        rec("not_closed", "needs_review", "no page was opened for this shop in wave 3")
        return
    async with sem:
        page = None
        try:
            page, text, status = await load(context, src)
            t = norm(text)
            postal = shop["postal_code"].strip()
            m = re.match(r"\s*(\d+[a-z]?)\s+(.+?)(?:\s+#|$)", shop["address"], re.I)
            number, street = (m.group(1), norm(m.group(2))) if m else ("", norm(shop["address"]))
            street_short = re.sub(r"\b(road|rd)\b", "r", street)
            t_short = re.sub(r"\b(road|rd)\b", "r", t)
            has_postal = bool(postal) and postal in t
            has_street = bool(number) and number in t and street_short.split(" #")[0] in t_short
            if has_postal and has_street:
                rec("address", "passed", f"'{shop['address']}' and {postal} both on the page")
            elif has_postal or has_street:
                rec("address", "needs_review",
                    f"postal on page: {has_postal}; street on page: {has_street}")
            else:
                rec("address", "failed", "neither the address nor the postal code is on the page")
            hit = CLOSED.search(text)
            if hit:
                rec("not_closed", "needs_review", f"page says '{hit.group(0)}'")
            else:
                rec("not_closed", "passed", "page loads and does not say the shop is closed")
        except Exception as e:
            why = f"page did not load: {str(e).splitlines()[0][:120]}"
            rec("address", "failed", why)
            rec("not_closed", "needs_review", why)
        finally:
            if page:
                await page.close()


# ------------------------------------------------------------------ medians
def current_medians():
    """Median per item the way the site computes it: one price per shop,
    takeaway > dine_in > online_order, cheapest within the winning basis."""
    _, menu = read("menu_items_all.csv")
    by = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for r in menu:
        if r["item_canonical"] in NINE and r["price_sgd"].strip():
            by[r["item_canonical"]][r["shop_id"]][r["price_basis"]].append(float(r["price_sgd"]))
    med = {}
    for item, shops in by.items():
        picks = []
        for bases in shops.values():
            basis = next((b for b in BASIS_PREFERENCE if bases.get(b)), sorted(bases)[0])
            picks.append(min(bases[basis]))
        med[item] = statistics.median(picks)
    return med


async def main():
    SHOTS.mkdir(exist_ok=True)
    _, menu = read("menu_items_wave3.csv")
    _, shops = read("shops_wave3.csv")
    _, sources = read("shop_sources_wave3.csv")
    _, old_shops = read("shops_all.csv")
    old = {s["shop_id"]: s for s in old_shops}
    medians = current_medians()

    outliers = {}
    for r in menu:
        item = r["item_canonical"]
        if item in medians:
            gap = (float(r["price_sgd"]) - medians[item]) / medians[item]
            if abs(gap) > OUTLIER_LIMIT:
                outliers[row_key(r)] = (f"{gap:+.0%} from the current {item} median of "
                                        f"${medians[item]:.2f}")

    group_of = {s["shop_id"]: s["group"] for s in sources}
    earlier = {}
    if "--resume" in sys.argv and (FOLDER / "verification_wave3.csv").exists():
        for v in read("verification_wave3.csv")[1]:
            if v["check"] == "price" and v["status"] == "passed":
                earlier[(v["row_key"], v["source_url"], v["expected_price"])] = v["reason"]
                shop, wording = v["row_key"].split("|")[:2]
                earlier[(shop, wording, v["source_url"], v["expected_price"])] = v["reason"]

    results, shop_results = {}, []
    by_url = defaultdict(list)
    for r in menu:
        url = r["source_url"]
        if (row_key(r), url, r["price_sgd"]) in earlier:
            results[row_key(r)] = ("passed", earlier[(row_key(r), url, r["price_sgd"])])
        elif (r["shop_id"], r["menu_wording"], url, r["price_sgd"]) in earlier:
            # same shop, wording, page and price as a row that passed earlier
            results[row_key(r)] = ("passed", "passed in an earlier run: " + earlier[
                (r["shop_id"], r["menu_wording"], url, r["price_sgd"])])
        elif not url.startswith("http"):
            results[row_key(r)] = ("needs_review", "team counter photo; a person must read it")
        elif IMAGE.search(url) or "drive.google.com" in url or "inside a menu image" in r["price_note"]:
            results[row_key(r)] = ("needs_review",
                                   "price is inside an image or PDF; text cannot be matched")
        else:
            by_url[url].append(r)

    core = ("shop_name", "district", "address", "postal_code", "outlet_count")
    changed = [s for s in shops if s["shop_id"] not in old
               or any(s[k] != old[s["shop_id"]][k] for k in core)]
    src_of = {s["shop_id"]: s["address_source_url"] for s in sources}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(user_agent=UA, locale="en-SG",
                                            viewport={"width": 1280, "height": 2000})
        sem = asyncio.Semaphore(PARALLEL)
        await asyncio.gather(
            *[check_url(context, sem, u, rs, results, outliers) for u, rs in by_url.items()],
            *[check_shop(context, sem, s, src_of.get(s["shop_id"], ""), shop_results)
              for s in changed])
        await browser.close()

    # ---- layer 1
    fields = ["row_key", "shop_id", "check", "status", "reason", "source_url", "expected_price"]
    ver = [dict(row_key=row_key(r), shop_id=r["shop_id"], check="price",
                status=results[row_key(r)][0], reason=results[row_key(r)][1],
                source_url=r["source_url"], expected_price=r["price_sgd"]) for r in menu]
    write("verification_wave3.csv", fields, ver + sorted(shop_results, key=lambda x: x["row_key"]))

    # ---- layer 2
    review = []
    for r in menu:
        status, reason = results[row_key(r)]
        gst = "page states prices exclude GST" if r["gst_inclusive"] == "no" else ""
        if status != "passed" or row_key(r) in outliers or gst:
            review.append(dict(row_key=row_key(r), shop_id=r["shop_id"], check="price",
                               item_canonical=r["item_canonical"], menu_wording=r["menu_wording"],
                               expected_price=r["price_sgd"], status=status, reason=reason,
                               outlier=outliers.get(row_key(r), ""), gst_flag=gst,
                               source_url=r["source_url"],
                               screenshot=r.get("_screenshot", "")))
    review += [dict(s, item_canonical="", menu_wording="", expected_price="", outlier="",
                    gst_flag="", screenshot="") for s in shop_results if s["status"] != "passed"]
    write("review_wave3.csv",
          ["row_key", "shop_id", "check", "item_canonical", "menu_wording", "expected_price",
           "status", "reason", "outlier", "gst_flag", "source_url", "screenshot"], review)

    # ---- layer 3
    rng, sample = random.Random(SEED), []
    for grp in ("c", "d"):
        pool = [r for r in menu if results[row_key(r)][0] == "passed"
                and group_of.get(r["shop_id"]) == grp]
        sample += [dict(group=grp, row_key=row_key(r), shop_id=r["shop_id"],
                        menu_wording=r["menu_wording"], size=r["size"],
                        expected_price=r["price_sgd"], source_url=r["source_url"],
                        checked_by_hand="")
                   for r in rng.sample(pool, min(SAMPLE_SIZE, len(pool)))]
    write("sample_wave3_stage2.csv",
          ["group", "row_key", "shop_id", "menu_wording", "size", "expected_price", "source_url",
           "checked_by_hand"], sample)

    # ---- summary
    def count(rows):
        c = defaultdict(int)
        for x in rows:
            c[x["status"]] += 1
        return dict(c)

    print(f"  price rows : {len(ver)}  {count(ver)}")
    print(f"  pages opened for prices: {len(by_url)}")
    print(f"  shop checks: {len(shop_results)}  {count(shop_results)}  ({len(changed)} new or changed shops)")
    print(f"  outliers (> {OUTLIER_LIMIT:.0%} from current median): {len(outliers)}")
    print(f"  review_wave3.csv rows : {len(review)}")
    print(f"  sample_wave3_stage2.csv rows : {len(sample)}  (seed {SEED})")
    print(f"  kept from an earlier run (--resume): {len(earlier)}")
    print(f"  medians used: { {k: round(v, 2) for k, v in medians.items()} }")
    by_shop = defaultdict(lambda: defaultdict(int))
    for v in ver:
        by_shop[v["shop_id"]][v["status"]] += 1
    for s, c in by_shop.items():
        if set(c) != {"passed"}:
            print(f"     {s:<40}{dict(c)}")


if __name__ == "__main__":
    asyncio.run(main())
