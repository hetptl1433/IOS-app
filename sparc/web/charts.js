// Charts as plain HTML and SVG. Each one has a table view with the same numbers,
// so a tooltip only ever repeats what can be read without hovering.
"use strict";

const Charts = (() => {
  const SVG_NS = "http://www.w3.org/2000/svg";

  // An HTML element. String children become text nodes, never markup.
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
      else if (name === "class") node.className = value;
      else node.setAttribute(name, value === true ? "" : value);
    }
    append(node, children);
    return node;
  }

  function append(node, children) {
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
  }

  // An SVG element. Colours given as CSS variables go through style, where var() works.
  function svg(tag, attrs = {}, text) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (typeof value === "string" && value.startsWith("var(")) node.style.setProperty(name, value);
      else node.setAttribute(name, value);
    }
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // One tooltip for the page. Rows lead with the value; the series name follows.
  let tip = null;
  function showTip(anchor, title, rows) {
    if (!tip) {
      tip = el("div", { class: "tooltip", role: "tooltip" });
      document.body.append(tip);
    }
    tip.replaceChildren(
      el("div", { class: "tip-title" }, title),
      ...rows.map(row =>
        el(
          "div",
          { class: "tip-row" },
          row.color ? el("span", { class: "key-line", style: `background:${row.color}` }) : null,
          el("strong", {}, row.value),
          row.name ? el("span", { class: "name" }, row.name) : null,
        ),
      ),
    );
    tip.hidden = false;
    let x, y;
    if (anchor.clientX !== undefined && anchor.type && anchor.type.startsWith("pointer")) {
      x = anchor.clientX + 14;
      y = anchor.clientY + 14;
    } else {
      const box = (anchor.currentTarget || anchor.target || anchor).getBoundingClientRect();
      x = box.left + Math.min(box.width, 40);
      y = box.bottom + 6;
    }
    const { width, height } = tip.getBoundingClientRect();
    tip.style.left = `${Math.max(8, Math.min(x, innerWidth - width - 8))}px`;
    tip.style.top = `${y + height > innerHeight - 8 ? Math.max(8, y - height - 28) : y}px`;
  }
  function hideTip() {
    if (tip) tip.hidden = true;
  }
  addEventListener("scroll", hideTip, { passive: true });

  // Title, subtitle, legend, the chart, and a button that swaps the chart for its table.
  function figure({ title, subtitle, legend, table }) {
    const body = el("div", { class: "chart-body" });
    const tableView = el("div", { class: "table-wrap", hidden: true });
    const root = el(
      "figure",
      {},
      el(
        "figcaption",
        {},
        el("span", { class: "chart-title" }, title),
        subtitle ? el("span", { class: "chart-sub" }, subtitle) : null,
      ),
      legend && legend.length
        ? el(
            "div",
            { class: "legend" },
            legend.map(item =>
              el(
                "span",
                {},
                item.kind === "ref"
                  ? el("span", { class: "key-ref" })
                  : el("span", {
                      class: item.kind === "line" ? "key-line" : "key-rect",
                      style: `background:${item.color}`,
                    }),
                item.label,
              ),
            ),
          )
        : null,
      body,
      tableView,
    );
    if (table) {
      const toggle = el("button", { class: "link chart-toggle", type: "button" }, "Show table");
      toggle.addEventListener("click", () => {
        const showing = tableView.hidden;
        if (showing && !tableView.firstChild) tableView.append(dataTable(table));
        tableView.hidden = !showing;
        body.hidden = showing;
        toggle.textContent = showing ? "Show chart" : "Show table";
      });
      root.append(toggle);
    }
    return { root, body };
  }

  function dataTable({ columns: heads, rows }) {
    return el(
      "table",
      {},
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          heads.map(c => el("th", { class: c.num ? "num" : null }, c.label)),
        ),
      ),
      el(
        "tbody",
        {},
        rows.map(row =>
          el(
            "tr",
            {},
            row.map((cell, i) => el("td", { class: heads[i].num ? "num" : null }, cell)),
          ),
        ),
      ),
    );
  }

  // Horizontal bars, one row per item, with an optional reference line.
  // rows: [{label, value, tag, accent, tip: [{value, name}]}]
  function barList(container, options) {
    const { rows, format, reference } = options;
    const { root, body } = figure(options);
    container.append(root);
    const values = rows.map(r => r.value).concat(reference ? [reference.value] : []);
    let low = Math.min(0, ...values),
      high = Math.max(0, ...values);
    if (high === low) high = low + 1;
    const at = v => (100 * (v - low)) / (high - low);
    const list = el("div", { class: "bars" });
    body.append(list); // in the page already, so labels can be measured
    for (const row of rows) {
      const track = el("div", { class: "bar-track" });
      track.style.marginRight = "4.6em";
      if (low < 0) track.style.marginLeft = "4.6em";
      const zero = at(0),
        end = at(row.value);
      const colour = row.value < 0 && options.signed ? "red" : row.accent ? "accent" : "";
      const bar = el("div", { class: `bar ${row.value < 0 ? "neg" : "pos"} ${colour}` });
      bar.style.left = `${Math.min(zero, end)}%`;
      bar.style.width = `${Math.abs(end - zero)}%`;
      const label = el("div", { class: "bar-value" }, format(row.value));
      if (row.value < 0) label.style.right = `calc(${100 - end}% + 6px)`;
      else label.style.left = `calc(${end}% + 6px)`;
      track.append(bar, label);
      if (low < 0) {
        const line = el("div", { class: "bar-zero" });
        line.style.left = `${zero}%`;
        track.append(line);
      }
      let line = null;
      if (reference) {
        line = el("div", { class: "bar-ref" });
        line.style.left = `calc(${at(reference.value)}% - 1px)`;
        track.append(line);
      }
      const item = el(
        "div",
        { class: "bar-row", tabindex: "0" },
        el(
          "div",
          { class: "bar-label" },
          el("span", {}, row.label),
          row.tag ? el("span", { class: "tag" }, row.tag) : null,
        ),
        track,
      );
      if (row.tip) {
        const show = event => showTip(event, row.label, row.tip);
        item.addEventListener("pointermove", show);
        item.addEventListener("focus", show);
        item.addEventListener("pointerleave", hideTip);
        item.addEventListener("blur", hideTip);
      }
      list.append(item);
      if (line && row.value >= 0) {
        // move a value that would sit on the reference line past it
        const value = label.getBoundingClientRect(),
          mark = line.getBoundingClientRect();
        if (value.left < mark.right + 3 && value.right > mark.left - 3) {
          label.style.left = `calc(${at(reference.value)}% + 8px)`;
        }
      }
    }
    return root;
  }

  function niceTicks(low, high, count = 4) {
    const rough = (high - low) / count;
    const magnitude = 10 ** Math.floor(Math.log10(rough));
    const ratio = rough / magnitude;
    const step = (ratio >= 7.5 ? 10 : ratio >= 3.5 ? 5 : ratio >= 1.5 ? 2 : 1) * magnitude;
    const ticks = [];
    for (let v = Math.floor(low / step) * step; v <= Math.ceil(high / step) * step + step / 2; v += step) {
      ticks.push(Math.round(v / step) * step);
    }
    return ticks;
  }

  // A bar from svg y `base` to `tip`, rounded at the data end only.
  function barPath(x, base, width, end, radius) {
    const r = Math.min(radius, Math.abs(base - end), width / 2);
    const s = end < base ? 1 : -1;
    return (
      `M${x},${base}V${end + s * r}Q${x},${end} ${x + r},${end}H${x + width - r}` +
      `Q${x + width},${end} ${x + width},${end + s * r}V${base}Z`
    );
  }

  // Shared frame for the month charts: y axis with nice ticks, x labels that never collide.
  function frame(body, categories, values, options) {
    const width = Math.max(280, body.clientWidth || 600);
    const height = options.height || 220;
    const margin = { top: 12, right: options.right || 12, bottom: 28, left: options.left || 46 };
    let low = Math.min(0, ...values),
      high = Math.max(0, ...values);
    if (high === low) high = low + 1;
    const ticks = niceTicks(low, high);
    low = Math.min(low, ticks[0]);
    high = Math.max(high, ticks[ticks.length - 1]);
    const plotWidth = width - margin.left - margin.right,
      plotHeight = height - margin.top - margin.bottom;
    const y = v => margin.top + (plotHeight * (high - v)) / (high - low);
    const band = plotWidth / categories.length;
    const root = svg("svg", {
      width,
      height,
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": options.title,
    });
    const grid = svg("g"),
      marks = svg("g"),
      overlay = svg("g");
    for (const t of ticks) {
      grid.append(
        svg("line", {
          x1: margin.left,
          x2: width - margin.right,
          y1: y(t),
          y2: y(t),
          class: t === 0 ? "baseline" : "gridline",
        }),
      );
      grid.append(svg("text", { x: margin.left - 6, y: y(t) + 4, "text-anchor": "end" }, options.format(t)));
    }
    const every = Math.max(1, Math.ceil(44 / band));
    let previous = null;
    categories.forEach((category, i) => {
      if (i % every) return;
      grid.append(
        svg(
          "text",
          { x: margin.left + band * (i + 0.5), y: height - 8, "text-anchor": "middle" },
          options.label(category, i, previous),
        ),
      );
      previous = category;
    });
    const highlight = svg("rect", {
      y: margin.top,
      height: plotHeight,
      width: band,
      fill: "var(--wash)",
      visibility: "hidden",
    });
    root.append(highlight, grid, marks, overlay);
    body.append(root);
    return { root, marks, overlay, highlight, y, band, margin, width, height, plotHeight };
  }

  // Columns by month: one or two series side by side, or one diverging series.
  function columns(container, options) {
    const { categories, series } = options;
    const { root, body } = figure(options);
    container.append(root);
    const values = series.flatMap(s => s.values).filter(v => v !== null && v !== undefined);
    const f = frame(body, categories, values, options);
    const gap = 2;
    const barWidth = Math.max(2, Math.min(24, (f.band - 4 - gap * (series.length - 1)) / series.length));
    const groupWidth = barWidth * series.length + gap * (series.length - 1);
    categories.forEach((category, i) => {
      const left = f.margin.left + f.band * i + (f.band - groupWidth) / 2;
      series.forEach((s, j) => {
        const v = s.values[i];
        if (v === null || v === undefined || v === 0) return;
        const colour = options.diverging ? (v > 0 ? options.positive : options.negative) : s.color;
        f.marks.append(
          svg("path", { d: barPath(left + j * (barWidth + gap), f.y(0), barWidth, f.y(v), 4), fill: colour }),
        );
      });
      const hit = svg("rect", {
        x: f.margin.left + f.band * i,
        y: f.margin.top,
        width: f.band,
        height: f.plotHeight,
        class: "hit",
        tabindex: "0",
      });
      const show = event => {
        f.highlight.setAttribute("x", f.margin.left + f.band * i);
        f.highlight.setAttribute("visibility", "visible");
        showTip(event, options.tipTitle(category), options.tipRows(i));
      };
      const hide = () => {
        f.highlight.setAttribute("visibility", "hidden");
        hideTip();
      };
      hit.addEventListener("pointermove", show);
      hit.addEventListener("focus", show);
      hit.addEventListener("pointerleave", hide);
      hit.addEventListener("blur", hide);
      f.overlay.append(hit);
    });
    return root;
  }

  // Lines by month, with a crosshair that snaps to the nearest month.
  function lines(container, options) {
    const { categories, series } = options;
    const { root, body } = figure(options);
    container.append(root);
    const values = series.flatMap(s => s.values).filter(v => v !== null && v !== undefined);
    const f = frame(body, categories, values, { ...options, right: options.right || 12 });
    const x = i => f.margin.left + f.band * (i + 0.5);
    for (const s of series) {
      let d = "",
        pen = false;
      s.values.forEach((v, i) => {
        if (v === null || v === undefined) {
          pen = false;
          return;
        }
        d += `${pen ? "L" : "M"}${x(i)},${f.y(v)}`;
        pen = true;
      });
      f.marks.append(
        svg("path", {
          d,
          fill: "none",
          stroke: s.color,
          "stroke-width": 2,
          "stroke-linejoin": "round",
          "stroke-linecap": "round",
        }),
      );
      const last = s.values
        .map((v, i) => [v, i])
        .filter(([v]) => v !== null && v !== undefined)
        .pop();
      if (last) {
        f.marks.append(
          svg("circle", {
            cx: x(last[1]),
            cy: f.y(last[0]),
            r: 4,
            fill: s.color,
            stroke: "var(--surface)",
            "stroke-width": 2,
          }),
        );
      }
    }
    const cross = svg("line", {
      y1: f.margin.top,
      y2: f.margin.top + f.plotHeight,
      class: "crosshair",
      visibility: "hidden",
    });
    f.overlay.append(cross);
    const surface = svg("rect", {
      x: f.margin.left,
      y: f.margin.top,
      width: f.band * categories.length,
      height: f.plotHeight,
      class: "hit",
      tabindex: "0",
    });
    let focused = categories.length - 1;
    const showAt = (i, event) => {
      cross.setAttribute("x1", x(i));
      cross.setAttribute("x2", x(i));
      cross.setAttribute("visibility", "visible");
      showTip(
        event,
        options.tipTitle(categories[i]),
        series.map(s => ({
          value: s.values[i] === null || s.values[i] === undefined ? "—" : options.format(s.values[i]),
          name: s.name,
          color: s.color,
        })),
      );
    };
    surface.addEventListener("pointermove", event => {
      const box = f.root.getBoundingClientRect();
      const px = ((event.clientX - box.left) * f.width) / box.width;
      focused = Math.max(0, Math.min(categories.length - 1, Math.floor((px - f.margin.left) / f.band)));
      showAt(focused, event);
    });
    surface.addEventListener("keydown", event => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      focused = Math.max(0, Math.min(categories.length - 1, focused + (event.key === "ArrowLeft" ? -1 : 1)));
      showAt(focused, surface);
    });
    surface.addEventListener("focus", () => showAt(focused, surface));
    const hide = () => {
      cross.setAttribute("visibility", "hidden");
      hideTip();
    };
    surface.addEventListener("pointerleave", hide);
    surface.addEventListener("blur", hide);
    f.overlay.append(surface);
    return root;
  }

  return { el, barList, columns, lines, hideTip };
})();
