#!/usr/bin/env python3
"""Clean the long tail of menu_items_all.csv after the wave 3 merge. Run this
after merge_wave3.py, then build_site_data.py.

Reads:  the committed (git HEAD) menu_items_all.csv and launches_clean.csv, so
        a second run gives the same result; verification_wave3.csv;
        wave3_parts/shopify_products/<domain>.<page>.json, each store's
        /products.json, fetched once if the file is not there.
Writes: menu_items_all.csv, launches_clean.csv, sample_wave3_clean.csv

No row is deleted. Every change is by rule and leaves a token in price_note:
  size was: <old>              the size before step 1 or 2 changed it
  milk: <choice>               a milk choice that had been stored as the size
  menu: no (<category>)        not a menu item; the build never counts it
  was: <item>, not single serve  a 9-item tag moved off a cake, box or set
  seasonal: by name            seasonal by its name; the build never counts it
  service charge rate unknown  the menu adds a service charge of unstated size
"""
import json
import random
import re
import subprocess
import sys
from collections import Counter
from io import StringIO
from pathlib import Path

import pandas as pd

FOLDER = Path(__file__).resolve().parent
SHOPIFY = FOLDER / "wave3_parts" / "shopify_products"
SAMPLE_SEED = 20261005
NINE = ["croissant_plain", "flat_white", "latte", "matcha_latte", "cappuccino",
        "americano", "espresso", "croissant_filled", "kouign_amann"]


def rule(t):
    print(f"\n{t}\n" + "=" * len(t))


def committed(name):
    out = subprocess.run(["git", "show", f"HEAD:{name}"], cwd=FOLDER, check=True,
                         capture_output=True).stdout.decode("utf-8")
    return pd.read_csv(StringIO(out, newline=""), dtype=str, keep_default_na=False), \
        "\r\n" if "\r\n" in out else "\n"


def examples(mask, n=3):
    rows = m[mask].drop_duplicates("menu_wording").head(n)
    for r in rows.itertuples():
        print(f"       {r.shop_id}: {r.menu_wording[:60]}  ${r.price_sgd}  [{r.size}]")


def add_note(mask, token):
    m.loc[mask, "price_note"] = [f"{n}; {token}" if n else token
                                 for n in m.loc[mask, "price_note"]]


m, menu_nl = committed("menu_items_all.csv")
if m.price_note.str.contains("menu: no").any():
    sys.exit("the long tail is already cleaned in the last commit; nothing to do")
size_before = m["size"].copy()
name = m.menu_wording
note0 = m.price_note.copy()


# ------------------------------------------------------------- size reading
PROPER = re.compile(
    r"^(single|regular|reg|lrg|large|small|medium|tall|short|whole|half|full|slice|double|unspecified"
    r"|bottle|glass|pot|bulk|cake|pastry|(box|pack)_of_\d+|\d+(\.\d+)?_inch"
    r"|\d+(\.\d+)?(g|kg|ml|l|oz|cm)|serves_\d+(-\d+)?|\d+_pcs|(single|double)_scoop"
    r"|\d+_cupcakes?|\d+_tier)$")
SIZE_WORDS = ["slice", "whole", "half", "single", "regular", "large", "small", "medium", "full"]
SIZE_OPTION = re.compile(r"size|quantity|pieces|no\. of|number of|serving|portion", re.I)


def num(text):
    return text.rstrip("0").rstrip(".") if "." in text else text


def stated_size(text, words=False, bare=False):
    """The size a piece of option text clearly states, or None."""
    t = re.sub(r"[“”″]", '"', text.lower()).replace("（", "(")
    g = re.search(r"(\d+)\s*tier", t)
    if g and int(g.group(1)) >= 2:
        return f"{g.group(1)}_tier"
    g = re.search(r"(?:box|set|pack) of (\d+)|(\d+)\s*(?:pcs?|pieces?|cupcakes?)\b", t) or \
        (re.fullmatch(r"\s*(\d+)\s*", t) if bare else None)
    if g:
        n = int(next(x for x in g.groups() if x))
        return "single" if n == 1 else f"box_of_{n}"
    g = re.search(r"(\d+(?:\.\d+)?)\s*(?:\"|-?inch)", t)
    if g:
        return f"{num(g.group(1))}_inch"
    g = re.search(r"(\d+(?:\.\d+)?)\s*(cm|kg|g|mls?|l|oz)\b", t)
    if g:
        return num(g.group(1)) + g.group(2).replace("mls", "ml")
    if words:
        for w in SIZE_WORDS:
            if re.search(rf"\b{w}\b", t):
                return w
    return None


