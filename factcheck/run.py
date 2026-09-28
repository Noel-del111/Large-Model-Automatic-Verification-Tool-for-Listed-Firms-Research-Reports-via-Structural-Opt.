"""零安装入口：python factcheck/run.py check --report ... --source ..."""
import sys
from pathlib import Path
root=Path(__file__).resolve().parent
sys.path[:0]=[str(root/"src"),str(root.parent/"pdfparse"/"src")]
from yjcheck.__main__ import main

if __name__=="__main__":
    raise SystemExit(main())
