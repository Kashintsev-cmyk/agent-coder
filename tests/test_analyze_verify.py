"""Тесты benchmark/analyze_verify.evaluate: error-записи не искажают метрику.

P0b: раньше run_verify при chat-exception оставлял verify_results пустым,
analyze_verify показывал 0/0 как "модель ничего не нашла". Теперь
verify_results содержит status="error", а evaluate считает их отдельно
и НЕ записывает в unmatched (иначе — ложный FP).
"""

from __future__ import annotations

from benchmark.analyze_verify import evaluate


def _gold(**kw):
    base = {
        "id": "f01",
        "finding_text": "alpha beta",
        "manual_label": "confirmed",
    }
    base.update(kw)
    return base


def test_error_status_not_counted_as_unmatched() -> None:
    results = [
        {"status": "error", "text": "LLM: TypeError: top_k"},
        {"status": "confirmed", "text": "alpha beta"},
    ]
    out = evaluate(results, [_gold()])
    assert out["errors"] == 1
    assert out["n_matched"] == 1
    assert out["unmatched"] == 0
    assert out["precision"] == 1.0


def test_error_only_run_has_zero_metrics_and_explicit_errors() -> None:
    results = [{"status": "error", "text": "LLM: timeout after 180s"}]
    out = evaluate(results, [_gold()])
    assert out["errors"] == 1
    assert out["n_matched"] == 0
    assert out["unmatched"] == 0
    assert "precision" not in out


def test_unparsed_also_not_counted_as_unmatched() -> None:
    results = [{"status": "unparsed", "text": "garbage without verdicts"}]
    out = evaluate(results, [_gold()])
    assert out["unparsed"] == 1
    assert out["errors"] == 0
    assert out["unmatched"] == 0
