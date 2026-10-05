#!/usr/bin/env python3
"""Merge wave 3 into the site data. Run ocr_wave3.py first, then this, then
build_site_data.py.

Reads:  menu_items_wave3.csv, shops_wave3.csv, shop_sources_wave3.csv,
        launches_wave3.csv, promos_wave3.csv, recheck_wave3.csv,
        review_wave3.csv, verification_wave3.csv, wave3_parts/ocr_cache/
        and the committed (git HEAD) copies of the files it appends to, so a
        second run gives the same result instead of appending twice.
Writes: menu_items_all.csv, shops_all.csv, launches.csv, launches_clean.csv,
        promos.csv, promos_clean.csv   (rows appended; see rule 3 for removals)
        verification_wave3.csv         (status passed_ocr on image rows)
        review_merge.csv               (failed-OCR rows waiting for a person)
        merge_held_wave3.csv           (every wave 3 row not merged, and why)

Rules, in the order they are applied to a wave 3 row:
  failed verification                      -> not merged
  Little Rogue Coffee wine list            -> not merged
  needs_review for a reason other than an
  image price, at an existing shop         -> not merged
  image/PDF price, OCR passes              -> merged
  image/PDF price, OCR fails               -> merged unless it is one of the 9
                                              compared items, an outlier, or at a
                                              signup or pitched shop (groups a, b);
                                              those go to review_merge.csv
  recheck "changed" and not like for like  -> not merged; the existing row stays
Service charge and GST the menu states are added before any comparison.
"""
import csv
import difflib
import hashlib
import io
import json
import re
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
CACHE = FOLDER / "wave3_parts" / "ocr_cache"
FIRST_SEEN = "2026-10-04"
NINE = ["croissant_plain", "flat_white", "latte", "matcha_latte", "cappuccino",
        "americano", "espresso", "croissant_filled", "kouign_amann"]
# one product per shop, whatever the menu calls it; every other item is told
# apart by its wording. build_site_data.py uses the same key for "latest row".
SINGLE_PRODUCT = set(NINE) - {"croissant_filled", "kouign_amann"}
IMAGE_REASON = "price is inside an image or PDF"
SIGNUP_OR_PITCHED = {"a", "a+b", "a (re-check)", "b"}
SHOP_STATUS = {"pelle_and_pepe": "temporarily_closed",
               "windowsill_pies_horne_rd": "closed",
               "asylum_coffeehouse": "removed"}


def rule(t):
    print(f"\n{t}\n" + "=" * len(t))


def parse(text):
    r = csv.DictReader(io.StringIO(text, newline=""))
    return r.fieldnames[:], list(r)


def read(name):
    return parse((FOLDER / name).read_text(encoding="utf-8"))


def committed(name):
    """The file as last committed: the state before this merge."""
    out = subprocess.run(["git", "show", f"HEAD:{name}"], cwd=FOLDER, check=True,
                         capture_output=True).stdout.decode("utf-8")
    return out, *parse(out)


