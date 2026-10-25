#!/usr/bin/env python3
"""Write counter/menu_dates.json: every price we know for each menu row, with its channel and day.

For each shop_id in menu_items_all.csv, a map from a row's key (its name and
variant, normalised exactly as Counter's diffKey() does in counter/index.html)
to a short list of [price, price_basis, observed_date], one for each price and
basis we have recorded, newest first. "_shop" is the latest observed_date of
any row of that shop.

The price is the one printed on the menu, before any service charge or GST:
where our data added charges, the printed price is the "listed: <price>" in the
row's price_note. Only rows our live site counts are used (an exact price, on
sale), the same rule as build_counter_data.py's counts(). A row with no
price_basis is kept with basis "": Counter never compares it.

Counter's What changed compares a menu that was just read only with prices of
the same price list: dine_in, takeaway or online_order.

Input, read only: menu_items_all.csv. Output: counter/menu_dates.json.
No network calls. Prints counts only.

The normalisation below must stay the same as diffKey() and menuWords() in counter/index.html.
"""

import csv
import glob
import json
import re
import unicodedata
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
SOURCE = FOLDER / "menu_items_all.csv"
OUT = FOLDER / "counter" / "menu_dates.json"

# ---------------------------------------- the same rules as diffKey() in counter/index.html
# short size words said in full
SYNONYMS = {"lrg": "large", "lg": "large", "sm": "small", "med": "medium", "reg": "regular"}
# size and temperature words: they belong to the variant, wherever they are written
SIZE_TEMP = {"hot", "iced", "cold", "regular", "small", "medium", "large", "8oz", "12oz", "16oz"}
# words no menu needs to say: our menu files leave them out of the variant, so they are dropped
PLAIN = {"hot", "regular", "single", "unspecified"}
# "hot" stays part of the name before these words: hot chocolate is not chocolate
HOT_KEEP = {"chocolate", "choc", "cocoa", "dog", "cross", "sauce", "honey", "pot"}


def words(text):
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.category(c).startswith("M")).lower()
    t = t.replace("&", " and ")
    t = re.sub(r"['’]", "", t)
    t = re.sub(r"([0-9]+)\s*oz", r"\1oz", t)
    t = "".join(c if unicodedata.category(c)[0] in ("L", "N") else " " for c in t)
    out = []
    for w in t.split():
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(SYNONYMS.get(w, w))
    return out


def canon(name, variant):
    """(name words, variant words): size and temperature words moved to the variant, plain ones dropped."""
    nw, keep, moved = words(name), [], []
    for i, w in enumerate(nw):
        nxt = nw[i + 1] if i + 1 < len(nw) else ""
        if w in SIZE_TEMP and not (w == "hot" and nxt in HOT_KEEP):
            moved.append(w)
        else:
            keep.append(w)
    if not keep:                      # a name that is only size words stays as it is
        keep, moved = nw, []
    vw = sorted({w for w in words(variant) + moved if w not in PLAIN})
    return keep, vw


def diff_key(name, variant):
    n, v = canon(name, variant)
    return " ".join(n) + "|" + " ".join(v)
# ---------------------------------------- end of the shared rules


# copied from build_counter_data.py: the variant a menu file row carries
PLAIN_WORDS = {"regular", "single", "hot", "not_applicable", ""}

def variant_of(r):
    parts = [w for w in (r["size"].strip(), r["hot_or_iced"].strip()) if w.lower() not in PLAIN_WORDS]
    return ", ".join(p.replace("_", " ") for p in parts)


# copied from build_counter_data.py (which copies build_site_data.py): the rows the live site counts
NOT_COUNTED = ("menu: no", "seasonal: by name", "service charge rate unknown")

def counts(row):
    note = row.get("price_note", "")
    if any(mark in note for mark in NOT_COUNTED):
        return False
    return (row["price_confidence"] == "exact"
            and (note.startswith("availability: available") or "availability:" not in note))


LISTED = re.compile(r"listed:\s*([0-9]+(?:\.[0-9]+)?)")

def printed_price(r):
    """The price as the menu prints it: the listed price where charges were added, else the recorded price."""
    m = LISTED.search(r.get("price_note", ""))
    raw = m.group(1) if m else r["price_sgd"].strip()
    try:
        return round(float(raw), 2)
    except ValueError:
        return None


def main():
    dates, per_basis, from_listed, skipped = {}, {}, 0, 0
    with SOURCE.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            day = r["observed_date"].strip()
            if not day:
                continue
            shop = dates.setdefault(r["shop_id"], {"_shop": ""})
            shop["_shop"] = max(shop["_shop"], day)
            price = printed_price(r)
            k = diff_key(r["menu_wording"], variant_of(r))
            if k == "|" or price is None or not counts(r):
                skipped += 1
                continue
            basis = r["price_basis"].strip()
            from_listed += bool(LISTED.search(r.get("price_note", "")))
            per_basis[basis or "(no basis)"] = per_basis.get(basis or "(no basis)", 0) + 1
            known = shop.setdefault(k, [])
            same = next((e for e in known if e[0] == price and e[1] == basis), None)
            if same:
                same[2] = max(same[2], day)
            else:
                known.append([price, basis, day])
    for shop in dates.values():
        for k, known in shop.items():
            if k != "_shop":
                known.sort(key=lambda e: (e[2], e[1], e[0]), reverse=True)
    OUT.write_text(json.dumps(dates, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    rows = dated = 0
    for f in glob.glob(str(FOLDER / "counter" / "menus" / "*.json")):
        m = json.loads(Path(f).read_text(encoding="utf-8"))
        shop = dates.get(m["shop_id"], {})
        for r in m["rows"]:
            rows += 1
            dated += diff_key(r["name"], r["variant"]) in shop
    keys = sum(len(v) - 1 for v in dates.values())
    prices = sum(len(e) for v in dates.values() for k, e in v.items() if k != "_shop")
    print(f"shops: {len(dates)}")
    print(f"keys: {keys}")
    print(f"known prices (price, basis, day): {prices}")
    print(f"rows used, by basis: {dict(sorted(per_basis.items()))}")
    print(f"rows with no basis: {per_basis.get('(no basis)', 0)}")
    print(f"rows whose printed price came from 'listed:' (charges taken off): {from_listed}")
    print(f"rows left out (not counted by the live site, no price or no name): {skipped}")
    print(f"menus/*.json rows with a known price: {dated} of {rows}")
    print(f"written: {OUT.relative_to(FOLDER)} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