# -------------------------------------------------- step 1: Shopify variants
SHOP_URL = re.compile(r"https?://([^/]+)/products/([^?#/]+)(?:\?variant=(\d+))?")
parts = m.source_url.str.extract(SHOP_URL)
SHOPIFY.mkdir(parents=True, exist_ok=True)
by_handle, by_id = {}, {}
for dom in sorted(parts[0].dropna().unique()):
    page = 1
    while True:
        path = SHOPIFY / f"{dom}.{page}.json"
        if not path.exists():
            subprocess.run(["curl", "-sL", "-m", "60", "-A", "Mozilla/5.0", "-o", str(path),
                            f"https://{dom}/products.json?limit=250&page={page}"], check=True)
        products = json.loads(path.read_text())["products"]
        for p in products:
            by_handle[(dom, p["handle"])] = p
            for v in p["variants"]:
                by_id[str(v["id"])] = (p, v)
        if len(products) < 250:
            break
        page += 1

matched = Counter()
product_type = pd.Series("", index=m.index)
option_names = pd.Series("", index=m.index)
is_shopify = parts[0].notna()
milk_note = {}
for i in m.index[is_shopify]:
    dom, handle, vid = parts.loc[i]
    listed = re.search(r"listed: ([\d.]+)", m.at[i, "price_note"])
    price = float(listed.group(1) if listed else m.at[i, "price_sgd"])
    if isinstance(vid, str) and vid in by_id:
        p, v = by_id[vid]
        how = "variant id"
    else:
        p = by_handle.get((dom, handle))
        same = [x for x in (p["variants"] if p else [])
                if x["title"] and abs(float(x["price"]) - price) < 0.005]
        if len(same) != 1:
            matched["no single variant with this title and price"] += 1
            continue
        v, how = same[0], "title and price"
    if abs(float(v["price"]) - price) >= 0.005:
        matched[f"{how}, but today's price differs"] += 1
        continue
    matched[how] += 1
    product_type[i] = p["product_type"]
    options = [(o["name"], str(val)) for o, val in
               zip(p["options"], (v["option1"], v["option2"], v["option3"]))
               if val and val != "Default Title"]
    option_names[i] = "|".join(n for n, _ in options)
    milk = [val for n, val in options if "milk" in n.lower() or "milk" in val.lower()]
    if milk:
        milk_note[i] = "; ".join(milk)
    sized = [(n, val) for n, val in options if SIZE_OPTION.search(n)]
    rest = [(n, val) for n, val in options if not SIZE_OPTION.search(n) and val not in milk]
    new = next((s for s in [stated_size(val, words=True, bare=True) for _, val in sized]
                + [stated_size(val) for _, val in rest] if s), None)
    if new:
        m.at[i, "size"] = new
    elif options and not PROPER.match(m.at[i, "size"]):
        m.at[i, "size"] = "unspecified"         # the options state no size at all

changed1 = is_shopify & (m["size"] != size_before)
rule("STEP 1: SHOPIFY SIZES")
print(f"  Shopify rows {is_shopify.sum()} on {parts[0].nunique()} stores; matched: {dict(matched)}")
print(f"  rows whose size changed: {changed1.sum()}")
print(f"     to the size the variant states : {(changed1 & (m['size'] != 'unspecified')).sum()}")
print(f"     to unspecified (options state no size): {(changed1 & (m['size'] == 'unspecified')).sum()}")
wrong = changed1 & size_before.str.match(PROPER) & (m["size"] != "unspecified")
print(f"     of the first kind, rows that had a real but wrong size: {wrong.sum()}")
moves = Counter(zip(size_before[wrong], m["size"][wrong])).most_common(8)
print("     commonest: " + ", ".join(f"{a}->{b} x{n}" for (a, b), n in moves))
r = m[m.menu_wording.str.startswith("Cocomelon Cupcakes - Pink") & (m.price_sgd == "255.00")].iloc[0]
print(f"  check: {r.menu_wording} ${r.price_sgd} is now {r['size']}")


# ------------------------------------------------- step 2: nonsense sizes
bad = ~m["size"].str.match(PROPER)
for i in m.index[bad]:
    old = m.at[i, "size"]
    text = re.sub(r"(?<=\d) (?=\d{3}(?:ml|g)\b)", "", old.replace("_", " "))
    if "milk" in text:
        milk_note.setdefault(i, text)
    first = text.split(" ")[0]
    m.at[i, "size"] = (stated_size(text) or (first if first in SIZE_WORDS else None)
                       or "unspecified")
