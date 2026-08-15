import json

import httpx
import pytest

from opc_agent_platform.deepseek import DeepSeekAPIError, DeepSeekClient
from opc_agent_platform.matching import analyze_pair
from opc_agent_platform.profiles import get_profile


def _response(content: dict, prompt_tokens: int = 20) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(content)}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 10,
                "total_tokens": prompt_tokens + 10,
                "completion_tokens_details": {"reasoning_tokens": 4},
            },
        },
    )


@pytest.mark.asyncio
async def test_deepseek_agent_decision_uses_compatible_chat_api() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-v4-flash"
        assert payload["thinking"] == {"type": "enabled"}
        return _response(
            {
                "summary": "双方能力互补。",
                "short_message": "我可以负责工程，你可以负责产品验证。",
                "common_ground": ["都服务小团队"],
                "complementarity": ["工程与产品能力互补"],
                "risks": ["决策机制尚未确认"],
                "questions": ["每周投入多少时间"],
            }
        )

    client = DeepSeekClient(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    source = get_profile("opc-builder")
    target = get_profile("shen-zhiye")
    decision, usage = await client.generate_agent_decision(
        receiver=target,
        sender=source,
        request={"round": 1, "intent": "introduce_opc"},
        baseline=analyze_pair(source, target),
    )

    assert decision.summary == "双方能力互补。"
    assert usage.total_tokens == 30
    assert usage.reasoning_tokens == 4


@pytest.mark.asyncio
async def test_deepseek_output_rejects_contact_details() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return _response(
            {
                "summary": "请联系 13800000001",
                "short_message": "继续沟通",
                "common_ground": [],
                "complementarity": [],
                "risks": [],
                "questions": [],
            }
        )

    client = DeepSeekClient(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    source = get_profile("opc-builder")
    target = get_profile("shen-zhiye")

    with pytest.raises(DeepSeekAPIError, match="contact details"):
        await client.generate_agent_decision(
            receiver=target,
            sender=source,
            request={"round": 1, "intent": "introduce_opc"},
            baseline=analyze_pair(source, target),
        )


@pytest.mark.asyncio
async def test_employee_chat_prompt_includes_shared_context_and_runtime_evidence() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        user_input = json.loads(payload["messages"][1]["content"])
        assert user_input["sharedContext"]["decisions"] == ["先验证一个真实用户需求"]
        assert user_input["sharedContext"]["openQuestions"] == ["谁负责第一版实现"]
        assert user_input["senderRuntime"] == {"provider": "ollama", "model": "qwen3:4b"}
        assert user_input["recipientRuntime"] == {"provider": "ollama", "model": "qwen3:1.7b"}
        return _response({"action": "REPLY", "reply": "我会基于已确认的真实用户需求，先负责第一版实现。"})

    client = DeepSeekClient(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    decision, _ = await client.generate_employee_chat(
        receiver=get_profile("shen-zhiye"),
        sender=get_profile("opc-builder"),
        request={
            "conversationTopic": "继续推进两周实验",
            "message": "请回应下一步",
            "sharedContext": {
                "goal": "继续推进两周实验",
                "knownFacts": ["双方已建立连接"],
                "decisions": ["先验证一个真实用户需求"],
                "openQuestions": ["谁负责第一版实现"],
            },
            "senderRuntime": {"provider": "ollama", "model": "qwen3:4b"},
            "recipientRuntime": {"provider": "ollama", "model": "qwen3:1.7b"},
        },
    )

    assert decision.action == "REPLY"


@pytest.mark.asyncio
async def test_ollama_agent_decision_uses_native_json_chat_api() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        assert "Authorization" not in request.headers
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3:4b"
        assert payload["think"] is False
        assert payload["format"]["type"] == "object"
        assert set(payload["format"]["required"]) == {
            "summary",
            "short_message",
            "common_ground",
            "complementarity",
            "risks",
            "questions",
        }
        assert payload["format"]["properties"]["short_message"]["type"] == "string"
        assert payload["stream"] is False
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "summary": "双方可以先做小实验。",
                            "short_message": "建议先验证一个真实需求。",
                            "common_ground": ["都在做 Agent 产品"],
                            "complementarity": ["产品与工程互补"],
                            "risks": ["边界仍需确认"],
                            "questions": ["如何验收"],
                        },
                        ensure_ascii=False,
                    ),
                },
                "prompt_eval_count": 120,
                "eval_count": 36,
            },
        )

    client = DeepSeekClient(
        api_key="",
        base_url="http://ollama.test",
        model="qwen3:4b",
        provider="ollama",
        transport=httpx.MockTransport(handler),
    )
    source = get_profile("opc-builder")
    target = get_profile("shen-zhiye")
    decision, usage = await client.generate_agent_decision(
        receiver=target,
        sender=source,
        request={"round": 1, "intent": "introduce_opc"},
        baseline=analyze_pair(source, target),
    )

    assert client.provider == "ollama"
    assert decision.summary == "双方可以先做小实验。"
    assert usage.total_tokens == 156


@pytest.mark.asyncio
async def test_ollama_public_inquiry_uses_public_profile() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3:1.7b"
        assert payload["think"] is False
        assert payload["options"]["num_predict"] == 240
        assert "/no_think" in payload["messages"][0]["content"]
        assert "publicProfile" in json.loads(payload["messages"][1]["content"])
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {"answer": "我是一个基于本地模型运行的 OPC Agent。"},
                        ensure_ascii=False,
                    ),
                },
                "prompt_eval_count": 80,
                "eval_count": 18,
            },
        )

    client = DeepSeekClient(
        api_key="",
        base_url="http://ollama.test",
        model="qwen3:1.7b",
        provider="ollama",
        transport=httpx.MockTransport(handler),
    )
    decision, usage = await client.generate_public_inquiry(
        profile=get_profile("shen-zhiye"),
        question="你是谁？",
    )

    assert decision.answer == "我是一个基于本地模型运行的 OPC Agent。"
    assert usage.total_tokens == 98


def test_environment_can_select_ollama_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:4b")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    client = DeepSeekClient.from_environment()

    assert client is not None
    assert client.provider == "ollama"
    assert client.model == "qwen3:4b"
    assert client.base_url == "http://127.0.0.1:11434"
    assert client.trust_env is False
