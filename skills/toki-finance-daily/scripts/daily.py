#!/usr/bin/env python3
"""Locate the cloned project behind a linked Skill without hard-coded host paths."""
from pathlib import Path
import os
import subprocess
import sys

root = Path(__file__).resolve().parents[3]
python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if not python.exists():
    python = Path(sys.executable)
env = dict(os.environ)
env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
if sys.argv[1:2] == ["finish"]:
    command = [str(python), str(Path(__file__).resolve().with_name("finish.py")), *sys.argv[2:]]
else:
    command = [str(python), "-m", "toki_finance_daily", *sys.argv[1:]]
raise SystemExit(subprocess.call(command, env=env))
