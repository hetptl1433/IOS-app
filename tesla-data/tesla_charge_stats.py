#!/usr/bin/env python3
"""
tesla_charge_stats.py - Extract daily charging data from Tesla app "Charge Stats" screenshots.

For every screenshot in a directory (one month per screenshot, iOS dark mode), this script:
  1. OCRs the month, the per-location totals (Home / Work / Supercharger / Other),
     "Total Charged", "Total Miles Added" and the chart's y-axis maximum.
  2. Measures every bar in the daily bar chart and splits each stacked bar into
     its colored segments (blue = Home, red = Supercharger, gray = Other, orange = Work).
  3. Converts bar heights to kWh and reconciles them so each category's monthly sum
     matches the exact total printed in the app's legend.
  4. Allocates the month's "Total Miles Added" to days/categories in proportion to kWh.
  5. Writes one CSV row per calendar day.

How the chart is rendered (measured from real screenshots, used for the kWh conversion):
  each bar is a capsule whose height = value * scale + bar_width (the rounded caps add
  half a bar width at each end). The value-0 line is the center of the "no charging" dots,
  and the top horizontal gridline sits at the y-axis maximum printed on the right.

Requirements:
  pip install -r requirements.txt     (pillow numpy pytesseract tqdm)
  plus the Tesseract OCR engine:  macOS: brew install tesseract
                                  Debian/Ubuntu/Raspberry Pi OS: sudo apt install tesseract-ocr
  (optional, for .HEIC files: pip install pillow-heif)

Usage:
  python tesla_charge_stats.py [--data SCREENSHOT_DIR] [--out charging_daily.csv] [--plot charging.html]
                               [--parallel N] [--debug] [--debug-dir DIR]
"""

import argparse
import calendar
import contextlib
import csv
import datetime as dt
import difflib
import importlib.util
import io
import json
import os
import re
import statistics
import sys
import time
import webbrowser
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

try:
    import pytesseract
except ImportError:
    sys.exit("pytesseract is not installed:  pip install pytesseract   (and install the tesseract binary)")

try:
    from tqdm import tqdm
except ImportError:
    sys.exit("tqdm is not installed:  pip install tqdm")

try:  # optional HEIC support
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except ImportError:
    pass

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".heic", ".webp"}
CATS = ["home", "supercharger", "work", "other"]  # as read from the app (checked against its legend)
OUT_CATS = ["home", "supercharger", "other"]  # in the CSV and plot: Work is counted as Other
LEGEND_LABELS = {"home": "Home", "supercharger": "Supercharger", "work": "Work", "other": "Other"}
DEFAULT_COLORS = {  # the Tesla app's bar colors (RGB): fallback if they can't be learned from the image,
    "home": (72, 106, 218),  # and the --plot bar colors
    "supercharger": (203, 75, 54),
    "work": (240, 148, 54),
    "other": (138, 139, 139),
}
PLOT_THEMES = {  # --plot surface and ink colors
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781", grid="#e1e0d9", axis="#c3c2b7"),
    "dark": dict(surface="#1a1a19", ink="#f0efec", ink2="#c3c2b7", muted="#898781", grid="#2c2c2a", axis="#383835"),
}
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})


class ExtractionError(Exception):
    pass


# --------------------------------------------------------------------------- OCR helpers

def ocr_words(img):
    """Return list of words {text,left,top,width,height,conf} from a dark-mode screenshot."""
    gray = ImageOps.invert(img.convert("L"))  # dark text on light background works best
    d = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT, config="--psm 11")
    words = []
    for i, t in enumerate(d["text"]):
        t = t.strip()
        if t:
            words.append(dict(text=t, left=d["left"][i], top=d["top"][i],
                              width=d["width"][i], height=d["height"][i],
                              conf=float(d["conf"][i])))
    return words


def ocr_line(img, box):
    """OCR a single text line inside box=(x0,y0,x1,y1)."""
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, img.width), min(y1, img.height)
    if x1 - x0 < 5 or y1 - y0 < 5:
        return ""
    crop = ImageOps.invert(img.crop((x0, y0, x1, y1)).convert("L"))
    crop = ImageOps.expand(crop, border=20, fill=255)
    return pytesseract.image_to_string(crop, config="--psm 7").strip()


def find_label(words, label, lowest=True, min_top=None, cutoff=0.8):
    """Find the word best matching `label` (fuzzy). Picks the lowest (or highest) occurrence."""
    hits = []
    for w in words:
        clean = re.sub(r"[^A-Za-z]", "", w["text"])
        if not clean:
            continue
        if difflib.SequenceMatcher(None, clean.lower(), label.lower()).ratio() >= cutoff:
            if min_top is None or w["top"] > min_top:
                hits.append(w)
    if not hits:
        return None
    return max(hits, key=lambda w: w["top"]) if lowest else min(hits, key=lambda w: w["top"])


def fix_sevens(text):
    """Tesseract often reads this font's 7 as '/' ('2,/42' -> '2,742', '4/' -> '47')."""
    return re.sub(r"(?<=[\d,])/|(?<!\S)/(?=\d)", "7", text)


def parse_number(text):
    """First number in text ('1,675' -> 1675.0, '877' -> 877.0)."""
    m = re.search(r"\d[\d,]*(?:\.\d+)?", fix_sevens(text))
    return float(m.group(0).replace(",", "")) if m else None


