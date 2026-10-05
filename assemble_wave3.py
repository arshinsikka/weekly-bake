#!/usr/bin/env python3
"""Wave 3: put the parts together and measure. No page is fetched here except
OneMap, for coordinates.

Reads:  wave3_parts/*/{shops,menu,launches,promos}.csv
        shops_all.csv, menu_items_all.csv, shops_geo.csv   (read only)
Writes: shops_wave3.csv, menu_items_wave3.csv, launches_wave3.csv,
        promos_wave3.csv, shop_sources_wave3.csv, shops_geo_wave3.csv,
        recheck_wave3.csv, counter_visits.csv

Never writes to shops_all.csv, menu_items_all.csv, launches.csv, promos.csv,
shops_geo.csv or site_data.json. Never runs build_site_data.py.
"""

import csv
import re
import statistics
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from wave3_lib import (DISTRICTS, LAUNCH_FIELDS, MENU_FIELDS, NINE, PARTS, PROMO_FIELDS,
                       SHOP_FIELDS, check_part, read_csv, write_csv)

FOLDER = Path(__file__).resolve().parent
MAP_ITEMS = ["croissant_plain", "flat_white", "latte", "matcha_latte"]
BASIS_PREFERENCE = ["takeaway", "dine_in", "online_order"]


def rule(t):
    print(f"\n{t}\n" + "=" * len(t))


def norm(text):
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def loose(text):
    """Order-free, plural-free token set, for 'Almond Croissant' vs 'Croissant, Almond'."""
    drop = {"the", "a", "of", "with", "and", "slice", "hot", "iced", "ice"}
    return frozenset(w.rstrip("s") for w in norm(text).split() if w not in drop)


def availability(row):
    m = re.match(r"availability: (\w+)", row["price_note"])
    return m.group(1) if m else ""


def on_menu_now(row):
    """A row the site may rank: exact price, in stock, not seasonal."""
    return row["price_confidence"] == "exact" and availability(row) == "available"


# =============================================================== 1. assemble
old_shops = {s["shop_id"]: s for s in read_csv(FOLDER / "shops_all.csv")}
old_menu = read_csv(FOLDER / "menu_items_all.csv")

shops, menu, launches, promos, part_of = {}, [], [], [], {}
problems = []
for d in sorted(p for p in PARTS.iterdir() if (p / "shops.csv").exists()):
    part = d.name
    problems += [f"{part}: {p}" for p in check_part(part)]
    for s in read_csv(d / "shops.csv"):
        if s["shop_id"] in shops:
            problems.append(f"{part}: shop_id {s['shop_id']} already written by "
                            f"{part_of[s['shop_id']]}")
            continue
        shops[s["shop_id"]] = s
        part_of[s["shop_id"]] = part
    menu += [m for m in read_csv(d / "menu.csv") if part_of.get(m["shop_id"]) == part]
    launches += read_csv(d / "launches.csv")
    promos += read_csv(d / "promos.csv")

for sid, s in shops.items():
    if s["group"] == "d" and sid in old_shops:
        problems.append(f"{part_of[sid]}: new shop_id {sid} collides with shops_all.csv")
    if s["group"] == "c" and sid not in old_shops:
        problems.append(f"{part_of[sid]}: group c shop_id {sid} is not in shops_all.csv")

# the same shop found by two district agents: same postal code and similar name
by_postal = defaultdict(list)
for s in list(shops.values()) + [o for k, o in old_shops.items() if k not in shops]:
    if s["postal_code"]:
        by_postal[s["postal_code"]].append(s)
possible_dupes = []
for postal, group in by_postal.items():
    for i, a in enumerate(group):
        for b in group[i + 1:]:
            if loose(a["shop_name"]) & loose(b["shop_name"]) - {"cafe", "coffee", "bakery"}:
                possible_dupes.append((postal, a["shop_id"], b["shop_id"]))

