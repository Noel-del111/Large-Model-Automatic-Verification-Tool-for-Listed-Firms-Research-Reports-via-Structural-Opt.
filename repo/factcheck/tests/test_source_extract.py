"""Compatibility reference: tests are maintained in the project root."""
from pathlib import Path as _Path
from runpy import run_path as _run_path
_target = _Path(__file__).resolve().parents[3] / "factcheck" / "tests" / "test_source_extract.py"
_namespace = _run_path(str(_target), run_name="canonical_test_source_extract")
globals().update({key: value for key, value in _namespace.items() if not key.startswith("__")})
if __name__ == "__main__":
    import unittest
    unittest.main()
