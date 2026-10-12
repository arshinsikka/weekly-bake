#!/usr/bin/env python3
"""Build counter/counter_data.json: the new items and the offers Counter shows,
with the fields the live site's file leaves out (the source link, limited
time, what an offer covers, and which of our shops a cafe name stands for).

The rules are the live site's own, copied from build_site_data.py rather than
imported, because importing that script runs it and it writes files. Before
anything is written the result is checked against site/site_data.json: the
same launches with the same chain flags, and the same offers. If either
differs nothing is written.

It also writes one file for each shop we have prices for, counter/menus/
<shop_id>.json: the shop's own menu, the rows the live site counts and no
others. Counter loads one of these only when an owner asks for ideas.

Inputs, all read only: launches_clean.csv, promos_clean.csv, shops_all.csv,
menu_items_all.csv, site/site_data.json.
Outputs: counter/counter_data.json, counter/menus/<shop_id>.json, and
counter/site_data.json as a byte copy of site/site_data.json when the two
differ. No network calls.
"""

import csv
import datetime
import filecmp
import hashlib
import json
import re
import shutil
import statistics
import sys
import unicodedata
from collections import Counter
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
TODAY = datetime.date.today().isoformat()
SITE_DATA = FOLDER / "site" / "site_data.json"
COUNTER_SITE_DATA = FOLDER / "counter" / "site_data.json"
COUNTER_DATA = FOLDER / "counter" / "counter_data.json"
MENUS = FOLDER / "counter" / "menus"
MENU_MAX_ROWS = 400


# ------------------------------- copied from build_site_data.py, lines 30-56
# Hotel F&B is out of scope: a neighbourhood cafe does not compete with a hotel
# afternoon tea.
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
# ------------------------------------------------------- end of copied rules


