# Tesla Charge Stats → daily CSV

`tesla_charge_stats.py` turns screenshots of the Tesla iOS app's **Charge Stats** screen into a CSV file with
one row per day. Each row shows how many kWh you charged at Home, at Superchargers and elsewhere ("Other", which
includes Work), and an estimate of the miles that charge added.

A second program, `download_tesla_data_web.py`, collects more Charge Stats screenshots that people have posted
online. See [Collecting screenshots from the web](#collecting-screenshots-from-the-web).

## What it does

For each screenshot (one month per screenshot), the program:

1. **Reads the text with OCR**: the month and year, the per-location totals in the legend (Home / Supercharger /
   Work / Other), "Total Charged", "Total Miles Added" and the maximum value on the chart's y-axis.
2. **Measures the daily bar chart**: it finds each day's bar and splits stacked bars into their colors
   (blue = Home, red = Supercharger, orange = Work, gray = Other).
3. **Converts bar heights to kWh** and scales each category so its monthly sum matches the exact total shown
   in the app's legend.
4. **Estimates miles per day**: the app only reports miles for the whole month, so the month's "Total Miles Added"
   is split across days and categories in proportion to kWh.
5. **Writes the CSV**, one row per calendar day, with zeros on days with no charging. Work charging is counted
   as Other. Days after today are left out unless you pass `--include-future`.

If two screenshots show the same month, the one with the larger "Total Charged" is kept.

## Libraries used

| Library | Used for |
|---|---|
| [Pillow](https://python-pillow.org/) | Opening the screenshots, cropping and inverting images for OCR, drawing debug images |
| [NumPy](https://numpy.org/) | Pixel analysis: finding the chart gridline, bars and bar colors |
| [pytesseract](https://github.com/madmaze/pytesseract) | Python interface to the Tesseract OCR engine |
| [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) | The OCR engine itself (a separate program, not a Python package) |
| [tqdm](https://github.com/tqdm/tqdm) | The progress bar and time estimate |
| [Plotly](https://plotly.com/python/) | The interactive chart made with `--plot` (only loaded when you use it) |
| [Requests](https://requests.readthedocs.io/) | Downloading images and calling the Google Cloud Vision API (`download_tesla_data_web.py` only) |
| [pillow-heif](https://github.com/bigcat88/pillow_heif) *(optional)* | Reading `.HEIC` screenshots |

Everything else (`argparse`, `concurrent.futures`, `csv`, `calendar`, `datetime`, `re`, `difflib`, `statistics`)
is from the Python standard library.

Tested with Python 3.14, Pillow 13.0, NumPy 2.5, pytesseract 0.3.13, tqdm 4.70, Plotly 7.1 and Tesseract 5.5
on macOS.

## Setup

This is a Python script, so there is nothing to compile. You only need Python 3, the libraries above and the
Tesseract program.

1. **Install Tesseract**

   ```bash
   # macOS (Homebrew)
   brew install tesseract

   # Debian / Ubuntu / Raspberry Pi OS
   sudo apt install tesseract-ocr
   ```

   On Windows, use the installer from the [Tesseract project](https://github.com/UB-Mannheim/tesseract/wiki).
   If `tesseract` is not on your `PATH`, give its location with `--tesseract-cmd`.

2. **Create a virtual environment and install the Python libraries**

   ```bash
   cd tesla-data
   python3 -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

   A virtual environment is needed on Homebrew Python, which blocks `pip install` into the system Python.

## Taking the screenshots

- Open the Tesla app → **Charge Stats**, pick a month and take a full-screen screenshot. Do this once per month.
- Use **iOS dark mode**. Light-mode screenshots are rejected.
- Make sure **Total Charged** is selected, not Total Spent, so the chart shows kWh and not dollars.
- The screenshot must show the chart, the Home / Supercharger / Work / Other legend and **Total Miles Added**.
- Put all the screenshots in one folder (`data/` by default). Supported formats: `.png`, `.jpg`, `.jpeg`,
  `.webp` and `.heic` (with `pillow-heif`). File names don't matter.

## Running

```bash
python tesla_charge_stats.py --data data --out charging_daily.csv
```

| Option | Default | Description |
|---|---|---|
| `--data DIR` | `data` | Folder containing the screenshots |
| `-o`, `--out FILE` | `charging_daily.csv` | Output CSV filename. Missing folders in the path are created. |
| `--plot FILE` | *(none)* | Also save an interactive chart of the kWh charged each day to this HTML file (for example `charging.html`) and open it in your browser. See [The chart](#the-chart). |
| `--parallel N` | number of CPU cores | Number of processes that work on screenshots at the same time. `1` processes them one at a time. |
| `--debug` | off | Print what was read from each screenshot, plus every warning and check. Without it, only the progress bar and a summary are shown. |
| `--debug-dir DIR` | *(none)* | Save annotated copies of each screenshot showing the detected days and kWh values |
| `--tesseract-cmd PATH` | *(none)* | Path to the `tesseract` program if it is not on your `PATH` |
| `--include-future` | off | Also output days after today, as zeros |

Each screenshot takes about 1–2 seconds of CPU time. By default, screenshots are processed in parallel, one per
CPU core. On a 12-core Mac, 53 screenshots take about 9 seconds with the default settings and about 67 seconds
with `--parallel 1`. Speed stops improving once `N` passes the number of cores. The CSV is the same whatever `N` you use.

While it runs, a progress bar shows how many screenshots are done, the time elapsed, an estimate of the time
remaining (ETA) and the current speed. The bar is hidden when the output isn't a terminal, for example when you
redirect it to a log file. When the run finishes, the program prints a short summary:

```
screenshots: 100%|█████████████████████████████████████████████| 53/53 [elapsed 00:09, ETA 00:00,  5.83img/s]
Processed 53 screenshot(s) in 9.2 s using 12 process(es)
Wrote 1,613 daily data points (2022-06-01 to 2026-10-30) for 53 month(s) to charging_daily.csv
Total: 39,087 kWh charged, 79,765 miles driven
By location:         kWh            miles
  Home            32,667   84%     66,662   84%
  Supercharger     5,501   14%     11,227   14%
  Other              919    2%      1,875    2%
Average per:         day     month      year
  kWh charged       24.2       738     8,851
  miles driven      49.5     1,505    18,062
0 screenshot(s) skipped, 1 warning(s): rerun with --debug for details
```

The totals are the sums of the `total_kwh` and `total_miles` columns in the CSV. The "By location" figures are
the sums of each location's columns (`home_kwh`, `home_miles` and so on), with each location's share of the total
(Other includes Work). Because each day's values are rounded to 0.1, the location figures can add up to 1 more or
less than the total. The daily averages divide those
totals by the number of days in the CSV, including days without charging. The monthly and yearly averages are
the daily average times 30.44 (365.25 ÷ 12) and 365.25 days. This avoids skewing the results with the partial
first and last months, and with years that are only partly covered. The last line appears only when a
screenshot couldn't be read or something looks off.

Add `--debug` to also see what the program read from each screenshot, plus every warning. This is printed above
the progress bar, always in filename order, even when running in parallel:

```
IMG_5705.PNG
    December 2024: total 1343.0 kWh, home 709.0, supercharger 555.0, work 13.0, other 67.0, miles added 2742.0, axis max 179.0 kWh, 27 days with bars / 31 day markers
    scale check: y-axis 1.343 px/kWh vs Total Charged 1.333 px/kWh
```

Compare these numbers with the screenshot if something looks off. `--debug-dir` shows exactly which bar was
assigned to which day.

## The chart

```bash
python tesla_charge_stats.py --plot charging.html
```

`--plot` saves a chart of every day in the CSV and opens it in your default browser when the run finishes.
At the top of the page, tiles show the total kWh and miles and the averages per day, month and year. A second
row shows the kWh, miles and share of all kWh for Home, Supercharger and Other. These are the same numbers as in
the console summary. Below them, each bar is one day, stacked by where you charged, in the same colors as the Tesla app: Home (blue),
Supercharger (red) and Other (gray, including Work). The bar's full height is that day's total kWh. The chart shows the whole date range at first, with the highest day labeled.

- **Zoom:** use the *1 month*, *3 months*, *6 months*, *1 year* and *All* buttons, drag the handles of the
  overview strip under the chart, or drag across the chart. Double-click to zoom back out.
- **Hover** over a day to see the date, the kWh for each location charged that day and the day's total.
- **Click a legend entry** to hide or show that location.
- The chart follows your system's light or dark mode.

The HTML file includes everything it needs (about 5 MB), so it works offline and can be shared as a single file.

## Output columns

| Column | Meaning |
|---|---|
| `date` | Day, `YYYY-MM-DD` |
| `home_kwh`, `supercharger_kwh`, `other_kwh` | kWh charged at each type of location that day. `other_kwh` includes charging at Work. |
| `total_kwh` | Sum of the three kWh columns |
| `home_miles`, `supercharger_miles`, `other_miles` | Estimated miles added by that charge |
| `total_miles` | Sum of the three miles columns |
| `source_image` | The screenshot the row came from |

## Limitations

- **Miles are estimates.** Tesla only reports "Total Miles Added" for the whole month, so miles are allocated
  in proportion to kWh. A day's miles reflect how much was charged that day, not how far you drove that day.
- **Daily kWh values are measured from bar heights**, so an individual day can be off slightly. Monthly totals
  per category are scaled to match the app's legend exactly.
- **Very small charges (about 1 kWh) can be too short to see** as a bar. The program warns when a legend total
  has no matching bars, and that amount is left out of the daily rows.
- **OCR can misread digits.** The program checks that the legend adds up to Total Charged and that the
  miles-per-kWh ratio is plausible, and counts a warning if not. If the summary reports warnings, rerun with
  `--debug` to see which months they're about, and check those months.
- The program is tuned for iPhone screenshots of the iOS app in dark mode. Other layouts may not be recognized.

## Collecting screenshots from the web

`download_tesla_data_web.py` builds a collection of Charge Stats screenshots that other people have posted
online. It can find them in two ways:

- **Automatically, with a Google Cloud API key.** Starting from one of your screenshots, it runs a Google reverse
  image search using the "web detection" feature of the Google Cloud Vision API, which is Google's official API
  for image search. It then searches again with every screenshot it keeps, until it has `--size` screenshots or
  runs out of new results.
- **Without a key, from searches you ran in your browser** (`--search`). You save Google image search results
  pages from your browser, and the program follows every result in them.

Either way, it downloads the images the results lead to: the matching images themselves, and the images on the
web pages that contain a match. It keeps only screenshots of a daily bar chart in kWh and saves them all in one
format (PNG by default).

### Which screenshots are kept

A downloaded image is kept only if all of these are true:

- **It's a daily chart.** In a daily chart, every day has a marker: a bar on days with charging and a small dot
  on days without. The program reads the date range at the top of the screen. That can be a month ("December
  2024"), "Last 31 Days", or a custom range ("Jun 19 – Aug 12, 2025"). It then checks that one row of the image
  crosses one evenly spaced marker for each day in that range. Charts by month or week, such as "Last 12
  Months", are discarded. If there's no date range to read (a cropped screenshot), it looks for a month's worth:
  28 to 31 markers.
- **It has bars.** At least 3 of those markers are bars standing on the chart's baseline, not dots. The tallest
  bar has to reach well up the chart, so a line of text can't pass for bars.
- **The chart is in kWh.** The axis label to the right of the chart reads kWh. Charts in dollars (the app's
  "Total Spent" view) are discarded.
- **It's not too small.** Images under 300 pixels on either side are thumbnails and are discarded.
- **It's new.** The same chart posted at another size or quality is saved only once, and your `--input`
  screenshot is never saved.

Light- and dark-mode screenshots both work. All 53 screenshots in `data/` pass this filter, as do smaller,
re-compressed copies of them. In a test with two saved Google searches, 20 images came from Reddit. The filter
kept the 10 daily kWh charts. It discarded the other 10: 8 by-month views, 1 by-week view, and 1 chart in
dollars.

Many screenshots online show "Last 31 Days" or a custom date range rather than a calendar month.
`tesla_charge_stats.py` needs the month and year on the screen, so it can only read the calendar-month ones.

### Setup

1. Install the requirements with `pip install -r requirements.txt`. This program also needs Requests.
2. Get a Google Cloud API key. This isn't needed if you use `--search`.
   1. In the [Google Cloud console](https://console.cloud.google.com/), create a project and link a billing
      account to it. The Vision API needs one, even within the free tier.
   2. Enable the **Cloud Vision API** for the project.
   3. Under *APIs & Services → Credentials*, create an API key. Restrict it to the Cloud Vision API.
3. Pass the key with `--api-key`, or set the `GOOGLE_API_KEY` environment variable.

Each search counts as one Vision API web detection unit. According to Google's
[pricing page](https://cloud.google.com/vision/pricing), the first 1,000 units each month are free, and after
that they cost $3.50 per 1,000. Downloading the results costs nothing.

### Running with an API key

```bash
export GOOGLE_API_KEY=your-api-key
python download_tesla_data_web.py --input data/IMG_5682.PNG --output web_data --size 10000
```

### Running with searches saved from your browser

1. Run a Google image search in your browser, for example by searching with one of your screenshots.
2. Save the results page with *File → Save Page As*. Both "Webpage, Complete" and "Web Page, HTML Only" work.
   Save as many searches as you like into one folder. Results that appear in more than one search are
   downloaded only once.
3. Run the program on that folder:

   ```bash
   python download_tesla_data_web.py --search google-search --output web_data
   ```

In this mode, the program downloads everything the saved results lead to and ignores `--size`. It doesn't
search again with the screenshots it keeps, because that needs the API. `--input` is optional here: if you give
it, that screenshot is never saved.

### Options

| Option | Default | Description |
|---|---|---|
| `--input FILE` | *(required without `--search`)* | The screenshot to start the search with |
| `--search DIR` | *(none)* | Use the Google search results pages saved in this folder instead of the API |
| `--output DIR` | `web_data` | Folder for the screenshots. It's created if needed. |
| `--size N` | `10000` | Stop once this many screenshots are saved. Ignored with `--search`. |
| `--format png\|jpg` | `png` | Save every screenshot in this format, converting it if it was downloaded in another |
| `--api-key KEY` | `$GOOGLE_API_KEY` | Google Cloud API key with the Cloud Vision API enabled. Not needed with `--search`. |
| `--max-results N` | `100` | Results to ask Google for in each search |
| `--delay SECONDS` | `1` | Minimum wait between requests to the same website |
| `--debug` | off | Print each image's URL and why it was saved or discarded |
| `--tesseract-cmd PATH` | *(none)* | Path to the `tesseract` program if it isn't on your `PATH` |

The screenshots are saved as `image0000.png`, `image0001.png` and so on, numbered in the order they were found.
All of them are in the same format: PNG, or JPEG with `--format jpg`. The number has 4 digits, or more if
needed (for example, 5 digits for `--size 50000`). The output folder also contains:

- `manifest.csv`: each saved file with the URL it was downloaded from, so you can credit or remove sources.
- `searched.txt`: the images already used as searches (API mode only).

While it runs, a progress bar shows the time elapsed and an estimate of the time remaining (ETA):

- **With an API key**, it counts the screenshots saved so far out of `--size`, plus how many searches, checks
  and discards there have been:

  ```
  screenshots:  30%|█████████▌                      | 3/10 [elapsed 00:03, ETA 00:07,  1.02s/img, searches: 1, checked: 4, discarded: 5]
  ```

- **With `--search`**, it counts the results it has worked through, out of all the results in the saved
  pages, plus how many screenshots it has saved, checked and discarded.

When it finishes, it prints how many screenshots it saved and why the others were discarded.

### Throttling and politeness

- **It waits `--delay` seconds between requests to the same website.**
- **It backs off after a failed request.** If a request gets "429 Too Many Requests" or a server error, or the
  network fails, it waits 2 seconds, then 4, 8, 16 and so on (up to 6 retries) before trying again. If the
  server says how long to wait (with a `Retry-After` header), it waits at least that long.
- **It stops if Google's quota is used up.** If the Vision API still answers "429 Too Many Requests" after all
  the retries, your quota is probably used up for now. Rerun the command later.
- **It respects `robots.txt`.** It skips pages and images that a site's `robots.txt` asks crawlers not to
  fetch, and identifies itself as `download_tesla_data_web/1.0`.

### Stopping and resuming

You can stop the program at any time with Ctrl+C. Run the same command again to continue. The numbering picks
up where it left off, and images and searches that were already done are skipped.

### What to expect

- **It may stop before `--size`.** One search returns up to `--max-results` matches of each kind, and the
  program widens the search by searching again with every screenshot it keeps. When no new results are left, it
  stops and says so. That can happen well before 10,000, because there may not be that many of these
  screenshots online.
- **Long runs take hours.** Checking an image takes about 0.4 seconds, plus the time to download it, and most
  downloaded images are discarded.
- **With `--search`, most result pages are skipped.** Most results link to Facebook, Reddit, X and Instagram
  pages, and their `robots.txt` asks crawlers to stay out. Images on Reddit's image servers are allowed,
  though. In a test with two saved searches (21 image links and 162 result pages), the program saved 10
  screenshots in about 2 minutes.
- **These are other people's screenshots,** and the map at the top can show where they charge. Keep the
  collection private, and use `manifest.csv` if you need to credit or remove a source.
