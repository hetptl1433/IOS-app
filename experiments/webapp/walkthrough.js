const { chromium } = require("playwright");
const out = process.argv[2];
const base = "http://127.0.0.1:8765/lab";

(async () => {
  const browser = await chromium.launch();
  const problems = [];
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on("console", m => { if (m.type() === "error" || m.type() === "warning") problems.push(`console ${m.type()}: ${m.text()}`); });
  page.on("pageerror", e => problems.push(`page error: ${e.message}`));
  page.on("requestfailed", r => problems.push(`request failed: ${r.url()} ${r.failure().errorText}`));

  await page.goto(base + "#data");
  await page.waitForSelector("#tab-data .card");
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${out}/1-data.png`, fullPage: true });

  await page.click('[data-tab="run"]');
  await page.waitForSelector("#tab-run .card");
  await page.screenshot({ path: `${out}/2-run.png`, fullPage: true });

  const started = Date.now();
  await page.click("text=Run backtest");
  await page.waitForSelector("#job:not([hidden])");
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${out}/3-running.png` });
  await page.waitForFunction(() => location.hash === "#results" && document.querySelector("#tab-results .winner"), null, { timeout: 600000 });
  console.log(`backtest finished in ${((Date.now() - started) / 1000).toFixed(0)} s`);
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${out}/4-results.png`, fullPage: true });

  const row = page.locator(".bar-row").first();
  await row.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await row.hover();
  await page.waitForTimeout(200);
  await page.screenshot({ path: `${out}/5-tooltip.png` });

  const column = page.locator("#tab-results svg .hit").nth(5);
  await column.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await column.hover();
  await page.waitForTimeout(200);
  const box = await column.boundingBox();
  await page.screenshot({ path: `${out}/6-month-tooltip.png`, clip: { x: 0, y: Math.max(0, box.y - 220), width: 1280, height: 560 } });

  await page.click('[data-tab="models"]');
  await page.waitForSelector("#tab-models .card");
  const before = await page.locator("#tab-models .card").count();
  await page.click("#tab-models button.primary");
  await page.waitForFunction(count => document.querySelectorAll("#tab-models .card").length > count, before, { timeout: 600000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${out}/7-models.png`, fullPage: true });

  const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  phone.on("pageerror", e => problems.push(`phone page error: ${e.message}`));
  await phone.goto(base + "#results");
  await phone.waitForSelector("#tab-results .winner");
  await phone.waitForTimeout(800);
  await phone.screenshot({ path: `${out}/8-phone-results.png`, fullPage: true });
  const overflow = await phone.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  console.log(`phone horizontal overflow: ${overflow}px`);
  await phone.goto(base + "#run");
  await phone.waitForSelector("#tab-run .card");
  await phone.screenshot({ path: `${out}/9-phone-run.png`, fullPage: true });
  const runOverflow = await phone.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  console.log(`phone run-tab horizontal overflow: ${runOverflow}px`);

  const dark = await browser.newPage({ viewport: { width: 1280, height: 900 }, colorScheme: "dark" });
  dark.on("pageerror", e => problems.push(`dark page error: ${e.message}`));
  await dark.goto(base + "#results");
  await dark.waitForSelector("#tab-results .winner");
  await dark.waitForTimeout(800);
  await dark.screenshot({ path: `${out}/10-dark-results.png`, fullPage: true });

  console.log(problems.length ? problems.join("\n") : "no browser errors");
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
