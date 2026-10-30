"""Builds docs/design/Chart_Risk_Engine_Design.pdf (design + feasibility of the chart-based risk engine).

    .venv\\Scripts\\python.exe docs\\design\\make_chart_risk_engine_pdf.py      (needs: pip install reportlab)
"""

from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

OUT = Path(__file__).with_name("Chart_Risk_Engine_Design.pdf")
FONT_DIRS = [Path("/usr/share/fonts/truetype/dejavu"), Path(r"C:\Windows\Fonts")]


def _font():
    for d in FONT_DIRS:
        if (d / "DejaVuSans.ttf").exists():
            pdfmetrics.registerFont(TTFont("DV", str(d / "DejaVuSans.ttf")))
            pdfmetrics.registerFont(TTFont("DVB", str(d / "DejaVuSans-Bold.ttf")))
            pdfmetrics.registerFont(TTFont("DVM", str(d / "DejaVuSansMono.ttf")))
            return "DV", "DVB", "DVM"
    return "Helvetica", "Helvetica-Bold", "Courier"


BODY, BOLD, MONO = _font()
NAVY, BLUE, GREEN, RED, GREY, AMBER = (colors.HexColor(x) for x in
                                       ("#1a2340", "#2962ff", "#16835f", "#c62828", "#5f6573", "#b26a00"))
ss = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("t", parent=ss["Title"], fontName=BOLD, fontSize=21, leading=26, textColor=NAVY),
    "sub": ParagraphStyle("s", parent=ss["Normal"], fontName=BODY, fontSize=11, leading=15, textColor=GREY,
                          alignment=TA_CENTER),
    "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontName=BOLD, fontSize=15, leading=19, textColor=NAVY,
                         spaceBefore=10, spaceAfter=6),
    "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName=BOLD, fontSize=12, leading=15, textColor=BLUE,
                         spaceBefore=8, spaceAfter=4),
    "p": ParagraphStyle("p", parent=ss["Normal"], fontName=BODY, fontSize=9.6, leading=13.4, spaceAfter=5),
    "b": ParagraphStyle("b", parent=ss["Normal"], fontName=BODY, fontSize=9.6, leading=13.4, leftIndent=12,
                        bulletIndent=2, spaceAfter=2),
    "code": ParagraphStyle("c", parent=ss["Code"], fontName=MONO, fontSize=8.4, leading=11, backColor=colors.HexColor(
        "#f3f5f9"), borderPadding=5, leftIndent=4, spaceBefore=3, spaceAfter=7),
    "cell": ParagraphStyle("cell", parent=ss["Normal"], fontName=BODY, fontSize=8.4, leading=10.8),
    "cellb": ParagraphStyle("cellb", parent=ss["Normal"], fontName=BOLD, fontSize=8.4, leading=10.8,
                            textColor=colors.white),
    "box": ParagraphStyle("box", parent=ss["Normal"], fontName=BODY, fontSize=9.6, leading=13.4),
}


def P(text, style="p"):
    return Paragraph(text, S[style])


def bullets(items):
    return [Paragraph(x, S["b"], bulletText="•") for x in items]


def table(rows, widths, head=True, zebra=True):
    data = [[Paragraph(str(c), S["cellb" if head and i == 0 else "cell"]) for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1 if head else 0)
    style = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9ced8")),
             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if head:
        style.append(("BACKGROUND", (0, 0), (-1, 0), NAVY))
    if zebra:
        for i in range(1 if head else 0, len(rows)):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f5f7fb")))
    t.setStyle(TableStyle(style))
    return t


def callout(text, color=BLUE):
    t = Table([[Paragraph(text, S["box"])]], colWidths=[174 * mm])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1.2, color), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                           ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f9ff"))]))
    return t


