#!/usr/bin/env python3
"""Shared code for the wave 3 research: page readers and the row conventions.

Every wave 3 capture script (wave3_parts/<part>/capture.py) builds its rows
through make_row() here, so one set of rules covers every shop:

  * price_note starts with exactly one availability token
  * size comes from what the page states; "single" only for one piece
  * item_canonical always holds a value from the existing vocabulary
  * 'From' and range prices are price_confidence = ambiguous
  * gst_inclusive is "yes" unless the page says ++ / before GST / excl. GST

verify_wave3.py does not import this file.
Nothing here writes to shops_all.csv, menu_items_all.csv, launches.csv or
promos.csv.

Needs Playwright:  pip install playwright && playwright install chromium
"""

import csv
import datetime
import html
import re
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
PARTS = FOLDER / "wave3_parts"
TODAY = datetime.date.today().isoformat()
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

MENU_FIELDS = ["shop_id", "item_canonical", "menu_wording", "flavour", "format", "size",
               "hot_or_iced", "price_sgd", "price_basis", "gst_inclusive", "price_confidence",
               "source_tier", "source_url", "source_date", "observed_date", "price_note"]
SHOP_FIELDS = ["shop_id", "shop_name", "district", "address", "postal_code", "outlet_count",
               "outlet_count_source", "own_website", "instagram_handle", "oddle_url",
               "menu_found", "best_source_tier"]
# part files carry a few working columns that the final CSVs drop
PART_MENU_FIELDS = MENU_FIELDS + ["section", "group"]
PART_SHOP_FIELDS = SHOP_FIELDS + ["group", "address_source_url", "no_price_reason"]
LAUNCH_FIELDS = ["shop_id_or_name", "district", "item_name", "flavour", "format", "price_sgd",
                 "price_confidence", "launch_month", "is_limited_time", "source_type",
                 "source_url", "source_date"]
PROMO_FIELDS = ["brand_name", "is_chain", "outlet_count", "offer_text", "promo_type",
                "items_covered", "start_date", "end_date", "end_date_stated", "source_type",
                "source_url", "observed_date"]

NINE = ["croissant_plain", "flat_white", "latte", "matcha_latte", "cappuccino", "americano",
        "espresso", "croissant_filled", "kouign_amann"]
VOCAB = NINE + ["hojicha_latte", "cruffin", "danish", "cake_slice", "cookie", "other"]
FORMATS = {"drink", "pastry", "cake", "savoury"}
DISTRICTS = [
    "Bugis / Kampong Glam / Haji Lane", "Bukit Timah / Sixth Avenue", "East Coast / Siglap",
    "Holland Village / Dempsey", "Jalan Besar", "Katong / Joo Chiat", "Novena / Balestier",
    "Orchard / Somerset", "Queenstown / Alexandra", "River Valley / Robertson Quay",
    "Serangoon Gardens / Kovan", "Tanjong Pagar / Duxton", "Telok Ayer / Amoy", "Tiong Bahru",
    "Upper Thomson"]

PRICE = re.compile(r"^(From )?S?\$\s?([\d,]+(?:\.\d{1,2})?)$")


# ================================================================== browser
class Browser:
    """Headless Chromium. `with Browser() as b: lines = b.lines(url)`"""

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        self.page = self._browser.new_context(
            user_agent=UA, locale="en-SG", viewport={"width": 1280, "height": 2000}).new_page()
        return self

    def __exit__(self, *exc):
        self._browser.close()
        self._pw.stop()

    def open(self, url, steps=(), wait=0):
        """Load a page and let its JavaScript finish. `steps` are visible labels
        to click (outlet pickers and the like). Never used for a login."""
        p = self.page
        resp = p.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            p.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        if wait:
            p.wait_for_timeout(wait)
        for label in steps:
            p.get_by_text(label, exact=False).first.click()
            p.wait_for_timeout(3000)
        for _ in range(15):
            p.mouse.wheel(0, 3000)
            p.wait_for_timeout(300)
        return resp.status if resp else None

    def text(self, url=None, steps=(), wait=0):
        if url:
            self.open(url, steps, wait)
        return self.page.evaluate("document.body ? document.body.innerText : ''")

    def lines(self, url=None, steps=(), wait=0):
        return [l.strip() for l in self.text(url, steps, wait).splitlines() if l.strip()]

    def links(self):
        return sorted(set(self.page.evaluate(
            "[...document.querySelectorAll('a')].map(a => String(a.href))"
            ".filter(h => h.startsWith('http'))")))

    def images(self):
        return sorted(set(self.page.evaluate(
            "[...document.querySelectorAll('img')].map(i => i.currentSrc || i.src)"
            ".filter(Boolean)")))

    def json(self, url):
        """JSON through the browser's network stack (python.org's macOS build
        has no CA bundle, so urllib cannot verify these sites)."""
        resp = self.page.request.get(url, timeout=60000)
        assert resp.ok, f"{resp.status} {url}"
        return resp.json()

    def download(self, url, path):
        resp = self.page.request.get(url, timeout=60000)
        assert resp.ok, f"{resp.status} {url}"
        Path(path).write_bytes(resp.body())
        return path