def parse_energy(text):
    """Number directly in front of kWh/MWh ('51 kWh · 12%' -> 51.0, 'OkWh-0%' -> 0.0)."""
    t = re.sub(r"[Oo](?=\s*[kKmM]\s*[wW]\s*[hH])", "0", fix_sevens(text))
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([kKmM])\s*[wW]\s*[hH]", t)
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    return val * 1000 if m.group(2) in "mM" else val


def value_below(img, words, label_word, width_factor=9.0, lines=1.0, phrase=False, energy=True):
    """Read the number printed on the line right below a label (e.g. '394 kWh · 48%')."""
    if label_word is None:
        return None
    h = label_word["height"]
    if phrase:  # value is aligned with the first word of a multi-word label ("Total Charged")
        label_word = dict(label_word, left=phrase_start(words, label_word)["left"])
    x0 = label_word["left"] - 0.5 * h
    y0 = label_word["top"] + h + 0.2 * h
    x1 = label_word["left"] + width_factor * h
    y1 = y0 + (1.0 + 2.2 * lines) * h
    parse = parse_energy if energy else parse_number
    # 1) words already found by the full-page OCR (value is a single line: order by x)
    cand = [w for w in words if y0 - 0.3 * h <= w["top"] <= y1 and x0 <= w["left"] <= x1]
    cand.sort(key=lambda w: w["left"])
    val = parse(" ".join(w["text"] for w in cand))
    if val is not None:
        return val
    # 2) targeted single-line OCR of that region
    txt = ocr_line(img, (x0, y0 - 0.3 * h, x1, y1))
    val = parse(txt)
    if val is None and energy:
        val = parse_number(txt)
    return val


def phrase_start(words, anchor):
    """Left-most word of the phrase ending in `anchor` on the same line (e.g. 'Total' in 'Total Charged')."""
    cur = anchor
    while True:
        h = cur["height"]
        prev = [w for w in words if abs(w["top"] - cur["top"]) < 0.6 * h
                and 0 <= cur["left"] - (w["left"] + w["width"]) < 1.2 * h and w is not cur]
        if not prev:
            return cur
        cur = max(prev, key=lambda w: w["left"])