def diagram():
    d = Drawing(174 * mm, 92 * mm)
    W = 174 * mm

    def box(x, y, w, h, title, lines, color):
        d.add(Rect(x, y, w, h, rx=4, ry=4, fillColor=colors.white, strokeColor=color, strokeWidth=1.3))
        d.add(Rect(x, y + h - 15, w, 15, rx=4, ry=4, fillColor=color, strokeColor=color))
        d.add(String(x + w / 2, y + h - 11, title, fontName=BOLD, fontSize=8.2, fillColor=colors.white,
                     textAnchor="middle"))
        for i, ln in enumerate(lines):
            d.add(String(x + 5, y + h - 27 - i * 10, ln, fontName=BODY, fontSize=7.2, fillColor=colors.black))

    def arrow(x1, y1, x2, y2, label="", color=GREY):
        d.add(Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=1.2))
        import math
        a = math.atan2(y2 - y1, x2 - x1)
        p = [x2, y2, x2 - 7 * math.cos(a - 0.4), y2 - 7 * math.sin(a - 0.4), x2 - 7 * math.cos(a + 0.4),
             y2 - 7 * math.sin(a + 0.4)]
        d.add(Polygon(p, fillColor=color, strokeColor=color))
        if label:
            d.add(String((x1 + x2) / 2, (y1 + y2) / 2 + 3, label, fontName=BODY, fontSize=6.8, fillColor=color,
                         textAnchor="middle"))

    h = 92 * mm
    cw, gap = 150, (W - 3 * 150) / 2                     # three columns that never overlap
    x1, x2, x3 = 0, cw + gap, 2 * (cw + gap)
    top, low, bh = h - 98, 8, 92
    box(x1, top, cw, bh, "1. CHART (browser)", ["Your candles (index / option)", "Long / Short R:R box",
                                                 "drag Entry / SL / Target", "Arm / Cancel / Exit buttons",
                                                 "live P&L, order status", "our own chart: we know",
                                                 "every coordinate"], BLUE)
    box(x2, top, cw, bh, "2. RISK ENGINE (Python)", ["validate the box", "lot size from FYERS master",
                                                      "qty = min(capital, risk)", "tick rounding, buffers",
                                                      "debounce drags (0.5 s)", "state machine per trade",
                                                      "journal every step"], GREEN)
    box(x3, top, cw, bh, "3. ORDER MANAGER", ["Limit entry (CMP + 2)", "one SL-L exit at exchange",
                                              "target watched locally", "modify on every drag",
                                              "partial fills, rejects", "never market orders"], AMBER)
    box(x1, low, cw, 82, "5. SAFETY", ["daily arm + Real switch", "max loss / day, max trades",
                                       "kill switch (exit all)", "15:10 square off", "token / net loss watchdog"], RED)
    box(x2, low, cw, 82, "4. FYERS API v3", ["REST: place / modify / cancel", "order websocket: fills",
                                             "data websocket: ticks (LTP)", "symbol master: lot, tick",
                                             "static IP whitelisted"], NAVY)
    box(x3, low, cw, 82, "6. RECORDS", ["orders_journal.jsonl", "trades.csv (R, P&L)", "events.log",
                                        "replay on the chart"], GREY)
    mid_top, mid_low = top + bh / 2, low + 41
    arrow(x1 + cw, mid_top, x2, mid_top, "box / drag", BLUE)
    arrow(x2 + cw, mid_top, x3, mid_top, "plan", GREEN)
    arrow(x3 + cw / 2, top, x2 + cw * 0.75, low + 82, "orders", AMBER)
    arrow(x2 + cw * 0.35, low + 82, x2 + cw * 0.35, top, "ticks, fills", NAVY)
    arrow(x1 + cw, mid_low, x2, mid_low, "limits", RED)
    arrow(x2 + cw, mid_low, x3, mid_low, "log", GREY)
    return d


