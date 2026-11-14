"""Pure availability rule (design 08 section 7.2): states, dictionary words, local material."""
import pytest

from kanamemory import content_availability as rules
from kanamemory import content_library_rules as library_rules
from support.library_availability_support import EXT_LIBRARY, registry


def catalogue(**kw):
    values = {"ids": frozenset({"grammar:a", "grammar:b", "grammar:ext", "grammar:new"}),
              "providers": {"grammar:ext": EXT_LIBRARY}}
    values.update(kw)
    return rules.Catalogue(**values)


def state(cid, *, enabled=True, reg=True, **row):
    return rules.content_availability(catalogue(**row.pop("cat", {})), registry(enabled) if reg else None,
                                      {"content_id": cid, **row})


def test_catalogue_items_of_enabled_libraries_are_available():
    assert state("grammar:a") == state("grammar:ext") == rules.AVAILABLE


def test_disabled_library_and_missing_catalogue_items_are_orphaned():
    assert state("grammar:ext", enabled=False) == rules.ORPHANED
    assert state("grammar:removed") == rules.ORPHANED
    assert state("") == rules.ORPHANED


def test_registry_entry_missing_or_damaged_makes_its_items_orphans():
    damaged = registry()
    damaged["libraries"][1]["state"] = "damaged"
    view = catalogue()
    assert rules.content_availability(view, damaged, {"content_id": "grammar:ext"}) == rules.ORPHANED
    core_only = library_rules.default_registry()
    assert rules.content_availability(view, core_only, {"content_id": "grammar:ext"}) == rules.ORPHANED


@pytest.mark.parametrize("cid", ["local:ai:material:1", "local:personal:x"])
def test_local_material_is_always_available(cid):
    assert state(cid) == rules.AVAILABLE
    assert state(cid, enabled=False) == rules.AVAILABLE


def test_without_registry_nothing_is_orphaned():
    assert state("grammar:removed", reg=False) == rules.AVAILABLE
    assert state("grammar:ext", reg=False) == rules.AVAILABLE


def test_dictionary_only_words_follow_the_dictionary_layer():
    cid = "word:jmdict:100:みず:1"
    assert rules.dictionary_word_id(cid) == "jmdict:100"
    assert rules.dictionary_word_id("word:mizu") is None
    assert state(cid, dictionary_only=True) == rules.AVAILABLE  # no lookup: layer not removable
    assert state(cid) == rules.ORPHANED                          # not a dictionary snapshot
    present = {"word_exists": lambda word_id: word_id == "jmdict:100"}
    assert state(cid, cat=present) == rules.AVAILABLE
    assert state("word:jmdict:200:やま:1", dictionary_only=True, cat=present) == rules.ORPHANED


def test_missing_required_dependency_blocks_unless_eligibility_was_earned():
    assert state("grammar:a", depends_on=["grammar:ext"], enabled=False) == rules.BLOCKED_DEPENDENCY
    assert state("grammar:a", depends_on=["grammar:gone"]) == rules.BLOCKED_DEPENDENCY
    earned = {"ready_ids": frozenset({"grammar:gone"})}
    assert state("grammar:a", depends_on=["grammar:gone"], cat=earned) == rules.AVAILABLE
    # Kana and other non-library dependencies come from the dictionary layer.
    assert state("grammar:a", depends_on=["kana:kana-3042:hiragana", "grammar:b"]) == rules.AVAILABLE
    # Catalogue dependencies apply when the row brings none.
    deps = {"dependencies": {"grammar:a": ("grammar:gone",)}}
    assert state("grammar:a", cat=deps) == rules.BLOCKED_DEPENDENCY


def test_retired_needs_an_available_replacement():
    retires = {"retires": {"grammar:a": "grammar:new"}}
    assert state("grammar:a", cat=retires) == rules.RETIRED
    assert state("grammar:a", cat={"retires": {"grammar:a": "grammar:missing"}}) == rules.AVAILABLE
    assert state("grammar:a", cat={"retires": {"grammar:a": None}}) == rules.AVAILABLE
    assert state("grammar:gone", cat={"retires": {"grammar:gone": "grammar:new"}}) == rules.RETIRED


