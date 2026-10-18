"""Тесты типов LLM-слоя: format_cost, Usage."""

from __future__ import annotations

from agent_coder.llm.types import Usage, format_cost


class TestFormatCost:
    def test_zero(self):
        assert format_cost(0) == "0.0000 ₽"
        assert format_cost(0.0) == "0.0000 ₽"

    def test_round_values(self):
        assert format_cost(0.6126) == "0.6126 ₽"

    def test_long_fraction_no_trailing_zeros(self):
        # Хвостовые нули обрезаются
        assert format_cost(0.08491812) == "0.08491812 ₽"

    def test_small_value(self):
        assert format_cost(0.00001234) == "0.00001234 ₽"

    def test_integer_value(self):
        assert format_cost(1.0) == "1 ₽"
        assert format_cost(42.0) == "42 ₽"

    def test_ends_with_ruble_sign(self):
        assert format_cost(3.14).endswith(" ₽")


class TestUsage:
    def test_defaults(self):
        u = Usage()
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.cost == 0.0

    def test_add(self):
        a = Usage(input_tokens=10, output_tokens=5, cache_read_tokens=2, cost=0.5)
        b = Usage(input_tokens=1, output_tokens=2, cache_write_tokens=3, cost=0.25)
        c = a + b
        assert c.input_tokens == 11
        assert c.output_tokens == 7
        assert c.cache_read_tokens == 2
        assert c.cache_write_tokens == 3
        assert c.cost == 0.75