def state_machine():
    rows = [["State", "What is at the exchange", "Moves to", "Trigger"],
            ["DRAFT", "nothing (box drawn, not armed)", "ARMED", "you press Arm (or auto-arm setting)"],
            ["ARMED", "nothing; engine watches entry condition", "ENTRY_WORKING", "price reaches entry (or at once if 'enter now')"],
            ["ENTRY_WORKING", "BUY LIMIT at entry + 2", "IN_TRADE / CANCELLED", "filled (full or part) / not filled in N s"],
            ["IN_TRADE", "ONE SELL SL-L: trigger = SL, limit = SL - 2, qty = filled qty",
             "TARGET_EXITING / STOPPED / EXITING", "LTP >= target / SL order filled / opposite action"],
            ["TARGET_EXITING", "the same order modified to SELL LIMIT at target - 2 (or LTP - 2)", "CLOSED",
             "filled; if not filled in N s: re-price down, never market"],
            ["STOP_CHASING", "SL triggered but price gapped below the limit", "CLOSED",
             "engine lowers the limit step by step (alert on screen)"],
            ["CLOSED", "nothing", "-", "trade written to trades.csv"]]
    return table(rows, [28, 58, 38, 50])


def build():
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title="Chart Risk Engine - Design and Feasibility",
                            author="Kunal Quant / Claude")
    st = []
    st += [Spacer(1, 30 * mm), P("Chart-Based Risk Engine", "title"),
           P("Semi-automated execution for SENSEX, NIFTY 50 and BANK NIFTY options on FYERS", "sub"),
           Spacer(1, 6 * mm), P("Design, feasibility answers, sizing maths, order flow, safety rules and the build "
                                "plan for the kunal-fyers-terminal project", "sub"),
           Spacer(1, 4 * mm), P("Version 1 - 30 Oct 2026 - for Kunal (owner)", "sub"), Spacer(1, 16 * mm),
           callout("<b>Short answer: YES, it is workable</b> - with one important change. Charts inside FYERS Web / "
                   "TradingView do <b>not</b> let any program read your hand-drawn Long/Short box or notice when you "
                   "drag it. So the box must be drawn on <b>our own chart</b> inside kunal-fyers-terminal (the same "
                   "TradingView-style chart you already use there). On our chart we know every coordinate, so "
                   "drawing, dragging and order-modify work reliably. Everything else in your specification "
                   "(dual-constraint sizing, limit + buffer orders, SL-L, dynamic modify) is fully possible with "
                   "the FYERS API v3.", GREEN),
           Spacer(1, 6 * mm),
           callout("<b>Where to build it:</b> your suggestion is right - the existing <b>kunal-fyers-terminal</b> "
                   "repository, on a new branch <b>feature/chart-risk-engine</b>. No new repository is needed: the "
                   "login, FYERS client, contract list (lot sizes), limit-order executor, daily arm switch, chart "
                   "and tests are already there and would otherwise be copied.", BLUE),
           PageBreak()]

    # 1 ----------------------------------------------------------------
    st += [P("1. What you asked for, in one picture", "h1"),
           P("You keep doing the analysis. The system only does the arithmetic and the order handling, exactly and "
             "every time. It never predicts direction."), diagram(),
           P("How one trade flows:", "h2")]
    st += bullets([
        "You draw a <b>Long box</b> (buy CALL) or a <b>Short box</b> (buy PUT - bearish = buy put, never sell "
        "options) on the option chart or the index chart. The box has three handles: <b>Entry</b>, <b>Stop</b>, "
        "<b>Target</b>.",
        "A small ticket appears next to the box: lots, quantity, money used, rupee risk, rupee reward, R:R, and "
        "which limit (capital or risk) decided the size. Nothing is sent yet.",
        "You press <b>Arm</b>. The engine places the BUY LIMIT (entry + Rs 2) when the price is at the entry.",
        "Once filled, ONE protective SELL SL-L order sits at the exchange (trigger = stop, limit = stop - Rs 2).",
        "You drag the Stop handle: after you let go, the SL-L order is modified at FYERS (trigger and limit). "
        "You drag the Target handle: the engine's target moves (and, if you choose the 'target order at exchange' "
        "mode, the order is modified).",
        "Target reached: the SAME exit order is changed into a SELL LIMIT at target - Rs 2. So there is never a "
        "second sell order that could fill twice."])

    # 2 ----------------------------------------------------------------
    st += [P("2. Answers to your feasibility questions", "h1"),
           P("2.1 Can FYERS Web / TradingView charts or Pine Script read the Long/Short box?", "h2"),
           table([["Way", "Can it read the box and its drags?", "Verdict"],
                  ["Pine Script (TradingView or FYERS Smart Script)",
                   "No. Pine has no access to your manual drawings at all. It only sees prices and its own inputs.",
                   "Not possible"],
                  ["Approach A - Chrome extension reading TradingView's page",
                   "Only by reaching into TradingView's private code inside the page. Drawings are painted on a "
                   "canvas; there is no public way to read them. It breaks whenever TradingView or FYERS updates the "
                   "website (often weekly), and it may break their terms of use.",
                   "Fragile - not for real money"],
                  ["Approach B - Pine input.price drag points + alert webhooks",
                   "You can drag interactive input lines, BUT an alert keeps the values it had when it was created. "
                   "After a drag you must delete and re-create the alert by hand. Also webhooks need a TradingView "
                   "paid plan, a public web address for our program, and add seconds of delay. FYERS Smart Script "
                   "cannot send webhooks to our program.",
                   "Workable only for 'set once' levels - not for live dragging"],
                  ["Approach C - separate desktop overlay window",
                   "It cannot see the browser chart either; you would type or click levels in a second window "
                   "that is not lined up with the candles.",
                   "Works, but clumsy"],
                  ["Approach D (recommended) - draw the box on OUR chart in kunal-fyers-terminal",
                   "Yes, completely. Our chart (TradingView Lightweight Charts) is our own code, so every click, "
                   "drag and release is an event we receive with the exact price.",
                   "Reliable - recommended"]],
                 [44, 96, 34]),
           Spacer(1, 3 * mm),
           P("You can still look at FYERS Web / TradingView for analysis on a second screen. The trade itself is "
             "drawn and managed on the terminal chart, which shows the same FYERS candles (index or option) with "
             "your KTMI, VWAP and BBP panes."),
           P("2.2 Brackets / OCO: does FYERS do it for us?", "h2"),
           table([["FYERS feature", "What it does", "Use it?"],
                  ["Order types 1 Limit, 4 Stop-Limit (SL-L)", "Exactly what your spec needs (entry limit, SL-L exit)",
                   "YES - the core"],
                  ["Modify order (price, trigger, qty, type)", "Change a pending order without cancelling it",
                   "YES - every drag"],
                  ["Order websocket (fills / rejects live)", "Tells us at once when an order fills, part-fills or is "
                   "rejected", "YES - drives the state machine"],
                  ["Take-profit / stop-loss offsets on place_order (API v3.2)",
                   "FYERS attaches TP/SL legs to an entry. It is not clear yet whether these legs exit with LIMIT or "
                   "MARKET orders, and whether they work for INTRADAY options.",
                   "Test with 1 lot first; use only if the exits are limit orders"],
                  ["GTT / OCO orders", "Two-leg trigger orders, documented for CNC / MARGIN / MTF products",
                   "Not for intraday option buying"],
                  ["Market orders", "-", "NEVER (your rule, test-guarded in the code)"]],
                 [50, 84, 40]),
           Spacer(1, 3 * mm),
           callout("<b>Our bracket = 'one exit order' synthetic OCO.</b> Placing BOTH a target sell and a stop sell "
                   "for the full quantity is dangerous: if the price touches the target and then falls, both could "
                   "fill and you would be SHORT an option (unlimited risk, margin call) - and FYERS may reject the "
                   "second sell for margin. So only ONE sell order ever exists: the SL-L protection at the exchange "
                   "(it protects you even if your PC or internet dies). The engine watches the target with live "
                   "ticks and, when it is reached, <b>modifies that same order</b> into a limit sell at the target. "
                   "One order, one fill, no double exit.", AMBER),
           PageBreak()]

    # 3 ----------------------------------------------------------------
    st += [P("3. Position sizing: the two limits", "h1"),
           P("Every trade is sized twice and the <b>smaller</b> quantity wins, always in whole lots."),
           P("Inputs", "h2"),
           table([["Input", "Meaning", "Example"],
                  ["Total capital", "FYERS Available Balance (real) or your Paper capital", "Rs 50,000"],
                  ["Capital allocation %", "Most of the capital one trade may use", "50 % = Rs 25,000"],
                  ["Max risk per trade", "Most rupees one trade may lose", "Rs 5,000"],
                  ["Entry, Stop, Target", "From the box handles (option premium)", "100 / 75 / 150"],
                  ["Lot size, tick size", "From the FYERS contract list every morning (not typed)",
                   "SENSEX 20, tick 0.05"],
                  ["Buffer", "Limit buffer for entry and stop (setting)", "Rs 2"]], [42, 92, 40]),
           P("Formulas", "h2"),
           P("Capital-capped:  lots_capital = floor( capital x allocation% / (entry_limit x lot) )<br/>"
             "Risk-capped:  lots_risk = floor( max_risk / (risk_per_unit x lot) )<br/>"
             "Final:  lots = min(lots_capital, lots_risk);  quantity = lots x lot.  Zero lots = no trade, with the "
             "reason on the ticket.", "code"),
           P("Your example, done two ways", "h2"),
           table([["", "Your spec (plain)", "Strict (recommended)"],
                  ["Entry price used", "100", "102 (the limit we send: worst fill)"],
                  ["Stop price used", "75", "73 (SL-L limit: worst normal fill)"],
                  ["Risk per unit", "25", "29"],
                  ["Capital check", "25,000 / (100 x 20) = 12.5 -> 12 lots (240)", "25,000 / (102 x 20) = 12.25 -> 12 lots"],
                  ["Risk check", "5,000 / (25 x 20) = 10 -> 10 lots (200)", "5,000 / (29 x 20) = 8.6 -> 8 lots (160)"],
                  ["Final", "10 lots = 200 qty, worst real loss about Rs 5,800 + charges",
                   "8 lots = 160 qty, worst normal loss Rs 4,640 + charges (under Rs 5,000)"]],
                 [34, 70, 70]),
           Spacer(1, 2 * mm),
           callout("Why strict: with buffers, the entry can fill up to Rs 2 higher and the stop can fill up to Rs 2 "
                   "lower, so 'plain' sizing can quietly lose more than your Rs 5,000 cap. Strict sizing also adds "
                   "the estimated charges (brokerage, STT, exchange, GST, stamp - about Rs 60-120 a trade) to the "
                   "risk. You choose: a 'Strict sizing' switch, ON by default.", BLUE),
           P("Extra checks before any order", "h2")]
    st += bullets([
        "Long box: stop below entry and target above entry; Short box (buy PUT) the same on the PUT premium "
        "(drawn on the index chart it is mirrored - see section 6).",
        "Quantity above the exchange freeze limit for one order: split into several orders (FYERS slice orders) "
        "- the limits are a setting, checked against the exchange circular.",
        "Stop too close (less than 3 ticks) or R:R below a minimum you set: warning, Arm stays disabled.",
        "Daily loss limit reached, max trades per day reached, or after 15:00: Arm disabled.",
        "Prices rounded to the 0.05 tick: buy limits round UP, sell limits round DOWN (already in the code)."])

    # 4 ----------------------------------------------------------------
    st += [PageBreak(), P("4. Orders: exactly what is sent to FYERS", "h1"),
           table([["Step", "FYERS order", "Example (entry 100, stop 80, target 150, buffer 2)"],
                  ["Entry", "type 1 LIMIT, side BUY, productType INTRADAY, validity DAY",
                   "limitPrice 102.00. Not filled in 10 s: re-priced once to LTP + 2, at most Rs 1 above the first "
                   "limit (your existing rule). Still not filled: cancelled, ticket says why."],
                  ["Protection", "type 4 SL-L, side SELL, qty = FILLED qty", "stopPrice 80.00, limitPrice 78.00 "
                   "(placed the moment the entry fills; part fill -> SL for the part, raised as more fills)"],
                  ["Stop dragged to 85", "modify_order(id, stopPrice, limitPrice)", "stopPrice 85.00, limitPrice 83.00"],
                  ["Target reached (LTP >= 150)", "modify the SAME order: type 1 LIMIT SELL",
                   "limitPrice = min(150, LTP) - 2 = 148.00 -> fills at the best bid (often 150+)"],
                  ["Target dragged to 160", "engine target moves (no order yet)", "the ticket shows the new R:R"],
                  ["You press Exit now", "modify the SAME order: LIMIT SELL at LTP - 2", "never market"],
                  ["15:10", "same as Exit now, for every open trade", "your daily rule"]],
                 [30, 52, 92]),
           P("Optional 'target order at the exchange' mode", "h2"),
           P("If you prefer to SEE the target order in FYERS: the engine keeps a LIMIT SELL at the target and NO "
             "stop order at the exchange; the stop is then watched by the engine (ticks) and executed as a limit "
             "sell at stop - 2. Faster target fills, but if the PC / internet dies there is no stop at the "
             "exchange. Default stays 'stop at exchange' (safer)."),
           P("Why SL-L can still fail - and what the engine does", "h2"),
           P("In a very fast fall the price can jump from 80 to 74 in one tick. The SL-L triggers at 80 but its limit "
             "78 is above the market, so it does not fill. Your rule forbids market orders, so the engine 'chases': "
             "every 2 seconds it lowers the limit to LTP - 2, up to a maximum number of steps, with a red alert and "
             "a sound. This is the honest risk of limit-only exits; the backtest already models it."),
           P("5. Dragging the box: how a modify is sent", "h1")]
    st += bullets([
        "While you drag, only the drawing and the ticket move (no orders). The order is modified when you RELEASE "
        "the handle, and only if the price really changed by at least one tick.",
        "Debounce 0.5 s and at most a few modifies per second (FYERS allows about 10 requests a second); a drag "
        "storm is merged into one modify.",
        "Rules on release: a stop dragged above the current price (long) would trigger at once -> the engine asks "
        "'Exit now?' instead of sending an instantly-triggered order. Stop dragged further away -> the risk grows: "
        "if the new rupee risk is above Max risk, the drag is refused (or quantity reduction is offered, your "
        "choice). Quantity is never increased by a drag.",
        "Every modify is confirmed by the order websocket; the handle turns grey while pending and back to colour "
        "when FYERS confirms. If FYERS rejects, the handle jumps back to the price FYERS actually holds.",
        "The line on the chart always shows the price FYERS holds - never a hoped-for price."])

    # 6 ----------------------------------------------------------------
    st += [P("6. Box on the option chart or on the index chart", "h1"),
           table([["", "Box on the OPTION chart (recommended first)", "Box on the INDEX chart"],
                  ["Levels mean", "option premium (exact)", "index points (your S/R zones)"],
                  ["Stop at exchange", "SL-L on the option at your stop", "engine watches the INDEX; when the index "
                   "crosses your stop it sells the option with a limit. Plus a wide 'disaster' SL-L on the option "
                   "at the exchange in case the PC dies"],
                  ["Risk in rupees", "exact: (entry - stop) x qty", "estimated with the option delta (changes "
                   "during the trade) - sized with a safety margin"],
                  ["Which strike", "the one on the chart", "rule you choose: ATM / ATM+1 / premium range / per lot "
                   "amount (already built)"],
                  ["Short box", "draw it on the PUT chart as a Long box", "Short box on the index = buy the PUT"]],
                 [30, 72, 72]),
           P("Plan: phase 1 = option chart only (exact risk). Phase 2 = index chart with the mapping above.")]

    # 7 ----------------------------------------------------------------
    st += [P("7. The trade life cycle (state machine)", "h1"),
           P("Every trade is always in exactly one state, saved to disk after every change, so a restart picks up "
             "exactly where it was (and re-reads the real orders from FYERS before doing anything)."),
           state_machine(),
           P("8. Safety rules and edge cases", "h1"),
           table([["Situation", "What the system does"],
                  ["PC, browser or internet dies", "The SL-L protection is AT THE EXCHANGE, so the stop still works. "
                   "On restart the engine reads the orderbook + positions from FYERS and rebuilds each trade."],
                  ["FYERS token expires / logged out", "No new entries; open trades keep their exchange SL; red banner "
                   "'log in again'."],
                  ["Partial fill of the entry", "SL quantity = filled quantity, raised as more fills arrive; the "
                   "unfilled rest is cancelled after the timeout."],
                  ["Order rejected (price band, margin, freeze qty)", "Shown on the ticket with FYERS' message; no "
                   "retry loop; trade -> CANCELLED or ALERT."],
                  ["Two sells could fill", "Impossible by design: only one exit order exists per trade."],
                  ["You also trade manually in the FYERS app", "The engine matches positions by symbol; a position it "
                   "did not open is shown but never touched."],
                  ["Duplicate clicks / double Arm", "Every order carries a unique tag; the same tag is never sent "
                   "twice."],
                  ["Max loss per day reached", "All Arm buttons disabled for the day; open trades keep their stops."],
                  ["Kill switch", "One button: cancel all working entries, exit all trades with limit orders."],
                  ["15:10", "Everything is closed with limit orders (your rule)."],
                  ["Expiry day", "Warning on the ticket (fast premium decay, wide spreads); optional block after "
                   "a time you set."],
                  ["Real orders", "Only when the Real switch is ON and today is armed (python arm_real_orders.py) and "
                   "your static IP is whitelisted at FYERS (SEBI rule). Otherwise everything runs as paper with the "
                   "same screens."]],
                 [48, 126]),
           PageBreak()]

    # 9 ----------------------------------------------------------------
    st += [P("9. Code structure (branch feature/chart-risk-engine)", "h1"),
           P("New files next to what already exists; the BBP_NM strategy and the backtest are not changed.")]
    st += [P("kunal-fyers-terminal/<br/>"
             "  terminal/risk/sizing.py        dual-constraint quantity (plain / strict), charges<br/>"
             "  terminal/risk/levels.py        box -> validated plan (tick rounding, R:R, checks)<br/>"
             "  terminal/risk/trade.py         trade state machine + saving / restoring<br/>"
             "  terminal/risk/orders.py        place / modify / cancel via FYERS, one-exit-order OCO<br/>"
             "  terminal/risk/stream.py        FYERS data + order websockets (ticks, fills)<br/>"
             "  terminal/risk/guard.py         daily loss, max trades, kill switch, 15:10, watchdog<br/>"
             "  terminal/risk/server.py        local link chart <-> engine (only on this PC)<br/>"
             "  static/risk_box.js             the Long/Short box drawing, handles, ticket<br/>"
             "  app.py                         new view 'Manual trade (R:R box)'<br/>"
             "  tests/test_risk_*.py           every rule above as a test, fake FYERS<br/>"
             "Reused: fyers.py (login, quotes, contract list), live_trading/ (limit orders, arm switch, journal), "
             "chart, settings, backtest.", "code"),
           P("10. Build plan", "h1"),
           table([["Phase", "What you get", "How it is checked"],
                  ["1. Sizing + ticket", "Draw a box on the option chart, see lots / risk / reward live",
                   "tests with your examples (SENSEX 20: 10 lots plain, 8 strict)"],
                  ["2. Paper execution", "Arm, entry, SL, target, drag-modify, exit - all simulated on live prices",
                   "a full paper day; journal shows every would-be order"],
                  ["3. Real orders, 1 lot", "Same screens, real FYERS orders, 1 lot only",
                   "you watch the FYERS orderbook change as you drag"],
                  ["4. Full size + index chart", "Normal sizing, boxes on the index chart, disaster stop",
                   "1 week live with small risk"]],
                 [34, 80, 60]),
           P("11. Your day with it", "h1")]
    st += bullets([
        "Folder C:\\Users\\USER\\Desktop\\Kunal\\kunal-fyers-terminal: double-click start_terminal.bat, log in.",
        "Open 'Manual trade (R:R box)'. Set (once): capital allocation %, max risk per trade, max loss per day, "
        "buffer, strict sizing.",
        "Pick the index and the option (or click a strike in the option chain): its chart opens.",
        "Click 'Long box', click your entry, stop and target on the chart. Read the ticket.",
        "Press Arm. Watch: entry working -> in trade (green) -> target / stop. Drag the stop to trail.",
        "Real money only after: Settings 'Real orders' ON + python arm_real_orders.py (today) + static IP."])

    st += [P("12. Questions for you (answers decide the details)", "h1")]
    st += bullets([
        "<b>Q1</b> Is drawing the box on the terminal chart (not FYERS Web) OK for you?",
        "<b>Q2</b> Phase 1 on the OPTION chart only - OK? Later: which strike rule for boxes on the index chart "
        "(ATM / ATM+1 / premium range / per lot amount)?",
        "<b>Q3</b> Sizing: Strict (worst fill + charges, recommended) or Plain (exactly your formula)?",
        "<b>Q4</b> Entry: only when the price reaches the box entry (pending), or also 'enter now at CMP + 2'?",
        "<b>Q5</b> Default: stop at the exchange + target watched by the engine (safer) - or target at the "
        "exchange + stop watched?",
        "<b>Q6</b> Dragging the stop further away so that risk goes above Max risk: refuse the drag, or reduce "
        "quantity automatically?",
        "<b>Q7</b> Several trades at the same time? Max how many, and max loss per day in rupees?",
        "<b>Q8</b> Partial exits (e.g. half at 1:1, rest trailed) - wanted now or later?",
        "<b>Q9</b> Should the existing BBP_NM auto-strategy and the manual box trades share one capital pool?",
        "<b>Q10</b> Is your static IP already whitelisted at FYERS for API orders?"])
    st += [PageBreak(), P("13. Your answers (30 Oct 2026) and what was built", "h1"),
           table([["Question", "Your answer", "Built as"],
                  ["Q1 terminal chart", "OK if there is no lag (milliseconds fine, not seconds)",
                   "FYERS live websocket -> desk -> chart over a local link: about 1 ms on this PC (measured in the "
                   "tests); an order change = one FYERS call (about 100-300 ms, FYERS side)"],
                  ["Q2 option chart first", "agreed", "boxes on the option chart; index-chart boxes later"],
                  ["Q3 sizing", "your formula by default + a switch", "PLAIN default (10 lots in the SENSEX example); "
                   "'Strict sizing' switch (8 lots)"],
                  ["Kill switch", "on every page; broker kill switch; no new orders for 12 h",
                   "red KILL SWITCH on every page: cancel all + limit close-out + 12 h lock. FYERS has no API for its "
                   "own kill switch, so the screen tells you to press 'Take a Break' in FYERS"],
                  ["Q4 entry", "only when the price reaches the box", "ARMED until reached, then BUY LIMIT price + 2"],
                  ["Q5 exits", "the safer one", "SL-L at FYERS, target watched, same order modified"],
                  ["Q6 risky drag", "do not reduce the quantity", "the drag is refused and the line jumps back"],
                  ["Q7 trades / loss", "no cap; max loss box", "no trade cap; 'Max loss per day' box (empty = none)"],
                  ["Q8 partial exits", "later", "not built"],
                  ["Q9 capital pool", "same pool later, separate now", "desk has its own capital for now"],
                  ["Q10 static IP", "to be confirmed", "real orders stay OFF until you confirm; then 1 lot first"]],
                 [30, 56, 88])]
    st += [Spacer(1, 4 * mm),
           callout("Notes: this is a tool that executes YOUR decisions; it is not advice and cannot remove market "
                   "risk (gaps, fast moves, liquidity). SEBI's 2026 rules for retail API trading (static IP, order "
                   "rate limits, algo tagging) apply - FYERS' current requirements must be checked before going "
                   "live with real orders.", GREY)]

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont(BODY, 7.5)
        canvas.setFillColor(GREY)
        canvas.drawString(18 * mm, 9 * mm, "Kunal FYERS Terminal - Chart Risk Engine design v1 (30 Oct 2026)")
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"page {doc_.page}")
        canvas.restoreState()

    doc.build(st, onFirstPage=footer, onLaterPages=footer)
    return OUT


if __name__ == "__main__":
    print(build())
