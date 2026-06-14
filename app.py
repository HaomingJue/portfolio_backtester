"""
Streamlit UI for the portfolio backtester.

Run:
    streamlit run app.py

Three tabs:
  🔍 Explore   — search any ticker and view it Yahoo-Finance style
                 (price chart, volume, key stats). Add it to your build.
  🛠️ Build & Save — assemble a portfolio by giving each ticker a percentage,
                 then save it for re-use (saved_portfolios.json).
  📊 Backtest  — compare presets, saved, and custom portfolios over any period.

Reuses the engine in backtest.py (live Yahoo downloads, simulation, metrics).
All prices come from Yahoo Finance — there is no synthetic/modeled history.
Presets come from portfolios.json; saved portfolios live in saved_portfolios.json.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd
import streamlit as st

from backtest import (
    AUTO_COLORS,
    compute_metrics,
    download_prices,
    fetch_ticker_overview,
    load_config,
    simulate,
    ticker_has_data,
)

ROOT = Path(__file__).parent

# When packaged as an .exe (PyInstaller), bundled files live in a temp/_internal
# folder (ROOT). Keep user-editable files next to the executable so they persist
# and can be edited; fall back to the project folder when running from source.
DATA_DIR       = (Path(sys.executable).parent if getattr(sys, "frozen", False)
                  else ROOT)
BUNDLED_CONFIG = ROOT / "portfolios.json"        # read-only (repo / inside .exe)
CONFIG_PATH    = DATA_DIR / "portfolios.json"    # writable copy actually used
SAVED_PATH     = DATA_DIR / "saved_portfolios.json"

REBAL_OPTS = ["none", "daily", "weekly", "monthly", "quarterly", "yearly"]

# Written verbatim if no portfolios.json can be found, so the app always has a
# few presets to start from. All tickers are downloaded live from Yahoo.
DEFAULT_CONFIG = {
    "_comment": "Auto-created default. Edit freely. "
                "CLI: python backtest.py --config portfolios.json",
    "start": "2010-01-01",
    "end": None,
    "capital": 10000,
    "rebalance": "quarterly",
    "portfolios": [
        {"name": "Permanent Portfolio",
         "weights": {"SPY": 0.25, "TLT": 0.25, "GLD": 0.25, "SHY": 0.25},
         "color": "#1E88E5"},
        {"name": "S&P 500", "weights": {"SPY": 1.0},
         "rebalance": "none", "color": "#757575"},
        {"name": "60/40", "weights": {"SPY": 0.6, "TLT": 0.4},
         "color": "#43A047"},
    ],
}


def ensure_config() -> None:
    """Make sure CONFIG_PATH exists: reuse a bundled file if present, else write
    DEFAULT_CONFIG. Lets the app run even if portfolios.json was deleted or never
    shipped (e.g. only the .exe / a couple of files were sent)."""
    if CONFIG_PATH.exists():
        return
    try:
        if BUNDLED_CONFIG.exists() and BUNDLED_CONFIG.resolve() != CONFIG_PATH.resolve():
            CONFIG_PATH.write_text(BUNDLED_CONFIG.read_text())
        else:
            CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    except Exception:
        pass  # read-only location: load_config() simply falls back to no presets


ensure_config()

st.set_page_config(page_title="Portfolio Backtester", page_icon="📈",
                   layout="wide")


# ---------------------------------------------------------------------------
# Cached data access
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner="Downloading price data…")
def cached_prices(tickers: tuple[str, ...], start: str, end: str) -> pd.DataFrame:
    # All prices come straight from Yahoo — no synthetic pre-inception modeling.
    return download_prices(list(tickers), start, end)


@st.cache_data(ttl=900, show_spinner="Looking up ticker…")
def cached_overview(symbol: str, start: str | None, end: str | None,
                    period: str | None) -> dict:
    return fetch_ticker_overview(symbol, start=start, end=end, period=period)


@st.cache_data(ttl=900, show_spinner=False)
def cached_valid(symbol: str) -> bool:
    return ticker_has_data(symbol)


# ---------------------------------------------------------------------------
# Saved-portfolio persistence
# ---------------------------------------------------------------------------

def load_saved() -> dict:
    """Return {name: {"weights": {...}, "rebalance": str|None}} from disk."""
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
# Engine wrapper used by both the Build and Backtest tabs
# ---------------------------------------------------------------------------

def run_backtest(
    portfolios: dict[str, dict],
    rebal_overrides: dict[str, str],
    default_rebalance: str,
    start: date,
    end: date,
    capital: int,
    preset_colors: dict,
    log_scale: bool,
) -> None:
    """Download, simulate, and render summary table + charts for `portfolios`."""
    all_tickers = sorted({t for w in portfolios.values() for t in w})
    try:
        prices = cached_prices(tuple(all_tickers), start.isoformat(),
                               end.isoformat())
    except Exception as exc:
        st.error(f"Data download failed: {exc}")
        return

    all_metrics, all_values, colors = [], [], []
    auto_i = 0
    for name, weights in portfolios.items():
        missing = [t for t in weights
                   if t not in prices.columns or prices[t].isna().all()]
        if missing:
            st.warning(f"**{name}** skipped — no data for {', '.join(missing)} "
                       "(check the symbol, or widen the date range).")
            continue
        rb   = rebal_overrides.get(name, default_rebalance)
        vals = simulate(prices, weights, rebalance=rb, initial=capital)
        if vals.empty:
            continue
        all_metrics.append(compute_metrics(vals, name=name, initial=capital))
        all_values.append(vals)
        if name in preset_colors:
            colors.append(preset_colors[name])
        else:
            colors.append(AUTO_COLORS[auto_i % len(AUTO_COLORS)])
            auto_i += 1

    if not all_metrics:
        st.error("No portfolio could be simulated with the current inputs.")
        return

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
    } for m in all_metrics]).set_index("Portfolio")
    st.dataframe(summary, width="stretch")

    # ── Growth chart ──────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(12, 5))
    for m, vals, color in zip(all_metrics, all_values, colors):
        ax.plot(vals.index, vals.values, label=f"{m['name']}  ({m['cagr']:+.1%})",
                color=color, lw=1.6)
    if log_scale:
        ax.set_yscale("log")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(
        lambda x, _: f"${x/1e6:.1f}M" if x >= 1e6
        else (f"${x/1e3:.0f}K" if x >= 1e3 else f"${x:.0f}")))
    ax.set_title(f"Growth of ${capital:,.0f}  |  rebalance: {default_rebalance}")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    st.pyplot(fig, clear_figure=True)

    # ── Drawdown chart ────────────────────────────────────────────────────
    fig2, ax2 = plt.subplots(figsize=(12, 3.5))
    for m, color in zip(all_metrics, colors):
        ax2.plot(m["drawdown"].index, m["drawdown"].values * 100,
                 label=m["name"], color=color, lw=1.2)
        ax2.fill_between(m["drawdown"].index, m["drawdown"].values * 100, 0,
                         alpha=0.08, color=color)
    ax2.set_title("Drawdown")
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax2.legend(fontsize=8, ncol=min(4, len(all_metrics)))
    ax2.grid(True, alpha=0.3)
    st.pyplot(fig2, clear_figure=True)

    # ── Annual returns table ──────────────────────────────────────────────
    annual = pd.DataFrame({m["name"]: m["annual"] for m in all_metrics})
    annual.index.name = "Year"
    st.subheader("Annual returns")
    st.dataframe(
        annual.style.format("{:+.2%}", na_rep="—")
              .map(lambda v: "" if pd.isna(v)
                   else ("color: #1a7f37" if v >= 0 else "color: #cf222e")),
        width="stretch",
    )


# ---------------------------------------------------------------------------
# Load config (presets + defaults). All prices come from Yahoo live.
# ---------------------------------------------------------------------------

preset_portfolios: dict[str, dict] = {}
preset_colors:     dict[str, str]  = {}
preset_rebal:      dict[str, str]  = {}
cfg_start = cfg_end = cfg_capital = cfg_rebalance = None

if CONFIG_PATH.exists():
    (preset_portfolios, preset_colors, preset_rebal, _synth,
     cfg_start, cfg_end, cfg_capital, cfg_rebalance) = load_config(str(CONFIG_PATH))

saved = load_saved()
saved_portfolios = {n: v.get("weights", {}) for n, v in saved.items()}
saved_rebal      = {n: v.get("rebalance") for n, v in saved.items()
                    if v.get("rebalance")}


# ---------------------------------------------------------------------------
# Sidebar — global backtest settings
# ---------------------------------------------------------------------------

st.sidebar.title("⚙️ Backtest settings")
st.sidebar.caption("Used by the **Build & Save** and **Backtest** tabs.")

col_a, col_b = st.sidebar.columns(2)
start = col_a.date_input("Start", value=pd.Timestamp(cfg_start or "2003-01-01"),
                         min_value=date(1990, 1, 1))
end   = col_b.date_input("End", value=date.today())

capital   = st.sidebar.number_input("Initial capital ($)", min_value=100,
                                    value=int(cfg_capital or 10_000), step=1000)
rebalance = st.sidebar.selectbox(
    "Rebalance", REBAL_OPTS,
    index=REBAL_OPTS.index(cfg_rebalance or "quarterly"),
)
log_scale = st.sidebar.checkbox("Log scale growth chart", value=True)

st.sidebar.caption("All prices are pulled live from Yahoo Finance. A backtest "
                   "starts on the latest inception date among its holdings.")


# ---------------------------------------------------------------------------
# Session state for the portfolio builder
# ---------------------------------------------------------------------------

if "builder" not in st.session_state:
    st.session_state.builder = [{"Ticker": "", "Weight %": 0.0}]
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
    """Replace builder contents and force the editor to re-seed."""
    st.session_state.builder = rows or [{"Ticker": "", "Weight %": 0.0}]
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


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

st.title("📈 Portfolio Backtester")
tab_explore, tab_build, tab_backtest = st.tabs(
    ["🔍 Explore ticker", "🛠️ Build & Save", "📊 Backtest"]
)


# ====================  TAB 1 — EXPLORE  ====================================

with tab_explore:
    st.subheader("Search any ticker")
    c1, c2 = st.columns([2, 3])
    symbol = c1.text_input("Ticker symbol", value="AAPL",
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
        st.info("Type a ticker symbol above to see its chart and stats.")
    else:
        try:
            ov = cached_overview(symbol, ov_start, ov_end, ov_period)
        except Exception as exc:
            st.error(f"❌ {exc}")
            ov = None

        if ov:
            up = ov["change"] >= 0
            head_l, head_r = st.columns([3, 1])
            head_l.markdown(
                f"### {ov['name']}  ·  `{ov['symbol']}`\n"
                f"{ov['exchange']} · {ov['sector']} / {ov['industry']}"
            )
            head_r.metric(
                f"Price ({ov['currency']})",
                fmt_num(ov["price"], prefix="$"),
                f"{ov['change']:+.2f} ({ov['change_pct']:+.2%})",
            )

            s1, s2, s3, s4 = st.columns(4)
            s1.metric("Market cap", human_money(ov["market_cap"]))
            s2.metric("P/E (ttm)",  fmt_num(ov["pe"]))
            dy = ov["dividend_yield"]
            dy_pct = (dy if (dy and dy > 1) else (dy or 0) * 100)
            s3.metric("Dividend yield", f"{dy_pct:.2f}%" if dy else "—")
            s4.metric("Volume", human_money(ov["volume"]).lstrip("$") if ov["volume"] else "—")

            r1, r2, r3 = st.columns(3)
            r1.metric("52-wk high", fmt_num(ov["year_high"], prefix="$"))
            r2.metric("52-wk low",  fmt_num(ov["year_low"],  prefix="$"))
            rng = ""
            if ov["day_low"] and ov["day_high"]:
                rng = f"${ov['day_low']:.2f} – ${ov['day_high']:.2f}"
            r3.metric("Day range", rng or "—")

            # Price + volume chart
            hist = ov["history"]
            fig, (axp, axv) = plt.subplots(
                2, 1, figsize=(12, 6), sharex=True,
                gridspec_kw={"height_ratios": [3, 1], "hspace": 0.05},
            )
            line_color = "#1a7f37" if up else "#cf222e"
            axp.plot(hist.index, hist["Close"], color=line_color, lw=1.5)
            axp.fill_between(hist.index, hist["Close"], hist["Close"].min(),
                             color=line_color, alpha=0.08)
            axp.yaxis.set_major_formatter(mticker.FuncFormatter(
                lambda x, _: f"${x:,.0f}"))
            axp.set_title(f"{ov['symbol']} — {period_label} close", fontsize=11)
            axp.grid(True, alpha=0.3)
            if "Volume" in hist:
                axv.bar(hist.index, hist["Volume"], color="#8b949e", width=1.0)
                axv.yaxis.set_major_formatter(mticker.FuncFormatter(
                    lambda x, _: f"{x/1e6:.0f}M" if x >= 1e6 else f"{x/1e3:.0f}K"))
            axv.set_ylabel("Vol")
            axv.grid(True, alpha=0.3)
            st.pyplot(fig, clear_figure=True)

            ac1, ac2 = st.columns([1, 3])
            if ac1.button("➕ Add to portfolio", type="primary",
                          key="explore_add"):
                add_to_builder(ov["symbol"])
                ac2.success(f"Added **{ov['symbol']}** — set its weight in "
                            "the **Build & Save** tab.")

            if ov["summary"]:
                with st.expander("About"):
                    st.write(ov["summary"])


# ====================  TAB 2 — BUILD & SAVE  ===============================

with tab_build:
    st.subheader("Build a portfolio")
    st.caption("Give each ticker a percentage. Weights should add up to 100%.")

    st.session_state.builder_name = st.text_input(
        "Portfolio name", value=st.session_state.builder_name, key="build_name")

    edited = st.data_editor(
        pd.DataFrame(st.session_state.builder),
        num_rows="dynamic",
        width="stretch",
        key=f"build_editor_{st.session_state.builder_version}",
        column_config={
            "Ticker": st.column_config.TextColumn(
                "Ticker", help="e.g. AAPL, SPY, TQQQ", required=False),
            "Weight %": st.column_config.NumberColumn(
                "Weight %", min_value=0.0, max_value=100.0, step=1.0,
                format="%.1f"),
        },
    )
    st.session_state.builder = edited.to_dict("records")

    # Parse current rows (blank- and NaN-safe)
    rows = clean_rows(st.session_state.builder)
    total_pct = sum(w for _, w in rows)

    mcol1, mcol2 = st.columns([1, 3])
    mcol1.metric("Total weight", f"{total_pct:.1f}%")
    if rows and abs(total_pct - 100) > 0.5:
        mcol2.warning("Weights don't add up to 100%. Use **Normalize** or adjust.")
    elif rows:
        mcol2.success("Weights add up to 100%. 👍")

    b1, b2, b3 = st.columns(3)

    if b1.button("⚖️ Normalize to 100%", disabled=total_pct <= 0):
        set_builder([{"Ticker": t, "Weight %": round(w / total_pct * 100, 2)}
                     for t, w in rows])
        st.rerun()

    if b2.button("🗑️ Clear"):
        set_builder([{"Ticker": "", "Weight %": 0.0}])
        st.rerun()

    save_clicked = b3.button("💾 Save portfolio", type="primary",
                             disabled=not rows)

    if save_clicked:
        name = st.session_state.builder_name.strip()
        errors = []
        if not name:
            errors.append("Give the portfolio a name.")
        if abs(total_pct - 100) > 0.5:
            errors.append(f"Weights add up to {total_pct:.1f}%, not 100%. "
                          "Normalize or fix them first.")
        # Validate every ticker actually exists on Yahoo
        bad = []
        with st.spinner("Checking tickers…"):
            for t, _ in rows:
                if not cached_valid(t):
                    bad.append(t)
        if bad:
            errors.append(f"Unknown ticker(s): {', '.join(bad)}. "
                          "Check the symbols in the Explore tab.")

        if errors:
            for e in errors:
                st.error(f"❌ {e}")
        else:
            weights = {t: round(w / total_pct, 6) for t, w in rows}
            saved[name] = {"weights": weights, "rebalance": rebalance}
            write_saved(saved)
            st.success(f"✅ Saved **{name}** with {len(weights)} holdings to "
                       "`saved_portfolios.json`. It's now available in the "
                       "**Backtest** tab.")
            st.rerun()

    # Quick one-click backtest of the current build
    if rows and abs(total_pct - 100) <= 0.5:
        with st.expander("📊 Backtest this portfolio now", expanded=False):
            weights = {t: w / total_pct for t, w in rows}
            run_backtest(
                {st.session_state.builder_name or "Current build": weights},
                rebal_overrides={}, default_rebalance=rebalance,
                start=start, end=end, capital=capital,
                preset_colors={}, log_scale=log_scale,
            )

    # Manage saved portfolios
    if saved:
        st.divider()
        st.subheader("Saved portfolios")
        for name in list(saved):
            w = saved[name].get("weights", {})
            holding = ", ".join(f"{t} {v:.0%}" for t, v in w.items())
            cc1, cc2 = st.columns([5, 1])
            cc1.markdown(f"**{name}** — {holding}")
            if cc2.button("Delete", key=f"del_{name}"):
                saved.pop(name, None)
                write_saved(saved)
                st.rerun()


# ====================  TAB 3 — BACKTEST  ===================================

with tab_backtest:
    st.subheader("Compare portfolios")

    selected_presets = st.multiselect(
        "Preset portfolios (portfolios.json)",
        options=list(preset_portfolios),
        default=list(preset_portfolios)[:2] if preset_portfolios else [],
    )
    selected_saved = st.multiselect(
        "Saved portfolios (saved_portfolios.json)",
        options=list(saved_portfolios),
        default=list(saved_portfolios),
    )

    with st.expander("➕ Quick custom portfolios (one per line)"):
        st.caption("`Name = TICKER:weight, TICKER:weight`")
        custom_text = st.text_area(
            "Custom portfolios",
            value="",
            placeholder="My 60/40 = SPY:0.6, TLT:0.4\nHFEA = UPRO:0.55, TMF:0.45",
            label_visibility="collapsed",
            height=90,
        )

    def parse_custom(text: str) -> tuple[dict[str, dict], list[str]]:
        """Parse 'Name = TICKER:w, TICKER:w' lines. Returns (portfolios, errors)."""
        out, errors = {}, []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            name, _, spec = line.partition("=")
            if not spec:
                spec, name = name, f"Custom {len(out) + 1}"
            name = name.strip()
            weights = {}
            try:
                for part in spec.replace(",", " ").split():
                    tk, _, w = part.partition(":")
                    weights[tk.strip().upper()] = float(w)
            except ValueError:
                errors.append(f"Could not parse line: `{raw_line}`")
                continue
            total = sum(weights.values())
            if not weights or abs(total - 1.0) > 0.02:
                errors.append(f"`{name}`: weights sum to {total:.2f}, must be ~1.0")
                continue
            out[name] = weights
        return out, errors

    custom_portfolios, parse_errors = parse_custom(custom_text)
    for err in parse_errors:
        st.error(err)

    portfolios: dict[str, dict] = {
        **{n: preset_portfolios[n] for n in selected_presets},
        **{n: saved_portfolios[n]  for n in selected_saved},
        **custom_portfolios,
    }
    rebal_overrides = {**preset_rebal, **saved_rebal}

    if not portfolios:
        st.info("Pick a preset, a saved portfolio, or type a custom one above "
                "to run a backtest.")
    else:
        run_backtest(
            portfolios, rebal_overrides, rebalance,
            start, end, capital, preset_colors, log_scale,
        )

st.caption("Prices are pulled live from Yahoo Finance; each backtest begins on "
           "the latest inception date among its holdings. Idle-cash yield, taxes, "
           "and trading costs are not modeled here. Not financial advice.")