def parse_month(words):
    for mw in sorted(words, key=lambda w: w["top"]):
        m = re.sub(r"[^A-Za-z]", "", mw["text"]).lower()
        if m not in MONTHS:
            continue
        years = [w for w in words if re.fullmatch(r"(19|20)\d\d", re.sub(r"[^0-9]", "", w["text"]))
                 and abs(w["top"] - mw["top"]) < mw["height"] and 0 < w["left"] - mw["left"] < 12 * mw["height"]]
        if years:
            y = min(years, key=lambda w: w["left"])
            return int(re.sub(r"[^0-9]", "", y["text"])), MONTHS[m]
    text = " ".join(w["text"] for w in sorted(words, key=lambda w: (w["top"] // 10, w["left"])))
    m = re.search(r"\b(" + "|".join(calendar.month_name[1:]) + r")\s+(\d{4})\b", text, re.I)
    if m:
        return int(m.group(2)), MONTHS[m.group(1).lower()]
    return None


# --------------------------------------------------------------------------- chart geometry

def find_gridline(arr, bg, y_lo, y_hi):
    """Find the top horizontal gridline of the chart: longest thin non-background row run."""
    H, W, _ = arr.shape
    best = None
    diff = np.abs(arr[y_lo:y_hi].astype(int) - bg).sum(2)
    for i, row in enumerate(diff):
        on = row > 30
        # longest contiguous run
        best_run, start = (0, 0, 0), None
        for x in range(W + 1):
            if x < W and on[x]:
                if start is None:
                    start = x
            elif start is not None:
                if x - start > best_run[0]:
                    best_run = (x - start, start, x - 1)
                start = None
        if best_run[0] > 0.6 * W:
            y = y_lo + i
            if best is None or y - best["rows"][-1] > 2:
                if best is not None:
                    break  # first line group found
                best = {"rows": [y], "x0": best_run[1], "x1": best_run[2], "w": [row[best_run[1]:best_run[2]].mean()]}
            else:
                best["rows"].append(y)
                best["w"].append(row[best_run[1]:best_run[2]].mean())
    if best is None:
        return None
    rows, wts = np.array(best["rows"], float), np.array(best["w"], float)
    return dict(y=float((rows * wts).sum() / wts.sum()), x0=best["x0"], x1=best["x1"],
                y_top=int(rows.min()), y_bot=int(rows.max()))


def learn_colors(region):
    """Learn the exact bar colors from confidently-classified pixels in the plot region."""
    r, g, b = [region[..., i].astype(int) for i in range(3)]
    masks = {
        "home": (b - r > 80) & (b - g > 60) & (b > 150),
        "supercharger": (r - g > 90) & (r - b > 90) & (g < 0.5 * r) & (r > 150),
        "work": (r > 180) & (g > 0.55 * r) & (g < 0.8 * r) & (b < 0.45 * r),
        "other": (np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b) < 12) & (r > 110) & (r < 175),
    }
    cols = {}
    for k, m in masks.items():
        cols[k] = np.median(region[m], axis=0) if m.sum() > 20 else np.array(DEFAULT_COLORS[k], float)
    return cols


def classify_profile(profile, bg, colors):
    """For each pixel (row) of a 1-D color profile return (category, coverage alpha) or (None, 0)."""
    out = []
    vecs = {k: (np.asarray(c, float) - bg) for k, c in colors.items()}
    for p in profile:
        d = p - bg
        best = (None, 0.0, 1e9)
        for k, v in vecs.items():
            a = float(np.dot(d, v) / np.dot(v, v))
            if a <= 0.08:
                continue
            resid = float(np.linalg.norm(d - a * v))
            if resid < best[2]:
                best = (k, a, resid)
        if best[0] is not None and best[2] < 30 and best[1] >= 0.3:
            out.append((best[0], min(best[1], 1.0)))
        else:
            out.append((None, 0.0))
    return out


def analyze_chart(arr, y_lo, y_hi):
    """Detect gridline, bars, dots; return per-marker measurements in pixels."""
    H, W, _ = arr.shape
    if y_hi - y_lo < 0.05 * H:
        raise ExtractionError("chart area not found (is the screenshot cropped?)")
    band = arr[y_lo:y_hi].reshape(-1, 3).astype(int)
    # background = most common dark color in the band
    q = (band // 4) * 4
    vals, counts = np.unique(q, axis=0, return_counts=True)
    bg = vals[counts.argmax()].astype(float) + 1.5
    if bg.mean() > 128:
        raise ExtractionError("light-mode screenshot detected; please use dark-mode screenshots")

    grid = find_gridline(arr, bg, y_lo, y_hi)
    if grid is None:
        raise ExtractionError("could not find the chart's top gridline")
    gx0, gx1 = grid["x0"], grid["x1"]

    # find the bottom of the bars: the lowest row that crosses (almost) every day marker
    # (bars and "no charge" dots), which excludes the x-axis day labels further down
    counts = []
    for y in range(grid["y_bot"] + 3, y_hi):
        on = np.abs(arr[y, gx0:gx1 + 1].astype(float) - bg).sum(1) > 30
        edges = np.flatnonzero(np.diff(np.concatenate(([0], on.astype(int), [0]))))
        widths = edges[1::2] - edges[0::2]
        counts.append((y, int((widths >= 6).sum())))
    cmax = max(c for _, c in counts)
    if cmax == 0:
        raise ExtractionError("no bars or day markers found in chart")
    bottom_est = max(y for y, c in counts if c >= 0.8 * cmax)

    # region where bars can live: a bit above the gridline down to just below the bars
    top_lim = max(grid["y_top"] - 40, 0)
    reg_bot = bottom_est + 4
    reg = arr[top_lim:reg_bot, gx0:gx1 + 1].astype(float)
    colors = learn_colors(reg.astype(int))

    # columns that contain confidently-colored bar pixels
    ri, gi, bi = reg[..., 0], reg[..., 1], reg[..., 2]
    mx, mn = np.maximum(np.maximum(ri, gi), bi), np.minimum(np.minimum(ri, gi), bi)
    colored = ((mx - mn > 60) & (mx > 120)) | ((mx - mn < 14) & (ri > 110) & (ri < 175))
    colcount = colored.sum(0)
    runs, start = [], None
    for x in range(len(colcount) + 1):
        if x < len(colcount) and colcount[x] >= 3:
            if start is None:
                start = x
        elif start is not None:
            runs.append((start, x - 1))
            start = None
    if not runs:
        raise ExtractionError("no bars found in chart")
    widths = [e - s + 1 for s, e in runs]
    bar_w = float(statistics.median([w for w in widths if w >= 4] or widths))
    runs = [(s, e) for s, e in runs if (e - s + 1) >= 0.6 * bar_w and (e - s + 1) <= 1.6 * bar_w]

    bars = []
    for s, e in runs:
        xc = (s + e) / 2.0
        c0, c1 = int(np.floor(xc)) - 1, int(np.ceil(xc)) + 1
        profile = reg[:, c0:c1 + 1].mean(1)
        cls = classify_profile(profile, bg, colors)
        segs, cur = [], None
        for i, (k, a) in enumerate(cls):
            if k is None:
                if cur:
                    segs.append(cur)
                cur = None
                continue
            if cur and cur["cat"] == k and i == cur["y1"] + 1:
                cur["vis"] += a
                cur["y1"] = i
                cur["a1"] = a
            else:
                if cur:
                    segs.append(cur)
                cur = {"cat": k, "vis": a, "y0": i, "y1": i, "a0": a, "a1": a}
        if cur:
            segs.append(cur)
        # drop faint/tiny artefacts (e.g. gridline anti-aliasing)
        segs = [sg for sg in segs if sg["vis"] >= 1.0]
        if not segs:
            continue
        top_edge = segs[0]["y0"] + 1 - segs[0]["a0"] + top_lim
        bot_edge = segs[-1]["y1"] + segs[-1]["a1"] + top_lim
        bars.append(dict(x=xc + gx0, top=top_edge, bottom=bot_edge,
                         vis={c: sum(sg["vis"] for sg in segs if sg["cat"] == c) for c in CATS}))
    if not bars:
        raise ExtractionError("no bar segments found")

    bottom = float(statistics.median([b["bottom"] for b in bars]))
    zero_y = bottom - bar_w / 2.0

    # markers along the zero line: bars + "no charge" dots (dots have bar width, gridline dashes are thin)
    zrow = int(round(zero_y))
    zdiff = np.abs(arr[zrow - 1:zrow + 2, gx0:gx1 + 1].astype(float) - bg).sum(2).max(0)
    markers, start = [], None
    for x in range(len(zdiff) + 1):
        if x < len(zdiff) and zdiff[x] > 30:
            if start is None:
                start = x
        elif start is not None:
            if 0.6 * bar_w <= (x - start) <= 1.6 * bar_w:
                markers.append(gx0 + (start + x - 1) / 2.0)
            start = None
    for b in bars:  # make sure every bar is a marker
        if not any(abs(b["x"] - m) < bar_w / 2 for m in markers):
            markers.append(b["x"])
    markers.sort()

    return dict(bg=bg, grid=grid, bar_w=bar_w, bottom=bottom, zero_y=zero_y,
                bars=bars, markers=markers, colors=colors)


def assign_days(chart, ndays, axis_labels):
    """Map each marker / bar x-position to a day of month."""
    xs = chart["markers"]
    diffs = np.diff(xs)
    if len(diffs) == 0:
        raise ExtractionError("not enough chart markers to determine day spacing")
    med = float(np.median(diffs))
    pitch = float(np.median([d for d in diffs if 0.6 * med < d < 1.4 * med]))

    def first_day_from(x_ref, d_ref):  # which day the first marker is, given one known (x, day)
        return int(round((xs[0] - x_ref) / pitch)) + d_ref

    # x-axis labels ("4", "11", ...) are left-aligned just left of their bar's center
    label_votes = {}
    for day, lx in axis_labels:
        nearest = min(xs, key=lambda m: abs(m - (lx + 0.12 * pitch)))
        if abs(nearest - (lx + 0.12 * pitch)) < 0.5 * pitch:
            k = first_day_from(nearest, day)
            label_votes[k] = label_votes.get(k, 0) + 1
    label_best = max(label_votes, key=label_votes.get) if label_votes else None
    labels_sure = label_best is not None and label_votes[label_best] >= 2

    if len(xs) == ndays:  # normal case: one bar or "no charge" dot per day
        first_day = 1
        if labels_sure and label_best != 1:
            print(f"    warning: x-axis labels suggest a different day alignment ({label_votes}); "
                  f"check the --debug-dir image")
    elif labels_sure:
        first_day = label_best
    else:  # the plot is centered horizontally
        cx = (chart["grid"]["x0"] + chart["grid"]["x1"]) / 2.0
        first_day = first_day_from(cx - (ndays - 1) / 2.0 * pitch, 1)
    if len(xs) != ndays:
        print(f"    note: found {len(xs)} day markers for a {ndays}-day month; first marker taken as day {first_day}")
    x0 = xs[0]
    return (lambda x: int(round((x - x0) / pitch)) + first_day), pitch


# --------------------------------------------------------------------------- per-screenshot

def process_image(path, debug_dir=None):
    img = Image.open(path).convert("RGB")
    arr = np.asarray(img)
    H, W, _ = arr.shape
    words = ocr_words(img)

    ym = parse_month(words)
    if not ym:
        raise ExtractionError("could not read month/year (is this a Charge Stats screen?)")
    year, month = ym
    ndays = calendar.monthrange(year, month)[1]

    charged_lbl = find_label(words, "Charged")
    total_charged = value_below(img, words, charged_lbl, width_factor=8, lines=1.6, phrase=True)

    min_top = charged_lbl["top"] if charged_lbl else H * 0.4
    legend_words = {c: find_label(words, LEGEND_LABELS[c], lowest=True, min_top=min_top) for c in CATS}
    legend = {c: value_below(img, words, w) for c, w in legend_words.items()}
    if total_charged and None not in legend.values() and abs(sum(legend.values()) - total_charged) > 3:
        print(f"    warning: legend values sum to {sum(legend.values()):.0f} kWh but Total Charged is "
              f"{total_charged:.0f} kWh; one of them was probably misread")

    miles_lbl = find_label(words, "Miles", min_top=min_top)
    miles_added = value_below(img, words, miles_lbl, width_factor=8, lines=1.6, phrase=True, energy=False)

    # chart band: between "Total Charged" box and the legend
    y_lo = int(charged_lbl["top"] + 3 * charged_lbl["height"]) if charged_lbl else int(H * 0.40)
    leg_tops = [w["top"] for w in legend_words.values() if w]
    y_hi = int(min(leg_tops) - 0.5 * legend_words["home"]["height"]) if leg_tops and legend_words["home"] else int(H * 0.66)
    chart = analyze_chart(arr, y_lo, y_hi)

    # y-axis max label: number to the right of the plot near the gridline
    gy, gx1 = chart["grid"]["y"], chart["grid"]["x1"]
    axis_max, axis_unit_ok = None, True
    for w in words:
        if w["left"] > gx1 - 10 and abs(w["top"] + w["height"] / 2 - gy) < 2.5 * w["height"]:
            if "$" in w["text"]:
                axis_unit_ok = False
            v = parse_number(w["text"])
            if v is not None:
                axis_max = v
                break
    if axis_max is None:  # keep the crop to the number's line; the "kWh" below it confuses --psm 7
        r = 2 * chart["bar_w"]
        txt = ocr_line(img, (gx1 + 5, gy - r, W - 5, gy + r))
        axis_unit_ok = "$" not in txt
        axis_max = parse_number(txt)
    if not axis_unit_ok:
        raise ExtractionError("chart shows Total Spent ($); tap 'Total Charged' before taking the screenshot")

    # x-axis day labels (for alignment cross-check)
    axis_labels = []
    for w in words:
        if (re.fullmatch(r"\d{1,2}", w["text"]) and chart["bottom"] < w["top"] < chart["bottom"] + 6 * chart["bar_w"]
                and chart["grid"]["x0"] - 5 <= w["left"] <= gx1):
            d = int(w["text"])
            if 1 <= d <= ndays:
                axis_labels.append((d, w["left"]))
    day_of, pitch = assign_days(chart, ndays, axis_labels)

    # ---- bar heights -> kWh
    bw, zero_y = chart["bar_w"], chart["zero_y"]
    raw_px = {}  # day -> {cat: px}
    for b in chart["bars"]:
        d = day_of(b["x"])
        if not 1 <= d <= ndays:
            print(f"    warning: bar at x={b['x']:.0f} mapped outside month (day {d}); skipped")
            continue
        height = chart["bottom"] - b["top"]
        value_px = max(height - bw, 0.0)  # remove the rounded caps (half a bar width at each end)
        vis_total = sum(b["vis"].values())
        raw_px[d] = {c: value_px * b["vis"][c] / vis_total for c in CATS}

    px_sum = sum(sum(v.values()) for v in raw_px.values())
    scale_axis = (zero_y - gy) / axis_max if axis_max else None  # px per kWh
    scale_total = px_sum / total_charged if total_charged else None
    scale = scale_axis or scale_total
    if not scale:
        raise ExtractionError("could not determine the chart scale (y-axis max and Total Charged unreadable)")

    daily = {d: {c: 0.0 for c in CATS} for d in range(1, ndays + 1)}
    for d, cats in raw_px.items():
        for c in CATS:
            daily[d][c] = cats[c] / scale

    # ---- reconcile each category with the exact legend total from the app
    factors = {}
    for c in CATS:
        measured = sum(daily[d][c] for d in daily)
        target = legend.get(c)
        if target is None:
            factors[c] = None
            continue
        if measured > 0 and target > 0:
            f = target / measured
            factors[c] = f
            for d in daily:
                daily[d][c] *= f
            if abs(f - 1) > 0.15:
                print(f"    warning: {c}: bars sum to {measured:.0f} kWh but legend says {target:.0f} kWh")
        elif target > 0 and measured == 0:
            print(f"    warning: legend has {target:.0f} kWh {c} but no {c} bars were detected")
        elif target == 0 and measured > 0.5:
            print(f"    warning: detected {measured:.1f} kWh of {c} bars but legend says 0; keeping bars")

    info = dict(file=Path(path).name, year=year, month=month, ndays=ndays, total_charged=total_charged,
                miles_added=miles_added, legend=legend, axis_max=axis_max, factors=factors,
                scale_axis=scale_axis, scale_total=scale_total, pitch=pitch, nbars=len(raw_px),
                nmarkers=len(chart["markers"]))

    if debug_dir:
        save_debug(img, chart, day_of, daily, info, debug_dir)
    return info, daily


def save_debug(img, chart, day_of, daily, info, debug_dir):
    dbg = img.copy()
    dr = ImageDraw.Draw(dbg)
    g = chart["grid"]
    dr.line([(g["x0"], g["y"]), (g["x1"], g["y"])], fill=(0, 255, 0), width=1)
    dr.line([(g["x0"], chart["zero_y"]), (g["x1"], chart["zero_y"])], fill=(0, 255, 0), width=1)
    s = chart["bar_w"] / 12.0  # 12 px bar width on a 1320 px wide screenshot
    for m in chart["markers"]:
        d = day_of(m)
        tot = sum(daily.get(d, {}).values()) if d in daily else 0
        dr.text((m - 8 * s, chart["bottom"] + 70 * s), str(d), fill=(255, 255, 0))
        dr.text((m - 10 * s, chart["bottom"] + (90 + (d % 2) * 18) * s), f"{tot:.0f}", fill=(0, 255, 255))
    out = Path(debug_dir) / f"debug_{info['year']}-{info['month']:02d}_{Path(info['file']).stem}.png"
    dbg.save(out)


# --------------------------------------------------------------------------- stats & plot

def averages(total, ndays):
    """(per day, per month, per year) averages of a total over ndays calendar days."""
    per_day = total / ndays
    return per_day, per_day * 365.25 / 12, per_day * 365.25


def share(part, parts):
    """part as a fraction of the sum of parts (a {location: value} dict)."""
    whole = sum(parts.values())
    return part / whole if whole else 0.0


PLOT_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tesla charging</title>
<style>
  :root {{ --surface: {light[surface]}; --ink: {light[ink]}; --ink2: {light[ink2]}; --border: rgba(11, 11, 11, 0.10); }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --surface: {dark[surface]}; --ink: {dark[ink]}; --ink2: {dark[ink2]}; --border: rgba(255, 255, 255, 0.10); }}
  }}
  html, body {{ height: 100%; margin: 0; }}
  body {{ display: flex; flex-direction: column; background: var(--surface); color: var(--ink);
          font: 13px system-ui, -apple-system, "Segoe UI", sans-serif; }}
  header {{ padding: 20px 24px 0 64px; }}  /* 64px lines up with the plot's left edge */
  h1 {{ margin: 0; font-size: 18px; font-weight: 600; }}
  .subtitle, .note, .stat-label {{ color: var(--ink2); }}
  .subtitle {{ margin-top: 4px; }}
  .stats {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 16px; }}
  .stats + .stats {{ margin-top: 12px; }}
  .stat {{ min-width: 140px; padding: 10px 14px; border: 1px solid var(--border); border-radius: 8px; }}
  .swatch {{ display: inline-block; width: 10px; height: 10px; margin-right: 6px; border-radius: 2px; }}
  .stat-value {{ margin-top: 2px; font-size: 18px; font-weight: 600; }}
  .note {{ margin-top: 8px; font-size: 12px; }}
  .chart {{ flex: 1; min-height: 420px; }}
  .chart > div {{ height: 100%; }}