def test_view_projects_rows_and_answers_new_targets():
    calls = []

    def ready():
        calls.append(1)
        return {"grammar:gone"}
    view = rules.build_view(catalogue(), registry(False), [
        {"content_id": "grammar:a"}, {"content_id": "grammar:ext"}, {"content_id": "local:ai:1"},
        {"content_id": "grammar:b", "depends_on": ["grammar:gone"]}], ready_ids=ready)
    assert calls == [1]
    assert view.unavailable_ids == view.orphaned_ids == {"grammar:ext"}
    assert view.counts() == {"available": 3, "orphaned": 1, "blocked_dependency": 0, "retired": 0}
    assert view.state_of("grammar:removed") == rules.ORPHANED and view.available("grammar:new")
    assert view.refusal("grammar:ext") == rules.REFUSALS[rules.ORPHANED] and view.refusal("grammar:a") is None
    assert rules.state_text(rules.RETIRED) == "已被取代" and rules.state_text(rules.AVAILABLE) == ""
    untouched = rules.build_view(catalogue(), registry(), [{"content_id": "grammar:a"}], ready_ids=ready)
    assert calls == [1] and untouched.unavailable_ids == frozenset()


class Effective:
    """Duck-typed content_pack.effective_catalogue() (the phase 3/4 EffectiveCatalogue interface)."""

    def __init__(self, owners=None, missing=None, retired=None):
        self.owners, self.missing, self.retired = owners or {}, missing or {}, retired or {}

    def library_of(self, content_id):
        return self.owners.get(content_id)

    def missing_required_references(self, content_id):
        return self.missing.get(content_id, ())

    def retirement(self, content_id):
        replaced = self.retired.get(content_id)
        return None if content_id not in self.retired else {
            "library_id": EXT_LIBRARY, "replaced_by": replaced, "reason": "",
            "replacement_available": replaced in self.owners}


def test_effective_catalogue_is_the_authority_for_owner_retirement_and_references():
    effective = Effective(owners={"grammar:a": library_rules.CORE_LIBRARY_ID, "grammar:ext": EXT_LIBRARY,
                                  "grammar:new": EXT_LIBRARY, "grammar:b": library_rules.CORE_LIBRARY_ID},
                          missing={"grammar:b": ("grammar:elsewhere",)}, retired={"grammar:a": "grammar:new"})
    view = catalogue(providers={}, effective=effective)  # the mapping is ignored once effective is given
    assert view.provider("grammar:ext") == EXT_LIBRARY and view.provider("grammar:a") is None
    reg = registry()
    assert rules.content_availability(view, reg, {"content_id": "grammar:ext"}) == rules.AVAILABLE
    assert rules.content_availability(view, registry(False), {"content_id": "grammar:ext"}) == rules.ORPHANED
    assert rules.content_availability(view, reg, {"content_id": "grammar:a"}) == rules.RETIRED
    assert rules.content_availability(view, reg, {"content_id": "grammar:b"}) == rules.BLOCKED_DEPENDENCY
    earned = catalogue(effective=effective, ready_ids=frozenset({"grammar:elsewhere"}))
    assert rules.content_availability(earned, reg, {"content_id": "grammar:b"}) == rules.AVAILABLE
    built = rules.build_view(view, reg, [{"content_id": "grammar:b"}], ready_ids=lambda: {"grammar:elsewhere"})
    assert built.state_of("grammar:b") == rules.AVAILABLE


def test_service_catalogue_reads_the_effective_catalogue(monkeypatch):
    from kanamemory import content_library_service as service
    from kanamemory import content_pack
    from kanamemory.learning_content import catalogue_ids
    effective = Effective(owners={"grammar:ext": EXT_LIBRARY})
    monkeypatch.setattr(content_pack, "effective_catalogue", lambda: effective)
    value = service.availability_catalogue()
    assert value.effective is effective and value.provider("grammar:ext") == EXT_LIBRARY
    assert value.ids == catalogue_ids()


def test_library_state_texts_are_one_wording_per_state():
    """Review B-06: disabled, not installed and damaged each have one text."""
    assert rules.state_text(rules.ORPHANED) == rules.LIBRARY_DISABLED_TEXT == "所属内容库已停用"
    assert rules.library_state_text(registry(), EXT_LIBRARY) is None
    assert rules.library_state_text(registry(enabled=False), EXT_LIBRARY) == "所属内容库已停用"
    assert rules.library_state_text(library_rules.default_registry(), EXT_LIBRARY) == "所属内容库未安装"
    damaged = registry()
    damaged["libraries"][1]["state"] = "damaged"
    assert rules.library_state_text(damaged, EXT_LIBRARY) == "所属内容库已损坏"
    assert not rules.library_usable(damaged, EXT_LIBRARY) and rules.library_usable(registry(), EXT_LIBRARY)
    assert rules.library_state_text(None, EXT_LIBRARY) is None
    assert rules.LIBRARY_CHANGE_NOTE == "停用后立即不再派题，重启后从目录与路线中移除"
