#!/usr/bin/env python3
"""
download_tesla_data_web.py - Collect Tesla app "Charge Stats" screenshots from the web.

Starting from one of your screenshots, this script:
  1. Runs a Google reverse image search on it (the Google Cloud Vision API's "web detection").
  2. Follows the results: downloads the matching and visually similar images, and the images on the
     pages that contain a match.
  3. Keeps only screenshots of a daily bar chart in kWh: one row crosses a marker for each day of the
     date range at the top of the screen (a month, "Last 31 Days" or a custom range; a bar, or a dot on
     days without charging), at least 3 of them are bars, and the chart is labeled kWh, not $.
     Light- and dark-mode screenshots both work.
  4. Saves each one as imageNNNN.png (or .jpg) and searches again with it, until --size are saved.

With --search DIR it skips the API and uses Google image searches you ran in your browser and saved
(File > Save Page As) in DIR instead: it follows every result in them, ignoring --size.

It respects robots.txt, waits --delay seconds between requests to the same site, and waits longer and
longer (or as long as the server asks) after a failed request. manifest.csv in the output directory
records where each screenshot came from; rerun the same command to continue an interrupted run.

Requirements:
  pip install -r requirements.txt   (plus the Tesseract OCR program, see README.md)
  without --search: a Google Cloud API key with the Cloud Vision API enabled (--api-key or GOOGLE_API_KEY)

Usage:
  python download_tesla_data_web.py --input data/IMG_5682.PNG --output web_data [--size 10000]
  python download_tesla_data_web.py --search google-search --output web_data
"""

import argparse
import base64
import calendar
import collections
import contextlib
import csv
import datetime as dt
import hashlib
import html
import io
import os
import random
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import numpy as np
from PIL import Image, ImageOps

try:
    import pytesseract
    import requests
    from tqdm import tqdm
except ImportError as e:
    sys.exit(f"{e.name} is not installed:  pip install -r requirements.txt")

try:  # optional HEIC support
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except ImportError:
    pass

VISION_URL = "https://vision.googleapis.com/v1/images:annotate"
ROBOTS_NAME = "download_tesla_data_web"
USER_AGENT = f"{ROBOTS_NAME}/1.0 (collects Tesla app screenshots; respects robots.txt)"
RETRY_STATUS = {429, 500, 502, 503, 504}  # worth retrying after a wait
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGES_PER_PAGE = 40
MIN_SIDE = 300  # px; smaller images are thumbnails or icons, too small to read
DUPLICATE_BITS = 16  # chart hashes this close (of 256 bits) are the same chart at another size or quality
FORMATS = {"png": dict(format="PNG"), "jpg": dict(format="JPEG", quality=95)}  # --format -> PIL save()
MANIFEST_FIELDS = ["file", "image_url", "sha256", "chart_hash"]
MONTHS = {m[:3].lower(): i for i, m in enumerate(calendar.month_name) if m}  # "jan" -> 1

VERBOSE = False


def say(msg):  # details, shown only with --debug
    if VERBOSE:
        tqdm.write(msg)


class FatalError(Exception):
    pass


# --------------------------------------------------------------------------- polite HTTP

