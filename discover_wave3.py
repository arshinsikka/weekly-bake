#!/usr/bin/env python3
"""Wave 3 group d discovery: new cafes and bakeries in our 15 districts, from
the NEA "List of NEA Licensed Eating Establishments" register (data.gov.sg,
dataset d_227473e811b09731e64725f140b77697), filtered in code.

Reads:  wave3_parts/_discovery/nea_eating_establishments.csv, shops_all.csv,
        wave3_parts/stage1/shops.csv
Writes: wave3_parts/_discovery/candidates.csv

A licence is kept when its street is in one of our districts, the licensee
name looks like a cafe or bakery, it is not a hotel or a major chain, and it
is not a shop we already have. The register gives the licensee (often the
company name), not the trading name, so shop_name is the licensee name tidied.
"""
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
D = FOLDER / "wave3_parts/_discovery"
REGISTER = "https://data.gov.sg/datasets/d_227473e811b09731e64725f140b77697/view"

# street -> district. (street, low, high) limits a long road to a house-number range.
STREETS = {
    "Tiong Bahru": ["TIONG BAHRU ROAD", "ENG HOON", "YONG SIAK", "SENG POH", "MOH GUAN", "KIM TIAN",
                    "TIONG POH", "ENG WATT", "GUAN CHUAN", "CHAY YAN", "KIM PONG", "KIM CHENG",
                    "BEO CRESCENT", "JALAN MEMBINA", "LIM LIAK"],
    "Katong / Joo Chiat": ["JOO CHIAT", "KOON SENG", "TEMBELING", "CRANE ROAD", "EVERITT", "CEYLON ROAD",
                           "TANJONG KATONG ROAD", "FOWLIE", ("EAST COAST ROAD", 1, 470),
                           ("MOUNTBATTEN ROAD", 800, 899), "ONAN ROAD", "CARPMAEL"],
    "Jalan Besar": ["JALAN BESAR", "HAMILTON ROAD", "TYRWHITT", "HORNE ROAD", "CAVAN ROAD", "KING GEORGE",
                    "DESKER", "PETAIN", "MAUDE ROAD", "KITCHENER", "FRENCH ROAD", "LAVENDER STREET",
                    "FOCH ROAD", ("BENDEMEER ROAD", 1, 100), "SAM LEONG", "JELLICOE", "KELANTAN",
                    "TOWNSHEND", "VEERASAMY", "ROWELL", "KAMPONG KAPOR"],
    "Tanjong Pagar / Duxton": ["DUXTON", "TANJONG PAGAR ROAD", "CRAIG ROAD", "NEIL ROAD", "KEONG SAIK",
                               "BUKIT PASOH", "TRAS STREET", "GOPENG", "PECK SEAH", "TECK LIM",
                               "JIAK CHUAN", "BLAIR ROAD", "EVERTON", "KRETA AYER", "WALLICH",
                               "CANTONMENT ROAD", "TANJONG PAGAR PLAZA", "ENGGOR", "TRAS LINK"],
    "Telok Ayer / Amoy": ["TELOK AYER", "AMOY STREET", "CLUB STREET", "ANN SIANG", "STANLEY STREET",
                          "BOON TAT", "CROSS STREET", "MCCALLUM", "GEMMILL", "LORONG TELOK",
                          "CHINA STREET", "PEKIN STREET", "NANKIN", "ROBINSON ROAD", "CECIL STREET"],
    "Bugis / Kampong Glam / Haji Lane": ["HAJI LANE", "ARAB STREET", "BALI LANE", "BUSSORAH", "KANDAHAR",
                                         "BAGHDAD", ("NORTH BRIDGE ROAD", 420, 999),
                                         ("BEACH ROAD", 100, 400), "JALAN PINANG", "JALAN KUBOR",
                                         "SULTAN GATE", "ALIWAL", "JALAN SULTAN", "LIANG SEAH",
                                         "TAN QUEE LAN", "PURVIS", "SEAH STREET", "SYED ALWI",
                                         "JALAN KLAPA", "PAHANG STREET", "MUSCAT STREET", "OPHIR ROAD",
                                         "JALAN KLEDEK", "MIDDLE ROAD", ("VICTORIA STREET", 200, 999)],
    "Orchard / Somerset": ["ORCHARD ROAD", "SOMERSET ROAD", "KILLINEY", "EMERALD HILL", "CUPPAGE",
                           "SCOTTS ROAD", "ORCHARD TURN", "ORCHARD BOULEVARD", "GRANGE ROAD", "EXETER",
                           "DEVONSHIRE", "PENANG ROAD", "HANDY ROAD", "CAIRNHILL", "BIDEFORD",
                           "ORCHARD LINK", "KOEK ROAD", "TANGLIN ROAD", "ORCHARD CENTRAL"],
    "River Valley / Robertson Quay": ["RIVER VALLEY ROAD", "ROBERTSON QUAY", "MARTIN ROAD", "MOHAMED SULTAN",
                                      "RODYK", "UNITY STREET", "KIM SENG", "ZION ROAD", "NANSON",
                                      "MERBAU", "JIAK KIM", "KIM YAM", "TONG WATT",
                                      ("HAVELOCK ROAD", 500, 800)],
    "Holland Village / Dempsey": ["HOLLAND AVENUE", "HOLLAND VILLAGE", "LORONG MAMBONG", "LORONG LIPUT",
                                  "JALAN MERAH SAGA", "CHIP BEE", "DEMPSEY", "HARDING ROAD", "LOEWEN",
                                  ("HOLLAND ROAD", 200, 300), "TAMAN WARNA", "HOLLAND CLOSE", "HOLLAND DRIVE"],
    "Bukit Timah / Sixth Avenue": [("BUKIT TIMAH ROAD", 400, 999), "SIXTH AVENUE", "JALAN SERENE", "HILLCREST",
                                   "EMPRESS ROAD", "LORONG KILAT", "CHEONG CHIN NAM", "JALAN JURONG KECHIL",
                                   "GREENWOOD", "CORONATION", "CHUN TIN", "YUK TONG", "BINJAI PARK",
                                   ("UPPER BUKIT TIMAH ROAD", 1, 200), "JALAN ANAK BUKIT"],
    "Serangoon Gardens / Kovan": ["SERANGOON GARDEN", "MAJU AVENUE", "KENSINGTON PARK", "LICHFIELD",
                                  "CHARTWELL", "FARLEIGH", "SIMON ROAD", ("UPPER SERANGOON ROAD", 900, 1100),
                                  ("YIO CHU KANG ROAD", 1, 100), "KOVAN ROAD", "HOUGANG STREET 21",
                                  "MYVILLE", "PENSHURST", "TEOW HOCK", "CRICHTON"],
    "Upper Thomson": ["UPPER THOMSON ROAD", "SIN MING", "THOMSON RIDGE", "SHUNFU", "JASMINE ROAD",
                      "SOO CHOW", "CASUARINA", "JALAN LEBAN", "JALAN KURAS"],
    "Novena / Balestier": ["BALESTIER ROAD", "IRRAWADDY", ("THOMSON ROAD", 1, 400), "AH HOOD", "SINARAN",
                           "NOVENA", "MOULMEIN", "JALAN KEMAMAN", "MANDALAY", "NEWTON ROAD", "GOLDHILL",
                           "ZHONGSHAN", "BOON TECK", "JALAN RAJAH", "WHAMPOA"],
    "East Coast / Siglap": ["UPPER EAST COAST ROAD", "SIGLAP", "FRANKEL", "SWAN LAKE", "LUCKY HEIGHTS",
                            "JALAN TUA KONG", ("EAST COAST ROAD", 471, 9999), "GOODMAN ROAD",
                            "ELITE PARK", "FIGARO", "JALAN PARI BURONG"],
    "Queenstown / Alexandra": ["ALEXANDRA ROAD", "JALAN BUKIT MERAH", "COMMONWEALTH", "TANGLIN HALT",
                               "STIRLING ROAD", "MEI LING", "MARGARET DRIVE", "QUEENSWAY", "DAWSON",
                               "STRATHMORE", "DEPOT ROAD", "BUKIT MERAH LANE 1", "HENDERSON ROAD"],
}
KIND = re.compile(r"CAF[EÉ]|COFFEE|KOPI(?!TIAM)|BAK(E|ERY|ES|EHOUSE|ER)\b|BAKER|PATISS|P[AÂ]TISS|BOULANG|"
                  r"ROAST(ER|ERS|ERY)|BREAD|\bCAKE|PASTR|ESPRESSO|\bBREW|DOUGH|CROISSANT|BAGEL|"
                  r"DESSERT|GELAT|WAFFLE|\bTARTS?\b|CONFECTION|BARISTA|MATCHA|PATISSERIE")
