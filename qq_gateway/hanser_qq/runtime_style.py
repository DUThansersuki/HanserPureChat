"""Full-corpus retrieval diagnostics plus the existing reviewed Style engine."""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from .admission import current_role_allows
from .corpus import corpus_status, search_corpus
from .retrieval_audit import ACTIVE_AUDIT


class FullCorpusStyleTool:
    def __init__(self, original, corpus: Path, *, user_trial: bool = False, selector=None):
        from hanser_agent.agent.tools.style_search import StyleSearchTool
        self.settings, self.corpus = original.settings, corpus
        self.user_trial = user_trial
        self.selector = selector
        self.pinned_generation = original.pinned_generation
        self.strict_v2 = original.strict_v2
        # Let the shared eligibility/ranking engine expose its candidate pool,
        # then perform QQ's literal-role and source-diversity selection once.
        pool_settings = replace(self.settings, style=replace(self.settings.style, top_k=self.settings.style.candidate_pool_size))
        self.original = StyleSearchTool(settings=pool_settings, strict_v2=original.strict_v2,
                                       pinned_generation=original.pinned_generation)
        self.generation = corpus_status(corpus)["generation"]
        with closing(sqlite3.connect(self.settings.db_path)) as conn:
            from hanser_agent.persona.data_pipeline import style_row_to_model
            conn.row_factory = sqlite3.Row
            self.reviews = {}
            self.by_unit = {}
            qa_examples = []
            for row in conn.execute("SELECT * FROM style_examples WHERE index_generation=?", (original.pinned_generation,)):
                meta = json.loads(row["metadata_json"])
                if self.user_trial and row['review_status'] == 'approved' and row['source_user_turn']:
                    from hanser_agent.persona.style_eligibility import style_exclusion_reason
                    example = style_row_to_model(row)
                    if style_exclusion_reason(example, active_generation=self.pinned_generation,
                                              min_quality=self.settings.style.min_quality) is None:
                        qa_examples.append(example)
                if "qq_literal_review" in meta:
                    example = style_row_to_model(row)
                    self.reviews[example.id] = meta["qq_literal_review"]
                    self.by_unit[meta["qq_literal_review"]["unit_id"]] = example
        if self.user_trial:
            from .rag_retrieval import QuestionIndex
            with closing(sqlite3.connect(self.corpus.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
                pairs = [json.loads(row[0]) for row in conn.execute("""SELECT record_json FROM units
                    WHERE bucket='classified' AND label_number<>13
                    AND json_extract(record_json,'$.qa_status')='source_pair'""")]
            self.qa_index = QuestionIndex(pairs, qa_examples, userdict_path=self.settings.userdict_path)

    async def search(self, message, plan, **kwargs):
        if self.user_trial:
            result = await self.original.search(message, plan, **kwargs)
            if not self.settings.style.enabled or plan.response_mode == 'factual':
                return result
            from .rag_retrieval import RETRIEVAL_HISTORY, LOCAL_TOP_K, LANE_TOP_K, retrieval_plan, search_lane, select_candidates
            query = retrieval_plan(message, plan, RETRIEVAL_HISTORY.get(), kwargs.get('turn_signals'), kwargs.get('behavior_decision'))
            search_terms = set(query['current_terms']) | set(query['context_terms'])
            lanes = {'qa': self.qa_index.search(query, signals=kwargs.get('turn_signals'),
                                               decision=kwargs.get('behavior_decision')),
                     'a': search_lane(self.corpus, search_terms, 'a')}
            selected, trace = select_candidates(lanes, result, query, kwargs.get('turn_signals'),
                kwargs.get('behavior_decision'), kwargs.get('recent_example_ids', ()),
                local_top_k=LOCAL_TOP_K if self.selector else LANE_TOP_K, semantic=self.selector is not None)
            audit = ACTIVE_AUDIT.get()
            if audit is not None:
                audit['full_corpus'] = {'query': message, 'generation': self.generation,
                    'user_trial': True, 'hits': [hit for hit in lanes['qa'] if 'candidate' in hit] + lanes['a'],
                    'lanes': {lane: [{**hit, 'example': hit['example'].model_dump(mode='json')}
                                    if 'example' in hit else hit for hit in hits] for lane, hits in lanes.items()}}
                audit['retrieval_selection'] = trace
            if self.selector:
                selected = await self.selector.select(selected, message, RETRIEVAL_HISTORY.get(), trace)
            return self._trial_selection(result, selected, trace)
        # The immutable release copy contains ALL source dispositions. Its text
        # is used for local candidate search only; the shared engine below sees
        # only previously approved payloads and the separately reviewed literals.
        candidates = search_corpus(self.corpus, message,
                                   limit=min(50, self.settings.style.candidate_pool_size))["hits"]
        audit = ACTIVE_AUDIT.get()
        if audit is not None:
            audit["full_corpus"] = {"query": message, "generation": self.generation,
                                    "user_trial": self.user_trial, "hits": candidates}
        result = await self.original.search(message, plan, **kwargs)
        if not self.settings.style.enabled or plan.response_mode == "factual":
            return result
        from hanser_agent.persona.style_eligibility import style_exclusion_reason
        from hanser_agent.persona.style_applicability import style_applicability_exclusion_reason
        from hanser_agent.persona.style_ranking import style_score_components
        from hanser_agent.persona.schemas import BehaviorDecision
        present = {item.id for item in result.examples}
        # This is the actual full-A lexical lane. A hit can nominate only its
        # separately reviewed source fragment; the full A never enters context.
        for hit in candidates:
            example = self.by_unit.get(hit["candidate"]["unit_id"])
            if example is None or example.id in present:
                continue
            reason = style_exclusion_reason(example, active_generation=self.pinned_generation,
                                             min_quality=self.settings.style.min_quality,
                                             decision=kwargs.get("behavior_decision"))
            if reason is None:
                reason, _ = style_applicability_exclusion_reason(example, kwargs.get("turn_signals"))
            if reason:
                result.excluded[example.id] = reason
                result.exclusion_counts[reason] = result.exclusion_counts.get(reason, 0) + 1
                continue
            result.examples.append(example.model_copy(update={"matched_by": ["A"]}))
            present.add(example.id)
            relevance = abs(hit["rank"]) / (abs(hit["rank"]) + 2)
            components = style_score_components(content_relevance=relevance, quality_score=example.quality_score,
                authenticity_score=example.authenticity_score, response_mode_match=example.response_mode == plan.response_mode,
                behavior_tags=set(example.behavior_tags), decision=kwargs.get("behavior_decision") or BehaviorDecision(),
                expression_tags=set(example.expression_tags), observations=kwargs.get("observations"),
                repetition_penalty=kwargs.get("repetition_penalty", 0.3), length_match=example.answer_length == plan.target_length,
                recently_used=example.id in (kwargs.get("recent_example_ids") or []))
            result.scores[example.id] = sum(components.values())
            result.score_components[example.id] = components
            result.matched_by[example.id] = ["A"]
            if example.id not in result.retrieved_style_ids:
                result.retrieved_style_ids.append(example.id)
            result.match_details[example.id] = {"full_A_lexical": True, "source_unit_id": hit["candidate"]["unit_id"],
                                               "raw_bm25": hit["rank"], "payload": "reviewed_fragment_only"}
        excluded_ids = set()
        for example in result.examples:
            review = self.reviews.get(example.id)
            if review and not current_role_allows(review["rule_id"], message, kwargs.get("turn_signals")):
                excluded_ids.add(example.id)
                result.excluded[example.id] = "literal_role_mismatch"
        if excluded_ids:
            result.examples = [item for item in result.examples if item.id not in excluded_ids]
            result.exclusion_counts["literal_role_mismatch"] = len(excluded_ids)
            for key in excluded_ids:
                result.scores.pop(key, None)
                result.matched_by.pop(key, None)
            if not result.examples:
                result.empty_reason = "no_applicable_candidate"
        ranked = sorted(result.examples, key=lambda item: (-result.scores.get(item.id, 0), item.id))
        # One admissible full-corpus source can contribute alongside the frozen
        # baseline. It must already have passed shared runtime and turn gates.
        new = next((item for item in ranked if item.id in self.reviews), None)
        selected = []
        seen_literals = set()
        for item in ([new] if new else []) + ranked:
            literal = re.sub(r"[\s，,。.!！?？;；~～]", "", item.runtime_reaction_fragment) if item.runtime_reaction_fragment else item.id
            if item.id in {x.id for x in selected} or literal in seen_literals:
                continue
            if len(selected) >= self.settings.style.top_k:
                break
            selected.append(item)
            seen_literals.add(literal)
        result.examples = selected
        result.empty_reason = None if selected else (result.empty_reason or "no_applicable_candidate")
        result.scores = {item.id: result.scores[item.id] for item in selected}
        result.matched_by = {item.id: result.matched_by.get(item.id, []) for item in selected}
        result.candidate_counts["selected"] = len(result.examples)
        result.candidate_counts["full_corpus_retrieved"] = len(candidates)
        result.candidate_counts["full_corpus_reviewed_payloads"] = len(self.reviews)
        result.candidate_counts["full_corpus_selected"] = sum(item.id in self.reviews for item in result.examples)
        result.match_details["full_corpus"] = {
            "generation": self.generation,
            "retrieved_unit_ids": [item["candidate"]["unit_id"] for item in candidates],
            "selected_source_fragments": {
                item.id: self.reviews[item.id] for item in result.examples if item.id in self.reviews
            },
            "unreviewed_raw_text_injected": False,
        }
        return result

    def _trial_selection(self, result, selected, trace):
        """Explicit user trial: original A text remains pending, never approved."""
        from hanser_agent.models import StyleExample
        examples, sources, scores, routes = [], {}, {}, {}
        original_counts = dict(result.candidate_counts)
        for item in selected:
            scores[item['id']] = item['score']
            routes[item['id']] = ['Q' if item['lane'] == 'qa' else 'A']
            if 'example' in item:
                examples.append(item['example'].model_copy(update={'matched_by': routes[item['id']]}))
                continue
            unit = item['unit']
            text = unit["text"]
            uid = unit["unit_id"]
            start, end = unit["reaction_source_start_char"], unit["reaction_source_end_char"]
            example = StyleExample(
                id="qq-trial:" + uid, unit_id=uid, user_context="", character_response=text,
                scene=unit.get("label_name", ""), speech_act="source_expression_reference",
                answer_length="short" if len(text) <= 80 else "medium" if len(text) <= 240 else "long",
                response_mode='playful' if unit['label_number'] == 6 else 'emotional' if unit['label_number'] == 5 else 'casual',
                source_type="real", source_tier="primary",
                source_ref=f"{uid}#chars={start}-{end}", source_document_id=unit.get("document_id"),
                source_start_char=start, source_end_char=end, source_speaker="hanser",
                source_user_turn=unit.get("explicit_source_trigger") if unit.get("qa_status") == "source_pair" else None,
                source_response_turn=text, source_revision=unit["source_revision"],
                attribution_basis=unit["attribution_basis"], speaker_candidate="hanser",
                provenance_kind="verbatim", authenticity_score=0, quality_score=0,
                review_status="pending", schema_review_status="pending", payload_class="unreviewed",
                runtime_payload_kind="legacy_full", runtime_payload_reviewed=False,
                projection_version="qq-user-full-corpus-trial-v1", index_generation=self.pinned_generation,
                matched_by=routes[item['id']], review_notes="User-authorized full-corpus trial, 2026-10-18; not formal approval")
            examples.append(example)
            sources[example.id] = {key: unit.get(key) for key in
                ("unit_id", "source_id", "document_id", "source_revision", "source_spans", "trigger_source_spans", "attribution_basis", "label_number")}
        result.examples = examples
        result.empty_reason = None if examples else 'no_applicable_candidate'
        result.scores, result.matched_by = scores, routes
        result.score_components = {key: {'fused_lane_rank_and_context': value} for key, value in scores.items()}
        result.excluded = {key: reason for key, reason in {**result.excluded, **trace['excluded']}.items()
                           if key not in scores}
        result.exclusion_counts = dict(Counter(result.excluded.values()))
        result.retrieved_style_ids = list(dict.fromkeys([*result.retrieved_style_ids, *trace['details']]))
        result.candidate_counts = {'selected': len(examples), 'qa_selected': trace['selected_by_lane'].get('qa', 0),
            'a_selected': trace['selected_by_lane'].get('a', 0), 'full_corpus_trial_selected': len(sources)}
        result.match_details = {**trace['details'], 'baseline_candidate_counts': original_counts}
        result.match_details['full_corpus'] = {
            "generation": self.generation, "mode": "user_trial", "formal_approval": False,
            "retrieved_unit_ids": [key.removeprefix('qq-trial:') for key in trace['details'] if key.startswith('qq-trial:')],
            "selected_source_fragments": {}, "selected_trial_originals": sources,
            "unreviewed_raw_text_selected": bool(sources),
        }
        return result


def attach_release_engine(agent, settings, manifest, corpus, *, user_trial=False, speech_habits_enabled=False, expression_polish_enabled=False, rag_selector=None):
    from .rag_selector import ModelRagSelector
    selector = ModelRagSelector(rag_selector) if rag_selector and rag_selector.enabled else None
    agent.style_tool = FullCorpusStyleTool(agent.style_tool, corpus, user_trial=user_trial, selector=selector)
    if manifest.get("runtime_engine") == "qq-text-v3":
        from .text_core import configure_text_core
        agent = configure_text_core(agent, settings, user_trial=user_trial, speech_habits_enabled=speech_habits_enabled,
                                    expression_polish_enabled=expression_polish_enabled)
    return agent
