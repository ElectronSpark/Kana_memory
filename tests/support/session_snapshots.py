"""Full fresh-session snapshot assertions, including native ability routing."""
from copy import deepcopy


def assert_fresh_native_snapshot(store, sid, original):
    """Only session creation may add the exact native routing metadata."""
    saved = store.get_session_questions(sid)
    from kanamemory.kana_ability_bridge import SKILL_TO_ABILITY, ability_unit_id
    from kanamemory.kana_routing import ROUTE_KEY
    carriers = {}
    assert len(saved) == len(original)
    for expected, actual in zip(original, saved):
        copied = deepcopy(actual)
        ability = SKILL_TO_ABILITY.get(expected.kind)
        if ability in {None, "legacy_combined"}:
            assert ROUTE_KEY not in copied.metadata
            assert copied == expected
            continue
        route = copied.metadata.pop(ROUTE_KEY)
        uid = ability_unit_id(expected.content_id, expected.script, ability)
        carrier = carriers.setdefault(uid, store._card_key(expected.content_id, expected.kind, expected.script))
        assert route == {"version": 1, "unit_id": uid, "native_session_id": sid,
                         "carrier_card_id": carrier, "skill": expected.kind}
        assert copied == expected  # Every other field and metadata entry is unchanged.
    return saved


def unrouted(value):
    """A loaded question (or list) as it was built, minus its native route.

    Session creation stamps ROUTE_KEY on native kana questions. The route is
    checked against the question's own ability unit and skill, then removed,
    so the caller's equality still covers every other field and metadata
    entry. Use assert_fresh_native_snapshot where the exact carrier and
    session matter.
    """
    from kanamemory.domain import Question
    from kanamemory.kana_ability_bridge import SKILL_TO_ABILITY, ability_unit_id
    from kanamemory.kana_routing import ROUTE_KEY
    if isinstance(value, (list, tuple)):
        return type(value)(unrouted(item) for item in value)
    if not isinstance(value, Question) or ROUTE_KEY not in value.metadata:
        return value
    ability = SKILL_TO_ABILITY.get(value.kind)
    assert ability not in {None, "legacy_combined"}
    copied = deepcopy(value)
    route = copied.metadata.pop(ROUTE_KEY)
    assert set(route) == {"version", "unit_id", "native_session_id", "carrier_card_id", "skill"}
    assert route["version"] == 1 and route["skill"] == value.kind
    assert route["unit_id"] == ability_unit_id(value.content_id, value.script, ability)
    assert route["native_session_id"] and route["carrier_card_id"]
    return copied
