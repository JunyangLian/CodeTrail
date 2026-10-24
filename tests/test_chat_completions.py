import json
import os
from unittest.mock import patch

import pytest

from pico.cli import build_agent, build_arg_parser
from pico.evaluation.metrics import _make_provider_client
from pico.providers.clients import OpenAICompatibleModelClient


class JsonResponse:
    headers = {"Content-Type": "application/json"}

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.data).encode("utf-8")


def test_chat_completions_request_and_usage():
    client = OpenAICompatibleModelClient(
        model="configured-model", base_url="https://api.siliconflow.cn/v1/",
        api_key="test-key", temperature=0.2, timeout=30, api_format="chat_completions",
    )
    response = JsonResponse({
        "choices": [{"message": {"content": "<final>ok</final>", "reasoning_content": "private reasoning"}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
    })
    with patch("urllib.request.urlopen", return_value=response) as urlopen:
        answer = client.complete("hello", 128, prompt_cache_key="unused", prompt_cache_retention="in_memory")

    request = urlopen.call_args.args[0]
    assert request.full_url == "https://api.siliconflow.cn/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer test-key"
    assert json.loads(request.data) == {
        "model": "configured-model", "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 128, "stream": False, "temperature": 0.2,
    }
    assert answer == "<final>ok</final>"
    assert client.supports_prompt_cache is False
    assert client.last_completion_metadata["input_tokens"] == 20
    assert client.last_completion_metadata["output_tokens"] == 5
    assert client.last_completion_metadata["total_tokens"] == 25


def test_chat_format_does_not_send_responses_cache_fields_on_other_hosts():
    client = OpenAICompatibleModelClient(
        model="test", base_url="https://api.openai.com/v1", api_key="test-key",
        temperature=None, timeout=30, api_format="chat_completions",
    )
    with patch("urllib.request.urlopen", return_value=JsonResponse({"choices": [{"message": {"content": "ok"}}]})) as call:
        assert client.complete("hello", 16, prompt_cache_key="unused") == "ok"
    payload = json.loads(call.call_args.args[0].data)
    assert "prompt_cache_key" not in payload
    assert "temperature" not in payload
    assert client.supports_prompt_cache is False


@pytest.mark.parametrize("api_format", ["unknown", "", None])
def test_invalid_api_format_fails_before_network_call(api_format):
    with pytest.raises(ValueError, match="api_format"):
        OpenAICompatibleModelClient("test", "https://example.com", "", None, 30, api_format=api_format)


@pytest.mark.parametrize("override, expected", [(None, "chat_completions"), ("responses", "responses")])
def test_project_config_and_cli_api_format_precedence(tmp_path, override, expected):
    (tmp_path / ".env").write_text(
        "PICO_PROVIDER=openai\nPICO_OPENAI_API_BASE=https://api.siliconflow.cn/v1\n"
        "PICO_OPENAI_MODEL=configured-model\nPICO_OPENAI_API_KEY=test-key\n"
        "PICO_OPENAI_API_FORMAT=chat_completions\n", encoding="utf-8",
    )
    argv = ["--cwd", str(tmp_path)]
    if override:
        argv += ["--api-format", override]
    with patch.dict(os.environ, {}, clear=True):
        agent = build_agent(build_arg_parser().parse_args(argv))
    assert agent.model_client.api_format == expected
    assert agent.model_client.model == "configured-model"


def test_experiment_client_uses_same_protocol_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch.dict(os.environ, {
        "PICO_OPENAI_API_KEY": "test-key", "PICO_OPENAI_MODEL": "configured-model",
        "PICO_OPENAI_API_BASE": "https://api.siliconflow.cn/v1",
        "PICO_OPENAI_API_FORMAT": "chat_completions",
    }, clear=True):
        client = _make_provider_client("gpt")
    assert client.api_format == "chat_completions"
    assert client.model == "configured-model"
