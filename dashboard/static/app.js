/* TradeyMcGrady dashboard — no build step, no CDN, no dependencies.
   Charts are drawn on <canvas> directly: fewer moving parts than a chart library,
   and the dashboard still works on a machine with no outbound network. */

const $ = (sel) => document.querySelector(sel);
const fmtUSD = (v, dp = 0) =>
  (v < 0 ? "-$" : "$") + Math.abs(v ?? 0).toLocaleString("en-US",
    { minimumFractionDigits: dp, maximumFractionDigits: dp });
const fmtPct = (v, dp = 1) => (v === null || v === undefined) ? "—" : `${v.toFixed(dp)}%`;
const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "muted");

const state = { symbol: "", since: "", timer: null };

/* ---------- fetch helpers ---------- */
function qs() {
  const p = new URLSearchParams();
  if (state.symbol) p.set("symbol", state.symbol);
  if (state.since) p.set("since", state.since);
  const s = p.toString();
  return s ? `?${s}` : "";
}
async function getJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json();
}

/* ---------- canvas plumbing ---------- */
function setupCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || canvas.parentElement.clientWidth || 600;
  const cssH = Number(canvas.getAttribute("height")) || 240;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  canvas.style.height = cssH + "px";
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  ctx.font = "11px -apple-system, system-ui, sans-serif";
  return { ctx, w: cssW, h: cssH };
}
const PAD = { l: 62, r: 12, t: 12, b: 26 };

function axes(ctx, w, h, yMin, yMax, yFmt, ticks = 5) {
  const plotH = h - PAD.t - PAD.b;
  ctx.strokeStyle = "#263141";
  ctx.fillStyle = "#8b98a8";
  ctx.lineWidth = 1;
  for (let i = 0; i <= ticks; i++) {
    const y = PAD.t + (plotH * i) / ticks;
    ctx.beginPath();
    ctx.moveTo(PAD.l, Math.round(y) + 0.5);
    ctx.lineTo(w - PAD.r, Math.round(y) + 0.5);
    ctx.stroke();
    const val = yMax - ((yMax - yMin) * i) / ticks;
    ctx.textAlign = "right";
    ctx.fillText(yFmt(val), PAD.l - 8, y + 4);
  }
}
const scaleY = (v, yMin, yMax, h) =>
  PAD.t + (h - PAD.t - PAD.b) * (1 - (v - yMin) / ((yMax - yMin) || 1));
const scaleX = (i, n, w) => PAD.l + ((w - PAD.l - PAD.r) * i) / Math.max(1, n - 1);

/* ---------- hover layer ----------
   Each draw function registers a hit-tester here; one generic listener per
   canvas turns a pointer position into a tooltip. Charts stay pure drawing
   functions, and adding a new one costs a single `register()` call. */
const CHARTS = {};

function register(canvas, hit) {
  CHARTS[canvas.id] = { hit, canvas };
  if (canvas.dataset.hoverBound) return;
  canvas.dataset.hoverBound = "1";

  const box = canvas.parentElement;                 // .chart-box is position:relative
  const tip = document.createElement("div");
  tip.className = "tip";
  box.appendChild(tip);

  const move = (e) => {
    const entry = CHARTS[canvas.id];
    if (!entry || !entry.hit) return;
    const r = canvas.getBoundingClientRect();
    const px = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
    const out = entry.hit(px, r.width);
    if (!out) { tip.classList.remove("on"); canvas.classList.remove("hot"); return; }

    tip.innerHTML = out.html;
    tip.classList.add("on");
    canvas.classList.add("hot");
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    tip.style.left = Math.max(0, Math.min(r.width - tw, out.x - tw / 2)) + "px";
    tip.style.top = Math.max(2, out.y - th - 12) + "px";

    // Crosshair is drawn on a second pass so it never pollutes the base render.
    if (out.x != null) {
      const { ctx } = lastFrame(canvas);
      if (ctx) {
        ctx.save();
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = "rgba(139,152,168,0.55)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(out.x, PAD.t);
        ctx.lineTo(out.x, canvas.clientHeight - PAD.b);
        ctx.stroke();
        if (out.y != null) {
          ctx.setLineDash([]);
          ctx.beginPath();
          ctx.arc(out.x, out.y, 4.5, 0, 7);
          ctx.fillStyle = out.dot || "#58a6ff";
          ctx.fill();
          ctx.strokeStyle = "#151b23";
          ctx.lineWidth = 2;
          ctx.stroke();
        }
        ctx.restore();
      }
    }
  };

  canvas.addEventListener("pointermove", move);
  canvas.addEventListener("pointerleave", () => {
    tip.classList.remove("on");
    canvas.classList.remove("hot");
    redraw(canvas);                                  // wipe the crosshair
  });
}