class Fetcher:
    """HTTP requests with a minimum delay per site, robots.txt checks, and exponential backoff on failures."""

    def __init__(self, delay, retries=6, backoff=2.0):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.delay, self.retries, self.backoff = delay, retries, backoff
        self.last = {}  # site -> time of the last request to it
        self.robots = {}  # site -> RobotFileParser
        self.waits = 0  # requests retried after a failure

    def request(self, method, url, retries=None, **kw):
        """The response, after retrying failures (429, 5xx, network errors) with growing waits; None if
        every attempt failed with a network error."""
        site = urlsplit(url).netloc
        retries = self.retries if retries is None else retries
        r = None
        for attempt in range(retries + 1):
            wait = self.last.get(site, 0) + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last[site] = time.monotonic()
            try:
                r = self.session.request(method, url, timeout=(10, 30), **kw)
                problem = None if r.status_code not in RETRY_STATUS else f"HTTP {r.status_code}"
            except requests.RequestException as e:
                r, problem = None, type(e).__name__
            if problem is None or attempt == retries:
                return r
            wait = self.backoff * 2 ** attempt + random.uniform(0, 1)  # 2, 4, 8, ... s
            retry_after = r.headers.get("Retry-After", "") if r is not None else ""
            if retry_after.isdigit():
                wait = max(wait, int(retry_after))
            wait = min(wait, 600)
            self.waits += 1
            say(f"    {site}: {problem}; waiting {wait:.0f} s before trying again")
            if r is not None:
                r.close()
            time.sleep(wait)
        return r

    def allowed(self, url):
        """Whether the site's robots.txt lets us fetch url."""
        parts = urlsplit(url)
        site = f"{parts.scheme}://{parts.netloc}"
        if site not in self.robots:
            rp = RobotFileParser()
            r = self.request("GET", site + "/robots.txt", retries=1)
            if r is None or r.status_code >= 500:
                rp.disallow_all = True  # can't tell, so stay out
            elif r.status_code >= 400:
                rp.allow_all = True  # no robots.txt
            else:
                rp.parse(r.text.splitlines())
            self.robots[site] = rp
        return self.robots[site].can_fetch(ROBOTS_NAME, url)


# --------------------------------------------------------------------------- search & follow results

def web_detection(fetcher, api_key, path, max_results):
    """Google reverse image search for the image at path (Cloud Vision API web detection)."""
    img = Image.open(path).convert("RGB")
    img.thumbnail((1600, 1600))  # a smaller upload; plenty for matching
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    body = {"requests": [{"image": {"content": base64.b64encode(buf.getvalue()).decode()},
                          "features": [{"type": "WEB_DETECTION", "maxResults": max_results}]}]}
    r = fetcher.request("POST", VISION_URL, params={"key": api_key}, json=body)
    if r is None:
        raise FatalError("could not reach the Cloud Vision API")
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code != 200:
        msg = data.get("error", {}).get("message", f"HTTP {r.status_code}")
        if r.status_code in (401, 403):
            raise FatalError(f"the Cloud Vision API refused the request: {msg}\n"
                             "Check the API key, and that the Cloud Vision API is enabled for its project.")
        if r.status_code == 429:
            raise FatalError(f"the Cloud Vision API quota is used up for now: {msg}\n"
                             "Try again later; rerun the same command to continue.")
        say(f"    search failed: {msg}")
        return {}
    resp = data.get("responses", [{}])[0]
    if "error" in resp:
        say(f"    search failed: {resp['error'].get('message')}")
    return resp.get("webDetection", {})


class ImageLinks(HTMLParser):
    """Image URLs on a page: og:image/twitter:image, <img src/srcset> and links to image files."""

    def __init__(self, base):
        super().__init__()
        self.base, self.urls = base, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("property") or a.get("name")) in ("og:image", "twitter:image"):
            self.add(a.get("content"))
        elif tag == "img":
            if a.get("srcset"):
                self.add(a["srcset"].split(",")[-1].split()[0])  # usually the largest
            self.add(a.get("src") or a.get("data-src"))
        elif tag == "a" and re.search(r"\.(png|jpe?g|webp)(\?|$)", a.get("href") or "", re.I):
            self.add(a["href"])

    def add(self, url):
        if url and not url.startswith("data:"):
            self.urls.append(urljoin(self.base, url.strip()))


def page_images(fetcher, url):
    """(image URLs found on the web page at url, None), or ([], why the page wasn't read)."""
    if not url or not url.startswith(("http://", "https://")):
        return [], "not a web URL"
    if not fetcher.allowed(url):
        return [], "blocked by robots.txt"
    r = fetcher.request("GET", url)
    if r is None or r.status_code != 200:
        return [], f"download failed{f' (HTTP {r.status_code})' if r is not None else ''}"
    if "html" not in r.headers.get("Content-Type", ""):
        return [], "not a web page"
    parser = ImageLinks(r.url)
    parser.feed(r.text)
    return list(dict.fromkeys(parser.urls))[:MAX_IMAGES_PER_PAGE], None


