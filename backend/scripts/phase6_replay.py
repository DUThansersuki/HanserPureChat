"""Replay frozen contexts through one configured responder; no retrieval or memory writes."""
from phase6_audit import *
from hanser_agent.agent.context_builder import ContextBundle
from hanser_agent.api import build_chat_agent
from hanser_agent.model_gateway import build_model_gateway


async def main(args):
    rows = [json.loads(line) for line in args.baseline.read_text(encoding='utf-8').splitlines() if line]
    contexts = [(r['case']['id'], ContextBundle.model_validate(r['contexts'][args.variant])) for r in rows]
    if args.dry_run:
        print(json.dumps({'validated_contexts': len(contexts), 'variant': args.variant, 'network_calls': 0}))
        return
    target = args.output.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects the immutable baseline and prior candidate runs.
    with target.open('x', encoding='utf-8') as handle:
        settings = replace(load_settings(args.config), db_path=OUT/'audit.db')
        gateway = build_model_gateway(settings)
        agent = build_chat_agent(settings, gateway, MemoryStore(settings.db_path))
        try:
            for cid, context in contexts:
                started = time.perf_counter()
                row = {'case_id': cid, 'variant': args.variant, 'profile': vars_profile(settings.responder), 'date': datetime.now(timezone.utc).isoformat(), 'baseline_sha256': hashlib.sha256(args.baseline.read_bytes()).hexdigest(), 'context_sha256': hashlib.sha256(json.dumps([m.model_dump() for m in context.messages],ensure_ascii=False).encode()).hexdigest()}
                try:
                    result = await asyncio.wait_for(agent.responder.respond(context), 180)
                    row.update(status='MEASURED', text=result.text, raw_text=result.raw_text, actions=result.validator_actions, latency_seconds=time.perf_counter()-started)
                except Exception as error:
                    row.update(status='NOT_EXECUTED', error=type(error).__name__)
                handle.write(json.dumps(row, ensure_ascii=False)+'\n')
                handle.flush()
                print(cid, row['status'], flush=True)
        finally:
            await gateway.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, default=ROOT/'audit_artifacts/phase6/frozen_contexts.jsonl')
    parser.add_argument('--config', type=Path, default=ROOT/'backend/config.yml')
    parser.add_argument('--variant', choices=list('ABCDEF'), default='F')
    parser.add_argument('--output', type=Path, default=OUT/'replay_outputs.jsonl')
    parser.add_argument('--dry-run', action='store_true')
    asyncio.run(main(parser.parse_args()))