/* Charts are re-rendered from a stored closure so the crosshair can be cleared
   without refetching. Cheaper than a full refresh() and it keeps hover snappy. */
const REDRAW = {};
function remember(canvas, fn) { REDRAW[canvas.id] = fn; fn(); }
function redraw(canvas) { const f = REDRAW[canvas.id]; if (f) f(); }
function lastFrame(canvas) { return { ctx: canvas.getContext("2d") }; }

function emptyChart(canvas, msg) {
  const { ctx, w, h } = setupCanvas(canvas);
  ctx.fillStyle = "#8b98a8";
  ctx.textAlign = "center";
  ctx.fillText(msg, w / 2, h / 2);
}

/* ---------- charts ---------- */
function drawEquity(canvas, points) {
  if (!points || points.length < 2) return emptyChart(canvas, "no trades yet");
  const { ctx, w, h } = setupCanvas(canvas);
  const eq = points.map((p) => p.equity), fl = points.map((p) => p.floor);
  let lo = Math.min(...eq, ...fl), hi = Math.max(...eq, ...fl);
  const pad = (hi - lo) * 0.08 || 100;
  lo -= pad; hi += pad;
  axes(ctx, w, h, lo, hi, (v) => fmtUSD(v));

  // Shaded band between equity and the floor: the account's remaining headroom.
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = scaleX(i, points.length, w), y = scaleY(p.equity, lo, hi, h);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  for (let i = points.length - 1; i >= 0; i--) {
    ctx.lineTo(scaleX(i, points.length, w), scaleY(points[i].floor, lo, hi, h));
  }
  ctx.closePath();
  ctx.fillStyle = "rgba(88,166,255,0.10)";
  ctx.fill();

  const line = (key, color, width, dash) => {
    ctx.beginPath();
    ctx.setLineDash(dash || []);
    points.forEach((p, i) => {
      const x = scaleX(i, points.length, w), y = scaleY(p[key], lo, hi, h);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.stroke();
    ctx.setLineDash([]);
  };
  line("floor", "#f85149", 1.5, [5, 4]);
  line("equity", "#58a6ff", 2);

  // Emphasised endpoint — the eye goes to "where am I now".
  const eX = scaleX(points.length - 1, points.length, w);
  const eY = scaleY(points[points.length - 1].equity, lo, hi, h);
  ctx.beginPath(); ctx.arc(eX, eY, 4, 0, 7); ctx.fillStyle = "#58a6ff"; ctx.fill();
  ctx.strokeStyle = "#151b23"; ctx.lineWidth = 2; ctx.stroke();

  ctx.fillStyle = "#8b98a8"; ctx.textAlign = "left";
  ctx.fillText(`${points.length - 1} closed trades`, PAD.l + 4, h - 8);
  ctx.textAlign = "right";
  ctx.fillStyle = "#f85149";
  ctx.fillText("trailing floor", w - PAD.r, h - 8);

  register(canvas, (px, cw) => {
    const i = Math.max(0, Math.min(points.length - 1,
      Math.round((px - PAD.l) / ((cw - PAD.l - PAD.r) / (points.length - 1)))));
    const d = points[i];
    if (!d || !d.t) return null;
    return {
      x: scaleX(i, points.length, cw), y: scaleY(d.equity, lo, hi, h), dot: "#58a6ff",
      html: `<b>${d.t.replace("T", " ").slice(0, 16)}</b><br>`
          + `${d.label} <span class="${cls(d.pnl)}">${fmtUSD(d.pnl, 2)}</span><br>`
          + `equity ${fmtUSD(d.equity)} &middot; floor ${fmtUSD(d.floor)}<br>`
          + `<b>headroom</b> ${fmtUSD(d.equity - d.floor)}`,
    };
  });
}

function drawHistogram(canvas, bins) {
  if (!bins || !bins.length) return emptyChart(canvas, "no trades yet");
  const { ctx, w, h } = setupCanvas(canvas);
  const hi = Math.max(...bins.map((b) => b.count)) * 1.1 || 1;
  axes(ctx, w, h, 0, hi, (v) => Math.round(v).toString(), 4);
  const bw = (w - PAD.l - PAD.r) / bins.length;
  bins.forEach((b, i) => {
    const x = PAD.l + i * bw;
    const y = scaleY(b.count, 0, hi, h);
    ctx.fillStyle = b.r < 0 ? "#f85149" : "#3fb950";
    ctx.globalAlpha = b.r < -1.001 ? 1 : 0.78;   // highlight slippage past the stop
    ctx.fillRect(x + 1, y, Math.max(1, bw - 2), h - PAD.b - y);
    ctx.globalAlpha = 1;
    if (bins.length <= 24 || i % Math.ceil(bins.length / 12) === 0) {
      ctx.fillStyle = "#8b98a8"; ctx.textAlign = "center";
      ctx.fillText(b.r.toFixed(2) + "R", x + bw / 2, h - 10);
    }
  });

  register(canvas, (px, cw) => {
    const bw2 = (cw - PAD.l - PAD.r) / bins.length;
    const i = Math.floor((px - PAD.l) / bw2);
    if (i < 0 || i >= bins.length) return null;
    const b = bins[i];
    const slip = b.r < -1.001 ? '<br><span class="down">past the stop \u2014 slippage</span>' : "";
    return {
      x: PAD.l + (i + 0.5) * bw2, y: scaleY(b.count, 0, hi, h),
      dot: b.r < 0 ? "#f85149" : "#3fb950",
      html: `<b>${b.r.toFixed(2)}R to ${(b.r + 0.25).toFixed(2)}R</b><br>`
          + `${b.count} trade${b.count === 1 ? "" : "s"}${slip}`,
    };
  });
}

function drawRolling(canvas, rolling, breakeven) {
  if (!rolling || rolling.length < 2) return emptyChart(canvas, "not enough trades");
  const { ctx, w, h } = setupCanvas(canvas);
  const vals = rolling.map((r) => r.win_rate);
  const lo = Math.min(0, Math.min(...vals) - 5, breakeven - 10);
  const hi = Math.max(100, Math.max(...vals) + 5);
  axes(ctx, w, h, lo, hi, (v) => v.toFixed(0) + "%", 4);

  // Breakeven-with-friction line: below it, the strategy loses money by design.
  const by = scaleY(breakeven, lo, hi, h);
  ctx.setLineDash([5, 4]); ctx.strokeStyle = "#d29922"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(PAD.l, by); ctx.lineTo(w - PAD.r, by); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#d29922"; ctx.textAlign = "right";
  ctx.fillText(`breakeven ${breakeven.toFixed(1)}%`, w - PAD.r - 2, by - 5);

  ctx.beginPath();
  rolling.forEach((r, i) => {
    const x = scaleX(i, rolling.length, w), y = scaleY(r.win_rate, lo, hi, h);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.strokeStyle = "#58a6ff"; ctx.lineWidth = 2; ctx.stroke();

  const lastR = rolling[rolling.length - 1];
  const rX = scaleX(rolling.length - 1, rolling.length, w);
  const rY = scaleY(lastR.win_rate, lo, hi, h);
  ctx.beginPath(); ctx.arc(rX, rY, 4, 0, 7);
  ctx.fillStyle = lastR.win_rate >= breakeven ? "#3fb950" : "#f85149"; ctx.fill();
  ctx.strokeStyle = "#151b23"; ctx.lineWidth = 2; ctx.stroke();

  register(canvas, (px, cw) => {
    const i = Math.max(0, Math.min(rolling.length - 1,
      Math.round((px - PAD.l) / ((cw - PAD.l - PAD.r) / (rolling.length - 1)))));
    const d = rolling[i];
    const over = d.win_rate - breakeven;
    return {
      x: scaleX(i, rolling.length, cw), y: scaleY(d.win_rate, lo, hi, h),
      dot: over >= 0 ? "#3fb950" : "#f85149",
      html: `<b>through trade ${d.i}</b> &middot; ${d.t.slice(0, 10)}<br>`
          + `win rate ${d.win_rate.toFixed(1)}% &middot; ${d.expectancy_r.toFixed(3)}R<br>`
          + `<span class="${over >= 0 ? "up" : "down"}">`
          + `${over >= 0 ? "+" : ""}${over.toFixed(1)} pts vs breakeven</span>`,
    };
  });
}

function drawDaily(canvas, days, limit) {
  if (!days || !days.length) return emptyChart(canvas, "no trading days yet");
  const { ctx, w, h } = setupCanvas(canvas);
  const vals = days.map((d) => d.pnl);
  const hi = Math.max(...vals, limit * 0.35, 0) * 1.15 || 100;
  const lo = Math.min(...vals, -limit * 1.05) * 1.15;
  axes(ctx, w, h, lo, hi, (v) => fmtUSD(v), 4);

  const zero = scaleY(0, lo, hi, h);
  const lim = scaleY(-limit, lo, hi, h);
  ctx.setLineDash([5, 4]); ctx.strokeStyle = "#f85149"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(PAD.l, lim); ctx.lineTo(w - PAD.r, lim); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#f85149"; ctx.textAlign = "left";
  ctx.fillText(`daily loss limit ${fmtUSD(-limit)}`, PAD.l + 4, lim - 5);

  const bw = (w - PAD.l - PAD.r) / days.length;
  days.forEach((d, i) => {
    const x = PAD.l + i * bw;
    const y = scaleY(d.pnl, lo, hi, h);
    ctx.fillStyle = d.limit_breached ? "#f85149" : d.pnl >= 0 ? "#3fb950" : "#8b4a47";
    ctx.fillRect(x + 1, Math.min(y, zero), Math.max(1, bw - 2), Math.abs(zero - y) || 1);
  });
  ctx.strokeStyle = "#8b98a8"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(PAD.l, zero); ctx.lineTo(w - PAD.r, zero); ctx.stroke();

  register(canvas, (px, cw) => {
    const bw2 = (cw - PAD.l - PAD.r) / days.length;
    const i = Math.floor((px - PAD.l) / bw2);
    if (i < 0 || i >= days.length) return null;
    const d = days[i];
    const lock = d.limit_breached
      ? '<br><span class="down">daily limit breached \u2014 locked out</span>' : "";
    return {
      x: PAD.l + (i + 0.5) * bw2, y: scaleY(Math.max(0, d.pnl), lo, hi, h),
      dot: d.pnl >= 0 ? "#3fb950" : "#f85149",
      html: `<b>${d.date}</b><br>`
          + `<span class="${cls(d.pnl)}">${fmtUSD(d.pnl, 2)}</span> on ${d.trades} trade`
          + `${d.trades === 1 ? "" : "s"} (${d.wins}W)<br>`
          + `limit used ${fmtPct(d.limit_used_pct)}${lock}`,
    };
  });
}

function drawWinrateBar(canvas, actual, be, beFriction) {
  const { ctx, w, h } = setupCanvas(canvas);
  const max = Math.max(100, actual + 10, beFriction + 10);
  const rows = [
    ["Actual win rate", actual, actual >= beFriction ? "#3fb950" : "#f85149"],
    ["Breakeven + costs", beFriction, "#d29922"],
    ["Breakeven, no costs", be, "#8b98a8"],
  ];
  const rowH = (h - 16) / rows.length;
  rows.forEach(([label, val, color], i) => {
    const y = 10 + i * rowH;
    ctx.fillStyle = "#8b98a8"; ctx.textAlign = "left";
    ctx.fillText(label, 0, y + 10);
    const x0 = 130, barW = w - x0 - 48;
    ctx.fillStyle = "#1c242e";
    ctx.fillRect(x0, y + 2, barW, 12);
    ctx.fillStyle = color;
    ctx.fillRect(x0, y + 2, barW * Math.min(1, val / max), 12);
    ctx.fillStyle = color; ctx.textAlign = "right";
    ctx.fillText(val.toFixed(1) + "%", w, y + 12);
  });
}

/* ---------- tables ---------- */
function table(el, cols, rows, fmt) {
  if (!rows.length) { el.innerHTML = `<tbody><tr><td class="empty" colspan="${cols.length}">no data</td></tr></tbody>`; return; }
  const head = `<thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead>`;
  const body = rows.map((r) => `<tr>${fmt(r).map((c) => `<td>${c}</td>`).join("")}</tr>`).join("");
  el.innerHTML = head + `<tbody>${body}</tbody>`;
}
const pnlCell = (v, dp = 0) => `<span class="${cls(v)}">${fmtUSD(v, dp)}</span>`;

/* ---------- meter ---------- */
function meter(label, valueText, pct, color) {
  return `<div class="meter">
    <div class="row"><span class="muted">${label}</span><span class="r">${valueText}</span></div>
    <div class="track"><div class="fill" style="width:${Math.max(0, Math.min(100, pct))}%;background:${color}"></div></div>
  </div>`;
}

/* ---------- render ---------- */
function renderOverview(o) {
  const c = o.config, s = o.summary, p = o.prop, f = o.friction;

  $("#strategy-line").textContent =
    `${c.strategy.toUpperCase()} · ${c.timeframe} · target ${c.target_r}R · risk ${c.risk_per_trade_pct}%/trade · max ${c.max_concurrent_positions} concurrent`;

  const badges = [
    [`${c.execution_mode.toUpperCase()}`, c.live_armed ? "bad" : "ok"],
    [o.source.demo ? "DEMO DATA" : "live blotter", o.source.demo ? "warn" : ""],
    [`${p.name || "prop"} ${fmtUSD(p.account_size)}`, ""],
    [p.mll_breached ? "MLL BREACHED" : "MLL intact", p.mll_breached ? "bad" : "ok"],
  ];
  if (p.daily_limit_breaches.length) {
    badges.push([`${p.daily_limit_breaches.length} daily-limit lockout${p.daily_limit_breaches.length > 1 ? "s" : ""}`, "bad"]);
  }
  $("#badges").innerHTML = badges.map(([t, k]) => `<span class="badge ${k}">${t}</span>`).join("");

  const notices = [];
  if (o.source.demo) {
    notices.push(`<div class="notice"><strong>Demo data.</strong> Every number on this page is
      synthetic — generated to give the UI shape, not produced by a backtest. Run without
      <code>--demo</code> to read your real blotter at <code>${o.source.blotter_path}</code>.</div>`);
  } else if (!o.source.blotter_exists) {
    notices.push(`<div class="notice"><strong>No blotter found</strong> at
      <code>${o.source.blotter_path}</code>. Run <code>python run_backtest.py</code> to produce one,
      or start the dashboard with <code>--demo</code>.</div>`);
  }
  if (o.source.error) notices.push(`<div class="notice err"><strong>Blotter error:</strong> ${o.source.error}</div>`);
  if (!c.config_found) notices.push(`<div class="notice">Config <code>${c.config_path}</code> not found — showing documented defaults.</div>`);
  if (c.live_armed) notices.push(`<div class="notice err"><strong>LIVE trading is armed in config.</strong> Real money is at risk.</div>`);
  $("#notices").innerHTML = notices.join("");

  const pf = s.profit_factor === null ? "∞" : (s.profit_factor ?? 0).toFixed(2);
  const kpis = [
    ["Net P&L", `<span class="${cls(s.net_pnl)}">${fmtUSD(s.net_pnl)}</span>`, `${fmtPct(s.return_pct, 2)} on ${fmtUSD(p.account_size)}`],
    ["Trades", s.trades, `${s.wins}W / ${s.losses}L · ${fmtPct(s.win_rate)}`],
    ["Profit factor", pf, `expectancy ${fmtUSD(s.expectancy, 2)}/trade`],
    ["Avg R", `<span class="${cls(s.avg_r)}">${s.avg_r.toFixed(3)}R</span>`, `Sharpe/trade ${s.sharpe_per_trade.toFixed(2)}`],
    ["Max drawdown", `<span class="down">${fmtUSD(s.max_drawdown)}</span>`, `${fmtPct(s.max_drawdown_pct, 2)} · ${s.max_loss_streak} loss streak`],
    ["Paid in costs", `<span class="down">${fmtUSD(s.total_costs)}</span>`, `${fmtUSD(f.cost_per_trade, 2)}/trade · ${fmtPct(f.cost_share_of_gross_pct)} of gross`],
  ];
  $("#kpis").innerHTML = kpis.map(([l, v, sub]) =>
    `<div class="card kpi"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${sub}</div></div>`).join("");
  if (!state.counted) { state.counted = true; countUp(); }

  // Risk meters
  const headroomPct = 100 * (p.current_headroom / p.trailing_drawdown);
  const today = p.today;
  const dayLoss = today ? Math.max(0, -today.pnl) : 0;
  const dayPct = 100 * (dayLoss / p.daily_loss_limit);
  const barColor = (used) => used > 75 ? "#f85149" : used > 45 ? "#d29922" : "#3fb950";
  let html = "";
  html += meter("Trailing-drawdown headroom",
    `${fmtUSD(p.current_headroom)} of ${fmtUSD(p.trailing_drawdown)}`,
    headroomPct, barColor(100 - headroomPct));
  html += meter(`Daily loss used${today ? ` (${today.date})` : ""}`,
    `${fmtUSD(dayLoss)} of ${fmtUSD(p.daily_loss_limit)}`,
    dayPct, barColor(dayPct));
  if (p.profit_target) {
    html += meter("Profit target progress",
      `${fmtUSD(p.current_equity - p.account_size)} of ${fmtUSD(p.profit_target)}`,
      p.target_progress_pct ?? 0, "#58a6ff");
  }
  html += `<div class="hint">Worst headroom over this period: <strong>${fmtUSD(p.min_headroom)}</strong>
    (${fmtPct(p.min_headroom_pct)} of the allowance). Floor now at ${fmtUSD(p.current_floor)}.</div>`;
  $("#meters").innerHTML = html;

  // Live positions
  const live = o.live;
  if (!live.available) {
    $("#live-positions").innerHTML = `<div class="empty">Execution layer is not reporting state.
      This is normal for a backtest-only workflow — it does <em>not</em> mean flat.</div>`;
  } else if (!live.open_positions.length) {
    $("#live-positions").innerHTML = `<div class="empty">Flat — no open positions${live.as_of ? ` as of ${live.as_of.replace("T", " ")}` : ""}.</div>`;
  } else {
    const used = live.open_positions.length;
    $("#live-positions").innerHTML = '<div class="table-scroll"><table id="t-live"></table></div>';
    table($("#t-live"),
      ["Symbol", "Side", "Qty", "Entry", "Stop", "Target", "Open P&L"],
      live.open_positions,
      (r) => [r.symbol, r.side, r.contracts, r.entry_price, r.stop, r.target ?? "—", pnlCell(r.unrealized, 2)]);
    $("#live-positions").insertAdjacentHTML("beforeend",
      `<div class="hint">${used} of ${c.max_concurrent_positions} concurrent slots in use.
       ${live.halted ? `<span class="down"><strong>HALTED:</strong> ${live.reason || "kill-switch active"}</span>` : ""}</div>`);
  }

  // Friction panel
  $("#friction-kpis").innerHTML = [
    ["Total costs", `<span class="down">${fmtUSD(f.total_cost)}</span>`],
    ["Per trade", fmtUSD(f.cost_per_trade, 2)],
    ["Friction in R", `${f.avg_friction_r.toFixed(3)}R`],
    ["% of gross edge", fmtPct(f.cost_share_of_gross_pct)],
  ].map(([l, v]) => `<div class="kpi"><div class="label">${l}</div><div class="value" style="font-size:19px">${v}</div></div>`).join("");

  drawWinrateBar($("#c-winrate"), f.actual_win_rate, f.breakeven_win_rate, f.breakeven_win_rate_with_friction);

  const margin = f.actual_win_rate - f.breakeven_win_rate_with_friction;
  $("#friction-hint").innerHTML =
    `Commission and slippage are charged <em>per trade</em>, not per dollar risked, so they cost
     an average of <strong>${f.avg_friction_r.toFixed(3)}R</strong> every time the bot trades.
     That lifts the breakeven win rate at a ${c.target_r}R target from
     <strong>${f.breakeven_win_rate.toFixed(1)}%</strong> to
     <strong>${f.breakeven_win_rate_with_friction.toFixed(1)}%</strong>.
     Measured: <strong class="${margin > 0 ? "up" : "down"}">${f.actual_win_rate.toFixed(1)}%</strong>
     — a margin of <strong class="${margin > 0 ? "up" : "down"}">${margin.toFixed(1)} points</strong>.
     ${margin < 5 ? "That is thin enough that worse real fills would erase it." : ""}`;

  return f.breakeven_win_rate_with_friction;
}

function renderTables(freq, friction, trades) {
  table($("#t-symbols"),
    ["Symbol", "Trades", "Win%", "Net P&L", "Costs", "Cost/RT", "Costs % gross"],
    friction.per_symbol,
    (r) => [r.symbol + (r.unknown_spec ? ' <span class="warn" title="no contract spec — dollars are estimated">?</span>' : ""),
            r.trades, fmtPct(r.win_rate), pnlCell(r.net_pnl), pnlCell(-r.cost),
            fmtUSD(r.round_turn_per_contract, 2), fmtPct(r.cost_share_pct)]);

  table($("#t-hours"), ["Entry hour", "Trades", "Win%", "Net P&L"], freq.by_hour,
    (r) => [`${String(r.hour).padStart(2, "0")}:00`, r.trades, fmtPct(r.win_rate), pnlCell(r.pnl)]);

  table($("#t-reasons"), ["Exit reason", "Trades", "Net P&L"], freq.by_exit_reason,
    (r) => [r.reason, r.trades, pnlCell(r.pnl)]);

  table($("#t-trades"),
    ["Exit time", "Symbol", "Side", "Qty", "Entry", "Exit", "Stop", "R", "Net P&L", "Hold", "Reason"],
    trades,
    (t) => [t.exit_time.replace("T", " ").slice(0, 16), t.symbol, t.side, t.contracts,
            t.entry_price, t.exit_price, t.stop,
            `<span class="${cls(t.r_multiple)}">${t.r_multiple.toFixed(2)}</span>`,
            pnlCell(t.pnl, 2), `${t.duration_min.toFixed(0)}m`, t.exit_reason]);
}

async function refresh() {
  try {
    const [o, eq, fr, freq, tr] = await Promise.all([
      getJSON("/api/overview"),
      getJSON("/api/equity" + qs()),
      getJSON("/api/friction" + qs()),
      getJSON("/api/frequency" + qs()),
      getJSON("/api/trades" + qs() + (qs() ? "&" : "?") + "limit=500"),
    ]);

    const breakeven = renderOverview(o);
    // remember() stores the render so pointerleave can wipe the crosshair
    // without another fetch.
    remember($("#c-equity"),  () => drawEquity($("#c-equity"), eq.points));
    remember($("#c-rhist"),   () => drawHistogram($("#c-rhist"), freq.r_histogram));
    remember($("#c-rolling"), () => drawRolling($("#c-rolling"), eq.rolling,
                                                fr.breakeven_win_rate_with_friction));
    remember($("#c-daily"),   () => drawDaily($("#c-daily"), eq.days, eq.daily_loss_limit));
    renderTables(freq, fr, tr.trades);

    $("#rolling-hint").innerHTML =
      `${freq.trades_per_day_avg} trades/day on average (max ${freq.trades_per_day_max}),
       average hold ${freq.avg_hold_minutes} min, average risk ${fmtUSD(freq.avg_risk_dollars, 0)}/trade.
       At that risk, <strong>${freq.losers_to_daily_limit}</strong> consecutive losers reach the daily
       loss limit, against <strong>${freq.expected_losers_per_day}</strong> expected losers per day.
       ${freq.losers_to_daily_limit < 4 ? '<span class="down">Under 4, the daily limit is a routine stop-out, not a circuit breaker.</span>' : ""}`;

    // Populate the symbol filter once we know which symbols exist.
    const sel = $("#f-symbol");
    if (sel.options.length <= 1) {
      fr.per_symbol.map((s) => s.symbol).sort().forEach((s) => sel.add(new Option(s, s)));
      sel.value = state.symbol;
    }
    $("#updated").innerHTML = '<i class="dot"></i>updated ' + new Date().toLocaleTimeString();
    pulse();
  } catch (err) {
    $("#notices").innerHTML = `<div class="notice err"><strong>Could not load data:</strong> ${err.message}</div>`;
  }
}

/* Roll the headline figures up on first paint. Purely cosmetic, so it is
   skipped entirely when the viewer has asked for reduced motion. */
function countUp() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  document.querySelectorAll("#kpis .value").forEach((el) => {
    const final = el.textContent;
    const m = final.match(/-?[\d,]+\.?\d*/);
    if (!m) return;
    const target = parseFloat(m[0].replace(/,/g, ""));
    if (!isFinite(target) || Math.abs(target) < 1) return;
    const dp = (m[0].split(".")[1] || "").length;
    const t0 = performance.now(), DUR = 620;
    const step = (now) => {
      const k = Math.min(1, (now - t0) / DUR);
      const eased = 1 - Math.pow(1 - k, 3);        // ease-out cubic
      const val = (target * eased).toLocaleString("en-US",
        { minimumFractionDigits: dp, maximumFractionDigits: dp });
      el.textContent = final.replace(m[0], val);
      if (k < 1) requestAnimationFrame(step); else el.textContent = final;
    };
    requestAnimationFrame(step);
  });
}

/* A brief flash on the refresh stamp: makes a silent 15s poll visible, so the
   page reads as connected rather than frozen. */
function pulse() {
  const el = $("#updated");
  el.classList.remove("beat");
  void el.offsetWidth;                              // restart the animation
  el.classList.add("beat");
}

function setAuto(on) {
  clearInterval(state.timer);
  if (on) state.timer = setInterval(refresh, 15000);
}

$("#f-symbol").addEventListener("change", (e) => { state.symbol = e.target.value; refresh(); });
$("#f-since").addEventListener("change", (e) => { state.since = e.target.value; refresh(); });
$("#btn-reset").addEventListener("click", () => {
  state.symbol = ""; state.since = "";
  $("#f-symbol").value = ""; $("#f-since").value = "";
  refresh();
});
$("#btn-refresh").addEventListener("click", refresh);
$("#f-auto").addEventListener("change", (e) => setAuto(e.target.checked));
window.addEventListener("resize", () => { clearTimeout(state.rs); state.rs = setTimeout(refresh, 200); });

refresh();
setAuto(true);
