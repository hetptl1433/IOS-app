const { chromium } = require("playwright");
const out = process.argv[2];
const base = "http://127.0.0.1:8765/";
const check = (ok, what) => { console.log(`${ok ? "ok  " : "FAIL"} ${what}`); if (!ok) process.exitCode = 1; };
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const problems = [];
  page.on("pageerror", e => problems.push(e.message));
  page.on("console", m => { if (m.type() === "error") problems.push(m.text()); });

  await page.goto(base + "#data");
  await page.waitForSelector("#tab-data .tile");
  await page.waitForTimeout(500);
  const tiles = await page.locator("#tab-data .tile .label").allTextContents();
  check(tiles.includes("Hours with a weather forecast"), "the Data tab shows weather coverage");
  await page.screenshot({ path: `${out}/w1-data.png`, fullPage: true });

  await page.click('[data-tab="run"]');
  await page.waitForSelector("#tab-run .card");
  const weatherBox = page.locator('[data-key="strategy-walk:ridge+weather"]');
  check(await weatherBox.isChecked(), "the ridge + weather forecast is ticked by default");
  await page.locator("#tab-run .card", { hasText: "Strategies" }).first().screenshot({ path: `${out}/w2-strategies.png` });

  await page.click('[data-tab="models"]');
  await page.waitForSelector("#tab-models .card");
  await page.locator("#tab-models select").nth(1).selectOption("weather");
  const before = await page.locator("#tab-models .card h2", { hasText: "+ weather" }).count();
  await page.click("#tab-models button.primary");
  await page.waitForFunction(count => [...document.querySelectorAll("#tab-models .card h2")].filter(h => h.textContent.includes("+ weather")).length > count, before, { timeout: 300000 });
  await page.waitForTimeout(600);
  const title = await page.locator("#tab-models .card h2").nth(1).textContent();
  check(title.includes("+ weather"), `trained a model with weather: "${title}"`);
  await page.locator("#tab-models .card").nth(1).screenshot({ path: `${out}/w3-model.png` });

  await page.click('[data-tab="run"]');
  await page.click("#tab-run button:has-text('Select every strategy that can run')");
  const started = Date.now();
  await page.click("#tab-run button:has-text('Run backtest')");
  await page.waitForFunction(() => location.hash === "#results" && document.querySelector("#tab-results .winner"), null, { timeout: 900000 });
  await page.waitForTimeout(800);
  console.log(`one-click backtest finished in ${Math.round((Date.now() - started) / 1000)} s`);
  const labels = await page.locator("#tab-results .bar-label > span:first-child").allTextContents();
  check(labels.includes("Forecast: LightGBM + weather, refit monthly"), "results include the weather forecasts");
  await page.locator("#tab-results .card", { hasText: "Forecast accuracy" }).first().screenshot({ path: `${out}/w4-accuracy.png` });
  check(problems.length === 0, `no browser errors${problems.length ? ": " + problems.join("; ") : ""}`);
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
