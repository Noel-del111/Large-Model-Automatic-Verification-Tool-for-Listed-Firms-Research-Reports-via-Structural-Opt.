"""Build the deterministic R09 long-request PDF and machine-readable acceptance summary."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

FRONTEND = Path(__file__).resolve().parents[1]
ROOT = FRONTEND.parent
sys.path.insert(0, str(FRONTEND))

from exports import evidence_request_contract, findings_csv_bytes, report_md, report_pdf_bytes


def main() -> int:
    fixture = json.loads((ROOT / "factcheck/examples/r09/04_ask_two_periods.json").read_text(encoding="utf-8"))
    result = deepcopy(fixture)
    request = result["findings"][0]["evidence_request"][0]
    request["file"] = "C:/仅作展示/不存在的来源|文件.pdf"
    request["reason"] += "；" + "长文本分页核对，保留全部业务含义。" * 260
    out = ROOT / "docs/validation/r09"
    out.mkdir(parents=True, exist_ok=True)
    pdf = report_pdf_bytes(result, {})
    if not pdf:
        raise RuntimeError("PDF generator unavailable")
    pdf_path = out / "r09-long-evidence-request.pdf"
    pdf_path.write_bytes(pdf)
    csv_data, md = findings_csv_bytes(result, {}), report_md(result, {})
    contract = evidence_request_contract(result)
    summary = {
        "fixture": "04_ask_two_periods.json with extended display-only reason",
        "contract": contract,
        "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "pdf_bytes": len(pdf),
        "csv_contains_both_periods": all(period.encode() in csv_data for period in ("2024FY", "2023FY")),
        "markdown_contains_both_periods": all(period in md for period in ("2024FY", "2023FY")),
        "request_file_accessed": False,
        "note": "Synthetic contract acceptance artifact; not a model-effect result or human sign-off."
    }
    (out / "r09-acceptance.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
