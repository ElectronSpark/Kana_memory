"""Expressive spelling does not become an unconverted kana inside romaji."""
import pytest
from kanamemory.romanization import romanize_kana


@pytest.mark.parametrize('text,expected', [
    ('はっやい', 'hayyai'), ('はっやーい', 'hayyāi'),
    ('ハッヤイ', 'hayyai'), ('ハッヤーイ', 'hayyāi'),
    ('ｱｯﾔ', 'ayya'), ('っや', 'yya'),
    ('あっ', 'aっ'), ('っあ', 'っa'), ('っー', 'っー'),
])
def test_y_onset_is_mechanical_but_unknown_final_or_vowel_onsets_are_retained(text, expected):
    assert romanize_kana(text) == expected