def write(name, fields, rows, newline):
    with open(FOLDER / name, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator=newline, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def newline_of(text):
    return "\r\n" if "\r\n" in text else "\n"


def norm(text):
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def item_key(r):
    k = (r["shop_id"], r["item_canonical"], r["size"], r["hot_or_iced"], r["price_basis"])
    return k if r["item_canonical"] in SINGLE_PRODUCT else k + (norm(r["menu_wording"]),)


def row_key(r):
    """Same key verify_wave3.py writes to verification_wave3.csv."""
    sec = re.search(r"section: ([^;]+)", r["price_note"])
    return "|".join([r["shop_id"], r["menu_wording"], r["size"], r["hot_or_iced"],
                     r["observed_date"]] + ([sec.group(1)] if sec else []))


# ----------------------------------------------------------------- baseline
menu_text, menu_fields, old_menu = committed("menu_items_all.csv")
shops_text, shop_fields, old_shops = committed("shops_all.csv")
launch_text, launch_fields, old_launches = committed("launches.csv")
lclean_text, lclean_fields, old_lclean = committed("launches_clean.csv")
promo_text, promo_fields, old_promos = committed("promos.csv")
pclean_text, pclean_fields, old_pclean = committed("promos_clean.csv")
if "first_seen" in launch_fields:
    sys.exit("wave 3 is already merged in the last commit; nothing to do")

_, wave = read("menu_items_wave3.csv")
_, wave_shops = read("shops_wave3.csv")
_, sources = read("shop_sources_wave3.csv")
_, recheck = read("recheck_wave3.csv")
_, review = read("review_wave3.csv")
ver_fields, ver = read("verification_wave3.csv")

group = {s["shop_id"]: s["group"] for s in sources}
outlier = {r["row_key"] for r in review if r["check"] == "price" and r["outlier"]}
ver_price = [v for v in ver if v["check"] == "price"]
assert len(ver_price) == len(wave) and all(
    v["row_key"] == row_key(w) for v, w in zip(ver_price, wave)), "verification out of step"
assert len(recheck) == len(old_menu) and all(
    r["shop_id"] == o["shop_id"] and r["menu_wording"] == o["menu_wording"]
    for r, o in zip(recheck, old_menu)), "recheck out of step with menu_items_all.csv"


# ------------------------------------------------- rule 6: OCR on image rows
OCR_DROP = {"hot", "iced", "ice", "the", "a", "of", "with", "and", "w", "or", "in", "on"}


def tokens(text):
    return re.findall(r"[a-z0-9]+", norm(text))


def listed_pattern(price):
    """5.50 matches '5.50' and '5.5'; 16.00 matches '16', '16.0' and '16.00'."""
    whole, cents = f"{float(price):.2f}".split(".")
    if cents == "00":
        tail = r"(?:\.0{1,2})?"
    elif cents.endswith("0"):
        tail = rf"\.{cents[0]}0?"
    else:
        tail = rf"\.{cents}"
    return re.compile(rf"(?<![\d.,]){whole}{tail}(?!\d)(?!\.\d)")


_ocr = {}


def ocr_units(url):
    """Every OCR line, plus every paragraph or block of up to 8 lines, as text."""
    if url not in _ocr:
        path = CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".ocr.json")
        data = json.loads(path.read_text())
        units, blocks = [], defaultdict(list)
        for l in data["lines"]:
            units.append(l["text"])
            blocks[(l["page"], l["psm"], l["block"], l["par"])].append(l["text"])
            blocks[(l["page"], l["psm"], l["block"])].append(l["text"])
        units += [" ".join(v) for v in blocks.values() if 1 < len(v) <= 8]
        _ocr[url] = ([(u.replace(",", ".").replace("$", ""), sorted(set(tokens(u))))
                      for u in units], data["file"])
    return _ocr[url]


def ocr_passes(url, wording, price):
    need = [w for w in tokens(wording) if w not in OCR_DROP]
    if not need:
        return False
    pat = listed_pattern(price)

    def has(word, have):
        return word in have or (len(word) >= 4 and
                                bool(difflib.get_close_matches(word, have, n=1, cutoff=0.8)))
    return any(pat.search(text) and all(has(w, have) for w in need)
               for text, have in ocr_units(url)[0])


# ------------------------------------------------ rule 4: charges the menu states
def charges(row):
    """(service charge 10% stated, GST stated as excluded, service charge
    stated without a rate) for one wave 3 row, read from its own price_note."""
    note = row["price_note"]
    svc10 = bool(re.search(r"10% service charge", note, re.I)) and row["price_basis"] == "dine_in"
    gst = row["gst_inclusive"] == "no"
    unrated = (not svc10 and bool(re.search(r"service charge", note, re.I))
               and not re.search(r"no service charge", note, re.I))
    return svc10, gst, unrated


