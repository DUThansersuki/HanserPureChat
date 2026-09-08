"""Recompute audit summaries from persisted raw evidence. No model calls."""
from phase6_audit import *
from itertools import combinations

def read(name):
    p=OUT/name
    return [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines() if l.strip()] if p.exists() else []

def main():
    import math
    from transformers import AutoTokenizer
    tok=AutoTokenizer.from_pretrained('Qwen/Qwen3-Embedding-0.6B',local_files_only=True)
    def tokens(t):return len(tok.encode(t,add_special_tokens=False))
    frozen={r['case']['id']:r for r in read('frozen_contexts.jsonl')}
    outputs=read('ablation_outputs.jsonl');runtime=read('runtime_traces.jsonl');judges=read('pairwise_judgments.jsonl')
    report={'ablation':{},'topk':{},'runtime':{},'judges':{},'style_retrieval':{}}
    for v in 'ABCDEF':
        rows=[r for r in outputs if r['variant']==v and r['status']=='MEASURED']
        if not rows:continue
        metrics=voice([r['text'] for r in rows]);metrics['latency_p50']=statistics.median(r['latency_seconds'] for r in rows)
        metrics['latency_mean']=statistics.mean(r['latency_seconds'] for r in rows)
        metrics['input_tokens_proxy_mean']=statistics.mean(sum(tokens(m['content']) for m in frozen[r['case_id']]['contexts'][v]['messages']) for r in rows)
        metrics['nickname_response_rate']=sum('憨憨' in r['text'] for r in rows)/len(rows)
        metrics['raw_punctuation_response_rate']=sum(bool(re.search('[，。！？；：]',r['raw_text'])) for r in rows)/len(rows)
        metrics['alias']=v=='F';report['ablation'][v]=metrics
    for k in [0,1,2,4,6]:
        rows=[r for r in read('topk_outputs.jsonl') if r['k']==k and r['status']=='MEASURED']
        if rows:report['topk'][k]={**voice([r['text'] for r in rows]),'latency_p50':statistics.median(r['latency_seconds'] for r in rows),'tokens_proxy_mean':statistics.mean(sum(tokens(m['content']) for m in r['context']['messages']) for r in rows)}
    scene_match=mode_match=length_match=total=0;counts=Counter();duplicate_sets=0
    for cid,f in frozen.items():
        labels=label_style(f['case']['message'],'');seen=[]
        for e in f['style']:
            total+=1;scene_match+=e['scene']==labels['scene'];mode_match+=e['response_mode']==f['case']['response_mode'];length_match+=e['answer_length']==f['case']['target_length'];counts[e['id']]+=1;seen.append(e['character_response'])
        duplicate_sets+=len(seen)!=len(set(seen))
    report['style_retrieval']={'queries':len(frozen),'selected':total,'unique_examples':len(counts),'same_heuristic_scene_match':scene_match/max(1,total),'mode_match':mode_match/max(1,total),'length_match':length_match/max(1,total),'exact_duplicate_topk_sets':duplicate_sets,'most_selected':counts.most_common(10)}
    completed=[r for r in runtime if r.get('http_status')==200]
    report['runtime']['turns']=len(runtime);report['runtime']['success']=len(completed)
    report['runtime']['voice']=voice([r['response']['text'] for r in completed])
    report['runtime']['stage_latency']={}
    for key in ['planner','wiki','style','memory','context','responder','validator','post_turn']:
        vals=[r['trace']['timing'][key] for r in completed if key in r['trace'].get('timing',{})]
        if vals:report['runtime']['stage_latency'][key]={'n':len(vals),'p50':statistics.median(vals),'min':min(vals),'max':max(vals)}
    if completed:
        vals=[r['seconds'] for r in completed];report['runtime']['total_latency']={'p50':statistics.median(vals),'min':min(vals),'max':max(vals)}
    usages=[p['usage'] for r in completed for p in r['trace'].get('provider_responses',[]) if p.get('usage')]
    if usages:report['runtime']['provider_tokens']={k:{'mean':statistics.mean(u[k] for u in usages),'max':max(u[k] for u in usages),'sum':sum(u[k] for u in usages)} for k in ['prompt_tokens','completion_tokens','total_tokens']}
    valid=[j for j in judges if j.get('status')=='MEASURED'];winners={};scores={v:[] for v in ['B','C']}
    for j in valid:
        result=j['verdict'];win='tie' if result['preferred']=='tie' else j[result['preferred']+'_variant'];winners[(j['case_id'],j['reverse'])]=win
        for side in ['left','right']:scores[j[side+'_variant']].append(result[side])
    both=[cid for cid in frozen if (cid,False) in winners and (cid,True) in winners]
    report['judges']={'completed':len(valid),'paired_cases':len(both),'winners_by_orientation':dict(Counter(winners.values())),'order_agreement':sum(winners[c,False]==winners[c,True] for c in both)/max(1,len(both)),
        'means':{v:{k:statistics.mean(x[k] for x in arr) for k in arr[0]} for v,arr in scores.items() if arr},
        'stable_C_wins':sum(winners[c,False]==winners[c,True]=='C' for c in both),'stable_B_wins':sum(winners[c,False]==winners[c,True]=='B' for c in both),
        'stable_ties':sum(winners[c,False]==winners[c,True]=='tie' for c in both)}
    # Case-cluster bootstrap, averaging both orientations per case; do not treat 74 judgments as 74 independent cases.
    deltas=[]
    for cid in both:
        pair=[j for j in valid if j['case_id']==cid];per={v:[] for v in ['B','C']}
        for j in pair:
            for side in ['left','right']:per[j[side+'_variant']].append(j['verdict'][side]['character_fidelity'])
        deltas.append(statistics.mean(per['C'])-statistics.mean(per['B']))
    if deltas:
        rng=random.Random(606);boot=sorted(statistics.mean(rng.choices(deltas,k=len(deltas))) for _ in range(2000))
        report['judges']['character_delta_C_minus_B']={'mean':statistics.mean(deltas),'bootstrap_95':[boot[50],boot[1949]],'n_cases':len(deltas)}
    save('summary_metrics.json',report)
    # Explicit auditor annotations, not represented as independent human ground truth.
    flags={1518:'response includes a new unparenthesized bullet turn',1308:'multiple speakers: 凉果/海 inside response',1513:'prompt swallowed Hanser answer; response contains another bullet',1151:'prompt swallowed prior response; next topic used as answer',1189:'timestamp/song annotation remains',1394:'prompt swallowed answer; next unrelated response paired',1417:'于尔丹 speaker inserted in response',1506:'prompt swallowed full response; next reading-show segment selected',919:'prompt includes character response; unrelated next response'}
    save('corpus_assessment.json',{'assessor':'Codex source-text spot review, not independent human annotation','seed':606,'n':50,'definite_structural_issues':len(flags),'issue_rate':len(flags)/50,'issues':flags,'speaker_audio_verified':False,'remaining_samples':'no definite structural defect identified in this text-only screen; not authenticated'})
    with db.connect(OUT/'audit.db') as conn:corpus=[dict(r) for r in conn.execute('SELECT id,prompt,response,scene FROM style_examples')]
    contamination=[r['id'] for r in corpus if re.search(r'弹幕[：:]|(?:海|凉果|于尔丹|憨憨)[：:]',r['response'])]
    greeting=[r for r in corpus if r['scene']=='greeting']
    save('corpus_structural_flags.json',{'response_speaker_marker_ids':contamination,'count':len(contamination),'not_an_error_rate':'regex screen only, requires source review','all_greeting_rows':greeting})
    coverage=['greeting','casual chat','factual answer','follow-up factual','being praised','being teased','comforting','user upset','user excited','user gives short reply','awkward silence','deep conversation','self-deprecation','disagreement','uncertainty','unknown fact','nostalgia','relationship callback','memory callback','late-night','playful','serious','storytelling']
    mapping={'greeting':'greeting','casual chat':'casual_chat','being praised':'receiving_praise','being teased':'teasing','comforting':'comfort'}
    counter=Counter(r['scene'] for r in corpus)
    cov=[{'scene':scene,'real_tagged':counter[mapping[scene]] if scene in mapping else None,'synthetic':0,'human_approved':None,'note':'None = no reliable independent tag/approval field; not proven zero real coverage'} for scene in coverage]
    save('coverage_matrix.json',cov)
    save('token_blocks.json',{cid:{name:tokens(block) for name,block in f['contexts']['F']['blocks'].items()} for cid,f in frozen.items()})
    print(json.dumps({'ablation':{k:{x:v[x] for x in ['n','mean_chars','latency_p50','input_tokens_proxy_mean','assistantese_rate','nickname_response_rate']} for k,v in report['ablation'].items()},'retrieval':report['style_retrieval'],'judge':report['judges'],'runtime':report['runtime']},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
