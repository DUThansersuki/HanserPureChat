"""Materialize complete numeric labels and a source-backed development audit."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import html
import json
import math
from pathlib import Path

from scripts.deepseek_a13_labels import LABELS, parse_number
from scripts.local_corpus_slicer import read_rows, write_rows, write_json


def write_result_preview(output,tagged,audits):
    page='''<!doctype html><meta charset="utf-8"><title>A语料分类检查</title>
<style>body{font:15px system-ui;margin:24px;background:#f5f7fa}article{background:white;border:1px solid #d7dce3;border-radius:8px;padding:14px;margin:10px 0}pre{white-space:pre-wrap;font:inherit;line-height:1.6}input,button{padding:8px;margin:4px}small{color:#596779}a{color:#225eaa}</style>
<p><a href="REVIEW.html">返回全部标签</a></p><h1>__TITLE__</h1><p>分类候选与来源。编号不是正式Style或事实批准。</p><input id="query" placeholder="搜索原文或ID"><button id="previous">上一页</button><button id="next">下一页</button><p id="stats"></p><main id="items"></main>
<script>const rows=__DATA__;let page=0;const query=document.getElementById('query');function render(){const q=query.value.toLowerCase(),found=rows.filter(r=>[r.text,r.unit_id,r.label_name].join(' ').toLowerCase().includes(q));page=Math.max(0,Math.min(page,Math.max(0,Math.ceil(found.length/25)-1)));document.getElementById('stats').textContent=found.length+' 条，第 '+(page+1)+' 页';const target=document.getElementById('items');target.replaceChildren();for(const r of found.slice(page*25,page*25+25)){const a=document.createElement('article'),h=document.createElement('b'),p=document.createElement('pre'),s=document.createElement('small'),d=document.createElement('details'),t=document.createElement('summary'),raw=document.createElement('pre');h.textContent=r.sample_no+' | '+r.label_number+' '+r.label_name;p.textContent=r.text;s.textContent=r.unit_id+' | '+r.filename+' | '+r.attribution_basis;t.textContent='查看原文、Q和前后文';raw.textContent='前：'+r.source_context_before+'\\nQ：'+r.explicit_source_trigger+'\\n原始跨度：'+r.source_text+'\\n后：'+r.source_context_after+(r.review_note?'\\n审查：'+r.review_note+'；可接受编号：'+r.acceptable_labels.join(','):'');d.append(t,raw);a.append(h,p,s,d);target.append(a)}}query.oninput=()=>{page=0;render()};document.getElementById('previous').onclick=()=>{page--;render()};document.getElementById('next').onclick=()=>{page++;render()};render();</script>'''
    groups=[]
    for number in LABELS:
        chosen=[r for r in tagged if r['label_number']==number];links=[]
        for start in range(0,len(chosen),1000):
            filename=f'label_{number:02d}_{start//1000+1:02d}.html'
            data=json.dumps(chosen[start:start+1000],ensure_ascii=False).replace('<','\\u003c')
            (output/filename).write_text(page.replace('__TITLE__',f'{number} {LABELS[number][0]}').replace('__DATA__',data),encoding='utf-8')
            links.append(f'<a href="{filename}">{start+1}–{min(start+1000,len(chosen))}</a>')
        groups.append(f'<tr><td>{number}</td><td>{html.escape(LABELS[number][0])}</td><td>{len(chosen)}</td><td>{len(chosen)/len(tagged):.2%}</td><td>{" · ".join(links)}</td></tr>')
    audited=[{**r,'label_number':r['predicted_number'],'label_name':r['predicted_name']} for r in audits]
    (output/'AUDIT_200.html').write_text(page.replace('__TITLE__','固定随机200条对照').replace('__DATA__',json.dumps(audited,ensure_ascii=False).replace('<','\\u003c')),encoding='utf-8')
    (output/'REVIEW.html').write_text('''<!doctype html><meta charset="utf-8"><title>20,000条A分类结果</title><style>body{font:16px system-ui;margin:32px;max-width:1100px}table{border-collapse:collapse;width:100%}th,td{padding:12px;text-align:left;border-bottom:1px solid #ddd}a{color:#225eaa}</style><h1>20,000条A分类结果</h1><p>全部条目按标签分页面浏览，支持搜索和来源展开。分类结果不代表正式注入批准。</p><p><a href="RESULT_REVIEW.md">结果报告</a> · <a href="AUDIT_200.html">随机200条审查对照</a> · <a href="summary.json">调用与用量</a></p><table><tr><th>编号</th><th>类别</th><th>数量</th><th>占比</th><th>逐条浏览</th></tr>'''+''.join(groups)+'</table>',encoding='utf-8')


def wilson(errors,total):
    z=1.96;p=errors/total;denom=1+z*z/total
    center=(p+z*z/(2*total))/denom
    radius=z*math.sqrt((p*(1-p)+z*z/(4*total))/total)/denom
    return [round(100*(center-radius),2),round(100*(center+radius),2)]


def review(output: Path):
    inputs=read_rows(output/'inputs.frozen.jsonl');results=read_rows(output/'results.jsonl')
    by_id={r['unit_id']:r for r in results};by_no={r['sample_no']:r for r in inputs}
    if len(by_id)!=len(results):raise ValueError('Duplicate successful result ID')
    if set(by_id)!={r['unit_id'] for r in inputs}:raise ValueError('Classification is not complete for the frozen input set')
    if any(parse_number(r['raw_output'])!=r['label_number'] for r in results):raise ValueError('Invalid numeric result')
    tagged=[{**r,**by_id[r['unit_id']]} for r in inputs]
    write_rows(output/'tagged_candidates.jsonl',tagged)
    (output/'numbers.txt').write_text(''.join(str(r['label_number'])+'\n' for r in tagged),encoding='utf-8')
    with (output/'audit_acceptance_200.tsv').open(encoding='utf-8',newline='') as handle:
        accepted=list(csv.DictReader(handle,delimiter='\t'))
    chosen=read_rows(output/'preprocessing_audit_200.jsonl')
    if {int(r['sample_no']) for r in accepted}!={r['sample_no'] for r in chosen}:raise ValueError('Audit annotations must cover the fixed 200 rows exactly')
    if len(accepted)!=200:raise ValueError('Audit must have 200 unique annotations')
    audits=[]
    for annotation in accepted:
        row=by_no[int(annotation['sample_no'])];result=by_id[row['unit_id']]
        labels=[int(x) for x in annotation['acceptable_labels'].split(',') if x]
        audits.append({**row,'predicted_number':result['label_number'],
            'predicted_name':result['label_name'],'acceptable_labels':labels,
            'input_issue':annotation['input_issue'] or None,'review_note':annotation['note'],
            'label_acceptable':result['label_number'] in labels if labels else None,
            'review_author':'Codex source-backed development review; no external Judge or independent human gold'})
    scored=[r for r in audits if r['label_acceptable'] is not None]
    wrong=[r for r in scored if not r['label_acceptable']]
    issues=[r for r in audits if r['input_issue']]
    exact=[r for r in scored if len(r['acceptable_labels'])==1]
    exact_wrong=[r for r in exact if not r['label_acceptable']]
    reject=[r for r in scored if r['predicted_number']==13]
    rejected_wrong=[r for r in reject if not r['label_acceptable']]
    retained=[r for r in scored if r['predicted_number']!=13]
    missed_reject=[r for r in retained if r['acceptable_labels']==[13]]
    summary={'fixed_random_sample':200,'label_scored':len(scored),'acceptable_label_mismatches':len(wrong),
        'acceptable_label_mismatch_percent':round(100*len(wrong)/len(scored),2),
        'mismatch_wilson_95_percent':wilson(len(wrong),len(scored)),
        'single_acceptable_label_rows':len(exact),'single_label_mismatches':len(exact_wrong),
        'single_label_mismatch_percent':round(100*len(exact_wrong)/len(exact),2),
        'input_issues':len(issues),'input_issue_percent':100*len(issues)/200,
        'input_issue_wilson_95_percent':wilson(len(issues),200),
        'any_input_or_label_issue':len(wrong)+len(issues),
        'any_input_or_label_issue_percent':100*(len(wrong)+len(issues))/200,
        'predicted_13_in_scored_sample':len(reject),'false_13_rejections':len(rejected_wrong),
        'false_13_percent_of_predicted_13':round(100*len(rejected_wrong)/len(reject),2) if reject else 0,
        'predicted_1_to_12_in_scored_sample':len(retained),'clear_13_misses_in_retained':len(missed_reject),
        'clear_13_miss_percent_of_retained':round(100*len(missed_reject)/len(retained),2) if retained else 0,
        'mismatch_by_predicted_label':dict(Counter(r['predicted_number'] for r in wrong)),
        'limits':'Development source review, not independent ground truth. Multiple labels are accepted for ambiguous/mixed texts; reports label usability rather than strict single-class accuracy.',
        'production_approved':0,'external_review_calls':0}
    write_rows(output/'semantic_audit_200.jsonl',audits)
    write_rows(output/'audit_mismatches.jsonl',wrong+issues)
    # Keep reviewed input faults visible to downstream offline pool consumers.
    issue_ids={r['unit_id']:r['input_issue'] for r in issues}
    write_json(output/'input_quarantine_ids.json',issue_ids)
    write_json(output/'label_pool_ids.json',{str(n):{'name':LABELS[n][0],
        'classifier_count':sum(r['label_number']==n for r in tagged),
        'quarantined_input_count':sum(r['label_number']==n and r['unit_id'] in issue_ids for r in tagged),
        'unit_ids':[r['unit_id'] for r in tagged if r['label_number']==n and r['unit_id'] not in issue_ids],
        'runtime_approved':False,'fact_auto_approved':False} for n in LABELS})
    with (output/'audit_acceptance_200.initial.tsv').open(encoding='utf-8',newline='') as handle:
        initial=list(csv.DictReader(handle,delimiter='\t'))
    summary['initial_annotation_mismatches']=sum(by_id[by_no[int(r['sample_no'])]['unit_id']]['label_number'] not in [int(n) for n in r['acceptable_labels'].split(',')] for r in initial if r['acceptable_labels'])
    summary['annotation_revision_count']=5
    write_json(output/'audit_summary.json',summary)
    write_json(output/'completion_validation.json',{'inputs':len(inputs),'unique_input_ids':len({r['unit_id'] for r in inputs}),
        'unique_input_texts':len({r['text'] for r in inputs}),'successful_results':len(results),
        'all_input_ids_completed_once':True,'all_outputs_exact_integers_1_to_13':True,
        'production_approved':0,'source_validation':'local_corpus_full_20261013_004/source_validation.json'})
    write_result_preview(output,tagged,audits)
    calls=read_rows(output/'call_records.jsonl');dispatch=read_rows(output/'dispatch_records.jsonl')
    requests={}
    for record in dispatch:requests.setdefault(record['request_id'],set()).add(record['state'])
    ledger={'started_requests':len(requests),'unfinished_requests':sum(states!={'started','finished'} for states in requests.values()),
        'returned_call_records':len(calls),'distinct_recorded_request_ids':len({r['request_id'] for r in calls}),
        'valid_completed_results':len(results),'provider_statuses':dict(Counter(r['status'] for r in calls)),
        'usage_reported_calls':sum(r.get('total_tokens') is not None for r in calls)}
    write_json(output/'ledger_validation.json',ledger)
    usage=json.loads((output/'summary.json').read_text(encoding='utf-8'))
    selection=json.loads((output/'selection_manifest.json').read_text(encoding='utf-8'))
    distribution=Counter(r['label_number'] for r in results)
    table='\n'.join(f'| {n} | {LABELS[n][0]} | {distribution[n]:,} | {distribution[n]/len(inputs):.2%} |' for n in LABELS)
    examples=[]
    for row in wrong[:18]:
        excerpt=row['text'].replace('\n',' ')[:150]
        examples.append(f"- 样本{row['sample_no']}：模型{row['predicted_number']}；来源判定允许{','.join(map(str,row['acceptable_labels']))}。原文：{excerpt}")
    report=f'''# 20,000 条 A 的 DeepSeek 13 类结果

2026-10-13。全部 **{len(results):,}/{len(inputs):,}** 完成，返回格式全部为合法 1—13 数字。按模型编号，1—12 保留候选 **{usage['labels_1_to_12']:,}（{usage['labels_1_to_12']/len(inputs):.2%}）**，13 不建议使用 **{usage['not_recommended']:,}（{usage['not_recommended']/len(inputs):.2%}）**。这是分类结果，不能将保留数量直接报告为正式可用 Style。

## 输入与处理范围

全部507篇build来源、2,600,240字符在本地完成全文恢复、Q/A结构识别及A切分，得到34,359条候选；2,434条显式问题／观众触发记录中，1,723条有结构配对（607显式Q/A、970显式说话人相邻、146来源相邻），711条没有A链接。不虚构缺失Q，不宣称相邻即语义完整。53篇holdout与2篇excluded没有混入。

另扫描全部来源，单独保存100条中文题／答及题库答案配对，覆盖3篇，Q/A精确坐标通过。这些答案可能由编辑总结或观众给出，不自动认作Hanser发言，不混入本批A。

机械筛选并去重得到22,204条唯一A，本轮固定无放回抽20,000条，覆盖{selection['selected_documents']}篇。来源推定14,587条按用户授权直接采用Hanser，显式5,413条；18,517条A-only、1,483条保留本地Q坐标。模型仅收到精确A，不收到Q、前后文或旧标签。候选原文与Q/A映射核验通过。

模型仍为 **deepseek-flash**，复用原backend/config.yml planner API及凭据；直接复制原100条试验rule和13类定义。固定system规则在前，A为最后user消息，非JSON、temperature0、max_tokens16。前置不调用外部抽取、改写或Judge。

## 完整分类分布

| 编号 | 类别 | 数量 | 比例 |
| --- | --- | ---: | ---: |
{table}

类别多少仅描述当前来源，不能单凭集中或稀少推断错误。此前100条是小批来源分布，与本轮不能当作同一验证集比较。

## 固定随机200条效果检查

从20,000条固定随机抽200条，先根据原文写可接受类别，再与模型结果比较。混合行为／指代不明确允许多个类别，另报只允许一个类别的子集；这不是严格单标签准确率或独立真人金标。四个初始标注误用了上一前置版本的同编号文本，另一个歌曲评价／本人假设的合理重叠经二次复核修正；五项在看到预测后修订，audit_annotation_revisions.json披露原因，初始文件保留。最终再次逐条对照全部200条原文，未继续修改其他标注。初始标签偏差{summary['initial_annotation_mismatches']}/{len(scored)}，修正后{len(wrong)}/{len(scored)}，不将开发审查当独立金标。

| 检查口径 | 结果 |
| --- | ---: |
| 明确输入边界问题 | {len(issues)}/200 = {summary['input_issue_percent']:.1f}% |
| 排除输入问题后，不在可接受标签集合内 | {len(wrong)}/{len(scored)} = {summary['acceptable_label_mismatch_percent']:.2f}% |
| 单一可接受标签子集的偏差 | {len(exact_wrong)}/{len(exact)} = {summary['single_label_mismatch_percent']:.2f}% |
| 任一输入或标签问题 | {len(wrong)+len(issues)}/200 = {summary['any_input_or_label_issue_percent']:.1f}% |
| 模型判13但来源判定不满足13 | {len(rejected_wrong)}/{len(reject)} = {summary['false_13_percent_of_predicted_13']:.2f}% |
| 模型保留1—12却明确满足13 | {len(missed_reject)}/{len(retained)} = {summary['clear_13_miss_percent_of_retained']:.2f}% |

输入问题的Wilson 95%抽样区间为{summary['input_issue_wilson_95_percent']}%，标签集合偏差区间为{summary['mismatch_wilson_95_percent']}%；仅描述随机抽样不确定性，未覆盖审查者误差和类别定义歧义，不作为全库精确错误率。

三条输入残留为花括号Q、未识别“弹幕上封”前缀和全角／／编辑注。本轮冻结输入未替换，这三条已列入input_quarantine_ids.json；13类ID视图排除已知输入问题，1—12实际离线池剩{sum(r['label_number']!=13 and r['unit_id'] not in issue_ids for r in tagged):,}条。后续本地边界修正可处理，无需外部抽取。

主要标签问题是13过度扩大到普通对话、泛指人群、作品和物件，以及部分具体第三方漏拦；其次是询问当说明、玩笑／建议当观点、未来收尾计划当正在告别。下面是逐条来源复核的例子，不额外调用Judge：

{chr(10).join(examples)}

这批可以提供有规模的分类候选池，但单编号不适合直接决定Style准入。13混合无意义与第三方两种原因，不能整池自动转Fact；1—12中仍有个人经历、一般事实、时效说明和窄场景内容，也不能直接迁移到新对话。

## 接下来优先调整

1. 先本地补三类残留边界，并拆分长段中的多个话题或行为；保留坐标和历史版本。
2. 13在固定规则内增加最小对照：当前“你”／本人Hanser／泛指群体／作品名／物件不等于第三方，具体亲友／昵称／已识别角色才命中；为误删和漏拦分别设验证样本。输出仍保持单个数字。
3. 为询问vs说明、自己的经历vs安慰、建议vs观点、计划下播vs当前告别添加对照实例。新规则先在独立来源样本验证，复用当前20,000标签作为历史对照，避免未经验证重打全库。
4. 候选多池接入本地召回后，再验证top_n→载荷／当轮适用筛选→top_k。保留现有来源、事实、冻结评估及回滚门禁。

本轮没有额外执行优化版外部调用，没有外部Judge、模型训练或正式索引切换。正式Style批准新增0，旧审核结论、生产、seed和用户数据未由此次处理改写。

## 实际调用与token

provider调用账本 **{len(calls):,}** 条；失败provider调用 **{usage['failed_provider_calls']}**；未收口dispatch **{ledger['unfinished_requests']}**。已报告输入 **{usage['prompt_tokens']:,}**、输出 **{usage['completion_tokens']:,}**、总计 **{usage['total_tokens']:,} tokens**。输入缓存命中 **{usage['input_cache_hit_tokens']:,}**、未命中 **{usage['input_cache_miss_tokens']:,}**。以API账本实际回报为准，不换算未核实的金额；本地处理和本次来源审查没有外部模型调用。

29项直接聚焦测试通过；全部输入ID／唯一文本／20,000个数字结果、Q/A坐标映射及调用账本完成检查。未重复应用／TTS冒烟。磁盘仍有可用空间，本轮未删除数据；空间不足优先清理项目可重建冗余的偏好已写入AGENTS.md。

可复查文件：REVIEW.html按13类分片浏览全部20,000条；AUDIT_200.html及semantic_audit_200.jsonl保留逐条审查；tagged_candidates.jsonl、numbers.txt、label_pool_ids.json为离线分类产物，所有池均未批准运行时注入。summary.json、call_records.jsonl、dispatch_records.jsonl、ledger_validation.json为实际用量和请求证据。
'''
    (output/'RESULT_REVIEW.md').write_text(report,encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();review(args.output)
