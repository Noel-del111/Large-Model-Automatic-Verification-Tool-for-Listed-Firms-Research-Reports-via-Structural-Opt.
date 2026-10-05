"""Explicit local budget authorization migration; never calls a model or reads keys."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "factcheck/src"))
from yjcheck.model_runtime import BudgetLedger, DEFAULT_LEDGER


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--limit-cny", required=True)
    parser.add_argument("--authorization-id", required=True, help="Stable ID; retries reuse the same ID")
    parser.add_argument("--authorization-basis", required=True, help="Direct human authorization text, without credentials")
    args = parser.parse_args(argv)
    try:
        result = BudgetLedger.authorize_increase(args.ledger, args.limit_cny,
                                                authorization_id=args.authorization_id,
                                                authorization_basis=args.authorization_basis)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
