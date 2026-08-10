from __future__ import annotations

from collections.abc import Iterable

from .models import AgentProfile, MatchReport, Recommendation


DIRECTION_LABELS = {
    "agent_products": "都在构建 Agent 产品",
    "small_teams": "都服务小团队",
    "indie_building": "都关注独立创业",
    "ai_products": "都在推进 AI 产品商业化",
}

VALUE_LABELS = {
    "async_first": "都接受异步协作",
    "small_experiment": "都愿意先用小实验验证合作",
    "long_term": "都在寻找长期合作关系",
    "revenue_validation": "都重视真实收入验证",
}

CAPABILITY_LABELS = {
    "agent_engineering": "Agent 工程",
    "automation": "工作流自动化",
    "ai_app_development": "AI 应用开发",
    "product_strategy": "产品策略",
    "product_narrative": "产品叙事",
    "user_research": "用户研究",
    "business_scenarios": "真实业务场景",
    "growth_distribution": "增长分发",
    "overseas_content": "海外内容",
    "ux_design": "交互设计",
    "prototype_validation": "原型验证",
    "early_users": "早期用户",
}


def _dedupe(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def analyze_pair(source: AgentProfile, target: AgentProfile) -> MatchReport:
    shared_directions = set(source.direction_codes) & set(target.direction_codes)
    shared_values = set(source.value_codes) & set(target.value_codes)

    common_ground = _dedupe(
        [DIRECTION_LABELS.get(code, "") for code in sorted(shared_directions)]
        + [VALUE_LABELS.get(code, "") for code in sorted(shared_values)]
    )

    source_meets_target = set(source.capability_codes) & set(target.need_codes)
    target_meets_source = set(target.capability_codes) & set(source.need_codes)
    complementarity: list[str] = []
    for code in sorted(source_meets_target):
        label = CAPABILITY_LABELS.get(code, code)
        complementarity.append(f"{source.name}可提供{label}，回应{target.name}的当前需求")
    for code in sorted(target_meets_source):
        label = CAPABILITY_LABELS.get(code, code)
        complementarity.append(f"{target.name}可提供{label}，回应{source.name}的当前需求")

    time_gap = abs(
        source.availability_hours_per_week - target.availability_hours_per_week
    )
    risks = list(target.collaboration_risks)
    if time_gap >= 6:
        risks.append("双方每周可投入时间差距较大")

    unconfirmed = _dedupe(target.open_questions + source.open_questions)[:4]
    score = min(
        96,
        42
        + len(shared_directions) * 8
        + len(shared_values) * 5
        + len(complementarity) * 12,
    )
    if score >= 65:
        recommendation = Recommendation.WORTH_MEETING
        summary = f"{target.name}值得你花 20 分钟进一步了解。"
    elif score >= 50:
        recommendation = Recommendation.KEEP_EXPLORING
        summary = f"与{target.name}有合作线索，但建议先补充关键条件。"
    else:
        recommendation = Recommendation.LOW_FIT
        summary = f"与{target.name}暂未发现足够强的合作基础。"

    confidence = "HIGH" if score >= 85 else "MEDIUM_HIGH" if score >= 65 else "MEDIUM"
    return MatchReport(
        recommendation=recommendation,
        confidence=confidence,
        score=score,
        summary=summary,
        common_ground=common_ground or ["都愿意了解潜在合作伙伴"],
        complementarity=complementarity or ["能力互补仍需在下一轮具体确认"],
        risks=_dedupe(risks) or ["暂未发现明显冲突"],
        unconfirmed=unconfirmed,
    )
