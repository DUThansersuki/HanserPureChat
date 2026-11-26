"""Recover explicit Chinese quiz Q/A records without attributing keys to Hanser."""
from __future__ import annotations
import argparse
from pathlib import Path
import re
from scripts.local_corpus_slicer import read_rows,write_rows,write_json

QUESTION=re.compile(r'^\s*(?:题(?:目)?(?:\d+|[一二三四五六七八九十]+)|进阶(?:题目|题)?)\s*[:：]\s*')
ANSWER=re.compile(r'^\s*(?:答案|答)\s*[:：]\s*')
STOP=re.compile(r'^\s*(?:\d{1,2}[:：.,]\d{2}|<!--|<第|Title[:：]|校对[:：]|回溯负责[:：])',re.I)


def extract(document):
    text=document['text'];lines=[];offset=0
    for line in text.splitlines(keepends=True):
        lines.append((offset,line));offset+=len(line)
    records=[]
    def save(qstart,qend,astart,aend,basis):
        while qstart<qend and text[qstart].isspace():qstart+=1
        while qend>qstart and text[qend-1].isspace():qend-=1
        while astart<aend and text[astart].isspace():astart+=1
        while aend>astart and text[aend-1].isspace():aend-=1
        if qstart==qend or astart==aend:return
        records.append({'qa_id':f"LQA1-{document['source_id']}-{qstart:07d}-{astart:07d}",
            'source_id':document['source_id'],'filename':document['filename'],'record_kind':'quiz_answer_key',
            'question_text':text[qstart:qend],'answer_text':text[astart:aend],
            'question_source_spans':[{'start':qstart,'end':qend}],
            'answer_source_spans':[{'start':astart,'end':aend}],
            'pair_basis':basis,'answer_speaker':'unverified_quiz_record',
            'answer_is_hanser_speech_verified':False,'runtime_eligible':False,'fact_auto_approved':False})
    for index,(start,line) in enumerate(lines):
        question=QUESTION.match(line)
        if question:
            following=index+1
            while following<len(lines) and not lines[following][1].strip():following+=1
            if following==len(lines):continue
            answer_start,answer_line=lines[following];answer=ANSWER.match(answer_line)
            if answer:
                end=answer_start+len(answer_line)
                for continuation_start,continuation in lines[following+1:]:
                    if not continuation.strip() or QUESTION.match(continuation) or ANSWER.match(continuation) or STOP.match(continuation):break
                    end=continuation_start+len(continuation)
                save(start+question.end(),start+len(line),answer_start+answer.end(),end,'explicit_chinese_question_answer_key')
        # In quiz documents // is an editorial answer key, not Hanser's A.
        if ('题库' in text or '问答赛道' in text) and '//' in line:
            marker=line.find('//');q=line[:marker].strip();a=line[marker+2:].strip()
            if q and a and not q.startswith(('<!--','<','//')) and ('？' in q or '?' in q or re.match(r'(?:请|参与|进阶|专辑|在家里|三位|四位|和谁|给B站|崩坏|海上|前几天|23年|是否)',q)):
                end=start+len(line)
                comment=line.find('<!--',marker+2)
                if comment>=0:end=start+comment
                save(start,start+marker,start+marker+2,end,'quiz_question_inline_editor_answer_key')
    return records


def run(output):
    documents=read_rows(output/'sources.snapshot.jsonl')
    records=[r for document in documents for r in extract(document)]
    write_rows(output/'quiz_qa_structure_records.jsonl',records)
    write_json(output/'quiz_qa_structure_summary.json',{'documents_scanned':len(documents),
        'explicit_quiz_key_pairs':len(records),'sources_with_pairs':len({r['source_id'] for r in records}),
        'source_split':'corpus_build','extra_hanser_A_candidates':0,'external_calls':0,
        'note':'Quiz keys may be editor summaries or audience answers; never automatically assigned to Hanser A or merged into Style.'})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
