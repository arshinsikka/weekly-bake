#!/usr/bin/env python3
"""Look up a lat/lng for every shop in shops_all.csv from its postal code.

Uses the OneMap search API, which needs no token. Results are cached in
shops_geo.csv, so a rerun only calls the API for shops that are new or whose
postal code has changed.

A location is only accepted when OneMap returns a result whose POSTAL field is
exactly the shop's postal code. Anything else is left blank and marked
unmatched. A location is never guessed.
"""

import csv
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
SHOPS = FOLDER / "shops_all.csv"
CACHE = FOLDER / "shops_geo.csv"
FIELDS = ["shop_id", "postal_code", "lat", "lng", "match_status"]
API = "https://www.onemap.gov.sg/api/common/elastic/search"
PAUSE = 0.3
BACKOFF = [5, 15, 30, 60]      # seconds to wait after each 429 before retrying


def tls_context():
    """A verifying TLS context. The python.org build on macOS ships without a
    CA bundle, so fall back to certifi, then to the system bundle."""
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats()["x509_ca"]:
        return ctx
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for bundle in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if Path(bundle).exists():
            return ssl.create_default_context(cafile=bundle)
    return ctx


TLS = tls_context()


def fetch(postal_code):
    """One search call. Raises on a network or HTTP failure, so a bad
    connection is never written to the cache as if there were no match."""
    query = urllib.parse.urlencode({
        "searchVal": postal_code, "returnGeom": "Y",
        "getAddrDetails": "Y", "pageNum": 1,
    })
    req = urllib.request.Request(API + "?" + query,
                                 headers={"User-Agent": "weekly-bake-geocoder"})
    for wait in BACKOFF + [None]:
        try:
            with urllib.request.urlopen(req, timeout=20, context=TLS) as res:
                return json.load(res)
        except urllib.error.HTTPError as err:
            # OneMap throttles bursts with a 429; wait it out rather than give up
            if err.code != 429 or wait is None:
                raise
            time.sleep(wait)


def exact(payload, postal_code):
    for hit in payload.get("results", []):
        if hit.get("POSTAL", "").strip() == postal_code:
            return hit["LATITUDE"], hit["LONGITUDE"]
    return None


def lookup(postal_code, control=None):
    """Return (lat, lng) for an exact POSTAL match, or None when there is none.

    OneMap attaches an "Authentication token missing" notice to some replies
    while still returning results. An empty reply carrying that notice could be
    a real no-match or a refusal, so it is only believed when a postal code
    that is already known to match (control) still resolves straight after.
    """
    payload = fetch(postal_code)
    found = exact(payload, postal_code)
    if found is None and payload.get("error"):
        time.sleep(PAUSE)
        if control is None or exact(fetch(control), control) is None:
            raise ValueError("OneMap: " + str(payload["error"]))
    return found


def main():
    with open(SHOPS, newline="", encoding="utf-8") as fh:
        shops = list(csv.DictReader(fh))

    cached = {}
    if CACHE.exists():
        with open(CACHE, newline="", encoding="utf-8") as fh:
            cached = {r["shop_id"]: r for r in csv.DictReader(fh)}

    by_postal = {}          # one call per postal code, however many shops share it
    control = next((r["postal_code"] for r in cached.values()
                    if r["match_status"] == "matched"), None)
    rows, calls, failed = [], 0, []
    for shop in shops:
        shop_id, postal = shop["shop_id"], shop["postal_code"].strip()
        old = cached.get(shop_id)
        if old and old["postal_code"] == postal:
            rows.append({k: old[k] for k in FIELDS})
            continue

        if not postal:
            found = None
        elif postal in by_postal:
            found = by_postal[postal]
        else:
            if calls:
                time.sleep(PAUSE)
            calls += 1
            try:
                found = lookup(postal, control)
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as err:
                failed.append((shop["shop_name"], postal, str(err)))
                continue                # not cached, so the next run tries again
            by_postal[postal] = found
            if found and control is None:
                control = postal

        rows.append({
            "shop_id": shop_id, "postal_code": postal,
            "lat": found[0] if found else "", "lng": found[1] if found else "",
            "match_status": "matched" if found else "unmatched",
        })

    with open(CACHE, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    names = {s["shop_id"]: s["shop_name"] for s in shops}
    matched = [r for r in rows if r["match_status"] == "matched"]
    unmatched = [r for r in rows if r["match_status"] != "matched"]
    print(f"shops in shops_all.csv : {len(shops)}")
    print(f"API calls this run     : {calls}")
    print(f"matched                : {len(matched)}")
    print(f"unmatched              : {len(unmatched)}")
    for r in unmatched:
        print(f"   {names[r['shop_id']]}  ({r['shop_id']}, postal {r['postal_code'] or 'missing'})")
    if failed:
        print(f"lookup failed, not cached, rerun to retry: {len(failed)}")
        for name, postal, err in failed:
            print(f"   {name}  (postal {postal}): {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
