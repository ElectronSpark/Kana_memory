"""Additive, version-one example annotations for pinned local course senses."""
import hashlib
import json

from .example_expansion_signatures import SIGNATURES


def lesson_signature(item):
    fields = ('id', 'family', 'domain', 'revision', 'title', 'meaning', 'reading', 'examples')
    value = {key: item.get(key) for key in fields}
    value['instruction_examples'] = item.get('instruction', {}).get('examples')
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def expansion_rows(item):
    """Only authored senses get additions; saved/custom material remains intact."""
    if SIGNATURES.get(item['id']) != lesson_signature(item):
        return ()
    # Imports are deferred so reading a saved/custom-only pack is inexpensive.
    from .example_expansion_grammar_a import ROWS as grammar_a
    from .example_expansion_grammar_a_tail import ROWS as grammar_a_tail
    from .example_expansion_grammar_b import ROWS as grammar_b
    from .example_expansion_words import ROWS as words
    from .example_expansion_pronunciation import ROWS as pronunciation
    for catalogue in (grammar_a, grammar_a_tail, grammar_b, words, pronunciation):
        if item['id'] in catalogue:
            return catalogue[item['id']]
    return ()
