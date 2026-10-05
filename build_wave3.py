#!/usr/bin/env python3
"""Wave 3, stage 1 (groups a and b): turn wave3_raw.json into the wave 3 CSVs.

Reads:  wave3_raw.json (from extract_wave3.py), shops_all.csv (read only)
Writes: shops_wave3.csv, menu_items_wave3.csv, launches_wave3.csv,
        promos_wave3.csv, shop_sources_wave3.csv

Nothing here fetches a page and nothing here writes to shops_all.csv,
menu_items_all.csv, launches.csv or promos.csv. The merge is a later step.

Every judgement is in this file, in plain tables, so it can be read and
challenged: which lines are not menu items (SKIP), which wording maps to one of
the 9 compared items (TAGS), and which were left untagged as ambiguous
(AMBIGUOUS, reported, never tagged).
"""

import csv
import json
import re
from collections import Counter
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
RAW = json.loads((FOLDER / "wave3_raw.json").read_text())
OBSERVED = RAW["observed_date"]

MENU_FIELDS = ["shop_id", "item_canonical", "menu_wording", "flavour", "format", "size",
               "hot_or_iced", "price_sgd", "price_basis", "gst_inclusive", "price_confidence",
               "source_tier", "source_url", "source_date", "observed_date", "price_note"]


