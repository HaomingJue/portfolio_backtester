"""
Streamlit UI for the portfolio backtester.

Run:
    streamlit run app.py

Two pages (left sidebar):

  🛠️ Build & backtest — one page that does everything for a single portfolio:
        search any ticker and add it, set any weights (the leftover is cash),
        see a pie / bar of the composition and a sector breakdown, name & save
        it, and backtest it over *any* date range. A holding that hadn't launched
        yet at the chosen start is held as cash until its first trading day.

  📂 Saved portfolios — the list of everything you've saved. Click one to see its
        holdings table, then load it straight into the builder.

Reuses the engine in backtest.py (live Yahoo downloads, simulation, metrics).
All prices come from Yahoo Finance — there is no synthetic/modeled history.
Saved portfolios live in saved_portfolios.json next to this file (or the .exe).
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import altair as alt
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd
import streamlit as st

from backtest import (
    AUTO_COLORS,
    compute_metrics,
    download_prices,
    fetch_ticker_meta,
    fetch_ticker_overview,
    simulate_with_cash,
    ticker_has_data,
)

ROOT = Path(__file__).parent

# When packaged as an .exe (PyInstaller), keep the user-saved file next to the
# executable so it persists; fall back to the project folder when run from source.
DATA_DIR   = (Path(sys.executable).parent if getattr(sys, "frozen", False)
              else ROOT)
SAVED_PATH = DATA_DIR / "saved_portfolios.json"

REBAL_OPTS = ["none", "daily", "weekly", "monthly", "quarterly", "yearly"]

NAV_BUILD = "🛠️ Build & backtest"
NAV_SAVED = "📂 Saved portfolios"

CASH_LABEL = "Cash"

st.set_page_config(page_title="Portfolio Backtester", page_icon="📈",
                   layout="wide")


# ---------------------------------------------------------------------------
# Cached data access
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner="Downloading price data…")
def cached_prices(tickers: tuple[str, ...], start: str, end: str) -> pd.DataFrame:
    # require_all=False → a ticker with no data in range becomes an all-NaN
    # column, which simulate_with_cash simply holds as cash (never launched).
    return download_prices(list(tickers), start, end, require_all=False)


@st.cache_data(ttl=900, show_spinner="Looking up ticker…")
def cached_overview(symbol: str, start: str | None, end: str | None,
                    period: str | None) -> dict:
    return fetch_ticker_overview(symbol, start=start, end=end, period=period)


@st.cache_data(ttl=86400, show_spinner=False)
def cached_meta(symbol: str) -> dict:
    return fetch_ticker_meta(symbol)


@st.cache_data(ttl=900, show_spinner=False)
def cached_valid(symbol: str) -> bool:
    return ticker_has_data(symbol)


# ---------------------------------------------------------------------------
# Saved-portfolio persistence
# ---------------------------------------------------------------------------

def load_saved() -> dict:
    """Return {name: {"weights": {ticker: frac}, "rebalance": str|None}}."""
    if SAVED_PATH.exists():
        try:
            data = json.loads(SAVED_PATH.read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def write_saved(data: dict) -> None:
    SAVED_PATH.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def human_money(x: float | None) -> str:
    if x is None:
        return "—"
    x = float(x)
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(x) >= div:
            return f"${x/div:.2f}{unit}"
    return f"${x:,.0f}"


def fmt_num(x: float | None, prefix: str = "", suffix: str = "", dp: int = 2) -> str:
    if x is None:
        return "—"
    try:
        return f"{prefix}{float(x):,.{dp}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


# ---------------------------------------------------------------------------
# Builder session state
#
# Two pieces of state, deliberately separate:
#   builder_df  — the DataFrame fed to st.data_editor. Its object identity stays
#                 STABLE between programmatic changes, so the editor never sees
#                 its input "change underneath it" mid-typing and never discards
#                 the cell you're editing. Only set_builder() replaces it.
#   builder     — a plain list-of-dicts SNAPSHOT of the editor's latest output,
#                 refreshed every run and read by composition/save/callbacks.
#                 Never fed back into the editor, so there's no feedback loop.
# ---------------------------------------------------------------------------

BLANK_ROW = {"Ticker": "", "Weight %": 0.0}


def _builder_df(rows: list[dict]) -> pd.DataFrame:
    """Dtype-stable DataFrame (string Ticker, float Weight %) for the editor."""
    df = pd.DataFrame(rows or [BLANK_ROW]).reindex(columns=["Ticker", "Weight %"])
    df["Ticker"]   = df["Ticker"].fillna("").astype("string")
    df["Weight %"] = pd.to_numeric(df["Weight %"], errors="coerce").fillna(0.0)
    return df


if "nav" not in st.session_state:
    st.session_state.nav = NAV_BUILD
if "builder" not in st.session_state:
    st.session_state.builder = [dict(BLANK_ROW)]
if "builder_df" not in st.session_state:
    st.session_state.builder_df = _builder_df(st.session_state.builder)
if "builder_name" not in st.session_state:
    st.session_state.builder_name = "My Portfolio"
# Bumped whenever we change the builder programmatically, so the data_editor
# (keyed on this version) re-seeds instead of clinging to stale widget state.
if "builder_version" not in st.session_state:
    st.session_state.builder_version = 0


def clean_rows(records: list[dict]) -> list[tuple[str, float]]:
    """(ticker, weight%) pairs from editor records, skipping blanks; NaN-safe."""
    out = []
    for r in records:
        t = r.get("Ticker")
        if t is None or (isinstance(t, float) and pd.isna(t)):
            continue
        t = str(t).strip().upper()
        if not t:
            continue
        w = r.get("Weight %")
        if w is None or (isinstance(w, float) and pd.isna(w)):
            w = 0.0
        out.append((t, float(w)))
    return out


def set_builder(rows: list[dict]) -> None:
    """Replace builder contents (snapshot + editor seed) and force a re-seed.
    The new builder_df object identity, plus the bumped version key, make the
    editor rebuild from these rows."""
    rows = rows or [dict(BLANK_ROW)]
    st.session_state.builder    = rows
    st.session_state.builder_df = _builder_df(rows)
    st.session_state.builder_version += 1


def add_to_builder(symbol: str) -> None:
    """Append a ticker to the builder unless it's already there."""
    symbol = symbol.strip().upper()
    existing = [{"Ticker": t, "Weight %": w} for t, w in
                clean_rows(st.session_state.builder)]
    if any(r["Ticker"] == symbol for r in existing):
        return
    existing.append({"Ticker": symbol, "Weight %": 0.0})
    set_builder(existing)


