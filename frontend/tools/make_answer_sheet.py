# -*- coding: utf-8 -*-
"""自建评测集答案表生成工具：借鉴 FinED-Bench 三字段标注法生成"预期输出清单.xlsx"。

FinED-Bench 的微调样本同时保留 origin_text（修改前正确原文）、error_text（修改后
错误文本）与 error_type，便于核对"系统找到依据还是猜对答案"。本工具把同样的三元组
标注清单写成本项目答案表格式（表头行：序号/位置/错误类型/研报原文/建议修改/
来源页码/依据，与 evaluate_samples.read_answer_rows 的列名自动对齐兼容）。

不依赖 openpyxl：直接写最小 OOXML（zipfile），读端 evaluate_samples 正是 XML 解析
路径，可相互验证。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="xml" ContentType="application/xml"/>'
                 '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                 '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                 '</Types>')
ROOT_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
             '</Relationships>')
WORKBOOK = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="预期输出清单" sheetId="1" r:id="rId1"/></sheets></workbook>')
WORKBOOK_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                 'Target="worksheets/sheet1.xml"/></Relationships>')

_COLUMN_LETTERS = "ABCDEFGHIJKL"


def _cell(column: str, row: int, text: str) -> str:
    return (f'<c r="{column}{row}" t="inlineStr"><is><t xml:space="preserve">'
            f'{escape(str(text))}</t></is></c>')


def _sheet_xml(rows: list[list[str]]) -> str:
    body = []
    for number, values in enumerate(rows, start=1):
        cells = "".join(_cell(_COLUMN_LETTERS[i], number, value)
                        for i, value in enumerate(values) if i < len(_COLUMN_LETTERS))
        body.append(f'<row r="{number}">{cells}</row>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(body)}</sheetData></worksheet>')


def build_rows(spec: list[dict]) -> tuple[list[list[str]], list[str]]:
    """spec 项字段：file/position/error_type/error_text/origin_text/suggestion/source_page/reason"""
    has_file = any(item.get("file") for item in spec)
    headers = ["序号"] + (["文件名称"] if has_file else []) + \
              ["位置", "错误类型", "研报原文", "建议修改", "来源页码", "依据"]
    rows = [headers]
    for index, item in enumerate(spec, start=1):
        suggestion = item.get("suggestion")
        if not suggestion and item.get("origin_text"):
            suggestion = "改为：" + str(item["origin_text"])
        row = [str(index)]
        if has_file:
            row.append(str(item.get("file", "")))
        row += [str(item.get("position", "")), str(item.get("error_type", "")),
                str(item.get("error_text", "")), str(suggestion or ""),
                str(item.get("source_page", "")), str(item.get("reason", ""))]
        rows.append(row)
    return rows, headers


def write_xlsx(rows: list[list[str]], path: Path) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", ROOT_RELS)
        archive.writestr("xl/workbook.xml", WORKBOOK)
        archive.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELS)
        archive.writestr("xl/worksheets/sheet1.xml", _sheet_xml(rows))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True,
                        help="错误注入清单 JSON：列表，项含 error_type/error_text（必填）、"
                             "file/position/origin_text/suggestion/source_page/reason（可选）")
    parser.add_argument("--out", type=Path, required=True, help="输出 预期输出清单.xlsx 路径")
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if not isinstance(spec, list) or not spec:
        parser.error("spec 必须是非空 JSON 数组")
    rows, headers = build_rows(spec)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_xlsx(rows, args.out)
    print(f"已生成 {len(rows) - 1} 行答案表（列：{'、'.join(headers)}）")
    print(str(args.out.resolve()))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())