def final_price(row):
    """The row as it will be stored: price the customer pays, original in the note."""
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


# ------------------------------------------------------- decide each wave 3 row
decision, ocr_ok = {}, {}
for i, (w, v) in enumerate(zip(wave, ver_price)):
    image = v["reason"].startswith(IMAGE_REASON)
    if image:
        ocr_ok[i] = ocr_passes(w["source_url"], w["menu_wording"], w["price_sgd"])
    protected = [why for why, hit in (
        ("one of the 9 compared items", w["item_canonical"] in NINE),
        ("outlier", row_key(w) in outlier),
        ("signup or pitched shop", group.get(w["shop_id"]) in SIGNUP_OR_PITCHED)) if hit]
    if v["status"] == "failed":
        decision[i] = ("failed verification", v["reason"])
    elif w["shop_id"] == "little_rogue_coffee" and "wine" in w["price_note"].lower():
        decision[i] = ("Little Rogue Coffee wine list", "")
    elif v["status"] == "needs_review" and not image and \
            group.get(w["shop_id"]) not in SIGNUP_OR_PITCHED:
        decision[i] = ("needs_review, not an image price", v["reason"])
    elif image and not ocr_ok[i] and protected:
        decision[i] = ("review_merge", "; ".join(protected))
    else:
        decision[i] = ("merge", "")

# rule 3c: a changed price replaces nothing unless the two rows are like for like
def product_key(r):
    """item_key without the price basis: every row that prices the same product."""
    k = item_key(r)
    return k[:4] + k[5:]


by_match, by_product = defaultdict(list), defaultdict(list)
for i, w in enumerate(wave):
    by_match[(w["shop_id"], w["menu_wording"], w["price_sgd"], w["price_basis"],
              w["source_url"])].append(i)
    by_product[product_key(w)].append(i)

route_a = Counter()
not_like = Counter()
for o, r in zip(old_menu, recheck):
    if o["source_tier"] in ("2", "3"):
        continue                                   # removed below; nothing to protect
    if r["classification"] not in ("changed", "confirmed"):
        continue
    hits = by_match[(o["shop_id"], r["today_wording"], r["today_price"],
                     r["today_price_basis"], r["today_url"])]
    hit = next((i for i in hits if wave[i]["hot_or_iced"] == o["hot_or_iced"]), hits[0])
    t = wave[hit]
    svc10, gst, unrated = charges(t)
    why = [text for text, bad in (
        ("brand-wide page, not this outlet", "brand-wide page" in t["price_note"]),
        ("different size", t["size"] != o["size"]),
        ("different hot_or_iced", t["hot_or_iced"] != o["hot_or_iced"]),
        ("existing row is not a final price (gst_inclusive no)", o["gst_inclusive"] != "yes"),
        ("today's price is a 'from' price", t["price_confidence"] != "exact"),
        ("today's menu adds a service charge without a rate", unrated),
        ("today's menu excludes charges; the existing row was recorded at the listed "
         "price, so it is not known to be final", svc10 or gst)) if bad]
    if r["classification"] == "confirmed":
        # same listed price; only a problem when today's menu adds charges on top
        why = [x for x in why if x.startswith("today's menu")]
    if not why:
        if decision[hit][0] == "merge":
            route_a[f"{r['classification']}: today's row appended"] += 1
        else:
            route_a[f"{r['classification']}: today's row not merged ({decision[hit][0]})"] += 1
        continue
    route_a[f"{r['classification']}: existing row stays current"] += 1
    for x in why:
        not_like[x] += 1
    # hold today's row and any other row for the same product, whatever its
    # basis, so nothing from today displaces the existing price
    for i in set(hits) | set(by_product[product_key(o)]):
        if decision[i][0] in ("merge", "review_merge"):
            decision[i] = ("existing row stays current (rule 3c)", "; ".join(why))

merged = [final_price(w) for i, w in enumerate(wave) if decision[i][0] == "merge"]
converted = [m for m in merged if "; listed: " in m["price_note"]]


