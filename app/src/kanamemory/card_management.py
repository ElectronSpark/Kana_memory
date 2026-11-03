"""Browse learner-owned kana memory items without changing packaged content.

One item is a kana content ID. Words provide contextual spelling evidence, not
independent memory items. Directions and scripts retain their own evidence;
browsing never creates a card, records a review, or changes a due date.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json

from .domain import PRACTICE_KANA_CATEGORIES, WORD_SKILLS, _utc
from .presentation import CATEGORY_LABELS
from .romanization import kana_learning_romaji
from .word_search import normalize_search, romanization_aliases


STATE_LABELS = {
    "new": "未学", "learning": "学习中", "review": "复习", "relearning": "重学",
    "suspended": "已暂停", "buried": "今日暂缓",
}
SKILL_LABELS = {
    "kana_choice": "看假名选读音", "kana_romaji": "看假名拼罗马音",
    "romaji_kana": "看罗马音选假名", "kana_handwriting": "听音手写假名",
    "romaji_kana_full": "看罗马音拼假名（完整表）", "romaji_handwriting": "看罗马音手写假名",
    "word_kana": "听词选假名", "word_meaning_kana": "看词义和罗马音选假名",
    "word_romaji": "看单词假名选罗马音",
}
SCRIPT_LABELS = {"hiragana": "平假名", "katakana": "片假名"}
ABILITY_LABELS = {"reading": "读音", "spelling": "选字与拼写", "handwriting": "手写"}
_FSRS_STATES = {1: "learning", 2: "review", 3: "relearning"}


def _card_state(card):
    if not card or card["review_count"] == 0:
        return "new"
    return _FSRS_STATES.get(json.loads(card["scheduler_state_json"]).get("state"), "learning")


def _due_time(card):
    return datetime.fromisoformat(card["due_at_utc"])


class CardManager:
    """A lightweight catalog projection combined with current learning data.

    Catalog text may be cached because the bundled database is read-only. Every
    operation reads fresh learner data so a rating, restore, or management
    change is immediately visible in an already-open manager.
    """

    def __init__(self, repository, store):
        self.repository = repository
        self.store = store
        # Page refreshes may create a new manager; share only the immutable
        # catalog projection for this read-only repository's lifetime.
        cached = getattr(repository, "_card_management_catalog", None)
        self._catalog_cache = (tuple(item for item in cached if item["kind"] == "kana")
                               if cached is not None else None)

    def _catalog(self):
        if self._catalog_cache is not None:
            return self._catalog_cache
        result = []
        for row in self.repository.kana_headers():
            data = dict(row)
            contextual_only = data["category"] in {"small", "long_vowel_mark"}
            if data["category"] not in PRACTICE_KANA_CATEGORIES and not contextual_only:
                continue
            title = " / ".join(dict.fromkeys(value for value in
                (data["hiragana"], data["katakana"]) if value))
            subtitle = " · ".join(value for value in
                (kana_learning_romaji(data), CATEGORY_LABELS.get(data["category"], "假名")) if value)
            search = [data["id"], title, subtitle]
            for reading in (data["hiragana"], data["katakana"]):
                if reading:
                    search.extend(romanization_aliases(reading))
            result.append({"content_id": data["id"], "kind": "kana", "title": title,
                "subtitle": subtitle, "category": data["category"],
                "practice_approved": True, "_contextual_only": contextual_only,
                "_search": normalize_search(" ".join(search))})
        self._catalog_cache = tuple(result)
        self.repository._card_management_catalog = self._catalog_cache
        return self._catalog_cache

    def _learning_data(self, now=None, *, spelling_content_id=None, include_spelling=True):
        cards = defaultdict(list)
        kana_ids = {item["content_id"] for item in self._catalog()}
        for row in self.store.scheduling_cards():
            if row["content_id"] not in kana_ids or row["skill"] in WORD_SKILLS:
                continue
            cards[row["content_id"]].append(dict(row))
        owners = {member: card["card_id"] for items in cards.values() for card in items
                  for member in card.get("history_card_ids", [card["card_id"]])}
        evidence, modes = {}, defaultdict(dict)
        for row in self.store.queries.kana_mode_evidence():
            owner = owners.get(row["card_id"])
            if owner is None:
                continue
            for stats in (evidence.setdefault(owner, {}), modes[owner].setdefault(row["actual_skill"], {})):
                for name in ("event_count", "success_count", "failure_count", "assisted_count"):
                    stats[name] = stats.get(name, 0) + row[name]
                stats["last_review_at_utc"] = max(stats.get("last_review_at_utc", ""), row["last_review_at_utc"])
        for owner, actual_modes in modes.items():
            weak = [(kind, stats) for kind, stats in actual_modes.items()
                    if stats["failure_count"] or stats["assisted_count"]]
            if weak:
                evidence[owner]["weakest_skill"] = max(weak, key=lambda pair: (
                    (pair[1]["failure_count"] + pair[1]["assisted_count"]) / pair[1]["event_count"],
                    pair[1]["failure_count"], pair[1]["last_review_at_utc"]))[0]
        controls = self.store.get_setting("study_item_controls_v1", {})
        spelling = defaultdict(list)
        if include_spelling:
            for summary in self.store.word_spelling_summary(spelling_content_id, now=now):
                spelling[summary["content_id"]].append(summary)
        return cards, evidence, controls, spelling

    def _item(self, catalog, cards, evidence, controls, now, spelling=()):
        item = {key: value for key, value in catalog.items() if not key.startswith("_")}
        control = controls.get(catalog["content_id"], {})
        active = [card for card in cards if not card["suspended"] and card["review_count"] > 0]
        studied = [card for card in cards if card["review_count"] > 0]
        first = min(active or studied, key=_due_time, default=None)
        underlying = _card_state(first)
        suspended = bool(control.get("suspended")) or bool(cards and all(card["suspended"] for card in cards))
        buried_until = control.get("buried_until_utc")
        buried = bool(buried_until and datetime.fromisoformat(buried_until) > now and not suspended)
        state = "suspended" if suspended else "buried" if buried else underlying
        due = bool(active and _due_time(min(active, key=_due_time)) <= now and not suspended and not buried)
        measured = []
        for card in active:
            stats = evidence.get(card["card_id"], {})
            if stats.get("failure_count", 0) or stats.get("assisted_count", 0):
                # Only measured difficulty is called weak. An unseen question
                # direction is an information gap, not evidence of forgetting.
                errors = stats.get("failure_count", 0)
                hints = stats.get("assisted_count", 0)
                measured.append(((errors + hints) / stats["event_count"], errors,
                                 stats.get("last_review_at_utc") or "",
                                 {**card, "skill": stats.get("weakest_skill", card["skill"])}))
        weakest = max(measured, key=lambda value: value[:3], default=None)
        weak_card = weakest[3] if weakest else None
        item.update(state=state, state_label=STATE_LABELS[state], underlying_state=underlying,
            due=due, due_at_utc=first["due_at_utc"] if first else None,
            review_count=sum(card.get("history_review_count", card["review_count"]) for card in cards), card_count=len(studied),
            suspended=suspended, buried_until_utc=buried_until if buried else None,
            weakest_skill=weak_card["skill"] if weak_card else None,
            weakest_script=weak_card["script"] if weak_card else None,
            weakest_label=(SKILL_LABELS.get(weak_card["skill"], weak_card["skill"]) + " · " +
                SCRIPT_LABELS[weak_card["script"]]) if weak_card else None)
        # Contextual spelling is displayed separately from independent FSRS
        # ratings, card counts and due dates; reading a word never invents a
        # separate test of every kana direction or the other script.
        item["word_spelling_summary"] = list(spelling)
        item["word_spelling_weak_symbols"] = [entry["symbol"] for entry in spelling if entry["weak"]]
        item["word_spelling_only"] = bool(spelling and not studied)
        return item

    def list_items(self, query="", status="all", kind="all", limit=30, offset=0,
                   now=None, include_research=False):
        """Return one row per content ID, with counts before status filtering.

        Supported status filters are all/new/learning/review/relearning/
        suspended/buried/due. Query affects counts; pagination and status do
        not. Legacy kind='word' callers receive an empty result. Research
        scope is accepted for compatibility and does not add word items.
        """
        if status not in {"all", "due", *STATE_LABELS}:
            raise ValueError("Unknown card status")
        if kind not in {"all", "kana", "word"}:
            raise ValueError("Unknown content kind")
        if type(limit) is not int or limit < 1 or type(offset) is not int or offset < 0:
            raise ValueError("Invalid page size or offset")
        if not isinstance(query, str) or type(include_research) is not bool:
            raise ValueError("Invalid search query or research scope")
        now = _utc(now)
        terms = normalize_search(query).split()
        counts = {key: 0 for key in ("all", *STATE_LABELS, "due")}
        if kind == "word":
            return {"items": [], "total": 0, "counts": counts, "has_next": False}
        cards, evidence, controls, spelling = self._learning_data(now)
        items = []
        for catalog in self._catalog():
            if ((catalog.get("_contextual_only") and not spelling[catalog["content_id"]])
                    or (kind != "all" and catalog["kind"] != kind)
                    or (not include_research and not catalog["practice_approved"])
                    or any(term not in catalog["_search"] for term in terms)):
                continue
            item = self._item(catalog, cards[catalog["content_id"]], evidence, controls, now,
                             spelling[catalog["content_id"]])
            counts["all"] += 1
            counts[item["state"]] += 1
            counts["due"] += int(item["due"])
            if status == "all" or (status == "due" and item["due"]) or item["state"] == status:
                items.append(item)
        # Keep the due queue visible first while preserving catalog order for
        # ties and new material. This never determines the actual study order.
        items.sort(key=lambda item: (not item["due"], item["due_at_utc"] or "9999"))
        return {"items": items[offset:offset + limit], "total": len(items),
                "counts": counts, "has_next": offset + limit < len(items)}

    def detail(self, content_id, now=None, include_research=False):
        """Return item metadata, tested directions and recent review evidence."""
        now = _utc(now)
        catalog = next((item for item in self._catalog() if item["content_id"] == content_id
                        and (include_research or item["practice_approved"])), None)
        if catalog is None:
            return None
        all_cards, evidence, controls, spelling = self._learning_data(now,
            spelling_content_id=content_id, include_spelling=catalog["kind"] == "kana")
        cards = all_cards[content_id]
        result = self._item(catalog, cards, evidence, controls, now, spelling[content_id])
        result["cards"] = []
        for card in sorted(cards, key=lambda value: (value["skill"], value["script"])):
            stats = evidence.get(card["card_id"], {})
            state = _card_state(card)
            result["cards"].append({"card_id": card["card_id"], "skill": card["skill"],
                "skill_label": ABILITY_LABELS.get(card.get("ability"), SKILL_LABELS.get(card["skill"], card["skill"])),
                "script": card["script"], "script_label": SCRIPT_LABELS[card["script"]],
                "state": state, "state_label": STATE_LABELS[state],
                "due": bool(card["review_count"] and _due_time(card) <= now and not card["suspended"]
                    and not result["suspended"] and result["state"] != "buried"),
                "due_at_utc": card["due_at_utc"], "review_count": card.get("history_review_count", card["review_count"]),
                "success_count": stats.get("success_count", 0),
                "failure_count": stats.get("failure_count", 0),
                "assisted_count": stats.get("assisted_count", 0),
                "last_review_at_utc": stats.get("last_review_at_utc"),
                "suspended": bool(card["suspended"]) or result["suspended"],
                "underlying_suspended": bool(card["suspended"])})
        # Older databases can still contain word-direction rows. Keep those
        # intact for compatibility while excluding them from kana management.
        result["history"] = [dict(row) for row in self.store.queries.recent_kana_history(content_id)]
        result["word_spelling_evidence"] = self.store.word_spelling_events(
            content_id=content_id, now=now, limit=8, newest_first=True)
        result["word_interval_history"] = self.store.word_interval_history(content_id, now=now)
        result["skipped_attempts"] = self.store.queries.kana_skipped_attempts(content_id)[0]
        return result

    def set_status(self, content_id, action, now=None):
        """Apply suspend/resume/bury/unbury to all directions of an item."""
        if action not in {"suspend", "resume", "bury", "unbury"}:
            raise ValueError("Unknown card management action")
        if not any(item["content_id"] == content_id for item in self._catalog()):
            raise ValueError("Unknown study content")
        return self.store.set_content_control(content_id, action, now=now)
