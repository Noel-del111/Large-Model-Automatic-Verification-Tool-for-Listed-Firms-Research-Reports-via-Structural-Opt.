"""Compatibility reference to the canonical PDF test fixtures."""
from pathlib import Path as _Path
from runpy import run_path as _run_path
_target = _Path(__file__).resolve().parents[3] / "pdfparse/tests/sample_pdfs.py"
_namespace = _run_path(str(_target))
globals().update({key: value for key, value in _namespace.items() if not key.startswith("__")})
