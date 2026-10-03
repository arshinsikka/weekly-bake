#!/usr/bin/env python3
"""Clean wave 2, merge it with the cleaned wave 1, and report.

Reads:  shops_wave2.csv, menu_items_wave2.csv (originals, never modified)
        shops_final_clean.csv, menu_items_final_clean.csv
Writes: shops_wave2_clean.csv, menu_items_wave2_clean.csv,
        shops_all.csv, menu_items_all.csv
"""

import csv
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

FOLDER = Path(__file__).resolve().parent

STALE_SHOPS = {
    "koki_bakery_duxton": "2025-03-03",
    "butter_tgt_duxton": "2025-04-12",
    "tarik_cafe_arab_st": "2025-09-28",
}
DROP_SHOP = "forty_hands"
BASIS_PREFERENCE = ["takeaway", "dine_in", "online_order"]


def read(name):
    with open(FOLDER / name, newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        return r.fieldnames[:], list(r)


def write(name, fields, rows):
    with open(FOLDER / name, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def rule(title):
    print(f"\n{title}\n" + "=" * len(title))


# ============================================================ 1. CLEAN WAVE 2
w2_menu_fields, w2_menu = read("menu_items_wave2.csv")
w2_shop_fields, w2_shops = read("shops_wave2.csv")
w2_menu_before, w2_shops_before = len(w2_menu), len(w2_shops)

# 1a - plain croissants carry flavour "none", matching wave 1
n_flavour = 0
for r in w2_menu:
    if r["item_canonical"] == "croissant_plain" and r["flavour"] == "butter":
        r["flavour"] = "none"
        n_flavour += 1

# 1b - drop rows whose source breaches the 6-month freshness rule
dropped_by_shop = Counter(r["shop_id"] for r in w2_menu if r["shop_id"] in STALE_SHOPS)
w2_menu = [r for r in w2_menu if r["shop_id"] not in STALE_SHOPS]

n_marked = 0
for s in w2_shops:
    if s["shop_id"] in STALE_SHOPS and s["menu_found"] != "stale_only":
        s["menu_found"] = "stale_only"
        n_marked += 1

# 1c - price_note column, so wave 2 matches the cleaned wave 1 shape
w2_menu_fields.append("price_note")
for r in w2_menu:
    r["price_note"] = ""

write("menu_items_wave2_clean.csv", w2_menu_fields, w2_menu)
write("shops_wave2_clean.csv", w2_shop_fields, w2_shops)


# =================================================================== 2. MERGE
w1_shop_fields, w1_shops = read("shops_final_clean.csv")
w1_menu_fields, w1_menu = read("menu_items_final_clean.csv")

header_problems = []
if w1_shop_fields != w2_shop_fields:
    header_problems.append(("shops", w1_shop_fields, w2_shop_fields))
if w1_menu_fields != w2_menu_fields:
    header_problems.append(("menu_items", w1_menu_fields, w2_menu_fields))

if header_problems:
    rule("STOPPED - COLUMN HEADERS DO NOT MATCH")
    for label, a, b in header_problems:
        print(f"  {label}:")
        print(f"    wave 1 : {a}")
        print(f"    wave 2 : {b}")
        print(f"    only in wave 1: {[c for c in a if c not in b]}")
        print(f"    only in wave 2: {[c for c in b if c not in a]}")
    print("\nNothing was merged. Fix the headers and re-run.")
    sys.exit(1)

shared_shop_ids = sorted({s["shop_id"] for s in w1_shops} & {s["shop_id"] for s in w2_shops})

all_shops = w1_shops + w2_shops
all_menu = w1_menu + w2_menu


# ========================================================== 3. DROP forty_hands
before_drop = len(all_shops)
all_shops = [s for s in all_shops if s["shop_id"] != DROP_SHOP]
n_dropped_shop = before_drop - len(all_shops)
orphan_menu = [r for r in all_menu if r["shop_id"] == DROP_SHOP]

write("shops_all.csv", w1_shop_fields, all_shops)
write("menu_items_all.csv", w1_menu_fields, all_menu)


# ================================================================== 5. REPORT
shop_meta = {s["shop_id"]: s for s in all_shops}


def pick_one_per_shop(item):
    by_shop = defaultdict(lambda: defaultdict(list))
    for r in all_menu:
        if r["item_canonical"] != item or not r["price_sgd"].strip():
            continue
        by_shop[r["shop_id"]][r["price_basis"]].append(float(r["price_sgd"]))
    chosen = {}
    for shop_id, by_basis in by_shop.items():
        for basis in BASIS_PREFERENCE:
            if by_basis.get(basis):
                chosen[shop_id] = min(by_basis[basis])
                break
        else:
            chosen[shop_id] = min(min(v) for v in by_basis.values())
    return chosen


rule("1. WAVE 2 CLEANING")
print(f"  1a flavour butter -> none on croissant_plain : {n_flavour} rows")
print(f"  1b stale-source rows deleted                 : {sum(dropped_by_shop.values())} rows")
for shop, dated in STALE_SHOPS.items():
    print(f"       {shop:<22} source {dated}  ->  {dropped_by_shop.get(shop, 0)} rows removed")
print(f"     shops set to menu_found=stale_only        : {n_marked}")
print(f"  1c price_note column added                   : yes (now {len(w2_menu_fields)} columns)")
print(f"  menu_items_wave2  {w2_menu_before} -> {len(w2_menu)} rows")
print(f"  shops_wave2       {w2_shops_before} -> {len(w2_shops)} rows (no shop rows deleted)")

rule("2. MERGE")
print("  column headers identical in both waves: yes (shops 12 cols, menu 16 cols)")
print(f"  shop_id present in BOTH waves: {shared_shop_ids if shared_shop_ids else 'none'}")
if not shared_shop_ids:
    print("    (nothing to reconcile by hand)")

rule("3. DROP forty_hands")
print(f"  shop rows removed : {n_dropped_shop}")
print(f"  orphan menu rows  : {len(orphan_menu)} (it had no price data)")

rule("5a. SHOPS")
mf = Counter(s["menu_found"] for s in all_shops)
print(f"  total shops in shops_all.csv : {len(all_shops)}")
print(f"  menu_found = yes             : {mf.get('yes', 0)}")
for k in sorted(k for k in mf if k != "yes"):
    print(f"  menu_found = {k:<16}: {mf[k]}")

rule("5b. MENU ROWS")
print(f"  total rows in menu_items_all.csv : {len(all_menu)}")
print(f"  distinct shops with prices       : {len({r['shop_id'] for r in all_menu})}")

rule("5c. PRICE TABLE (one price per shop: takeaway > dine_in > online_order)")
print(f"  {'item_canonical':<18}{'shops':>6}{'low':>8}{'median':>8}{'high':>8}")
items = sorted({r["item_canonical"] for r in all_menu},
               key=lambda i: -len(pick_one_per_shop(i)))
for item in items:
    prices = sorted(pick_one_per_shop(item).values())
    print(f"  {item:<18}{len(prices):>6}{prices[0]:>8.2f}"
          f"{statistics.median(prices):>8.2f}{prices[-1]:>8.2f}")

rule("5d. croissant_plain BY DISTRICT")
buckets = defaultdict(list)
for shop_id, price in pick_one_per_shop("croissant_plain").items():
    buckets[shop_meta.get(shop_id, {}).get("district", "UNKNOWN")].append(price)
print(f"  {'district':<34}{'shops':>6}{'low':>8}{'median':>8}{'high':>8}")
for d, v in sorted(buckets.items(), key=lambda x: (-len(x[1]), x[0])):
    v = sorted(v)
    print(f"  {d:<34}{len(v):>6}{v[0]:>8.2f}{statistics.median(v):>8.2f}{v[-1]:>8.2f}")

rule("5e. CHECK FIX 1a")
leftovers = [r for r in all_menu
             if r["item_canonical"] == "croissant_plain" and r["flavour"] == "butter"]
if leftovers:
    print(f"  !! {len(leftovers)} row(s) still have flavour=butter on croissant_plain:")
    for r in leftovers:
        print(f"     {r['shop_id']} | {r['menu_wording']}")
else:
    print("  0 rows remain with flavour=butter on croissant_plain - fix 1a verified")
    print(f"  croissant_plain flavour values now: "
          f"{dict(Counter(r['flavour'] for r in all_menu if r['item_canonical']=='croissant_plain'))}")
print()
