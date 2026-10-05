#!/usr/bin/env python3
"""Merge the approved rows of review_merge.csv into menu_items_all.csv. Run
this after a person has filled the approve column, then build_site_data.py.

Reads:  review_merge.csv, menu_items_wave3.csv, menu_items_all.csv
Writes: menu_items_all.csv   (approved rows appended; nothing else touched)

Each approved row is the wave 3 row it was held from, put through the same
rules the merged rows went through:
  merge_wave3.py       service charge and GST the menu states are added, the
                       listed price kept in price_note
  clean_wave3_tail.py  steps 2 to 6: size vocabulary, not a menu item, 9-item
                       tags on single serves only, seasonal by name, service
                       charge without a rate
Rule 3c of merge_wave3.py was settled before a row could reach
review_merge.csv, so it is not applied again. A second run appends nothing.
"""
import csv
import io
import re
import sys
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
NINE = ["croissant_plain", "flat_white", "latte", "matcha_latte", "cappuccino",
        "americano", "espresso", "croissant_filled", "kouign_amann"]


def rule(t):
    print(f"\n{t}\n" + "=" * len(t))


def read(name):
    text = (FOLDER / name).read_text(encoding="utf-8")
    r = csv.DictReader(io.StringIO(text, newline=""))
    return text, r.fieldnames[:], list(r)


# ------------------------------------------- merge_wave3.py: charges the menu states
def charges(row):
    note = row["price_note"]
    svc10 = bool(re.search(r"10% service charge", note, re.I)) and row["price_basis"] == "dine_in"
    gst = row["gst_inclusive"] == "no"
    unrated = (not svc10 and bool(re.search(r"service charge", note, re.I))
               and not re.search(r"no service charge", note, re.I))
    return svc10, gst, unrated


