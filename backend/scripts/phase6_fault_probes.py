"""Reproduce contract failures on temporary databases; report expected vs actual."""
from phase6_audit import *
import tempfile
from hanser_agent.retrieval import SQLiteVectorStore
from hanser_agent.memory import MemoryRetriever
from hanser_agent.config import MemoryConfig
from hanser_agent.agent.conversation import ConversationOwnershipError

async def main():
    results=[]
    from tests.test_reranker import FakeHybridRetriever
    from hanser_agent.agent.tools.wiki_search import WikiSearchTool
    class FailedReranker:
        async def rerank(self,*args,**kwargs):raise RuntimeError('injected reranker unavailable')
    try:
        await WikiSearchTool(load_settings(OUT/'isolated.yml'),FailedReranker(),FakeHybridRetriever()).search('query',[])
        outcome='degraded evidence returned'
    except Exception as e:outcome=type(e).__name__
    results.append({'id':'reranker_failure','expected':'structured degraded retrieval result','actual':outcome,'pass':outcome=='degraded evidence returned'})
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'probe.db'
        with db.connect(path) as conn:
            db.init_db(conn)
            db.apply_slice5_ownership_index_migration(conn)
        store=MemoryStore(path);ex=MemoryCandidateExtractor();gate=MemoryWriteGate(MemoryConfig())
        values=gate.select(ex.extract(user_id='u',conversation_id='c',message_id='m',message='你还记得我们之前一起去过上海吗'))
        results.append({'id':'false_shared_event','expected':'no verified shared event from a question','actual':[v.model_dump() for v in values],'pass':not values})
        conv=ConversationStore(path)
        for i,msg in enumerate(['我喜欢咖啡','我不喜欢咖啡','我喜欢茶']):
            source_id,_=conv.append_turn(conversation_id='c',user_id='u',user_text=msg,assistant_text='ok',model_name='test',trace_id=str(i),persona_version='test')
            for v in gate.select(ex.extract(user_id='u',conversation_id='c',message_id=source_id,message=msg)):store.upsert_candidate(v)
        active=[m.content for m in store.list_memories(user_id='u')]
        results.append({'id':'preference_conflict','expected':'old positive coffee preference inactive','actual':active,'pass':'用户喜欢咖啡' not in active})
        m=store.list_memories(user_id='u')[0]
        try:store.update_memory(m.id,MemoryPatch(content=None));actual='accepted'
        except Exception as e:actual=type(e).__name__
        results.append({'id':'null_patch','expected':'validation rejection before SQL','actual':actual,'pass':actual=='ValidationError'})
        conv.append_turn(conversation_id='same',user_id='alice',user_text='Alice私密消息',assistant_text='received',model_name='test',trace_id='1',persona_version='test')
        history=[m.content for m in conv.get_recent('same', user_id='alice')]
        try:
            conv.get_recent('same', user_id='bob')
            owner_result='history leaked'
        except ConversationOwnershipError:
            owner_result='rejected before history'
        try:
            conv.append_turn(conversation_id='same',user_id='bob',user_text='Bob',assistant_text='received',model_name='test',trace_id='2',persona_version='test')
        except ConversationOwnershipError:
            pass
        with db.connect(path) as conn:owner=conn.execute("SELECT user_id FROM conversations WHERE id='same'").fetchone()[0]
        results.append({'id':'conversation_owner','expected':'reject conflicting user before loading history','actual':{'alice_history':history,'bob':owner_result,'owner_after':owner},'pass':owner=='alice' and owner_result=='rejected before history'})
        vector=SQLiteVectorStore(path)
        own_source,_=conv.append_turn(conversation_id='owner-c',user_id='owner',user_text='own fact',assistant_text='ok',model_name='test',trace_id='own',persona_version='test')
        own,_=store.upsert_candidate(MemoryCandidate(user_id='owner',conversation_id='owner-c',type='user_fact',memory_key='own',content='own fact',importance=.9,confidence=.9,source_message_ids=[own_source]))
        vector.upsert('memories','test',[(own.id,[.9,.1])]+[(f'foreign-{i}',[1.,0.]) for i in range(21)])
        store.set_embedding_ref(own.id)
        class Embed:
            model_name='test'
            async def embed_queries(self,texts):return [[1.,0.] for _ in texts]
        retrieved=await MemoryRetriever(store=store,embedder=Embed(),vector_store=vector,config=MemoryConfig()).search(query='own fact',user_id='owner')
        results.append({'id':'memory_prefilter','expected':'own relevant memory remains eligible','actual_count':len(retrieved),'pass':bool(retrieved)})
        vector.upsert('test','test',[('old',[1.,0.])]);before=vector.search('test','test',[1.,0.],top_k=1)[0].item_id
        second=SQLiteVectorStore(path);second.clear('test');second.upsert('test','test',[('new',[1.,0.])])
        after=vector.search('test','test',[1.,0.],top_k=1)[0].item_id
        results.append({'id':'external_index_cache','expected':'new after external index replacement','actual':after,'pass':after=='new'})
    save('fault_probes.json',results);print(json.dumps(results,ensure_ascii=False,indent=2))

if __name__=='__main__':asyncio.run(main())
