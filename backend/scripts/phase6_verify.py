"""Offline integrity, lexical near-duplicate and baseline manifest checks."""
import ast
import hashlib
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from phase6_audit import OUT, ROOT, save


def main():
    snapshot = json.loads((OUT / 'snapshot.json').read_text(encoding='utf-8'))
    changed = [r['path'] for r in snapshot['files']
               if hashlib.sha256((ROOT / r['path']).read_bytes()).hexdigest() != r['sha256']]
    source_unchanged = hashlib.sha256(Path(snapshot['source_db']).read_bytes()).hexdigest() == snapshot['source_sha256']
    with sqlite3.connect(f'file:{(OUT / "audit.db").as_posix()}?mode=ro', uri=True) as conn:
        rows = conn.execute('SELECT id,response FROM style_examples').fetchall()
    normalized = [(i, re.sub(r'\W+', '', text)) for i, text in rows]
    grams = [(i, text, set(text[n:n+3] for n in range(len(text)-2))) for i, text in normalized if len(text) >= 12]
    near = []
    for index, (aid, a, ga) in enumerate(grams):
        for bid, b, gb in grams[index+1:]:
            if a == b or min(len(a), len(b)) / max(len(a), len(b)) < .75:
                continue
            score = len(ga & gb) / len(ga | gb)
            if score >= .75:
                near.append({'a': aid, 'b': bid, 'trigram_jaccard': score})
    save('near_duplicates.json', {'method': 'nonidentical normalized response character trigram Jaccard >= .75, min length 12; lexical screen, not semantic duplicate gold', 'pairs': near, 'count': len(near)})
    counts = {}
    for name in ['ablation_outputs.jsonl', 'runtime_traces.jsonl', 'topk_outputs.jsonl', 'pairwise_judgments.jsonl', 'human_review_samples.jsonl']:
        records = [json.loads(line) for line in (OUT / name).read_text(encoding='utf-8').splitlines() if line]
        counts[name] = {'rows': len(records), 'statuses': dict(Counter(r.get('status', str(r.get('http_status', 'sample'))) for r in records))}
    scripts = list((ROOT / 'backend/scripts').glob('phase6_*.py'))
    for path in scripts:
        ast.parse(path.read_text(encoding='utf-8'))
    reports = [ROOT / name for name in ['CURRENT_IMPLEMENTATION_AUDIT.md', 'PERSONA_SYSTEM_DEEP_REVIEW.md', 'CURRENT_SYSTEM_EVALUATION.md', 'NEXT_STAGE_ENGINEERING_GUIDE.md', 'PERSONA_REGRESSION_SUITE.md', 'ARCHITECTURE_SPEC_DELTA.md']]
    manifest_files = scripts + [p for p in reports if p.exists()] + [OUT / 'fixed_cases.json', OUT / 'frozen_contexts.jsonl', OUT / 'multi_turn_cases.json', OUT / 'judge_prompt.txt']
    save('final_verification.json', {'production_db_unchanged': source_unchanged, 'baseline_files_changed': changed, 'production_files_changed': [p for p in changed if not p.startswith('backend/scripts/phase6_')], 'counts': counts, 'scripts_syntax_checked': len(scripts), 'reports_present': {p.name:p.exists() for p in reports}, 'manifest': {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in manifest_files}})
    print(json.dumps({'production_db_unchanged': source_unchanged, 'baseline_files_changed': changed, 'near_duplicate_pairs': len(near), 'counts': counts, 'reports_present': all(p.exists() for p in reports)}, ensure_ascii=True))


if __name__ == '__main__':
    main()
