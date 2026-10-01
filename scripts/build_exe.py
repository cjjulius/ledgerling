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


def _run_pyinstaller(name: str, entry_code: str, windowed: bool,
                     extra: list[str] | None = None) -> Path:
    """Bundle one one-file executable from a small generated entry script."""
    import PyInstaller.__main__ as pyi

    # Best-effort pre-clean of this build's work dir. On Windows (especially
    # under OneDrive) a synced/locked file can make PyInstaller's own --clean
    # raise PermissionError, so we remove it ourselves and tolerate stragglers.
    work = BUILD / ("pyinstaller_" + name)
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    entry = BUILD / ("_entry_" + name + ".py")
    entry.write_text(entry_code, encoding="utf-8")

    args = [
        str(entry),
        "--name", name,
        "--onefile",
        "--windowed" if windowed else "--console",
        "--paths", str(SRC),
        "--distpath", str(DIST),
        "--workpath", str(work),
        "--specpath", str(BUILD),
        "--noconfirm",
    ]
    args += extra or []
    pyi.run(args)
    return DIST / (name + (".exe" if os.name == "nt" else ""))


def build_pyinstaller() -> list[Path]:
    """Bundle the native executables: the console CLI, plus a windowed desktop
    app that launches straight into the Tkinter GUI."""
    cli_exe = _run_pyinstaller(
        APP,
        "from ledgerling.cli import main\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        windowed=False,
    )
    gui_exe = _run_pyinstaller(
        APP + "-gui",
        "from ledgerling.gui import launch\n\n"
        "if __name__ == '__main__':\n"
        "    launch()\n",
        windowed=True,
        # tkinter is imported lazily inside gui.py; name the submodules so
        # PyInstaller always bundles them.
        extra=["--hidden-import", "tkinter",
               "--hidden-import", "tkinter.ttk",
               "--hidden-import", "tkinter.messagebox"],
    )
    return [cli_exe, gui_exe]


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
        print("Building with PyInstaller (native one-file executables: "
              "console CLI + windowed desktop app)...")
        outs = build_pyinstaller()
        backend = "PyInstaller"
    else:
        if not args.zipapp:
            print("PyInstaller not available; falling back to stdlib zipapp.")
        print("Building with zipapp (portable .pyz archive)...")
        outs = [build_zipapp()]
        backend = "zipapp"

    missing = [o for o in outs if not o.exists()]
    if missing:
        for o in missing:
            print(f"\nERROR: expected output {o} was not created", file=sys.stderr)
        return 1
    print()
    for o in outs:
        print(f"OK [{backend}] -> {o}  ({o.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
