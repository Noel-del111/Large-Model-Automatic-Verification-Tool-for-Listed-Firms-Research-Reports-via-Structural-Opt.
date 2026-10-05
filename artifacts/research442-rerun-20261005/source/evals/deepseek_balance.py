"""Read official DeepSeek account balance without displaying credentials."""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "factcheck/src"))
from yjcheck.model import ModelConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    config = ModelConfig.from_env()
    if config.base_url.rstrip("/") not in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}:
        raise SystemExit("余额查询仅支持当前已配置的 DeepSeek 官方端点")
    if not config.api_key.strip():
        raise SystemExit("尚未配置 API Key")
    req = urllib.request.Request("https://api.deepseek.com/user/balance", headers={"Authorization": "Bearer " + config.api_key})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = json.load(response)
    except urllib.error.HTTPError as exc:
        print(json.dumps({"ok": False, "http_status": exc.code}))
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}))
        return 2
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "is_available": raw.get("is_available"),
              "balance_infos": raw.get("balance_infos", [])}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
