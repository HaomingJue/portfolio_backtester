#!/usr/bin/env python3
"""
backtest.py — general-purpose multi-asset portfolio backtester

Two modes:

  Single-portfolio mode (--portfolio):
    Compare one portfolio against benchmark tickers.
    Note: synthetic leveraged ETF NAV requires --config mode.

    python backtest.py --portfolio SPY:0.60 TLT:0.40
    python backtest.py --portfolio SPY:0.25 TLT:0.25 GLD:0.25 SHY:0.25 --start 2007-01-01
    python backtest.py --portfolio VTI:0.30 TLT:0.40 IEI:0.15 GLD:0.075 DJP:0.075 \\
        --rebalance quarterly --start 2007-01-01 --name "All Weather"

  Config mode (--config):
    Compare any number of named portfolios defined in a JSON file.
    Define synthetic leveraged ETFs in the "instruments" section.

    python backtest.py --config portfolios.json
    python backtest.py --config my_custom.json --start 2015-01-01

    JSON format:
      {
        "start": "2003-01-01",      // optional, overridden by --start
        "end":   "2024-12-31",      // optional, overridden by --end
        "capital": 10000,           // optional, overridden by --initial
        "rebalance": "quarterly",   // optional, overridden by --rebalance

        "instruments": [
          { "ticker": "SSO",  "base": "SPY", "leverage": 2, "mer": 0.0089 },
          { "ticker": "TMF",  "base": "TLT", "leverage": 3, "mer": 0.0093 }
        ],

        "portfolios": [
          { "name": "HFEA", "weights": {"SSO": 0.55, "TMF": 0.45}, "color": "crimson" },
          { "name": "SPY B&H", "weights": {"SPY": 1.0}, "color": "gray" }
        ]
      }

    Each entry in "instruments" tells the backtester to build synthetic pre-inception
    NAV for that ticker using a daily-reset model:
      daily_return = L * base_return - 0.5*(L^2 - L)*variance_20d - MER/252
    Real prices are used from the ETF's first trading day onward; synthetic data
    fills in every day before that, going all the way back to the base ETF's history.

Arguments:
  --portfolio   TICKER:WEIGHT pairs (weights must sum to ~1.0)
  --config      JSON file path defining multiple portfolios
  --start       Start date YYYY-MM-DD  (default: 2003-01-01)
  --end         End date   YYYY-MM-DD  (default: today)
  --rebalance   none | daily | weekly | monthly | quarterly | yearly (default: monthly)
  --initial     Starting value in USD  (default: 10000)
  --benchmark   Extra tickers to compare against (default: SPY QQQ) [--portfolio mode only]
  --name        Label for the portfolio (default: "Portfolio") [--portfolio mode only]
  --no-plot     Skip chart generation
  --out         Output folder for charts (default: ./output)
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import date
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")


# ---------------------------------------------------------------------------
# Synthetic leveraged ETF NAV
# ---------------------------------------------------------------------------

AUTO_COLORS = [
    "#2196F3", "#FF5722", "#4CAF50", "#FF9800", "#9C27B0",
    "#009688", "#E91E63", "#607D8B", "#795548", "#F44336",
    "#3F51B5", "#00BCD4",
]


def _synthetic_lev(base: pd.Series, real: pd.Series | None, L: int, annual_mer: float) -> pd.Series:
    ret       = base.pct_change().fillna(0)
    var20     = ret.rolling(20).var().fillna(0)
    daily_mer = annual_mer / 252.0

    first_real = None
    if real is not None and not real.dropna().empty:
        common = base.index.intersection(real.dropna().index)
        if not common.empty:
            first_real = base.index.get_loc(common[0])

    nav = np.ones(len(base))
    for i in range(1, len(base)):
        r = L * ret.values[i] - 0.5 * (L**2 - L) * var20.values[i]
        if first_real is None or i < first_real:
            r -= daily_mer
        nav[i] = nav[i - 1] * (1.0 + r)

    synth = pd.Series(nav, index=base.index, name=base.name)
    if first_real is None:
        return synth

    anchor   = base.index[first_real]
    stitched = synth.copy()
    real_aln = real.reindex(base.index)
    stitched.loc[anchor:] = real_aln.loc[anchor:] * (synth.loc[anchor] / real.loc[anchor])
    return stitched


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def _dl(ticker: str, start: str, end: str) -> pd.Series:
    try:
        s = yf.download(ticker, start=start, end=end,
                        auto_adjust=True, progress=False)["Close"].squeeze().dropna()
        s.name = ticker
        return s
    except Exception:
        return pd.Series(dtype=float, name=ticker)


def download_prices(
    tickers: list[str],
    start: str,
    end: str,
    synth_tickers: dict | None = None,
) -> pd.DataFrame:
    """Download price data, building synthetic pre-inception NAV for any ticker
    defined in synth_tickers (parsed from the JSON "instruments" section).

    synth_tickers format: {"SSO": {"base": "SPY", "L": 2, "mer": 0.0089}, ...}
    """
    if synth_tickers is None:
        synth_tickers = {}

    all_tickers  = sorted(set(tickers))
    synth_set    = {t for t in all_tickers if t in synth_tickers}
    base_needed  = {synth_tickers[t]["base"] for t in synth_set}
    direct_set   = (set(all_tickers) - synth_set) | base_needed

    # Pull extra history for the 20-day rolling variance used in synthetic model
    dl_start = (pd.Timestamp(start) - pd.DateOffset(months=3)).strftime("%Y-%m-%d")

    print(f"Downloading {', '.join(sorted(direct_set))} …")
    raw_series = {t: _dl(t, dl_start, end) for t in sorted(direct_set)}

    for tk in sorted(synth_set):
        spec = synth_tickers[tk]
        base = raw_series.get(spec["base"])
        if base is not None and not base.empty:
            real = _dl(tk, dl_start, end)
            raw_series[tk] = _synthetic_lev(base, real, spec["L"], spec["mer"])
            real_start = real.dropna().index[0].strftime("%Y-%m-%d") if not real.dropna().empty else "no real data"
            print(f"  Built synthetic NAV for {tk} (real data from {real_start})")
        else:
            print(f"  WARNING: base {spec['base']} unavailable — cannot build {tk}")

    df = pd.DataFrame(raw_series)
    df = df[df.index >= start].ffill()

    missing = [t for t in tickers if t not in df.columns or df[t].isna().all()]
    if missing:
        raise ValueError(f"No price data for: {', '.join(missing)}")
    return df


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------

def load_config(path: str) -> tuple:
    """Return (portfolios_dict, colors_dict, rebalance_map, synth_tickers, start, end, capital, rebalance).

    synth_tickers: dict built from the JSON "instruments" array, keyed by ticker symbol.
      e.g. {"SSO": {"base": "SPY", "L": 2, "mer": 0.0089}}

    rebalance_map: per-portfolio overrides, e.g. {"SPY B&H": "none"}.
    Falls back to the top-level "rebalance" value (or CLI flag) for portfolios
    that don't specify one.
    """
    data = json.loads(Path(path).read_text())
    portfolios:    dict[str, dict] = {}
    colors:        dict[str, str]  = {}
    rebalance_map: dict[str, str]  = {}
    synth_tickers: dict[str, dict] = {}

    # Parse instruments.
    # - Has base + leverage + mer  →  synthetic pre-inception NAV is built.
    # - Has only mer (no base/leverage)  →  downloaded directly from Yahoo; mer is informational.
    # - Not listed at all  →  downloaded directly from Yahoo.
    for inst in data.get("instruments", []):
        tk = inst["ticker"].upper()
        if "base" in inst and "leverage" in inst and "mer" in inst:
            synth_tickers[tk] = {
                "base": inst["base"].upper(),
                "L":    inst["leverage"],
                "mer":  inst["mer"],
            }

    # Parse portfolios
    entries = data.get("portfolios", {})
    if isinstance(entries, dict):
        portfolios = dict(entries)
    else:
        for entry in entries:
            name = entry["name"]
            portfolios[name] = entry["weights"]
            if "color" in entry:
                colors[name] = entry["color"]
            if "rebalance" in entry:
                rebalance_map[name] = entry["rebalance"]

    color_idx = len(colors)
    for name in portfolios:
        if name not in colors:
            colors[name] = AUTO_COLORS[color_idx % len(AUTO_COLORS)]
            color_idx += 1

    return (
        portfolios,
        colors,
        rebalance_map,
        synth_tickers,
        data.get("start"),
        data.get("end"),
        data.get("capital"),
        data.get("rebalance"),
    )


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def _period_first_dates(index: pd.DatetimeIndex, freq: str) -> set:
    if freq == "none":
        return {index[0]}
    if freq == "daily":
        return set(index)
    df = pd.DataFrame({"dt": index})
    period_map = {
        "weekly":    df["dt"].dt.isocalendar().week.astype(str) + "-" + df["dt"].dt.year.astype(str),
        "monthly":   df["dt"].dt.to_period("M").astype(str),
        "quarterly": df["dt"].dt.to_period("Q").astype(str),
        "yearly":    df["dt"].dt.year.astype(str),
    }
    df["period"] = period_map[freq]
    return set(df.groupby("period")["dt"].first())


def simulate(
    prices: pd.DataFrame,
    weights: dict[str, float],
    rebalance: str = "monthly",
    initial: float = 10_000,
) -> pd.Series:
    tickers = list(weights.keys())
    w = np.array([weights[t] for t in tickers])
    p = prices[tickers].dropna()

    rebal_dates = _period_first_dates(p.index, rebalance)
    daily_ret   = p.pct_change().fillna(0)
    holding     = w * initial
    values      = []

    for i, dt in enumerate(p.index):
        if i > 0:
            holding = holding * (1 + daily_ret.iloc[i][tickers].values)
        total = holding.sum()
        values.append(total)
        if dt in rebal_dates and i > 0:
            holding = w * total

    return pd.Series(values, index=p.index, name="value")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(values: pd.Series, name: str = "Portfolio", initial: float = None) -> dict:
    if initial is None:
        initial = values.iloc[0]

    start, end = values.index[0], values.index[-1]
    years = (end - start).days / 365.25
    final = values.iloc[-1]

    cagr    = (final / initial) ** (1 / years) - 1
    peak    = values.cummax()
    dd      = (values - peak) / peak
    max_dd  = dd.min()
    max_dd_end   = dd.idxmin()
    max_dd_start = values.loc[:max_dd_end].idxmax()

    daily_ret = values.pct_change().dropna()
    vol       = daily_ret.std() * np.sqrt(252)
    sharpe    = (cagr - 0.04) / vol if vol > 0 else 0.0
    calmar    = cagr / abs(max_dd) if max_dd != 0 else float("inf")

    annual = {}
    for yr in sorted(set(values.index.year)):
        yr_vals = values[values.index.year == yr]
        if yr_vals.empty:
            continue
        prev      = values[values.index < yr_vals.index[0]]
        start_val = prev.iloc[-1] if not prev.empty else initial
        annual[yr] = yr_vals.iloc[-1] / start_val - 1

    annual_s    = pd.Series(annual)
    worst_yr    = annual_s.min()
    worst_yr_yr = int(annual_s.idxmin())
    best_yr     = annual_s.max()
    best_yr_yr  = int(annual_s.idxmax())
    green_n     = (annual_s > 0).sum()
    total_n     = len(annual_s)
    green_years = f"{green_n}/{total_n} ({green_n/total_n*100:.0f}%)"

    return {
        "name":          name,
        "start":         start.strftime("%Y-%m-%d"),
        "end":           end.strftime("%Y-%m-%d"),
        "years":         round(years, 1),
        "initial":       initial,
        "final":         final,
        "cagr":          cagr,
        "vol":           vol,
        "sharpe":        sharpe,
        "calmar":        calmar,
        "max_dd":        max_dd,
        "max_dd_start":  max_dd_start.strftime("%Y-%m-%d"),
        "max_dd_end":    max_dd_end.strftime("%Y-%m-%d"),
        "worst_yr":      worst_yr,
        "worst_yr_yr":   worst_yr_yr,
        "best_yr":       best_yr,
        "best_yr_yr":    best_yr_yr,
        "green_years":   green_years,
        "annual":        annual_s,
        "drawdown":      dd,
    }


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

COLS = [
    ("CAGR",        "cagr",        "{:+.2%}"),
    ("Vol (ann)",   "vol",         "{:.2%}"),
    ("Sharpe",      "sharpe",      "{:.2f}"),
    ("Calmar",      "calmar",      "{:.2f}"),
    ("Max DD",      "max_dd",      "{:.2%}"),
    ("Worst Year",  "worst_yr",    "{:+.2%}"),
    ("Worst Yr",    "worst_yr_yr", "{:d}"),
    ("Best Year",   "best_yr",     "{:+.2%}"),
    ("Best Yr",     "best_yr_yr",  "{:d}"),
    ("Final Value", "final",       "${:,.0f}"),
    ("Green Years", "green_years", "{}"),
]


def print_summary(all_metrics: list[dict]) -> None:
    headers = ["Metric"] + [m["name"] for m in all_metrics]
    rows = []
    for label, key, fmt in COLS:
        row = [label]
        for m in all_metrics:
            v = m[key]
            if key == "calmar" and v == float("inf"):
                row.append("∞")
            else:
                try:
                    row.append(fmt.format(v))
                except (ValueError, TypeError):
                    row.append(str(v))
        rows.append(row)

    rows.append(["Period"]     + [f"{m['start']} to {m['end']}" for m in all_metrics])
    rows.append(["Years"]      + [f"{m['years']}" for m in all_metrics])
    rows.append(["Max DD period"] + [f"{m['max_dd_start']} -> {m['max_dd_end']}" for m in all_metrics])

    col_widths = [max(len(str(r[i])) for r in [headers] + rows) for i in range(len(headers))]
    sep     = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    fmt_row = lambda r: "|" + "|".join(f" {str(v):<{w}} " for v, w in zip(r, col_widths)) + "|"

    print()
    print(sep)
    print(fmt_row(headers))
    print(sep.replace("-", "="))
    for row in rows:
        print(fmt_row(row))
    print(sep)
    print()


def print_annual_table(all_metrics: list[dict]) -> None:
    all_years = sorted(set(y for m in all_metrics for y in m["annual"].index))
    if not all_years:
        return

    headers   = ["Year"] + [m["name"] for m in all_metrics]
    col_widths = [max(4, max(len(str(h)) for h in headers))] + [
        max(8, len(m["name"])) for m in all_metrics
    ]
    sep     = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    fmt_row = lambda r: "|" + "|".join(f" {str(v):>{w}} " for v, w in zip(r, col_widths)) + "|"

    print("Annual Returns:")
    print(sep)
    print(fmt_row(headers))
    print(sep.replace("-", "="))
    for yr in all_years:
        row = [str(yr)]
        for m in all_metrics:
            v = m["annual"].get(yr)
            row.append(f"{v:+.2%}" if v is not None else "   —  ")
        print(fmt_row(row))
    print(sep)
    print()


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _dollar_fmt(x, _):
    if x >= 1_000_000:
        return f"${x/1_000_000:.1f}M"
    if x >= 1_000:
        return f"${x/1_000:.0f}K"
    return f"${x:.0f}"


def plot_all(
    all_metrics:  list[dict],
    all_values:   list[pd.Series],
    colors:       list[str],
    rebalance:    str,
    initial:      float,
    out_path:     Path,
) -> None:
    fig = plt.figure(figsize=(16, 14))
    fig.patch.set_facecolor("#0d1117")

    gs = fig.add_gridspec(
        3, 2,
        height_ratios=[3, 1.5, 1.5],
        hspace=0.45, wspace=0.35,
        left=0.07, right=0.97, top=0.93, bottom=0.06,
    )

    ax_growth = fig.add_subplot(gs[0, :])
    ax_dd     = fig.add_subplot(gs[1, :])
    ax_annual = fig.add_subplot(gs[2, 0])
    ax_stats  = fig.add_subplot(gs[2, 1])

    for ax in [ax_growth, ax_dd, ax_annual, ax_stats]:
        ax.set_facecolor("#161b22")
        ax.tick_params(colors="#c9d1d9", labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor("#30363d")

    # ── 1. Growth chart ──────────────────────────────────────────────────
    for i, (m, vals) in enumerate(zip(all_metrics, all_values)):
        color = colors[i]
        lw = 2.5 if i == 0 else 1.5
        ax_growth.plot(vals.index, vals.values, color=color, lw=lw,
                       label=m["name"], zorder=10 - i)

    ax_growth.set_yscale("log")
    ax_growth.yaxis.set_major_formatter(mticker.FuncFormatter(_dollar_fmt))
    ax_growth.yaxis.set_minor_formatter(mticker.NullFormatter())
    ax_growth.set_title(
        f"Growth of ${initial:,.0f}  |  rebalance: {rebalance}  |  "
        f"{all_metrics[0]['start']} – {all_metrics[0]['end']}",
        color="#c9d1d9", fontsize=12, pad=8,
    )
    ax_growth.legend(
        facecolor="#161b22", edgecolor="#30363d",
        labelcolor="#c9d1d9", fontsize=8, ncol=min(4, len(all_metrics)),
    )
    ax_growth.grid(True, color="#21262d", lw=0.5, which="major")
    ax_growth.grid(True, color="#161b22", lw=0.3, which="minor")

    # ── 2. Drawdown chart ────────────────────────────────────────────────
    for i, (m, color) in enumerate(zip(all_metrics, colors)):
        lw = 2 if i == 0 else 1
        ax_dd.fill_between(m["drawdown"].index, m["drawdown"].values * 100,
                           alpha=0.25 if i == 0 else 0.1, color=color)
        ax_dd.plot(m["drawdown"].index, m["drawdown"].values * 100,
                   color=color, lw=lw, label=m["name"])

    ax_dd.set_title("Drawdown (%)", color="#c9d1d9", fontsize=10, pad=6)
    ax_dd.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax_dd.legend(facecolor="#161b22", edgecolor="#30363d",
                 labelcolor="#c9d1d9", fontsize=7, ncol=min(4, len(all_metrics)))
    ax_dd.grid(True, color="#21262d", lw=0.5)

    # ── 3. Annual returns bar chart (first portfolio) ─────────────────────
    ann        = all_metrics[0]["annual"]
    years      = [int(y) for y in ann.index]
    bar_colors = ["#4CAF50" if v >= 0 else "#F44336" for v in ann.values]
    ax_annual.bar(years, ann.values * 100, color=bar_colors, edgecolor="none", width=0.7)
    ax_annual.axhline(0, color="#555", lw=0.8)
    ax_annual.set_title(f"{all_metrics[0]['name']} — Annual Returns",
                        color="#c9d1d9", fontsize=10, pad=6)
    ax_annual.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax_annual.tick_params(axis="x", labelrotation=45, labelsize=7)
    ax_annual.grid(True, color="#21262d", lw=0.5, axis="y")

    # ── 4. Stats comparison table ─────────────────────────────────────────
    ax_stats.axis("off")
    stat_rows = [
        ("CAGR",     lambda m: f"{m['cagr']:+.1%}"),
        ("Max DD",   lambda m: f"{m['max_dd']:.1%}"),
        ("Worst Yr", lambda m: f"{m['worst_yr']:+.1%} ({m['worst_yr_yr']})"),
        ("Best Yr",  lambda m: f"{m['best_yr']:+.1%} ({m['best_yr_yr']})"),
        ("Sharpe",   lambda m: f"{m['sharpe']:.2f}"),
        ("Calmar",   lambda m: f"{m['calmar']:.2f}" if m["calmar"] != float("inf") else "∞"),
        ("Final",    lambda m: f"${m['final']:,.0f}"),
    ]
    col_labels = ["Metric"] + [m["name"][:12] for m in all_metrics]
    table_data = [[rname] + [fn(m) for m in all_metrics] for rname, fn in stat_rows]

    table = ax_stats.table(
        cellText=table_data, colLabels=col_labels, loc="center", cellLoc="right",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    for (r, c), cell in table.get_celld().items():
        cell.set_facecolor("#1c2128" if r % 2 == 0 else "#161b22")
        cell.set_edgecolor("#30363d")
        cell.set_text_props(color="#c9d1d9")
        if r == 0:
            cell.set_facecolor("#21262d")
            cell.set_text_props(color="#58a6ff", fontweight="bold")
        if c == 1 and r > 0:
            cell.set_facecolor("#1f2d1f" if r % 2 == 0 else "#192119")

    ax_stats.set_title("Summary Comparison", color="#c9d1d9", fontsize=10, pad=6)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"Chart saved -> {out_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="General-purpose multi-asset portfolio backtester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--config",    default=None, metavar="FILE",
                   help="JSON file defining multiple portfolios (see portfolios.json)")
    p.add_argument("--portfolio", nargs="+", metavar="TICKER:WEIGHT",
                   help="e.g. --portfolio QQQ:0.6 TQQQ:0.4  (required without --config)")
    p.add_argument("--start",     default=None, help="Start date YYYY-MM-DD")
    p.add_argument("--end",       default=None, help="End date YYYY-MM-DD")
    p.add_argument("--rebalance", default=None,
                   choices=["none", "daily", "weekly", "monthly", "quarterly", "yearly"])
    p.add_argument("--initial",   type=float, default=None, help="Starting value in USD")
    p.add_argument("--benchmark", nargs="*", default=["SPY", "QQQ"],
                   help="Benchmark tickers [--portfolio mode only]")
    p.add_argument("--name",      default="Portfolio",
                   help="Portfolio label [--portfolio mode only]")
    p.add_argument("--no-plot",   action="store_true", help="Skip chart generation")
    p.add_argument("--out",       default="output", help="Output folder for charts")
    return p.parse_args()


def parse_portfolio(tokens: list[str]) -> dict[str, float]:
    weights = {}
    for token in tokens:
        if ":" not in token:
            raise ValueError(f"Bad format '{token}'. Use TICKER:WEIGHT, e.g. QQQ:0.6")
        ticker, weight_str = token.split(":", 1)
        weights[ticker.upper()] = float(weight_str)
    total = sum(weights.values())
    if abs(total - 1.0) > 0.02:
        raise ValueError(f"Weights sum to {total:.4f}, must be ~1.0. Got: {weights}")
    return weights


def main() -> int:
    args = parse_args()

    if not args.config and not args.portfolio:
        print("ERROR: provide --config <file> or --portfolio TICKER:WEIGHT …", file=sys.stderr)
        return 1

    # ── Build portfolio list ──────────────────────────────────────────────
    if args.config:
        portfolios, color_map, rebalance_map, synth_tickers, cfg_start, cfg_end, cfg_capital, cfg_rebalance = load_config(args.config)
        start     = args.start    or cfg_start    or "2003-01-01"
        end       = args.end      or cfg_end      or date.today().isoformat()
        initial   = args.initial  or cfg_capital  or 10_000
        rebalance = args.rebalance or cfg_rebalance or "quarterly"
        if synth_tickers:
            inst_summary = ", ".join(f"{t} ({v['L']}x {v['base']})" for t, v in synth_tickers.items())
            print(f"Instruments: {inst_summary}")
        print(f"Loaded {len(portfolios)} portfolio(s) from {args.config}")
        all_tickers = list({t for w in portfolios.values() for t in w})
    else:
        rebalance_map = {}
        synth_tickers = {}
        try:
            port_weights = parse_portfolio(args.portfolio)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        start     = args.start    or "2003-01-01"
        end       = args.end      or date.today().isoformat()
        initial   = args.initial  or 10_000
        rebalance = args.rebalance or "monthly"
        portfolios = {args.name: port_weights}
        color_map  = {args.name: AUTO_COLORS[0]}
        for i, bm in enumerate(args.benchmark or []):
            portfolios[bm] = {bm: 1.0}
            color_map[bm]  = AUTO_COLORS[(i + 1) % len(AUTO_COLORS)]
        all_tickers = list({t for w in portfolios.values() for t in w})

    print(f"\nPeriod    : {start} → {end}")
    print(f"Capital   : ${initial:,.0f}")
    print(f"Rebalance : {rebalance}")
    print()

    # ── Download data ─────────────────────────────────────────────────────
    try:
        prices = download_prices(all_tickers, start, end, synth_tickers)
    except Exception as e:
        print(f"ERROR downloading data: {e}", file=sys.stderr)
        return 1

    # ── Simulate ──────────────────────────────────────────────────────────
    all_metrics: list[dict]      = []
    all_values:  list[pd.Series] = []
    colors:      list[str]       = []

    for name, weights in portfolios.items():
        missing = [t for t in weights if t not in prices.columns or prices[t].isna().all()]
        if missing:
            print(f"  {name}: skipped — missing data for {missing}")
            continue

        rb = rebalance_map.get(name, rebalance)
        vals = simulate(prices, weights, rebalance=rb, initial=initial)
        if vals.empty:
            continue

        m = compute_metrics(vals, name=name, initial=initial)
        all_metrics.append(m)
        all_values.append(vals)
        colors.append(color_map.get(name, AUTO_COLORS[len(colors) % len(AUTO_COLORS)]))
        print(f"  {name}: {m['cagr']:+.2%} CAGR  |  {m['max_dd']:.1%} max DD  |  Sharpe {m['sharpe']:.2f}")

    if not all_metrics:
        print("ERROR: no portfolios ran successfully", file=sys.stderr)
        return 1

    # ── Print results ─────────────────────────────────────────────────────
    print_summary(all_metrics)
    print_annual_table(all_metrics)

    # ── Chart ─────────────────────────────────────────────────────────────
    if not args.no_plot:
        ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug      = all_metrics[0]["name"].replace(" ", "_").lower()
        out_path  = Path(args.out) / f"{slug}_{ts}.png"
        try:
            plot_all(all_metrics, all_values, colors, rebalance, initial, out_path)
        except Exception as e:
            print(f"WARNING: chart generation failed: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
