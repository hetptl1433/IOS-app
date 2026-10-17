const { chromium } = require("playwright");
const out = process.argv[2];
const base = "http://127.0.0.1:8765/lab";
const check = (ok, what) => { console.log(`${ok ? "ok  " : "FAIL"} ${what}`); if (!ok) process.exitCode = 1; };

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const problems = [];
  page.on("pageerror", e => problems.push(e.message));
  page.on("console", m => { if (m.type() === "error") problems.push(m.text()); });

  // A saved model, from the Models tab into a backtest.
  await page.goto(base + "#models");
  await page.waitForSelector("#tab-models .card h2");
  const name = (await page.locator("#tab-models .card h2").nth(1).textContent()).trim();
  await page.locator("#tab-models button:has-text('Backtest it')").first().click();
  await page.waitForSelector("#tab-run .card");
  check(location => true, "went to the Run tab");
  const start = await page.inputValue('[data-key="start"]');
  check(start === "2026-01-29", `first night moved to the model's first usable night (${start})`);
  const boxes = await page.locator('#tab-run input[type="checkbox"][data-key^="strategy-model:"]').all();
  const checked = await Promise.all(boxes.map(b => b.isChecked()));
  check(checked.includes(true), "the saved model is ticked");

  // Narrow the run so it is quick, then run it.
  await page.click("#tab-run button:has-text('Clear')");
  await page.locator('[data-key="strategy-day-ahead"]').check();
  await boxes[checked.indexOf(true)].check();
  await page.fill('[data-key="end"]', "2026-03-31");
  await page.locator('[data-key="end"]').dispatchEvent("change");
  await page.waitForTimeout(300);
  await page.click("#tab-run button:has-text('Run backtest')");
  await page.waitForFunction(() => location.hash === "#results" && document.querySelector("#tab-results .winner"), null, { timeout: 300000 });
  await page.waitForTimeout(500);
  const labels = await page.locator("#tab-results .bar-label > span:first-child").allTextContents();
  check(labels.includes(`Forecast: ${name}`), `results include "Forecast: ${name}"`);
  check(labels.includes("Day-ahead plan") && !labels.includes("Timer 23:00"), "only the chosen strategies ran");

  // Switch the delivery rate and open a table view.
  const before = await page.locator("#tab-results .winner .figures").first().textContent();
  await page.click("#tab-results .segmented button:has-text('Time-of-Day')");
  await page.waitForTimeout(300);
  const after = await page.locator("#tab-results .winner .figures").first().textContent();
  check(before !== after, `the rate toggle changes the figures (${before.trim()} -> ${after.trim()})`);
  await page.locator("#tab-results button:has-text('Show table')").first().click();
  const rows = await page.locator("#tab-results .chart-body[hidden] + .table-wrap tbody tr").count();
  check(rows > 0, `a chart's table view shows ${rows} rows`);
  await page.screenshot({ path: `${out}/11-model-run.png`, fullPage: true });

  // Cancel a run part-way.
  await page.click('[data-tab="run"]');
  await page.click("#tab-run button:has-text('Select every strategy that can run')");
  await page.fill('[data-key="start"]', "2025-04-02");
  await page.locator('[data-key="start"]').dispatchEvent("change");
  await page.waitForTimeout(300);
  await page.click("#tab-run button:has-text('Run backtest')");
  await page.waitForSelector("#job button:has-text('Cancel')");
  await page.waitForTimeout(3000);
  await page.click("#job button:has-text('Cancel')");
  await page.waitForFunction(() => document.querySelector("#job .job-title")?.textContent.includes("cancelled"), null, { timeout: 120000 });
  check(true, "a running backtest can be cancelled");
  await page.screenshot({ path: `${out}/12-cancelled.png` });

  check(problems.length === 0, `no browser errors${problems.length ? ": " + problems.join("; ") : ""}`);
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
