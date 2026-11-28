from agent_coder.agent.prompts import SYSTEM_ACT


def test_act_batches_independent_calls():
    assert "Независимые вызовы" in SYSTEM_ACT


def test_act_keeps_dependent_actions_sequential_and_respects_approval():
    text = " ".join(SYSTEM_ACT.split())
    assert "Зависимые действия выполняй последовательно" in text
    assert "Соблюдай разрешения и подтверждения каждого инструмента" in text
    assert "не сцепляй команды, чтобы обойти их" in text


def test_act_repeats_verification_only_after_code_changes():
    text = " ".join(SYSTEM_ACT.split())
    assert "после успеха повторяй только если код изменился" in text


def test_act_groups_edits_and_runs_relevant_tests_once():
    text = " ".join(SYSTEM_ACT.split())
    assert "Группируй связанные правки в один цельный набор" in text
    assert "Запускай релевантные тесты после него один раз" in text


def test_act_work_state_records_only_substantive_results():
    text = " ".join(SYSTEM_ACT.split())
    assert (
        "сохраняй только найденную причину, выполненную правку или результат тестов"
        in text
    )
    assert "Просьбу пользователя и рутинные шаги не записывай" in text
