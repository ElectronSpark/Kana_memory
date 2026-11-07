"""Shared pitch sequence fixtures and assertions; no collected scenarios."""
from copy import deepcopy
from kanamemory import pitch_practice as pitch
import pytest
from kanamemory import pitch_sequence as sequence


def synthetic_spec(*, mode="pitch_segmentation", learner=True):
    def token(identity, role, start, end, source_start, source_end):
        return {"id": identity, "role": role, "start": start, "end": end,
                "source_spans": [{"start": source_start, "end": source_end}]}
    tokens = [token("today", "word", 0, 3, 0, 2), token("comma", "punctuation", 3, 4, 2, 3),
              token("cat", "word", 4, 6, 3, 4), token("subject", "particle", 6, 7, 4, 5),
              token("exist", "word", 7, 9, 5, 7), token("stop", "punctuation", 9, 10, 7, 8)]
    alternate = [*deepcopy(tokens[:2]), token("cat-subject", "word", 4, 7, 3, 5), *deepcopy(tokens[4:])]
    value = {"version": 2, "stimulus_id": "synthetic:sequence:1", "text": "今日、猫がいる。",
        "characters": list("きょう、ねこがいる。"),
        "mora_spans": [{"start": a, "end": b} for a, b in [(0, 2), (2, 3), (4, 5), (5, 6), (6, 7), (7, 8), (8, 9)]],
        "tokens": tokens, "accent_phrases": [{"id": "phrase:1", "start": 0, "end": 3},
                                              {"id": "phrase:2", "start": 4, "end": 9}],
        "wrap_units": [{"start": 0, "end": 10}],
        "contexts": [{"id": "context:synthetic", "prompt_zh": "测试用虚构对照曲线，不是学习材料。", "source_refs": ["source:synthetic"]}],
        "segmentations": [{"id": "words", "tokens": tokens, "explanation_zh": "分别标出词与助词。"},
                          {"id": "phrase-test", "tokens": alternate, "explanation_zh": "第二套测试用许可分组。"}],
        "reviewed_patterns": [{"id": "pattern:a", "context_id": "context:synthetic",
            "levels": ["L", "H", "H", "L", "L", "H", "L"], "segmentation_ids": ["words"], "source_refs": ["source:synthetic"]},
            {"id": "pattern:b", "context_id": "context:synthetic", "levels": ["H", "L", "H", "L", "L", "H", "L"],
             "segmentation_ids": ["phrase-test"], "source_refs": ["source:synthetic"]}],
        "selected_pattern_id": "pattern:a", "accepted_pattern_ids": ["pattern:a", "pattern:b"],
        "representation": "mora_plateau_hl", "lexical_accents": [],
        "learner_morae": learner, "task_mode": mode,
        "review": {"review_id": "synthetic:review", "content_sha256": "", "source_refs": ["source:synthetic"], "status": "approved"}}
    value["review"]["content_sha256"] = sequence.semantic_fingerprint(value)
    return value


@pytest.fixture
def registry(monkeypatch):
    value = {"format": "kanamemory-pitch-sequences", "version": 1,
             "sources": [{"id": "source:synthetic", "title": "Original synthetic test fixture", "url": "https://example.test/synthetic"}], "records": []}
    monkeypatch.setattr(sequence, "load_namespace", lambda name, default=None: deepcopy(value) if name == "pitch_sequences" else default)
    return value


def register(registry, spec):
    spec["review"]["content_sha256"] = sequence.semantic_fingerprint(spec)
    registry["records"].append(deepcopy(spec))
    return spec


def lesson(registry, *, spec=None, kind="pronunciation_application"):
    spec = register(registry, spec or synthetic_spec())
    raw = {"input_mode": "pitch_fill", "prompt": "".join(spec["characters"]),
           "task": "按给定测试曲线标高低并分开词与助词。", "feedback": "按照本题明确给定的语境检查。",
           "pitch_practice": spec, "choices": [], "safe": False,
           "text_roles": {"prompt": "study", "answer": "ui"}}
    raw["answer"] = pitch.canonical_answer(raw)
    return {"id": "pronunciation:synthetic_sequence", "revision": "synthetic-v1", "family": "grammar",
            "domain": "pronunciation", "abilities": ["understanding", "application"], "title": "整句音高测试",
            "meaning": "仅用于合同测试", "depends_on": [],
            "instruction": {"rule": "对照完整句谱练习。", "example": "今日、猫がいる。"},
            "question_templates": {kind: [raw]}}


CORRECT = ["L", "merge", "H", "H", "L", "split", "L", "split", "H", "L"]
