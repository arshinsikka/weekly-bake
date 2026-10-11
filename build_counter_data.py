#!/usr/bin/env python3
"""Build counter/counter_data.json: the new items Counter shows, with the
fields the live site's file leaves out (the source link, limited time, and
which of our shops a cafe name stands for).

The rules are the live site's own, copied from build_site_data.py rather than
imported, because importing that script runs it and it writes files. Before
anything is written the result is checked against site/site_data.json: the
same launches, the same chain flags. If they differ nothing is written.

Inputs, all read only: launches_clean.csv, shops_all.csv, site/site_data.json.
Outputs: counter/counter_data.json, and counter/site_data.json as a byte copy
of site/site_data.json when the two differ. No network calls.
"""

import csv
import datetime
import filecmp
import json
import re
import shutil
import sys
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
TODAY = datetime.date.today().isoformat()
SITE_DATA = FOLDER / "site" / "site_data.json"
COUNTER_SITE_DATA = FOLDER / "counter" / "site_data.json"
COUNTER_DATA = FOLDER / "counter" / "counter_data.json"


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


# ------------------------------------------------------------------ output
counter_data = {
    "meta": {"generated_at": TODAY, "launches": len(records)},
    "launches": records,
}
with open(COUNTER_DATA, "w", encoding="utf-8") as fh:
    json.dump(counter_data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")

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
print(f"  wrote {COUNTER_DATA.relative_to(FOLDER)}")
