"""Regression tests for the client-side WebUI translations."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TEMPLATE = ROOT / "bambucam" / "web" / "templates" / "index.html"
STATIC_JS = ROOT / "bambucam" / "web" / "static" / "js"
I18N = STATIC_JS / "i18n.js"


def _dictionary_keys(source: str, start: str, end: str) -> set[str]:
    block = source.split(start, 1)[1].split(end, 1)[0]
    return set(re.findall(r'^\s+"([^"]+)":', block, re.MULTILINE))


def test_english_and_german_dictionaries_have_identical_keys():
    source = I18N.read_text(encoding="utf-8")
    english = _dictionary_keys(source, "    en: {", "    de: {")
    german = _dictionary_keys(source, "    de: {", "  };\n")

    assert english
    assert english == german


def test_all_static_translation_keys_are_defined():
    i18n_source = I18N.read_text(encoding="utf-8")
    defined = _dictionary_keys(i18n_source, "    en: {", "    de: {")
    sources = [
        TEMPLATE.read_text(encoding="utf-8"),
        (STATIC_JS / "app.js").read_text(encoding="utf-8"),
        (STATIC_JS / "features.js").read_text(encoding="utf-8"),
    ]
    used: set[str] = set()
    for source in sources:
        used.update(re.findall(r'data-i18n(?:-[\w-]+)?="([^"]+)"', source))
        used.update(re.findall(r"\btr\([\"']([^\"']+)[\"']", source))

    assert used - defined == set()


def test_webui_defaults_to_english_and_loads_i18n_before_application_code():
    template = TEMPLATE.read_text(encoding="utf-8")

    assert '<html lang="en">' in template
    assert 'id="language-select"' in template
    assert template.index("js/i18n.js") < template.index("js/app.js")
    assert 'value="en"' in template
    assert 'value="de"' in template


def test_language_detection_and_persistence_are_present():
    source = I18N.read_text(encoding="utf-8")

    assert 'navigator.languages || [navigator.language || "en"]' in source
    assert "localStorage.getItem(STORAGE_KEY)" in source
    assert "localStorage.setItem(STORAGE_KEY, language)" in source
    assert 'CustomEvent("bambucam:languagechange"' in source