# ------------------------------------------------------------ menu_items_all
removed = [o for o in old_menu if o["source_tier"] in ("2", "3")]
kept = [o for o in old_menu if o["source_tier"] not in ("2", "3")]
merged_keys = {(m["shop_id"], m["item_canonical"]) for m in merged}
stood_in = [o for o in removed if (o["shop_id"], o["item_canonical"]) in merged_keys]
write("menu_items_all.csv", menu_fields, kept + merged, newline_of(menu_text))


# ----------------------------------------------------------------- shops_all
priced_now = {r["shop_id"] for r in kept + merged}
old_by_id = {s["shop_id"]: s for s in old_shops}
shop_rows, shops_new, shops_updated = [], 0, 0
for s in wave_shops:
    row = {k: s[k] for k in shop_fields}
    before = old_by_id.get(s["shop_id"])
    if row["menu_found"] == "yes" and s["shop_id"] not in priced_now:
        row["menu_found"] = before["menu_found"] if before else "no"
        row["best_source_tier"] = before["best_source_tier"] if before else ""
    if s["shop_id"] in SHOP_STATUS:
        row["menu_found"] = SHOP_STATUS[s["shop_id"]]
    if before is None:
        shops_new += 1
    elif row != before:
        shops_updated += 1
    else:
        continue
    shop_rows.append(row)
for sid, status in SHOP_STATUS.items():            # status changes for shops wave 3 did not re-list
    if sid not in {r["shop_id"] for r in shop_rows}:
        shop_rows.append(dict(old_by_id[sid], menu_found=status))
        shops_updated += 1
write("shops_all.csv", shop_fields, old_shops + shop_rows, newline_of(shops_text))


# ------------------------------------------------------- launches and promos
def map_format(value):
    """Same vocabulary as clean_csvs.py."""
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


SEASONAL = ("mooncake", "snowskin", "mid-autumn", "national day", "merlion")
_, new_launches = read("launches_wave3.csv")
_, new_promos = read("promos_wave3.csv")
for r in new_launches:
    r["first_seen"] = FIRST_SEEN
new_lclean = []
for r in new_launches:
    blob = f"{r['item_name']} {r['flavour']}".lower()
    new_lclean.append(dict(
        r, price_confidence={"high": "exact", "medium": "ambiguous"}.get(
            r["price_confidence"], r["price_confidence"]),
        format=map_format(r["format"]),
        is_seasonal="yes" if any(t in blob for t in SEASONAL) else "no"))
for rows in (old_launches, old_lclean):
    for r in rows:
        r["first_seen"] = ""
write("launches.csv", launch_fields + ["first_seen"], old_launches + new_launches,
      newline_of(launch_text))
write("launches_clean.csv", lclean_fields + ["first_seen"], old_lclean + new_lclean,
      newline_of(lclean_text))
write("promos.csv", promo_fields, old_promos + new_promos, newline_of(promo_text))
write("promos_clean.csv", pclean_fields, old_pclean + new_promos, newline_of(pclean_text))


# ------------------------------------------------- verification + review files
for i, v in enumerate(ver_price):
    if i in ocr_ok and ocr_ok[i]:
        v["status"] = "passed_ocr"
        v["reason"] = IMAGE_REASON + "; OCR text has the price in the same line or block as the wording"
    elif i in ocr_ok:
        v["status"] = "needs_review"
        v["reason"] = IMAGE_REASON + "; OCR did not find the price beside the wording"
write("verification_wave3.csv", ver_fields, ver, "\r\n")

name_of = {s["shop_id"]: s["shop_name"] for s in old_shops + wave_shops}
district_of = {s["shop_id"]: s["district"] for s in old_shops + wave_shops}