def remove_from_builder(symbol: str) -> None:
    """Drop a ticker (all rows matching it) from the builder."""
    symbol = symbol.strip().upper()
    kept = [{"Ticker": t, "Weight %": w} for t, w in
            clean_rows(st.session_state.builder) if t != symbol]
    set_builder(kept)


def normalize_builder() -> None:
    """Scale current weights so they sum to 100%."""
    rows = clean_rows(st.session_state.builder)
    total = sum(w for _, w in rows)
    if total > 0:
        set_builder([{"Ticker": t, "Weight %": round(w / total * 100, 2)}
                     for t, w in rows])


def clear_builder() -> None:
    """Empty the builder back to a single blank row."""
    set_builder([{"Ticker": "", "Weight %": 0.0}])


def load_into_builder(name: str, saved: dict) -> None:
    """Load a saved portfolio into the builder and jump to the build page."""
    weights = saved.get(name, {}).get("weights", {})
    rows = [{"Ticker": t, "Weight %": round(float(v) * 100, 2)}
            for t, v in weights.items()]
    set_builder(rows)
    st.session_state.builder_name = name
    st.session_state.nav = NAV_BUILD


# ---------------------------------------------------------------------------
# Composition (pie + bar) and sector breakdown
# ---------------------------------------------------------------------------

def composition_dict(weights_frac: dict[str, float]) -> dict[str, float]:
    """Holdings as fractions, plus a Cash slice for the leftover under 100%."""
    comp = {t: w for t, w in weights_frac.items() if w > 0}
    cash = max(0.0, 1.0 - sum(weights_frac.values()))
    if cash > 0.0005:
        comp[CASH_LABEL] = cash
    return comp