changed2 = bad & (m["size"] != size_before)
rule("STEP 2: NONSENSE SIZES (rows step 1 did not already settle)")
print(f"  rows changed: {changed2.sum()}; to a stated size {(changed2 & (m['size'] != 'unspecified')).sum()}"
      f", to unspecified {(changed2 & (m['size'] == 'unspecified')).sum()}")
print("  " + ", ".join(f"{a}->{b} x{n}" for (a, b), n in
                       Counter(zip(size_before[changed2], m["size"][changed2])).most_common(12)))
print(f"  sizes still outside the vocabulary: {(~m['size'].str.match(PROPER)).sum()}")
changed = m["size"] != size_before
m.loc[changed, "price_note"] = [f"{n}; size was: {s}" if n else f"size was: {s}"
                                for n, s in zip(m.price_note[changed], size_before[changed])]
for i, milk in milk_note.items():
    add_note(m.index == i, f"milk: {milk}")
print(f"  milk choices moved into price_note: {len(milk_note)}")


# ------------------------------------------------- step 3: not menu items
def has(pattern):
    return name.str.contains(pattern, case=False, regex=True)


price = m.price_sgd.astype(float)
core = name.str.replace(r"\s*\([^)]*\)\s*$", "", regex=True).str.split(" - ").str[0].str.strip()
nylon = parts[0].fillna("").eq("www.nyloncoffee.sg")       # a roaster's bean and gear shop
NYLON_GEAR = r"xpods|carafe|filter|kimtech|cleaner"
CATEGORIES = [
    ("add-on, option set or template",
     m.source_url.str.contains("option-set|template", case=False)
     | has(r"\badd[- ]?ons?\b|^\W*(?:additional|additonal|optional)\b|do not delete|^test product")),
    ("surcharge",
     has(r"surcharge|monthly specials") | ((price < 1.5) & has(r"special"))),
    ("topper, candle or card",
     has(r"\btoppers?\b|\bcandles?\b")
     | (has(r"\bcards?\b") & ~has(r"cake|cupcake|cookie|\bbox\b"))),
    ("merchandise or decor",
     has(r"handwash|bucket hat|chawan|whisking set|sipper jar|crochet|disposable cake stand"
         r"|kraft box")
     | option_names.str.contains("Ticket type|Color")
     | product_type.isin(["hat", "Donation", "Superfood Supplement"])
     | (nylon & has(NYLON_GEAR))),
    ("packaged groceries",
     product_type.isin(["Groceries", "Essentials"])
     | (nylon & ~has(NYLON_GEAR))
     | core.str.contains(r"\b(?:jams?|powder|beans)$", case=False) & ~has(r"baked beans|\+|&|\bwith\b")
     | core.str.contains(r"^spreads?$|\b(?:kaya|nut|butter|chocolate) spreads?$", case=False)
     | (has(r"\bjars?\b") & ~has(r"\bin (?:a )?jar\b"))),
    ("gift or care pack",
     has(r"\bgifts?\b|\bhampers?\b|care pack|goodie bag|voucher")
     | product_type.isin(["Gifts", "Gift", "Hamper"])),
]
non_menu = pd.Series("", index=m.index)
for label, mask in CATEGORIES:
    non_menu[mask & (non_menu == "")] = label
rule("STEP 3: NOT MENU ITEMS")
print(f"  rows marked 'menu: no': {(non_menu != '').sum()} of {len(m)}"
      f"  ({m[non_menu != ''].shop_id.nunique()} shops)")
for label, _ in CATEGORIES:
    print(f"  {(non_menu == label).sum():>5}  {label}")
    examples(non_menu == label)
for label, _ in CATEGORIES:
    add_note(non_menu == label, f"menu: no ({label})")
low = (price < 1.5) & (non_menu == "")
print(f"  rows under $1.50 left as menu items: {low.sum()}")
examples(low, 5)


# ------------------------------------------ step 4: nine items, single serve
NOT_SINGLE_SIZE = re.compile(r"^((box|pack)_of_\d+|\d+_pcs|whole|half|\d+(\.\d+)?_inch|\d+_tier"
                             r"|serves_.*|\d+_cupcakes|bulk|\d+(\.\d+)?(kg|l)|[5-9]\d\dg|[6-9]\d\dml)$")
NOT_SINGLE_NAME = (r"\bwhole\b|\bbox(?:es)?\b|platter|bento|\bsets?\b|bundle|\bcakes?\b|\bloaf\b"
                   r"|\b\d+\s?(?:pcs?|pieces)\b|\bdozen\b|\bparty\b|\btray\b|\bassorted\b|\b\d+\s?pax\b")
