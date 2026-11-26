"""QQ trial retrieval: independent QA/A quotas, local context, no inference."""
from __future__ import annotations

from contextvars import ContextVar
from collections import Counter
import json
import re
import sqlite3

from .corpus import _tokens


RETRIEVAL_HISTORY = ContextVar('qq_retrieval_history', default=())
LANE_TOP_K = 3
LOCAL_TOP_K = 10
HISTORY_ROUNDS = 10
WEAK = set('我 你 他 她 它 我们 你们 他们 那 这 这个 那个 一个 什么 怎么 咋办 干嘛 好 呀 啊 哦 嗯 嘛 啦 呢 吧 了 的 是 在 也 都 就 还 很 说 一句 现在 今天 最近 可以 一下 这样 这里 那里 不让 我来'.split())
BEHAVIOR_LABELS = {
    'light_contextual_tease': (6,), 'playful_reframe': (6,),
    'listen_without_forcing_advice': (5,), 'natural_close': (12,),
    'greet_current_partner': (9,), 'receive_current_praise': (10,),
    'reasoned_independent_response': (7,), 'brief_contextual_uptake': (1,),
}


def terms(text):
    return {word for word in _tokens(text) if word not in WEAK}


def observed(signals, name):
    item = signals.get(name) if signals else None
    return item.value if item and item.status == 'observed' else None


def retrieval_plan(message, plan, history, signals, decision):
    # History is an interaction query, never source evidence or a fabricated Q.
    context = [item.content for item in history[-2 * HISTORY_ROUNDS:]]
    from .text_core import topic_shift
    if topic_shift(message):
        context = []
    current = terms(message)
    contextual = terms(' '.join(context)) - current
    labels = {1: 0.15}
    for item in decision.persona_affordances if decision else ():
        for label in BEHAVIOR_LABELS.get(item.id, ()):
            labels[label] = max(labels.get(label, 0), min(1, item.weight))
    greeting = bool(re.fullmatch(r'(?:下午好|晚上好|早上好|中午好|你好|嗨|哈喽|早安)[呀啊哇哦！!。\s]*', message))
    if greeting:
        labels = {9: 1.0}
        contextual = set()
    if observed(signals, 'playful_frame') is True:
        labels[6] = max(labels.get(6, 0), 0.8)
    return {'surface_query': message, 'context_messages': context,
            'current_terms': sorted(current), 'context_terms': sorted(contextual),
            'label_preferences': labels, 'greeting': greeting,
            'evidence_refs': ['current_user'] + [f'history_-{i}' for i in range(1, len(context) + 1)],
            'label_basis': 'existing_behavior_decision; relevant_QA_adds_soft_support',
            'lane_limits': {'qa': LANE_TOP_K, 'a': LANE_TOP_K}}


