import string

from holdem.i18n import LANGUAGES, TEXTS, translate


def placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_language_defines_the_same_keys_and_placeholders():
    reference = TEXTS[LANGUAGES[0]]
    for language in LANGUAGES:
        assert TEXTS[language].keys() == reference.keys()
        for key, text in TEXTS[language].items():
            assert placeholders(text) == placeholders(reference[key]), (language, key)


def test_translate_formats_values():
    assert translate("zh", "btn_raise", amount=120) == "加注到 120"
    assert translate("en", "blinds", small=1, big=2) == "Blinds 1 / 2"