def _slice_color(label: str, i: int) -> str:
    return "#9e9e9e" if label == CASH_LABEL else AUTO_COLORS[i % len(AUTO_COLORS)]


def _weight_bar(df: pd.DataFrame, label_col: str, order: list[str],
                colors: list[str], height: int) -> alt.Chart:
    """Horizontal % bar chart (Altair → rendered client-side, cheap to produce)."""
    return (
        alt.Chart(df).mark_bar().encode(
            x=alt.X("Weight:Q", title=None, axis=alt.Axis(format=".0f")),
            y=alt.Y(f"{label_col}:N", sort=order, title=None),
            color=alt.Color(f"{label_col}:N",
                            scale=alt.Scale(domain=order, range=colors),
                            legend=None),
            tooltip=[label_col, alt.Tooltip("Weight:Q", format=".1f")],
        ).properties(height=height)
    )


def render_composition(weights_frac: dict[str, float]) -> None:
    comp = composition_dict(weights_frac)
    if not comp:
        st.info("Add a holding and give it a weight to see the composition.")
        return

    labels = sorted(comp, key=lambda k: (k == CASH_LABEL, -comp[k]))
    colors = [_slice_color(k, i) for i, k in enumerate(labels)]
    df = pd.DataFrame({"Holding": labels,
                       "Weight": [comp[k] * 100 for k in labels]})

    pie = (
        alt.Chart(df).mark_arc(stroke="white", strokeWidth=1).encode(
            theta=alt.Theta("Weight:Q", stack=True),
            color=alt.Color("Holding:N",
                            scale=alt.Scale(domain=labels, range=colors),
                            legend=alt.Legend(title=None, orient="bottom",
                                              columns=3)),
            tooltip=["Holding", alt.Tooltip("Weight:Q", format=".1f")],
        ).properties(height=220)
    )
    st.altair_chart(pie, use_container_width=True)
    st.altair_chart(_weight_bar(df, "Holding", labels, colors,
                                max(120, 26 * len(labels))),
                    use_container_width=True)


def render_sectors(weights_frac: dict[str, float]) -> None:
    holdings = {t: w for t, w in weights_frac.items() if w > 0}
    if not holdings:
        return

    sector_w: dict[str, float] = {}
    for t, w in holdings.items():
        sec = cached_meta(t).get("sector") or "—"
        sector_w[sec] = sector_w.get(sec, 0.0) + w
    cash = max(0.0, 1.0 - sum(weights_frac.values()))
    if cash > 0.0005:
        sector_w[CASH_LABEL] = cash

    order  = sorted(sector_w, key=lambda k: (k == CASH_LABEL, -sector_w[k]))
    colors = [_slice_color(s, i) for i, s in enumerate(order)]
    df = pd.DataFrame({"Sector": order,
                       "Weight": [sector_w[s] * 100 for s in order]})
    st.altair_chart(_weight_bar(df, "Sector", order, colors,
                                max(120, 30 * len(order))),
                    use_container_width=True)


# ---------------------------------------------------------------------------
# Backtest renderer (cash-aware, single or compared portfolios)
# ---------------------------------------------------------------------------