def search_lane(corpus, query, lane, limit=24):
    if not query:
        return []
    column = 'trigger_text' if lane == 'qa' else 'answer'
    status = 'source_pair' if lane == 'qa' else 'a_only'
    expression = column + ' : (' + ' OR '.join('"' + t.replace('"', '""') + '"' for t in sorted(query)) + ')'
    with sqlite3.connect(corpus.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        rows = conn.execute('''SELECT u.record_json,bm25(candidate_fts,0,1,1.5) AS score
            FROM candidate_fts JOIN units u ON u.unit_id=candidate_fts.unit_id
            WHERE candidate_fts MATCH ? AND u.bucket='classified' AND u.label_number<>13
              AND json_extract(u.record_json,'$.qa_status')=?
            ORDER BY score,u.unit_id LIMIT ?''', (expression, status, limit)).fetchall()
    return [{'candidate': json.loads(row[0]), 'rank': row[1]} for row in rows]


class QuestionIndex:
    """Index only real source Q text; answer length/tags cannot affect ranking."""
    def __init__(self, raw_pairs, reviewed_pairs, *, userdict_path=None):
        from hanser_agent.retrieval.style_lexical import StyleLexicalIndex
        self.items = {}
        questions = {}
        for unit in raw_pairs:
            if not unit.get('explicit_source_trigger'):
                continue
            key = 'qq-trial:' + unit['unit_id']
            self.items[key] = {'candidate': unit}
            questions[key] = {'trigger_text': unit['explicit_source_trigger']}
        for example in reviewed_pairs:
            if not example.source_user_turn:
                continue
            self.items[example.id] = {'example': example}
            questions[example.id] = {'trigger_text': example.source_user_turn}
        self.index = StyleLexicalIndex.build(questions, userdict_path=userdict_path)

    def search(self, query, *, signals=None, decision=None, limit=24):
        from hanser_agent.persona.style_applicability import style_applicability_exclusion_reason
        from hanser_agent.persona.style_eligibility import style_exclusion_reason
        current = self.index.score(' '.join(query['current_terms']))
        recent = self.index.score(' '.join(sorted(terms(' '.join(query['context_messages'][-4:])))))
        context = self.index.score(' '.join(query['context_terms']))
        hits = []
        for key in current.keys() | recent.keys() | context.keys():
            matches = [pool.get(key) for pool in (current, recent, context)]
            # Auxiliary character overlap alone is not a source-Q term match.
            if not any(m and m.raw_score > 0 and m.meaningful_terms for m in matches):
                continue
            item = self.items[key]
            if 'example' in item:
                e = item['example']
                if style_exclusion_reason(e, active_generation=e.index_generation, decision=decision):
                    continue
                if style_applicability_exclusion_reason(e, signals)[0]:
                    continue
            components = [m.raw_score / (m.raw_score + 2) if m and m.raw_score > 0 else 0 for m in matches]
            score = components[0] + .35 * components[1] + .1 * components[2]
            hits.append({**item, 'q_score': score, 'q_match': {
                'route': 'qa_source_q', 'current_terms': list(matches[0].meaningful_terms) if matches[0] else [],
                'recent_terms': list(matches[1].meaningful_terms) if matches[1] else [],
                'context_terms': list(matches[2].meaningful_terms) if matches[2] else [],
                'current_score': components[0], 'recent_score': components[1], 'context_score': components[2]},
                'id': key})
        hits.sort(key=lambda h: (-bool(h['q_match']['current_terms']), -h['q_score'], h['id']))
        return [{**hit, 'rank': i} for i, hit in enumerate(hits[:limit], 1)]


def raw_admissibility(unit, query, signals, decision, *, semantic=False):
    if unit.get('speaker_candidate') not in (None, 'hanser') or unit.get('attribution_basis') not in {'source_prior', 'explicit_marker'}:
        return 'source_not_allowed'
    if unit.get('risk_flags'):
        return 'source_risk_requires_review'
    label, text = unit['label_number'], unit['text']
    if label == 13:
        return 'label_not_recommended'
    if not semantic and query['greeting'] and label != 9:
        return 'greeting_scene_mismatch'
    if observed(signals, 'no_advice') is True and label == 4:
        return 'no_advice'
    if observed(signals, 'disable_humor') is True and label == 6:
        return 'humor_disabled'
    if not semantic and observed(signals, 'distress') is True and label == 6:
        return 'distress_scene_mismatch'
    blocked = set(decision.expression_caps.hard_disallowed) if decision else set()
    # Reuse the existing expression lexicon; unknown metadata is not approval.
    from hanser_agent.persona.expression import _TOKENS
    for key in ('profanity', 'innuendo', 'cutesy', 'meme'):
        if key in blocked and (key in unit.get('expression_tags', []) or any(p.search(text) for p in _TOKENS.get(key, ()))):
            return 'expression_hard_blocked'
    return None


def content_match(text, current, context):
    candidate = terms(text)
    direct, contextual = current & candidate, context & candidate
    coverage = len(direct) / len(current) if current else len(contextual) / max(1, len(context))
    strong = {x for x in direct | contextual if len(x) >= 2}
    return direct, contextual, coverage, bool(strong and coverage >= 0.6)


def select_candidates(raw_lanes, original, query, signals=None, decision=None, recent_ids=(), *, local_top_k=LANE_TOP_K, semantic=False):
    """Rank within each structural lane. Never backfill one lane from the other."""
    pools = {'qa': [], 'a': []}
    excluded, details = {}, {}
    preferences = dict(query['label_preferences'])
    current, context = set(query['current_terms']), set(query['context_terms'])
    for lane in ('qa', 'a'):
        for rank, hit in enumerate(raw_lanes[lane], 1):
            if 'example' in hit:
                example = hit['example']
                pools['qa'].append({'id': example.id, 'example': example, 'lane': 'qa',
                    'score': 1 / (60 + rank) + .003 * hit['q_score'],
                    'source': example.source_document_id or example.group_id or example.id,
                    'text': example.runtime_reaction_fragment or example.character_response,
                    'route': 'qa_source_q'})
                details[example.id] = {**hit['q_match'], 'lane_rank': rank, 'q_score': hit['q_score']}
                continue
            u = hit['candidate']; key = 'qq-trial:' + u['unit_id']
            reason = raw_admissibility(u, query, signals, decision, semantic=semantic)
            field = u.get('explicit_source_trigger', '') if lane == 'qa' else u['text']
            direct, contextual, coverage, admissible = content_match(field, current, context)
            # Generic single characters and historical overlap alone cannot
            # establish that an arbitrary source is applicable now.
            if not reason and not semantic and not admissible:
                reason = 'insufficient_context_match'
            if reason:
                excluded[key] = reason
                continue
            label_support = preferences.get(u['label_number'], 0) if lane == 'a' else 0
            score = 1 / (60 + rank) + 0.006 * label_support + 0.003 * (hit.get('q_score', coverage) if lane == 'qa' else coverage)
            if key in recent_ids:
                score -= 0.012
            route = 'qa_source_q' if lane == 'qa' else 'a_context_lexical'
            item = {'id': key, 'unit': u, 'lane': lane, 'score': score,
                    'source': u['source_id'], 'text': u['text'], 'route': route}
            pools[lane].append(item)
            details[key] = {'route': route, 'raw_bm25': hit['rank'], 'lane_rank': rank,
                            'matched_current': sorted(direct), 'matched_context': sorted(contextual),
                            'label': u['label_number'], 'coverage': coverage,
                            'payload': 'user_trial_original_A'}
            if lane == 'qa':
                details[key].pop('raw_bm25')
                details[key]['q_score'] = hit.get('q_score')
                details[key]['q_match'] = hit.get('q_match', {})
        if lane == 'qa':
            # Relevant QA is additional evidence; it cannot erase the plan or
            # impose a single response act. Use only the leading bounded set.
            for item in sorted(pools[lane], key=lambda x: -x['score'])[:LANE_TOP_K]:
                if 'unit' not in item:
                    continue
                label = item['unit']['label_number']
                preferences[label] = min(1, preferences.get(label, 0) + 0.15)
    for rank, example in enumerate(original.examples, 1):
        # Source pairs must enter through QuestionIndex, never through A tags.
        if example.source_user_turn:
            if example.id not in details:
                excluded[example.id] = 'qa_requires_source_q_retrieval'
            continue
        lane = 'a'
        pools[lane].append({'id': example.id, 'example': example, 'lane': lane,
            'score': 1 / (60 + rank) + (0.004 if 'A' in original.matched_by.get(example.id, []) else 0),
            'source': example.source_document_id or example.group_id or example.id,
            'text': example.runtime_reaction_fragment or example.character_response,
            'route': 'reviewed_projection'})
        details[example.id] = {'route': 'reviewed_projection', 'lane_rank': rank,
                               'original': original.match_details.get(example.id, {})}
    selected, seen_units, seen_texts = [], set(), set()
    used_sources = Counter()
    # Interleave the two pools so ContextBuilder's token budget does not always
    # drop an entire lane. Caps and accounting stay independent.
    counts = Counter()
    while any(pools.values()):
        progressed = False
        for lane in ('qa', 'a'):
            if counts[lane] >= local_top_k or not pools[lane]:
                continue
            pool = pools[lane]
            best = max(pool, key=lambda x: (x['score'] - 0.004 * used_sources[x['source']], x['id']))
            pool.remove(best); progressed = True
            uid = best['unit']['unit_id'] if 'unit' in best else (best['example'].unit_id or best['id'])
            text = re.sub(r'[\s，,。.!！?？;；~～]', '', best['text'])
            if uid in seen_units or text in seen_texts:
                excluded[best['id']] = 'duplicate_source_or_text'
                continue
            selected.append(best); counts[lane] += 1
            seen_units.add(uid); seen_texts.add(text); used_sources[best['source']] += 1
        if not progressed:
            break
    return selected, {'plan': {**query, 'final_label_preferences': preferences},
                      'selected_by_lane': dict(counts), 'details': details, 'excluded': excluded,
                      'selected_ids': [x['id'] for x in selected]}
