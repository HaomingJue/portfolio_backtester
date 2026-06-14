"""
Launcher for the packaged Portfolio Backtester.

Runs the Streamlit app in-process and opens it in the default browser. Works
both as a normal script (`python run_app.py`) and when frozen into an .exe by
PyInstaller (see portfolio_backtester.spec / build_exe.bat).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _resource_dir() -> Path:
    """Folder that holds the bundled app.py — differs when frozen by PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).parent


def main() -> int:
    here = _resource_dir()
    app  = here / "app.py"

    # Keep Streamlit's own config inside the bundle predictable.
    os.environ.setdefault("STREAMLIT_GLOBAL_DEVELOPMENT_MODE", "false")
    os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")

    # Streamlit reads its launch options from argv.
    sys.argv = [
        "streamlit", "run", str(app),
        "--global.developmentMode=false",
        "--server.headless=false",        # auto-open the browser for the user
        "--server.fileWatcherType=none",  # no file watcher when frozen
        "--browser.gatherUsageStats=false",
    ]

    from streamlit.web import cli as stcli
    return stcli.main()


if __name__ == "__main__":
    sys.exit(main())
