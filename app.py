"""
Streamlit UI for the portfolio backtester.

Run:
    streamlit run app.py

Reuses the engine in backtest.py (downloads, synthetic leveraged NAV,
simulation, metrics). Presets come from portfolios.json; custom portfolios
can be typed directly in the sidebar.
"""

from __future__ import annotations

import json
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
    load_config,
    simulate,
)

ROOT        = Path(__file__).parent
CONFIG_PATH = ROOT / "portfolios.json"

st.set_page_config(page_title="Portfolio Backtester", page_icon="📈",
                   layout="wide")


# ---------------------------------------------------------------------------
# Cached data download
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner="Downloading price data…")
def cached_prices(tickers: tuple[str, ...], start: str, end: str,
                  synth_json: str) -> pd.DataFrame:
    return download_prices(list(tickers), start, end, json.loads(synth_json))


# ---------------------------------------------------------------------------
# Sidebar — inputs
# ---------------------------------------------------------------------------

st.sidebar.title("Portfolio Backtester")

# Presets from portfolios.json
preset_portfolios: dict[str, dict] = {}
preset_colors:     dict[str, str]  = {}
preset_rebal:      dict[str, str]  = {}
synth_tickers:     dict[str, dict] = {}
cfg_start = cfg_end = cfg_capital = cfg_rebalance = None

if CONFIG_PATH.exists():
    (preset_portfolios, preset_colors, preset_rebal, synth_tickers,
     cfg_start, cfg_end, cfg_capital, cfg_rebalance) = load_config(str(CONFIG_PATH))

selected_presets = st.sidebar.multiselect(
    "Preset portfolios (portfolios.json)",
    options=list(preset_portfolios),
    default=list(preset_portfolios)[:2] if preset_portfolios else [],
)

st.sidebar.markdown("**Custom portfolios** — one per line:\n"
                    "`Name = TICKER:weight, TICKER:weight`")
custom_text = st.sidebar.text_area(
    "Custom portfolios",
    value="",
    placeholder="My 60/40 = SPY:0.6, TLT:0.4\nHFEA = UPRO:0.55, TMF:0.45",
    label_visibility="collapsed",
    height=90,
)

col_a, col_b = st.sidebar.columns(2)
start = col_a.date_input("Start", value=pd.Timestamp(cfg_start or "2003-01-01"),
                         min_value=date(1990, 1, 1))
end   = col_b.date_input("End", value=date.today())

capital   = st.sidebar.number_input("Initial capital ($)", min_value=100,
                                    value=int(cfg_capital or 10_000), step=1000)
rebalance = st.sidebar.selectbox(
    "Rebalance",
    ["none", "daily", "weekly", "monthly", "quarterly", "yearly"],
    index=["none", "daily", "weekly", "monthly", "quarterly", "yearly"
           ].index(cfg_rebalance or "quarterly"),
)
log_scale = st.sidebar.checkbox("Log scale growth chart", value=True)

if synth_tickers:
    st.sidebar.caption(
        "Synthetic pre-inception NAV available for: "
        + ", ".join(f"{t} ({v['L']}× {v['base']})" for t, v in synth_tickers.items())
    )


# ---------------------------------------------------------------------------
# Parse custom portfolio lines
# ---------------------------------------------------------------------------

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
    st.sidebar.error(err)

portfolios: dict[str, dict] = {
    **{n: preset_portfolios[n] for n in selected_presets},
    **custom_portfolios,
}


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

st.title("📈 Portfolio Backtester")

if not portfolios:
    st.info("Select a preset portfolio or define a custom one in the sidebar "
            "to get started.")
    st.stop()

all_tickers = sorted({t for w in portfolios.values() for t in w})

try:
    prices = cached_prices(tuple(all_tickers), start.isoformat(),
                           end.isoformat(), json.dumps(synth_tickers))
except Exception as exc:
    st.error(f"Data download failed: {exc}")
    st.stop()

all_metrics, all_values, colors = [], [], []
auto_i = 0
for name, weights in portfolios.items():
    missing = [t for t in weights
               if t not in prices.columns or prices[t].isna().all()]
    if missing:
        st.warning(f"**{name}** skipped — no data for {', '.join(missing)}")
        continue
    rb   = preset_rebal.get(name, rebalance)
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
    st.stop()

# ── Summary metrics table ────────────────────────────────────────────────
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
st.dataframe(summary, use_container_width=True)

# ── Growth chart ─────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(12, 5))
for m, vals, color in zip(all_metrics, all_values, colors):
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

# ── Drawdown chart ───────────────────────────────────────────────────────
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

# ── Annual returns table ─────────────────────────────────────────────────
annual = pd.DataFrame({m["name"]: m["annual"] for m in all_metrics})
annual.index.name = "Year"
st.subheader("Annual returns")
st.dataframe(
    annual.style.format("{:+.2%}", na_rep="—")
          .map(lambda v: "" if pd.isna(v)
               else ("color: #1a7f37" if v >= 0 else "color: #cf222e")),
    use_container_width=True,
)

st.caption("Synthetic leveraged-ETF history is modeled before each fund's "
           "inception (daily-reset leverage with volatility decay and MER). "
           "Idle-cash yield, taxes, and trading costs are not modeled here. "
           "Not financial advice.")
