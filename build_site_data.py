#!/usr/bin/env python3
"""Apply the pain-suisse format fix and build site_data.json from the merged CSVs.

Shops and menu items come from the merged shops_all.csv / menu_items_all.csv.
Both files are append-only: the last row for a shop_id is the shop as it is
now, and the rows with the latest observed_date for a shop and item are its
current price. Older rows stay in the file as history and are never counted.
Coordinates come from shops_geo.csv and shops_geo_wave3.csv (wave 3 wins).
Original scraped CSVs are never touched.
"""

import csv
import datetime
import json
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
TODAY = datetime.date.today().isoformat()


# --------------------------------------------------- venue / chain classification
# Hotel F&B is out of scope: a neighbourhood cafe does not compete with a hotel
# afternoon tea. Caught three ways, because name alone misses the ones that do
# not say "hotel" — Crossroads sits in the Marriott, L'Espresso in Goodwood Park
# and Royale in the Mercure Bugis.
HOTEL_DOMAINS = {"carltonhotel.sg", "goodwoodparkhotel.com", "marinabaysands.com"}
HOTEL_BRANDS = {"crossroads cafe", "l'espresso", "royale", "origin + bloom"}

def is_hotel(name, source_url, source_type=""):
    """Shared by promos and launches: same venue, same rule, either file."""
    n = (name or "").strip().lower()
    domain = re.sub(r"^https?://(www\.)?", "", source_url or "").split("/")[0].lower()
    return ("hotel" in n
            or domain in HOTEL_DOMAINS
            or n in HOTEL_BRANDS
            or (source_type or "").strip().lower() == "hotel website")

def is_hotel_promo(row):
    return is_hotel(row["brand_name"], row["source_url"], row["source_type"])

# Chains price differently from a four-shop operator, so the page filters them
# out of launches by default.
CHAIN_BRANDS = {
    "starbucks", "breadtalk", "toast box", "chateraise", "délifrance", "delifrance",
    "cedele", "beard papa's", "tous les jours", "polar puffs & cakes", "gokoku",
    "gokoku japanese bakery", "old chang kee", "auntie anne's", "luckin coffee",
    "the coffee bean & tea leaf", "tai cheong bakery", "cat & the fiddle",
}
BIG_OUTLET_MIN = 10

def brand_key(name):
    return re.sub(r"\s*\(.*?\)", "", name).strip().lower()

BASIS_PREFERENCE = ["takeaway", "dine_in", "online_order"]
# menu_found values for a shop that is kept on file but takes no part in prices
NOT_TRADING = {"closed", "temporarily_closed", "removed"}

ITEMS = [
    ("croissant_plain", "Croissant, plain"),
    ("flat_white", "Flat white"),
    ("latte", "Latte"),
    ("matcha_latte", "Matcha latte"),
    ("cappuccino", "Cappuccino"),
    ("americano", "Americano / long black"),
    ("espresso", "Espresso"),
    ("croissant_filled", "Croissant, filled"),
    ("kouign_amann", "Kouign-amann"),
]
NAMED_ITEMS = ["croissant_plain", "flat_white", "latte", "matcha_latte"]
DISTRICT_ITEMS = ["croissant_plain", "flat_white"]
# one product per shop, whatever the menu calls it; every other item is told
# apart by its wording. merge_wave3.py uses the same key.
SINGLE_PRODUCT = {i for i, _ in ITEMS} - {"croissant_filled", "kouign_amann"}


def norm(text):
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def item_key(r):
    k = (r["shop_id"], r["item_canonical"], r["size"], r["hot_or_iced"], r["price_basis"])
    return k if r["item_canonical"] in SINGLE_PRODUCT else k + (norm(r["menu_wording"]),)


# price_note marks written by clean_wave3_tail.py. A row carrying one stays in
# the file but is never counted: not a menu item (add-on, surcharge, gift,
# merchandise...), seasonal by its name, or a price to which the menu adds a
# service charge without giving the rate, so the price paid is not known.
NOT_COUNTED = ("menu: no", "seasonal: by name", "service charge rate unknown")


