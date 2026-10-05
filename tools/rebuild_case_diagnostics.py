"""Rebuild local diagnostic evidence and HTML from compact tracked notes.

The frozen package and authored analysis remain read-only. Upstream eval_data
is optional; without it the resulting page explicitly reports not_available.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_case_diagnostics import export
from complete_case_evidence import complete
from render_case_diagnostics import href, render


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE = ROOT / "artifacts/research442-rerun-20261005"
DEFAULT_NOTES = ROOT / "artifacts/case-diagnostics-20261005"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rebuild(package, notes, out, original_source=None, supplemental_selection=None):
    package, notes, out = Path(package).resolve(), Path(notes).resolve(), Path(out).resolve()
    if out == package or out.is_relative_to(package) or out == notes or out.is_relative_to(notes):
        raise ValueError("Rebuild output must be outside both the frozen package and the authored notes")
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite diagnostic material: {out}")
    chosen = read(notes / "selection.json")["cases"]
    for case in chosen:
        path = notes / "cases" / f"{case['case_id']}.analysis.json"
        if read(path).get("case_id") != case["case_id"]:
            raise ValueError(f"Authored case identity mismatch: {case['case_id']}")
    if supplemental_selection is None and (notes / "supplemental_selection.json").is_file():
        supplemental_selection = notes / "supplemental_selection.json"
    if supplemental_selection is not None:
        supplemental_selection = Path(supplemental_selection).resolve()
        if not supplemental_selection.is_file():
            raise ValueError("Supplemental selection file does not exist")
    export(package, out, supplemental_selection=supplemental_selection)
    rebuilt = read(out / "selection.json")["cases"]
    if rebuilt != chosen:
        raise ValueError("Rebuilt case identities differ from the authored selection; no analysis was copied")
    for case in chosen:
        name = f"{case['case_id']}.analysis.json"
        shutil.copyfile(notes / "cases" / name, out / "cases" / name)
    for name in ("README.md", "meeting.md", "human_review.jsonl", "typicality.md", "typicality.json"):
        source = notes / name
        if source.is_file():
            shutil.copyfile(source, out / name)
            if name.endswith(".md"):
                # Tracked notes live two levels below the repository. Rebase only
                # their repository-doc links when --out uses another location.
                text = (out / name).read_text(encoding="utf-8")
                rebased = re.sub(r"(?<=\]\()\.\./\.\./docs/([^\s)]+)",
                              lambda m: href(out, ROOT / "docs" / m[1].split("#", 1)[0],
                                             "#" + m[1].split("#", 1)[1] if "#" in m[1] else ""), text)
                if rebased != text:
                    (out / name).write_text(rebased, encoding="utf-8", newline="\n")
    if supplemental_selection is not None:
        shutil.copyfile(supplemental_selection, out / "supplemental_selection.json")
    complete(package, out, Path(original_source) if original_source is not None else None)
    render(out, package=package)
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--notes", type=Path, default=DEFAULT_NOTES)
    parser.add_argument("--out", type=Path, required=True, help="New local output directory")
    parser.add_argument("--original-source", type=Path)
    parser.add_argument("--supplemental-selection", type=Path,
                        help="Defaults to supplemental_selection.json in the notes, when present")
    args = parser.parse_args()
    rebuild(args.package, args.notes, args.out, args.original_source, args.supplemental_selection)


if __name__ == "__main__":
    main()