def render_backtest(
    portfolios: dict[str, dict],
    rebalance: str,
    start: date,
    end: date,
    capital: int,
    log_scale: bool,
    colors: dict | None = None,
) -> None:
    """Download, simulate (holding not-yet-launched tickers as cash), and render
    a summary table + charts. Warns about any holding that started as cash."""
    colors = colors or {}
    all_tickers = sorted({t for w in portfolios.values() for t in w})
    try:
        prices = cached_prices(tuple(all_tickers), start.isoformat(),
                               end.isoformat())
    except Exception as exc:
        st.error(f"Data download failed: {exc}")
        return

    metrics, values_list, color_list = [], [], []
    deferred_msgs: list[str] = []
    auto_i = 0
    for name, weights in portfolios.items():
        vals, deferred = simulate_with_cash(prices, weights,
                                            rebalance=rebalance, initial=capital)
        if vals.empty:
            continue
        metrics.append(compute_metrics(vals, name=name, initial=capital))
        values_list.append(vals)
        color_list.append(colors.get(name) or AUTO_COLORS[auto_i % len(AUTO_COLORS)])
        auto_i += 1
        for t, when in deferred.items():
            if when is None:
                deferred_msgs.append(f"**{t}** (no data in range — cash throughout)")
            else:
                deferred_msgs.append(f"**{t}** (cash until {when.date()})")

    if not metrics:
        st.error("No portfolio could be simulated with the current inputs.")
        return

    if deferred_msgs:
        st.warning(
            f"⚠️ You picked a start of **{start}**, but some holdings didn't "
            "trade yet then — they're held as **cash** until they launch: "
            + ", ".join(dict.fromkeys(deferred_msgs)) + "."
        )

    # ── Summary metrics table ─────────────────────────────────────────────
    summary = pd.DataFrame([{
        "Portfolio":  m["name"],
        "CAGR":       f"{m['cagr']:+.2%}",
        "Vol (ann)":  f"{m['vol']:.2%}",
        "Sharpe":     f"{m['sharpe']:.2f}",
        "Max DD":     f"{m['max_dd']:.1%}",
        "Worst year": f"{m['worst_yr']:+.1%} ({m['worst_yr_yr']})",
        "Best year":  f"{m['best_yr']:+.1%} ({m['best_yr_yr']})",
        "Final":      f"${m['final']:,.0f}",
        "Green yrs":  m["green_years"],
    } for m in metrics]).set_index("Portfolio")
    st.dataframe(summary, width="stretch")

    # ── Growth chart ──────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(11, 3.6))
    for m, vals, color in zip(metrics, values_list, color_list):
        ax.plot(vals.index, vals.values, label=f"{m['name']}  ({m['cagr']:+.1%})",
                color=color, lw=1.6)
    if log_scale:
        ax.set_yscale("log")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(
        lambda x, _: f"${x/1e6:.1f}M" if x >= 1e6
        else (f"${x/1e3:.0f}K" if x >= 1e3 else f"${x:.0f}")))
    ax.set_title(f"Growth of ${capital:,.0f}  |  rebalance: {rebalance}")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    st.pyplot(fig, clear_figure=True)

    # ── Drawdown chart ────────────────────────────────────────────────────
    fig2, ax2 = plt.subplots(figsize=(11, 2.3))
    for m, color in zip(metrics, color_list):
        ax2.plot(m["drawdown"].index, m["drawdown"].values * 100,
                 label=m["name"], color=color, lw=1.2)
        ax2.fill_between(m["drawdown"].index, m["drawdown"].values * 100, 0,
                         alpha=0.08, color=color)
    ax2.set_title("Drawdown")
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax2.legend(fontsize=8, ncol=min(4, len(metrics)))
    ax2.grid(True, alpha=0.3)
    st.pyplot(fig2, clear_figure=True)

    # ── Annual returns table ──────────────────────────────────────────────
    annual = pd.DataFrame({m["name"]: m["annual"] for m in metrics})
    annual.index.name = "Year"
    st.subheader("Annual returns")
    st.dataframe(
        annual.style.format("{:+.2%}", na_rep="—")
              .map(lambda v: "" if pd.isna(v)
                   else ("color: #1a7f37" if v >= 0 else "color: #cf222e")),
        width="stretch",
    )


# ---------------------------------------------------------------------------
# Build & backtest sections (fragments → each re-runs on its own, so editing
# weights stays snappy and the backtest controls update live)
# ---------------------------------------------------------------------------

