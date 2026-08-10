from opc_agent_platform.matching import analyze_pair
from opc_agent_platform.models import Recommendation
from opc_agent_platform.profiles import get_profile


def test_matching_finds_bidirectional_complementarity() -> None:
    report = analyze_pair(get_profile("opc-builder"), get_profile("shen-zhiye"))

    assert report.recommendation == Recommendation.WORTH_MEETING
    assert report.score >= 65
    assert any("Agent 工程" in item for item in report.complementarity)
    assert any("产品叙事" in item for item in report.complementarity)
    assert report.unconfirmed
