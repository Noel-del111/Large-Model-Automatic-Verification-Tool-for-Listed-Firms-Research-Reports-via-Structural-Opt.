"""Compatibility reference to the canonical yjparse.retrieval module."""
from importlib import import_module as _import_module
from pathlib import Path as _Path
import sys as _sys
_root = next(parent for parent in _Path(__file__).resolve().parents if (parent / "repo").is_dir())
_sys.path[:0] = [str(_root / "factcheck" / "src"), str(_root / "pdfparse" / "src")]
if __name__ == "__main__":
    from runpy import run_module as _run_module
    _run_module("yjparse.retrieval", run_name="__main__")
else:
    globals().update(_import_module("yjparse.retrieval").__dict__)
