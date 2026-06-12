# Portfolio Backtester

Compare any number of multi-asset portfolios over any time range. Supports quarterly/monthly/yearly rebalancing, synthetic pre-inception NAV for common leveraged ETFs, and outputs a full stats table + charts.

---

## Installation

```bash
pip install -r requirements.txt
```

---

## Usage

### Interactive UI (recommended)

```bash
streamlit run app.py
```

Opens a browser dashboard:

- pick preset portfolios from `portfolios.json` and/or type custom ones
  (`My 60/40 = SPY:0.6, TLT:0.4` — one per line),
- adjust start/end dates, capital, and rebalance frequency with widgets,
- get the summary stats table, log-scale growth chart, drawdown chart, and
  color-coded annual returns table live; price data is cached for an hour.

Synthetic pre-inception leveraged NAV (SSO, UPRO, TQQQ, QLD, TMF, …) works
exactly as in the CLI — it is read from the `instruments` section of
`portfolios.json`.

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
  "end": null,
  "capital": 10000,
  "rebalance": "quarterly",

  "instruments": [
    { "ticker": "SSO",  "base": "SPY", "leverage": 2, "mer": 0.0089 },
    { "ticker": "TMF",  "base": "TLT", "leverage": 3, "mer": 0.0093 }
  ],

  "portfolios": [
    {
      "name": "My Portfolio",
      "weights": { "SSO": 0.60, "TMF": 0.40 },
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

### Top-level fields

| Field | Required | Description |
|---|---|---|
| `start` | No | Backtest start date (default: 2003-01-01) |
| `end` | No | Backtest end date (default: today). Set to `null` for today. |
| `capital` | No | Starting capital in USD (default: 10000) |
| `rebalance` | No | Default rebalance frequency for all portfolios |

All top-level fields can be overridden by CLI flags (`--start`, `--end`, `--initial`, `--rebalance`).

### `instruments` — ticker metadata (optional section)

The `instruments` section is optional. Two modes:

**Normal ETF** — just record the MER, download prices normally from Yahoo:
```json
{ "ticker": "SPY", "mer": 0.0009 }
```
Prices are downloaded directly. If Yahoo has no data before a certain date, the backtest simply starts from the first available day.

**Leveraged / synthetic ETF** — provide `base`, `leverage`, and `mer` to build pre-inception NAV:
```json
{ "ticker": "SSO", "base": "SPY", "leverage": 2, "mer": 0.0089 }
```
Synthetic history is built going back to the base ETF's first available date. Real prices are used from the ETF's actual inception date onward.

| Field | Required for synth | Description |
|---|---|---|
| `ticker` | Yes | Symbol to use in portfolio weights |
| `base` | Synth only | Underlying index ETF (e.g. `"SPY"`) |
| `leverage` | Synth only | Daily leverage multiplier (e.g. `2` or `3`) |
| `mer` | Yes | Annual management expense ratio as a decimal (e.g. `0.0089` = 0.89%) |

Any ticker not listed in `instruments` is downloaded directly from Yahoo Finance and starts from its first available trading day.

### `portfolios` fields

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Label shown in output and charts |
| `weights` | Yes | Ticker → allocation (must sum to ~1.0) |
| `color` | No | Hex color for charts (auto-assigned if omitted) |
| `rebalance` | No | Per-portfolio override of the global rebalance setting |

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

Leveraged ETFs are defined in the `"instruments"` section of your JSON config. There are no hardcoded tickers — you control which ETFs get synthetic history and with what parameters.

The daily-reset NAV model used for synthetic data:

```
daily_return = L × base_return − 0.5 × (L² − L) × variance_20d − MER/252
```

**MER is only applied during the synthetic period (before the ETF's inception date).** After inception, real market prices from Yahoo Finance are used directly — those prices already have the MER baked into the ETF's NAV. This applies equally to leveraged and non-leveraged ETFs: recording `mer` for a normal ETF like SPY is purely informational and has no effect on the simulation.

Real prices are used from the ETF's inception date onward. Synthetic data fills in every day before that, going as far back as the base ETF's history allows.

### Example instruments block (common leveraged ETFs)

```json
"instruments": [
  { "ticker": "SSO",  "base": "SPY", "leverage": 2, "mer": 0.0089 },
  { "ticker": "UPRO", "base": "SPY", "leverage": 3, "mer": 0.0091 },
  { "ticker": "TMF",  "base": "TLT", "leverage": 3, "mer": 0.0093 },
  { "ticker": "UGL",  "base": "GLD", "leverage": 2, "mer": 0.0095 },
  { "ticker": "TQQQ", "base": "QQQ", "leverage": 3, "mer": 0.0086 },
  { "ticker": "QLD",  "base": "QQQ", "leverage": 2, "mer": 0.0095 },
  { "ticker": "SPXL", "base": "SPY", "leverage": 3, "mer": 0.0102 }
]
```

Any ticker not listed in `instruments` is downloaded directly from Yahoo Finance.

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