def candidate_urls(fetcher, wd):
    """Image URLs from a search result: the matching and similar images first, then the images on the
    pages that contain a match (those pages are fetched only once the earlier images are used up)."""
    pages = wd.get("pagesWithMatchingImages", [])
    for group in [wd, *pages]:
        for key in ("fullMatchingImages", "partialMatchingImages", "visuallySimilarImages"):
            for match in group.get(key, []):
                yield match.get("url")
    for page in pages:
        urls, why = page_images(fetcher, page.get("url"))
        say(f"    {'following' if why is None else f'skipped ({why})'}: {page.get('url')}")
        yield from urls


def is_google(url):
    return re.search(r"(^|\.)(google\.[a-z.]+|gstatic\.com)$", urlsplit(url).netloc.lower()) is not None


class Links(HTMLParser):
    """The <a href> links on a page."""

    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href")
        if tag == "a" and href:
            self.hrefs.append(href)


def saved_search_results(path):
    """(image URLs, page URLs) of the results on a Google image search page saved from the browser."""
    page = path.read_text(encoding="utf-8", errors="replace")
    parser = Links()
    parser.feed(page)
    pages = []
    for href in parser.hrefs:
        if is_google(href) and urlsplit(href).path == "/url":  # Google's redirect to a result
            query = parse_qs(urlsplit(href).query)
            href = (query.get("q") or query.get("url") or [""])[0]
        if href.startswith(("http://", "https://")) and not is_google(href):
            pages.append(href)
    # the results' image links are in the page's embedded data, with escapes like = for "="
    data = re.sub(r"\\(?:u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2}))",
                  lambda m: chr(int(m.group(1) or m.group(2), 16)), page).replace("\\/", "/")
    images = [u for u in re.findall(r"https?://[^\s\"'<>\\]+?\.(?:jpe?g|png|webp)(?:\?[^\s\"'<>\\]*)?",
                                    html.unescape(data), re.I) if not is_google(u)]
    return images, pages


def download_image(fetcher, url):
    """(bytes, None), or (None, why it wasn't downloaded)."""
    if not url.startswith(("http://", "https://")):
        return None, "not a web URL"
    if re.search(r"\.(svg|gif|ico)(\?|$)", url, re.I):
        return None, "not a screenshot format"
    if not fetcher.allowed(url):
        return None, "blocked by robots.txt"
    r = fetcher.request("GET", url, stream=True)
    if r is None:
        return None, "download failed"
    with r:
        if r.status_code != 200:
            return None, f"download failed (HTTP {r.status_code})"
        kind = r.headers.get("Content-Type", "").lower()
        if not kind.startswith(("image/", "application/octet-stream", "binary/octet-stream")):
            return None, "not an image"
        data = bytearray()
        for chunk in r.iter_content(65536):
            data += chunk
            if len(data) > MAX_IMAGE_BYTES:
                return None, "too large"
    return bytes(data), None


# --------------------------------------------------------------------------- is it a daily kWh bar chart?

def to_rgb(img):
    img.load()
    if img.mode in ("P", "LA", "PA") or "transparency" in img.info:  # via RGBA, so transparency is handled
        img = img.convert("RGBA")
    return img.convert("RGB")


def steady_run(xs, pitch):
    """Indices of the longest run of xs spaced one pitch apart, skipping stray marks in between."""
    best = []
    for i in range(len(xs)):
        run = [i]
        for j in range(i + 1, len(xs)):
            gap = xs[j] - xs[run[-1]]
            if gap > 1.15 * pitch:
                break
            if gap >= 0.85 * pitch:
                run.append(j)
        best = max(best, run, key=len)
    return best


