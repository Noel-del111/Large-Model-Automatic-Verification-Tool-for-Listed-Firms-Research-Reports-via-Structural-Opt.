"""Compatibility package; implementation lives in the project root."""
from pathlib import Path as _Path
_canonical = _Path(__file__).resolve().parents[4] / "factcheck" / "src" / "yjcheck"
__path__ = [str(_canonical)]
__file__ = str(_canonical / "__init__.py")
exec(compile(_Path(__file__).read_text(encoding="utf-8"), __file__, "exec"), globals())
