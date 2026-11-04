"""Фаза B (provenance): --project / task['project'] → results.json.

Дизамбигуация проектов: одна установка agent-coder гоняет бенчмарки
нескольких проектов (agent_coder, na_shtile, weather-bot, futures_3.0).
Поле `project` в results.json — единственный способ отличить их
постфактум (физический путь worktree эфемерен).
"""

from benchmark.runner import _project_for


class TestProjectFor:
    def test_cli_wins(self):
        assert _project_for({"project": "task_level"}, "cli_level") == "cli_level"

    def test_task_when_no_cli(self):
        assert _project_for({"project": "na_shtile"}, None) == "na_shtile"

    def test_none_when_neither(self):
        assert _project_for({}, None) is None

    def test_empty_cli_falls_back_to_task(self):
        assert _project_for({"project": "x"}, "") == "x"

    def test_empty_both_yields_none(self):
        assert _project_for({}, "") is None
