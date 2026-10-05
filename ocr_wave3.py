#!/usr/bin/env python3
"""Wave 3 OCR pass for rows whose price sits inside a menu image or PDF.

Downloads each source image or PDF once into wave3_parts/ocr_cache/, runs
Tesseract on it once, and caches the text as JSON beside the file. Reads
menu_items_wave3.csv and verification_wave3.csv; writes neither.
merge_wave3.py reads the cached text to decide which rows pass.

Needs: brew install tesseract; pip install pytesseract pymupdf pillow
"""
import csv
import hashlib
import io
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import subprocess

import pymupdf
import pytesseract
from PIL import Image

FOLDER = Path(__file__).resolve().parent
CACHE = FOLDER / "wave3_parts" / "ocr_cache"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
IMAGE_REASON = "price is inside an image or PDF"
PSMS = (3, 6, 11)          # automatic layout, one uniform block, sparse text


def stem(url):
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def image_urls():
    with open(FOLDER / "verification_wave3.csv", newline="", encoding="utf-8") as fh:
        return sorted({v["source_url"] for v in csv.DictReader(fh)
                       if v["check"] == "price" and v["reason"].startswith(IMAGE_REASON)})


def download(url):
    """Return the local file for this url, fetching it on first use only."""
    hit = next(iter(CACHE.glob(stem(url) + ".src.*")), None)
    if hit:
        return hit
    fetch = url
    drive = re.search(r"drive\.google\.com/file/d/([^/]+)", url)
    if drive:
        fetch = f"https://drive.google.com/uc?export=download&id={drive.group(1)}"
    # curl, so the system certificate store is used
    body = subprocess.run(["curl", "-sSfL", "--max-time", "120", "-A", UA, fetch],
                          check=True, capture_output=True).stdout
    ext = "pdf" if body[:5] == b"%PDF-" else (Image.open(io.BytesIO(body)).format or "img").lower()
    path = CACHE / f"{stem(url)}.src.{ext}"
    path.write_bytes(body)
    return path


def pages(path):
    if path.suffix == ".pdf":
        with pymupdf.open(path) as doc:
            for page in doc:
                pix = page.get_pixmap(dpi=300)
                yield Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    else:
        img = Image.open(path).convert("RGB")
        if max(img.size) < 2400:                       # small photos read better enlarged
            scale = 2400 / max(img.size)
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        yield img


def ocr(url):
    """[{page, psm, block, par, line, text}] for every text line Tesseract finds."""
    out_path = CACHE / f"{stem(url)}.ocr.json"
    if out_path.exists():
        return url, "cached", len(json.loads(out_path.read_text())["lines"])
    try:
        src = download(url)
    except Exception as err:
        return url, f"download failed: {str(err)[:100]}", 0
    lines = []
    for n, img in enumerate(pages(src)):
        for psm in PSMS:
            d = pytesseract.image_to_data(img, config=f"--psm {psm}",
                                          output_type=pytesseract.Output.DICT)
            grouped = {}
            for i, word in enumerate(d["text"]):
                if word.strip():
                    key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
                    grouped.setdefault(key, []).append(word)
            lines += [dict(page=n, psm=psm, block=b, par=p, line=l, text=" ".join(w))
                      for (b, p, l), w in grouped.items()]
    out_path.write_text(json.dumps(dict(url=url, file=src.name, lines=lines), ensure_ascii=False))
    return url, "ok", len(lines)


if __name__ == "__main__":
    CACHE.mkdir(parents=True, exist_ok=True)
    urls = image_urls()
    with ProcessPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(ocr, urls))
    for url, status, n in results:
        print(f"  {status:<12}{n:>6} lines  {url[:110]}")
    bad = [r for r in results if r[1] not in ("ok", "cached")]
    print(f"  sources: {len(urls)}; read: {len(urls) - len(bad)}; not read: {len(bad)}")
    sys.exit(1 if bad else 0)
