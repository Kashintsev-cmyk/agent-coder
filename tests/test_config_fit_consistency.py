"""Инвариант: RECOMMENDED[mode] должен подходить для mode по MODEL_MODE_FIT."""


def test_recommended_models_fit_their_mode():
    """🎯 не должен указывать на модель, которой нет в FIT[mode]."""
    from agent_coder.config import Config

    for mode, model in Config.RECOMMENDED_MODEL_BY_MODE.items():
        if mode == "verify":
            continue
        fits = Config.MODEL_MODE_FIT.get(model, [])
        assert mode in fits, (
            f"RECOMMENDED[{mode!r}]={model!r} не подходит: FIT={fits!r}"
        )


def test_v4_1_flash_fits_audit():
    """Регресс B9.2g: v4.1-flash — стабильный winner audit (48/58 findings)."""
    from agent_coder.config import Config

    assert "audit" in Config.MODEL_MODE_FIT["deepseek/deepseek-v4.1-flash"]


def test_haiku_in_audit_fit():
    """haiku тоже подходит для audit по FIT, но не выбрана override-ом (нестабильна)."""
    from agent_coder.config import Config

    assert "audit" in Config.MODEL_MODE_FIT["anthropic/claude-haiku-5.5"]
