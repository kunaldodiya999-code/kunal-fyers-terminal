// Risk desk chart: candles of the chosen option, the Long box with three drag handles, live ticket, trades.
// Source of truth = the program (it holds what FYERS holds). While you drag, only the drawing moves; on release
// the change is sent; if it is refused the line jumps back.
(() => {
  "use strict";
  const SHIFT = 19800, TICK = 0.05, HIT = 7;
  const $ = id => document.getElementById(id);
  const fmt = (v, n = 2) => (v === null || v === undefined) ? "-" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: n, maximumFractionDigits: n });
  const round = p => Math.round(p / TICK) * TICK;

  let ws, state = { trades: [], settings: {} }, chain = null, symbol = null, tf = 3, minutes = [], bars = [];
  let draft = null, tool = false, drag = null, ticketRef = 0, lastTickRx = null, lastTickAt = 0;

  // ---------------------------------------------------------------- chart
  const chart = LightweightCharts.createChart($("chart"), { autoSize: true,
    layout: { background: { color: "#131722" }, textColor: "#d1d4dc" },
    grid: { vertLines: { color: "#1e222d" }, horzLines: { color: "#1e222d" } },
    rightPriceScale: { borderColor: "#2a2e39" }, crosshair: { mode: 0 },
    timeScale: { borderColor: "#2a2e39", timeVisible: true, secondsVisible: false, rightOffset: 12 } });
  const series = chart.addCandlestickSeries({ upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
    wickUpColor: "#26a69a", wickDownColor: "#ef5350" });
  const canvas = $("overlay"), ctx = canvas.getContext("2d");
  // the price scale always stretches to show every box line (stop, entry, target) of this chart
  series.applyOptions({ autoscaleInfoProvider: original => {
    const r = original();
    const levels = boxes().flatMap(b => [b.stop, b.entry, b.target]);
    if (!levels.length) return r;
    const lo = Math.min(...levels), hi = Math.max(...levels), pad = (hi - lo) * 0.06 || 1;
    if (!r || !r.priceRange) return { priceRange: { minValue: lo - pad, maxValue: hi + pad } };
    return { ...r, priceRange: { minValue: Math.min(r.priceRange.minValue, lo - pad),
                                 maxValue: Math.max(r.priceRange.maxValue, hi + pad) } };
  } });
  window.deskDebug = { y: p => series.priceToCoordinate(p) };        // used by the automatic screen test

  function bucket(t) {                       // t: epoch seconds -> chart time of its candle (IST, from 09:15)
    const s = t + SHIFT, day = Math.floor(s / 86400) * 86400, open = day + 9 * 3600 + 15 * 60;
    return tf === 1 ? s - (s % 60) : open + Math.floor((s - open) / (tf * 60)) * tf * 60;
  }
  function rebuild() {
    const map = new Map();
    for (const m of minutes) {
      const b = bucket(m.t), x = map.get(b);
      if (!x) map.set(b, { time: b, open: m.o, high: m.h, low: m.l, close: m.c });
      else { x.high = Math.max(x.high, m.h); x.low = Math.min(x.low, m.l); x.close = m.c; }
    }
    bars = [...map.values()].sort((a, b) => a.time - b.time);
    series.setData(bars);
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - 90), to: bars.length + 12 });
    draw();
  }
  function onTick(ltp, rx) {
    const t = bucket(Math.floor(rx || Date.now() / 1000));
    const last = bars[bars.length - 1];
    if (last && last.time === t) { last.high = Math.max(last.high, ltp); last.low = Math.min(last.low, ltp); last.close = ltp; series.update(last); }
    else if (!last || t > last.time) { const b = { time: t, open: ltp, high: ltp, low: ltp, close: ltp }; bars.push(b); series.update(b); }
    $("legend").innerHTML = `<b>${symbol || ""}</b> &nbsp; LTP <b>${fmt(ltp)}</b> &nbsp; <span style="color:#787b86">${tf}m</span>`;
  }

  // ---------------------------------------------------------------- boxes
  function editable(b) {
    if (b.key === "draft" || b.state === "ARMED") return { entry: true, stop: true, target: true };
    if (b.state === "ENTRY_WORKING" || b.state === "IN_TRADE") return { entry: false, stop: true, target: true };
    return { entry: false, stop: false, target: false };
  }
  function boxes() {
    const out = [];
    for (const t of state.trades) {
      if (t.symbol !== symbol || t.state === "CLOSED" || t.state === "CANCELLED") continue;
      const b = { key: t.id, state: t.state, entry: t.entry_avg || t.entry, stop: t.stop, target: t.target, t0: t.t0, trade: t };
      if (drag && drag.box.key === t.id) b[drag.handle] = drag.price;
      out.push(b);
    }
    if (draft && draft.symbol === symbol) out.push(draft);
    return out;
  }
  function draw() {
    const el = $("chart"), w = el.clientWidth, h = el.clientHeight, dpr = window.devicePixelRatio || 1;
    canvas.width = w * dpr; canvas.height = h * dpr; canvas.style.width = w + "px"; canvas.style.height = h + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
    const right = w - chart.priceScale("right").width();
    for (const b of boxes()) {
      const ye = series.priceToCoordinate(b.entry), ys = series.priceToCoordinate(b.stop), yt = series.priceToCoordinate(b.target);
      if (ye === null || ys === null || yt === null) continue;
      let x0 = b.t0 ? chart.timeScale().timeToCoordinate(b.t0) : null;
      if (x0 === null || x0 > right) x0 = Math.max(0, right - 220);
      const armed = b.state === "ARMED", draftBox = b.key === "draft";
      ctx.globalAlpha = draftBox ? 0.14 : 0.22;
      ctx.fillStyle = "#26a69a"; ctx.fillRect(x0, Math.min(ye, yt), right - x0, Math.abs(ye - yt));
      ctx.fillStyle = "#ef5350"; ctx.fillRect(x0, Math.min(ye, ys), right - x0, Math.abs(ye - ys));
      ctx.globalAlpha = 1;
      const lines = [["target", yt, "#26a69a", "TARGET"], ["stop", ys, "#ef5350", "STOP"], ["entry", ye, armed || draftBox ? "#2962ff" : "#d1d4dc", b.trade && b.trade.entry_avg ? "FILLED" : "ENTRY"]];
      for (const [k, y, col, name] of lines) {
        ctx.strokeStyle = col; ctx.lineWidth = drag && drag.box.key === b.key && drag.handle === k ? 2.5 : 1.5;
        ctx.setLineDash(editable(b)[k] ? [] : [4, 3]);
        ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(right, y); ctx.stroke(); ctx.setLineDash([]);
        const label = `${name} ${fmt(b[k])}`, tw = ctx.measureText(label).width + 10;
        ctx.fillStyle = col; ctx.fillRect(right - tw - 4, y - 9, tw, 18);
        ctx.fillStyle = "#fff"; ctx.font = "11px sans-serif"; ctx.fillText(label, right - tw + 1, y + 4);
        if (editable(b)[k]) { ctx.fillStyle = col; ctx.fillRect(x0 + 6, y - 4, 8, 8); }
      }
      const tag = draftBox ? "NEW BOX" : `${b.state} ${b.trade.qty} qty`;
      ctx.fillStyle = "#d1d4dc"; ctx.font = "bold 11px sans-serif"; ctx.fillText(tag, x0 + 18, Math.min(ye, yt) - 6);
    }
  }
  chart.timeScale().subscribeVisibleLogicalRangeChange(() => draw());
  new ResizeObserver(() => draw()).observe($("chart"));

  function hitTest(y) {
    for (const b of boxes().reverse()) {
      for (const k of ["stop", "target", "entry"]) {
        if (!editable(b)[k]) continue;
        const yy = series.priceToCoordinate(b[k]);
        if (yy !== null && Math.abs(yy - y) <= HIT) return { box: b, handle: k };
      }
    }
    return null;
  }
  const el = $("chart");
  el.addEventListener("mousemove", e => {
    const y = e.offsetY;
    if (drag) {
      const p = series.coordinateToPrice(y);
      if (p !== null && p > 0) {
        drag.price = Math.max(TICK, round(p));
        if (drag.box.key === "draft") { draft[drag.handle] = drag.price; askTicket(); }
        draw();
      }
      return;
    }
    el.style.cursor = tool ? "crosshair" : (hitTest(y) ? "ns-resize" : "");
  });
  el.addEventListener("mousedown", e => {
    if (tool) return;
    const hit = hitTest(e.offsetY);
    if (!hit) return;
    drag = { box: hit.box, handle: hit.handle, price: hit.box[hit.handle], orig: hit.box[hit.handle] };
    chart.applyOptions({ handleScroll: false, handleScale: false });
    e.preventDefault(); e.stopPropagation();
  }, true);
  window.addEventListener("mouseup", () => {
    if (!drag) return;
    const d = drag; drag = null;
    chart.applyOptions({ handleScroll: true, handleScale: true });
    if (d.box.key === "draft") { askTicket(); }
    else if (Math.abs(d.price - d.orig) >= TICK / 2) {
      const t = state.trades.find(x => x.id === d.box.key);
      if (t) { t[d.handle === "entry" ? "entry" : d.handle] = d.price; }
      send({ op: "modify", id: d.box.key, [d.handle]: d.price });
    }
    draw();
  });
  chart.subscribeClick(param => {
    if (!tool || !param.point) return;
    const p = series.coordinateToPrice(param.point.y);
    if (p === null) return;
    const entry = round(p), risk = Math.max(round(entry * 0.12), 5 * TICK);
    draft = { key: "draft", symbol, entry, stop: round(entry - risk), target: round(entry + 2 * risk),
              t0: param.time || (bars.length ? bars[bars.length - 1].time : null) };
    tool = false; $("longbox").classList.remove("active"); $("hint").style.display = "none";
    askTicket(); draw();
  });

  // ---------------------------------------------------------------- link to the program
  function send(m) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); }
  let ticketTimer = null;
  function askTicket() {
    if (!draft) return;
    clearTimeout(ticketTimer);
    ticketTimer = setTimeout(() => {
      if (!draft) return;
      ticketRef++; send({ op: "ticket", ref: ticketRef, symbol: draft.symbol, entry: draft.entry, stop: draft.stop, target: draft.target });
    }, 60);
  }
  function toast(text, bad) {
    const t = $("toast"); t.textContent = text; t.style.display = "block"; t.style.border = `1px solid ${bad ? "#ef5350" : "#26a69a"}`;
    clearTimeout(t._h); t._h = setTimeout(() => t.style.display = "none", bad ? 6000 : 2500);
  }
  function connect() {
    ws = new WebSocket(`ws://${location.host}/ws?k=${encodeURIComponent(window.DESK_KEY)}`);
    ws.onopen = () => { $("feed").textContent = "connected"; send({ op: "chain", index: $("index").value }); };
    ws.onclose = () => { $("feed").textContent = "reconnecting..."; setTimeout(connect, 1000); };
    ws.onmessage = ev => {
      const m = JSON.parse(ev.data);
      if (m.type === "tick") {
        if (m.symbol === symbol) { onTick(m.ltp, m.rx); lastTickRx = m.rx; lastTickAt = Date.now(); if (m.rx) $("lat").textContent = `${Math.max(0, Math.round(Date.now() - m.rx * 1000))} ms`; }
        updateChainPrice(m.symbol, m.ltp);
        if (state.trades.some(t => t.symbol === m.symbol)) { const t = state.trades.find(t => t.symbol === m.symbol); t.ltp = m.ltp; }
      } else if (m.type === "state") { state = m; renderState(); draw(); }
      else if (m.type === "chain") { renderChain(m); }
      else if (m.type === "history") { if (m.symbol === symbol) { minutes = m.bars; rebuild(); } }
      else if (m.type === "ticket") { if (draft && m.ref === ticketRef) renderTicket(m); }
      else if (m.type === "result") { toast(m.msg, !m.ok); if (m.op === "arm" && m.ok) { draft = null; renderTicket(null); } if (m.op === "kill") { $("killdlg").style.display = "none"; } }
    };
  }

  // ---------------------------------------------------------------- panels
  function renderChain(c) {
    chain = c;
    const sel = $("symbol"), keep = symbol;
    sel.innerHTML = "";
    for (const r of c.rows) for (const [sym, ltp, kind] of [[r.ce, r.ce_ltp, "CE"], [r.pe, r.pe_ltp, "PE"]]) {
      const o = document.createElement("option"); o.value = sym; o.dataset.kind = kind;
      o.textContent = `${r.strike} ${kind}  (${fmt(ltp)})`; sel.appendChild(o);
    }
    const atm = c.rows.reduce((a, r) => (!a || Math.abs(r.strike - c.spot) < Math.abs(a.strike - c.spot)) ? r : a, null);
    sel.value = keep && [...sel.options].some(o => o.value === keep) ? keep : (atm ? atm.ce : "");
    $("legend").textContent = `${c.label} ${fmt(c.spot)} · expiry ${c.expiry} · lot ${c.lot_size}`;
    pick(sel.value);
  }
  function updateChainPrice(sym, ltp) {
    for (const o of $("symbol").options) if (o.value === sym) o.textContent = o.textContent.replace(/\(.*\)/, `(${fmt(ltp)})`);
  }
  function pick(sym) {
    if (!sym || sym === symbol) return;
    symbol = sym; minutes = []; bars = []; series.setData([]);
    send({ op: "history", symbol: sym });
    if (draft && draft.symbol !== sym) { draft = null; renderTicket(null); }
    draw();
  }
  function renderTicket(t) {
    const box = $("ticket");
    if (!t) { box.innerHTML = `<span style="color:#787b86">Press <b>+ Long box</b> and click your entry price on the option chart.</span>`; return; }
    const z = t.size || {};
    box.innerHTML = `<b>${t.symbol}</b> &nbsp;<span class="badge ${t.mode === "REAL" ? "real" : "paper"}">${t.mode}</span>
      <div class="grid" style="margin-top:6px">
        <div><span>Entry</span> ${fmt(t.entry)}</div><div><span>LTP</span> ${fmt(t.ltp)}</div>
        <div><span>Stop</span> ${fmt(t.stop)}</div><div><span>Target</span> ${fmt(t.target)}</div>
        <div><span>Lots</span> <b>${t.lots ?? 0}</b> (${t.qty ?? 0} qty)</div><div><span>R:R</span> 1:${t.rr ?? "-"}</div>
        <div><span>Risk</span> <b style="color:#ef5350">Rs ${fmt(t.risk_rs, 0)}</b></div><div><span>Reward</span> <b style="color:#26a69a">Rs ${fmt(t.reward_rs, 0)}</b></div>
        <div><span>Money</span> Rs ${fmt(z.money, 0)}</div><div><span>Decided by</span> ${z.decided_by || "-"}</div>
        <div><span>Capital lots</span> ${z.lots_capital ?? "-"}</div><div><span>Risk lots</span> ${z.lots_risk ?? "-"}</div>
      </div>
      ${(t.warnings || []).map(w => `<div class="warn">• ${w}</div>`).join("")}
      <div class="row"><button class="primary" id="armbtn" ${t.ok ? "" : "disabled"}>Arm</button><button id="discard">Discard</button></div>
      <div style="color:#787b86;margin-top:4px">Arm sends nothing yet: the BUY LIMIT (price + buffer) goes when the price reaches the entry.</div>`;
    $("armbtn").onclick = () => send({ op: "arm", symbol: draft.symbol, entry: draft.entry, stop: draft.stop, target: draft.target, t0: draft.t0 });
    $("discard").onclick = () => { draft = null; renderTicket(null); draw(); };
  }
  function renderState() {
    const s = state.settings || {};
    $("mode").textContent = state.mode || "PAPER"; $("mode").className = "badge " + (state.mode === "REAL" ? "real" : "paper");
    $("daypnl").textContent = fmt(state.day_pnl, 0); $("daypnl").style.color = state.day_pnl >= 0 ? "#26a69a" : "#ef5350";
    $("killinfo").textContent = state.kill || state.blocked || "";
    if (state.feed) $("feed").textContent = `prices: ${state.feed}`;
    const box = $("trades");
    box.innerHTML = state.trades.slice().reverse().map(t => `<div class="card">
      <div><span class="state ${t.state}">${t.state}</span> &nbsp;<b>${t.symbol}</b> <span class="badge ${t.mode === "REAL" ? "real" : "paper"}">${t.mode}</span></div>
      <div class="grid" style="margin-top:4px">
        <div><span>Qty</span> ${t.qty} (${t.lots} lots)</div><div><span>LTP</span> ${fmt(t.ltp)}</div>
        <div><span>Entry</span> ${fmt(t.entry_avg || t.entry)}</div><div><span>Stop</span> ${fmt(t.stop)}</div>
        <div><span>Target</span> ${fmt(t.target)}</div>
        <div><span>P&amp;L</span> <b style="color:${(t.pnl ?? t.open_pnl ?? 0) >= 0 ? "#26a69a" : "#ef5350"}">${t.pnl !== null && t.pnl !== undefined ? fmt(t.pnl, 0) : fmt(t.open_pnl, 0)}</b></div>
      </div>
      <div class="row">${t.state === "ARMED" ? `<button data-cancel="${t.id}">Cancel</button>` : ""}
        ${["ENTRY_WORKING", "IN_TRADE", "TARGET_EXITING", "STOP_CHASING", "EXITING"].includes(t.state) ? `<button data-exit="${t.id}">Exit now</button>` : ""}
        ${t.symbol !== symbol && !["CLOSED", "CANCELLED"].includes(t.state) ? `<button data-show="${t.symbol}">Show</button>` : ""}</div>
      <div class="notes">${(t.notes || []).slice(-4).join("<br>")}</div></div>`).join("") || `<div style="color:#787b86">No trades today.</div>`;
    box.querySelectorAll("[data-cancel]").forEach(b => b.onclick = () => send({ op: "cancel", id: b.dataset.cancel }));
    box.querySelectorAll("[data-exit]").forEach(b => b.onclick = () => send({ op: "exit", id: b.dataset.exit }));
    box.querySelectorAll("[data-show]").forEach(b => b.onclick = () => { const o = document.createElement("option"); o.value = b.dataset.show; o.textContent = b.dataset.show; $("symbol").appendChild(o); $("symbol").value = b.dataset.show; pick(b.dataset.show); });
    $("log").textContent = (state.log || []).join("\n");
    if (!document.activeElement || !document.activeElement.id.startsWith("s_")) {
      for (const k of ["allocation_pct", "max_risk", "max_day_loss", "buffer", "paper_capital", "real_max_lots"]) $("s_" + k).value = s[k] ?? "";
      $("s_strict").checked = !!s.strict; $("s_real_orders").checked = !!s.real_orders;
    }
    if (draft) askTicket();
  }

  // ---------------------------------------------------------------- controls
  $("index").onchange = () => { symbol = null; send({ op: "chain", index: $("index").value }); };
  $("symbol").onchange = () => pick($("symbol").value);
  document.querySelectorAll(".tf").forEach(b => b.onclick = () => { tf = Number(b.dataset.tf); document.querySelectorAll(".tf").forEach(x => x.classList.toggle("active", x === b)); rebuild(); });
  document.querySelector('.tf[data-tf="3"]').classList.add("active");
  $("longbox").onclick = () => { tool = !tool; $("longbox").classList.toggle("active", tool); $("hint").style.display = tool ? "block" : "none"; };
  $("savesettings").onclick = () => {
    const v = {};
    for (const k of ["allocation_pct", "max_risk", "buffer", "paper_capital"]) v[k] = Number($("s_" + k).value);
    v.real_max_lots = parseInt($("s_real_max_lots").value || "1", 10);
    v.max_day_loss = $("s_max_day_loss").value === "" ? null : Number($("s_max_day_loss").value);
    v.strict = $("s_strict").checked; v.real_orders = $("s_real_orders").checked;
    send({ op: "settings", values: v });
  };
  $("kill").onclick = () => { $("killtext").value = ""; $("killdlg").style.display = "flex"; $("killtext").focus(); };
  $("killno").onclick = () => $("killdlg").style.display = "none";
  $("killgo").onclick = () => send({ op: "kill", confirm: $("killtext").value.trim() });
  setInterval(() => { if (lastTickAt && Date.now() - lastTickAt > 5000) $("lat").textContent = "no tick for " + Math.round((Date.now() - lastTickAt) / 1000) + " s"; }, 1000);
  connect();
})();
