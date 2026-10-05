"""命令行入口：parse / verify / report / doctor。

设计上刻意做成“一条命令跑通、一条命令校验”，对应竞赛对可复现的要求。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import load_thresholds, project_root
from .engines import registry
from .ledger import Ledger
from .pipeline import PipelineConfig, parse_document
from .report import render_summary_table, summarize, write_summary
from .utils import ensure_dir, new_run_id


def _iter_pdfs(target: Path):
    if target.is_file() and target.suffix.lower() == ".pdf":
        yield target
        return
    if target.is_dir():
        for path in sorted(target.rglob("*.pdf")):
            yield path


def cmd_parse(args) -> int:
    from .vlm_check import VlmConfig

    inputs = list(_iter_pdfs(Path(args.input)))
    if not inputs:
        print(f"未找到 PDF：{args.input}", file=sys.stderr)
        return 2
    if args.max_files:
        inputs = inputs[: args.max_files]

    out_dir = ensure_dir(args.out)
    thresholds = load_thresholds(args.thresholds)
    vlm_config = VlmConfig.from_env()
    if args.vlm_base_url:
        vlm_config.base_url = args.vlm_base_url.rstrip("/")
    if args.vlm_api_key:
        vlm_config.api_key = args.vlm_api_key
    if args.vlm_model:
        vlm_config.model = args.vlm_model
    vlm_config.max_pages = args.vlm_max_pages
    vlm_config.sample_ratio = args.vlm_sample_ratio
    config = PipelineConfig(
        primary=args.primary,
        reference=args.reference,
        ocr=args.ocr,
        ocr_dpi=args.ocr_dpi,
        ocr_min_score=args.ocr_min_score,
        vlm=args.vlm,
        vlm_config=vlm_config,
        thresholds=thresholds,
        engine_params=json.loads(args.engine_params) if args.engine_params else {},
        random_seed=args.seed,
    )
    if args.mineru_output:
        config.engine_params.setdefault("mineru", {})["output"] = args.mineru_output

    failures = 0
    for pdf in inputs:
        run_id = new_run_id()
        log_dir = ensure_dir(out_dir / "logs" / run_id)
        ledger = Ledger(log_dir, run_id, repo_dir=project_root())
        try:
            result = parse_document(pdf, out_dir, config, run_id=run_id, ledger=ledger)
        except Exception as exc:
            failures += 1
            ledger.tool("pipeline.parse", exit_code=1, extra={"error": str(exc)})
            print(f"[fail] {pdf.name}: {exc}", file=sys.stderr)
            continue
        print(
            f"[{result.quality_report.status:>4}] {result.doc.doc_id}  "
            f"页数={len(result.pages)}  块={result.quality_report.summary['blocks']}  "
            f"表={result.quality_report.summary['tables']}  "
            f"引擎={result.engine.name}/{result.engine.version}  "
            f"对照={result.reference_engine['name'] if result.reference_engine else 'none'}  "
            f"run={run_id}"
        )
    write_summary(out_dir)
    print()
    print(render_summary_table(summarize(out_dir)))
    print(f"\n产物目录：{out_dir}")
    return 1 if failures else 0


def cmd_verify(args) -> int:
    from .artifacts import verify_run

    checks = verify_run(Path(args.out), doc_id=args.doc)
    if not checks:
        print("没有可校验的产物，请先执行 parse。", file=sys.stderr)
        return 2
    bad = [c for c in checks if not c["match"]]
    for check in checks:
        flag = "OK  " if check["match"] else "FAIL"
        print(f"{flag} {Path(check['artifact']).name:<26} {check['actual'][:12]}")
    print(f"\n合计 {len(checks)} 项，通过 {len(checks) - len(bad)} 项，不一致 {len(bad)} 项")
    return 1 if bad else 0


def cmd_report(args) -> int:
    out_dir = Path(args.out)
    summary = summarize(out_dir)
    written = write_summary(out_dir)
    print(render_summary_table(summary))
    print()
    for name, path in written.items():
        print(f"{name}: {path}")
    return 0


def cmd_doctor(args) -> int:
    import platform
    import sys as _sys

    print(f"yjparse {__version__}")
    print(f"python   {_sys.version.split()[0]}  ({_sys.executable})")
    print(f"platform {platform.platform()}")
    print(f"项目根目录 {project_root()}")
    print("\n引擎可用性：")
    for name, info in sorted(registry().items()):
        status = "可用" if info.get("available") else "不可用"
        print(f"  - {name:<12} {status:<6} {info.get('version', '')}")
        if not info.get("available") and info.get("install_hint"):
            print(f"      {info['install_hint']}")
        for note in info.get("notes", []) or []:
            print(f"      说明：{note}")
    print("\n提示：核心链路为全本地运行，不访问网络；离线复现请配合 REPRODUCE.md 使用。")
    return 0


def cmd_preview(args) -> int:
    from .preview import render_for_doc

    pages = None
    if args.pages:
        pages = [int(x) for x in str(args.pages).replace("，", ",").split(",") if x.strip()]
    blocks = None
    if args.blocks:
        blocks = [b.strip() for b in str(args.blocks).replace("，", ",").split(",") if b.strip()]
    written = render_for_doc(Path(args.out), doc_id=args.doc, pages=pages,
                             dpi=args.dpi, draw_ids=args.draw_ids, only_blocks=blocks)
    if not written:
        print("没有生成预览，请检查 --out 与 --doc 是否正确。", file=sys.stderr)
        return 2
    for path in written[: args.limit]:
        print(path)
    if len(written) > args.limit:
        print(f"... 共 {len(written)} 张")
    return 0


def cmd_export_kb(args) -> int:
    from .kb_export import export_kb

    manifest = export_kb(Path(args.out), Path(args.kb), project=args.project)
    print(f"文档 {len(manifest['documents'])} 篇，索引块 {manifest['blocks']} 条")
    print(f"检索索引：{manifest['kb_index']}")
    print(f"接入清单：{manifest['manifest']}")
    print("\n入库命令（OpenViking CLI）：")
    for target in manifest["viking_targets"][:5]:
        print("  " + target["command"])
    if len(manifest["viking_targets"]) > 5:
        print(f"  ... 共 {len(manifest['viking_targets'])} 条")
    return 0


def cmd_model_check(args) -> int:
    from .model_check import probe, render

    results = probe(
        base_url=args.base_url,
        api_key=args.api_key or "",
        chat_model=args.chat_model or "",
        embedding_model=args.embedding_model or "",
        vision_model=args.vision_model or "",
        timeout=args.timeout,
    )
    print(render(results))
    return 0 if all(item.ok for item in results) else 1


def cmd_search(args) -> int:
    from .retrieval import Bm25Index, compare_across_documents, load_index

    rows = load_index(Path(args.kb) / "kb_index.jsonl")
    if not rows:
        print("索引为空，请先执行 export-kb。", file=sys.stderr)
        return 2
    if args.compare:
        groups = compare_across_documents(rows, args.query, top_k_per_doc=args.top)
        for group in groups:
            print(f"\n=== {group['doc_id']}")
            for hit in group["hits"]:
                bbox = ",".join(f"{v:.0f}" for v in (hit["bbox"] or []))
                print(f"  第{hit['page']}页 {hit['block_id']} [{hit['type']}] "
                      f"bbox=[{bbox}] 分数 {hit['score']}")
                print(f"    {hit['text'][:120]}")
        return 0
    index = Bm25Index(rows)
    hits = index.search(args.query, top_k=args.top, doc_ids=args.doc and [args.doc])
    if not hits:
        print("没有命中。")
        return 1
    for hit in hits:
        bbox = ",".join(f"{v:.0f}" for v in (hit["bbox"] or []))
        print(f"[{hit['score']:>7.3f}] {hit['doc_id']} 第{hit['page']}页 "
              f"{hit['block_id']} [{hit['type']}] bbox=[{bbox}] 状态={hit['page_status']}")
        print(f"          {hit['text'][:150]}")
    print("\n回链命令（把命中的块高亮出来）：")
    for hit in hits[:3]:
        print(f"  run.cmd preview --out <产物目录> --doc \"{hit['doc_id']}\" "
              f"--pages {hit['page']} --blocks {hit['block_id']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yjparse",
        description="研报 PDF 解析模块：抽取文本与表格、保留页码与坐标、识别解析失败",
    )
    parser.add_argument("--version", action="version", version=f"yjparse {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="解析 PDF 并生成结构化产物与日志")
    p_parse.add_argument("--input", required=True, help="PDF 文件或目录")
    p_parse.add_argument("--out", required=True, help="产物输出目录")
    p_parse.add_argument("--primary", default="pymupdf", help="主引擎，默认 pymupdf")
    p_parse.add_argument("--reference", default="auto",
                         help="对照引擎，默认 auto（优先 pdfplumber）")
    p_parse.add_argument("--thresholds", default=None, help="阈值配置路径")
    p_parse.add_argument("--engine-params", default=None, help="引擎参数 JSON 字符串")
    p_parse.add_argument("--mineru-output", default=None, help="MinerU 离线产物路径")
    p_parse.add_argument("--ocr", default="auto", choices=["auto", "off", "always"],
                         help="OCR 兜底策略，默认 auto：只处理无文本层或位图密集的页面")
    p_parse.add_argument("--ocr-dpi", type=int, default=200, help="OCR 渲染分辨率")
    p_parse.add_argument("--ocr-min-score", type=float, default=0.5, help="OCR 置信度下限")
    p_parse.add_argument("--vlm", default="off", choices=["off", "auto", "always"],
                         help="视觉模型抽检：auto 只抽问题页，always 按比例全篇抽检")
    p_parse.add_argument("--vlm-base-url", default=None,
                         help="视觉模型端点，如 https://ark.cn-beijing.volces.com/api/v3")
    p_parse.add_argument("--vlm-model", default=None, help="视觉模型名")
    p_parse.add_argument("--vlm-api-key", default=None, help="接口密钥，建议改用环境变量")
    p_parse.add_argument("--vlm-max-pages", type=int, default=20, help="单篇最多抽检页数")
    p_parse.add_argument("--vlm-sample-ratio", type=float, default=0.1,
                         help="always 模式的抽样比例")
    p_parse.add_argument("--max-files", type=int, default=None, help="最多处理几个文件")
    p_parse.add_argument("--seed", type=int, default=0, help="随机种子，写入运行清单")
    p_parse.set_defaults(func=cmd_parse)

    p_verify = sub.add_parser("verify", help="按运行清单校验产物哈希，验证可复现性")
    p_verify.add_argument("--out", required=True)
    p_verify.add_argument("--doc", default=None, help="只校验指定文档")
    p_verify.set_defaults(func=cmd_verify)

    p_report = sub.add_parser("report", help="汇总解析质量报告")
    p_report.add_argument("--out", required=True)
    p_report.set_defaults(func=cmd_report)

    p_doctor = sub.add_parser("doctor", help="检查运行环境与引擎可用性")
    p_doctor.set_defaults(func=cmd_doctor)

    p_preview = sub.add_parser("preview", help="把坐标画回页面图像，便于人工核对与演示")
    p_preview.add_argument("--out", required=True)
    p_preview.add_argument("--doc", default=None, help="文档名，默认处理全部")
    p_preview.add_argument("--pages", default=None, help="页码，例如 1,3,5")
    p_preview.add_argument("--dpi", type=int, default=110)
    p_preview.add_argument("--draw-ids", action="store_true", help="在框内标注 block_id")
    p_preview.add_argument("--blocks", default=None, help="只画指定块，逗号分隔，用于回链演示")
    p_preview.add_argument("--limit", type=int, default=20, help="最多打印多少条结果")
    p_preview.set_defaults(func=cmd_preview)

    p_kb = sub.add_parser("export-kb", help="导出知识库可入库的 Markdown、索引与接入清单")
    p_kb.add_argument("--out", required=True, help="解析产物目录")
    p_kb.add_argument("--kb", required=True, help="知识库导出目录")
    p_kb.add_argument("--project", default="research-reports", help="知识库项目名")
    p_kb.set_defaults(func=cmd_export_kb)

    p_search = sub.add_parser("search", help="在解析索引上做离线检索（BM25）")
    p_search.add_argument("--kb", required=True, help="知识库导出目录")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--top", type=int, default=8)
    p_search.add_argument("--doc", default=None, help="限定单个文档")
    p_search.add_argument("--compare", action="store_true", help="按文档分组，做横向对比")
    p_search.set_defaults(func=cmd_search)

    p_model = sub.add_parser("model-check", help="体检模型端点（Embedding / 对话 / 视觉）")
    p_model.add_argument("--base-url", required=True, help="OpenAI 兼容端点，如 https://.../v1")
    p_model.add_argument("--api-key", default=None)
    p_model.add_argument("--chat-model", default=None)
    p_model.add_argument("--embedding-model", default=None)
    p_model.add_argument("--vision-model", default=None)
    p_model.add_argument("--timeout", type=float, default=30.0)
    p_model.set_defaults(func=cmd_model_check)
    return parser


def main(argv=None) -> int:
    # 控制台代码页可能是 GBK，遇到 • 等字符会直接抛异常，这里统一降级为替换字符
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