def read(name):
    with open(FOLDER / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


launches = read("launches_clean.csv")
promos = read("promos_clean.csv")
shop_history = read("shops_all.csv")
with open(SITE_DATA, encoding="utf-8") as fh:
    site_data = json.load(fh)

# copied from build_site_data.py, lines 143-144 and 165-172
shop_meta = {s["shop_id"]: s for s in shop_history}        # last row per shop wins
shops = [s for s in shop_meta.values() if s["menu_found"] != "removed"]

big_brands = set()
for _s in shops:
    try:
        if int(_s["outlet_count"]) >= BIG_OUTLET_MIN:
            big_brands.add(brand_key(_s["shop_name"]))
    except (ValueError, KeyError):
        pass
CHAINS = CHAIN_BRANDS | big_brands

# copied from build_site_data.py, lines 308-312: seasonal rows and hotels are left out
non_seasonal_all = [r for r in launches if r["is_seasonal"] == "no"]
non_seasonal = [r for r in non_seasonal_all
                if not is_hotel(r["shop_id_or_name"], r["source_url"], r.get("source_type", ""))]

# A launch names a cafe, not a shop. It stands for every shop we have prices
# for that carries the same name once the text in brackets is dropped.
priced_shops = site_data["shop_items"]

def linked(name):
    return [s for s in priced_shops if brand_key(s["shop_name"]) == brand_key(name)]

records = []
for r in non_seasonal:
    matches = linked(r["shop_id_or_name"])
    records.append({
        "item_name": r["item_name"],
        "shop": r["shop_id_or_name"],
        "flavour": r["flavour"],
        "format": r["format"],
        "price": round(float(r["price_sgd"]), 2) if r["price_sgd"].strip() else None,
        "price_confidence": r["price_confidence"],
        "launch_month": r["launch_month"],
        "first_seen": r.get("first_seen", ""),
        "is_limited_time": r["is_limited_time"] == "yes",
        "is_chain": brand_key(r["shop_id_or_name"]) in CHAINS,
        "source_url": r["source_url"],
        "source_type": r["source_type"],
        "shop_ids": [s["shop_id"] for s in matches],
        # one shop: its own district. None or several: the district the launch came with.
        "district": matches[0]["district"] if len(matches) == 1 else r["district"],
    })


# ------------------------------------------------- the same as the live site?
def key(r):
    return (r["shop"], r["item_name"], r["launch_month"], r["first_seen"], r["is_chain"])

site_launches = site_data["launches_list"]
ours, theirs = {key(r) for r in records}, {key(r) for r in site_launches}
if ours != theirs or len(records) != len(site_launches):
    print("NOT WRITTEN: the launches differ from site/site_data.json")
    print(f"  records here {len(records)}, in site_data.json {len(site_launches)}")
    for k in sorted(ours - theirs):
        print("  only here            :", k)
    for k in sorted(theirs - ours):
        print("  only in site_data.json:", k)
    sys.exit(1)


# ------------------------------------------------------------------ offers
# copied from build_site_data.py, lines 42-43 and 362-365: hotel rows are left out
def is_hotel_promo(row):
    return is_hotel(row["brand_name"], row["source_url"], row["source_type"])

promos_kept = [p for p in promos if not is_hotel_promo(p)]

offers = []
for p in promos_kept:
    matches = linked(p["brand_name"])
    offers.append({
        "brand_name": p["brand_name"],
        "offer_text": p["offer_text"],
        "promo_type": p["promo_type"],
        "items_covered": p["items_covered"],
        "start_date": p["start_date"].strip() or None,
        "end_date": p["end_date"].strip() or None,
        "end_date_stated": p["end_date_stated"] == "yes",
        # the same chain rule as a launch, not the CSV's own is_chain column
        "is_chain": brand_key(p["brand_name"]) in CHAINS,
        "source_url": p["source_url"],
        "source_type": p["source_type"],
        "observed_date": p["observed_date"].strip() or None,
        "shop_ids": [s["shop_id"] for s in matches],
        # one shop: its own district. An offer names a brand and no place, so otherwise none.
        "district": matches[0]["district"] if len(matches) == 1 else "",
    })

def offer_key(r):
    return (r["brand_name"], r["offer_text"], r["promo_type"], r["end_date"] or "")

site_promos = site_data["promos_list"]
ours_o, theirs_o = {offer_key(r) for r in offers}, {offer_key(r) for r in site_promos}
if ours_o != theirs_o or len(offers) != len(site_promos):
    print("NOT WRITTEN: the offers differ from site/site_data.json")
    print(f"  records here {len(offers)}, in site_data.json {len(site_promos)}")
    for k in sorted(ours_o - theirs_o):
        print("  only here            :", k)
    for k in sorted(theirs_o - ours_o):
        print("  only in site_data.json:", k)
    sys.exit(1)


# ------------------------------------------------------- each shop's menu
# The rows of menu_items_all.csv that the live site counts: the latest
# observation of each item, with an exact price, on sale now, for a shop that
# is trading. The rules below are copied from build_site_data.py.

# copied from build_site_data.py, line 60
NOT_TRADING = {"closed", "temporarily_closed", "removed"}

# copied from build_site_data.py, lines 62-72 and 78: the compared items, and
# which of them are one product per shop whatever the menu calls it
ITEM_KEYS = ["croissant_plain", "flat_white", "latte", "matcha_latte", "cappuccino",
             "americano", "espresso", "croissant_filled", "kouign_amann"]
SINGLE_PRODUCT = set(ITEM_KEYS) - {"croissant_filled", "kouign_amann"}

# copied from build_site_data.py, lines 81-84
def norm(text):
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()

# copied from build_site_data.py, lines 87-89
def item_key(r):
    k = (r["shop_id"], r["item_canonical"], r["size"], r["hot_or_iced"], r["price_basis"])
    return k if r["item_canonical"] in SINGLE_PRODUCT else k + (norm(r["menu_wording"]),)

# copied from build_site_data.py, lines 97 and 100-108
NOT_COUNTED = ("menu: no", "seasonal: by name", "service charge rate unknown")

def counts(row):
    note = row.get("price_note", "")
    if any(mark in note for mark in NOT_COUNTED):
        return False
    return (row["price_confidence"] == "exact"
            and (note.startswith("availability: available") or "availability:" not in note))

menu_history = read("menu_items_all.csv")

# copied from build_site_data.py, lines 145 and 147-154
not_trading = {k for k, s in shop_meta.items() if s["menu_found"] in NOT_TRADING}
latest = {}
for r in menu_history:
    k = item_key(r)
    latest[k] = max(latest.get(k, ""), r["observed_date"])
menu_current = [r for r in menu_history if r["observed_date"] == latest[item_key(r)]]
menu = [r for r in menu_current if counts(r) and r["shop_id"] not in not_trading]
# ------------------------------------------------------- end of copied rules

KINDS = {"drink", "pastry", "cake", "savoury"}           # anything else is "other"
PLAIN_WORDS = {"regular", "single", "hot", "not_applicable", ""}   # said by no menu, so left out

def variant_of(r):
    parts = [w for w in (r["size"].strip(), r["hot_or_iced"].strip()) if w.lower() not in PLAIN_WORDS]
    return ", ".join(p.replace("_", " ") for p in parts)

# copied from build_site_data.py, line 58: which price stands for an item that
# has several, takeaway first
BASIS_PREFERENCE = ["takeaway", "dine_in", "online_order"]

def basis_rank(basis):
    return BASIS_PREFERENCE.index(basis) if basis in BASIS_PREFERENCE else len(BASIS_PREFERENCE)

def menu_rows(shop_id):
    """One row for each name and variant of one shop, in the order of the file.
    Returns the rows, and how many counted prices they were chosen from."""
    mine = [r for r in menu if r["shop_id"] == shop_id and r["price_sgd"].strip()]
    # Where one name and variant has several prices, one is kept the way
    # build_site_data.py picks a shop's price (pick_one_per_shop, lines 176-205):
    # the preferred price basis first, and within it the lowest price.
    best = {}
    for r in mine:
        k = (r["menu_wording"], variant_of(r))
        rank = (basis_rank(r["price_basis"]), float(r["price_sgd"]))
        if k not in best or rank < best[k][0]:
            best[k] = (rank, r)
    rows, seen = [], set()
    for k in dict.fromkeys((r["menu_wording"], variant_of(r)) for r in mine):
        r = best[k][1]
        # a short id that stays the same from run to run while the row does
        base = hashlib.sha1("|".join(k).encode("utf-8")).hexdigest()[:6]
        rid, n = "m" + base, 1
        while rid in seen:
            n += 1
            rid = f"m{base}{n}"
        seen.add(rid)
        rows.append({"id": rid, "name": r["menu_wording"], "variant": k[1],
                     "price": round(float(r["price_sgd"]), 2),
                     "kind": r["format"] if r["format"] in KINDS else "other"})
    return rows, len(mine)

shop_menus, cut_shops, prices_in = {}, [], {}
for s in priced_shops:
    rows, priced = menu_rows(s["shop_id"])
    # the same number of counted prices the live site's file gives for this shop
    if priced != s["price_count"]:
        print(f"NOT WRITTEN: {s['shop_id']} has {priced} counted prices here and {s['price_count']} in site_data.json")
        sys.exit(1)
    prices_in[s["shop_id"]] = priced
    if len(rows) > MENU_MAX_ROWS:
        # too long to send: the dearest rows go, the cheapest are the last to be cut
        keep = {r["id"] for r in sorted(rows, key=lambda r: r["price"])[:MENU_MAX_ROWS]}
        cut_shops.append((s["shop_id"], len(rows)))
        rows = [r for r in rows if r["id"] in keep]
    shop_menus[s["shop_id"]] = rows


# ------------------------------------------------------------------ output
counter_data = {
    "meta": {"generated_at": TODAY, "launches": len(records), "offers": len(offers)},
    "launches": records,
    "offers": offers,
}
with open(COUNTER_DATA, "w", encoding="utf-8") as fh:
    json.dump(counter_data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")

MENUS.mkdir(exist_ok=True)
for shop_id, rows in shop_menus.items():
    with open(MENUS / f"{shop_id}.json", "w", encoding="utf-8") as fh:
        fh.write('{"shop_id": ' + json.dumps(shop_id) + ', "rows": [\n')
        fh.write(",\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
        fh.write("\n]}\n")

refreshed = not COUNTER_SITE_DATA.exists() or not filecmp.cmp(SITE_DATA, COUNTER_SITE_DATA, shallow=False)
if refreshed:
    shutil.copyfile(SITE_DATA, COUNTER_SITE_DATA)

with_shop = [r for r in records if r["shop_ids"]]
print("COUNTER DATA")
print(f"  check against site_data.json : equal ({len(ours)} distinct launches here, {len(theirs)} there; "
      f"{len(records)} records here, {len(site_launches)} there)")
print(f"  launches kept                : {len(records)} of {len(launches)} rows")
print(f"  chains                       : {sum(1 for r in records if r['is_chain'])}")
print(f"  with at least one shop_id    : {len(with_shop)}")
print(f"  distinct shops linked        : {len({i for r in records for i in r['shop_ids']})}")
print(f"  counter/site_data.json       : {'refreshed' if refreshed else 'already identical, left alone'}")
print(f"  offers                       : {len(promos)} rows read, {len(offers)} kept, {len(promos) - len(offers)} hotels dropped, "
      f"{sum(1 for r in offers if r['is_chain'])} chains, {sum(1 for r in offers if r['shop_ids'])} with at least one shop_id, "
      f"{len({i for r in offers for i in r['shop_ids']})} distinct shops linked "
      f"(check against site_data.json: equal, {len(ours_o)} here, {len(theirs_o)} there)")
print(f"  wrote {COUNTER_DATA.relative_to(FOLDER)}")
sizes = sorted(len(r) for r in shop_menus.values())
print("MENUS")
print(f"  rules                        : latest observation, exact price, on sale, shop trading ({len(menu)} rows of "
      f"{len(menu_history)} in menu_items_all.csv)")
print(f"  check against site_data.json : equal (each shop's number of counted prices is its price_count there)")
print(f"  shops written                : {len(shop_menus)} to {MENUS.relative_to(FOLDER)}/")
merged = {i: prices_in[i] - len(r) for i, r in shop_menus.items() if i not in dict(cut_shops) and prices_in[i] > len(r)}
print(f"  one row per name and variant : {sum(prices_in.values())} counted prices became {sum(dict(cut_shops).get(i, len(r)) for i, r in shop_menus.items())} rows "
      f"({len(merged) + sum(1 for i, n in cut_shops if prices_in[i] > n)} shops had an item at several prices; takeaway, then dine-in, then online, then the lowest)")
print(f"  rows                         : {sum(sizes)} in all; smallest {sizes[0]}, median {statistics.median(sizes)}, largest {sizes[-1]}")
print(f"  cut to {MENU_MAX_ROWS} rows               : "
      + (", ".join(f"{i} (had {n})" for i, n in cut_shops) if cut_shops else "none"))
twice = {i: sum(1 for k, n in Counter((r["name"], r["variant"]) for r in rows).items() if n > 1)
         for i, rows in shop_menus.items()}
print(f"  same name and variant twice  : {sum(1 for n in twice.values() if n)} shops, {sum(twice.values())} names "
      f"(one item at several prices, for example dine-in and online)")