NOT_KIND = re.compile(r"COFFEE ?SHOP|COFFEESHOP|KOPITIAM|COFFEE HOUSE|EATING HOUSE|FOOD ?COURT|CANTEEN|"
                      r"RESTAURANT|SEAFOOD|BAK KUT|BAKKUT|HOTEL|RESORT|\bINN\b|HOSTEL|CLUB|CATER|"
                      r"BAR\b|PUB\b|BISTRO|KARAOKE|KTV|STEAM|NOODLE|RICE|BBQ|HOTPOT|PRATA|CURRY|"
                      r"TEA ?HOUSE|BUBBLE|CHICKEN|ENTERTAINMENT|KHANA|MINIMART|SUPERMARKET|TZE CHAR|ZI CHAR|HAWKER|FOODSTUFF")
CHAINS = re.compile(r"STARBUCKS|BREADTALK|TOAST ?BOX|CHATERAISE|DELIFRANCE|D[EÉ]LIFRANCE|CEDELE|BEARD PAPA|"
                    r"TOUS LES JOURS|POLAR PUFFS|GOKOKU|OLD CHANG KEE|AUNTIE ANNE|LUCKIN|COFFEE BEAN|"
                    r"TAI CHEONG|CAT (&|AND) THE FIDDLE|YA KUN|PARIS BAGUETTE|FOUR LEAVES|SWEE HENG|"
                    r"MR BEAN|DUNKIN|KRISPY|FLASH COFFEE|KENANGAN|HUGGS|MCDONALD|HANS |FUN TOAST|"
                    r"WANG CAFE|KILLINEY|BENGAWAN|PRIMA DELI|BARCOOK|DUKE BAKERY|PARIS FRANCE|"
                    r"JOLLIBEAN|BAKER TALENT|PROVENCE|COTTI|ZUS|TIM HORTONS|BACHA|TWG|PAUL |"
                    r"SELECT GROUP|KOUFU|KOPITIAM|FOOD REPUBLIC|NTUC|SATS|COMPASS GROUP|SODEXO|"
                    r"PASTAMANIA|JOLLIBEE|SUBWAY|HEAVENLY WANG|O'COFFEE|OLDTOWN|NANYANG OLD")