def counts(row):
    """A row the map, ranks, medians and tables may use: an exact price for a
    menu item on sale now. Rows recorded before wave 3 carry no availability
    note and were all on the menu when recorded, so they count."""
    note = row.get("price_note", "")
    if any(mark in note for mark in NOT_COUNTED):
        return False
    return (row["price_confidence"] == "exact"
            and (note.startswith("availability: available") or "availability:" not in note))


def read(name):
    with open(FOLDER / name, newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        return r.fieldnames[:], list(r)


def write(name, fields, rows):
    with open(FOLDER / name, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------ 2. pain suisse -> pastry
launch_fields, launches = read("launches_clean.csv")
_, launches_orig = read("launches.csv")          # read only, for original format text

assert len(launches) == len(launches_orig), "clean/original launches row counts differ"

pain_suisse_fixed = 0
for clean_row, orig_row in zip(launches, launches_orig):
    if orig_row["format"].strip().lower() == "pain suisse":
        if clean_row["format"] != "pastry":
            clean_row["format"] = "pastry"
            pain_suisse_fixed += 1
write("launches_clean.csv", launch_fields, launches)


# ---------------------------------------------------------------- load rest
_, shop_history = read("shops_all.csv")
_, menu_history = read("menu_items_all.csv")
_, promos = read("promos_clean.csv")

shop_meta = {s["shop_id"]: s for s in shop_history}        # last row per shop wins
shops = [s for s in shop_meta.values() if s["menu_found"] != "removed"]
not_trading = {k for k, s in shop_meta.items() if s["menu_found"] in NOT_TRADING}

# current rows: the latest observed_date for each shop and item
latest = {}
for r in menu_history:
    k = item_key(r)
    latest[k] = max(latest.get(k, ""), r["observed_date"])
menu_current = [r for r in menu_history if r["observed_date"] == latest[item_key(r)]]
# and of those, the ones that count
menu = [r for r in menu_current if counts(r) and r["shop_id"] not in not_trading]

# lat/lng per shop, or None where the postal code had no exact OneMap match.
# A shop missing from shops_geo.csv altogether is treated the same way.
geo_rows = read("shops_geo.csv")[1] + read("shops_geo_wave3.csv")[1]
shop_geo = {
    g["shop_id"]: (float(g["lat"]), float(g["lng"]))
    for g in geo_rows
    if g["match_status"] == "matched" and g["lat"].strip() and g["lng"].strip()
}

big_brands = set()
for _s in shops:
    try:
        if int(_s["outlet_count"]) >= BIG_OUTLET_MIN:
            big_brands.add(brand_key(_s["shop_name"]))
    except (ValueError, KeyError):
        pass
CHAINS = CHAIN_BRANDS | big_brands


# ------------------------------------------- one price per shop per item
def pick_one_per_shop(item):
    """Return {shop_id: (price, basis, note, observed_date)} using
    takeaway > dine_in > online_order.

    Within the winning basis a shop can still list several products for the same
    canonical item (e.g. two filled croissants), so the cheapest is taken as the
    shop's representative entry price.
    """
    by_shop = defaultdict(lambda: defaultdict(list))
    for r in menu:
        if r["item_canonical"] != item or not r["price_sgd"].strip():
            continue
        by_shop[r["shop_id"]][r["price_basis"]].append(
            (float(r["price_sgd"]), r.get("price_note", ""), r.get("observed_date", "")))

    chosen, multi = {}, 0
    for shop_id, by_basis in by_shop.items():
        total = sum(len(v) for v in by_basis.values())
        if total > 1:
            multi += 1
        for basis in BASIS_PREFERENCE:
            if by_basis.get(basis):
                price, note, seen = min(by_basis[basis], key=lambda t: t[0])
                chosen[shop_id] = (price, basis, note, seen)
                break
        else:                                   # basis outside the known three
            basis = sorted(by_basis)[0]
            price, note, seen = min(by_basis[basis], key=lambda t: t[0])
            chosen[shop_id] = (price, basis, note, seen)
    return chosen, multi


def stats(values):
    v = sorted(values)
    return {
        "low": round(v[0], 2),
        "median": round(statistics.median(v), 2),
        "high": round(v[-1], 2),
    }


price_table, named_prices, by_district = [], {}, {}
multi_price_shops = {}
picked = {}

for item, display in ITEMS:
    chosen, multi = pick_one_per_shop(item)
    picked[item] = chosen
    multi_price_shops[item] = multi
    # the commonest format the menus give this item: "pastry", "drink", ...
    formats = Counter(r["format"] for r in menu if r["item_canonical"] == item)
    entry = {"item_canonical": item, "display_name": display,
             "format": formats.most_common(1)[0][0] if formats else "",
             "shop_count": len(chosen)}
    entry.update(stats([p for p, _, _, _ in chosen.values()]) if chosen
                 else {"low": None, "median": None, "high": None})
    # when the prices behind this row were recorded, earliest to latest
    seen_dates = sorted(s for _, _, _, s in chosen.values() if s)
    entry["observed_from"] = seen_dates[0] if seen_dates else None
    entry["observed_to"] = seen_dates[-1] if seen_dates else None
    price_table.append(entry)

for item in NAMED_ITEMS:
    rows = []
    for shop_id, (price, basis, note, seen) in picked[item].items():
        meta = shop_meta.get(shop_id, {})
        lat, lng = shop_geo.get(shop_id, (None, None))
        rows.append({
            "shop_id": shop_id,
            "shop_name": meta.get("shop_name", shop_id),
            "district": meta.get("district", ""),
            "address": meta.get("address", ""),
            "lat": lat,
            "lng": lng,
            "price": round(price, 2),
            "price_basis": basis,
            "observed_date": seen,
            "note": note,
            "is_outlier": note.lower().startswith("outlier"),
        })
    named_prices[item] = sorted(rows, key=lambda r: (r["price"], r["shop_name"]))

# Which of the tracked items each shop has a recorded price for. Every shop with
# a recorded menu is listed, including those selling none of the tracked items,
# so "N of the M other shops in the district" counts against the right M.
shop_items = [
    {"shop_id": shop_id,
     "shop_name": shop_meta.get(shop_id, {}).get("shop_name", shop_id),
     "district": shop_meta.get(shop_id, {}).get("district", ""),
     "items": [item for item, _ in ITEMS if shop_id in picked[item]]}
    for shop_id in sorted({r["shop_id"] for r in menu if r["price_sgd"].strip()})
]

for item in DISTRICT_ITEMS:
    buckets = defaultdict(list)
    for shop_id, (price, _, _, _) in picked[item].items():
        buckets[shop_meta.get(shop_id, {}).get("district", "Unknown")].append(price)
    by_district[item] = [
        dict(district=d, shop_count=len(v), **stats(v))
        for d, v in sorted(buckets.items())
    ]


# ------------------------------------------------- flavours / format split
# Hotel F&B leaves the launches data the same way it leaves promos: dropped
# outright, so All, Independents and Chains add up on the page.
non_seasonal_all = [r for r in launches if r["is_seasonal"] == "no"]
hotel_launches = [r for r in non_seasonal_all
                  if is_hotel(r["shop_id_or_name"], r["source_url"], r.get("source_type", ""))]
_hotel_ids = {id(r) for r in hotel_launches}
non_seasonal = [r for r in non_seasonal_all if id(r) not in _hotel_ids]
months = sorted({r["launch_month"] for r in non_seasonal})

flavour_shops = defaultdict(set)
flavour_items = defaultdict(set)
flavour_month = defaultdict(lambda: defaultdict(lambda: {"shops": set(), "items": set()}))
for r in non_seasonal:
    f, shop, item, m = r["flavour"], r["shop_id_or_name"], r["item_name"], r["launch_month"]
    flavour_shops[f].add(shop)
    flavour_items[f].add(item)
    flavour_month[f][m]["shops"].add(shop)
    flavour_month[f][m]["items"].add(item)

flavours = sorted(
    ({"flavour": f,
      "shop_count": len(flavour_shops[f]),
      "item_count": len(flavour_items[f]),
      "by_month": {m: {"shops": len(flavour_month[f][m]["shops"]),
                       "items": len(flavour_month[f][m]["items"])}
                   for m in sorted(flavour_month[f])}}
     for f in flavour_shops),
    key=lambda e: (-e["shop_count"], -e["item_count"], e["flavour"]),
)

launches_list = [
    {"item_name": r["item_name"],
     "shop": r["shop_id_or_name"],
     "district": r["district"],
     "flavour": r["flavour"],
     "format": r["format"],
     "price": round(float(r["price_sgd"]), 2) if r["price_sgd"].strip() else None,
     "price_confidence": r["price_confidence"],
     "launch_month": r["launch_month"],
     "first_seen": r.get("first_seen", ""),
     "is_chain": brand_key(r["shop_id_or_name"]) in CHAINS,
     "venue_type": "hotel" if is_hotel(r["shop_id_or_name"], r["source_url"], r.get("source_type", "")) else "independent_or_chain"}
    for r in non_seasonal
]

fmt_counter = Counter((r["format"], r["launch_month"]) for r in non_seasonal)
format_split = [
    {"format": fmt,
     "total": sum(c for (f, _), c in fmt_counter.items() if f == fmt),
     "by_month": {m: fmt_counter.get((fmt, m), 0) for m in months}}
    for fmt in sorted({f for f, _ in fmt_counter})
]
format_split.sort(key=lambda e: -e["total"])


# ----------------------------------------------------------------- promos
for _p in promos:
    _p["venue_type"] = "hotel" if is_hotel_promo(_p) else "independent_or_chain"
hotel_promos = [p for p in promos if p["venue_type"] == "hotel"]
promos_kept = [p for p in promos if p["venue_type"] != "hotel"]

promo_type_counts = Counter(p["promo_type"] for p in promos_kept)
chain_counts = Counter(p["is_chain"] for p in promos_kept)


# ------------------------------------------------------------------ output
site_data = {
    "meta": {
        "generated_at": TODAY,
        "shops_total": len(shops),
        "shops_with_prices": len({r["shop_id"] for r in menu}),
        "districts": sorted({s["district"] for s in shops}),
        "districts_count": len({s["district"] for s in shops}),
        "launches_total": len(launches),
        "launches_excluding_seasonal": len(non_seasonal_all),
        "launches_hotel_excluded": len(hotel_launches),
        "launches_listed": len(non_seasonal),
        "promos_total": len(promos_kept),
        "menu_rows_on_file": len(menu_history),
        "menu_rows_current": len(menu_current),
        "menu_rows_counted": len(menu),
        "menu_rows_not_counted": {mark: sum(1 for r in menu_current if mark in r["price_note"])
                                  for mark in NOT_COUNTED},
        "price_selection_rule": (
            "Only current rows count: the latest observed_date for a shop and "
            "item, with an exact price, for a menu item on sale now. Add-ons, "
            "surcharges, gifts, merchandise and groceries are not menu items, "
            "and a price with a service charge of unstated rate is left out. "
            "One price per shop per item. Where a shop lists the item under "
            "several price_basis values, takeaway wins, then dine_in, then "
            "online_order; within the winning basis the cheapest product is used."
        ),
    },
    "price_table": price_table,
    "named_prices": named_prices,
    "by_district": by_district,
    "shop_items": shop_items,
    "flavours": flavours,
    "launches_list": launches_list,
    "format_split": format_split,
    "promos_summary": {
        "total": len(promos_kept),
        "hotel_excluded": len(hotel_promos),
        "by_promo_type": dict(sorted(promo_type_counts.items())),
        "by_is_chain": {"yes": chain_counts.get("yes", 0),
                        "no": chain_counts.get("no", 0)},
    },
    "promos_list": [
        {"brand_name": p["brand_name"], "offer_text": p["offer_text"],
         "promo_type": p["promo_type"], "end_date": p["end_date"],
         "venue_type": p["venue_type"]}
        for p in promos_kept
    ],
}

with open(FOLDER / "site_data.json", "w", encoding="utf-8") as fh:
    json.dump(site_data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")


# ----------------------------------------------------------------- reports
def rule(title):
    print(f"\n{title}\n" + "=" * len(title))


rule("FIX: pain suisse -> pastry (launches_clean.csv)")
print(f"  rows changed from other to pastry : {pain_suisse_fixed}")
print("  left as other: soft-serve, dessert bowl, traditional dessert")

rule("MENU ROWS")
print(f"  on file {len(menu_history)}; current (latest per shop and item) {len(menu_current)}; "
      f"counted (exact price, available) {len(menu)}")
for _mark in NOT_COUNTED:
    print(f"  current rows never counted, '{_mark}': "
          f"{sum(1 for r in menu_current if _mark in r['price_note'])}")
print(f"  shops on file {len(shops)}; with a counted price {len({r['shop_id'] for r in menu})}; "
      f"not trading {len(not_trading)}")

rule("PRICE TABLE (one price per shop; takeaway > dine_in > online_order)")
print(f"  {'item':<22}{'display':<24}{'shops':>6}{'low':>8}{'median':>8}{'high':>8}"
      f"{'multi-price shops':>20}")
for e in price_table:
    print(f"  {e['item_canonical']:<22}{e['display_name']:<24}{e['shop_count']:>6}"
          f"{e['low']:>8.2f}{e['median']:>8.2f}{e['high']:>8.2f}"
          f"{multi_price_shops[e['item_canonical']]:>20}")

rule("TOP 10 FLAVOURS BY SHOP COUNT (seasonal rows excluded)")
print(f"  {'flavour':<38}{'shops':>6}{'items':>6}   monthly split (shops/items)")
for e in flavours[:10]:
    split = "  ".join(f"{m}: {v['shops']}/{v['items']}" for m, v in e["by_month"].items())
    print(f"  {e['flavour']:<38}{e['shop_count']:>6}{e['item_count']:>6}   {split}")
print(f"  ... {len(flavours)} distinct flavours in total, all included in the JSON")

rule("PROMOS - HOTEL F&B EXCLUDED")
print(f"  excluded {len(hotel_promos)} of {len(promos)} rows:")
for _p in hotel_promos:
    print(f"     {_p['brand_name']}  ({_p['source_type']})")
print(f"  kept {len(promos_kept)}")

rule("LAUNCHES - HOTEL F&B EXCLUDED")
print(f"  excluded {len(hotel_launches)} of {len(non_seasonal_all)} non-seasonal rows:")
for _r in hotel_launches:
    print(f"     {_r['shop_id_or_name']}  {_r['item_name']}  {_r['launch_month']}")
print(f"  listed {len(non_seasonal)}")

rule("LAUNCHES - WHO BUCKETS")
_lc = Counter(r["is_chain"] for r in launches_list)
print(f"  All {len(launches_list)} = Independents {_lc[False]} + Chains {_lc[True]}"
      f"  -> adds up: {_lc[False] + _lc[True] == len(launches_list)}")
print("  brands marked chain: " + ", ".join(sorted({r["shop"] for r in launches_list if r["is_chain"]})))
print("  brands in the independent view: " + ", ".join(sorted({r["shop"] for r in launches_list if not r["is_chain"]})))

rule("PROMO TYPE SPLIT")
for k, v in sorted(promo_type_counts.items()):
    print(f"  {k:<12} {v:>3}")
print(f"  {'TOTAL':<12} {len(promos_kept):>3}   "
      f"(chain: {chain_counts.get('yes',0)}, independent: {chain_counts.get('no',0)})")

rule("COORDINATES (shops_geo.csv)")
_named_ids = {r["shop_id"] for rows in named_prices.values() for r in rows}
_no_geo = sorted(i for i in _named_ids if i not in shop_geo)
print(f"  shops in shops_all.csv with a location : {sum(1 for s in shops if s['shop_id'] in shop_geo)} of {len(shops)}")
print(f"  shops in named_prices with a location  : {len(_named_ids) - len(_no_geo)} of {len(_named_ids)}")
for _i in _no_geo:
    print(f"     no location, left off the map: {shop_meta.get(_i, {}).get('shop_name', _i)}")

rule("TOO THIN TO PUBLISH (fewer than 5 shops)")
thin = [e for e in price_table if e["shop_count"] < 5]
if thin:
    for e in thin:
        print(f"  !! {e['item_canonical']:<20} only {e['shop_count']} shop(s)")
else:
    print("  none - every item in the list has 5 or more shops")
print()
