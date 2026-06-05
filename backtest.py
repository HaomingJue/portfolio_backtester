#!/usr/bin/env python3
"""
backtest.py — general-purpose multi-asset portfolio backtester

Usage examples:
  # 60/40 QQQ + TQQQ rebalanced monthly
  python backtest.py --portfolio QQQ:0.6 TQQQ:0.4

  # HFEA: 55% UPRO + 45% TMF since 2010
  python backtest.py --portfolio UPRO:0.55 TMF:0.45 --start 2010-01-01

  # Your QQQ strategy vs HFEA vs SPY
  python backtest.py --portfolio TQQQ:1.0 --benchmark SPY QQQ UPRO --start 2003-01-01

  # All Weather-style with quarterly rebalance
  python backtest.py --portfolio VTI:0.30 TLT:0.40 IEI:0.15 GLD:0.075 DJP:0.075 \
      --rebalance quarterly --start 2007-01-01 --name "All Weather"

Arguments:
  --portfolio   TICKER:WEIGHT pairs (weights must sum to ~1.0)
  --start       Start date YYYY-MM-DD  (default: 2003-01-01)
  --end         End date   YYYY-MM-DD  (default: today)
  --rebalance   none | daily | weekly | monthly | quarterly | yearly (default: monthly)
  --initial     Starting value in USD  (default: 10000)
  --benchmark   Extra tickers to compare against (default: SPY QQQ)
  --name        Label for the portfolio in output (default: "Portfolio")
  --no-plot     Skip chart generation
  --out         Output folder for charts (default: ./output)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import yfinance as yf


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def download_prices(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    all_tickers = sorted(set(tickers))
    print(f"Downloading {', '.join(all_tickers)} from {start} to {end} …")
    raw = yf.download(
        all_tickers, start=start, end=end,
        auto_adjust=True, progress=False, multi_level_index=len(all_tickers) > 1,
    )
    if len(all_tickers) == 1:
        closes = pd.DataFrame({"Close": raw["Close"]})
        closes.columns = all_tickers
    else:
        closes = raw["Close"].copy()

    closes = closes.ffill()
    missing = [t for t in tickers if t not in closes.columns or closes[t].isna().all()]
    if missing:
        raise ValueError(f"No price data for: {', '.join(missing)}")
    return closes


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def _period_first_dates(index: pd.DatetimeIndex, freq: str) -> set:
    """Return the first trading date of each calendar period."""
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
    daily_ret = p.pct_change().fillna(0)

    holding = w * initial  # dollar amount per ticker
    values = []

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

    cagr = (final / initial) ** (1 / years) - 1

    peak = values.cummax()
    dd = (values - peak) / peak
    max_dd = dd.min()
    max_dd_end = dd.idxmin()
    max_dd_start = values.loc[:max_dd_end].idxmax()

    daily_ret = values.pct_change().dropna()
    vol = daily_ret.std() * np.sqrt(252)
    sharpe = (cagr - 0.04) / vol if vol > 0 else 0.0  # 4% risk-free
    calmar = cagr / abs(max_dd) if max_dd != 0 else float("inf")

    # Calendar year returns: compare Jan 1 (or start) to Dec 31 (or end)
    year_ends = values.resample("YE").last()
    year_starts = pd.concat([pd.Series([initial], index=[values.index[0]]),
                             values.resample("YE").last().shift(1)]).dropna()

    annual = {}
    years_seen = sorted(set(values.index.year))
    for yr in years_seen:
        yr_vals = values[values.index.year == yr]
        if yr_vals.empty:
            continue
        prev = values[values.index < yr_vals.index[0]]
        start_val = prev.iloc[-1] if not prev.empty else initial
        annual[yr] = yr_vals.iloc[-1] / start_val - 1

    annual_s = pd.Series(annual)
    worst_yr = annual_s.min()
    worst_yr_yr = int(annual_s.idxmin())
    best_yr = annual_s.max()
    best_yr_yr = int(annual_s.idxmax())

    return {
        "name": name,
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "years": round(years, 1),
        "initial": initial,
        "final": final,
        "cagr": cagr,
        "vol": vol,
        "sharpe": sharpe,
        "calmar": calmar,
        "max_dd": max_dd,
        "max_dd_start": max_dd_start.strftime("%Y-%m-%d"),
        "max_dd_end": max_dd_end.strftime("%Y-%m-%d"),
        "worst_yr": worst_yr,
        "worst_yr_yr": worst_yr_yr,
        "best_yr": best_yr,
        "best_yr_yr": best_yr_yr,
        "annual": annual_s,
        "drawdown": dd,
    }


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

COLS = [
    ("CAGR",         "cagr",        "{:+.2%}"),
    ("Vol (ann)",    "vol",         "{:.2%}"),
    ("Sharpe",       "sharpe",      "{:.2f}"),
    ("Calmar",       "calmar",      "{:.2f}"),
    ("Max DD",       "max_dd",      "{:.2%}"),
    ("Worst Year",   "worst_yr",    "{:+.2%}"),
    ("Worst Yr",     "worst_yr_yr", "{:d}"),
    ("Best Year",    "best_yr",     "{:+.2%}"),
    ("Best Yr",      "best_yr_yr",  "{:d}"),
    ("Final Value",  "final",       "${:,.0f}"),
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

    # Period row
    rows.append(["Period"] + [f"{m['start']} to {m['end']}" for m in all_metrics])
    rows.append(["Years"] + [f"{m['years']}" for m in all_metrics])
    rows.append(["Max DD period"] + [f"{m['max_dd_start']} -> {m['max_dd_end']}" for m in all_metrics])

    col_widths = [max(len(str(r[i])) for r in [headers] + rows) for i in range(len(headers))]
    sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
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

    headers = ["Year"] + [m["name"] for m in all_metrics]
    col_widths = [max(4, max(len(str(h)) for h in headers))] + [
        max(8, len(m["name"])) for m in all_metrics
    ]
    sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
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

COLORS = ["#2196F3", "#FF5722", "#4CAF50", "#FF9800", "#9C27B0",
          "#009688", "#E91E63", "#607D8B", "#795548"]


def _dollar_fmt(x, _):
    if x >= 1_000_000:
        return f"${x/1_000_000:.1f}M"
    if x >= 1_000:
        return f"${x/1_000:.0f}K"
    return f"${x:.0f}"


def plot_all(
    portfolio_values: pd.Series,
    portfolio_metrics: dict,
    benchmark_values: dict[str, pd.Series],
    benchmark_metrics: dict[str, dict],
    portfolio_name: str,
    initial: float,
    out_path: Path,
    rebalance: str,
    weights: dict[str, float],
) -> None:
    all_names = [portfolio_name] + list(benchmark_values.keys())
    all_series = [portfolio_values] + list(benchmark_values.values())
    all_metrics = [portfolio_metrics] + list(benchmark_metrics.values())

    fig = plt.figure(figsize=(16, 14))
    fig.patch.set_facecolor("#0d1117")

    gs = fig.add_gridspec(
        3, 2,
        height_ratios=[3, 1.5, 1.5],
        hspace=0.45, wspace=0.35,
        left=0.07, right=0.97, top=0.93, bottom=0.06,
    )

    ax_growth  = fig.add_subplot(gs[0, :])
    ax_dd      = fig.add_subplot(gs[1, :])
    ax_annual  = fig.add_subplot(gs[2, 0])
    ax_stats   = fig.add_subplot(gs[2, 1])

    for ax in [ax_growth, ax_dd, ax_annual, ax_stats]:
        ax.set_facecolor("#161b22")
        ax.tick_params(colors="#c9d1d9", labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor("#30363d")

    # ── 1. Growth chart ──────────────────────────────────────────────────
    for i, (name, vals) in enumerate(zip(all_names, all_series)):
        color = COLORS[i % len(COLORS)]
        lw = 2.5 if i == 0 else 1.5
        ax_growth.plot(vals.index, vals.values, color=color, lw=lw,
                       label=name, zorder=10 - i)

    ax_growth.set_yscale("log")
    ax_growth.yaxis.set_major_formatter(mticker.FuncFormatter(_dollar_fmt))
    ax_growth.yaxis.set_minor_formatter(mticker.NullFormatter())
    ax_growth.set_title(
        f"Growth of ${initial:,.0f}  |  {portfolio_name}  |  rebalance: {rebalance}",
        color="#c9d1d9", fontsize=12, pad=8,
    )
    ax_growth.legend(
        facecolor="#161b22", edgecolor="#30363d",
        labelcolor="#c9d1d9", fontsize=8, ncol=min(4, len(all_names)),
    )
    ax_growth.grid(True, color="#21262d", lw=0.5, which="major")
    ax_growth.grid(True, color="#161b22", lw=0.3, which="minor")

    # ── 2. Drawdown chart ────────────────────────────────────────────────
    for i, (name, m) in enumerate(zip(all_names, all_metrics)):
        color = COLORS[i % len(COLORS)]
        lw = 2 if i == 0 else 1
        ax_dd.fill_between(m["drawdown"].index, m["drawdown"].values * 100,
                           alpha=0.25 if i == 0 else 0.1, color=color)
        ax_dd.plot(m["drawdown"].index, m["drawdown"].values * 100,
                   color=color, lw=lw, label=name)

    ax_dd.set_title("Drawdown (%)", color="#c9d1d9", fontsize=10, pad=6)
    ax_dd.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax_dd.legend(facecolor="#161b22", edgecolor="#30363d",
                 labelcolor="#c9d1d9", fontsize=7, ncol=min(4, len(all_names)))
    ax_dd.grid(True, color="#21262d", lw=0.5)

    # ── 3. Annual returns bar chart (portfolio only) ─────────────────────
    ann = portfolio_metrics["annual"]
    years = [int(y) for y in ann.index]
    bar_colors = ["#4CAF50" if v >= 0 else "#F44336" for v in ann.values]
    ax_annual.bar(years, ann.values * 100, color=bar_colors, edgecolor="none", width=0.7)
    ax_annual.axhline(0, color="#555", lw=0.8)
    ax_annual.set_title(f"{portfolio_name} — Annual Returns",
                        color="#c9d1d9", fontsize=10, pad=6)
    ax_annual.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax_annual.tick_params(axis="x", labelrotation=45, labelsize=7)
    ax_annual.grid(True, color="#21262d", lw=0.5, axis="y")

    # ── 4. Stats comparison table ─────────────────────────────────────────
    ax_stats.axis("off")
    table_data = []
    col_labels = ["Metric"] + [
        m["name"][:12] for m in all_metrics
    ]
    stat_rows = [
        ("CAGR",       lambda m: f"{m['cagr']:+.1%}"),
        ("Max DD",     lambda m: f"{m['max_dd']:.1%}"),
        ("Worst Yr",   lambda m: f"{m['worst_yr']:+.1%} ({m['worst_yr_yr']})"),
        ("Best Yr",    lambda m: f"{m['best_yr']:+.1%} ({m['best_yr_yr']})"),
        ("Sharpe",     lambda m: f"{m['sharpe']:.2f}"),
        ("Calmar",     lambda m: f"{m['calmar']:.2f}" if m['calmar'] != float('inf') else "∞"),
        ("Final",      lambda m: f"${m['final']:,.0f}"),
    ]
    for row_name, fn in stat_rows:
        row = [row_name] + [fn(m) for m in all_metrics]
        table_data.append(row)

    table = ax_stats.table(
        cellText=table_data,
        colLabels=col_labels,
        loc="center",
        cellLoc="right",
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
        if c == 1 and r > 0:  # portfolio column
            cell.set_facecolor("#1f2d1f" if r % 2 == 0 else "#192119")

    ax_stats.set_title("Summary Comparison", color="#c9d1d9", fontsize=10, pad=6)

    # ── Holdings watermark ────────────────────────────────────────────────
    alloc_str = "  ".join(f"{t} {w:.0%}" for t, w in weights.items())
    fig.text(0.5, 0.005, f"Holdings: {alloc_str}  |  Backtester — not financial advice",
             ha="center", fontsize=7, color="#484f58")

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
    p.add_argument(
        "--portfolio", nargs="+", required=True, metavar="TICKER:WEIGHT",
        help='e.g. --portfolio QQQ:0.6 TQQQ:0.4',
    )
    p.add_argument("--start",     default="2003-01-01", help="Start date YYYY-MM-DD")
    p.add_argument("--end",       default=date.today().isoformat(), help="End date YYYY-MM-DD")
    p.add_argument("--rebalance", default="monthly",
                   choices=["none", "daily", "weekly", "monthly", "quarterly", "yearly"])
    p.add_argument("--initial",   type=float, default=10_000, help="Starting value in USD")
    p.add_argument("--benchmark", nargs="*", default=["SPY", "QQQ"],
                   help="Benchmark tickers (each $initial invested, no rebalancing)")
    p.add_argument("--name",      default="Portfolio", help="Label for the portfolio")
    p.add_argument("--no-plot",   action="store_true", help="Skip chart generation")
    p.add_argument("--out",       default="output", help="Output folder for charts")
    return p.parse_args()


def parse_portfolio(tokens: list[str]) -> dict[str, float]:
    weights = {}
    for token in tokens:
        if ":" not in token:
            raise ValueError(
                f"Bad format '{token}'. Use TICKER:WEIGHT, e.g. QQQ:0.6"
            )
        ticker, weight_str = token.split(":", 1)
        weights[ticker.upper()] = float(weight_str)
    total = sum(weights.values())
    if abs(total - 1.0) > 0.02:
        raise ValueError(
            f"Weights sum to {total:.4f}, must be ~1.0. Got: {weights}"
        )
    return weights


def main() -> int:
    args = parse_args()

    try:
        weights = parse_portfolio(args.portfolio)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"\nPortfolio  : {args.name}")
    print(f"Holdings   : {', '.join(f'{t} {w:.1%}' for t, w in weights.items())}")
    print(f"Period     : {args.start} to {args.end}")
    print(f"Rebalance  : {args.rebalance}")
    print(f"Benchmarks : {', '.join(args.benchmark) if args.benchmark else '(none)'}")
    print()

    all_tickers = list(weights.keys()) + (args.benchmark or [])
    try:
        prices = download_prices(all_tickers, args.start, args.end)
    except Exception as e:
        print(f"ERROR downloading data: {e}", file=sys.stderr)
        return 1

    # Align start date to first date where ALL portfolio tickers have data
    portfolio_tickers = list(weights.keys())
    first_valid = prices[portfolio_tickers].dropna().index[0]
    if str(first_valid.date()) > args.start:
        print(f"NOTE: Data starts {first_valid.date()} (latest first-available among {portfolio_tickers})")
    prices = prices.loc[first_valid:]

    # Simulate portfolio
    port_vals = simulate(prices, weights, rebalance=args.rebalance, initial=args.initial)
    port_metrics = compute_metrics(port_vals, name=args.name, initial=args.initial)

    # Simulate benchmarks (buy-and-hold, no rebalancing)
    bench_vals: dict[str, pd.Series] = {}
    bench_metrics: dict[str, dict] = {}
    for ticker in (args.benchmark or []):
        if ticker not in prices.columns:
            print(f"WARNING: {ticker} not available, skipping benchmark")
            continue
        bvals = simulate(prices, {ticker: 1.0}, rebalance="none", initial=args.initial)
        bench_vals[ticker] = bvals
        bench_metrics[ticker] = compute_metrics(bvals, name=ticker, initial=args.initial)

    # Print results
    all_m = [port_metrics] + list(bench_metrics.values())
    print_summary(all_m)
    print_annual_table(all_m)

    # Chart
    if not args.no_plot:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name_slug = args.name.replace(" ", "_").lower()
        chart_path = Path(args.out) / f"{name_slug}_{ts}.png"
        try:
            plot_all(
                portfolio_values=port_vals,
                portfolio_metrics=port_metrics,
                benchmark_values=bench_vals,
                benchmark_metrics=bench_metrics,
                portfolio_name=args.name,
                initial=args.initial,
                out_path=chart_path,
                rebalance=args.rebalance,
                weights=weights,
            )
        except Exception as e:
            print(f"WARNING: Chart generation failed: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