@st.fragment
def build_fragment() -> None:
    """Holdings editor + live composition + backtest, all in one fragment so the
    backtest always reflects the current table. Editing only re-runs this
    fragment (the search section above is untouched), so typing stays snappy."""
    st.subheader("2. Your portfolio")
    left, right = st.columns([1.1, 1], gap="large")

    with left:
        st.session_state.builder_name = st.text_input(
            "Portfolio name", value=st.session_state.builder_name,
            key="build_name")

        # Seed from the STABLE builder_df (same object across typing reruns) so
        # the editor keeps your in-progress edit instead of reverting it. The
        # return is only snapshotted to `builder`; it's never fed back as input.
        edited = st.data_editor(
            st.session_state.builder_df,
            num_rows="dynamic",
            width="stretch",
            key=f"build_editor_{st.session_state.builder_version}",
            column_config={
                "Ticker": st.column_config.TextColumn(
                    "Ticker", help="e.g. AAPL, SPY, TQQQ", required=False),
                "Weight %": st.column_config.NumberColumn(
                    "Weight %", min_value=0.0, max_value=100.0, step=0.5,
                    format="%.2f"),
            },
        )
        st.session_state.builder = edited.to_dict("records")

        rows       = clean_rows(st.session_state.builder)
        total_pct  = sum(w for _, w in rows)
        cash_pct   = max(0.0, 100.0 - total_pct)
        over_alloc = total_pct > 100.5

        if not rows:
            st.caption("Add a holding above, or search for one on the left.")
        elif over_alloc:
            st.error(f"Weights total {total_pct:.1f}% (over 100%). "
                     "Normalize or lower them.")
        elif cash_pct > 0.5:
            st.caption(f"Invested **{total_pct:.0f}%** · 💵 **{cash_pct:.0f}%** cash")
        else:
            st.caption("Fully invested 👍")

        # Remove holdings (the grid also allows row delete; these are clearer)
        if rows:
            per_row = 4
            rcols = st.columns(per_row)
            for i, (t, _) in enumerate(rows):
                rcols[i % per_row].button(f"✕ {t}", key=f"rm_{t}",
                                          width="stretch",
                                          on_click=remove_from_builder, args=(t,))

        b1, b2, b3 = st.columns(3)
        b1.button("⚖️ Normalize", disabled=total_pct <= 0, width="stretch",
                  on_click=normalize_builder)
        b2.button("🗑️ Clear", width="stretch", on_click=clear_builder)
        save_clicked = b3.button("💾 Save", type="primary",
                                 disabled=not rows or over_alloc, width="stretch")

        if save_clicked:
            name = st.session_state.builder_name.strip()
            errors = []
            if not name:
                errors.append("Give the portfolio a name before saving.")
            bad = []
            with st.spinner("Checking tickers…"):
                for t, _ in rows:
                    if not cached_valid(t):
                        bad.append(t)
            if bad:
                errors.append(f"Unknown ticker(s): {', '.join(bad)}. "
                              "Check the symbols in the search box above.")
            if errors:
                for e in errors:
                    st.error(f"❌ {e}")
            else:
                # Raw fractions; a sum below 1.0 keeps the cash slice implicit.
                weights = {t: round(w / 100, 6) for t, w in rows}
                data = load_saved()
                data[name] = {"weights": weights,
                              "rebalance": st.session_state.get("bt_rebalance",
                                                                "quarterly")}
                write_saved(data)
                st.success(f"✅ Saved **{name}** ({len(weights)} holding(s)). "
                           "See the **📂 Saved portfolios** page.")

    with right:
        if rows and not over_alloc and total_pct > 0:
            weights_frac = {t: w / 100 for t, w in rows}
            st.markdown("**Composition**")
            render_composition(weights_frac)
            st.markdown("**Sector breakdown**")
            render_sectors(weights_frac)
        else:
            st.caption("📊 Composition and sector breakdown appear here once you "
                       "add holdings with weights.")

    # ── 3. Backtest — same fragment as the editor, so the controls always
    #       reflect the table above (no stale state / missing Run button). ──
    st.divider()
    st.subheader("3. Backtest over any time range")

    if not rows or over_alloc:
        st.info("Add holdings above (weights up to 100%) to back-test them.")
        return
    bt_weights = {t: w / 100 for t, w in rows}

    # Tie the result to the current holdings: if they change, drop the stale
    # result so typing stays cheap (the user just re-runs to refresh).
    sig = tuple(sorted((t, round(w, 4)) for t, w in rows))
    if st.session_state.get("bt_sig") != sig:
        st.session_state.bt_sig = sig
        st.session_state.bt_done = False

    c1, c2, c3, c4 = st.columns(4)
    bt_start = c1.date_input("Start", key="bt_start",
                             min_value=date(1970, 1, 1), max_value=date.today())
    bt_end   = c2.date_input("End", key="bt_end",
                             min_value=date(1970, 1, 1), max_value=date.today())
    bt_capital   = c3.number_input("Capital ($)", min_value=100,
                                   step=1000, key="bt_capital")
    bt_rebalance = c4.selectbox("Rebalance", REBAL_OPTS, key="bt_rebalance")

    o1, o2, o3 = st.columns([1.4, 1, 1.2])
    bench     = o1.checkbox("Compare vs S&P 500 (SPY)", key="bt_bench")
    log_scale = o2.checkbox("Log scale", key="bt_log")
    if o3.button("▶️ Run backtest", type="primary", width="stretch"):
        st.session_state.bt_done = True

    if not st.session_state.get("bt_done"):
        st.caption("Pick a date range above, then hit **Run backtest**.")
        return
    if bt_start >= bt_end:
        st.error("Start date must be before end date.")
        return

    pname = st.session_state.builder_name.strip() or "Current build"
    portfolios = {pname: bt_weights}
    colors = {pname: AUTO_COLORS[0]}
    if bench:
        if "SPY" in bt_weights:
            st.caption("ℹ️ Your portfolio already holds SPY — no separate "
                       "benchmark line to add.")
        else:
            portfolios["S&P 500 (SPY)"] = {"SPY": 1.0}
            colors["S&P 500 (SPY)"] = "#757575"
    render_backtest(portfolios, bt_rebalance, bt_start, bt_end, bt_capital,
                    log_scale, colors)