nine = m.item_canonical.isin(NINE)
not_single = nine & (m["size"].str.match(NOT_SINGLE_SIZE) | has(NOT_SINGLE_NAME))
rule("STEP 4: NINE-ITEM TAGS, SINGLE SERVE ONLY")
print(f"  rows moved to 'other': {not_single.sum()}  "
      f"{dict(Counter(m.item_canonical[not_single]))}")
for r in m[not_single].drop_duplicates(["shop_id", "menu_wording", "size"]).itertuples():
    print(f"       {r.item_canonical:<16} {r.shop_id}: {r.menu_wording[:52]}  ${r.price_sgd}  [{r.size}]")
for item in NINE:
    add_note(not_single & (m.item_canonical == item), f"was: {item}, not single serve")
m.loc[not_single, "item_canonical"] = "other"


# ------------------------------------------------- step 5: seasonal by name
SEASONAL = (r"halloween|christmas|xmas|\bcny\b|chinese new year|lunar new year|mid-autumn"
            r"|mooncake|mother[’']?s day|father[’']?s day|valentine|easter|deepavali")
seasonal_name = has(SEASONAL)
page_marked = note0.str.contains("availability: seasonal")
seasonal_new = seasonal_name & ~page_marked
add_note(seasonal_new, "seasonal: by name")
launches, launch_nl = committed("launches_clean.csv")
launch_new = launches.item_name.str.contains(SEASONAL, case=False) & (launches.is_seasonal != "yes")
rule("STEP 5: SEASONAL BY NAME")
print(f"  menu rows with a seasonal name {seasonal_name.sum()}; the page already marked "
      f"{(seasonal_name & page_marked).sum()}; newly marked {seasonal_new.sum()}")
print(f"     of the newly marked, one of the 9 compared items: "
      f"{(seasonal_new & m.item_canonical.isin(NINE)).sum()}")
examples(seasonal_new)
print(f"  launches newly marked seasonal: {launch_new.sum()} of {len(launches)}")
for r in launches[launch_new].itertuples():
    print(f"       {r.shop_id_or_name}: {r.item_name[:60]}")
launches.loc[launch_new, "is_seasonal"] = "yes"


# ------------------------------------------- step 6: unrated service charge
unrated = note0.str.contains("service charge stated without a rate, not added")
add_note(unrated, "service charge rate unknown")
rule("STEP 6: SERVICE CHARGE WITHOUT A RATE")
print(f"  rows marked: {unrated.sum()}  shops: {sorted(m[unrated].shop_id.unique())}")
print(f"     of them one of the 9 compared items: "
      f"{dict(Counter(m.item_canonical[unrated & m.item_canonical.isin(NINE)]))}")


# ------------------------------------------------------------------- write
m.to_csv(FOLDER / "menu_items_all.csv", index=False, lineterminator=menu_nl)
launches.to_csv(FOLDER / "launches_clean.csv", index=False, lineterminator=launch_nl)


# ------------------------------------------------------- step 8: new sample
ver = pd.read_csv(FOLDER / "verification_wave3.csv", dtype=str, keep_default_na=False)
ver = ver[ver.check == "price"]
passed = ver.groupby("row_key").status.agg(lambda s: s.str.startswith("passed").all())
section = note0.str.extract(r"section: ([^;]+)")[0]
key = (m.shop_id + "|" + m.menu_wording + "|" + size_before + "|" + m.hot_or_iced + "|"
       + m.observed_date + ("|" + section).fillna(""))          # verify_wave3.py's row key
pool = m[key.map(passed).fillna(False).astype(bool) & (non_menu == "")]
order = list(pool.index)
random.Random(SAMPLE_SEED).shuffle(order)
taken, per_shop = [], Counter()
for i in order:
    if per_shop[m.at[i, "shop_id"]] < 3 and len(taken) < 40:
        taken.append(i)
        per_shop[m.at[i, "shop_id"]] += 1
sample = m.loc[taken, ["shop_id", "menu_wording", "size", "price_sgd", "source_url"]] \
    .rename(columns={"price_sgd": "expected_price"})
sample.to_csv(FOLDER / "sample_wave3_clean.csv", index=False, lineterminator="\r\n")
rule("STEP 8: NEW SAMPLE")
print(f"  sample_wave3_clean.csv: {len(sample)} rows from {sample.shop_id.nunique()} shops, "
      f"drawn from {len(pool)} passed menu rows, seed {SAMPLE_SEED}")
print(f"  menu_items_all.csv: {len(m)} rows (none deleted)")
