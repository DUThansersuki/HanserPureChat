import json

import httpx
import pytest

from hanser_qq.headless import install_backend
from hanser_qq.model_routes import apply_responder_gateway, model_route_status


@pytest.mark.asyncio
async def test_only_responder_uses_explicit_local_gateway(config, tmp_path):
    install_backend(config.backend.package_path)
    from hanser_agent.config import ModelProfileConfig
    from hanser_agent.model_gateway import ModelGateway
    from hanser_agent.models import ChatMessage
    from types import SimpleNamespace
    planner = ModelProfileConfig(endpoint='https://api.deepseek.com/v1', model='deepseek-flash', api_key='ds-test')
    original = ModelProfileConfig(endpoint=planner.endpoint, model=planner.model, api_key='ds-test', max_tokens=1024)
    settings = SimpleNamespace(planner=planner, responder=original)
    config.responder_gateway.enabled = True
    config.responder_gateway.api_key_file = tmp_path / 'local-agent.token'
    config.responder_gateway.api_key_file.write_text('local-test-token', encoding='utf-8')
    apply_responder_gateway(settings, config)
    assert settings.planner is planner and settings.responder is not original
    assert settings.responder.max_tokens == original.max_tokens
    requests = []
    def upstream(request):
        requests.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': '收到'}}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    gateway = ModelGateway({'planner': planner, 'responder': settings.responder}, client=client)
    messages = [ChatMessage(role='user', content='测试')]
    await gateway.generate('planner', messages)
    await gateway.generate('responder', messages)
    assert str(requests[0].url) == 'https://api.deepseek.com/v1/chat/completions'
    assert str(requests[1].url) == 'http://127.0.0.1:3000/v1/chat/completions'
    assert requests[0].headers['Authorization'] == 'Bearer ds-test'
    assert requests[1].headers['Authorization'] == 'Bearer local-test-token'
    assert json.loads(requests[1].content)['model'] == 'auto-code'
    assert 'api_key' not in json.dumps(model_route_status(settings, config))
    await client.aclose()


def test_gateway_token_missing_is_explicit_and_route_defaults_off(config, tmp_path):
    from types import SimpleNamespace
    settings = SimpleNamespace(responder=object())
    before = settings.responder
    apply_responder_gateway(settings, config)
    assert settings.responder is before
    config.responder_gateway.enabled = True
    config.responder_gateway.api_key_file = tmp_path / 'missing.token'
    with pytest.raises(FileNotFoundError):
        apply_responder_gateway(settings, config)
    config.responder_gateway.api_key_file.write_text('', encoding='utf-8')
    with pytest.raises(ValueError, match='empty'):
        apply_responder_gateway(settings, config)