# ---------------------------------------------------------------------------
# Sidebar — navigation only
# ---------------------------------------------------------------------------

st.sidebar.title("📈 Portfolio Backtester")
nav = st.sidebar.radio("Page", [NAV_BUILD, NAV_SAVED], key="nav")
st.sidebar.caption("Prices are pulled live from Yahoo Finance. A holding that "
                   "hadn't launched yet at the chosen start is held as cash "
                   "until its first trading day.")

# Backtest settings live in the backtest section (Page 1, step 3); seed defaults.
st.session_state.setdefault("bt_start", date(2010, 1, 1))
st.session_state.setdefault("bt_end", date.today())
st.session_state.setdefault("bt_capital", 10_000)
st.session_state.setdefault("bt_rebalance", "quarterly")
st.session_state.setdefault("bt_log", True)
st.session_state.setdefault("bt_bench", True)

saved = load_saved()


# ====================  PAGE 1 — BUILD & BACKTEST  ==========================

if nav == NAV_BUILD:
    st.title("🛠️ Build & backtest a portfolio")

    # ── 1. Find a stock ───────────────────────────────────────────────────
    st.subheader("1. Find a stock")
    c1, c2 = st.columns([2, 3])
    symbol = c1.text_input("Ticker symbol", value="",
                           placeholder="e.g. AAPL, SPY, BTC-USD",
                           key="explore_symbol").strip().upper()
    period_label = c2.radio(
        "Period", ["1M", "6M", "YTD", "1Y", "5Y", "Max", "Custom"],
        index=3, horizontal=True, key="explore_period",
    )

    period_map = {"1M": "1mo", "6M": "6mo", "1Y": "1y", "5Y": "5y", "Max": "max"}
    ov_start = ov_end = ov_period = None
    if period_label == "Custom":
        d1, d2 = st.columns(2)
        cs = d1.date_input("From", value=date(date.today().year - 1, 1, 1),
                           key="explore_from")
        ce = d2.date_input("To", value=date.today(), key="explore_to")
        ov_start, ov_end = cs.isoformat(), ce.isoformat()
    elif period_label == "YTD":
        ov_start = date(date.today().year, 1, 1).isoformat()
        ov_end   = date.today().isoformat()
    else:
        ov_period = period_map[period_label]

    if not symbol:
        st.info("Type a ticker symbol above to preview it, then add it below.")
    else:
        try:
            ov = cached_overview(symbol, ov_start, ov_end, ov_period)
        except Exception as exc:
            st.error(f"❌ {exc}")
            ov = None

        if ov:
            chart_col, info_col = st.columns([2, 1], gap="large")

            with info_col:
                st.markdown(f"**{ov['name']}** &nbsp;`{ov['symbol']}`  \n"
                            f"<small>{ov['exchange']} · {ov['sector']}</small>",
                            unsafe_allow_html=True)
                st.metric(
                    f"Price ({ov['currency']})",
                    fmt_num(ov["price"], prefix="$"),
                    f"{ov['change']:+.2f} ({ov['change_pct']:+.2%})",
                )
                dy = ov["dividend_yield"]
                dy_pct = (dy if (dy and dy > 1) else (dy or 0) * 100)
                div_str = f"{dy_pct:.2f}%" if dy else "—"
                if ov["year_low"] and ov["year_high"]:
                    range_str = f"${ov['year_low']:.0f}–${ov['year_high']:.0f}"
                else:
                    range_str = "—"
                st.caption(
                    f"Mkt cap {human_money(ov['market_cap'])} · "
                    f"P/E {fmt_num(ov['pe'])} · Div {div_str}  \n"
                    f"52-wk {range_str}"
                )
                st.button(f"➕ Add {ov['symbol']}", type="primary",
                          key="explore_add", width="stretch",
                          on_click=add_to_builder, args=(ov["symbol"],))

            with chart_col:
                hist = ov["history"]
                up = ov["change"] >= 0
                fig, axp = plt.subplots(figsize=(9, 2.6))
                line_color = "#1a7f37" if up else "#cf222e"
                axp.plot(hist.index, hist["Close"], color=line_color, lw=1.5)
                axp.fill_between(hist.index, hist["Close"], hist["Close"].min(),
                                 color=line_color, alpha=0.08)
                axp.yaxis.set_major_formatter(mticker.FuncFormatter(
                    lambda x, _: f"${x:,.0f}"))
                axp.set_title(f"{ov['symbol']} — {period_label} close", fontsize=10)
                axp.tick_params(labelsize=8)
                axp.grid(True, alpha=0.3)
                fig.tight_layout()
                st.pyplot(fig, clear_figure=True)

    st.divider()

    build_fragment()