def held_row(i, **extra):
    w, f = wave[i], final_price(wave[i])
    return dict(shop_id=w["shop_id"], shop_name=name_of[w["shop_id"]],
                district=district_of[w["shop_id"]], item_canonical=w["item_canonical"],
                menu_wording=w["menu_wording"], size=w["size"], hot_or_iced=w["hot_or_iced"],
                listed_price=w["price_sgd"], price_after_charges=f["price_sgd"],
                source_url=w["source_url"], **extra)


review_rows = sorted(
    (held_row(i, why_held=decision[i][1],
              image_path=str((CACHE / ocr_units(wave[i]["source_url"])[1]).relative_to(FOLDER)),
              approve="")
     for i in decision if decision[i][0] == "review_merge"),
    key=lambda r: (r["shop_name"], r["image_path"], r["menu_wording"]))
if review_rows:
    write("review_merge.csv", list(review_rows[0]), review_rows, "\r\n")
held_rows = [held_row(i, not_merged_because=decision[i][0], detail=decision[i][1])
             for i in decision if decision[i][0] != "merge"]
write("merge_held_wave3.csv", list(held_rows[0]), held_rows, "\r\n")


# ------------------------------------------------------------------- report
rule("WAVE 3 ROWS")
tally = Counter(d[0] for d in decision.values())
for k, n in tally.most_common():
    print(f"  {n:>5}  {k}")
print(f"  {len(wave):>5}  total")
by_group = Counter(group.get(w["shop_id"], "?") for i, w in enumerate(wave)
                   if decision[i][0] == "merge")
print(f"  merged rows by group: {dict(by_group)}")

rule("RULE 3: EXISTING DATA")
print(f"  existing rows before {len(old_menu)}; tier 2/3 removed {len(removed)}; kept {len(kept)}")
print(f"  removed rows with a wave 3 row for the same shop and item: {len(stood_in)}")
for k, n in sorted(route_a.items()):
    print(f"  {n:>5}  {k}")
print("  why a changed or confirmed row was not like for like (a row can have several):")
for k, n in not_like.most_common():
    print(f"  {n:>5}  {k}")
print(f"  menu_items_all.csv now {len(kept) + len(merged)} rows")

rule("RULE 4: SERVICE CHARGE AND GST")
kinds = Counter(m["price_note"].split("; plus: ")[1] for m in converted)
print(f"  merged rows converted: {len(converted)}  {dict(kinds)}")
unrated = [m for m in merged if m["price_note"].endswith("not added")]
print(f"  merged rows whose menu states a service charge without a rate (not added): "
      f"{len(unrated)}  shops: {sorted({m['shop_id'] for m in unrated})}")

rule("RULE 6: OCR")
passed = sum(ocr_ok.values())
failed_ocr = [i for i, ok in ocr_ok.items() if not ok]
print(f"  image rows {len(ocr_ok)}; passed {passed}; failed {len(failed_ocr)}")
print(f"  passed and merged {sum(1 for i, ok in ocr_ok.items() if ok and decision[i][0] == 'merge')}"
      f"; failed but merged {sum(1 for i in failed_ocr if decision[i][0] == 'merge')}")
print(f"  review_merge.csv rows: {len(review_rows)}  "
      f"{dict(Counter(r['why_held'] for r in review_rows))}")
print(f"  image rows not merged for another reason: "
      f"{dict(Counter(decision[i][0] for i in ocr_ok if decision[i][0] not in ('merge', 'review_merge')))}")

rule("SHOPS, LAUNCHES, PROMOS")
print(f"  shops_all.csv: {shops_new} new shops, {shops_updated} updated rows appended "
      f"(the build reads the last row for a shop_id)")
print(f"  new shops with a merged price: "
      f"{sum(1 for r in shop_rows if r['shop_id'] not in old_by_id and r['shop_id'] in priced_now)}")
print(f"  menu_found on appended rows: {dict(Counter(r['menu_found'] for r in shop_rows))}")
print(f"  launches appended {len(new_launches)}; promos appended {len(new_promos)}")
