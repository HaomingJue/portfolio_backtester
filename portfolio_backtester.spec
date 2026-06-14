# PyInstaller spec for the Portfolio Backtester.
#
# Build:  pyinstaller --noconfirm portfolio_backtester.spec   (or run build_exe.bat)
#
# Produces a SINGLE file: dist/PortfolioBacktester.exe — send your friend just
# that one file. First launch takes ~20-40s (it unpacks to a temp folder), then
# a browser tab opens. See README "Sending it to a friend" for the version note.

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas, binaries, hiddenimports = [], [], []

# Pull in data files, submodules, and binaries for the tricky packages.
for pkg in ("streamlit", "yfinance", "altair", "pyarrow"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Several of these read their own version via importlib.metadata at runtime.
for pkg in ("streamlit", "yfinance", "pandas", "numpy", "matplotlib",
            "altair", "pyarrow", "click", "rich", "tornado", "tenacity",
            "validators", "narwhals"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

# Bundle the application source + default config alongside the launcher.
datas += [
    ("app.py", "."),
    ("backtest.py", "."),
    ("portfolios.json", "."),
    (".streamlit", ".streamlit"),
]

hiddenimports += ["backtest"]

a = Analysis(
    ["run_app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# onefile: bundle binaries + data INTO the single .exe (no COLLECT step).
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PortfolioBacktester",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=True,          # keep the console: shows the URL and any error text
)
