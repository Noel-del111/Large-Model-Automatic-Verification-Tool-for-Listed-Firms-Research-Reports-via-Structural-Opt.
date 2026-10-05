import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"factcheck/src"),str(ROOT/"pdfparse/src")]
from jsonschema import Draft202012Validator
from yjcheck.models import Block, Document
from yjcheck.pipeline import check_documents


class OutputContractTests(unittest.TestCase):
    def test_text_review_uses_global_offsets_without_fabricated_pages(self):
        report=Document("report","a"*64,"r-run","report.docx","report",blocks=[
            Block("heading","报告标题",paragraph=1),Block("p","落款2025年2月30日。",paragraph=2)])
        result=check_documents(report,[])
        error=result["text_review"]["errors"][0]
        self.assertEqual(error["status"],"confirmed_error")
        for span in error["spans"]:
            self.assertEqual(report.text[span["start"]:span["end"]],span["text"])
        self.assertEqual(error["source_locations"][0]["block_id"],"p")
        self.assertIsNone(error["source_locations"][0]["page"])

    def test_low_quality_parse_never_auto_confirms_text_error(self):
        for issues,quality in ((["document:incomplete"],"ok"),([],"needs_review")):
            report=Document("report","a"*64,"r-run","report.pdf","report",
                            blocks=[Block("p","日期2025年2月30日。",page=3,status=quality)],issues=issues)
            result=check_documents(report,[])
            error=result["text_review"]["errors"][0]
            self.assertEqual(error["status"],"needs_review")
            self.assertEqual(error["validation"],"input_quality_requires_review")

    def test_three_statuses_validate_for_public_consumer(self):
        schema=json.loads((ROOT/"factcheck/schemas/check_result.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator=Draft202012Validator(schema)
        source=Document("source","b"*64,"s-run","source.docx","source","测试公司","2024FY",[
            Block("h1","2024年度合并利润表",paragraph=1),Block("h2","单位：万元",paragraph=2),
            Block("h3","项目 2024年度 2023年度",paragraph=3),Block("v1","营业收入 100 90",paragraph=4)])
        statuses=[]
        for text in ("2024年营业收入100万元。","2024年营业收入200万元。","2024年货币资金300万元。"):
            report=Document("report","a"*64,"r-run","report.docx","report","测试公司","2024FY",[Block("p",text,paragraph=1)])
            result=check_documents(report,[source])
            validator.validate(result)
            statuses.append(result["findings"][0]["status"])
        self.assertEqual(statuses,["no_issue","confirmed_error","needs_review"])

    def test_report_table_assertions_are_checked_without_prose(self):
        def document(role,value):
            return Document(role,("a" if role=="report" else "b")*64,role+"-run",role+".pdf",role,"测试公司","2024FY",[
                Block("title","测试公司2024年度合并利润表",page=1,bbox=[20,20,350,40],type="heading"),
                Block("unit","单位：万元",page=1,bbox=[20,45,250,60]),
                Block("table","",page=1,bbox=[20,80,400,140],type="table",cells=[
                    {"row":0,"col":0,"text":"项目"},{"row":0,"col":1,"text":"2024年度"},
                    {"row":1,"col":0,"text":"营业收入"},{"row":1,"col":1,"text":value}])])
        result=check_documents(document("report","123"),[document("source","100")])
        errors=[f for f in result["findings"] if f["status"]=="confirmed_error"]
        self.assertEqual(len(errors),1)
        self.assertEqual(errors[0]["suggested_value"],"100")


if __name__=="__main__":unittest.main()
