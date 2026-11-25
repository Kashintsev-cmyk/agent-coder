from agent_coder.agent.prompts import SYSTEM_ACT


def test_act_batches_independent_calls():
    assert "Независимые вызовы" in SYSTEM_ACT


def test_act_allows_chained_shell_steps():
    assert "через `&&`" in SYSTEM_ACT


def test_act_no_recheck_after_successful_git():
    assert "повторная проверка не нужна" in SYSTEM_ACT
