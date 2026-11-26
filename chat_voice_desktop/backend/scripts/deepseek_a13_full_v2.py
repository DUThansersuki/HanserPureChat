"""Label every eligible existing A slice, without sampling or re-extraction.

prepare is local and reuses the 100 identical-rule pilot predictions.
run uses deepseek_a13_labels; finalize checks complete provenance and exports.
"""
from __future__ import annotations

import argparse
from collections import Counter
import html
import json
from pathlib import Path
import shutil

from scripts.deepseek_a13_labels import LABELS, parse_number
from scripts.prepare_a13_bulk import exclusion_reason
from scripts.local_corpus_slicer import read_rows, write_json, write_rows

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'audit_artifacts/local_corpus_full_20261013_004'
OLD = ROOT / 'audit_artifacts/deepseek_a13_bulk_20261013_004'
PILOT = ROOT / 'audit_artifacts/deepseek_a13_optimization_20261016_001'
REVIEW = ROOT / 'audit_artifacts/deepseek_a13_sample_review_20261013_001'
DEFAULT_OUTPUT = ROOT / 'audit_artifacts/deepseek_a13_full_v2_20261016_001'


def prepare(output):
    output.mkdir(exist_ok=False)
    candidates = read_rows(SOURCE / 'candidates.jsonl')
    known_bad = json.loads((OLD / 'input_quarantine_ids.json').read_text(encoding='utf-8'))
    known_bad.update(json.loads((REVIEW / 'new_input_issue_ids.json').read_text(encoding='utf-8')))
    excluded, duplicates, pool, seen = [], [], [], {}
    for row in candidates:
        reason = exclusion_reason(row)
        if reason:
            excluded.append({'unit_id': row['unit_id'], 'source_id': row['source_id'], 'reason': reason})
        elif row['text'] in seen:
            duplicates.append({'unit_id': row['unit_id'], 'canonical_unit_id': seen[row['text']]})
        else:
            seen[row['text']] = row['unit_id']
            pool.append(row)
    quarantine = [{**r, 'quarantine_reason': known_bad[r['unit_id']]} for r in pool if r['unit_id'] in known_bad]
    inputs = [{**r, 'sample_no': i} for i, r in enumerate(
        (r for r in pool if r['unit_id'] not in known_bad), 1)]
    assert len(candidates) == len(excluded) + len(duplicates) + len(pool)
    old_inputs = {r['unit_id']: r for r in read_rows(OLD / 'inputs.frozen.jsonl')}
    input_by_id = {r['unit_id']: r for r in inputs}
    assert set(old_inputs) - set(input_by_id) == set(known_bad)
    # Same frozen rule AND exact A text are required to reuse a paid prediction.
    pilot_inputs = {r['unit_id']: r for r in read_rows(PILOT / 'inputs.frozen.jsonl')}
    reused = []
    for result in read_rows(PILOT / 'results.jsonl'):
        row = input_by_id[result['unit_id']]
        assert row['text'] == pilot_inputs[row['unit_id']]['text']
        assert parse_number(result['raw_output']) == result['label_number']
        reused.append({**result, 'sample_no': row['sample_no'], 'reused_from': str(PILOT),
                       'original_sample_no': result['sample_no']})
    assert len(reused) == 100
    for name in ('rule.frozen.txt', 'labels.frozen.json'):
        shutil.copyfile(PILOT / name, output / name)
    write_rows(output / 'inputs.frozen.jsonl', inputs)
    write_rows(output / 'results.jsonl', reused)
    write_rows(output / 'reused_results.jsonl', reused)
    write_rows(output / 'preparation_exclusions.jsonl', excluded)
    write_rows(output / 'duplicate_lineage.jsonl', duplicates)
    write_rows(output / 'input_quarantine.jsonl', quarantine)
    write_json(output / 'selection_manifest.json', {
        'rule_version': 'deepseek-a13-object-behavior-v2', 'source': str(SOURCE),
        'selection': 'ALL existing mechanically eligible unique A slices; no sampling, extraction or reslicing',
        'input_documents': len(read_rows(SOURCE / 'sources.snapshot.jsonl')),
        'all_candidates': len(candidates), 'unique_eligible_before_known_quarantine': len(pool),
        'mechanical_exclusions': dict(Counter(r['reason'] for r in excluded)),
        'exact_text_duplicates': len(duplicates), 'known_input_quarantine': len(quarantine),
        'selected': len(inputs), 'selected_documents': len({r['source_id'] for r in inputs}),
        'selected_basis': dict(Counter(r['attribution_basis'] for r in inputs)),
        'selected_qa_status': dict(Counter(r['qa_status'] for r in inputs)),
        'prior_20000_retained': len(set(old_inputs) & set(input_by_id)),
        'previously_unsampled_included': len(set(input_by_id) - set(old_inputs)),
        'reused_pilot_successes': len(reused), 'new_calls_required': len(inputs) - len(reused),
        'rule_reused_from': str(PILOT), 'input_text_modified': False,
        'source_prior_accepted_by_existing_user_policy': True, 'source_split': 'corpus_build',
        'model_input': 'fixed system rule first; exact A only as final user message; no Q or source context',
        'sampling': False, 'source_results_modified': False, 'production_modified': False,
    })
    print(json.dumps({'output': str(output), 'total': len(inputs), 'reused': len(reused),
                      'new_calls': len(inputs)-len(reused), 'quarantine': len(quarantine)}, ensure_ascii=False))


