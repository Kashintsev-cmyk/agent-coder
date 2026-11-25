from agent_coder.agent.modes import Mode


def test_mode_values_are_stable():
    # Значения пишутся в сохранённые сессии (session.json["mode"]),
    # менять их нельзя без миграции.
    assert {m.value for m in Mode} == {"plan", "act", "audit"}


def test_prompts_reexports_same_mode():
    from agent_coder.agent import prompts
    from agent_coder.agent.modes import Mode as ModesMode

    assert prompts.Mode is ModesMode