priced = Counter(m["shop_id"] for m in menu)
write_csv(FOLDER / "shops_wave3.csv", SHOP_FIELDS, shops.values())
write_csv(FOLDER / "menu_items_wave3.csv", MENU_FIELDS, menu)
write_csv(FOLDER / "launches_wave3.csv", LAUNCH_FIELDS, launches)
write_csv(FOLDER / "promos_wave3.csv", PROMO_FIELDS, promos)
write_csv(FOLDER / "shop_sources_wave3.csv",
          ["shop_id", "group", "part", "is_new", "address_source_url", "rows_captured",
           "no_price_reason"],
          [dict(shop_id=k, group=s["group"], part=part_of[k],
                is_new="no" if k in old_shops else "yes",
                address_source_url=s["address_source_url"], rows_captured=priced.get(k, 0),
                no_price_reason=s["no_price_reason"]) for k, s in shops.items()])


# ============================================================ 2. coordinates
def geocode():
    """Same rule as geocode_shops.py: OneMap, exact POSTAL match only, else blank."""
    from geocode_shops import lookup
    fields = ["shop_id", "postal_code", "lat", "lng", "match_status"]
    path = FOLDER / "shops_geo_wave3.csv"
    cache = {r["shop_id"]: r for r in read_csv(path)} if path.exists() else {}
    live = {r["shop_id"]: r for r in read_csv(FOLDER / "shops_geo.csv")}
    control = next((r["postal_code"] for r in live.values() if r["match_status"] == "matched"),
                   None)
    by_code, rows, failed = {}, [], []
    for sid, s in shops.items():
        postal = s["postal_code"].strip()
        for known in (cache.get(sid), live.get(sid)):
            if known and known["postal_code"] == postal and known["match_status"] == "matched":
                rows.append({k: known[k] for k in fields})
                break
        else:
            if postal and postal not in by_code:
                time.sleep(0.3)
                try:
                    by_code[postal] = lookup(postal, control)
                except Exception as err:                # not cached: retried next run
                    failed.append((sid, postal, str(err)[:80]))
                    continue
            found = by_code.get(postal)
            rows.append(dict(shop_id=sid, postal_code=postal,
                             lat=found[0] if found else "", lng=found[1] if found else "",
                             match_status="matched" if found else "unmatched"))
    write_csv(path, fields, rows)
    return {r["shop_id"]: r for r in rows}, failed


geo, geo_failed = geocode() if "--no-geocode" not in sys.argv else ({}, [])


# ================================================================ 3. recheck
today = defaultdict(list)
for m in menu:
    today[m["shop_id"]].append(m)


def classify(old):
    """confirmed / changed / not_found / no_page for one existing row."""
    rows = today.get(old["shop_id"], [])
    if not rows:
        return "no_page", None, ""
    price = float(old["price_sgd"])

    def pick(cands, method):
        same_temp = [c for c in cands if c["hot_or_iced"] == old["hot_or_iced"]] or cands
        hit = next((c for c in same_temp if abs(float(c["price_sgd"]) - price) < 0.005), None)
        if hit:
            return "confirmed", hit, method
        best = min(same_temp, key=lambda c: (c["price_basis"] != old["price_basis"],
                                             c["price_confidence"] != "exact",
                                             abs(float(c["price_sgd"]) - price)))
        return "changed", best, method

    exact = [c for c in rows if norm(c["menu_wording"]) == norm(old["menu_wording"])]
    if exact:
        return pick(exact, "same wording")
    near = [c for c in rows if loose(c["menu_wording"]) == loose(old["menu_wording"])
            and loose(old["menu_wording"])]
    if near:
        return pick(near, "same words, different order or plural")
    # the single-product drinks and the plain croissant: match on the item itself
    item = old["item_canonical"]
    if item in NINE and item not in ("croissant_filled", "kouign_amann"):
        cands = [c for c in rows if c["item_canonical"] == item
                 and c["hot_or_iced"] == old["hot_or_iced"] and c["size"] in ("single", "regular")]
        if cands:
            return pick(cands, f"same item ({item}), different wording")
    return "not_found", None, ""


