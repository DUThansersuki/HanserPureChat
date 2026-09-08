"""Small fixed top-k experiment; same responder profile and frozen non-style blocks."""
from phase6_audit import *
from hanser_agent.api import build_chat_agent
from hanser_agent.model_gateway import build_model_gateway

async def main():
    import torch
    torch.set_num_threads(4)
    s=load_settings(ROOT/'backend/config.yml');s=replace(s,db_path=OUT/'audit.db',style=replace(s.style,top_k=6),embedding=replace(s.embedding,local_files_only=True))
    gateway=build_model_gateway(s);agent=build_chat_agent(s,gateway,MemoryStore(s.db_path))
    frozen=[json.loads(l) for l in (OUT/'frozen_contexts.jsonl').read_text(encoding='utf-8').splitlines()]
    ids={'greeting','being_praised','being_teased','comforting','short_reply','mixed_fact','false_memory_trap','warm_interaction','disagreement','user_upset'}
    jobs=[]
    for f in frozen:
        c=f['case']
        if c['id'] not in ids:continue
        p=DialoguePlan(intent='wiki_fact' if c['response_mode']=='factual' else 'chitchat',need_wiki=c['response_mode']=='factual',standalone_query=c['message'],keywords=[],response_mode=c['response_mode'],fact_sensitivity='high' if c['response_mode']=='factual' else 'low',target_length=c['target_length'])
        result=await agent.style_tool.search(c['message'],p)
        for k in [0,1,2,4,6]:
            ctx=agent.context_builder.build(current_message=c['message'],history=[],plan=p,wiki_evidence=[WikiEvidence.model_validate(x) for x in f['evidence']],style_examples=result.examples[:k])
            jobs.append((c['id'],k,ctx))
    random.Random(606).shuffle(jobs);sem=asyncio.Semaphore(3)
    async def run(cid,k,ctx):
        async with sem:
            started=time.perf_counter()
            try:
                result=await asyncio.wait_for(agent.responder.respond(ctx),120)
                rec={'case_id':cid,'k':k,'status':'MEASURED','text':result.text,'raw_text':result.raw_text,'latency_seconds':time.perf_counter()-started,'context':ctx.model_dump(mode='json')}
            except Exception as e:rec={'case_id':cid,'k':k,'status':'NOT_EXECUTED','error':type(e).__name__}
            append('topk_outputs.jsonl',rec);print(cid,k,rec['status'],flush=True)
    await asyncio.gather(*(run(*j) for j in jobs));await gateway.close()

if __name__=='__main__':asyncio.run(main())
