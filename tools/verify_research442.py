"""Verify hashes, replay frozen scoring, and export every all-hints TP/FP/FN offline."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="New verification output directory")
    args = parser.parse_args()
    package = args.package.resolve()
    for row in read(package / "manifest.json"):
        path = (package / row["path"]).resolve()
        if not path.is_relative_to(package) or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError(f"Hash mismatch: {row['path']}")
    args.out.mkdir(parents=True, exist_ok=False)
    subprocess.run([sys.executable, str(package / "source/evals/run_v2.py"), "score",
                    "--inputs", str(package / "inputs/inputs.research442.jsonl"),
                    "--gold", str(package / "inputs/gold.research442.jsonl"),
                    "--runs", str(package / "run"), "--out", str(args.out / "score.json")], check=True)
    replay = read(args.out / "score.json")
    original = read(package / "run/score.json")
    if replay != original:
        raise ValueError("Frozen-score replay differs from original score")
    sys.path[:0] = [str(package / "source/evals"), str(package / "source/factcheck/src")]
    from run_v2 import validate_predictions
    from yjcheck.review_hints import all_review_hints
    from fined_bench_eval import _maximum_matching, _contains_error
    inputs = {r['doc_id']: r for r in map(json.loads, (package / 'inputs/inputs.research442.jsonl').read_text(encoding='utf-8').splitlines())}
    gold = {r['document_id']: r for r in map(json.loads, (package / 'inputs/gold.research442.jsonl').read_text(encoding='utf-8').splitlines())}
    cases, totals = [], {}
    for arm in original['detectors']:
        counts = Counter()
        for path in sorted((package / 'run/predictions' / arm).glob('*.json')):
            report = read(path)
            did = report['document_id']
            preds = all_review_hints(validate_predictions(report, inputs[did]['content']))['errors']
            targets = [g for g in gold[did]['errors'] if g.get('scorable', True)]
            pairs = _maximum_matching(preds, targets, lambda p,g: _contains_error(p,g) and p.get('error_type',p.get('type')) == g.get('type',g.get('error_type')))
            matched_p, matched_g = {id(p) for p,g in pairs}, {id(g) for p,g in pairs}
            entries = [('TP',p,g) for p,g in pairs] + [('FP',p,None) for p in preds if id(p) not in matched_p] + [('FN',None,g) for g in targets if id(g) not in matched_g]
            for label,p,g in entries:
                counts[label] += 1
                cases.append({'arm':arm,'document_id':did,'result':label,
                              'prediction_file':path.relative_to(package).as_posix(),
                              'all_hints_index':next((i for i,x in enumerate(preds) if x is p),None),
                              'gold_index':next((i for i,x in enumerate(gold[did]['errors']) if x is g),None),
                              'prediction':p,'gold':g,'root_cause':'pending_human_review'})
        expected = original['detectors'][arm]['all_review_hints_detection']
        for label,key in [('TP','true_positive'),('FP','false_positive'),('FN','false_negative')]:
            if counts[label] != expected[key]:
                raise ValueError(f'Case totals differ: {arm} {label}')
        totals[arm] = dict(counts)
    (args.out / 'cases.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in cases),encoding='utf-8')
    summary = {'hashes_verified':True,'frozen_score_exact_match':True,'all_hints_case_counts':totals,
               'note':'New inference batch; old deleted batch not recovered. Root causes are not human-reviewed.'}
    (args.out / 'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    hashes = [{'path': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
              for p in sorted(args.out.iterdir()) if p.is_file()]
    (args.out / 'manifest.json').write_text(json.dumps(hashes,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