recheck = []
for o in old_menu:
    if old_shops[o["shop_id"]]["menu_found"] != "yes":
        continue
    status, hit, method = classify(o)
    recheck.append(dict(
        shop_id=o["shop_id"], district=old_shops[o["shop_id"]]["district"],
        item_canonical=o["item_canonical"], menu_wording=o["menu_wording"], size=o["size"],
        hot_or_iced=o["hot_or_iced"], price_basis=o["price_basis"],
        source_tier=o["source_tier"], old_source_url=o["source_url"],
        old_price=o["price_sgd"], classification=status,
        today_price=hit["price_sgd"] if hit else "",
        today_wording=hit["menu_wording"] if hit else "",
        today_price_basis=hit["price_basis"] if hit else "",
        today_availability=availability(hit) if hit else "",
        today_price_confidence=hit["price_confidence"] if hit else "",
        today_url=hit["source_url"] if hit else "", match_method=method,
        note=shops.get(o["shop_id"], {}).get("no_price_reason", "") if status == "no_page" else ""))
write_csv(FOLDER / "recheck_wave3.csv", list(recheck[0]), recheck)


def live_pins(item):
    """The row behind each shop's pin: takeaway > dine_in > online_order, cheapest."""
    by = defaultdict(lambda: defaultdict(list))
    for r in recheck:
        if r["item_canonical"] == item:
            by[r["shop_id"]][r["price_basis"]].append(r)
    out = []
    for bases in by.values():
        basis = next((b for b in BASIS_PREFERENCE if bases.get(b)), sorted(bases)[0])
        out.append(min(bases[basis], key=lambda r: float(r["old_price"])))
    return out


def table(title, groups):
    rule(title)
    print(f"  {'item':<20}{'live':>6}{'confirmed':>11}{'changed':>9}{'not_found':>11}{'no_page':>9}")
    for name, rows in groups:
        c = Counter(r["classification"] for r in rows)
        print(f"  {name:<20}{len(rows):>6}{c['confirmed']:>11}{c['changed']:>9}"
              f"{c['not_found']:>11}{c['no_page']:>9}")


# ========================================================= 4. counter visits
def street_key(address):
    m = re.match(r"\s*(?:blk\s*)?(\d+)[a-z]?\s+(.*?)(?:\s+#.*)?$", address, re.I)
    return (norm(m.group(2)), int(m.group(1))) if m else (norm(address), 0)


every = dict(old_shops)
every.update(shops)                         # wave 3 rows win (own-site addresses)
visits = []
for sid, s in every.items():
    rows = today.get(sid, [])
    now = [r for r in rows if on_menu_now(r)]
    have = {r["item_canonical"] for r in now}
    if not rows:
        missing = list(MAP_ITEMS)
        why = "no allowed online prices: " + (s.get("no_price_reason") or
                                              "not re-captured in wave 3")
    else:
        espresso_bar = have & {"flat_white", "latte", "cappuccino", "americano", "espresso"}
        any_drink = any(r["format"] == "drink" for r in rows)
        viennoiserie = have & {"croissant_filled", "kouign_amann", "danish", "cruffin"} or any(
            "croissant" in r["menu_wording"].lower() for r in rows)
        missing, why = [], []
        if "croissant_plain" not in have and viennoiserie:
            missing.append("croissant_plain")
            why.append("sells croissants or viennoiserie online but no plain croissant price")
        if not any_drink:
            missing += ["flat_white", "latte"]
            why.append("online pages list food only; no drinks menu online")
        elif espresso_bar:
            for item in ("flat_white", "latte"):
                if item not in have:
                    missing.append(item)
            if missing and "espresso-based" not in " ".join(why):
                if {"flat_white", "latte"} & set(missing):
                    why.append("has an espresso menu online without this item")
        if "matcha_latte" not in have and any(
                re.search(r"matcha|hojicha", r["menu_wording"], re.I) and r["format"] == "drink"
                for r in rows):
            missing.append("matcha_latte")
            why.append("sells matcha or hojicha drinks online but no plain matcha latte price")
        why = "; ".join(why)
    if not missing:
        continue
    oc = s["outlet_count"].strip()
    visits.append(dict(
        shop_id=sid, shop_name=s["shop_name"], district=s["district"], address=s["address"],
        postal_code=s["postal_code"], outlet_count=oc, missing_map_items=", ".join(missing),
        reason=why, pitch_priority="yes" if oc.isdigit() and 2 <= int(oc) <= 9 else "no",
        group=s.get("group") or ("c" if s["menu_found"] == "yes" else "b"),
        online_rows_today=len(rows)))