def read(name):
    with open(FOLDER / name, newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        return r.fieldnames[:], list(r)


def write(name, fields, rows):
    with open(FOLDER / name, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


# ----------------------------------------------------------- 1. the 9 items
# wording exactly as the page shows it -> (item_canonical, flavour, hot_or_iced)
# Follows the existing data: plain croissant flavour "none"; pain au chocolat
# and any flavoured or savoury croissant are croissant_filled; long black and
# americano are both americano; espresso rows carry size "single".
TAGS = {
    "flock_cafe": {
        "Single Espresso": ("espresso", "none", "hot"),
        "Double Espresso": ("espresso", "none", "hot"),
        "Latte": ("latte", "none", "hot"),
        "Flat White": ("flat_white", "none", "hot"),
        "Cappuccino": ("cappuccino", "none", "hot"),
        "Long Black (Americano)": ("americano", "none", "hot"),
        "Long Black (Black coffee)": ("americano", "none", "hot"),
        "Ice Latte": ("latte", "none", "iced"),
        "Ice Cappuccino": ("cappuccino", "none", "iced"),
        "Ice Black (Ice Americano)": ("americano", "none", "iced"),
        "Ice Black (Ice Black Coffee)": ("americano", "none", "iced"),
    },
    "drips_bakery_cafe": {
        "Butter Croissant": ("croissant_plain", "none", "not_applicable"),
        "Almond Croissant": ("croissant_filled", "almond", "not_applicable"),
        "Pain Au Chocolat": ("croissant_filled", "chocolate", "not_applicable"),
        "Paprika Cheese Sausage Croissant": ("croissant_filled", "paprika cheese sausage", "not_applicable"),
        "Kouign-Amann": ("kouign_amann", "butter", "not_applicable"),
    },
    "lunar_rabbit_boulangerie_hamilton": {
        "Croissant": ("croissant_plain", "none", "not_applicable"),
        "Pain au Chocolat": ("croissant_filled", "chocolate", "not_applicable"),
        "Ham and Cheese Croissant": ("croissant_filled", "ham & cheese", "not_applicable"),
        "Almond Croissant": ("croissant_filled", "almond", "not_applicable"),
        "Croissant Orange Pistache": ("croissant_filled", "orange pistachio", "not_applicable"),
        "Hotdog Croissant": ("croissant_filled", "hotdog", "not_applicable"),
        "Croissant à la Pêche": ("croissant_filled", "peach", "not_applicable"),
        "Banana Cream Croissant": ("croissant_filled", "banana cream", "not_applicable"),
        "Kouign Amann": ("kouign_amann", "butter", "not_applicable"),
    },
    "la_levain": {
        "French Butter Croissant": ("croissant_plain", "none", "not_applicable"),
        "Pain Au Chocolate": ("croissant_filled", "chocolate", "not_applicable"),
        "Almond Croissant": ("croissant_filled", "almond", "not_applicable"),
        "Four Cheese and Ham Croissant": ("croissant_filled", "four cheese & ham", "not_applicable"),
        "Burnt Cheese Croissant": ("croissant_filled", "burnt cheese", "not_applicable"),
        "Bobochacha Croissant": ("croissant_filled", "sweet potato & yam", "not_applicable"),
        "Camembert Mushroom Croissant": ("croissant_filled", "camembert mushroom", "not_applicable"),
        "Kouign Amann": ("kouign_amann", "butter", "not_applicable"),
        "Smoked Sea Salt Kouign Amann": ("kouign_amann", "smoked sea salt", "not_applicable"),
    },
}

# Could be read as one of the 9 items but does not clearly fit the existing
# conventions. Left untagged, listed in the report.
AMBIGUOUS = {
    ("flock_cafe", "Scrambled Eggs & Croissant w/ Smoked Salmon or Crispy Bacon"):
        "plated brunch dish built on a croissant, not a counter croissant",
    ("drips_bakery_cafe", "Egg Custard Croissant (3 Pieces)"):
        "sold as a pack of 3; no per-piece price on the page",
    ("drips_bakery_cafe", "Mini Croissant"): "mini, sold by the box, 'From' price",
    ("drips_bakery_cafe", "Mini Almond Croissant"): "mini, sold by the box, 'From' price",
    ("drips_bakery_cafe", "Mini Pain Au Chocolat"): "mini, sold by the box, 'From' price",
    ("drips_bakery_cafe", "Mini Paprika Chicken Cheese Sausage Croissant"):
        "mini, sold by the box, 'From' price",
    ("drips_bakery_cafe", "Hot Flat White"): "96oz tetra pack (about 12 cups), not a cup",
    ("drips_bakery_cafe", "Hot Long Black Tetra"): "96oz tetra pack (about 12 cups), not a cup",
    ("lunar_rabbit_boulangerie_hamilton", "Tuna Mayo Croissant Sandwiches"):
        "croissant sandwich; existing data has no sandwich precedent under croissant_filled",
    ("lunar_rabbit_boulangerie_hamilton", "Croque Champignons"): "croissant sandwich",
    ("lunar_rabbit_boulangerie_hamilton", "Croque Madame"): "croissant sandwich",
    ("lunar_rabbit_boulangerie_hamilton", "Croque Monsieur"): "croissant sandwich",
    ("lunar_rabbit_boulangerie_hamilton", "The Lunar"):
        "described as a filled crescent croissant but not named a croissant",
    ("lunar_rabbit_boulangerie_hamilton", "Almond Chocolate"):
        "probably an almond pain au chocolat; the wording does not say so",
    ("lunar_rabbit_boulangerie_hamilton", "Jalapeno & Cheddar Crisps"):
        "described as a twisted croissant; closer to a savoury twist",
    ("lunar_rabbit_boulangerie_hamilton", "Original Crookie"):
        "croissant-cookie hybrid; existing data files a crookie under cookie",
    ("lunar_rabbit_boulangerie_hamilton", "Matcha Crookie"): "croissant-cookie hybrid",
    ("lunar_rabbit_boulangerie_hamilton", "2L Americano (Hot)"): "2 litre catering size, not a cup",
    ("lunar_rabbit_boulangerie_hamilton", "2L Latte (Hot)"): "2 litre catering size, not a cup",
    ("lunar_rabbit_boulangerie_hamilton", "Cold Brew White 350"):
        "bottled cold brew with milk; not a latte or flat white",
    ("lunar_rabbit_boulangerie_hamilton", "Cold Brew Black 350"):
        "bottled cold brew; existing data files cold brew under other, not americano",
    ("lunar_rabbit_boulangerie_hamilton", "Principled Bundle"): "box of six croissants",
    ("flock_cafe", "Genmaicha Latte"): "tea latte, not matcha",
}

# ------------------------------------------------- 2. lines that are not menu
SKIP = re.compile(r"gift ?card|e-gift|postcard|message card|topper|acrylic tag|^candles$|"
                  r"surcharge|gift box \(box only\)", re.I)
# On these two Shopify stores every variant is the same product plus a paid
# extra (greeting card, candles, plaque, frosting). Only the base is an item.
BASE_VARIANT_ONLY = {"windowsill_pies_horne_rd", "boms_and_buns"}
ADDON_VARIANT = re.compile(r"card|no addons|add candles|add happy|add cream cheese", re.I)


# ----------------------------------------------------- 3. format / size / temp
DRINK_SECTIONS = {"coffee", "iced coffee", "tea", "fresh juices", "frappe", "smoothie", "fizzy",
                  "beers", "wines", "beverages", "tetra pack (96oz, est. 12 cups)"}
COLD_SECTIONS = {"iced coffee", "fresh juices", "frappe", "smoothie", "fizzy"}
ALCOHOL_SECTIONS = {"beers", "wines"}
SAVOURY_SECTIONS = {"best seller", "combos", "all day breakfast", "salad", "classics",
                    "pizzas 12'", "pasta", "sandwiches"}
SAVOURY_WORDS = re.compile(
    r"sandwich|burger|pizza|quiche|focaccia|hotdog|croque|cheese melt|salami|sausage|"
    r"hummus|\bham\b|bacon|tuna|truffle mushroom|mushroom onion|camembert|otah|"
    r"tartiflette|savoury|cheese bun|garlic|scallion|gratin|rendang|parmesan|ciabatta|"
    r"baguette", re.I)
CAKE_WORDS = re.compile(
    r"cake|gâteau|gateau|brownie|tiramisu|chiffon|mousse|pudding|swiss roll|banana loaf|"
    r"canape|shooter cup|clafoutis|pastry box|boîte|babka", re.I)


def classify(section, wording):
    s = section.strip().lower()
    if s in DRINK_SECTIONS:
        return "drink"
    if s in SAVOURY_SECTIONS or SAVOURY_WORDS.search(wording):
        return "savoury"
    if CAKE_WORDS.search(wording):
        return "cake"
    return "pastry"


def size_of(fmt, section, wording, variant):
    w = f"{wording} {variant}".lower()
    if fmt == "drink":
        if re.search(r"\bbot\b|bottle|\d+ ?ml\b| 350$", w):
            return "bottle"
        if "tetra" in section.lower() or re.search(r"\b2l\b|96oz", w):
            return "bulk"
        return "regular"
    m = re.search(r"(?:box|set|bundle) of (\d+)|\((\d+) ?(?:pcs|pieces|pc)\)|(\d+) ?(?:pcs|pieces)\b"
                  r"|- (\d+)'s|(\d+)'s\b|box of one", w)
    if m:
        n = next((g for g in m.groups() if g), "1")
        return f"box_of_{n}"
    if variant and not ADDON_VARIANT.search(variant):
        return re.sub(r"[^a-z0-9]+", "_", variant.lower()).strip("_")
    if re.search(r"whole|\d(\.\d)?\s?(\"|inch|”)|sourdough|loaf|bread|panettone|chocottone|"
                 r"christollen|log cake|\bpie\b|galette", w) and "slice" not in w:
        return "whole"
    return "single"


def temp_of(fmt, section, wording):
    if fmt != "drink":
        return "not_applicable"
    s, w = section.strip().lower(), wording.lower()
    if s in ALCOHOL_SECTIONS:
        return "not_applicable"
    if s in COLD_SECTIONS or re.match(r"^(ice|iced)\b", w) or "cold brew" in w or " 350" in w:
        return "iced"
    return "hot"


# ------------------------------------------------------------ 4. menu rows
menu, skipped = [], []


def add(shop_id, wording, price, url, *, section="", variant="", basis="online_order",
        confidence="exact", note="", fmt=None, size=None, temp=None):
    tag = TAGS.get(shop_id, {}).get(wording)
    fmt = fmt or classify(section, wording)
    canon, flavour, hot = tag if tag else ("", "", None)
    if tag and tag[0] == "espresso":
        size = "single"
    menu.append({
        "shop_id": shop_id, "item_canonical": canon, "menu_wording": wording,
        "flavour": flavour, "format": fmt,
        "size": size or size_of(fmt, section, wording, variant),
        "hot_or_iced": hot or temp or temp_of(fmt, section, wording),
        "price_sgd": f"{float(price):.2f}", "price_basis": basis, "gst_inclusive": "yes",
        "price_confidence": confidence, "source_tier": "1", "source_url": url,
        "source_date": "", "observed_date": OBSERVED, "price_note": note,
    })


janie_seen, base_seen = set(), set()
for r in RAW["rows"]:
    shop, wording, note, conf = r["shop_id"], r["wording"], [], "exact"

    if shop == "janie_the_bakery":
        # take.app lists name / description / [Sold out] / price, repeated per
        # collection day. Product names are the lines set in capitals.
        ctx = r["context"]
        after = [i for i, c in enumerate(ctx[:-1]) if c.startswith("$")]
        caps = [c for c in ctx if c == c.upper() and re.search(r"[A-Z]{3}", c)
                and not c.startswith("$")]
        if after:                           # the name follows the previous price
            wording = ctx[after[-1] + 1]
        elif caps:                          # first product under a day heading
            wording = caps[-1]
        else:
            skipped.append((shop, str(ctx[-2:]), r["price"], "no product name found"))
            continue
        if (wording, r["price"]) in janie_seen:
            continue
        janie_seen.add((wording, r["price"]))
        if "Sold out" in r["context"][r["context"].index(wording):]:
            note.append("sold out when observed")

    if SKIP.search(wording):
        skipped.append((shop, wording, r["price"], "not a menu item"))
        continue
    variant = r.get("variant", "")
    if variant and shop in BASE_VARIANT_ONLY:
        base = r["url"].split("?")[0]
        if base in base_seen:
            continue                        # paid-extra variants: keep the base only
        base_seen.add(base)
        assert re.search(r"^no gift card$|^no / no addons$|^no addons$|^\$", variant, re.I) \
            or not ADDON_VARIANT.search(variant), f"unexpected first variant: {variant}"
        note.append("base price; paid-extra variants on the page not recorded")
        variant = ""
    if r.get("from_price"):
        conf = "ambiguous"
        if r.get("range_high"):
            note.append(f"page shows a range ${r['price']} to ${r['range_high']} across sizes; "
                        "lowest recorded")
        else:
            note.append("page shows 'From' this price; options not recorded")
    if not r.get("in_stock", True):
        note.append("out of stock when observed")
    if shop == "flock_cafe" and wording == "Oceanic Lover":
        note.append(f"listed twice on the page at different prices; this is the "
                    f"{r['section'].strip()} listing")
    if shop == "flock_cafe" and wording == "Double Espresso":
        note.append("double shot")
    if shop == "windowsill_pies_horne_rd":
        note.append("Horne Road shop no longer listed on the cafe's site; "
                    "prices are from its online store")
    add(shop, wording, r["price"], r["url"], section=r.get("section", ""), variant=variant,
        confidence=conf, note="; ".join(note))

# --- menus the cafe publishes as images on its own website (read by eye) ---
V = "https://velvete.co/wp-content/uploads/2026/01/WhatsApp-Image-2026-01-25-at-11.28."
V_NOTE = "price is inside a menu image on velvete.co/cafe-and-events/"
for img, items in {
    "41.jpeg": [("SCALLION LOTUS BITES", 6.90, "savoury"), ("TAIWANESE TEMPURA", 6.90, "savoury"),
                ("PRAWN FRITTER (3 PCS)", 6.90, "savoury"), ("SPICY OTAK ROLL", 6.90, "savoury"),
                ("Combo Platter", 12.90, "savoury")],
    "41-1.jpeg": [("MOCK PIG TROTTERS W/VINEGAR", 11.90, "savoury"),
                  ("MOCK FISH (SPICY/TOMATO)", 11.90, "savoury"),
                  ("SPICY MUSHROOM MEDLEY", 11.90, "savoury"),
                  ("SWEET & SPICY MOCK CHICKEN", 11.90, "savoury")],
    "41-2.jpeg": [("REFRESHING CHICKEN SALAD", 11.90, "savoury"),
                  ("CHICKEN BREAST STEAK", 18.90, "savoury"),
                  ("CHICKEN TERIYAKI/GRILLED BEEF", 15.90, "savoury")],
    "42.jpeg": [("YAM CUBES", 7.90, "pastry"), ("JAGGED FRIES", 7.90, "savoury"),
                ("NACHOS", 7.90, "savoury"), ("PIZZA (5.5-INCH)", 7.90, "savoury")],
}.items():
    for wording, price, fmt in items:
        add("velvete_alexandra", wording, price, V + img, basis="dine_in", note=V_NOTE,
            fmt=fmt, size="single", temp="not_applicable")

LRC = "https://cdn.shopify.com/s/files/1/0570/6933/8823/files/LRCAAR_NEW_MENU_LANDSCAPE_2.jpg?v=1744730895"
LRC_NOTE = ("price is inside the menu image on littleroguecoffee.com/pages/menu; "
            "that image is a wine list only")
for wording in ("HOUSE WHITE WINES", "HOUSE RED WINES", "SPARKLING PROSECCO"):
    add("little_rogue_coffee", wording, 16, LRC, basis="dine_in", note=LRC_NOTE,
        fmt="drink", size="glass", temp="not_applicable")
    add("little_rogue_coffee", wording, 80, LRC, basis="dine_in", note=LRC_NOTE,
        fmt="drink", size="bottle", temp="not_applicable")

write("menu_items_wave3.csv", MENU_FIELDS, menu)
priced = Counter(m["shop_id"] for m in menu)


# ----------------------------------------------------------------- 5. shops
shop_fields, existing = read("shops_all.csv")
existing = {s["shop_id"]: s for s in existing}
GROUP_B = [s for s in existing.values() if s["menu_found"] != "yes"]


def shop(shop_id, name, district, address, postal, outlets, outlets_src, site, ig, oddle,
         found, tier):
    return dict(zip(shop_fields, [shop_id, name, district, address, postal, outlets, outlets_src,
                                  site, ig, oddle, found, tier]))


# shop_id -> (row, page the address was read from, group, reason when no prices)
SHOPS = {}


def put(row, addr_src, group, reason=""):
    SHOPS[row["shop_id"]] = (row, addr_src, group, reason)


ALEX = "Queenstown / Alexandra"
# ---- group a
put(shop("flock_cafe", "Flock Cafe", "Tiong Bahru", "78 Moh Guan Terrace #01-25", "162078", "2",
         "https://flockcafe.oddle.me/en_SG/stores", "", "@flockcafe",
         "https://flockcafe.oddle.me/en_SG", "yes", "1"),
    "https://flockcafe.oddle.me/en_SG/stores", "a+b")
put(shop("carrara_cafe", "Carrara Cafe", ALEX, "2 Jalan Bukit Merah", "150002", "unclear",
         "unclear", "", "", "", "no", ""),
    "https://www.chope.co/singapore-restaurants/restaurant/carrara-cafe", "a",
    "no own website or ordering page found; only a Facebook page and third-party listings")
put(shop("cafe_ondo", "Cafe On:do", ALEX, "321 Alexandra Road #02-26 Alexandra Central",
         "159971", "1", "https://hungrygowhere.com/food-news/cafe-ondo/", "", "", "", "no", ""),
    "https://hungrygowhere.com/food-news/cafe-ondo/", "a",
    "no own website or ordering page found; only press coverage")
put(shop("velvete_alexandra", "Velveté", ALEX, "321 Alexandra Road #03-24 Alexandra Central Mall",
         "159971", "1", "https://velvete.co/cafe-and-events/", "https://velvete.co", "@velvetesg",
         "", "yes", "1"),
    "https://velvete.co/cafe-and-events/", "a")
put(shop("caro_patisserie_joo_chiat", "Caro Patisserie", "Katong / Joo Chiat",
         "285 Joo Chiat Road", "427539", "2", "https://caro-patisserie.com/pages/retail-shops",
         "https://caro-patisserie.com", "", "", "yes", "1"),
    "https://caro-patisserie.com/pages/retail-shops", "a")
put(shop("cake_inspiration_joo_chiat", "Cake Inspiration", "Katong / Joo Chiat",
         "253 Joo Chiat Road", "427507", "1", "https://cake-inspiration.oddle.me/en_SG/stores",
         "https://www.cake-inspiration.com", "", "https://cake-inspiration.oddle.me/en_SG",
         "yes", "1"),
    "https://cake-inspiration.oddle.me/en_SG/stores", "a")
put(shop("average_service", "Average Service", "Jalan Besar", "315 Jalan Besar", "208973",
         "unclear", "unclear", "", "@average_service", "", "no", ""),
    "https://eatbook.sg/average-service/", "a",
    "averageservice.com is a 'Launching Soon' placeholder; no menu or ordering page")
put(shop("space_23", "Space 23", "Jalan Besar", "393 Jalan Besar", "209005", "unclear",
         "unclear", "https://www.space23.sg", "", "", "no", ""),
    "https://eatbook.sg/space-23-cafe/", "a",
    "space23.sg timed out on every attempt (Cloudflare 522); no other allowed source")
put(shop("refuel_ii_bukit_merah", "Refuel II", ALEX, "28 Jalan Bukit Merah", "152028", "unclear",
         "unclear", "", "", "", "no", ""),
    "https://eatbook.sg/bukit-merah-cafe/", "a",
    "no Refuel found in Tiong Bahru; Refuel II in Bukit Merah is the nearest match and is not "
    "confirmed as the cafe visited; no own website or ordering page found")
put(shop("lunar_rabbit_boulangerie_hamilton", "The Lunar Rabbit Boulangerie", "Jalan Besar",
         "16 Hamilton Road #01-02", "209186", "2", "https://lunar-rabbit.com/",
         "https://lunar-rabbit.com", "", "", "yes", "1"),
    "https://lunar-rabbit.com/", "a")
la = dict(existing["la_levain"])
la["oddle_url"] = ""                                  # the Oddle shop is closed
la["outlet_count_source"] = "https://www.lalevain.com/visit-la-levain-singapore/"
put(la, "https://www.lalevain.com/", "a (re-check)")

# ---- group b: every shop in shops_all.csv whose menu_found is not "yes"
B_UPDATES = {
    "drips_bakery_cafe": dict(address="71 Seng Poh Road #01-37", postal_code="160071",
                              outlet_count="2",
                              outlet_count_source="https://www.drips.com.sg/pages/visit-us",
                              oddle_url="https://dripsbakery.oddle.me/en_SG",
                              menu_found="yes", best_source_tier="1"),
    "little_rogue_coffee": dict(own_website="https://littleroguecoffee.com",
                                outlet_count_source="https://littleroguecoffee.com/",
                                menu_found="yes", best_source_tier="1"),
    "janie_the_bakery": dict(menu_found="yes", best_source_tier="1"),
    "boms_and_buns": dict(address="193 Joo Chiat Road", postal_code="427464",
                          own_website="https://bomsnbuns.com",
                          outlet_count_source="https://bomsnbuns.com/pages/visit-us",
                          menu_found="yes", best_source_tier="1"),
    "windowsill_pies_horne_rd": dict(menu_found="yes", best_source_tier="1"),
    "butter_tgt_duxton": dict(address="55 Neil Road", postal_code="088892", outlet_count="2",
                              outlet_count_source="https://buttertgt.com/",
                              own_website="https://buttertgt.com"),
    "asylum_coffeehouse": dict(own_website="https://www.asylumcoffeehouse.com"),
    "pelle_and_pepe": dict(address="83 Joo Chiat Place #01-01", postal_code="427800",
                           own_website="https://pepelevain.co",
                           outlet_count_source="https://pepelevain.co/"),
}
B_SOURCE = {
    "drips_bakery_cafe": "https://www.drips.com.sg/pages/visit-us",
    "little_rogue_coffee": "https://littleroguecoffee.com/",
    "janie_the_bakery": "https://www.janiethebakery.com/",
    "boms_and_buns": "https://bomsnbuns.com/pages/visit-us",
    "windowsill_pies_horne_rd": "https://windowsillpies.sg/pages/contact",
    "butter_tgt_duxton": "https://buttertgt.com/",
    "asylum_coffeehouse": "https://www.asylumcoffeehouse.com/",
    "pelle_and_pepe": "https://pepelevain.co/",
}
NO_SITE = "no own website or ordering page found (Instagram only)"
B_REASON = {
    "butter_tgt_duxton": "own site sells jarred butters and merchandise only; no cafe menu. "
                         "Site gives 55 Neil Road, not the 76A Duxton Road on file",
    "asylum_coffeehouse": "own site sells beans and brewing gear only; no cafe menu. "
                          "The asylumcoffeesg.com on file no longer resolves",
    "pelle_and_pepe": "own ordering page says 'closed for now but we'll be back soon'; no prices "
                      "shown. Page gives 83 Joo Chiat Place, not the 201 Joo Chiat Road on file",
    "chye_seng_huat_hardware_robertson":
        "cshhcoffee.com lists only 150 Tyrwhitt Road; could not confirm a Robertson Quay outlet "
        "exists. Its ordering page belongs to the Tyrwhitt shop (group c)",
    "glass_roasters_tiong_bahru": "glassroasters.com fails to load (TLS error); no ordering page",
    "sunday_morning_coffee_shop": NO_SITE, "binge_tiong_bahru": NO_SITE,
    "the_neptune": NO_SITE,
    "le_petit_katong": "lepetitkatong.com sits behind a bot check and would not load; "
                       "not confirmed to be the cafe's site",
    "lola_faye_cafe": NO_SITE, "breaditation_bakery": NO_SITE, "cugini_trattoria_bakery": NO_SITE,
    "koki_bakery_duxton": NO_SITE, "tarik_cafe_arab_st": NO_SITE, "monochrome_boutique": NO_SITE,
    "clump_coffee_serangoon": NO_SITE, "pueblo_cafe_upper_thomson": NO_SITE,
    "woodcraft_bakehouse_thomson": NO_SITE,
    "slow_bakes_balestier": NO_SITE + "; third-party pages place Slow Bakes in Yishun, "
                            "not at the Ah Hood Road address on file",
    "black_and_ink_swan_lake": "no own website or ordering page found (Facebook only)",
}
for s in GROUP_B:
    if s["shop_id"] == "flock_cafe":
        continue                                       # already added in group a
    row = dict(s)
    row.update(B_UPDATES.get(s["shop_id"], {}))
    put(row, B_SOURCE.get(s["shop_id"], ""), "b",
        "" if priced.get(s["shop_id"]) else B_REASON[s["shop_id"]])

collisions = [k for k in SHOPS if k in existing and SHOPS[k][2] == "a"]
assert not collisions, f"new shop_id collides with shops_all.csv: {collisions}"
assert all(m["shop_id"] in SHOPS for m in menu), "menu row without a shop row"

write("shops_wave3.csv", shop_fields, [v[0] for v in SHOPS.values()])
write("shop_sources_wave3.csv",
      ["shop_id", "group", "is_new", "address_source_url", "rows_captured", "no_price_reason"],
      [dict(shop_id=k, group=g, is_new="no" if k in existing else "yes",
            address_source_url=src, rows_captured=priced.get(k, 0), no_price_reason=why)
       for k, (row, src, g, why) in SHOPS.items()])


# -------------------------------------------------------- 6. launches / promos
launch_fields, _ = read("launches.csv")
promo_fields, _ = read("promos.csv")
price = {(m["shop_id"], m["menu_wording"]): m["price_sgd"] for m in menu}


def launch(name, district, shop_id, item, flavour, fmt, month, limited, url):
    return dict(zip(launch_fields, [name, district, item, flavour, fmt, price[(shop_id, item)],
                                    "high", month, limited, "brand_website", url, ""]))


launches = [
    # bomsnbuns.com home page: "Three new Boms have entered the lineup."
    # Month is the product's published_at in the shop's own products.json.
    launch("Boms & Buns", "Katong / Joo Chiat", "boms_and_buns", item, flavour, "cream puff",
           "2026-09", "no", "https://bomsnbuns.com/")
    for item, flavour in [("Sticky Date Toffee", "sticky date toffee"),
                          ("Ricotta Chocolate Chip", "ricotta chocolate chip"),
                          ("Lemon Meringue", "lemon meringue")]
]
# The Lunar Rabbit's ordering page has a "New Bakes" section. The page gives no
# date, so launch_month is left empty rather than guessed.
launches += [
    launch("The Lunar Rabbit Boulangerie", "Jalan Besar", "lunar_rabbit_boulangerie_hamilton",
           r["wording"], "", classify(r["section"], r["wording"]), "", "no",
           "https://lunar-rabbit.com/catalog")
    for r in RAW["rows"]
    if r["shop_id"] == "lunar_rabbit_boulangerie_hamilton" and r["section"] == "New Bakes"
]
# Cake Inspiration's ordering page has a Halloween Collection. Seasonal and
# limited, no date shown on the page.
launches += [
    launch("Cake Inspiration", "Katong / Joo Chiat", "cake_inspiration_joo_chiat",
           r["wording"], "halloween", classify(r["section"], r["wording"]), "", "yes",
           "https://cake-inspiration.oddle.me/en_SG")
    for r in RAW["rows"]
    if r["shop_id"] == "cake_inspiration_joo_chiat" and r["section"] == "Halloween Collection"
]
for l in launches:                                     # 'From' prices are not exact
    m = next(m for m in menu if m["menu_wording"] == l["item_name"]
             and m["source_url"].startswith(l["source_url"].rstrip("/")[:24]))
    if m["price_confidence"] == "ambiguous":
        l["price_confidence"] = "medium"
write("launches_wave3.csv", launch_fields, launches)


def promo(brand, outlets, text, kind, items, url):
    return dict(zip(promo_fields, [brand, "no", outlets, text, kind, items, "", "", "no",
                                   "brand ordering page", url, OBSERVED]))


promos = [
    promo("Flock Cafe", "2", "Free Cajun Fries for first-time customers with min spend $50, "
          "promo code WELCOME50", "bundle", "Cajun Fries", "https://flockcafe.oddle.me/en_SG"),
    promo("Flock Cafe", "2", "Weekday Breakfast Ticket 8am-11am, $14-$15 with coffee or tea",
          "bundle", "Eggs & Sourdough Toast, Eggs Benedict, Waffles with Egg, Granola & Pancakes",
          "https://eats.oddle.me/menus/flock-cafe"),
    promo("Flock Cafe", "2", "Weekday Lunch Ticket 11am-3pm, $15-$16 with drinks", "bundle",
          "Grilled fish, roasted chicken, chicken katsu or beef patty with risotto rice",
          "https://eats.oddle.me/menus/flock-cafe"),
    promo("Flock Cafe", "2", "Weekday Dinner Ticket 6pm-8pm, $22-$26 with drinks", "bundle",
          "Steak, porkchop or pizza with wine or beer", "https://eats.oddle.me/menus/flock-cafe"),
    promo("Drips Bakery Cafe", "2", "$8 off $80 on first order with code 8OFF80", "price_cut",
          "All online orders", "https://dripsbakery.oddle.me/en_SG"),
    promo("Drips Bakery Cafe", "2", "Pick up and save 10%", "price_cut", "All online orders",
          "https://dripsbakery.oddle.me/en_SG"),
]
write("promos_wave3.csv", promo_fields, promos)


# ------------------------------------------------------------------- report
def rule(t):
    print(f"\n{t}\n" + "=" * len(t))


rule("SHOPS")
for k, (row, src, g, why) in SHOPS.items():
    print(f"  {g:<12}{k:<36}{row['district'][:22]:<24}{row['menu_found']:<11}"
          f"{priced.get(k, 0):>4}  {why[:70]}")
rule("TOTALS")
print(f"  shops in shops_wave3.csv : {len(SHOPS)}  (new: {sum(k not in existing for k in SHOPS)})")
print(f"  shops with prices        : {len(priced)}")
print(f"  menu rows                : {len(menu)}")
for item, n in sorted(Counter(m['item_canonical'] for m in menu if m['item_canonical']).items()):
    print(f"     {item:<18}{n:>4}")
print(f"  launches {len(launches)}   promos {len(promos)}")
print(f"  skipped as not menu items: {len(skipped)}")
print(f"  formats {dict(Counter(m['format'] for m in menu))}")
print(f"  confidence {dict(Counter(m['price_confidence'] for m in menu))}")
missing = [k for k in AMBIGUOUS if not any((m['shop_id'], m['menu_wording']) == k for m in menu)]
print(f"  AMBIGUOUS entries not found in menu rows: {missing}")
unused = [(s, w) for s, t in TAGS.items() for w in t
          if not any(m['shop_id'] == s and m['menu_wording'] == w for m in menu)]
print(f"  TAGS entries not found in menu rows: {unused}")
keys = Counter((m['shop_id'], m['menu_wording'], m['size'], m['hot_or_iced']) for m in menu)
print(f"  duplicate row keys: {[k for k, n in keys.items() if n > 1]}")
