#!/usr/bin/env python3
"""Write counter/menu_dates.json: the day each menu row was last checked.

For each shop_id in menu_items_all.csv, a map from a row's key (its name and
variant, normalised exactly as Counter's diffKey() does in counter/index.html)
to the latest observed_date of that key, plus "_shop": the latest
observed_date of any row of that shop. Counter's What changed panel uses it to
say when a price was last checked.

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


def main():
    dates = {}
    with SOURCE.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            day = r["observed_date"].strip()
            if not day:
                continue
            shop = dates.setdefault(r["shop_id"], {"_shop": ""})
            k = diff_key(r["menu_wording"], variant_of(r))
            if k != "|":
                shop[k] = max(shop.get(k, ""), day)
            shop["_shop"] = max(shop["_shop"], day)
    OUT.write_text(json.dumps(dates, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    rows = dated = 0
    for f in glob.glob(str(FOLDER / "counter" / "menus" / "*.json")):
        m = json.loads(Path(f).read_text(encoding="utf-8"))
        shop = dates.get(m["shop_id"], {})
        for r in m["rows"]:
            rows += 1
            dated += diff_key(r["name"], r["variant"]) in shop
    keys = sum(len(v) - 1 for v in dates.values())
    print(f"shops: {len(dates)}")
    print(f"keys: {keys}")
    print(f"menus/*.json rows with their own date: {dated} of {rows}")
    print(f"written: {OUT.relative_to(FOLDER)} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
