# Portfolio Backtester

Compare any number of multi-asset portfolios over any time range. Prices are pulled live from Yahoo Finance, with quarterly/monthly/yearly rebalancing and a full stats table + charts.

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

Opens a browser dashboard with two pages (left sidebar):

**🛠️ Build & backtest** — everything for one portfolio on a single page:

1. *Find a stock* — search *any* symbol (stocks, ETFs, `BTC-USD`, …) and see it
   Yahoo-Finance style: price with daily change, market cap, P/E, dividend yield,
   52-week range, and a price chart over a chosen period. Hit
   **➕ Add … to portfolio**.
2. *Your portfolio* — give each holding any weight in an editable grid. Whatever
   you leave under 100% is held as **cash** (any proportion is allowed). Name it
   and hit **💾 Save** (validates every ticker, then writes to
   `saved_portfolios.json`).
3. *Composition* — a pie + bar of the current allocation (cash included) and a
   **sector breakdown** of the holdings.
4. *Backtest* — run it over **any** date range (sidebar settings), optionally
   versus the S&P 500. Summary stats, log-scale growth chart, drawdown chart, and
   a color-coded annual-returns table.

**📂 Saved portfolios** — the list of everything you've saved. Pick one to see
its holdings table (ticker, name, sector, weight) and composition, then
**✏️ Load into builder** to drop it back onto the build page, or **🗑️ Delete** it.

Start/end dates, capital, and rebalance frequency live in the left sidebar.

All prices come straight from Yahoo Finance — there is no synthetic/modeled
history. A holding that hadn't launched yet at the chosen start is simply held as
cash until its first trading day, and the app tells you which ones and from when
(e.g. start a 2010 backtest holding TSLA and it sits in cash until 2010-06-29).

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
  "start": "2010-01-01",
  "end": null,
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

### Top-level fields

| Field | Required | Description |
|---|---|---|
| `start` | No | Backtest start date (default: 2010-01-01) |
| `end` | No | Backtest end date (default: today). Set to `null` for today. |
| `capital` | No | Starting capital in USD (default: 10000) |
| `rebalance` | No | Default rebalance frequency for all portfolios |

All top-level fields can be overridden by CLI flags (`--start`, `--end`, `--initial`, `--rebalance`).

### Data source

Every ticker is downloaded directly from Yahoo Finance. Any symbol Yahoo lists
works — stocks, ETFs (including leveraged ones like `TQQQ`, `UPRO`), and crypto
like `BTC-USD`. There is no synthetic/pre-inception modeling: each portfolio's
backtest starts on the **latest inception date** among its holdings, so the
effective start can be later than the `start` you request.

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

---

## Sending it to a friend

The app needs an internet connection at runtime (it downloads prices from Yahoo
Finance). There are two ways to share it.

### Option A — they have (or can install) Python *(easiest, most reliable)*

Send them this folder (zip it). They double-click **`run.bat`**. It installs the
dependencies on first run and opens the dashboard in their browser. Done.

### Option B — a single standalone `.exe` (no Python needed on their machine)

Streamlit is a web app, so the "exe" is really a launcher that starts the app
and opens a browser tab — packaged with [PyInstaller](https://pyinstaller.org).

```bat
build_exe.bat
```

This installs PyInstaller if needed and builds from `portfolio_backtester.spec`.
The result is a **single file**: `dist\PortfolioBacktester.exe` — send your
friend just that one file. They double-click it; a console window opens, the
first launch takes ~20–40 s while it unpacks, then a browser tab opens with the
app. User-saved portfolios (`saved_portfolios.json`) are written next to the .exe
so they persist between runs.

Notes / caveats:

- The single file is **large (~230 MB)** — it bundles Python plus
  pandas/numpy/matplotlib. That's normal. (To trade size/startup the spec can be
  switched back to a `onedir` folder build, which starts faster.)
- **Build on the same OS you'll run on** (build on Windows → Windows .exe).
- **Python version:** built and tested on Python 3.14 with PyInstaller 6.21. If a
  future PyInstaller can't handle your Python, build inside a 3.12/3.13 venv:
  ```bat
  py -3.12 -m venv build-env
  build-env\Scripts\activate
  pip install -r requirements.txt pyinstaller
  pyinstaller --noconfirm --clean portfolio_backtester.spec
  ```
- First launch may be flagged by SmartScreen/antivirus (unsigned exe) — that's
  normal for self-built apps; "More info → Run anyway".

Files involved: `run_app.py` (launcher), `portfolio_backtester.spec` (build
recipe), `build_exe.bat` (one-click build), `.streamlit/config.toml` (launch
settings).
