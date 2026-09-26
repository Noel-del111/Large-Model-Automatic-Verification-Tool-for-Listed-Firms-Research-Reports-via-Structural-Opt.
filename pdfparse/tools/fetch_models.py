"""离线模型权重准备脚本（骨架）。

用途：在联网环境下载权重并记录哈希，再把文件拷贝到封闭环境，保证复现时版本一致。
当前未绑定具体模型，启用视觉模型或版面引擎后按需补充下载源与文件名。

用法：
    py -3 tools/fetch_models.py --list
    py -3 tools/fetch_models.py --verify models/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# 启用模型后在此登记：名称、来源、版本、目标文件名、sha256
MODELS = [
    # {
    #     "name": "MinerU2.5-VLM",
    #     "source": "https://huggingface.co/<repo>",
    #     "version": "<revision>",
    #     "file": "model.safetensors",
    #     "sha256": "<fill-after-download>",
    # },
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_list() -> int:
    if not MODELS:
        print("尚未登记模型。启用视觉模型或版面引擎后，在脚本顶部的 MODELS 中补充条目。")
        return 0
    for item in MODELS:
        print(f"- {item['name']}  {item['version']}\n  {item['source']}\n  文件 {item['file']}  sha256 {item.get('sha256', '')}")
    return 0


def cmd_verify(target: Path) -> int:
    if not MODELS:
        print("尚未登记模型，无需校验。")
        return 0
    failures = 0
    for item in MODELS:
        path = target / item["file"]
        if not path.exists():
            print(f"缺失 {path}")
            failures += 1
            continue
        actual = sha256_file(path)
        ok = actual == item.get("sha256")
        print(f"{'OK  ' if ok else 'FAIL'} {path.name} {actual[:16]}")
        failures += 0 if ok else 1
    return 1 if failures else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="离线模型权重准备与校验")
    parser.add_argument("--list", action="store_true", help="列出已登记的模型")
    parser.add_argument("--verify", type=Path, help="校验目录下权重文件的哈希")
    args = parser.parse_args(argv)
    if args.list or not args.verify:
        return cmd_list()
    return cmd_verify(args.verify)


if __name__ == "__main__":
    sys.exit(main())
