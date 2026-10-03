#!/usr/bin/env python3
"""Two conventions fixes on menu_items_all.csv (edited in place).

1. Carpenter and Cook's "Lemon Drizzle Loaf Slice": item_canonical danish -> cake_slice.
2. Wave 2 food rows (format pastry/cake/savoury) take wave 1's convention:
   hot_or_iced -> not_applicable, size -> single. Drink rows untouched.

Fix 2 is scoped to wave 2 shop_ids on purpose. Wave 1 food rows already use
not_applicable, and three of them carry a real size that 'single' would destroy
(two whole loaves and a box of 6 egg tarts), so they are left alone.
"""

import csv
from collections import Counter
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
FOOD_FORMATS = {"pastry", "cake", "savoury"}


def read(name):
    with open(FOLDER / name, newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        return r.fieldnames[:], list(r)


fields, rows = read("menu_items_all.csv")
wave2_ids = {r["shop_id"] for r in csv.DictReader(
    open(FOLDER / "shops_wave2_clean.csv", newline="", encoding="utf-8"))}

# ---- 1. mis-categorised loaf slice
n_recat = 0
for r in rows:
    if (r["shop_id"] == "carpenters_and_cook_beauty_world"
            and r["menu_wording"] == "Lemon Drizzle Loaf Slice"
            and r["item_canonical"] == "danish"):
        r["item_canonical"] = "cake_slice"
        n_recat += 1

# ---- 2. wave 2 food rows adopt wave 1's hot_or_iced / size convention
n_hot = n_size = n_rows_touched = 0
for r in rows:
    if r["shop_id"] not in wave2_ids or r["format"] not in FOOD_FORMATS:
        continue
    touched = False
    if r["hot_or_iced"] != "not_applicable":
        r["hot_or_iced"] = "not_applicable"
        n_hot += 1
        touched = True
    if r["size"] != "single":
        r["size"] = "single"
        n_size += 1
        touched = True
    n_rows_touched += touched

with open(FOLDER / "menu_items_all.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)


def rule(t):
    print(f"\n{t}\n" + "=" * len(t))


rule("FIX 1 - Lemon Drizzle Loaf Slice")
print(f"  item_canonical danish -> cake_slice : {n_recat} row")

rule("FIX 2 - wave 2 food rows normalised")
print(f"  rows touched               : {n_rows_touched}")
print(f"  hot_or_iced -> not_applicable : {n_hot}")
print(f"  size -> single                : {n_size}")
print("  scope: wave 2 shop_ids only; drink rows untouched")

rule("VERIFY (whole file)")
food = [r for r in rows if r["format"] in FOOD_FORMATS]
drink = [r for r in rows if r["format"] == "drink"]
print(f"  food rows  {len(food):>4}  hot_or_iced={dict(Counter(r['hot_or_iced'] for r in food))}")
print(f"                   size={dict(Counter(r['size'] for r in food))}")
print(f"  drink rows {len(drink):>4}  hot_or_iced={dict(Counter(r['hot_or_iced'] for r in drink))}")
print(f"  danish rows with format=cake remaining: "
      f"{sum(1 for r in rows if r['item_canonical']=='danish' and r['format']=='cake')}")
print("  wave 1 food rows keeping a non-single size:")
for r in rows:
    if r["shop_id"] not in wave2_ids and r["format"] in FOOD_FORMATS and r["size"] != "single":
        print(f"     {r['shop_id']:<24} {r['menu_wording']:<42} size={r['size']}")
print()