</style>
</head>
<body>
<header>
  <h1>kWh charged per day</h1>
  <div class="subtitle">{subtitle}</div>
  <div class="stats">{stat_tiles}</div>
  <div class="stats">{location_tiles}</div>
  <div class="note">{note}</div>
</header>
<div class="chart">{chart}</div>
</body>
</html>
"""


def plot_theme(t):
    """Theme-dependent layout properties (dotted paths, as used by Plotly.relayout)."""
    return {
        "paper_bgcolor": t["surface"], "plot_bgcolor": t["surface"], "font.color": t["ink"],
        "hoverlabel.bgcolor": t["surface"], "hoverlabel.bordercolor": t["axis"], "hoverlabel.font.color": t["ink"],
        "xaxis.linecolor": t["axis"], "xaxis.tickfont.color": t["muted"],
        "xaxis.rangeslider.bgcolor": t["surface"], "xaxis.rangeslider.bordercolor": t["axis"],
        "xaxis.rangeselector.bgcolor": t["grid"], "xaxis.rangeselector.activecolor": t["axis"],
        "xaxis.rangeselector.font.color": t["ink"],
        "yaxis.gridcolor": t["grid"], "yaxis.linecolor": t["axis"], "yaxis.tickfont.color": t["muted"],
        "yaxis.title.font.color": t["ink2"],
        "annotations[0].font.color": t["ink2"], "annotations[0].arrowcolor": t["muted"],
    }


def write_plot(days, path, stats):
    """Write an HTML page with summary stats and an interactive stacked bar chart of the kWh charged each
    day, by location, and open it in the browser. days = [(date, {cat: kWh}), ...]; stats = the totals
    printed in the console summary."""
    import plotly.graph_objects as go  # imported here: only --plot needs it, and every worker would load it

    dates = [d for d, _ in days]
    totals = [sum(k.values()) for _, k in days]
    colors = {c: "#%02x%02x%02x" % DEFAULT_COLORS[c] for c in OUT_CATS}
    fig = go.Figure()
    for c in OUT_CATS:  # zero-kWh days get no segment, which also keeps them out of the hover readout
        fig.add_bar(x=dates, y=[k[c] or None for _, k in days], name=LEGEND_LABELS[c],
                    marker=dict(color=colors[c], line_width=0),  # outlines would swamp 1px-wide bars
                    hovertemplate="%{y:.1f} kWh")
    fig.add_scatter(x=dates, y=totals, name="Total", mode="markers", marker_opacity=0, showlegend=False,
                    hovertemplate="<b>%{y:.1f} kWh</b>")
    peak = max(range(len(days)), key=totals.__getitem__)
    fig.add_annotation(x=dates[peak], y=totals[peak], ax=0, ay=-28, arrowwidth=1, arrowhead=0,
                       text=f"Peak: {totals[peak]:.1f} kWh on {dates[peak]:%b} {dates[peak].day}, {dates[peak].year}")
    fig.update_layout(  # the title and stats are in the page around the chart (PLOT_PAGE)
        barmode="stack", bargap=0.15, barcornerradius=4, hovermode="x unified",
        font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif', size=13),
        legend=dict(orientation="h", x=1, xanchor="right", y=1.02, yanchor="bottom", traceorder="normal"),
        margin=dict(l=64, r=24, t=56, b=24),
        xaxis=dict(type="date", showgrid=False, hoverformat="%a %b %-d, %Y",
                   rangeslider=dict(visible=True, thickness=0.08),
                   rangeselector=dict(x=0, y=1.02, yanchor="bottom", buttons=[
                       dict(count=1, step="month", label="1 month"), dict(count=3, step="month", label="3 months"),
                       dict(count=6, step="month", label="6 months"), dict(count=1, step="year", label="1 year"),
                       dict(step="all", label="All")])),
        yaxis=dict(title="kWh", rangemode="tozero", zeroline=False, gridwidth=1),
    )
    fig.plotly_relayout(plot_theme(PLOT_THEMES["light"]))
    # switch the chart to the dark colors when the browser/OS is in dark mode (the page does it in CSS)
    dark_js = ("if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {"
               "  Plotly.relayout(document.getElementById('{plot_id}'),"
               f" {json.dumps(plot_theme(PLOT_THEMES['dark']))});"
               "}")
    chart = fig.to_html(full_html=False, include_plotlyjs=True, post_script=dark_js,
                        config=dict(displaylogo=False, responsive=True))

    def tile(label, kwh, miles, decimals=0, extra=""):
        return (f'<div class="stat"><div class="stat-label">{label}</div>'
                f'<div class="stat-value">{kwh:,.{decimals}f} kWh</div>'
                f'<div class="stat-value">{miles:,.{decimals}f} miles</div>{extra}</div>')
    kwh_avg, miles_avg = averages(stats["kwh"], stats["ndays"]), averages(stats["miles"], stats["ndays"])
    loc_kwh, loc_miles = stats["loc_kwh"], stats["loc_miles"]
    page = PLOT_PAGE.format(
        light=PLOT_THEMES["light"], dark=PLOT_THEMES["dark"], chart=chart,
        subtitle=f"{dates[0]} to {dates[-1]} ({len(days):,} days), stacked by charging location",
        stat_tiles=tile("Total", stats["kwh"], stats["miles"]) + tile("Per day", kwh_avg[0], miles_avg[0], 1)
                   + tile("Per month", kwh_avg[1], miles_avg[1]) + tile("Per year", kwh_avg[2], miles_avg[2]),
        location_tiles="".join(
            tile(f'<span class="swatch" style="background: {colors[c]}"></span>{LEGEND_LABELS[c]}',
                 loc_kwh[c], loc_miles[c],
                 extra=f'<div class="stat-label">{share(loc_kwh[c], loc_kwh):.0%} of all kWh</div>')
            for c in OUT_CATS),
        note="Averages include days without charging. A month is 30.4 days and a year is 365.25 days.")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    webbrowser.open(path.resolve().as_uri())


# --------------------------------------------------------------------------- worker processes

def init_worker(tesseract_cmd):
    """Runs once in each worker process (workers don't inherit settings made in main on macOS/Windows)."""
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    # Tesseract builds with OpenMP (e.g. Debian/Raspberry Pi OS) otherwise start one thread per core
    # in every process, which makes parallel runs much slower
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")


def run_one(path, debug_dir):
    """Process one screenshot in a worker. Returns (status, result, log) where status is "ok" (result is
    (info, daily)), "skipped" (result is the reason) or "no-tesseract"; log holds the warnings it printed,
    so main can print them in file order. Errors are returned, not raised, because not every exception
    can be sent back from a worker process (TesseractNotFoundError can't)."""
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        try:
            result = ("ok", process_image(path, debug_dir))
        except ExtractionError as e:
            result = ("skipped", str(e))
        except pytesseract.TesseractNotFoundError:
            result = ("no-tesseract", None)
        except Exception as e:  # keep going with the other screenshots
            result = ("skipped", f"unexpected error ({type(e).__name__}: {e})")
    return (*result, log.getvalue())


def in_file_order(futures, bar):
    """Yield the futures' results in submission order, ticking the progress bar as each one finishes
    (in any order), so the bar doesn't stall behind one slow screenshot."""
    next_i = 0
    for _ in as_completed(futures):
        bar.update()
        while next_i < len(futures) and futures[next_i].done():
            yield futures[next_i].result()
            next_i += 1


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Extract daily kWh by location from Tesla Charge Stats screenshots.")
    ap.add_argument("--data", default="data", help="directory containing the screenshots (default: data)")
    ap.add_argument("-o", "--out", "--output", default="charging_daily.csv",
                    help="output CSV filename (default: charging_daily.csv)")
    ap.add_argument("--debug", action="store_true",
                    help="print what was read from each screenshot, with all warnings and checks "
                         "(default: only a progress bar and a summary)")
    ap.add_argument("--debug-dir", help="write annotated images showing detected days/values here")
    ap.add_argument("--plot", metavar="FILE",
                    help="also write an interactive bar chart of the kWh charged each day to this HTML file "
                         "and open it in the browser")
    ap.add_argument("--tesseract-cmd", help="path to the tesseract executable if it is not on PATH")
    ap.add_argument("--include-future", action="store_true", help="also output days after today (as zeros)")
    ap.add_argument("--parallel", type=int, default=os.cpu_count() or 1, metavar="N",
                    help="number of processes to use for the screenshots (default: number of CPU cores, "
                         f"{os.cpu_count() or 1} here); 1 processes them one at a time")
    args = ap.parse_args()
    if args.parallel < 1:
        ap.error("--parallel must be at least 1")
    if args.plot and importlib.util.find_spec("plotly") is None:
        sys.exit("plotly is not installed (needed for --plot):  pip install plotly")
    t0 = time.perf_counter()

    def say(msg, end="\n"):  # the detailed report, shown only with --debug
        if args.debug:
            tqdm.write(msg, end=end)

    if args.debug_dir:
        Path(args.debug_dir).mkdir(parents=True, exist_ok=True)

    data_dir = Path(args.data)
    if not data_dir.is_dir():
        sys.exit(f"--data: {args.data} is not a directory")
    files = sorted(p for p in data_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not files:
        sys.exit(f"no images found in {args.data}")

    nproc = min(args.parallel, len(files))
    say(f"processing {len(files)} screenshot(s) with {nproc} process(es)\n")
    months = {}  # (year, month) -> (info, daily)
    nskipped = nwarnings = 0
    # the bar goes to stderr and is hidden when that isn't a terminal; the --debug report is printed
    # above it with tqdm.write, in file order, so it reads the same for any --parallel
    with ProcessPoolExecutor(nproc, initializer=init_worker, initargs=(args.tesseract_cmd,)) as pool, \
            tqdm(total=len(files), desc="screenshots", disable=None,
                 bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [elapsed {elapsed}, ETA {remaining}, {rate_fmt}]",
                 unit="img") as bar:
        futures = [pool.submit(run_one, p, args.debug_dir) for p in files]
        for p, (status, result, log) in zip(files, in_file_order(futures, bar)):
            say(f"{p.name}")
            if log:
                say(log, end="")
                nwarnings += sum(line.lstrip().startswith("warning") for line in log.splitlines())
            if status == "no-tesseract":
                sys.exit("Tesseract binary not found. Install it (brew install tesseract / apt install tesseract-ocr) "
                         "or pass --tesseract-cmd /path/to/tesseract")
            if status == "skipped":
                say(f"    skipped: {result}")
                nskipped += 1
                continue
            info, daily = result
            key = (info["year"], info["month"])
            lg = info["legend"]
            say(f"    {calendar.month_name[info['month']]} {info['year']}: total {info['total_charged']} kWh, "
                f"home {lg['home']}, supercharger {lg['supercharger']}, work {lg['work']}, other {lg['other']}, "
                f"miles added {info['miles_added']}, axis max {info['axis_max']} kWh, "
                f"{info['nbars']} days with bars / {info['nmarkers']} day markers")
            if info["scale_axis"] and info["scale_total"]:
                say(f"    scale check: y-axis {info['scale_axis']:.3f} px/kWh "
                    f"vs Total Charged {info['scale_total']:.3f} px/kWh")
            if key in months:
                prev = months[key][0]
                keep_new = (info["total_charged"] or 0) >= (prev["total_charged"] or 0)
                say(f"    duplicate month; keeping {info['file'] if keep_new else prev['file']}")
                if not keep_new:
                    continue
            months[key] = (info, daily)

    today = dt.date.today()
    header = ["date", *(f"{c}_kwh" for c in OUT_CATS), "total_kwh",
              *(f"{c}_miles" for c in OUT_CATS), "total_miles", "source_image"]
    rows = []
    plot_days = []  # (date, {cat: kWh as written to the CSV})
    total_kwh = total_miles = 0.0
    loc_kwh, loc_miles = dict.fromkeys(OUT_CATS, 0.0), dict.fromkeys(OUT_CATS, 0.0)  # per-location column sums
    for (year, month) in sorted(months):
        info, daily = months[(year, month)]
        month_kwh = sum(sum(v.values()) for v in daily.values())
        if month_kwh == 0:
            mi_per_kwh = 0.0
        else:
            mi_per_kwh = (info["miles_added"] / month_kwh) if info["miles_added"] else None
        if mi_per_kwh is None:
            say(f"warning: {year}-{month:02d}: miles could not be computed (Total Miles Added unreadable)")
            nwarnings += 1
        elif mi_per_kwh and not 1 <= mi_per_kwh <= 6:
            say(f"warning: {year}-{month:02d}: {info['miles_added']:.0f} miles for {month_kwh:.0f} kWh looks wrong; "
                f"check Total Miles Added in {info['file']}")
            nwarnings += 1
        for d in range(1, info["ndays"] + 1):
            date = dt.date(year, month, d)
            if date > today and not args.include_future:
                continue
            k = dict(daily[d])
            k["other"] += k.pop("work")
            tot = sum(k.values())
            miles = {c: (k[c] * mi_per_kwh if mi_per_kwh else None) for c in OUT_CATS}
            tot_mi = sum(miles.values()) if mi_per_kwh else None
            total_kwh += round(tot, 1)  # sum the values as written, so the summary matches the CSV
            total_miles += round(tot_mi, 1) if tot_mi else 0.0
            for c in OUT_CATS:
                loc_kwh[c] += round(k[c], 1)
                loc_miles[c] += round(miles[c], 1) if miles[c] else 0.0

            # Note: miles are allocated from the month's "Total Miles Added" in proportion to kWh
            def f(x, n):
                return "" if x is None else f"{x:.{n}f}"
            rows.append([date.isoformat(),
                         *(f(k[c], 1) for c in OUT_CATS), f(tot, 1),
                         *(f(miles[c], 1) for c in OUT_CATS), f(tot_mi, 1),
                         info["file"]])
            plot_days.append((date, {c: round(k[c], 1) for c in OUT_CATS}))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    stats = dict(ndays=len(rows), kwh=total_kwh, miles=total_miles, loc_kwh=loc_kwh, loc_miles=loc_miles)
    if args.plot and plot_days:
        write_plot(plot_days, args.plot, stats)

    elapsed = time.perf_counter() - t0
    if args.debug:
        print()
    print(f"Processed {len(files)} screenshot(s) in {elapsed:.1f} s using {nproc} process(es)")
    date_range = f" ({rows[0][0]} to {rows[-1][0]})" if rows else ""
    print(f"Wrote {len(rows):,} daily data points{date_range} for {len(months)} month(s) to {out}")
    print(f"Total: {total_kwh:,.0f} kWh charged, {total_miles:,.0f} miles driven")
    if rows:
        print(f"{'By location:':<16}{'kWh':>8}{'miles':>17}")
        for c in OUT_CATS:
            print(f"  {LEGEND_LABELS[c]:<14}{loc_kwh[c]:>8,.0f} {share(loc_kwh[c], loc_kwh):>5.0%}"
                  f"{loc_miles[c]:>11,.0f} {share(loc_miles[c], loc_miles):>5.0%}")
    if rows:  # per day over every day in the CSV; a month is 365.25/12 days
        print(f"{'Average per:':<16}{'day':>8}{'month':>10}{'year':>10}")
        for name, total in (("kWh charged", total_kwh), ("miles driven", total_miles)):
            per_day, per_month, per_year = averages(total, len(rows))
            print(f"  {name:<14}{per_day:>8,.1f}{per_month:>10,.0f}{per_year:>10,.0f}")
    if args.plot:
        print(f"Plot written to {args.plot} and opened in your browser" if plot_days else "Nothing to plot")
    if nskipped or nwarnings:
        where = "see the details above" if args.debug else "rerun with --debug for details"
        print(f"{nskipped} screenshot(s) skipped, {nwarnings} warning(s): {where}")


if __name__ == "__main__":
    main()
