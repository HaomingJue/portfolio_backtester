# Portfolio Backtester

Compare any number of multi-asset portfolios over any time range. Supports quarterly/monthly/yearly rebalancing, synthetic pre-inception NAV for common leveraged ETFs, and outputs a full stats table + charts.

---

## Installation

```bash
pip install -r requirements.txt
```

---

## Usage

### Config mode — compare multiple portfolios from a JSON file

```bash
python backtest.py --config portfolios.json
```

Override any JSON setting from the command line:

```bash
python backtest.py --config portfolios.json --start 2010-01-01
python backtest.py --config portfolios.json --start 2015-01-01 --initial 100000
```

### Single-portfolio mode — one portfolio vs benchmarks

```bash
# 60% QQQ / 40% TQQQ, monthly rebalance
python backtest.py --portfolio QQQ:0.6 TQQQ:0.4

# HFEA since 2010 vs SPY and QQQ benchmarks
python backtest.py --portfolio UPRO:0.55 TMF:0.45 --start 2010-01-01

# Custom name, quarterly rebalance, no benchmark
python backtest.py --portfolio SPY:0.25 TLT:0.25 GLD:0.25 SHY:0.25 \
    --name "Permanent" --rebalance quarterly --benchmark
```

---

## JSON Config Format

```json
{
  "start": "2003-01-01",
  "capital": 10000,
  "rebalance": "quarterly",

  "portfolios": [
    {
      "name": "My Portfolio",
      "weights": { "SPY": 0.60, "TLT": 0.40 },
      "color": "#1E88E5",
      "rebalance": "monthly"
    },
    {
      "name": "S&P 500 B&H",
      "weights": { "SPY": 1.0 },
      "rebalance": "none"
    }
  ]
}
```

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Label shown in output and charts |
| `weights` | Yes | Ticker → allocation (must sum to ~1.0) |
| `color` | No | Hex color for charts (auto-assigned if omitted) |
| `rebalance` | No | Per-portfolio override of the global rebalance setting |

Top-level fields (`start`, `capital`, `rebalance`) can all be overridden by CLI flags.

### Rebalance options

| Value | Behaviour |
|---|---|
| `none` | Buy and hold — no rebalancing |
| `daily` | Rebalance every trading day |
| `weekly` | First trading day of each week |
| `monthly` | First trading day of each month |
| `quarterly` | First trading day of each quarter |
| `yearly` | First trading day of each year |

---

## Leveraged ETF Synthetic NAV

The following leveraged ETFs are extended backwards before their inception date using a daily-reset NAV model (`L × base_return − vol_drag − MER`). Real ETF prices are used from inception onward.

| Ticker | Base | Leverage | MER |
|---|---|---|---|
| UPRO | SPY | 3× | 0.91% |
| SSO | SPY | 2× | 0.89% |
| TQQQ | QQQ | 3× | 0.86% |
| QLD | QQQ | 2× | 0.95% |
| TMF | TLT | 3× | 0.93% |
| UGL | GLD | 2× | 0.95% |

Any other ticker is downloaded directly from Yahoo Finance. If it has no data before its IPO/inception, the entire portfolio simulation starts from that ticker's first available date.

To add a new leveraged ETF, add an entry to `_SYNTH_TICKERS` in `backtest.py`:

```python
"SPXL": {"base": "SPY", "L": 3, "mer": 0.0102},
```

---

## Output

**Console**
- Summary table: CAGR, Vol, Sharpe, Calmar, Max DD, Worst/Best year, Final value, Green years
- Annual returns table: year-by-year comparison across all portfolios

**Chart** (saved to `output/`)
- Growth of $N (log scale)
- Drawdown over time
- Annual returns bar chart (first portfolio)
- Stats comparison table

Use `--no-plot` to skip chart generation.
