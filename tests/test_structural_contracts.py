"""Pure contracts introduced by the bounded module/interface repair."""
from datetime import datetime, timedelta, timezone
from typing import get_args, get_type_hints
from unittest.mock import Mock

import pytest

from kanamemory import adaptive_evidence, domain
from kanamemory.adaptive_evidence import AdaptiveEvent, advance, stages
from kanamemory.ai_connection import ConfiguredProvider, preset_config
from kanamemory.ai_service import AIServiceError, OpenAIProvider, structured_body
from kanamemory.answer_result_ui import distinct_explanation, entered_answer_row
from kanamemory.learning_dispatch_types import LearningCurrent
from kanamemory.learning_store import LearningStore
from kanamemory.material_workflow import digest
from support.fake_ui import View


NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


def test_pure_fold_keeps_low_weight_slips_local_and_retains_history():
    evidence = {}
    for identity in ("ka", "ki"):
        advance(evidence, AdaptiveEvent(identity, NOW, "credit"))
        advance(evidence, AdaptiveEvent(identity, NOW + timedelta(days=1), "credit"))
    assert stages(evidence) == {"ka": 2, "ki": 2}
    unaffected = evidence["ki"]
    advance(evidence, AdaptiveEvent("word", NOW + timedelta(days=2), "skip",
                                    (("ka", "incorrect"),), .25))
    assert evidence["ka"] == (1.75, NOW, NOW + timedelta(days=1))
    assert evidence["ki"] == unaffected and "word" not in evidence
    advance(evidence, AdaptiveEvent("word", NOW + timedelta(days=2), "skip",
                                    (("ka", "correct"),), .25))
    assert stages(evidence) == {"ka": 2, "ki": 2}
    advance(evidence, AdaptiveEvent("ka", NOW + timedelta(days=3), "reset"))
    assert evidence["ka"] == (0, None, None) and evidence["ki"] == unaffected


def test_pure_fold_preserves_same_day_and_next_day_stage_thresholds():
    evidence = {}
    advance(evidence, AdaptiveEvent("ka", NOW, "credit"))
    assert stages(evidence) == {"ka": 0}
    advance(evidence, AdaptiveEvent("ka", NOW + timedelta(minutes=1), "credit"))
    assert stages(evidence) == {"ka": 1}
    advance(evidence, AdaptiveEvent("ka", NOW + timedelta(days=1), "credit"))
    assert stages(evidence) == {"ka": 2}
    advance(evidence, AdaptiveEvent("word", NOW + timedelta(days=2), "skip",
                                    (("ka", "incorrect"),), 1))
    assert stages(evidence) == {"ka": 0}


def test_full_sql_replay_stays_independent_of_incremental_fold(tmp_path, monkeypatch):
    with domain.StudyStore(tmp_path / "learner", "UTC") as store:
        for when in (NOW, NOW + timedelta(days=1)):
            store.record_review("synthetic-ka", "kana_choice", "hiragana", 3,
                                now=when, outcome="correct")
        snapshots = [tuple(row) for row in store._db.execute("SELECT * FROM study_card")]
        reject_fold = Mock(side_effect=AssertionError("incremental fold must not be an oracle dependency"))
        reject_stages = Mock(side_effect=AssertionError("incremental stages must not be an oracle dependency"))
        monkeypatch.setattr(adaptive_evidence, "advance", reject_fold)
        monkeypatch.setattr(adaptive_evidence, "stages", reject_stages)
        assert domain._adaptive_stages_uncached(store, "hiragana", NOW + timedelta(days=2)) == {"synthetic-ka": 2}
        reject_fold.assert_not_called()
        reject_stages.assert_not_called()
        assert [tuple(row) for row in store._db.execute("SELECT * FROM study_card")] == snapshots


@pytest.mark.parametrize("configured", [False, True])
def test_bound_provider_rejects_rebinding_and_repeat_dispatch_without_losing_usage(configured):
    transport = Mock(return_value={"status": "incomplete", "usage": {
        "input_tokens": 12, "output_tokens": 3, "total_tokens": 15}})
    token = "sk-" + "x" * 24
    provider = (ConfiguredProvider(preset_config("OpenAI"), token, transport=transport) if configured
                else OpenAIProvider(token, transport=transport))
    assert provider.material_request_sha256 is None
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    body = structured_body(provider.model, "fixture", {}, schema, "fixture_contract")
    provider.bind_material_request(digest(body))
    with pytest.raises(AIServiceError, match="未完成"):
        provider.structured("fixture", {}, schema, "fixture_contract")
    usage = dict(provider.last_usage)
    assert usage == {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}
    with pytest.raises(AIServiceError, match="已经绑定"):
        provider.bind_material_request(digest(body))
    with pytest.raises(AIServiceError, match="预留"):
        provider.structured("fixture", {}, schema, "fixture_contract")
    assert provider.last_usage == usage and transport.call_count == 1


def test_dispatch_protocol_names_seven_states_and_keeps_none_explicit():
    variants = get_args(LearningCurrent)
    assert len(variants) == 7
    tags = {get_args(get_type_hints(variant)["type"])[0] for variant in variants}
    assert tags == {"instruction", "question", "prerequisite", "content_blocked",
                    "waiting", "diagnostic", "disabled"}
    returned = set(get_args(get_type_hints(LearningStore.current)["return"]))
    assert returned == set(variants) | {type(None)}


def test_shared_explanation_removes_only_exact_answer_lines():
    assert distinct_explanation("ともだち\n朋友。\n友達：ともだち。\n ともだち ", "ともだち") == "朋友。\n友達：ともだち。"
    assert distinct_explanation(None, "ともだち") == ""
    assert distinct_explanation("1\n第1组。\n11", "1") == "第1组。\n11"


def test_shared_entered_answer_separates_ui_label_from_study_text():
    from types import SimpleNamespace
    render_label = Mock(side_effect=lambda text, **kw: View(text=text, **kw))
    ui = SimpleNamespace(font_scale=lambda: 1.3, MUTED="muted",
        row=lambda *nodes: View(*nodes), label=render_label)
    row = entered_answer_row(ui, "とまだち", text_role="study")
    label, answer = row.children
    label_options, answer_options = [call.kwargs for call in render_label.call_args_list]
    assert label.text == "我的回答：" and label_options["width"] > 80
    assert answer.text == "とまだち" and answer_options["text_role"] == "study"
    assert answer_options["flex"] == 1
