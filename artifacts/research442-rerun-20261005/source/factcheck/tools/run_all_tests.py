"""Run B and C tests and keep inspectable results; any skipped test fails this gate."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"factcheck/src"),str(ROOT/"pdfparse/src")]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,default=ROOT/"data/test-results")
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    combined=[]
    started=time.monotonic()
    with (args.out/"tests.log").open("w",encoding="utf-8") as stream:
        for module in ("pdfparse","factcheck"):
            suite=unittest.TestLoader().discover(str(ROOT/module/"tests"))
            result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
            info={"module":module,"total":result.testsRun,"passed":result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
                  "failures":[{"test":t.id(),"trace":trace} for t,trace in result.failures],
                  "errors":[{"test":t.id(),"trace":trace} for t,trace in result.errors],
                  "skipped":[{"test":t.id(),"reason":reason} for t,reason in result.skipped]}
            combined.append(info)
            print(f"{module}: {info['passed']}/{info['total']} passed; failures={len(info['failures'])}; errors={len(info['errors'])}; skipped={len(info['skipped'])}",flush=True)
    passed=all(m["total"] and m["passed"]==m["total"] for m in combined)
    versions={}
    for package in ("PyMuPDF","pdfplumber","pypdf","Pillow","streamlit","rapidocr","onnxruntime","reportlab","jsonschema"):
        try: versions[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: versions[package]=None
    summary={"created_at":datetime.now(timezone.utc).isoformat(),"passed":passed,"total":sum(m["total"] for m in combined),
             "duration_seconds":round(time.monotonic()-started,3),"python":sys.version,"dependencies":versions,"suites":combined}
    (args.out/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(args.out.resolve())
    return 0 if passed else 1


if __name__=="__main__":
    raise SystemExit(main())
