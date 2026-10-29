"""One-time setup on the PC:  python setup_pc.py   (run in the kunal-fyers-terminal folder).

1. makes the project's own Python box (.venv) if it is not there yet;
2. installs everything into it (requirements.txt);
3. installs fyers-apiv3 WITHOUT its strict old pins (its aiohttp 3.9.3 cannot install on Python 3.13 on Windows);
4. runs the tests.
No PowerShell script permission is needed: nothing here uses Activate.ps1.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FYERS = "fyers-apiv3==3.2.0"


def run(*args) -> None:
    print("\n>>", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT)


def main() -> None:
    venv = ROOT / ".venv"
    py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not py.exists():
        run(sys.executable, "-m", "venv", venv)
    run(py, "-m", "pip", "install", "--upgrade", "pip")
    run(py, "-m", "pip", "install", "-r", "requirements.txt")
    run(py, "-m", "pip", "install", "--no-deps", FYERS)
    run(py, "-c", "from fyers_apiv3 import fyersModel; print('fyers-apiv3 OK')")
    run(py, "-m", "pytest", "tests", "-q")
    print("\nALL DONE. Start the terminal: double-click start_terminal.bat  (or: .venv\\Scripts\\python.exe -m "
          "streamlit run app.py)")


if __name__ == "__main__":
    main()