# ====================  PAGE 2 — SAVED PORTFOLIOS  ==========================

elif nav == NAV_SAVED:
    st.title("📂 Saved portfolios")

    if not saved:
        st.info("No saved portfolios yet. Build one on the "
                "**🛠️ Build & backtest** page and hit 💾 Save.")
    else:
        names = list(saved)
        selected = st.selectbox("Pick a portfolio", names, key="saved_pick")

        entry   = saved.get(selected, {})
        weights = entry.get("weights", {})
        rb      = entry.get("rebalance") or "—"

        invested = sum(float(v) for v in weights.values())
        cash     = max(0.0, 1.0 - invested)

        st.markdown(f"### {selected}")
        st.caption(f"{len(weights)} holding(s) · rebalance: {rb}")

        # Holdings table (with sector if we can fetch it cheaply)
        with st.spinner("Loading holdings…"):
            table_rows = []
            for t, v in sorted(weights.items(), key=lambda kv: -float(kv[1])):
                meta = cached_meta(t)
                table_rows.append({
                    "Ticker": t,
                    "Name":   meta.get("name", t),
                    "Sector": meta.get("sector", "—"),
                    "Weight": f"{float(v) * 100:.1f}%",
                })
        if cash > 0.005:
            table_rows.append({"Ticker": CASH_LABEL, "Name": "—",
                               "Sector": CASH_LABEL,
                               "Weight": f"{cash * 100:.1f}%"})
        st.dataframe(pd.DataFrame(table_rows).set_index("Ticker"),
                     width="stretch")

        render_composition({t: float(v) for t, v in weights.items()})

        a1, a2 = st.columns([1, 1])
        a1.button("✏️ Load into builder", type="primary",
                  key=f"load_{selected}",
                  on_click=load_into_builder, args=(selected, saved))
        if a2.button("🗑️ Delete", key=f"del_{selected}"):
            saved.pop(selected, None)
            write_saved(saved)
            st.rerun()


st.caption("Prices are pulled live from Yahoo Finance. Holdings that hadn't "
           "launched yet at the chosen start are held as cash until they do. "
           "Idle-cash yield, taxes, and trading costs are not modeled. "
           "Not financial advice.")