visits.sort(key=lambda v: (v["district"], *street_key(v["address"])))
write_csv(FOLDER / "counter_visits.csv", list(visits[0]), visits)


# ================================================================== summary
rule("PARTS")
for part in sorted(set(part_of.values())):
    ids = [k for k, p in part_of.items() if p == part]
    print(f"  {part:<26} shops {len(ids):>3}  with prices {sum(1 for i in ids if priced.get(i)):>3}"
          f"  rows {sum(priced.get(i, 0) for i in ids):>5}")
print(f"  problems: {len(problems)}")
for p in problems[:60]:
    print("   !!", p)
print(f"  possible duplicate shops (same postal, similar name): {possible_dupes}")

rule("TOTALS")
g = Counter(s["group"] for s in shops.values())
print(f"  shops {len(shops)} by group {dict(g)}; new {sum(k not in old_shops for k in shops)}")
print(f"  menu rows {len(menu)}; launches {len(launches)}; promos {len(promos)}")
print(f"  availability {dict(Counter(availability(m) for m in menu))}")
print(f"  confidence {dict(Counter(m['price_confidence'] for m in menu))}")
print(f"  gst_inclusive {dict(Counter(m['gst_inclusive'] for m in menu))}")
print(f"  item_canonical {dict(Counter(m['item_canonical'] for m in menu))}")
print(f"  geocode: matched {sum(r['match_status'] == 'matched' for r in geo.values())} of "
      f"{len(geo)}; failed calls {geo_failed}")

table("RECHECK: the row behind each live pin / table entry",
      [(i, live_pins(i)) for i in MAP_ITEMS + [i for i in NINE if i not in MAP_ITEMS]])
table("RECHECK: every existing row", [
    ("all rows", recheck),
    ("the 9 items", [r for r in recheck if r["item_canonical"] in NINE]),
    ("tier 1 rows", [r for r in recheck if r["source_tier"] == "1"]),
    ("tier 2 and 3 rows", [r for r in recheck if r["source_tier"] in ("2", "3")])])
c_shops = [k for k, s in old_shops.items() if s["menu_found"] == "yes"]
print(f"  existing shops with a current menu: {len(c_shops)}; with a page today: "
      f"{sum(1 for k in c_shops if today.get(k))}; no page: "
      f"{sum(1 for k in c_shops if not today.get(k))}")

rule("GROUP D: new shops per district, and map pins gained")
new = [s for s in shops.values() if s["group"] == "d"]
print(f"  {'district':<36}{'new':>5}{'priced':>8}")
for dist in DISTRICTS:
    ds = [s for s in new if s["district"] == dist]
    print(f"  {dist:<36}{len(ds):>5}{sum(1 for s in ds if priced.get(s['shop_id'])):>8}")
print(f"  {'TOTAL':<36}{len(new):>5}{sum(1 for s in new if priced.get(s['shop_id'])):>8}")
for item in MAP_ITEMS:
    gain = {m["shop_id"] for m in menu if m["item_canonical"] == item and on_menu_now(m)
            and shops[m["shop_id"]]["group"] == "d"}
    located = [k for k in gain if geo.get(k, {}).get("match_status") == "matched"]
    ab = {m["shop_id"] for m in menu if m["item_canonical"] == item and on_menu_now(m)
          and shops[m["shop_id"]]["group"] in ("a", "b", "a+b") and m["shop_id"] not in
          {r["shop_id"] for r in recheck if r["item_canonical"] == item}}
    print(f"  {item:<18} group d shops with a usable price: {len(gain):>3} "
          f"(with coordinates: {len(located)});  groups a/b: {len(ab)}")

rule("COUNTER VISITS")
print(f"  shops on the list: {len(visits)}; pitch priority: "
      f"{sum(v['pitch_priority'] == 'yes' for v in visits)}")
for dist in DISTRICTS:
    ds = [v for v in visits if v["district"] == dist]
    print(f"  {dist:<36}{len(ds):>4}  pitch {sum(v['pitch_priority'] == 'yes' for v in ds):>3}"
          f"  no online prices {sum(v['online_rows_today'] == 0 for v in ds):>3}")