def header_days(img):
    """How many days the chart covers, from the date range at the top of the screen ("Last 31 Days",
    "December 2024", "Jun 19 – Aug 12, 2025"): 0 if it's by month or week ("Last 12 Months"), None if
    there's no date range to read."""
    top = img.crop((0, 0, img.width, int(img.height * 0.15))).convert("L")
    if np.asarray(top).mean() < 128:  # dark mode: Tesseract wants dark text on a light background
        top = ImageOps.invert(top)
    text = " ".join(pytesseract.image_to_string(top, config="--psm 6").split())
    if m := re.search(r"last\s+(\d+)\s+(day|week|month|year)", text, re.I):
        return int(m[1]) if m[2].lower() == "day" else 0
    def month(name):
        return MONTHS.get(name[:3].lower())

    if m := re.search(r"\b([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?\s*[-–—~]\s*(?:([a-z]{3})[a-z]*\.?\s+)?"
                      r"(\d{1,2}),?\s+(\d{4})", text, re.I):
        m1, m2 = month(m[1]), month(m[4] or m[1])
        if m1 and m2:
            y2 = int(m[6])
            y1 = int(m[3]) if m[3] else y2 - (m1 > m2)
            with contextlib.suppress(ValueError):
                return (dt.date(y2, m2, int(m[5])) - dt.date(y1, m1, int(m[2]))).days + 1
    if m := re.search(r"\b(" + "|".join(calendar.month_name[1:]) + r")\s+(\d{4})\b", text, re.I):
        return calendar.monthrange(int(m[2]), month(m[1]))[1]
    return None


