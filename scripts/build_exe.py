#!/usr/bin/env python3
"""Build a standalone Ledgerling executable.

Ledgerling itself has zero runtime dependencies (standard library only). This
helper turns the package into a distributable binary so people can run it
without a Python install or a checkout.

Two strategies, tried in order:

1. **PyInstaller** (preferred) -- produces a truly self-contained native
   executable under ``dist/`` that bundles the interpreter. Used when the
   ``PyInstaller`` package is importable.
2. **zipapp** (stdlib fallback) -- always available, no third-party packages.
   Produces ``dist/ledgerling.pyz``, a single executable archive that runs with
   any Python 3.7+ on the ``PATH`` (``python ledgerling.pyz ...`` or, where the
   shebang is honoured, ``./ledgerling.pyz ...``).

Both backends write only into ``build/`` and ``dist/`` next to this project,
which are git-ignored. Nothing is uploaded or pushed.

Usage::

    python scripts/build_exe.py            # auto-select backend
    python scripts/build_exe.py --zipapp   # force the stdlib fallback
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP = "ledgerling"


def _have_pyinstaller() -> bool:
    try:
        import PyInstaller  # noqa: F401
    except Exception:
        return False
    return True


def build_pyinstaller() -> Path:
    """Bundle a native one-file executable with PyInstaller."""
    import PyInstaller.__main__ as pyi

    entry = BUILD / "_entry.py"
    BUILD.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "from ledgerling.cli import main\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )
    pyi.run([
        str(entry),
        "--name", APP,
        "--onefile",
        "--console",
        "--paths", str(SRC),
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "pyinstaller"),
        "--specpath", str(BUILD),
        "--noconfirm",
        "--clean",
    ])
    exe = DIST / (APP + (".exe" if os.name == "nt" else ""))
    return exe


def build_zipapp() -> Path:
    """Produce a single-file executable archive using only the stdlib."""
    DIST.mkdir(parents=True, exist_ok=True)
    staging = BUILD / "zipapp_src"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copytree(SRC / APP, staging / APP,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    target = DIST / (APP + ".pyz")
    zipapp.create_archive(
        staging,
        target=target,
        interpreter="/usr/bin/env python3",
        main="ledgerling.cli:main",
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Ledgerling executable.")
    parser.add_argument("--zipapp", action="store_true",
                        help="force the stdlib zipapp backend")
    args = parser.parse_args()

    if not args.zipapp and _have_pyinstaller():
        print("Building with PyInstaller (native one-file executable)...")
        out = build_pyinstaller()
        backend = "PyInstaller"
    else:
        if not args.zipapp:
            print("PyInstaller not available; falling back to stdlib zipapp.")
        print("Building with zipapp (portable .pyz archive)...")
        out = build_zipapp()
        backend = "zipapp"

    if out.exists():
        size = out.stat().st_size
        print(f"\nOK [{backend}] -> {out}  ({size:,} bytes)")
        return 0
    print(f"\nERROR: expected output {out} was not created", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
