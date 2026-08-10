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
