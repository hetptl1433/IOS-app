"""Render the verified Norway comparison as a shareable PNG (requires Pillow)."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "results/charging-history/norway-strategies-2025.json"
TARGET = SOURCE.with_suffix(".png")
FONT_DIR = Path("/System/Library/Fonts/Supplemental")


def main():
    report = json.loads(SOURCE.read_text())
    results = {(row["strategy"], row["tariff"]): row for row in report["results"]}
    core = ["charge-now", "cap-2", "cap-3", "cap-5", "day-ahead", "live", "hindsight"]
    strategies = core + [f"timer-{hour:02}00" for hour in range(24)]
    assert len(strategies) == len(set(strategies)) == 31
    assert len(results) == 62
    assert all(row["commonSessions"] == report["commonSessions"] for row in results.values())

    width, height = 3000, 1900
    paper, ink, muted = "#F5F5ED", "#173C30", "#647268"
    image = Image.new("RGB", (width, height), paper)
    draw = ImageDraw.Draw(image)

    def font(size, bold=False):
        filename = "Arial Bold.ttf" if bold else "Arial.ttf"
        path = FONT_DIR / filename
        return ImageFont.truetype(str(path), size)

    def text(x, y, value, size=32, color=ink, bold=False, right=False):
        face = font(size, bold)
        box = draw.textbbox((0, 0), value, font=face)
        if right:
            x -= box[2] - box[0]
        draw.text((x, y), value, font=face, fill=color)

    def money(value):
        return f"${value:,.2f}"

    text(80, 48, "SPARC / NIGHT SHIFT", 28, muted, True)
    text(80, 105, "Norway charging strategies", 82, ink, True)
    text(80, 214, "Dataset 1 residential sessions · 2025 ComEd price scenario", 35, muted)

    cards = [
        (f"{report['commonSessions']:,}", "COMMON COMPLETED SESSIONS"),
        (f"{report['commonEnergyKwh']:,.0f} kWh", "SAME ENERGY FOR EVERY STRATEGY"),
        ("31 configurations", "TESTED ON BOTH DELIVERY RATES"),
        (money(report["baselineCostUsd"]), "STANDARD IMMEDIATE-CHARGING BASELINE"),
    ]
    card_width, gap = 689, 28
    for index, (value, label) in enumerate(cards):
        x = 80 + index * (card_width + gap)
        draw.rounded_rectangle((x, 298, x + card_width, 438), radius=18, fill="#E9EEE3")
        text(x + 26, 319, value, 47, ink, True)
        text(x + 26, 387, label, 20, muted, True)

    text(80, 470, "Savings versus immediate charging on standard delivery", 33, ink, True)
    text(80, 516, "Standard isolates timing. Time-of-day also includes the delivery-rate change. All costs are totals across the compared sessions.", 27, muted)

    def table(x, ids):
        panel_width, top, header_height, row_height = 1394, 584, 98, 62
        panel_bottom = top + header_height + 16 * row_height
        draw.rounded_rectangle((x, top, x + panel_width, panel_bottom), radius=16, fill="white", outline="#D7DFD2", width=2)
        draw.rounded_rectangle((x, top, x + panel_width, top + header_height), radius=16, fill=ink)
        draw.rectangle((x, top + 48, x + panel_width, top + header_height), fill=ink)
        text(x + 28, top + 33, "STRATEGY", 24, "white", True)
        for anchor, line1, line2 in [(660, "STANDARD", "COST"), (850, "TIMING", "SAVED"),
                                       (1140, "TIME-OF-DAY", "COST"), (1350, "TOTAL", "SAVED")]:
            text(x + anchor, top + 18, line1, 21, "white", True, True)
            text(x + anchor, top + 49, line2, 21, "white", True, True)
        for index, strategy in enumerate(ids):
            standard = results[strategy, "standard"]
            tod = results[strategy, "time-of-day"]
            y = top + header_height + index * row_height
            fill = "#F6F8F3" if index % 2 else "white"
            bold = strategy in ("day-ahead", "live", "timer-0100")
            if strategy == "live":
                fill = "#D5E8BA"
            elif strategy == "day-ahead":
                fill = "#E9F2DD"
            elif strategy == "hindsight":
                fill = "#E8EFEC"
            elif strategy == "timer-0100":
                fill = "#F8F0D9"
            draw.rectangle((x + 2, y, x + panel_width - 2, y + row_height), fill=fill)
            draw.line((x + 24, y + row_height, x + panel_width - 24, y + row_height), fill="#E5E9E0", width=1)
            label = {
                "charge-now": "Immediate charging", "cap-2": "Supply cap 2¢/kWh", "cap-3": "Supply cap 3¢/kWh",
                "cap-5": "Supply cap 5¢/kWh", "day-ahead": "SPARC day-ahead", "live": "SPARC + live prices",
                "hindsight": "Hindsight (reference)",
            }.get(strategy, standard["label"])
            assert draw.textlength(label, font=font(30, bold)) < 415, label
            text(x + 28, y + 17, label, 30, ink, bold)
            for anchor, value, color in [
                (660, money(standard["totalCostUsd"]), ink),
                (850, f"{standard['savingPct']:.2f}%", "#B44F3B" if standard["savingPct"] < 0 else ink),
                (1140, money(tod["totalCostUsd"]), ink),
                (1350, f"{tod['savingPct']:.2f}%", "#B44F3B" if tod["savingPct"] < 0 else ink),
            ]:
                text(x + anchor, y + 17, value, 30, color, bold, True)

    table(80, strategies[:16])
    table(1526, strategies[16:])

    text(80, 1720, "Best tested strategy without future prices: SPARC + live. Best fixed timer: 01:00.", 30, ink, True)
    text(80, 1766, "Timers and price caps start early when needed to finish. Negative savings mean higher cost. Hindsight knows future settled prices.", 25, muted)
    text(80, 1807, "1,552 reports lacked required inputs/prices; 41 lacked charging capacity. All 31 configurations completed every remaining session.", 25, muted)
    text(80, 1848, "Rendered from verified simulation results · Norwegian habits mapped to Chicago prices · Not measured Norwegian bills or annual savings", 24, muted)

    image.save(TARGET, optimize=True, dpi=(200, 200))
    print(f"Saved {TARGET} ({width} × {height})")


if __name__ == "__main__":
    main()
