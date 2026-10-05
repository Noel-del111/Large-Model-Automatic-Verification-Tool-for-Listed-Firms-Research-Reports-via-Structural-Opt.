from __future__ import annotations
import argparse
import json
import sys
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError
from .pipeline import run_check, verify_artifacts


def main(argv=None):
    parser=argparse.ArgumentParser(description="C：带证据的研报数值、单位、期间、口径与引用核查")
    commands=parser.add_subparsers(dest="command",required=True)
    check=commands.add_parser("check")
    check.add_argument("--report",required=True)
    check.add_argument("--source",required=True,action="append")
    check.add_argument("--out",default="data/check")
    check.add_argument("--company")
    check.add_argument("--engine",choices=["pdfplumber","pymupdf"],default="pdfplumber")
    check.add_argument("--model",action="store_true",help="使用 YJCHECK_* 环境变量配置的模型辅助抽取")
    check.add_argument("--review-text", action="store_true", help="同时运行模型文本检测，费用计入共享预算")
    verify=commands.add_parser("verify")
    verify.add_argument("directory")
    args=parser.parse_args(argv)
    try:
        if args.command=="verify":
            passed=verify_artifacts(args.directory)
            print("verified" if passed else "verification_failed")
            return 0 if passed else 1
        config=None
        if args.model or args.review_text:
            from .model import ModelConfig
            config=ModelConfig.from_env()
            config.review_text = args.review_text
        result,path=run_check(args.report,args.source,args.out,args.company,args.engine,config)
        print(json.dumps({"output":str(path.resolve()),"summary":result["summary"]},ensure_ascii=False))
        return 0  # 业务发现错误也是成功执行；调用方看 summary，不以退出码推断研报正确。
    except (ValueError,OSError,KeyError,ImportError,TypeError,AttributeError,BadZipFile,ParseError) as exc:
        print(f"核查未完成：{exc}",file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
