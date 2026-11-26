"""Freeze a deduplicated A-text sample from local source-mapped slices."""
from __future__ import annotations
import argparse
from collections import Counter
from pathlib import Path
import random
import re
import shutil

from scripts.local_corpus_slicer import read_rows, write_rows, write_json, write_preview


def exclusion_reason(row):
    if row['speaker_candidate']!='hanser':return 'unknown_speaker'
    risks=row['risk_flags']
    if any(f.startswith('hard_length_split') for f in risks):return 'hard_length_split'
    if 'unclosed_quote' in risks:return 'unclosed_quote'
    if 'qa_number_mismatch' in risks:return 'qa_number_mismatch'
    if 'stage_direction_possible' in risks:return 'stage_direction'
    text=row['text']
    if re.search(r'[（(）)]',text):return 'parenthetical_content_requires_check'
    if re.search(r'第[一二三四五六七八九十百零0-9]+封|\bL\d+\s*[:：；;]|毛怪\d+\s*[:：；;]|(?:^|[。！？!?\s])(?:总结|内容|投稿人)\s*[:：]',text):return 'submission_record_requires_check'
    if re.fullmatch(r'\s*(?:晚安歌\s*)?《[^》]+》\s*(?:(?:——|—|--)?\s*by\s*[:：].*)?',text,re.I):return 'performance_title_only'
    if re.search(r'(?<![A-Za-z])(?:Q\d{0,2}|A\d{0,2}|弹幕|校对|负责记录|负责校对)\s*[:：]|#@p@#|<!--|-->',text,re.I):return 'residual_structure_marker'
    if re.search(r'(?:憨憨|憨|H|包包|蛀牙|M\d{1,2})\s*[:：]|[（(]弹幕[)）]\s*[:：]|(?:B站|斗鱼)\s*ID\s*[:：]|\bL\d+\s*[:：]',text,re.I):return 'residual_role_or_submission'
    if re.search(r'/[^/\r\n]{1,160}/',text) and not re.search(r'https?://',text):return 'slash_quoted_source'
    if re.fullmatch(r'(?:BV[A-Za-z0-9]+|[\d:：.,；;\s]+)',text):return 'non_speech_record'
    if re.search(r'\d{1,2}[:：]\d{2}',text) and not re.search(r'(?:时间是|约你|凌晨|晚上|早上|下午|上午|中午|从|到)\s*\d{1,2}[:：]\d{2}',text):return 'unresolved_clock_marker'
    if re.search(r'(?<!\d)\d{1,2}[:：.]\d{2}\s*[；;]',text):return 'residual_recording_marker'
    if re.search(r'[（(]\s*(?:详情|注[:：]|编辑|校对|字幕|口技中|憨笑|系统通知)',text):return 'residual_editor_note'
    return None


def prepare(source: Path,output: Path,pilot: Path,count=20000,seed=2026101305):
    if (output/'inputs.frozen.jsonl').exists():raise FileExistsError('Use a new output directory')
    output.mkdir(parents=True,exist_ok=True)
    rows=read_rows(source/'candidates.jsonl')
    excluded=[];pool=[];seen={};duplicates=[]
    for row in rows:
        reason=exclusion_reason(row)
        if reason:
            excluded.append({'unit_id':row['unit_id'],'source_id':row['source_id'],'reason':reason})
        elif row['text'] in seen:
            duplicates.append({'unit_id':row['unit_id'],'canonical_unit_id':seen[row['text']]})
        else:
            seen[row['text']]=row['unit_id'];pool.append(row)
    if len(pool)<count:raise ValueError(f'Only {len(pool)} unique eligible A texts; requested {count}')
    random.Random(seed).shuffle(pool)
    selected=[{**r,'sample_no':i} for i,r in enumerate(pool[:count],1)]
    write_rows(output/'inputs.frozen.jsonl',selected)
    write_rows(output/'preparation_exclusions.jsonl',excluded)
    write_rows(output/'duplicate_lineage.jsonl',duplicates)
    for filename in ['rule.frozen.txt','labels.frozen.json']:
        shutil.copyfile(pilot/filename,output/filename)
    write_json(output/'selection_manifest.json',{
        'source':str(source.resolve()),'input_documents':len(read_rows(source/'sources.snapshot.jsonl')),
        'all_candidates':len(rows),'unique_eligible_pool':len(pool),'mechanical_exclusions':dict(Counter(r['reason'] for r in excluded)),
        'exact_text_duplicates':len(duplicates),'selected':count,'seed':seed,
        'selected_documents':len({r['source_id'] for r in selected}),
        'selected_basis':dict(Counter(r['attribution_basis'] for r in selected)),
        'selected_qa_status':dict(Counter(r['qa_status'] for r in selected)),
        'model_input':'exact A only; source Q retained locally but not sent',
        'selection':'fixed random sample without replacement; no third-party-name or expected-label filtering',
        'rule_reused_from':str(pilot.resolve()),'source_split':'corpus_build',
        'runtime_approved':0,'fact_auto_approval':False,'production_modified':False})
    audit=random.Random(seed+1).sample(selected,200)
    write_rows(output/'preprocessing_audit_200.jsonl',audit)
    write_preview(output,audit)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--pilot',type=Path,required=True)
    parser.add_argument('--count',type=int,default=20000)
    args=parser.parse_args()
    prepare(args.source,args.output,args.pilot,args.count)
