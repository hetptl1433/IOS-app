const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.goto("http://127.0.0.1:8765/lab#" + (process.argv[2] || "results"));
  await page.waitForTimeout(1500);
  const wide = await page.evaluate(() => {
    const limit = document.documentElement.clientWidth;
    const out = [];
    for (const node of document.querySelectorAll("body *")) {
      const box = node.getBoundingClientRect();
      if (box.right > limit + 1 && box.width > 0) {
        const parentWide = node.parentElement && node.parentElement.getBoundingClientRect().right > limit + 1;
        out.push(`${parentWide ? "  " : ""}${node.tagName.toLowerCase()}.${node.className && node.className.baseVal === undefined ? node.className : ""} right=${Math.round(box.right)} width=${Math.round(box.width)} text=${(node.textContent || "").slice(0, 40).replace(/\s+/g, " ")}`);
      }
    }
    return out.slice(0, 40);
  });
  console.log(wide.join("\n"));
  await browser.close();
})();
