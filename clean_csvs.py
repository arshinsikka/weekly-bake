#!/usr/bin/env python3
"""Clean the four scraped CSVs into *_clean.csv copies. Originals are never modified."""

import csv
import re
import statistics
from pathlib import Path

FOLDER = Path(__file__).resolve().parent

ODDLE_BASIS = "online_order (Oddle platform markup)"
ODDLE_NOTE = "Oddle applies a platform markup over in-store price"
OUTLIER_NOTE = ("Outlier, highest plain croissant in dataset, from Chope listing, "
                "may be a plated item rather than counter price")

counts = {}   # fix label -> rows changed


def read_csv(name):
    with open(FOLDER / name, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return reader.fieldnames[:], list(reader)


def write_csv(name, fields, rows):
    with open(FOLDER / name, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------- menu_items
menu_fields, menu_rows = read_csv("menu_items_final.csv")
menu_before = len(menu_rows)

menu_fields.append("price_note")
for row in menu_rows:
    row["price_note"] = ""

# FIX 1 - strip the Oddle markup annotation out of price_basis into price_note
n = 0
for row in menu_rows:
    if row["price_basis"] == ODDLE_BASIS:
        row["price_basis"] = "online_order"
        row["price_note"] = ODDLE_NOTE
        n += 1
counts["FIX 1  price_basis -> online_order + price_note"] = n

# FIX 2 - flavoured drink mis-filed as a plain latte
n = 0
for row in menu_rows:
    if (row["shop_id"] == "keong_saik_bakery_bendemeer"
            and row["menu_wording"] == "Iced Kaya Latte"):
        row["item_canonical"] = "other"
        n += 1
counts["FIX 2  Iced Kaya Latte -> item_canonical other"] = n

# FIX 5 - flag (do not delete) the highest plain croissant
n = 0
for row in menu_rows:
    if (row["shop_id"] == "prairie_by_craftsmen_joo_chiat"
            and row["item_canonical"] == "croissant_plain"):
        row["price_note"] = OUTLIER_NOTE
        n += 1
counts["FIX 5  Prairie croissant_plain outlier note"] = n

write_csv("menu_items_final_clean.csv", menu_fields, menu_rows)


# ------------------------------------------------------------------ launches
launch_fields, launch_rows = read_csv("launches.csv")
launch_before = len(launch_rows)

CONFIDENCE_MAP = {"high": "exact", "medium": "ambiguous"}


def map_format(value):
    """Collapse free-text format into drink/pastry/cake/savoury/other.

    Checked in an order that keeps the user's explicit token lists intact:
    'mooncake' is listed under pastry even though it contains 'cake', and
    'teacake' is listed under cake even though it contains 'tea', so the
    drink test uses word boundaries and mooncake is resolved first.
    """
    v = value.lower()
    if "mooncake" in v:
        return "pastry"
    if re.search(r"\b(beverage|latte|frappe|coffee|tea|drink)\b", v):
        return "drink"
    if re.search(r"cake|cheesecake|cupcake|teacake|roll cake", v):
        return "cake"
    if re.search(r"savoury|sandwich|plate", v):
        return "savoury"
    if re.search(r"pastry|croissant|danish|bun|tart|scone|puff|mochi|"
                 r"shio pan|kouign|clairssant|loaf|toast", v):
        return "pastry"
    return "other"


# FIX 3 - price_confidence + format vocabularies
n_conf = 0
n_fmt = 0
format_map_seen = {}
for row in launch_rows:
    old_conf = row["price_confidence"]
    new_conf = CONFIDENCE_MAP.get(old_conf, old_conf)
    if new_conf != old_conf:
        row["price_confidence"] = new_conf
        n_conf += 1

    old_fmt = row["format"]
    new_fmt = map_format(old_fmt)
    format_map_seen[old_fmt] = new_fmt
    if new_fmt != old_fmt:
        row["format"] = new_fmt
        n_fmt += 1
counts["FIX 3a price_confidence high/medium -> exact/ambiguous"] = n_conf
counts["FIX 3b format -> controlled vocabulary"] = n_fmt

# FIX 4 - seasonal (Mid-Autumn / National Day) flag
SEASONAL = ("mooncake", "snowskin", "mid-autumn", "national day", "merlion")
launch_fields.append("is_seasonal")
n_seasonal = 0
for row in launch_rows:
    blob = f"{row['item_name']} {row['flavour']}".lower()
    flagged = any(term in blob for term in SEASONAL)
    row["is_seasonal"] = "yes" if flagged else "no"
    if flagged:
        n_seasonal += 1
counts["FIX 4  is_seasonal column added (rows flagged yes)"] = n_seasonal

write_csv("launches_clean.csv", launch_fields, launch_rows)


# --------------------------------------------- shops + promos (copied as-is)
shop_fields, shop_rows = read_csv("shops_final.csv")
write_csv("shops_final_clean.csv", shop_fields, shop_rows)

promo_fields, promo_rows = read_csv("promos.csv")
write_csv("promos_clean.csv", promo_fields, promo_rows)


# ------------------------------------------------------------------- reports
def rule(title):
    print(f"\n{title}\n" + "-" * len(title))


rule("FORMAT MAPPING (launches.csv)")
width = max(len(k) for k in format_map_seen)
for original in sorted(format_map_seen):
    print(f"  {original:<{width}}  ->  {format_map_seen[original]}")

rule("SEASONAL FLAG (launches.csv)")
print(f"  rows flagged is_seasonal=yes : {n_seasonal} of {len(launch_rows)}")

rule("ROW COUNTS (before -> after)")
pairs = [
    ("shops_final.csv", "shops_final_clean.csv", len(shop_rows), len(shop_rows)),
    ("menu_items_final.csv", "menu_items_final_clean.csv", menu_before, len(menu_rows)),
    ("launches.csv", "launches_clean.csv", launch_before, len(launch_rows)),
    ("promos.csv", "promos_clean.csv", len(promo_rows), len(promo_rows)),
]
for src, dst, before, after in pairs:
    print(f"  {src:<22} {before:>4}  ->  {dst:<28} {after:>4}")

rule("ROWS CHANGED PER FIX")
for label, changed in counts.items():
    print(f"  {label:<52} {changed:>4}")

rule("PRICE SPREAD IN menu_items_final_clean.csv")
for item in ("croissant_plain", "flat_white", "latte", "matcha_latte"):
    rows = [r for r in menu_rows if r["item_canonical"] == item and r["price_sgd"].strip()]
    prices = sorted(float(r["price_sgd"]) for r in rows)
    shops = {r["shop_id"] for r in rows}
    if not prices:
        print(f"  {item:<16} no priced rows")
        continue
    print(f"  {item:<16} shops={len(shops):>2}  rows={len(prices):>3}  "
          f"low={prices[0]:>5.2f}  median={statistics.median(prices):>5.2f}  "
          f"high={prices[-1]:>5.2f}")
print()