def strip_html(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def raw(wording, price, url, *, section="", description="", variant="", from_price=False,
        range_high="", in_stock=True):
    """One priced line exactly as a page shows it."""
    return dict(wording=wording.strip(), price=str(price).replace(",", ""), url=url,
                section=section.strip(), description=description.strip(), variant=variant.strip(),
                from_price=from_price, range_high=range_high, in_stock=in_stock)


# ============================================================= page readers
ODDLE_JS = r"""() => {
  const out = []; let section = '';
  for (const el of document.querySelectorAll('h1, h2, h3, a')) {
    if (el.tagName !== 'A') { section = el.innerText.trim(); continue; }
    const h4 = [...el.querySelectorAll('h4')].map(h => h.innerText.trim());
    const price = h4.find(t => /^(From )?S\$[\d,.]+$/.test(t));
    if (!price || h4.length < 2 || h4[0] === price) continue;
    const p = el.querySelector('p');
    out.push({section, wording: h4[0], price, description: p ? p.innerText.trim() : '',
              sold_out: /out of stock|sold out/i.test(el.innerText)});
  }
  return out;
}"""


def oddle(b, url):
    """Oddle storefront (<shop>.oddle.me/en_SG). Each product is a card: an <a>
    with the name in its first <h4> and the price in a later <h4>. The same
    product can sit in two sections; both are kept, each with its section."""
    b.open(url, wait=4000)
    cards = b.page.evaluate(ODDLE_JS)
    for _ in range(3):                      # slow stores: wait and look again
        if cards:
            break
        b.page.wait_for_timeout(8000)
        cards = b.page.evaluate(ODDLE_JS)
    out = []
    for c in cards:
        m = PRICE.match(c["price"])
        out.append(raw(c["wording"], m.group(2), url, section=c["section"],
                       description=c["description"], from_price=bool(m.group(1)),
                       in_stock=not c["sold_out"]))
    return out


def shopify(b, base):
    """Shopify store: <base>/products.json. One line per variant, each with the
    product page URL that shows that variant's price."""
    out = []
    for p in b.json(f"{base}/products.json?limit=250")["products"]:
        many = len(p["variants"]) > 1
        for v in p["variants"]:
            url = f"{base}/products/{p['handle']}" + (f"?variant={v['id']}" if many else "")
            r = raw(p["title"], v["price"], url, section=p.get("product_type", ""),
                    description=strip_html(p.get("body_html"))[:600],
                    variant=v["title"] if many else "", in_stock=bool(v["available"]))
            r["tags"] = ", ".join(p.get("tags") or [])
            r["published_at"] = (p.get("published_at") or "")[:10]
            out.append(r)
    return out


def woo(b, base):
    """WooCommerce store API. Variable products give a price range."""
    out, page = [], 1
    while True:
        batch = b.json(f"{base}/wp-json/wc/store/products?per_page=100&page={page}")
        for p in batch:
            pr = p["prices"]
            rng = pr.get("price_range")
            low = int(rng["min_amount"]) if rng else int(pr["price"])
            high = int(rng["max_amount"]) if rng else low
            out.append(raw(html.unescape(p["name"]), f"{low / 100:.2f}", p["permalink"],
                           section=" / ".join(html.unescape(c["name"]) for c in p["categories"]),
                           description=strip_html(p.get("short_description")
                                                  or p.get("description"))[:600],
                           from_price=high != low,
                           range_high=f"{high / 100:.2f}" if high != low else "",
                           in_stock=bool(p["is_in_stock"])))
        if len(batch) < 100:
            return out
        page += 1


def price_lines(lines):
    """Generic helper for a text menu: yields (index, price, is_from) for every
    line that is only a price. The caller decides which line is the name."""
    for i, l in enumerate(lines):
        m = PRICE.match(l)
        if m:
            yield i, m.group(2).replace(",", ""), bool(m.group(1))


# ============================================================== conventions
GST_EXCLUSIVE = re.compile(r"\+\+|before gst|excl(?:\.|uding|usive of)?\s*gst|"
                           r"subject to[^.\n]{0,40}gst|gst (?:is )?not included", re.I)
SERVICE_CHARGE = re.compile(r"service charge", re.I)
SEASONAL = re.compile(
    r"christmas|x-?mas|\bcny\b|chinese new year|lunar new year|mid-?autumn|mooncake|"
    r"halloween|easter|valentine|national day|deepavali|hari raya|thanksgiving|"
    r"mother'?s day|father'?s day|seasonal|festive|log cake|galette des rois|king cake|"
    r"panettone|chocottone|stollen|formula 1|\bf1\b|gingerbread|mince pie|pride month|"
    r"nurses'? day|teacher'?s day|women'?s day|children'?s day", re.I)

SAVOURY_WORDS = re.compile(
    r"sandwich|burger|pizza|quiche|focaccia|hotdog|hot dog|croque|cheese melt|salami|sausage|"
    r"hummus|\bham\b|bacon|tuna|mushroom|camembert|otah|tartiflette|savoury|savory|"
    r"cheese bun|garlic|scallion|gratin|rendang|parmesan|ciabatta|baguette|pasta|linguin|"
    r"rigatoni|penne|spaghetti|salad|fries|steak|chicken|salmon|\begg|toast|bagel|soup|"
    r"\brice\b|noodle|wrap|panini|toastie|sando\b|pork|beef|prawn|fish|nachos|breakfast", re.I)
CAKE_WORDS = re.compile(
    r"cake|gâteau|gateau|brownie|tiramisu|chiffon|mousse|pudding|swiss roll|banana loaf|"
    r"banana bread|canape|shooter cup|clafoutis|boîte|babka|cupcake|loaf slice|pound", re.I)
DRINK_WORDS = re.compile(
    r"latte|espresso|cappuccino|americano|long black|flat white|mocha|macchiato|piccolo|"
    r"cortado|\btea\b|\bteh\b|\bkopi\b|coffee|cold brew|juice|smoothie|frappe|soda|\bcoke\b|"
    r"chocolate$|babycino|kombucha|\bbeer\b|\bwine\b|prosecco|\bade\b|milkshake|affogato|"
    r"matcha$|hojicha$|sparkling|water$", re.I)


def classify_format(section, wording):
    """drink / pastry / cake / savoury, the four formats the existing data uses."""
    s = section.lower()
    if re.search(r"coffee|\btea\b|drink|beverage|juice|frappe|smoothie|fizzy|beer|wine|"
                 r"tetra|espresso|brew", s) and not re.search(r"cake|bakes|food", s):
        return "drink"
    if DRINK_WORDS.search(wording) and not re.search(
            r"cake|tart|cookie|croissant|bun|loaf|danish|slice|roll|scone|macaron|pie\b|"
            r"tiramisu|brownie|truffle|bread", wording, re.I):
        return "drink"
    if SAVOURY_WORDS.search(wording) or re.search(
            r"breakfast|brunch|mains?\b|salad|classics|pizza|pasta|sandwich|savou?ry|sides|"
            r"burger|toast", s):
        return "savoury"
    if CAKE_WORDS.search(wording):
        return "cake"
    return "pastry"


SIZE_PATTERNS = [
    (r"(?:box|set|bundle|pack|bag|tub|jar|tin) of (\d+)", "box_of_{}"),
    (r"\bx\s?(\d+)\b", "box_of_{}"),
    (r"(\d+)\s?(?:pcs?|pieces?|buns|tarts|cookies|slices)\b", "box_of_{}"),
    (r"(\d+)'s\b", "box_of_{}"),
    (r"(\d+(?:\.\d+)?)\s?(?:\"|”|''|-?\s?inch(?:es)?\b)", "{}_inch"),
    (r"serves (\d+(?:\s?-\s?\d+)?)", "serves_{}"),
    (r"(\d+(?:\.\d+)?)\s?kg\b", "{}kg"),
    (r"(\d+)\s?(?:g|gm|grams?)\b", "{}g"),
    (r"(\d+(?:\.\d+)?)\s?l\b", "{}l"),
    (r"(\d+)\s?ml\b", "{}ml"),
    (r"(\d+)\s?oz\b", "{}oz"),
]


def size_from_page(fmt, wording, variant="", description="", section=""):
    """Size as the page states it. The wording and variant are trusted first,
    then the section title, then the description. 'single' only when nothing
    on the page says the item is more than one piece."""
    named = f"{wording} {variant}"
    own_variant = bool(variant) and not re.search(r"default|no add|no gift", variant, re.I)
    for where in (named, description):
        if where is not named and own_variant:
            break                           # the variant itself is the size
        w = where.lower()
        if re.search(r"box of one\b", w):
            return "box_of_1"
        for pat, out in SIZE_PATTERNS:
            m = re.search(pat, w)
            if m:
                return out.format(re.sub(r"\s", "", m.group(1)))
        if where is named and re.search(r"\bslice\b", w):
            return "single"
        if where is named and re.search(r"\bwhole\b", w):
            return "whole"
    if re.search(r"\bwhole\b", section.lower()) and not own_variant:
        return "whole"                      # section sizes like "6 & 8 inch" cover many items
    w = f"{wording} {variant}".lower()
    if own_variant:
        return re.sub(r"[^a-z0-9.]+", "_", variant.lower()).strip("_")[:40]
    if fmt == "drink":
        if re.search(r"\bbot\b|bottle|\bcan\b", w):
            return "bottle"
        if re.search(r"\bglass\b", w):
            return "glass"
        if re.search(r"\bpot\b", w):
            return "pot"
        return "regular"
    if re.search(r"sourdough|loaf|bread|baguette|ciabatta|brioche$|shokupan|\bpie\b|galette|"
                 r"log cake|whole cake|stollen|panettone", w) and not re.search(r"slice|toast|sandwich", w):
        return "whole"
    return "single"


def temp_of(fmt, section, wording):
    if fmt != "drink":
        return "not_applicable"
    s, w = section.lower(), wording.lower()
    if re.search(r"beer|wine|alcohol|cocktail|spirit", s) or re.search(
            r"\bbeer\b|\bwine|prosecco|sauvignon|\d+ml|pilsner|lager|\bale\b", w):
        return "not_applicable"
    if re.search(r"\biced?\b|cold|juice|frappe|smoothie|fizzy|soda|ade\b", s + " " + w):
        return "iced"
    return "hot"


def nine_tag(wording, fmt):
    """Strict match to one of the 9 compared items, or None. Mirrors the
    existing data: long black, americano and black coffee are americano; pain
    au chocolat and any flavoured or savoury croissant are croissant_filled.
    Packs, minis, boxes, sandwiches and plated dishes are never tagged."""
    w = re.sub(r"\s+", " ", wording.lower()).strip()
    w = re.sub(r"\s*\((hot|iced?|cold)\)$", "", w)
    core = re.sub(r"^(hot|iced?|cold)\s+", "", w)
    if re.search(r"\bmini\b|box|bundle|\bset\b|\bpcs?\b|pieces|sandwich|platter|tetra|\b\d+l\b|"
                 r"\bw/|\bwith\b|&.*egg|crookie|cube", w):
        return None
    if fmt == "drink":
        if re.fullmatch(r"(single |double )?espresso", core):
            return "espresso"
        if re.fullmatch(r"(caff[eèé] |cafe |café )?latte", core):
            return "latte"
        if core == "flat white":
            return "flat_white"
        if core == "cappuccino":
            return "cappuccino"
        if re.fullmatch(r"long black|americano|caff[eèé] americano|black coffee|"
                        r"long black \(.*\)|black \(.*americano.*\)|black \(.*black coffee.*\)",
                        core):
            return "americano"
        if re.fullmatch(r"(uji |ceremonial |premium )?matcha latte", core):
            return "matcha_latte"
        return None
    if re.fullmatch(r"(le |the )?(plain |butter |french butter |classic |original |"
                    r"all[- ]butter |sourdough |artisan |traditional |french )*croissant"
                    r"( français)?", w):
        return "croissant_plain"
    if "kouign" in w:
        return "kouign_amann"
    if re.search(r"pain au chocolat|croissant", w):
        return "croissant_filled"
    return None


def default_canonical(fmt, wording, size):
    """The existing vocabulary for items outside the 9."""
    w = wording.lower()
    if "cruffin" in w:
        return "cruffin"
    if re.search(r"cookie|crookie", w) and size == "single":
        return "cookie"
    if fmt == "drink" and re.fullmatch(r"(hot |iced? )?(roasted )?hojicha latte", w.strip()):
        return "hojicha_latte"
    if size == "single" and re.search(r"danish|pain aux raisins|cinnamon (roll|bun|scroll|swirl)|"
                                      r"chausson|kanelbulle", w):
        return "danish"
    if fmt == "cake" and size == "single" and not re.search(r"whole", w):
        return "cake_slice"
    return "other"


def make_row(shop_id, r, *, tag=None, flavour=None, basis="online_order", tier="1",
             fmt=None, size=None, temp=None, gst_exclusive=False, seasonal=None,
             notes=(), source_date="", group="", observed=TODAY):
    """Build one menu row from a raw line. `tag` overrides the automatic
    item_canonical; pass tag="other" to force an ambiguous item out of the 9."""
    assert basis in ("takeaway", "dine_in", "online_order"), basis
    wording = r["wording"]
    fmt = fmt or classify_format(r["section"], wording)
    assert fmt in FORMATS, fmt
    if not size:
        size = size_from_page(fmt, wording, r["variant"], r["description"], r["section"])
        if r["from_price"] and size == "single":
            size = "unspecified"            # a 'From' price does not say which option
    canon = tag or nine_tag(wording, fmt) or default_canonical(fmt, wording, size)
    assert canon in VOCAB, canon
    if canon in NINE and size not in ("single", "regular"):
        canon = default_canonical(fmt, wording, size)      # packs are never one of the 9
    if canon == "espresso":
        size = "single"                                    # existing convention
    temp = temp or temp_of(fmt, r["section"], wording)
    if flavour is None:
        flavour = "none" if canon in ("croissant_plain", "flat_white", "latte", "cappuccino",
                                      "americano", "espresso") else ""
    blob = " ".join([r["section"], wording, r.get("tags", "")])
    is_seasonal = SEASONAL.search(blob) is not None if seasonal is None else seasonal
    note = ["availability: " + ("seasonal" if is_seasonal else
                                "available" if r["in_stock"] else "out_of_stock")]
    if is_seasonal and not r["in_stock"]:
        note.append("out of stock when observed")
    conf = "exact"
    if r["from_price"]:
        conf = "ambiguous"
        note.append(f"page shows a range ${r['price']} to ${r['range_high']}; lowest recorded"
                    if r.get("range_high") else "page shows 'From' this price")
    if gst_exclusive:
        note.append("page states prices exclude GST")
    note += list(notes)
    return dict(zip(PART_MENU_FIELDS, [
        shop_id, canon, wording, flavour, fmt, size, temp, f"{float(r['price']):.2f}", basis,
        "no" if gst_exclusive else "yes", conf, str(tier), r["url"], source_date, observed,
        "; ".join(note), r["section"], group]))


def unique_keys(rows):
    """Where one shop lists the same item twice (same wording, size and
    temperature), put the menu section in price_note so the row key is unique.
    Exact duplicates (same price, same section) are dropped."""
    out, seen = [], {}
    for r in rows:
        k = (r["shop_id"], r["menu_wording"], r["size"], r["hot_or_iced"])
        seen.setdefault(k, []).append(r)
    for k, group in seen.items():
        if len(group) == 1:
            out += group
            continue
        kept = {}
        for r in group:                     # same price twice is one item; the
            kept[r["price_sgd"]] = r                         # later section wins
        if len(kept) == 1:
            out += kept.values()
            continue
        labels = [r["section"].strip() for r in kept.values()]
        for i, r in enumerate(kept.values(), 1):
            label = r["section"].strip() or f"listing {i}"
            if labels.count(r["section"].strip()) > 1:
                label = f"{label} (listing {i})".strip()
            head, _, rest = r["price_note"].partition("; ")
            r["price_note"] = f"{head}; section: {label}" + (f"; {rest}" if rest else "")
            out.append(r)
    return out


# ================================================================ part files
def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def shop_row(shop_id, name, district, address, postal, outlets, outlets_src, *, website="",
             instagram="", oddle_url="", menu_found="no", tier="", group="d",
             address_source_url="", no_price_reason=""):
    assert district in DISTRICTS, district
    assert menu_found in ("yes", "no", "stale_only"), menu_found
    assert re.fullmatch(r"[a-z0-9_]+", shop_id), shop_id
    return dict(zip(PART_SHOP_FIELDS, [
        shop_id, name, district, address, str(postal), str(outlets), outlets_src, website,
        instagram, oddle_url, menu_found, str(tier), group, address_source_url,
        no_price_reason]))


def write_part(part, shops, menu, launches=(), promos=()):
    """Write wave3_parts/<part>/{shops,menu,launches,promos}.csv and check them."""
    menu = unique_keys(list(menu))
    d = PARTS / part
    write_csv(d / "shops.csv", PART_SHOP_FIELDS, shops)
    write_csv(d / "menu.csv", PART_MENU_FIELDS, menu)
    write_csv(d / "launches.csv", LAUNCH_FIELDS, launches)
    write_csv(d / "promos.csv", PROMO_FIELDS, promos)
    problems = check_part(part)
    print(f"  {part}: {len(shops)} shops, {len(menu)} menu rows, {len(launches)} launches, "
          f"{len(promos)} promos, {len(problems)} problems")
    for p in problems[:40]:
        print("   !!", p)
    return problems


def check_part(part):
    d, problems = PARTS / part, []
    shops, menu = read_csv(d / "shops.csv"), read_csv(d / "menu.csv")
    ids = [s["shop_id"] for s in shops]
    problems += [f"duplicate shop_id {i}" for i in set(ids) if ids.count(i) > 1]
    priced = {m["shop_id"] for m in menu}
    for s in shops:
        if s["district"] not in DISTRICTS:
            problems.append(f"{s['shop_id']}: district '{s['district']}' not one of the 15")
        if s["shop_id"] in priced and s["menu_found"] != "yes":
            problems.append(f"{s['shop_id']}: has menu rows but menu_found={s['menu_found']}")
        if s["shop_id"] not in priced and not s["no_price_reason"]:
            problems.append(f"{s['shop_id']}: no menu rows and no no_price_reason")
        if s["postal_code"] and not re.fullmatch(r"\d{6}", s["postal_code"]):
            problems.append(f"{s['shop_id']}: postal code '{s['postal_code']}' is not 6 digits")
    keys = set()
    for m in menu:
        tag = f"{m['shop_id']} / {m['menu_wording']}"
        if m["shop_id"] not in ids:
            problems.append(f"{tag}: shop_id has no shop row")
        if m["item_canonical"] not in VOCAB:
            problems.append(f"{tag}: item_canonical '{m['item_canonical']}'")
        if m["format"] not in FORMATS:
            problems.append(f"{tag}: format '{m['format']}'")
        if not re.match(r"availability: (available|out_of_stock|seasonal)(;|$)", m["price_note"]):
            problems.append(f"{tag}: price_note does not start with an availability token")
        if m["price_confidence"] not in ("exact", "ambiguous"):
            problems.append(f"{tag}: price_confidence '{m['price_confidence']}'")
        if m["price_basis"] not in ("takeaway", "dine_in", "online_order"):
            problems.append(f"{tag}: price_basis '{m['price_basis']}'")
        if m["source_tier"] not in ("1", "4"):
            problems.append(f"{tag}: source_tier '{m['source_tier']}' is not allowed for prices")
        if not m["source_url"].startswith("http") and not m["source_url"].startswith("menu_photos/"):
            problems.append(f"{tag}: source_url '{m['source_url']}'")
        if re.search(r"grab\.com|foodpanda|deliveroo|google\.[a-z.]+/maps|maps\.google|"
                     r"instagram\.com|facebook\.com|burpple|chope\.co|eatbook|danielfooddiary|"
                     r"sethlui|misstamchiak|timeout\.com|tripadvisor|hungrygowhere|lemon8",
                     m["source_url"]):
            problems.append(f"{tag}: banned price source {m['source_url']}")
        sec = re.search(r"section: ([^;]+)", m["price_note"])
        k = (m["shop_id"], m["menu_wording"], m["size"], m["hot_or_iced"], m["observed_date"],
             sec.group(1) if sec else "")
        if k in keys:
            problems.append(f"{tag}: row key is not unique")
        keys.add(k)
    return problems
