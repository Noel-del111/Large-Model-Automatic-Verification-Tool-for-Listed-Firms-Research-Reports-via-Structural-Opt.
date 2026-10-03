"""Compatibility launcher for the project-root implementation."""
from pathlib import Path
import runpy
import sys
_root = Path(__file__).resolve().parents[2]
_target = _root / 'pdfparse/app.py'
sys.path[:0] = [str(_target.parent), str(_root / "factcheck/src"), str(_root / "pdfparse/src")]
if __name__ == "__main__":
    runpy.run_path(str(_target), run_name="__main__")
else:
    globals().update(runpy.run_path(str(_target)))
