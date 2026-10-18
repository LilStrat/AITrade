"""First-time setup without .bat files (for PCs where Smart App Control blocks them).

Run from PowerShell inside the project folder:

    py -3.14 setup_windows.py

It uses only Python's standard library: creates the private .venv folder,
installs the pinned packages into it, initialises the database and runs the
self-check. Nothing is installed outside this folder.
"""

import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(*args) -> None:
    print("\n>", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True)


def main() -> int:
    print("UK Trading Bot - first time setup (installs into this folder only)")
    print(f"Python {sys.version.split()[0]} at {sys.executable}")
    if sys.version_info < (3, 12):
        print("Python 3.12 or newer is required (3.14 recommended).")
        return 1
    if not PY.exists():
        print(f"Creating private environment in {VENV} ...")
        venv.EnvBuilder(with_pip=True).create(VENV)
    os.environ["PYTHONUTF8"] = "1"
    run(PY, "-m", "pip", "install", "--upgrade", "pip")
    run(PY, "-m", "pip", "install", "-r", ROOT / "requirements.txt")
    run(PY, ROOT / "app.py", "init")
    run(PY, ROOT / "app.py", "selfcheck")
    print("\nSetup finished. To start the bot run:   py app.py")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except subprocess.CalledProcessError as exc:
        print(f"\nA step failed (exit code {exc.returncode}). Please send the messages above (they contain no secrets).")
        code = 1
    if sys.stdin and sys.stdin.isatty():
        input("\nPress Enter to close...")
    sys.exit(code)
