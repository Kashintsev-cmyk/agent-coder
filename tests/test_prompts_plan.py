from agent_coder.agent.prompts import SYSTEM_PLAN


def test_plan_limits_exploration_to_relevant_scope():
    text = " ".join(SYSTEM_PLAN.split())
    assert "определи минимальную область" in text
    assert "независимые чтения выполняй одним пакетом" in text
    assert "останавливайся, когда собраны достаточные факты" in text


def test_plan_uses_complexity_to_decide_when_to_plan():
    text = " ".join(SYSTEM_PLAN.split())
    assert "Для простой локальной задачи дай прямой ответ без формального плана" in text
    assert (
        "межмодульной, рискованной или высокоэффективной задачи дай краткий план"
        in text
    )
    assert "цель, область, шаги, проверка и ключевые риски" in text
    assert "Подробный план составляй только если его запросили или он нужен" in text
