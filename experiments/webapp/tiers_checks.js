const { chromium } = require("playwright");
const out = process.argv[2];
const base = "http://127.0.0.1:8765/lab";
const check = (ok, what) => { console.log(`${ok ? "ok  " : "FAIL"} ${what}`); if (!ok) process.exitCode = 1; };
(async () => {
  const browser = await chromium.launch();
  for (const [scheme, width] of [["light", 1280], ["dark", 390]]) {
    const page = await browser.newPage({ viewport: { width, height: 900 }, colorScheme: scheme });
    const problems = [];
    page.on("pageerror", e => problems.push(e.message));
    page.on("console", m => { if (m.type() === "error") problems.push(m.text()); });

    await page.goto(base + "#run");
    await page.waitForSelector("#tab-run table.profiles");
    const names = async () => page.locator('#tab-run table.profiles td:first-child input').evaluateAll(n => n.map(x => x.value));
    check(JSON.stringify(await names()) === JSON.stringify(["Average", "Moderate", "Heavy"]), `${scheme}: the three driver tiers are the default`);
    const rows = count => page.waitForFunction(n => document.querySelectorAll("#tab-run table.profiles tbody tr").length === n, count, { timeout: 5000 }).then(() => true, () => false);
    await page.getByRole("button", { name: "The four example drivers" }).click();
    check((await rows(4)) && (await names())[0] === "Commuter", `${scheme}: the example preset loads four drivers`);
    await page.getByRole("button", { name: "Average, moderate and heavy drivers" }).click();
    check((await rows(3)) && (await names())[2] === "Heavy", `${scheme}: the tiers preset loads three drivers`);
    await page.locator("#tab-run .card", { hasText: "Drivers" }).first().screenshot({ path: `${out}/${scheme}-drivers.png` });

    await page.goto(base + "#results");
    const headline = page.locator("#tab-results .card", { hasText: "Against the default setup" });
    await headline.waitFor();
    const tiles = await headline.locator(".tile .value").allTextContents();
    check(JSON.stringify(tiles) === JSON.stringify(["$355 a year", "$643 a year", "$1,228 a year"]), `${scheme}: tiles ${JSON.stringify(tiles)}`);
    const subs = await headline.locator(".tile .sub").allTextContents();
    check(subs[0] === "54% cheaper · Day-ahead plan", `${scheme}: first tile says ${subs[0]}`);
    const plan = await headline.locator("tr", { hasText: "Day-ahead plan" }).first().locator("td").allTextContents();
    check(plan[1] === "$355 (54%)" && plan[3] === "$1,224 (41%)", `${scheme}: plan row ${JSON.stringify(plan)}`);
    const bold = await headline.locator("td.best").allTextContents();
    check(bold.length === 3, `${scheme}: one highlighted cell per driver ${JSON.stringify(bold)}`);
    const order = await page.locator("#tab-results .card h2").allTextContents();
    check(order.indexOf("Against the default setup") < order.indexOf("Cheapest strategy"), `${scheme}: headline comes before the cheapest strategy`);
    const overflow = await page.evaluate(() => document.scrollingElement.scrollWidth - innerWidth);
    check(overflow <= 0, `${scheme}: no sideways page scroll (${overflow}px)`);
    await headline.screenshot({ path: `${out}/${scheme}-headline.png` });
    check(problems.length === 0, `${scheme}: no console errors ${JSON.stringify(problems)}`);
    await page.close();
  }
  await browser.close();
})();