def find_day_markers(ink, lo=28, hi=31):
    """Where the chart's day markers could be: in a daily chart every day has a bar or a small "no charge"
    dot, so one row crosses a mark per day, evenly spaced (a year view has 12). Returns a dict(y, pitch,
    xs, width) for each group of rows with lo..hi such marks, top to bottom."""
    W = ink.shape[1]
    padded = np.zeros((ink.shape[0], W + 2), np.int8)
    padded[:, 1:-1] = ink
    edges = np.diff(padded, axis=1)
    groups = []
    for y in range(ink.shape[0]):
        starts, ends = np.flatnonzero(edges[y] == 1), np.flatnonzero(edges[y] == -1)
        widths = ends - starts
        keep = (widths >= 3) & (widths <= W / 25)
        if keep.sum() < lo:
            continue
        xs, widths = (starts + ends - 1)[keep] / 2, widths[keep]
        pitch = float(np.median(np.diff(xs)))
        run = steady_run(xs, pitch)
        if lo <= len(run) <= hi:
            if not groups or y > groups[-1][-1]["y"] + 1:
                groups.append([])
            groups[-1].append(dict(y=y, pitch=pitch, xs=xs[run], width=float(np.median(widths[run]))))
    return [g[len(g) // 2] for g in groups]  # the middle row of each group


def bars_on(ink, markers):
    """(top, bottom) of each mark in the marker row that is a bar: at least twice as tall as it is wide
    (a "no charge" dot isn't), with background beside it, and standing on the same baseline as the other
    bars (unlike shapes inside a picture such as the map)."""
    def run_end(col, step, gap=3):  # last ink pixel going up or down from y, across the thin gaps
        end = i = y                       # between a stacked bar's segments
        while 0 <= i + step < len(col) and abs(i + step - end) <= gap + 1:
            i += step
            if col[i]:
                end = i
        return end

    y, W = markers["y"], ink.shape[1]
    bars = []
    for x in markers["xs"]:
        col = ink[:, int(round(x))]
        top, bottom = run_end(col, -1), run_end(col, 1)
        if bottom - top + 1 < 2 * markers["width"]:
            continue
        beside = [ink[top:bottom + 1, int(round(x + side * markers["pitch"] / 2))].mean()
                  for side in (-1, 1) if 0 <= x + side * markers["pitch"] / 2 < W]
        if beside and min(beside) < 0.3:
            bars.append((top, bottom))
    baseline = np.median([bottom for _, bottom in bars]) if bars else 0
    return [(top, bottom) for top, bottom in bars if abs(bottom - baseline) <= max(2, markers["width"] / 2)]


def chart_rows(arr, markers):
    """The image rows of the chart, from the marker row up (the app's chart is about 0.18 widths tall)."""
    W = arr.shape[1]
    return arr[max(int(markers["y"] - 0.22 * W), 0):int(markers["y"] + 0.03 * W)]


def chart_hash(arr, markers):
    """256-bit difference hash of the chart, to recognize the same chart at another size or quality."""
    g = np.asarray(Image.fromarray(chart_rows(arr, markers)).convert("L").resize((33, 8), Image.LANCZOS), int)
    return int("".join("1" if b else "0" for b in (g[:, 1:] > g[:, :-1]).flatten()), 2)


def axis_words(arr, bg, markers):
    """Words Tesseract finds at the right of the chart, where the y-axis labels are ("179 kWh", "$31")."""
    strip = chart_rows(arr, markers)[:, int(arr.shape[1] * 0.7):].astype(np.int16)
    # black text on white, whatever the colors: the gray labels are faint otherwise
    text = Image.fromarray(np.where(np.abs(strip - bg).sum(2) > 100, 0, 255).astype(np.uint8))
    text = text.resize((text.width * 2, text.height * 2), Image.LANCZOS)
    return pytesseract.image_to_string(text, config="--psm 6").split()


def check_screenshot(data):
    """(chart hash, None) for a screenshot of a daily bar chart in kWh, or (None, why not)."""
    try:
        img = to_rgb(Image.open(io.BytesIO(data)))
    except Exception:
        return None, "unreadable image"
    if min(img.size) < MIN_SIDE:
        return None, "too small"
    if img.width < 1000:  # mark detection and OCR need some resolution
        img = img.resize((1000, round(img.height * 1000 / img.width)), Image.LANCZOS)
    days = header_days(img)
    if days == 0:
        return None, "chart by month or week, not by day"
    arr = np.asarray(img)
    a = arr.astype(np.int16)
    vals, counts = np.unique((a[::4, ::4] // 8 * 8).reshape(-1, 3), axis=0, return_counts=True)
    bg = vals[counts.argmax()] + 4  # the most common color
    ink = np.abs(a - bg).sum(2) > 60  # differs from the background
    # one mark per day of the date range on screen; without one, a month's worth
    candidates = find_day_markers(ink, *((max(days - 1, 5), days + 1) if days else (28, 31)))
    if not candidates:
        return None, "no daily chart"
    for markers in candidates:  # the chart's row is the topmost with bars on it
        bars = bars_on(ink, markers)
        # the tallest bar reaches the top of the chart (~0.18 widths); letters in a line of text don't
        if len(bars) >= 3 and max(bottom - top for top, bottom in bars) >= 0.08 * ink.shape[1]:
            markers = dict(markers, y=int(np.median([bottom for _, bottom in bars])))  # the chart's baseline
            break
    else:
        return None, "no bars"
    words = axis_words(arr, bg, markers)
    if any("$" in w for w in words):
        return None, "chart in $, not kWh"
    if not any(re.fullmatch(r"\W*[km]?[wv]h\W*", w, re.I) for w in words):  # OCR sometimes drops the k
        return None, "no kWh on the chart"
    return chart_hash(arr, markers), None


# --------------------------------------------------------------------------- main

def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description="Collect Tesla Charge Stats screenshots (daily kWh bar charts) "
                                             "found by a Google reverse image search.")
    ap.add_argument("--input", help="screenshot to search with, e.g. data/IMG_5682.PNG")
    ap.add_argument("--search", metavar="DIR",
                    help="instead of searching with the API, use the Google image search results saved from the "
                         "browser (File > Save Page As) in DIR; downloads everything they lead to, ignoring --size")
    ap.add_argument("--output", default="web_data",
                    help="directory to save the screenshots in (default: web_data)")
    ap.add_argument("--size", type=int, default=10000,
                    help="stop once this many screenshots are saved (default: 10000)")
    ap.add_argument("--format", choices=sorted(FORMATS), default="png",
                    help="save every screenshot in this format (default: png)")
    ap.add_argument("--api-key", default=os.environ.get("GOOGLE_API_KEY"),
                    help="Google Cloud API key with the Cloud Vision API enabled (default: $GOOGLE_API_KEY)")
    ap.add_argument("--max-results", type=int, default=100,
                    help="results to ask for per search (default: 100)")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds to wait between requests to the same site (default: 1)")
    ap.add_argument("--debug", action="store_true", help="print why each image was saved or discarded")
    ap.add_argument("--tesseract-cmd", help="path to the tesseract executable if it is not on PATH")
    args = ap.parse_args()
    if args.search:
        search_pages = sorted(Path(args.search).glob("*.htm*")) if Path(args.search).is_dir() else []
        if not search_pages:
            ap.error(f"--search: no saved search pages (.html files) in {args.search}")
    else:
        if not args.input:
            ap.error("--input is needed (or use --search with search results saved from the browser)")
        if not args.api_key:
            ap.error("a Google Cloud API key is needed: pass --api-key or set GOOGLE_API_KEY (see README.md)")
        if args.size < 1:
            ap.error("--size must be at least 1")
    if args.input and not Path(args.input).is_file():
        ap.error(f"--input: {args.input} is not a file")
    if args.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = args.tesseract_cmd
    try:
        pytesseract.get_tesseract_version()
    except pytesseract.TesseractNotFoundError:
        sys.exit("Tesseract binary not found. Install it (brew install tesseract / apt install tesseract-ocr) "
                 "or pass --tesseract-cmd /path/to/tesseract")
    VERBOSE = args.debug
    t0 = time.perf_counter()

    # pick up where an earlier run stopped
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    manifest, searched_log = out / "manifest.csv", out / "searched.txt"
    rows = []
    if manifest.exists():
        with open(manifest, newline="") as fh:
            rows = list(csv.DictReader(fh))
    searched = set(searched_log.read_text().split()) if searched_log.exists() else set()
    seen_urls = {r["image_url"] for r in rows}
    seen_sha = {r["sha256"] for r in rows}
    chart_hashes = [int(r["chart_hash"]) for r in rows]
    numbers = [int(m.group(1)) for p in out.iterdir() if (m := re.fullmatch(r"image(\d+)\.\w+", p.name))]
    next_n = max(numbers, default=-1) + 1
    nsaved_before = len(rows)

    queue = collections.deque()  # (image, sha256) to search with
    if args.input:
        input_data = Path(args.input).read_bytes()
        input_sha = hashlib.sha256(input_data).hexdigest()
        seen_sha.add(input_sha)
        input_hash, _ = check_screenshot(input_data)
        if input_hash is not None:  # don't save your own screenshot if it turns up online
            chart_hashes.append(input_hash)
        queue.extend((path, sha) for path, sha in [(Path(args.input), input_sha)]
                     + [(out / r["file"], r["sha256"]) for r in rows] if sha not in searched)
    if args.search:  # every result in the saved pages, once (searches can overlap)
        images, pages = [], []
        for p in search_pages:
            found_images, found_pages = saved_search_results(p)
            images += found_images
            pages += found_pages
        # originals (no ?width=... resizing) first, so the best copy is the one kept
        images = sorted(dict.fromkeys(images), key=lambda u: bool(urlsplit(u).query))
        pages = list(dict.fromkeys(pages))
        digits = max(4, len(str(next_n + len(images) + MAX_IMAGES_PER_PAGE * len(pages))))
    else:
        digits = max(4, len(str(args.size - 1)))

    fetcher = Fetcher(args.delay)
    nsearches = nchecked = 0
    discarded, skipped_pages = collections.Counter(), collections.Counter()
    stop_reason = None
    try:
        with tqdm(total=len(images) + len(pages) if args.search else args.size,
                  initial=0 if args.search else min(len(rows), args.size),
                  desc="search results" if args.search else "screenshots", unit="result" if args.search else "img",
                  bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [elapsed {elapsed}, ETA {remaining}, "
                             "{rate_fmt}{postfix}]", disable=None) as bar, \
                open(manifest, "a", newline="") as mf:
            writer = csv.DictWriter(mf, fieldnames=MANIFEST_FIELDS)
            if not rows:
                writer.writeheader()

            def status():
                done = f"saved: {len(rows) - nsaved_before}" if args.search else f"searches: {nsearches}"
                bar.set_postfix_str(f"{done}, checked: {nchecked}, discarded: {sum(discarded.values())}",
                                    refresh=True)

            def consider(url):
                """Download url and save it if it's a new daily kWh chart. Returns (path, sha256) if saved."""
                nonlocal nchecked, next_n
                if not url or url in seen_urls:
                    return None
                seen_urls.add(url)
                data, why = download_image(fetcher, url)
                h = None
                if data is not None:
                    sha = hashlib.sha256(data).hexdigest()
                    if sha in seen_sha:
                        why = "duplicate"
                    else:
                        seen_sha.add(sha)
                        nchecked += 1
                        h, why = check_screenshot(data)
                        if h is not None and any((h ^ old).bit_count() <= DUPLICATE_BITS for old in chart_hashes):
                            h, why = None, "duplicate"
                if h is None:
                    discarded[why] += 1
                    say(f"  discarded ({why}): {url}")
                    return None
                name = f"image{next_n:0{digits}d}.{args.format}"
                next_n += 1
                to_rgb(Image.open(io.BytesIO(data))).save(out / name, **FORMATS[args.format])
                row = dict(file=name, image_url=url, sha256=sha, chart_hash=h)
                writer.writerow(row)
                mf.flush()
                rows.append(row)
                chart_hashes.append(h)
                say(f"  saved {name}: {url}")
                return out / name, sha

            if args.search:
                for url in images:
                    consider(url)
                    bar.update(1)
                    status()
                for page in pages:
                    urls, why = page_images(fetcher, page)
                    say(f"    {'following' if why is None else f'skipped ({why})'}: {page}")
                    if why:
                        skipped_pages[why] += 1
                    for url in urls:
                        consider(url)
                        status()
                    bar.update(1)
                    status()
            else:
                while queue and len(rows) < args.size:
                    qpath, qsha = queue.popleft()
                    say(f"searching with {qpath}")
                    wd = web_detection(fetcher, args.api_key, qpath, args.max_results)
                    nsearches += 1
                    with open(searched_log, "a") as sf:
                        sf.write(qsha + "\n")
                    status()
                    for url in candidate_urls(fetcher, wd):
                        if len(rows) >= args.size:
                            break
                        saved = consider(url)
                        if saved:
                            queue.append(saved)  # search with it too
                            bar.update(1)
                        status()
        if not args.search and len(rows) < args.size:
            stop_reason = "no more search results to follow"
    except FatalError as e:
        stop_reason = f"stopped: {e}"
    except KeyboardInterrupt:
        stop_reason = "interrupted"

    elapsed = time.perf_counter() - t0
    print(f"Saved {len(rows) - nsaved_before:,} new screenshot(s) in {elapsed / 60:.1f} min "
          f"({len(rows):,}{'' if args.search else f' of {args.size:,}'} in {out}; sources in {manifest})")
    if args.search:
        print(f"{len(search_pages)} saved search page(s): {len(images):,} image link(s) and {len(pages):,} result "
              f"page(s)" + (" (skipped: " + ", ".join(f"{why} {n:,}" for why, n in skipped_pages.most_common())
                            + ")" if skipped_pages else ""))
    print(("" if args.search else f"{nsearches:,} search(es), ")
          + f"{nchecked:,} image(s) checked, {sum(discarded.values()):,} discarded"
          + (f", {fetcher.waits:,} request(s) retried after a failure" if fetcher.waits else ""))
    if discarded:
        print("Discarded: " + ", ".join(f"{why} {n:,}" for why, n in discarded.most_common()))
    if stop_reason:
        print(stop_reason[0].upper() + stop_reason[1:]
              + ("" if stop_reason.startswith("no more") else "; rerun the same command to continue"))


if __name__ == "__main__":
    main()
