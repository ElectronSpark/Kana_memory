"""Script-specific structural prerequisites for kana, without learner state.

These are teaching dependencies, not pronunciation equivalence. For example,
ぎゃ requires ぎ and ゃ; learning じ never substitutes for the spelling ぢ.
Small characters and historical signs stay reference items in the caller's
catalogue. This module neither turns them into quizzes nor awards mastery.

The Japan Foundation's kana teaching guide describes the character/sound
system and gradual acquisition, without prescribing a universal order between
hiragana and katakana. This graph therefore keeps both scripts independent.
https://www.jpf.go.jp/j/urawa/j_rsorcs/methods/dl/moji_U2P1_text.pdf
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from unicodedata import normalize


KANA_DEPENDENCY_VERSION = 1
SCRIPTS = ("hiragana", "katakana")
_SMALL = dict(zip("ぁぃぅぇぉっゃゅょゎゕゖ", "あいうえおつやゆよわかけ"))
_SMALL.update({chr(ord(small) + 0x60): chr(ord(large) + 0x60)
               for small, large in tuple(_SMALL.items())})
_COMBINATIONS = frozenset({"yoon", "foreign_combination", "foreign_variant"})
_VOICED = frozenset({"dakuten", "handakuten", "foreign_voiced", "rare_legacy_katakana"})


def kana_entry_id(content_id: str, script: str) -> str:
    if not isinstance(content_id, str) or not content_id or script not in SCRIPTS:
        raise ValueError("假名身份或书写体系无效。")
    return f"kana:{content_id}:{script}"


def _forms(form, category, *, strict):
    if category in _COMBINATIONS:
        # Ordinary contracted sounds use only small ya/yu/yo. Foreign spelling
        # additionally uses small vowels and, for old variants, small wa.
        suffixes = "ゃゅょャュョ" if category == "yoon" else _SMALL
        if len(form) != 2 or form[1] not in suffixes:
            if strict:
                raise ValueError("假名组合字形无效：" + form)
            return ()
        return tuple(form)
    if category == "small":
        if form not in _SMALL:
            if strict:
                raise ValueError("小假名字形无效：" + form)
            return ()
        return (_SMALL[form],)
    if category in _VOICED:
        decomposed = normalize("NFD", form)
        if len(decomposed) != 2 or decomposed[1] not in "\u3099\u309a":
            if strict:
                raise ValueError("浊音或半浊音字形无效：" + form)
            return ()
        return (decomposed[0],)
    if category == "iteration_mark" and form in {"ゞ", "ヾ"}:
        return ("ゝ" if form == "ゞ" else "ヽ", "゛")
    return ()


def build_kana_dependencies(rows: Iterable[Mapping], *, strict=False):
    """Return ``entry_id -> direct prerequisite entry IDs`` in stable order.

    Build from actual normalized spellings, not romaji or homophone classes.
    A partial inventory may omit parents; those missing edges are omitted by
    default. Full bundled-catalogue auditing should use ``strict=True`` so a
    missing constituent, ambiguous identity or cycle cannot pass unnoticed.
    Both eligible quiz entries and readable reference entries are represented.
    """
    identities, forms = {}, {}
    for row in rows:
        for script in SCRIPTS:
            form = row.get(script)
            if not form:
                continue
            form = normalize("NFC", form)
            identity = kana_entry_id(row["id"], script)
            if identity in identities or (script, form) in forms:
                raise ValueError("假名身份或字形重复：" + identity)
            identities[identity] = (script, form, row.get("category"))
            forms[script, form] = identity
    graph = {}
    for identity, (script, form, category) in identities.items():
        prerequisites = []
        for dependency_form in _forms(form, category, strict=strict):
            dependency = forms.get((script, dependency_form))
            if dependency is None:
                if strict:
                    raise ValueError(f"假名 {form} 缺少同书写体系的前置：{dependency_form}")
                continue
            if dependency == identity:
                raise ValueError("假名不能依赖自身：" + identity)
            if dependency not in prerequisites:
                prerequisites.append(dependency)
        graph[identity] = tuple(prerequisites)
    kana_dependency_order(graph)
    return graph


def kana_dependency_order(graph: Mapping[str, Iterable[str]], targets=None):
    """Return a stable prerequisite-first closure; reject unknowns and cycles."""
    selected = graph if targets is None else (targets,) if isinstance(targets, str) else targets
    visited, active, result = set(), set(), []

    def visit(identity):
        if identity in visited:
            return
        if identity not in graph:
            raise ValueError("假名前置身份不存在：" + str(identity))
        if identity in active:
            raise ValueError("假名前置关系成环：" + str(identity))
        active.add(identity)
        for dependency in graph[identity]:
            visit(dependency)
        active.remove(identity)
        visited.add(identity)
        result.append(identity)

    for identity in selected:
        visit(identity)
    return tuple(result)


def _practice_graph(rows, *, strict):
    # Local import keeps the structural graph usable while domain builds its
    # other catalogues. Eligibility is the app's existing visual-quiz policy.
    from .domain import _eligible_kana

    rows = list(rows)
    full = build_kana_dependencies(rows, strict=strict)
    eligible = {kana_entry_id(row["id"], script) for row in rows for script in SCRIPTS
                if _eligible_kana(row, script, True, audio_only=False, kind="kana_choice")}
    graph, paths = {}, {}
    for identity in full:
        if identity not in eligible:
            continue
        expanded = []

        def walk(node, path):
            if node in eligible:
                expanded.append((*path, node))
            else:
                for dependency in full[node]:
                    walk(dependency, (*path, node))

        for parent in full[identity]:
            walk(parent, (identity,))
        paths[identity] = tuple(expanded)
        graph[identity] = tuple(dict.fromkeys(path[-1] for path in expanded))
    kana_dependency_order(graph)
    return graph, paths


def practice_kana_dependencies(rows: Iterable[Mapping], *, strict=False):
    """Return only quiz identities, bypassing non-quiz reference nodes.

    A small character cannot earn a quiz score in the current app. Requiring
    such a score would deadlock compounds. Its full-sized letter is therefore
    the practice prerequisite (ぎゃ -> ぎ, や), while the compound's first lesson
    must still explain the small glyph and one-mora reading. This is not a
    statement that や and ゃ are interchangeable spellings or memory units.
    """
    return _practice_graph(rows, strict=strict)[0]


def practice_kana_dependency_paths(rows: Iterable[Mapping], *, strict=False):
    """Return every expansion path from target through reference to quiz parent.

    Direct edges have two identities. A reference expansion such as
    ぎゃ -> ゃ -> や has three. This makes the full-versus-practice policy auditable.
    """
    return _practice_graph(rows, strict=strict)[1]
