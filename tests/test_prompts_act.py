from agent_coder.agent.prompts import SYSTEM_ACT


def test_act_batches_independent_calls():
    assert "Независимые вызовы" in SYSTEM_ACT


def test_act_allows_chained_shell_steps():
    assert "через `&&`" in SYSTEM_ACT


def test_act_no_recheck_after_successful_git():
    assert "повторная проверка не нужна" in SYSTEM_ACT


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
