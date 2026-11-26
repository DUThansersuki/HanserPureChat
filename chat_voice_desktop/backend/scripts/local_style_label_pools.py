"""Offline ID-only label pools sharing one source store; not a production index."""
from __future__ import annotations

from collections import defaultdict


def build_pools(rows: list[dict]) -> dict:
    expression = defaultdict(list)
    scene = defaultdict(list)
    for row in rows:
        label = row['expression']['label']
        if label != 'none':
            expression[label].append(row['unit_id'])
        scene[row['scene_candidate']].append(row['unit_id'])
    return {'main': [row['unit_id'] for row in rows],
            'plain': [row['unit_id'] for row in rows if row['expression']['label']=='none'],
            'expression': dict(expression), 'scene': dict(scene),
            'storage': 'IDs only; exact source text remains in tagged_candidates.jsonl',
            'classification_only': True}


def select_candidates(rows_by_id: dict[str,dict], pools: dict, *,
                      requested_expression: str | None = None,
                      requested_scene: str | None = None,
                      blocked_expressions: set[str] | None = None,
                      prior_eligible_only: bool = True,
                      limit: int = 3) -> dict:
    """Filter an offline pool before ranking; no special-pool fallback to plain text.

    The prior eligibility flag is a snapshot aid, not a fresh runtime gate.
    Returned IDs require the approved runtime projection and current production
    eligibility/applicability checks before any Responder injection.
    """
    blocked = blocked_expressions or set()
    if requested_expression is not None and requested_expression in blocked:
        return {'pool': f'expression:{requested_expression}', 'unit_ids': [],
                'empty_reason': 'expression_blocked', 'production_injection': False}
    if requested_expression is None:
        ids = pools['main']
        pool = 'main'
    elif requested_expression == 'none':
        ids = pools['plain']
        pool = 'plain'
    else:
        ids = pools['expression'].get(requested_expression, [])
        pool = f'expression:{requested_expression}'
    if requested_scene is not None:
        scene_ids = set(pools['scene'].get(requested_scene, []))
        ids = [uid for uid in ids if uid in scene_ids]
        pool += f'+scene:{requested_scene}'
    eligible = [uid for uid in ids
                if not blocked.intersection(rows_by_id[uid]['expression_tags_candidate'])
                and (not prior_eligible_only or rows_by_id[uid]['prior_runtime_eligible'])]
    return {'pool': pool, 'unit_ids': eligible[:limit], 'matching_before_limit': len(eligible),
            'empty_reason': None if eligible else 'no_eligible_candidate',
            'fallback_to_main': False, 'prior_eligible_only': prior_eligible_only,
            'needs_current_runtime_gates_and_approved_projection': True,
            'ranking': 'source order for offline demonstration; not semantic retrieval',
            'production_injection': False}
