"""Защита от пропадания кеша промпта у провайдера.

Чистая логика без I/O: получает usage каждого запроса и решает, пора ли
остановить сессию. Вся политика — здесь (ядро), адаптеры только показывают
результат (см. AGENTS.md: «единое ядро»).

Правило «плохого» запроса:
- в предыдущем запросе кеш был (cache_read или cache_write > 0);
- текущий промпт достаточно большой (input >= min_input);
- cache_read упал ниже ratio * input предыдущего запроса.

Подряд `streak` плохих запросов → trip. Хороший запрос сбрасывает серию.
Провайдер без кеша (все поля 0) «плохим» не считается — защита не трогает.
После сжатия истории (compress) вызывать reset(): префикс законно меняется.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _Snapshot:
    input_tokens: int
    cache_read: int
    cache_write: int


class CacheGuard:
    def __init__(self, *, min_input: int, ratio: float, streak: int) -> None:
        if streak < 1:
            raise ValueError("streak должен быть >= 1")
        self.min_input = min_input
        self.ratio = ratio
        self.streak = streak
        self._prev: _Snapshot | None = None
        self._bad_run = 0

    @property
    def bad_run(self) -> int:
        """Сколько плохих запросов подряд сейчас (для логов/диагностики)."""
        return self._bad_run

    def reset(self) -> None:
        """Сбросить историю наблюдений (после compress или смены сессии)."""
        self._prev = None
        self._bad_run = 0

    def observe(self, input_tokens: int, cache_read: int, cache_write: int) -> bool:
        """Учесть usage очередного запроса. True — пора останавливаться."""
        cur = _Snapshot(input_tokens, cache_read, cache_write)
        prev, self._prev = self._prev, cur
        if prev is None:
            self._bad_run = 0
            return False
        had_cache = prev.cache_read > 0 or prev.cache_write > 0
        bad = (
            had_cache
            and cur.input_tokens >= self.min_input
            and cur.cache_read < self.ratio * prev.input_tokens
        )
        self._bad_run = self._bad_run + 1 if bad else 0
        return self._bad_run >= self.streak
