"""Opt-in Responder routing through the personal New API gateway."""
from dataclasses import replace
from pathlib import Path
import os
import subprocess


def start_responder_gateway(config):
    if not (config.responder_gateway.enabled or config.rag_selector.enabled):
        return
    script = Path('C:/AI-Gateway/scripts/start-all.ps1')
    if not script.is_file():
        raise ValueError('Local AI Gateway is not installed; complete its setup first')
    subprocess.run(['powershell.exe', '-NoProfile', '-File', str(script)], check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def apply_responder_gateway(settings, config):
    route = config.responder_gateway
    if not route.enabled:
        return
    token = route.api_key_file.read_text(encoding='utf-8-sig').strip()
    if not token:
        raise ValueError('New API local-agent token file is empty')
    # Preserve the original Planner and all generation/context budgets. Only this
    # explicitly configured QQ consumer accepts the fixed loopback gateway URL.
    settings.responder = replace(settings.responder, endpoint=route.endpoint,
        api_key=token, model=route.model)


def model_route_status(settings, config):
    return {'planner': {'endpoint': settings.planner.endpoint, 'model': settings.planner.model},
            'responder': {'endpoint': settings.responder.endpoint, 'model': settings.responder.model,
                          'local_gateway': config.responder_gateway.enabled},
            'rag_selector': {'enabled': config.rag_selector.enabled, 'endpoint': config.rag_selector.endpoint,
                             'model': config.rag_selector.model, 'upstream_model': 'gpt-6.1-terra',
                             'history_rounds': 10, 'local_top_k_per_lane': 10, 'final_top_k_per_lane': 3}}