def audit_metrics(rows, results, label_field):
    valid = [r for r in rows if r['acceptable_labels'] and r['unit_id'] in results]
    def label(r):
        return r['predicted_label'] if label_field == 'baseline' else results[r['unit_id']]['label_number']
    decidable = [r for r in valid if r['third_party_gate'] in ('keep', 'reject')]
    return {'n': len(valid), 'accepted': sum(label(r) in r['acceptable_labels'] for r in valid),
            'false_13': sum(label(r) == 13 and r['third_party_gate'] == 'keep' for r in decidable),
            'missed_13': sum(label(r) != 13 and r['third_party_gate'] == 'reject' for r in decidable)}


def previews(output, tagged):
    # Plain static pages keep every source mapped item inspectable without a server.
    style = '<style>body{max-width:1100px;margin:30px auto;font:16px/1.65 sans-serif}article{border:1px solid #ddd;padding:16px;margin:15px 0}pre{white-space:pre-wrap;font:inherit}td,th{padding:10px;text-align:left;border-bottom:1px solid #ddd}small{color:#576575}</style>'
    table = []
    for label in LABELS:
        group = [r for r in tagged if r['label_number'] == label]
        links = []
        for start in range(0, len(group), 500):
            filename = f'label_{label:02d}_{start//500+1:02d}.html'
            links.append(f'<a href="{filename}">{start+1}–{min(start+500,len(group))}</a>')
            blocks = []
            for row in group[start:start+500]:
                esc = lambda value: html.escape(str(value))
                blocks.append(f'<article><b>{row["sample_no"]} · {label} {esc(row["label_name"])}</b><pre>{esc(row["text"])}</pre>'
                              f'<small>{esc(row["filename"])} · {esc(row["unit_id"])} · {esc(row["attribution_basis"])} · 字符{row["reaction_source_start_char"]}–{row["reaction_source_end_char"]}</small>'
                              f'<details><summary>来源与本地Q</summary><pre>前：{esc(row["source_context_before"])}\nQ：{esc(row["explicit_source_trigger"])}\n后：{esc(row["source_context_after"])}</pre></details></article>')
            (output / filename).write_text('<!doctype html><meta charset="utf-8">'+style+'<p><a href="REVIEW.html">返回汇总</a></p>'+''.join(blocks), encoding='utf-8')
        table.append(f'<tr><td>{label}</td><td>{html.escape(LABELS[label][0])}</td><td>{len(group)}</td><td>{len(group)/len(tagged):.2%}</td><td>{" · ".join(links)}</td></tr>')
    (output / 'REVIEW.html').write_text('<!doctype html><meta charset="utf-8"><title>全部A语料v2分类</title>'+style+
        f'<h1>全量{len(tagged):,}条A语料 · v2</h1><p>全部既有合格去重A，无抽样；标签候选不等于正式Style/Fact批准。</p><p><a href="RESULT_REVIEW.md">结果报告</a> · <a href="full_validation.json">完整性验证</a></p>'+
        '<table><tr><th>编号</th><th>标签</th><th>数量</th><th>占比</th><th>全部原文</th></tr>'+''.join(table)+'</table>', encoding='utf-8')


