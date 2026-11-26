"""Source-mapped local reply slicing. Standard library only; no model gateway."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys

PROJECT = Path(__file__).resolve().parents[3]
VERSION = 'local-rule-reply-slicer-v14-full-corpus'
SOURCE_PRIOR_POLICY = 'hanser-source-prior-accepted-by-user-20261013'
PILOT_IDS = ['DOC-0001','DOC-0002','DOC-0006','DOC-0023','DOC-0003','DOC-0013','DOC-0032','DOC-0074']
HAN = {'憨','憨憨','憨色','hanser','han','h'}
AUDIENCE = {'弹幕','sc','superchat','from','投稿','邮件','来信'}
SPEAKER = re.compile(
    r'(?<![A-Za-z])(?P<name>Hanser|憨憨|憨色|憨|H|大毛|毛怪\d*|毛|茶理理|茶|七海|海|阿萨|小缘|缘|'
    r'yousa|Y|聂总|聂|三无|三|猫|Han|包包|蛀牙|切片组|鸟|菠萝|菠|嘉宾|主持|旁白|SuperChat|SC|应援弹幕|弹幕聊天|弹幕|From|投稿|邮件|来信|Q\d{0,2}|A\d{0,2}|S|T|M\d{0,2})\s*[:：；;]', re.I)
QUESTION_LINE = re.compile(r'^[ \t]*(?:-?\d{1,2}[:：.]\d{2}[；;]?[ \t]*)?(?P<name>问题(?:\d{1,2})?)\s*(?:[:：]|(?<=\d))')
TIME = re.compile(r'(?<![\dA-Za-z])-?\d{1,2}[:：.,]\d{2}(?:\s*[；;]|[:：](?!\d))?')
LINE_LABEL = re.compile(r'(?m)^[ \t]*(?:-?\d{1,2}[:：.]\d{2}[；;]?[ \t]*)?(?P<name>[A-Za-z]{1,12}|[\u4e00-\u9fff]{1,8})\s*[:：]')
COMMENTS = re.compile(r'<!--[\s\S]*?-->|/\*[\s\S]*?\*/')
PAREN = re.compile(r'（[^（）\r\n]{0,180}）|\([^()\r\n]{0,180}\)')
EDITOR_HINT = re.compile(r'^(?:详情|指(?:[：:]|[^向])|弹幕[：:]|ps[：:]|注[：:]|备注|后期|字幕|校对|'
                         r'录播|录屏|谢礼物|谢\s*sc|感谢(?:礼物|\s*sc)|谜语人|毕竟天使|狗头|弄大毛头发|乖乖去捡了|口技中|憨笑|系统通知|唱ing|笑场|害羞|战术后仰|导出的是|下次出|[？?]+$)',re.I)
HEADER = re.compile(r'^\s*(?:\d{4}年\d{1,2}月\d{1,2}日|期(?:号|数)[：:]|(?:Title|Tips|Notice)[：:]|本周话题[：:]|投稿到邮箱|'
                    r'记录(?:负责|员)?[：:]|校对(?:负责)?[：:]|负责(?:记录|校对)[：:]|笔记负责[：:]|标题[：:]|特别鸣谢[：:]|字幕[：:]|视频地址[：:]|投稿内容[：:]|温馨提示[：:]|'
                    r'L\d{1,3}\s*[:：]|第[一二三四五六七八九十]+(?:次|位)[：:]|憨憨出货统计[：:]|番外篇|[-—_=]{3,}|歌曲[：:]|歌词[：:]|[♪♫])',re.I)
SONG = re.compile(r'^\s*《[^》\r\n]+》\s*(?:[（(][^\r\n]*[）)])?\s*$')
TERMINAL = re.compile(r'[。！？!?；;]+[”’」』]*')
CONTINUATION = re.compile(r'^\s*(?:但是|不过|所以|然后|而且|因为|反正|还有|就是|也|那|但|其实|对了)')
ACK = {'嗯','哦','好','啊','哈','诶','唉','喔','哎','喂','对','是','不','行','能','有','没','谢','累'}


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')


def write_preview(output: Path, rows: list[dict]) -> None:
    data=json.dumps(rows,ensure_ascii=False).replace('<','\\u003c')
    page=r'''<!doctype html><meta charset="utf-8"><title>本地切片检查</title>
<style>body{font:15px system-ui;margin:24px;background:#f5f7fa}article{background:white;border:1px solid #d7dce3;border-radius:8px;padding:14px;margin:10px 0}pre{white-space:pre-wrap;font:inherit;line-height:1.6}select,input,button{padding:8px;margin:4px}small{color:#596779}</style>
<h1>本地原文切片候选</h1><p>按说话人、时间标记和连续句段切片。此页不是语义审核通过列表；原文坐标和未知归属保留。</p>
<select id="doc"><option value="">全部文档</option></select><select id="scope"><option value="">全部候选</option><option value="explicit_marker">明确标签</option><option value="source_prior">来源推定（已采用）</option><option value="unmarked_multispeaker">多人语境未知</option><option value="hard">长度切分风险</option></select><input id="q" placeholder="搜索原文或ID"><p id="stats"></p><button id="prev">上一页</button><button id="next">下一页</button><main id="items"></main>
<script>const rows=__DATA__,doc=document.getElementById('doc'),scope=document.getElementById('scope'),q=document.getElementById('q');let page=0;
for(const id of [...new Set(rows.map(r=>r.source_id))]){let o=document.createElement('option');o.value=id;o.textContent=id+' '+rows.find(r=>r.source_id===id).filename;doc.append(o)}
function render(){const needle=q.value.toLowerCase();let found=rows.filter(r=>(!doc.value||r.source_id===doc.value)&&(!scope.value||(scope.value==='hard'?r.risk_flags.some(f=>f.startsWith('hard_length_split')):r.attribution_basis===scope.value))&&(!needle||[r.text,r.unit_id,r.filename].join(' ').toLowerCase().includes(needle)));page=Math.max(0,Math.min(page,Math.ceil(found.length/25)-1));document.getElementById('stats').textContent=found.length+' 条 | 第 '+(page+1)+' 页';const target=document.getElementById('items');target.replaceChildren();for(const r of found.slice(page*25,page*25+25)){let a=document.createElement('article'),h=document.createElement('b'),p=document.createElement('pre'),s=document.createElement('small'),d=document.createElement('details'),t=document.createElement('summary'),raw=document.createElement('pre');h.textContent=r.unit_id+' | '+r.attribution_basis;p.textContent=r.text;s.textContent=r.filename+' | '+r.reaction_source_start_char+'..'+r.reaction_source_end_char+' | '+r.risk_flags.join(', ');t.textContent='原文及前后文';raw.textContent='前：'+r.source_context_before+'\n原始跨度：'+r.source_text+'\n后：'+r.source_context_after;d.append(t,raw);a.append(h,p,s,d);target.append(a)}}for(const e of [doc,scope,q])e.oninput=()=>{page=0;render()};document.getElementById('prev').onclick=()=>{page--;render()};document.getElementById('next').onclick=()=>{page++;render()};render();</script>'''
    (output/'REVIEW.html').write_text(page.replace('__DATA__',data),encoding='utf-8')


def reconstruct_documents(frozen: Path, selected_ids: list[str]) -> list[dict]:
    sources = {r['source_id']:r for r in read_rows(frozen/'sources.frozen.jsonl')}
    selected = set(selected_ids)
    if not selected <= sources.keys():
        raise ValueError('Unknown source IDs')
    if any(sources[sid]['split']!='corpus_build' for sid in selected):
        raise ValueError('Only corpus_build sources may enter this pilot')
    windows = defaultdict(list)
    for row in read_rows(frozen/'windows.frozen.jsonl'):
        if row['source_id'] in selected:
            windows[row['source_id']].append(row)
    documents = []
    for sid in selected_ids:
        text = ''
        for row in sorted(windows[sid],key=lambda r:r['source_start_char']):
            start,end,span = row['source_start_char'],row['source_end_char'],row['source_span']
            if start>len(text) or len(span)!=end-start:
                raise ValueError(f'{sid}: source window gap or invalid range')
            overlap = min(len(span),len(text)-start)
            if text[start:start+overlap]!=span[:overlap]:
                raise ValueError(f'{sid}: overlapping source windows disagree')
            text += span[overlap:]
        if len(text)!=sources[sid]['content_chars']:
            raise ValueError(f'{sid}: incomplete reconstructed source')
        documents.append({**sources[sid],'text':text})
    return documents


def atoms(text: str) -> list[dict]:
    """Preserve every character; classify only explicit mechanical boundaries."""
    exclusions = []
    for match in COMMENTS.finditer(text):
        exclusions.append((match.start(),match.end(),'editor_comment'))
    for match in re.finditer(r'[（(]弹幕[)）]\s*[:：]\s*[“「][^”」\r\n]*[”」]',text):
        exclusions.append((match.start(),match.end(),'quoted_audience_record'))
    for match in re.finditer(r'#@p@#',text):
        exclusions.append((match.start(),match.end(),'paragraph_separator'))
    for match in re.finditer(r'(?<![:/])//',text):
        if any(a<=match.start()<b for a,b,_ in exclusions):continue
        line_end=text.find('\n',match.end())
        if line_end<0:line_end=len(text)
        # Inline transcript comments end at the next explicit recording marker,
        # not at the end of a potentially enormous single-line transcript.
        endings=[line_end]
        for pattern in [TIME,SPEAKER]:
            next_marker=pattern.search(text,match.end(),line_end)
            if next_marker:endings.append(next_marker.start())
        end=min(endings)
        exclusions.append((match.start(),end,'inline_editor_comment'))
    # Balanced parentheses include multiline editor notes; spoken asides remain.
    stack=[];parentheses=[]
    for index,char in enumerate(text):
        if char in '(（':stack.append((index,char))
        elif char in ')）' and stack:
            start,_=stack.pop();parentheses.append((start,index+1))
    for start,end in parentheses:
        interior = text[start+1:end-1].strip()
        line_start=text.rfind('\n',0,start)+1
        line_end=text.find('\n',end)
        if line_end<0:line_end=len(text)
        prefix=TIME.sub('',text[line_start:start]).strip()
        if SPEAKER.fullmatch(prefix):prefix=''
        standalone = not prefix and not text[end:line_end].strip()
        editorial=EDITOR_HINT.search(interior) or SPEAKER.match(interior) or re.match(r'弹幕[)）]?\s*[:：]',interior)
        if editorial or standalone:
            exclusions.append((start,end,'editor_note' if editorial else 'standalone_parenthesis_unverified'))
    for match in re.finditer(r'<[^<>\r\n]{1,180}>',text):
        if not match.group().startswith('<!--'):
            exclusions.append((match.start(),match.end(),'editor_angle_note'))
    boundaries={0,len(text)}
    events={}
    offset=0
    for line in text.splitlines(keepends=True):
        left,right=offset,offset+len(line)
        offset=right
        boundaries.update([left,right])
        if HEADER.match(TIME.sub('',line,count=1)):
            exclusions.append((left,right,'document_metadata'))
            continue
        without_time=TIME.sub('',line).rstrip('\r\n')
        heading=without_time.split('//',1)[0].strip()
        if SONG.fullmatch(heading) or re.fullmatch(r'\s*晚安歌\s*《[^》]+》\s*',heading):
            exclusions.append((left,right,'performance_heading'))
            continue
        if re.fullmatch(r'\s*(?:小缘|憨憨|hanser|菠萝).*(?:谢完|谢礼物|拉人ing|发红包ing).*',without_time,re.I):
            exclusions.append((left,right,'editor_action_note'))
            continue
        for pattern,kind in [(SPEAKER,'speaker_marker'),(TIME,'timestamp')]:
            for match in pattern.finditer(line):
                if kind=='timestamp' and line[:match.start()].strip() and line[match.start()-1] not in '。！？!?；;':
                    # 00:xx / 01:xx are common inline recording markers. Keep
                    # explicit numerical facts and clock-time statements intact.
                    before=line[max(0,match.start()-8):match.start()]
                    recording=(bool(re.match(r'0\d?[:：]',match.group())) or match.group().rstrip().endswith(('；',';'))
                               or len(list(TIME.finditer(line)))>=3 or bool(SPEAKER.match(line,match.end())))
                    if not recording or re.search(r'(?:时间是|约你|凌晨|晚上|早上|下午|上午|中午|从|到)\s*$',before):
                        continue
                a,b=left+match.start(),left+match.end()
                boundaries.update([a,b]);events[(a,b)]={'kind':kind,'label':match.groupdict().get('name')}
        generic=LINE_LABEL.match(line)
        # Some source Q/A records use circled numbering and omit both colons.
        for match in re.finditer(r'(?:^[ \t]*|[①②③④⑤⑥⑦⑧⑨⑩])(?P<q>Q)[^A\r\n]+(?P<a>A)(?=[\u4e00-\u9fff])',line):
            for name in ('q','a'):
                a,b=left+match.start(name),left+match.end(name)
                boundaries.update([a,b]);events[(a,b)]={'kind':'speaker_marker','label':name.upper()}
        question_marker=QUESTION_LINE.match(line)
        if question_marker:
            a,b=left+question_marker.start('name'),left+question_marker.end()
            boundaries.update([a,b]);events[(a,b)]={'kind':'speaker_marker','label':question_marker.group('name')}
        if generic:
            start=left+generic.start('name');end=left+generic.end()
            label=generic.group('name')
            known=bool(SPEAKER.fullmatch(label+':'))
            natural_colon=label in {'我','他说','她说','问题','原因','说明','回答','注意','答案','备注'} or len(label)>3 and not label.isascii()
            if not any(a==start for a,b in events) and (known or not natural_colon):
                boundaries.update([start,end]);events[(start,end)]={'kind':'speaker_marker' if known else 'unknown_speaker_marker','label':label}
        for match in TERMINAL.finditer(line):
            boundaries.add(left+match.end())
    # A title occupying an entire recording segment is a performance heading.
    # Titles inside an actual spoken sentence are retained.
    time_ranges=sorted((a,b) for (a,b),event in events.items() if event['kind']=='timestamp')
    for index,(a,b) in enumerate(time_ranges):
        newline=text.find('\n',b)
        end=min(newline if newline>=0 else len(text),time_ranges[index+1][0] if index+1<len(time_ranges) else len(text))
        segment=text[b:end]
        if re.fullmatch(r'\s*(?:晚安歌\s*)?《[^》\r\n]+》\s*(?:(?:——|—|--)?\s*by\s*[:：]\s*[^\r\n]+)?\s*',segment,re.I):
            exclusions.append((b,end,'performance_heading'))
    for left,right,_ in exclusions:boundaries.update([left,right])
    positions=sorted(boundaries)
    result=[]
    for left,right in zip(positions,positions[1:]):
        covered=next((kind for a,b,kind in exclusions if a<=left and right<=b),None)
        event=events.get((left,right),{})
        kind=covered or event.get('kind') or ('space' if not text[left:right].strip() else 'speech')
        result.append({'start':left,'end':right,'text':text[left:right],'kind':kind,'label':event.get('label')})
    assert ''.join(r['text'] for r in result)==text
    return result


@dataclass
class SliceConfig:
    target_chars: int = 120
    max_chars: int = 240


def role(label: str) -> tuple[str,str]:
    label=label.casefold()
    if label in HAN or re.fullmatch(r'a\d{0,2}',label):return 'hanser','explicit_marker'
    if label in AUDIENCE or re.fullmatch(r'(?:q\d{0,2}|m\d{1,2}|问题\d{0,2}|毛怪\d*|应援弹幕|弹幕聊天)',label):return 'audience','explicit_marker'
    return label,'explicit_marker'


def open_quote(text: str) -> bool:
    return text.count('“')>text.count('”') or text.count('「')>text.count('」')


def slice_document(document: dict, config: SliceConfig | None = None) -> dict:
    config=config or SliceConfig()
    if not 1<=config.target_chars<=config.max_chars:
        raise ValueError('Require 1 <= target_chars <= max_chars')
    text=document['text'];source_id=document['source_id']
    pieces=atoms(text)
    named_others = {role(p['label'])[0] for p in pieces if p['kind']=='speaker_marker'}-{'hanser','audience'}
    if any(p['kind']=='unknown_speaker_marker' for p in pieces):named_others.add('unrecognized')
    default_role='unknown' if named_others else 'hanser'
    default_basis='unmarked_multispeaker' if named_others else 'source_prior'
    speaker,basis=default_role,default_basis
    speaker_label=None
    candidates=[];excluded=[];group=[];flags=set();pending_trigger=None;active_trigger=None

    def emit():
        nonlocal group,flags,pending_trigger,active_trigger
        if not group:return
        spans=[dict(p) for p in group]
        # Strip boundary whitespace by changing source ranges, never by inventing text.
        while spans and not spans[0]['text'].strip():spans.pop(0)
        while spans and not spans[-1]['text'].strip():spans.pop()
        if not spans:group=[];flags=set();return
        lead=len(spans[0]['text'])-len(spans[0]['text'].lstrip())
        trail=len(spans[-1]['text'])-len(spans[-1]['text'].rstrip())
        spans[0]['start']+=lead;spans[0]['text']=spans[0]['text'][lead:]
        if trail:spans[-1]['end']-=trail;spans[-1]['text']=spans[-1]['text'][:-trail]
        rendered=''.join(s['text'] for s in spans)
        start,end=spans[0]['start'],spans[-1]['end']
        exclusion=None
        if speaker not in {'hanser','unknown'}:exclusion='explicit_external_speaker'
        elif not re.search(r'[\w\u4e00-\u9fff]',rendered):exclusion='punctuation_only'
        elif re.fullmatch(r'[A-Za-z]{1,6}',rendered) and rendered.lower() not in {'ok','emmm','www','bye'}:exclusion='latin_label_fragment'
        elif len(rendered)==1 and rendered not in ACK:exclusion='single_character_fragment'
        record={'source_id':source_id,'start':start,'end':end,'text':rendered,'speaker':speaker}
        if exclusion:
            excluded.append({**record,'reason':exclusion})
            if speaker=='audience':
                pending_trigger={'text':rendered,'spans':[{'start':s['start'],'end':s['end']} for s in spans],
                                 'speaker_label':speaker_label}
            elif speaker not in {'hanser','unknown'}:pending_trigger=None
        else:
            risk=set(flags)
            if basis not in {'explicit_marker','source_prior'}:risk.add(basis)
            if re.search(r'[“”「」]|引用|转述|歌词',rendered):risk.add('quote_or_performance_requires_check')
            if open_quote(rendered):risk.add('unclosed_quote')
            if re.fullmatch(r'(?:开播|下播|哼唱|调麦ing|谢礼物ing)[。.!！~～]*',rendered,re.I):risk.add('stage_direction_possible')
            candidates.append({
                'unit_id':f'LCS14-{source_id}-{start:07d}-{end:07d}',
                'source_id':source_id,'document_id':document['document_id'],'filename':document['filename'],
                'slice_version':VERSION,'source_revision':document.get('content_sha256'),
                'reaction_source_start_char':start,'reaction_source_end_char':end,
                'source_spans':[{'start':s['start'],'end':s['end'],'text':s['text']} for s in spans],
                'source_text':text[start:end],'text':rendered,
                'speaker_candidate':speaker,'attribution_basis':basis,
                'attribution_status':'accepted_by_source_policy' if basis=='source_prior' else 'unverified',
                'speaker_confirmation_required':basis!='source_prior',
                'speaker_source_policy':SOURCE_PRIOR_POLICY if basis=='source_prior' else None,
                'explicit_source_trigger':active_trigger['text'] if active_trigger else '',
                'trigger_source_spans':active_trigger['spans'] if active_trigger else [],
                'qa_status':'source_pair' if active_trigger else 'a_only',
                'qa_pair_basis':('explicit_qa_markers' if active_trigger and re.fullmatch(r'q\d{0,2}',(active_trigger.get('speaker_label') or '').casefold()) and re.fullmatch(r'a\d{0,2}',(speaker_label or '').casefold())
                    else 'explicit_speaker_adjacency' if active_trigger and basis=='explicit_marker'
                    else 'source_prior_adjacency' if active_trigger else None),
                'qa_relation_semantically_verified':False,
                'source_context_before':text[max(0,start-180):start],
                'source_context_after':text[end:end+120],
                'risk_flags':sorted(risk),'payload_class':'unreviewed','review_status':'pending',
                'runtime_eligible':False,'semantic_approval_transferred':False,
                'cleaning_operations':['select_exact_source_spans','strip_boundary_whitespace'],
            })
            pending_trigger=None;active_trigger=None
        group=[];flags=set()

    def append_piece(piece):
        nonlocal group,flags
        left,right=piece['start'],piece['end']
        while left<right:
            occupied=sum(len(p['text']) for p in group)
            room=config.max_chars-occupied
            if room==0:emit();room=config.max_chars
            end=min(right,left+room)
            hard=end<right
            if hard:
                # Avoid breaking a word when a nearby comma/space is available.
                floor=left+max(1,int(room*.6))
                choices=[m.end()+left for m in re.finditer(r'[,，、\s]',text[left:end]) if m.end()+left>=floor]
                if choices:end=choices[-1]
                flags.add('hard_length_split')
            group.append({'start':left,'end':end,'text':text[left:end]})
            left=end
            if hard:emit();flags.add('hard_length_split_continuation')

    for piece in pieces:
        kind=piece['kind'];raw=piece['text']
        if kind=='speaker_marker':
            emit()
            speaker,basis=role(piece['label'])
            speaker_label=piece['label']
            active_trigger=pending_trigger if speaker=='hanser' else None
            if active_trigger:
                q_number=re.fullmatch(r'q(\d+)',(active_trigger.get('speaker_label') or '').casefold())
                a_number=re.fullmatch(r'a(\d+)',(speaker_label or '').casefold())
                if q_number and a_number and q_number.group(1)!=a_number.group(1):
                    flags.add('qa_number_mismatch');active_trigger=None;pending_trigger=None
            if speaker!='hanser':pending_trigger=None
        elif kind=='unknown_speaker_marker':
            emit();speaker,basis='unknown','unrecognized_speaker_label';speaker_label=piece['label'];pending_trigger=None;active_trigger=None
            excluded.append({**piece,'source_id':source_id,'reason':'unrecognized_speaker_label'})
        elif kind=='timestamp':
            emit();speaker,basis=default_role,default_basis;speaker_label=None;pending_trigger=None;active_trigger=None
            excluded.append({**piece,'source_id':source_id,'reason':'timestamp'})
        elif kind not in {'speech','space'}:
            emit()
            excluded.append({**piece,'source_id':source_id,'reason':kind})
            if kind in {'document_metadata','performance_heading','standalone_parenthesis_unverified'}:
                speaker,basis=default_role,default_basis;speaker_label=None;pending_trigger=None;active_trigger=None
        elif kind=='space':
            if '\n' in raw and (raw.count('\n')>1 or (group and TERMINAL.search(group[-1]['text'].rstrip()[-3:]) and not open_quote(''.join(p['text'] for p in group)))):emit()
            elif group:append_piece(piece)
            if '\n' in raw and speaker=='audience':
                emit();speaker,basis=default_role,default_basis;speaker_label=None
                active_trigger=pending_trigger if speaker=='hanser' else None
        else:
            # A new line after a complete sentence is a reply boundary; no window overlap duplicates.
            newline_before=piece['start']>0 and text[piece['start']-1]=='\n'
            if group and newline_before:
                size=sum(len(p['text']) for p in group)
                ended=bool(TERMINAL.search(group[-1]['text'].rstrip()[-3:]))
                if not open_quote(''.join(p['text'] for p in group)) and (size>=config.target_chars or (ended and not CONTINUATION.match(raw))):emit()
                if speaker=='audience':
                    emit();speaker,basis=default_role,default_basis;speaker_label=None
                    active_trigger=pending_trigger if speaker=='hanser' else None
            append_piece(piece)
            if sum(len(p['text']) for p in group)>=config.target_chars and TERMINAL.search(raw[-3:]) and not open_quote(''.join(p['text'] for p in group)):emit()
    emit()
    for row in candidates:
        if ''.join(text[s['start']:s['end']] for s in row['source_spans'])!=row['text']:
            raise ValueError('Candidate source mapping disagrees')
        if len(row['text'])>config.max_chars:
            raise ValueError('Candidate length exceeds configured limit')
    return {'candidates':candidates,'excluded':excluded,'atoms':pieces,
            'summary':{'source_id':source_id,'filename':document['filename'],'source_chars':len(text),
                'candidates':len(candidates),'speaker_basis':dict(Counter(r['attribution_basis'] for r in candidates)),
                'excluded_reasons':dict(Counter(r['reason'] for r in excluded)),
                'lengths':{'min':min([len(r['text']) for r in candidates],default=0),
                           'max':max([len(r['text']) for r in candidates],default=0)},
                'hard_split_candidates':sum('hard_length_split' in r['risk_flags'] or 'hard_length_split_continuation' in r['risk_flags'] for r in candidates)}}


def run(frozen: Path,output: Path,source_ids: list[str],config: SliceConfig):
    output.mkdir(parents=True,exist_ok=True)
    if (output/'candidates.jsonl').exists():raise FileExistsError('Use a new run directory')
    documents=reconstruct_documents(frozen,source_ids)
    all_candidates=[];excluded=[];summaries=[];all_atoms=[]
    for document in documents:
        result=slice_document(document,config)
        all_candidates.extend(result['candidates']);excluded.extend(result['excluded']);summaries.append(result['summary'])
        all_atoms.extend({'source_id':document['source_id'],**a} for a in result['atoms'])
    write_rows(output/'sources.snapshot.jsonl',documents)
    write_rows(output/'candidates.jsonl',all_candidates)
    write_rows(output/'excluded.jsonl',excluded)
    write_rows(output/'atoms.jsonl',all_atoms)
    # Keep the review page small enough to open for a full-corpus run.
    write_preview(output,all_candidates if len(all_candidates)<=1000 else all_candidates[:1000])
    qa_pairs=[{k:r[k] for k in ('unit_id','source_id','text','source_spans','explicit_source_trigger','trigger_source_spans','qa_pair_basis','qa_relation_semantically_verified')} for r in all_candidates if r['qa_status']=='source_pair']
    questions=[r for r in excluded if r.get('speaker')=='audience']
    write_rows(output/'qa_pairs.jsonl',qa_pairs)
    write_rows(output/'question_records.jsonl',questions)
    lengths=sorted(len(r['text']) for r in all_candidates)
    summary={'version':VERSION,'documents':len(documents),'source_chars':sum(len(d['text']) for d in documents),
        'candidates':len(all_candidates),'basis':dict(Counter(r['attribution_basis'] for r in all_candidates)),
        'short_le_5':sum(len(r['text'])<=5 for r in all_candidates),'long_ge_200':sum(len(r['text'])>=200 for r in all_candidates),
        'median_chars':lengths[len(lengths)//2] if lengths else 0,'max_chars':max(lengths,default=0),
        'hard_split_candidates':sum(any(f.startswith('hard_length_split') for f in r['risk_flags']) for r in all_candidates),
        'excluded_reasons':dict(Counter(r['reason'] for r in excluded)),
        'speaker_accepted_by_source_policy':sum(r['attribution_basis']=='source_prior' for r in all_candidates),
        'source_prior_policy':SOURCE_PRIOR_POLICY,
        'qa_pairs':len(qa_pairs),'qa_pair_basis':dict(Counter(r['qa_pair_basis'] for r in qa_pairs)),
        'question_records':len(questions),'preview_rows':min(1000,len(all_candidates)),
        'runtime_eligible':0,'external_model_calls':0,'production_modified':False,'per_document':summaries}
    write_json(output/'summary.json',summary)
    write_json(output/'input_manifest.json',{'source_ids':source_ids,'frozen_source':str(frozen.resolve()),
        'config':config.__dict__,'slice_version':VERSION,'source_split':'corpus_build','external_model_calls':0,
        'source_prior_policy':SOURCE_PRIOR_POLICY,
        'semantic_approval':False,'source_mapping':'exact Unicode character ranges; source preserved'})
    print(json.dumps({k:v for k,v in summary.items() if k!='per_document'},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen',type=Path,default=PROJECT/'audit_artifacts/desktop_style_full_corpus_20260916_002')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source-ids',nargs='+',default=PILOT_IDS)
    parser.add_argument('--all-build',action='store_true')
    parser.add_argument('--target-chars',type=int,default=120)
    parser.add_argument('--max-chars',type=int,default=240)
    args=parser.parse_args()
    ids=[r['source_id'] for r in read_rows(args.frozen/'sources.frozen.jsonl') if r['split']=='corpus_build'] if args.all_build else args.source_ids
    run(args.frozen,args.output,ids,SliceConfig(args.target_chars,args.max_chars))