BIG_OUTLET_MIN = 10


def district_of(address):
    m = re.match(r"\s*(?:BLK |BLOCK )?(\d+)", address)
    number = int(m.group(1)) if m else None
    for district, streets in STREETS.items():
        for s in streets:
            name, low, high = (s, None, None) if isinstance(s, str) else s
            if name in address and (low is None or (number and low <= number <= high)):
                return district
    return None


def tidy(name):
    n = re.sub(r"\b(PTE\.?|PRIVATE|LTD\.?|LIMITED|LLP|L\.L\.P\.?|\(S\)|\(SINGAPORE\)|\(SG\)|"
               r"HOLDINGS?|GROUP|ENTERPRISES?|TRADING|INTERNATIONAL|F&B|F & B|CONCEPTS?)\b", " ", name)
    n = re.sub(r"[.,]+$|\s+", " ", n).strip(" .,-&")
    return n.title().replace("'S", "'s")


def key(name):
    return re.sub(r"[^a-z0-9]+", "", tidy(name).lower())


rows = list(csv.DictReader(open(D / "nea_eating_establishments.csv", encoding="utf-8", errors="replace")))
per_licensee = Counter(r["licensee_name"].strip() for r in rows)

known = [dict(r) for r in csv.DictReader(open(FOLDER / "shops_all.csv"))]
known += [dict(r) for r in csv.DictReader(open(FOLDER / "wave3_parts/stage1/shops.csv"))]
known_postal = defaultdict(list)
for k in known:
    known_postal[k["postal_code"]].append(re.sub(r"[^a-z0-9]+", "", k["shop_name"].lower()))
known_names = {re.sub(r"[^a-z0-9]+", "", re.sub(r"\(.*?\)", "", k["shop_name"]).lower()) for k in known}

out, seen, why = [], set(), Counter()
for r in rows:
    name, addr = r["licensee_name"].strip(), r["premises_address"].strip().upper()
    district = district_of(addr)
    if not district:
        continue
    why["in a district"] += 1
    up = name.upper()
    if not KIND.search(up) or NOT_KIND.search(up) or NOT_KIND.search(addr.split("SINGAPORE")[0][-60:]):
        continue
    why["looks like a cafe or bakery"] += 1
    if CHAINS.search(up) or per_licensee[name] >= BIG_OUTLET_MIN or "HOTEL" in addr:
        why["dropped: chain, 10+ licences, or hotel"] += 1
        continue
    pm = re.search(r"SINGAPORE (\d{6})", addr)
    postal = pm.group(1) if pm else ""
    k = key(name)
    if any(k and (k in n or n in k) for n in known_postal.get(postal, [])) or k in known_names:
        why["dropped: already in our files"] += 1
        continue
    if (k, postal) in seen:
        continue
    seen.add((k, postal))
    street = re.sub(r"\s+SINGAPORE \d{6}.*$", "", r["premises_address"].strip()).title()
    shop_id = re.sub(r"[^a-z0-9]+", "_", tidy(name).lower()).strip("_")[:40] + "_" + postal
    out.append(dict(shop_id=shop_id, shop_name=tidy(name), licensee_name=name, district=district,
                    address=street, postal_code=postal, outlet_count=per_licensee[name],
                    outlet_count_source=REGISTER, licence_number=r["licence_number"]))

with open(D / "candidates.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(out[0]))
    w.writeheader()
    w.writerows(out)
for k, v in why.items():
    print(f"  {k:<42}{v:>6}")
print(f"  candidates written: {len(out)}")
for d, n in sorted(Counter(o["district"] for o in out).items()):
    print(f"     {d:<36}{n:>4}")