def finalize(output):
    inputs = read_rows(output / 'inputs.frozen.jsonl')
    results = read_rows(output / 'results.jsonl')
    by_id = {r['unit_id']: r for r in results}
    manifest = json.loads((output / 'selection_manifest.json').read_text(encoding='utf-8'))
    assert len(inputs) == len(results) == len(by_id) == manifest['selected']
    assert set(by_id) == {r['unit_id'] for r in inputs}
    assert all(parse_number(r['raw_output']) == r['label_number'] for r in results)
    assert (output / 'rule.frozen.txt').read_bytes() == (PILOT / 'rule.frozen.txt').read_bytes()
    source = {r['unit_id']: r for r in read_rows(SOURCE / 'candidates.jsonl')}
    for row in inputs:
        for key in ('text', 'source_id', 'source_spans', 'reaction_source_start_char', 'reaction_source_end_char'):
            assert row[key] == source[row['unit_id']][key]
        assert by_id[row['unit_id']]['sample_no'] == row['sample_no']
    calls = read_rows(output / 'call_records.jsonl')
    dispatch = read_rows(output / 'dispatch_records.jsonl')
    reused = read_rows(output / 'reused_results.jsonl')
    new_ids = set(by_id) - {r['unit_id'] for r in reused}
    assert {r['unit_id'] for r in calls if r['status'] == 'success'} == new_ids
    assert Counter((r['request_id'], r['state']) for r in dispatch) == Counter(
        (r['request_id'], state) for r in calls for state in ('started', 'finished'))
    provider = json.loads((output / 'provider_manifest.json').read_text(encoding='utf-8'))
    original_provider = json.loads((PILOT / 'provider_manifest.json').read_text(encoding='utf-8'))
    for key in ('provider', 'model', 'think', 'temperature', 'max_tokens', 'json_mode'):
        assert provider[key] == original_provider[key]
    tagged = [{**r, **by_id[r['unit_id']]} for r in inputs]
    write_rows(output / 'tagged_candidates.jsonl', tagged)
    write_json(output / 'label_pool_ids.json', {str(label): [r['unit_id'] for r in tagged if r['label_number'] == label] for label in LABELS})
    (output / 'numbers.txt').write_text(''.join(str(r['label_number'])+'\n' for r in tagged), encoding='utf-8')
    sources = read_rows(SOURCE / 'sources.snapshot.jsonl')
    excluded = read_rows(output / 'preparation_exclusions.jsonl')
    duplicates = read_rows(output / 'duplicate_lineage.jsonl')
    quarantine = read_rows(output / 'input_quarantine.jsonl')
    assert len(source) == len(inputs) + len(excluded) + len(duplicates) + len(quarantine)
    coverage = []
    for doc in sources:
        sid = doc['source_id']
        coverage.append({'source_id': sid, 'filename': doc['filename'],
                         'raw_candidates': sum(r['source_id'] == sid for r in source.values()),
                         'labeled_unique_A': sum(r['source_id'] == sid for r in inputs),
                         'mechanical_exclusions': dict(Counter(r['reason'] for r in excluded if r['source_id'] == sid)),
                         'duplicate_aliases': sum(source[r['unit_id']]['source_id'] == sid for r in duplicates),
                         'known_input_quarantine': sum(r['source_id'] == sid for r in quarantine)})
    assert sum(r['raw_candidates'] for r in coverage) == len(source)
    write_rows(output / 'source_coverage.jsonl', coverage)
    write_rows(output / 'duplicate_label_aliases.jsonl', [
        {**r, 'label_number': by_id[r['canonical_unit_id']]['label_number'],
         'label_name': by_id[r['canonical_unit_id']]['label_name'], 'source': 'canonical exact-text duplicate; no new model call'}
        for r in duplicates if r['canonical_unit_id'] in by_id])
    old = {r['unit_id']: r for r in read_rows(OLD / 'results.jsonl')}
    common = set(by_id) & set(old)
    transitions = Counter((old[uid]['label_number'], by_id[uid]['label_number']) for uid in common)
    comparison = {'common_with_old_20000': len(common), 'previously_unsampled': len(by_id)-len(common),
                  'same_label': sum(a == b for uid in common for a, b in [(old[uid]['label_number'], by_id[uid]['label_number'])]),
                  'old_13_to_retained': sum(old[uid]['label_number'] == 13 and by_id[uid]['label_number'] != 13 for uid in common),
                  'old_retained_to_13': sum(old[uid]['label_number'] != 13 and by_id[uid]['label_number'] == 13 for uid in common),
                  'transition_counts': {f'{a}->{b}': n for (a,b),n in sorted(transitions.items())},
                  'label_change_is_not_correctness': True}
    write_json(output / 'baseline_comparison.json', comparison)
    reviewed = read_rows(REVIEW / 'reviewed_samples.jsonl')
    audited = {}
    for cohort in ('random', 'rare_label_supplement'):
        group = [r for r in reviewed if r['cohort'] == cohort]
        audited[cohort] = {'baseline': audit_metrics(group, by_id, 'baseline'), 'v2': audit_metrics(group, by_id, 'v2')}
    audited['caution'] = 'Existing development audit reused; overlaps prompt development and 100-case pilot, NOT independent blind accuracy.'
    write_json(output / 'existing_audit_comparison.json', audited)
    validation = {'total': len(inputs), 'all_eligible_included_without_sampling': True, 'same_exact_A_and_source_spans': True,
                  'source_documents_accounted': len(coverage), 'all_source_candidates_accounted': len(source),
                  'unique_complete_numeric_outputs': True, 'same_frozen_v2_rule_and_model': True,
                  'reused_pilot_results': len(reused), 'new_success_ids': len(new_ids), 'new_provider_records': len(calls),
                  'failed_records': sum(r['status'] != 'success' for r in calls), 'remaining': 0,
                  'all_previously_failed_ids_recovered': all(r['unit_id'] in by_id for r in calls if r['status'] != 'success'),
                  'dispatch_closed': True,
                  'source_results_modified': False, 'production_modified': False}
    write_json(output / 'full_validation.json', validation)
    previews(output, tagged)
    counts = Counter(r['label_number'] for r in tagged)
    usage = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
    report = f'''# 全部既有A语料：v2分类完成

2026-10-16。直接复用已完成的507篇全文切片，不重新抽取、切片或抽样。34,359条来源候选按原清洗门槛有22,204条合格去重A；9条此前已确认的输入残留单列保留待修，全部其余{len(inputs):,}条完成v2分类。原20,000条之外的{comparison['previously_unsampled']:,}条全部纳入。

沿用100条试验冻结的对象／行为规则v2，原API与deepseek-flash不变，每次system固定规则在前、精确A在后，只返回1—13。原100条同规则同文本结果复用，新增成功分类{len(new_ids):,}条；旧结果、来源、生产索引均保留。

| 编号 | 标签 | 数量 | 占比 |
| --- | --- | ---: | ---: |
'''
    report += '\n'.join(f'| {n} | {LABELS[n][0]} | {counts[n]:,} | {counts[n]/len(inputs):.2%} |' for n in LABELS)
    report += f'''

1—12共{len(inputs)-counts[13]:,}条，13共{counts[13]:,}条。候选标签不代表已通过Style可迁移性或事实核验；13包含多种原因，不自动整池进入Fact。

与旧版可比的{len(common):,}条中，同标签{comparison['same_label']:,}条；旧13→1—12为{comparison['old_13_to_retained']:,}条，旧1—12→13为{comparison['old_retained_to_13']:,}条。变化量不等于修复量。

已有随机开发审查的可评分{audited['random']['v2']['n']}条，可接受{audited['random']['baseline']['accepted']}→{audited['random']['v2']['accepted']}；稀有标签补充可评分{audited['rare_label_supplement']['v2']['n']}条，可接受{audited['rare_label_supplement']['baseline']['accepted']}→{audited['rare_label_supplement']['v2']['accepted']}。这些样本已参与问题发现，且部分与100条试验重叠，不是独立盲测总体准确率。后续独立抽样再判断实际质量。

同一随机开发样本中，误判13为{audited['random']['baseline']['false_13']}→{audited['random']['v2']['false_13']}，第三方漏拦{audited['random']['baseline']['missed_13']}→{audited['random']['v2']['missed_13']}。总体标签可接受改善不代表每项指标改善，第三方漏拦仍需进一步优化。

新增调用账本已回报输入{usage['prompt_tokens']:,}、输出{usage['completion_tokens']:,}，总{usage['total_tokens']:,}tokens，缓存命中{usage['input_cache_hit_tokens']:,}；不含此前100条试验费用。{len(calls):,}条provider记录，其中首轮失败{validation['failed_records']}条，均已补跑成功，未完成0，dispatch全部收口。全部唯一ID、精确文本／来源跨度、冻结规则、模型参数及数字输出已验证，没有新增外部Judge。

完整性范围：507篇corpus_build均扫描；现有冻结holdout/excluded划分、未知说话人及原机械排除条目保留原用途，不把非Hanser／未确认多人发言当作A。重复74条保留lineage；9条确认残留完整保留在input_quarantine.jsonl，未直接丢弃原文。

[全部标签原文](H:/HanserAgent/{output.relative_to(ROOT).as_posix()}/REVIEW.html) · [完整性核验](H:/HanserAgent/{output.relative_to(ROOT).as_posix()}/full_validation.json) · [与旧版转移矩阵](H:/HanserAgent/{output.relative_to(ROOT).as_posix()}/baseline_comparison.json) · [既有开发审查对照](H:/HanserAgent/{output.relative_to(ROOT).as_posix()}/existing_audit_comparison.json)
'''
    (output / 'RESULT_REVIEW.md').write_text(report, encoding='utf-8')
    print(json.dumps({'validation': validation, 'labels_1_to_12': len(inputs)-counts[13], 'label_13': counts[13],
                      'usage_new_tokens': usage['total_tokens'], 'old_comparison': {k:v for k,v in comparison.items() if k != 'transition_counts'},
                      'existing_audit': audited}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'finalize'])
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    (prepare if args.action == 'prepare' else finalize)(args.output)