def final_price(row):
    svc10, gst, unrated = charges(row)
    out = dict(row)
    if svc10 or gst:
        price = Decimal(row["price_sgd"])
        if svc10:
            price *= Decimal("1.10")
        if gst:
            price *= Decimal("1.09")
        out["price_sgd"] = str(price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        plus = ", ".join(p for p, on in (("service charge 10%", svc10), ("GST 9%", gst)) if on)
        out["price_note"] += f"; listed: {row['price_sgd']}; plus: {plus}"
        out["gst_inclusive"] = "yes"
    if unrated:
        out["price_note"] += "; service charge stated without a rate, not added"
    return out


# --------------------------------------- clean_wave3_tail.py: steps 2 to 6, per row
PROPER = re.compile(
    r"^(single|regular|reg|lrg|large|small|medium|tall|short|whole|half|full|slice|double|unspecified"
    r"|bottle|glass|pot|bulk|cake|pastry|(box|pack)_of_\d+|\d+(\.\d+)?_inch"
    r"|\d+(\.\d+)?(g|kg|ml|l|oz|cm)|serves_\d+(-\d+)?|\d+_pcs|(single|double)_scoop"
    r"|\d+_cupcakes?|\d+_tier)$")
# step 3, the categories a name alone can decide. The others need a Shopify
# product page, and no held row comes from one (checked below).
NOT_MENU = [
    ("add-on, option set or template",
     r"\badd[- ]?ons?\b|^\W*(?:additional|additonal|optional)\b|do not delete|^test product"),
    ("surcharge", r"surcharge|monthly specials"),
    ("topper, candle or card", r"\btoppers?\b|\bcandles?\b"),
    ("merchandise or decor",
     r"handwash|bucket hat|chawan|whisking set|sipper jar|crochet|disposable cake stand|kraft box"),
    ("gift or care pack", r"\bgifts?\b|\bhampers?\b|care pack|goodie bag|voucher"),
]
NOT_SINGLE_SIZE = re.compile(r"^((box|pack)_of_\d+|\d+_pcs|whole|half|\d+(\.\d+)?_inch|\d+_tier"
                             r"|serves_.*|\d+_cupcakes|bulk|\d+(\.\d+)?(kg|l)|[5-9]\d\dg|[6-9]\d\dml)$")
NOT_SINGLE_NAME = (r"\bwhole\b|\bbox(?:es)?\b|platter|bento|\bsets?\b|bundle|\bcakes?\b|\bloaf\b"
                   r"|\b\d+\s?(?:pcs?|pieces)\b|\bdozen\b|\bparty\b|\btray\b|\bassorted\b|\b\d+\s?pax\b")
SEASONAL = (r"halloween|christmas|xmas|\bcny\b|chinese new year|lunar new year|mid-autumn"
            r"|mooncake|mother[’']?s day|father[’']?s day|valentine|easter|deepavali")
# rows the tail rules would touch are not guessed at here: they stay held
tail_hits = Counter()


def tail(row):
    """The token clean_wave3_tail.py would add to this row, or None."""
    name, note0 = row["menu_wording"], row["price_note"]
    if not PROPER.match(row["size"]):
        return "size outside the vocabulary"
    if re.search(r"/products/|option-set|template", row["source_url"], re.I):
        return "Shopify product page"
    for label, pattern in NOT_MENU:
        if re.search(pattern, name, re.I):
            return f"menu: no ({label})"
    if float(row["price_sgd"]) < 1.5 and re.search(r"special", name, re.I):
        return "menu: no (surcharge)"
    if re.search(r"\bcards?\b|\bjars?\b|\b(?:jams?|powder|beans|spreads?)\b", name, re.I):
        return "menu: no, to be checked by hand"
    if row["item_canonical"] in NINE and (NOT_SINGLE_SIZE.match(row["size"])
                                          or re.search(NOT_SINGLE_NAME, name, re.I)):
        return "not single serve"
    if re.search(SEASONAL, name, re.I) and "availability: seasonal" not in note0:
        return "seasonal: by name"
    return None


# ------------------------------------------------------------------- the rows
_, _, review = read("review_merge.csv")
_, _, wave = read("menu_items_wave3.csv")
menu_text, menu_fields, menu = read("menu_items_all.csv")
newline = "\r\n" if "\r\n" in menu_text else "\n"

approved = [r for r in review if r["approve"].strip().lower() == "yes"]
on_file = {(m["shop_id"], m["menu_wording"], m["size"], m["hot_or_iced"], m["source_url"],
            m["observed_date"]) for m in menu}

new, already, held = [], 0, []
for r in approved:
    hits = [w for w in wave
            if (w["shop_id"], w["item_canonical"], w["menu_wording"], w["size"], w["hot_or_iced"],
                w["source_url"]) == (r["shop_id"], r["item_canonical"], r["menu_wording"],
                                     r["size"], r["hot_or_iced"], r["source_url"])
            and float(w["price_sgd"]) == float(r["listed_price"])]
    assert len(hits) == 1, f"{len(hits)} wave 3 rows match {r['shop_id']} {r['menu_wording']}"
    w = hits[0]
    out = final_price(w)
    assert float(out["price_sgd"]) == float(r["price_after_charges"]), \
        f"price after charges differs for {r['shop_id']} {r['menu_wording']}"
    if (w["shop_id"], w["menu_wording"], w["size"], w["hot_or_iced"], w["source_url"],
            w["observed_date"]) in on_file:
        already += 1
        continue
    why = tail(w)
    if why:
        tail_hits[why] += 1
        held.append((r, why))
        continue
    if "service charge stated without a rate, not added" in w["price_note"] + out["price_note"]:
        out["price_note"] += "; service charge rate unknown"      # step 6
        tail_hits["service charge rate unknown (merged, never counted)"] += 1
    new.append(out)

if new:
    assert menu_text.endswith(newline), "menu_items_all.csv does not end with a line break"
    with open(FOLDER / "menu_items_all.csv", "a", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=menu_fields, lineterminator=newline,
                       extrasaction="ignore").writerows(new)

rule("REVIEW ROWS")
print(f"  in review_merge.csv {len(review)}; approved {len(approved)}; "
      f"already on file {already}; appended {len(new)}; still held {len(held)}")
for r, why in held:
    print(f"     held: {r['shop_id']}: {r['menu_wording']}  ({why})")
print(f"  tail rules that applied: {dict(tail_hits) or 'none'}")
conv = [m for m in new if "; listed: " in m["price_note"]]
print(f"  appended rows with charges added: {len(conv)}  "
      f"{dict(Counter(m['price_note'].split('; plus: ')[1].split(';')[0] for m in conv))}")
print(f"  appended rows by item: {dict(Counter(m['item_canonical'] for m in new))}")
print(f"  shops: {sorted({m['shop_id'] for m in new})}")
print(f"  menu_items_all.csv now {len(menu) + len(new)} rows")
if not new and not already:
    sys.exit("nothing approved")
