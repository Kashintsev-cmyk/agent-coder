"""Тесты strip_dsml: очистка нативной DSML-разметки DeepSeek.

Регрессия: DeepSeek отдаёт теги с FULLWIDTH VERTICAL LINE `｜` (U+FF5C)
вместо ASCII-пайпа, и старая реализация (только `|`) их не видела —
разметка утекала в Telegram и оставалась в истории сессии.
"""

from __future__ import annotations

from agent_coder.log_safety import strip_dsml

# Реальный фрагмент из сессии 1a9507ff (теги с U+FF5C).
FULLWIDTH_LEAK = (
    "<\uff5c\uff5cDSML\uff5c\uff5c calls>\n"
    '<\uff5c\uff5cDSML\uff5c\uff5c invoke name="list_files">\n'
    '<\uff5c\uff5cDSML\uff5c\uff5c parameter name="path" string="true">.'
    "</\uff5c\uff5cDSML\uff5c\uff5c parameter>\n"
    "</\uff5c\uff5cDSML\uff5c\uff5c invoke>\n"
    "</\uff5c\uff5cDSML\uff5c\uff5c calls>"
)

ASCII_LEAK = (
    '<||DSML||invoke name="read_file">'
    '<||DSML||parameter name="path">a.py</||DSML||parameter>'
    "</||DSML||invoke>"
)


class TestStripDsml:
    def test_fullwidth_tags_removed(self):
        """Основная регрессия: U+FF5C-разметка должна срезаться целиком."""
        out = strip_dsml(FULLWIDTH_LEAK)
        assert "DSML" not in out
        assert "\uff5c" not in out
        assert out == ""

    def test_ascii_tags_removed(self):
        out = strip_dsml(ASCII_LEAK)
        assert "DSML" not in out
        assert "read_file" not in out

    def test_plain_text_untouched(self):
        text = "Готово: файл переименован."
        assert strip_dsml(text) == text

    def test_empty_and_none_safe(self):
        assert strip_dsml("") == ""
        assert strip_dsml(None) is None

    def test_text_before_marker_kept(self):
        """Осмысленный текст до разметки сохраняем, мусор после — режем."""
        out = strip_dsml(f"Читаю файлы.\n{FULLWIDTH_LEAK}")
        assert out == "Читаю файлы."

    def test_dsml_word_in_prose_kept(self):
        """Слово DSML без тегов — это обычный текст, не зачищаем."""
        text = "В логе встретился маркер DSML, но тегов нет."
        assert strip_dsml(text) == text

    def test_stray_open_tag_without_close(self):
        """Обрезанный поток: открывающий тег без закрывающего."""
        out = strip_dsml('<||DSML||invoke name="x"')
        assert out == ""

    def test_result_is_stripped(self):
        assert strip_dsml(f"  {ASCII_LEAK}  ") == ""

    def test_inline_prose_with_tags_not_truncated(self):
        """Модель *упоминает* разметку в прозе — объяснение не калечим."""
        text = (
            "Нашёл баг: strip_dsml не видел теги "
            "<\uff5c\uff5cDSML\uff5c\uff5c calls>\n"
            '<\uff5c\uff5cDSML\uff5c\uff5c invoke name="list_files">\n'
            '<\uff5c\uff5cDSML\uff5c\uff5c parameter name="path">.</\uff5c\uff5cDSML\uff5c\uff5c parameter>\n'
            "</\uff5c\uff5cDSML\uff5c\uff5c invoke>\n"
            "</\uff5c\uff5cDSML\uff5c\uff5c calls>"
            " — и это утекало в чат."
        )
        out = strip_dsml(text)
        assert "DSML" not in out
        assert "\uff5c" not in out
        assert out.startswith("Нашёл баг: strip_dsml не видел теги")
        assert out.endswith("— и это утекало в чат.")

    def test_inline_mention_of_single_tag_kept(self):
        """Одиночное упоминание открывающего тега в тексте не срезает прозу."""
        text = "В логе встретился DSML-формат (<||DSML||invoke>) — убрали фильтром."
        out = strip_dsml(text)
        assert out == text

    def test_text_after_original_markup_survives(self):
        """Текст ПОСЛЕ разметки сохраняется (не режем всё до конца)."""
        text = f"{FULLWIDTH_LEAK}\nГотово: файл переименован."
        out = strip_dsml(text)
        assert "DSML" not in out
        assert out == "Готово: файл переименован."
