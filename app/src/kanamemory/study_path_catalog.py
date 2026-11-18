"""A navigation syllabus over existing identities, never a new memory schema.

Stages describe this app's teaching order, not certified JLPT/CEFR coverage.
Authored functional topics form sections. Dependency depth separates a topic's
successive lessons; section and unit graphs are then ordered prerequisite first.
The catalogue contains references as references, not fabricated quiz cards.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from .kana_question_policy import direction_allowed

from .content_pack import load_namespace

# These are course-specific navigation data, supplied by the content pack.
_PATH = load_namespace("study_path_catalog", {})
PATH_VERSION = _PATH.get("PATH_VERSION", "study-path-v1")
STAGES = _PATH.get("STAGES", tuple(("stage:" + name, title) for name, title in (
    ("sounds", "文字与声音"), ("daily", "初步表达"), ("conversation", "日常交流"),
    ("connected", "连贯表达"), ("reading", "阅读"), ("formal", "综合运用"),
    ("reference", "扩展参考"), ("personal", "自选内容"))))
WORD_TOPICS = _PATH.get("WORD_TOPICS", ())
GRAMMAR_TOPICS = _PATH.get("GRAMMAR_TOPICS", {})
COHERENCE_GROUPS = _PATH.get("COHERENCE_GROUPS", ())
EXAM_COHERENCE = _PATH.get("EXAM_COHERENCE", ())
SMALL_UNIT_PLANS = _PATH.get("SMALL_UNIT_PLANS", {})
COHERENCE_RULES = _PATH.get("COHERENCE_RULES", {})
GRAMMAR_ROUTE_CONFIG = _PATH.get("GRAMMAR_ROUTE_CONFIG", {})
PRONUNCIATION_ROUTE_CONFIG = _PATH.get("PRONUNCIATION_ROUTE_CONFIG", {})
# Stage of the loanword kana spellings and the unit order of the kana stage
# (design 11 section 6, R1). Absent or invalid values keep the original order.
KANA_ROUTE_CONFIG = _PATH.get("KANA_ROUTE_CONFIG", {})
_STAGE_ZERO_UNIT_ORDER = ("sound:mora", "alphabet:hiragana", "alphabet:katakana", "voiced:hiragana",
                          "voiced:katakana", "sound:small_kana", "sound:shared_special",
                          "yoon:hiragana", "yoon:katakana", "notation:hiragana", "notation:katakana")


_REFERENCE_STAGE = 6
# Neutral titles of a flat MAIN_ROUTE_ORDER (main_route_order.flat_layout): a section of words of
# several topics, and a unit without a grammar lesson ("{}": its number among such units).
ORDER_WORDS_TITLE = "词汇"
ORDER_OPENING_TITLE = "开头 · 第 {} 组"


def _loan_stage(script):
    """Stage of one script's loanword spellings: 0, 1 or "reference" (the optional glyph stage).

    loan_stage is one value for both scripts or {"hiragana": ..., "katakana": ...}.
    """
    stage = KANA_ROUTE_CONFIG.get("loan_stage", 1) if isinstance(KANA_ROUTE_CONFIG, dict) else 1
    if isinstance(stage, dict):
        stage = stage.get(script, 1)
    if stage == "reference":
        return _REFERENCE_STAGE
    return stage if type(stage) is int and 0 <= stage <= 1 else 1


def _stage_zero_unit_order():
    order = KANA_ROUTE_CONFIG.get("stage_zero_unit_order") if isinstance(KANA_ROUTE_CONFIG, dict) else None
    if (isinstance(order, (list, tuple)) and order
            and all(isinstance(key, str) and key for key in order) and len(set(order)) == len(order)):
        return tuple(order)
    return _STAGE_ZERO_UNIT_ORDER


def _matched_group(group, key):
    for prefixes, keys, title in COHERENCE_RULES.get(group, ()):
        if key in keys or any(key.startswith(prefix) for prefix in prefixes):
            return title
    return None


def _coherence(item):
    if item.get("domain") == "pronunciation":
        key = item["id"].split(":", 1)[-1]
        matched = _matched_group("pronunciation", key)
        if matched:
            return matched
        return item.get("category", "读音规则")
    key = item["id"].removeprefix("grammar:")
    if key.startswith("exam:"):
        suffix = key.split(":", 1)[1]
        return next((title for title, keys in EXAM_COHERENCE if suffix in keys.split()), item.get("category"))
    if key.startswith("usage:"):
        suffix = key.split(":", 1)[1]
        matched = _matched_group("usage", suffix)
        if matched:
            return matched
        return item.get("category", "语法用法")
    family = key.split(":")[0]
    return next((title for title, families in COHERENCE_GROUPS if family in families.split()), item.get("category"))


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


EXPRESSION_PREFIX = "expression:"


def _expression_stage():
    """Stage of the common expressions: GRAMMAR_ROUTE_CONFIG["expression_stage"] (1..5), else 1."""
    stage = GRAMMAR_ROUTE_CONFIG.get("expression_stage", 1)
    return stage if type(stage) is int and 1 <= stage <= 5 else 1


def _grammar_route(item):
    key = item["id"].removeprefix("grammar:")
    family = key.split(":")[0]
    category = item.get("category", "扩展语法")
    if key.startswith("exam:"):
        stage = 4 if item.get("priority", 2) == 1 else 5
        # Each exam construction has a distinct contrast and prerequisite set.
        topic = item["title"].split("：")[0]
    elif key.startswith("usage:"):
        stage = 2 if item.get("priority", 2) == 1 else 3
        topic = item["title"].split("：")[0]
        if category == GRAMMAR_ROUTE_CONFIG.get("written_category"):
            stage = 4
    elif key.startswith(EXPRESSION_PREFIX):
        # A common expression (grammar:expression:<slug>, domain "expression") is said from the first
        # expression stage on; a level moves it as it moves other grammar, prerequisites still lift it.
        # Its section is its function (category: 问候, 道谢 ...).
        stage = GRAMMAR_ROUTE_CONFIG.get("levels", {}).get(item.get("level"), _expression_stage())
        topic = category
    else:
        stage = GRAMMAR_ROUTE_CONFIG.get("levels", {}).get(item.get("level"), 3)
        # Skeleton grammar placed in the first expression stage regardless of
        # level (design 11 section 6, R2); prerequisites still lift the stage.
        if key in GRAMMAR_ROUTE_CONFIG.get("stage_one_keys", ()):
            stage = 1
        if category == GRAMMAR_ROUTE_CONFIG.get("written_category"):
            stage = 3 if item.get("level") == "基础" else 4
        if key in GRAMMAR_ROUTE_CONFIG.get("formal_keys", ()):
            stage = 5
        topic = GRAMMAR_TOPICS.get(family, category)
    return stage, "grammar:" + category, category, topic


def _learning_route(item, topics=None):
    if item.get("domain") == "pronunciation":
        key = item["id"].split(":", 1)[-1]
        category = item.get("category", "读音规则")
        stage = 0
        if key in PRONUNCIATION_ROUTE_CONFIG.get("stage_one_keys", ()):
            stage = 1
        elif (PRONUNCIATION_ROUTE_CONFIG.get("stage_two_prefix") and key.startswith(PRONUNCIATION_ROUTE_CONFIG["stage_two_prefix"])) or key in PRONUNCIATION_ROUTE_CONFIG.get("stage_two_keys", ()):
            stage = 2
        elif item.get("priority") == 2:
            stage = 3
        if key == PRONUNCIATION_ROUTE_CONFIG.get("mora_key"):
            return (stage, *PRONUNCIATION_ROUTE_CONFIG["mora_route"])
        shared_route = PRONUNCIATION_ROUTE_CONFIG.get("shared_rule_routes", {}).get(key)
        if shared_route:
            return (stage, *shared_route)
        return stage, "sound:" + category, category, item["title"].split("：")[0]
    if item["family"] == "grammar":
        return _grammar_route(item)
    route, _ = _word_topic(item, _word_topic_index() if topics is None else topics)
    if route is not None:
        return route
    return 3, "word:additional", "其他常用词义", item.get("semantic_group", "词义与读法")


def _word_topic_index():
    """Lesson key -> (stage, unit key, unit title, topic) of the first WORD_TOPICS row listing it.

    A row lists lesson keys: a form, or "form@reading" for a lesson bound to
    one reading of a form that another lesson also uses (vocabulary_lessons).
    """
    index = {}
    for stage, unit, title, topic, words in WORD_TOPICS:
        for key in words.split():
            index.setdefault(key, (stage, "word:" + unit, title, topic))
    return index


def _word_topic(item, topics):
    """(route, key) placing a word lesson, or (None, None); "form@reading" wins over the form."""
    for key in (f"{item['title']}@{item.get('reading', '')}", item["title"]):
        if key in topics:
            return topics[key], key
    return None, None


def _kana_route(row, script, eligible):
    text = row.get(script, "")
    category, tier = row.get("category"), row.get("tier")
    alphabet = "平假名" if script == "hiragana" else "片假名"
    if tier in {"historical", "specialist", "variant", "rare_modern_spelling"}:
        topic = {"historical_kana": "ゐ与ゑ", "rare_legacy_katakana": "旧片假名扩展",
                 "foreign_variant": "合拗音旧拼写", "small": "特殊小假名", "yoon": "ぢゃ系列旧拼写"}.get(category, "特殊字形")
        return 6, "reference:" + script, alphabet + "与旧字形", topic
    if not eligible:
        topic = {"small": "小假名的字形", "long_vowel_mark": "长音符号",
                 "voicing_mark": "浊点与半浊点", "iteration_mark": "重复符号"}.get(category, "上下文中的符号")
        return 0, "notation:" + script, alphabet + "的拼写符号", topic
    if category in {"foreign_combination", "foreign_voiced"}:
        stage = _loan_stage(script)
        if stage == _REFERENCE_STAGE:
            # A script's loanword spellings that real text hardly uses are
            # read beside the historical glyphs, not studied before words.
            return stage, "reference:" + script, alphabet + "与旧字形", "外来语拼写"
        head = row.get("hiragana") or text
        topic = {"て": "ティ与テュ", "で": "ディ与デュ", "と": "トゥ", "ど": "ドゥ",
                 "ふ": "ファ行与フュ", "ゔ": "ヴ系列", "う": "ウィ・ウェ・ウォ",
                 "つ": "ツァ行", "く": "クァ行", "ぐ": "グァ", "し": "シェ",
                 "じ": "ジェ", "ち": "チェ", "い": "イェ"}.get(head[0], "外来语拼写")
        return stage, "loan:" + script, alphabet + "外来语拼写", topic
    hira = row.get("hiragana") or text
    if category == "yoon":
        return 0, "yoon:" + script, alphabet + "拗音", hira[0] + "的小ゃ・ゅ・ょ组合"
    if category == "handakuten":
        return 0, "voiced:" + script, alphabet + "浊音与半浊音", "ぱ行"
    rows = ("あいうえお", "かきくけこ", "さしすせそ", "たちつてと", "なにぬねの",
            "はひふへほ", "まみむめも", "やゆよ", "らりるれろ", "わをん",
            "がぎぐげご", "ざじずぜぞ", "だぢづでど", "ばびぶべぼ")
    row_name = next((chars[0] + "行" for chars in rows if hira in chars), "基本字形")
    if hira in "わをん":
        row_name = "わ・を・ん"
    unit = "voiced" if category == "dakuten" else "alphabet"
    unit_title = "浊音与半浊音" if category == "dakuten" else "五十音"
    return 0, unit + ":" + script, alphabet + unit_title, row_name


def _topological_scan_remaining(remaining, dependencies, order):
    result = []
    while remaining:
        ready = [n for n in remaining if not (set(dependencies.get(n, ())) & remaining)]
        if not ready:
            raise ValueError("学习路径依赖成环")
        chosen = min(ready, key=lambda n: order[n])
        result.append(chosen)
        remaining.remove(chosen)
    return result


def _topological(nodes, dependencies, order):
    """Use a heap only when all priorities are present and uniquely ordered.

    Existing set-order tie behavior and unusual priority/error cases retain
    the original implementation. Dependencies outside this node subset are
    still ignored, and each duplicate edge is counted only once.
    """
    from heapq import heapify, heappop, heappush
    nodes = set(nodes)
    if any(node not in order for node in nodes):
        return _topological_scan_remaining(nodes, dependencies, order)
    priorities = [order[node] for node in nodes]
    # Only homogeneous integer/tuple shapes are known total orders here.
    # Partial orders (NaN/custom objects) and mixed types keep legacy behavior.
    def shape(priority):
        if type(priority) is int:
            return "int"
        if type(priority) is tuple:
            children = tuple(shape(part) for part in priority)
            if all(child is not None for child in children):
                return ("tuple", children)
        return None
    shapes = [shape(priority) for priority in priorities]
    if any(value is None or value != shapes[0] for value in shapes):
        return _topological_scan_remaining(nodes, dependencies, order)
    try:
        unique = len(set(priorities)) == len(nodes)
    except TypeError:
        unique = False
    if not unique:
        return _topological_scan_remaining(nodes, dependencies, order)
    indegree, followers = {}, {node: [] for node in nodes}
    for node in nodes:
        parents = set(dependencies.get(node, ())) & nodes
        indegree[node] = len(parents)
        for parent in parents:
            followers[parent].append(node)
    ready = [(order[node], node) for node in nodes if indegree[node] == 0]
    heapify(ready)
    result = []
    while ready:
        _, node = heappop(ready)
        result.append(node)
        for child in followers[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                heappush(ready, (order[child], child))
    if len(result) != len(nodes):
        raise ValueError("学习路径依赖成环")
    return result


def _merge_cyclic_units(units, section_dependencies):
    """Merge mutually dependent topics into one coherent comparison unit."""
    while True:
        owner = {s["id"]: uid for uid, unit in units.items() for s in unit["sections"]}
        edges = {uid: {owner[d] for s in unit["sections"] for d in section_dependencies[s["id"]]
                       if d in owner and owner[d] != uid} for uid, unit in units.items()}
        merged = False
        for start in list(units):
            reachable = set()
            todo = list(edges[start])
            while todo:
                node = todo.pop()
                if node in reachable:
                    continue
                reachable.add(node)
                todo.extend(edges[node] - reachable)
            if start not in reachable:
                continue
            component = {start}
            for node in reachable:
                seen, todo = set(), [node]
                while todo:
                    current = todo.pop()
                    if current in seen:
                        continue
                    seen.add(current)
                    todo.extend(edges[current] - seen)
                if start in seen:
                    component.add(node)
            ordered = sorted(component, key=lambda uid: units[uid]["_order"])
            first = units[ordered[0]]
            unit = {"id": "unit:combined:" + _digest("|".join(sorted(component))),
                    "title": "・".join(dict.fromkeys(units[uid]["title"] for uid in ordered)),
                    "sections": [s for uid in ordered for s in units[uid]["sections"]],
                    "_order": first["_order"]}
            for uid in ordered:
                del units[uid]
            units[unit["id"]] = unit
            merged = True
            break
        if not merged:
            return units, edges


def flatten_sections(path):
    return [section for stage in path["stages"] for unit in stage["units"] for section in unit["sections"]]


def _unit_priority(stage, key, position):
    if stage == 0:
        # Letter recognition precedes explanations that use longer examples.
        prefixes = _stage_zero_unit_order()
        return (next((i for i, prefix in enumerate(prefixes) if key == prefix), len(prefixes)), position)
    return (0, position)


def _combine_micro_lessons(path, scope_key=""):
    """Combine related neighbouring rules after ordering, without hiding edges.

    Each neighbour already belongs to one functional/topic unit. Consolidating
    consecutive graph nodes cannot introduce a prerequisite cycle. Four rules
    is an app pacing choice, not a scientifically established daily quota.
    scope_key (empty for the core route) scopes derived unit IDs to one route.
    """
    replacement = {}
    for stage in path["stages"]:
        for unit in stage["units"]:
            combined = []
            for section in unit["sections"]:
                kinds = {entry["kind"] for entry in section["entries"]}
                family = {entry.get("family") for entry in section["entries"]}
                coherence = {entry.get("teaching_group") for entry in section["entries"]}
                previous = combined[-1] if combined else None
                can_merge = (previous and kinds == {"learning"}
                    and {entry["kind"] for entry in previous["entries"]} == kinds
                    and {entry.get("family") for entry in previous["entries"]} == family
                    and family == {"grammar"}
                    and {entry.get("teaching_group") for entry in previous["entries"]} == coherence
                    and len(previous["entries"]) + len(section["entries"]) <= 4)
                if can_merge:
                    previous["entries"].extend(section["entries"])
                    previous["depends_on"].extend(section["depends_on"])
                    previous["component_section_ids"].append(section["id"])
                    previous["topics"].append(section["title"])
                    previous["topics"] = list(dict.fromkeys(previous["topics"]))
                    previous["title"] = (next(iter(coherence)) if len(previous["topics"]) > 2
                                         else " · ".join(previous["topics"]))
                    replacement[section["id"]] = previous["id"]
                else:
                    section["component_section_ids"] = [section["id"]]
                    section["topics"] = [section["title"]]
                    combined.append(section)
                    replacement[section["id"]] = section["id"]
            unit["sections"] = combined
    sections = flatten_sections(path)
    positions = {s["id"]: i for i, s in enumerate(sections)}
    for section in sections:
        section["depends_on"] = sorted({replacement[d] for d in section["depends_on"]
            if replacement[d] != section["id"]}, key=positions.__getitem__)
    # Long functional units are split at authored topic boundaries; all ordered
    # sections keep their IDs and their original content identities.
    for stage in path["stages"]:
        units = []
        for unit in stage["units"]:
            if len(unit["sections"]) <= 8:
                units.append(unit)
                continue
            groups, pending = [], []
            for section in unit["sections"]:
                if len(pending) >= 6 and not (set(pending[-1]["topics"]) & set(section["topics"])):
                    groups.append(pending)
                    pending = []
                pending.append(section)
            if pending:
                if len(pending) == 1 and groups:
                    groups[-1].extend(pending)
                else:
                    groups.append(pending)
            for group in groups:
                first_topic, last_topic = group[0]["topics"][0], group[-1]["topics"][-1]
                if "五十音" in unit["title"] or "拗音" in unit["title"] or "外来语拼写" in unit["title"]:
                    first_topic = first_topic.split(" · ", 1)[-1]
                    last_topic = last_topic.split(" · ", 1)[-1]
                    title = unit["title"] + "（" + first_topic + "～" + last_topic + "）"
                else:
                    category_title = unit["title"]
                    if "・" in category_title:
                        category_title = "基本句法与活用" if "词类与活用" in category_title else "判断与限定表达"
                    title = category_title + "：" + first_topic + "～" + last_topic
                new = {"id": unit["id"] + ":" + group[0]["id"].removeprefix("section:"),
                       "title": title, "sections": group}
                for section in group:
                    section.update(unit_id=new["id"], unit_title=new["title"])
                units.append(new)
        stage["units"] = units
    return _combine_small_units(path, scope_key)


def _combine_small_units(path, scope_key=""):
    """Place small lessons in explicitly related, multi-section teaching units."""
    plans = SMALL_UNIT_PLANS
    for stage in path["stages"]:
        original_sections = [s for u in stage["units"] for s in u["sections"]]
        section_order = {s["id"]: p for p, s in enumerate(original_sections)}
        for title, names in plans.get(stage["id"], ()):
            selected = [u for u in stage["units"] if u["title"] in names]
            # Small supplied test catalogues may not contain all these topics.
            if len(selected) < 2:
                continue
            ids = {u["id"] for u in selected}
            first = min(stage["units"].index(u) for u in selected)
            new = {"id": "unit:topic:" + _digest((scope_key + "|" if scope_key else "") + stage["id"] + "|" + title),
                   "title": title, "sections": [s for u in selected for s in u["sections"]]}
            stage["units"] = [u for u in stage["units"] if u["id"] not in ids]
            stage["units"].insert(first, new)
        units = {u["id"]: {**u, "_order": min(section_order[s["id"]] for s in u["sections"])} for u in stage["units"]}
        dependencies = {s["id"]: s["depends_on"] for s in original_sections}
        units, edges = _merge_cyclic_units(units, dependencies)
        order = _topological(units, edges, {uid: u["_order"] for uid, u in units.items()})
        stage["units"] = []
        for uid in order:
            unit = units[uid]
            by_id = {s["id"]: s for s in unit["sections"]}
            unit["sections"] = [by_id[sid] for sid in _topological(by_id, dependencies, section_order)]
            for section in unit["sections"]:
                section.update(unit_id=uid, unit_title=unit["title"])
            unit.pop("_order", None)
            stage["units"].append(unit)
    return path


def _weave_vocabulary(path, plans=None):
    """Place whole vocabulary sections beside authored, related grammar.

    Only navigation order and unit context change. Section/component/entry IDs
    and dependency lists remain the enrollment identities produced by the
    original builder. A move is accepted only when every known dependency is
    still earlier (or, for entry dependencies, in the same section). Invalid,
    absent or unsafe mappings leave their sections in the original location.
    """
    plans = _PATH.get("VOCABULARY_WEAVE", ()) if plans is None else plans
    if not isinstance(plans, (list, tuple)):
        return path
    rules = []
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        forms, targets = plan.get("word_forms"), plan.get("before")
        if all(isinstance(values, (list, tuple)) and values
               and all(isinstance(value, str) and value for value in values)
               for values in (forms, targets)):
            rules.append((frozenset(forms), targets))
    if not rules:
        return path

    result = deepcopy(path)

    def family(section, expected):
        return bool(section["entries"]) and all(
            entry["kind"] == "learning" and entry.get("family") == expected
            for entry in section["entries"])

    def ordered_safely(sections):
        positions = {section["id"]: index for index, section in enumerate(sections)}
        owners = {entry["id"]: positions[section["id"]]
                  for section in sections for entry in section["entries"]}
        for section in sections:
            position = positions[section["id"]]
            if any(positions[dependency] >= position for dependency in section.get("depends_on", ())
                   if dependency in positions):
                return False
            if any(owners[dependency] > position for entry in section["entries"]
                   for dependency in entry.get("depends_on", ()) if dependency in owners):
                return False
        return True

    for stage in result["stages"]:
        if stage.get("optional"):
            continue
        originals = [(unit, section) for unit in stage["units"] for section in unit["sections"]]
        grammar = {entry["id"]: (unit, section) for unit, section in originals
                   if not section.get("optional") and family(section, "grammar")
                   for entry in section["entries"]}
        for source_unit, source in originals:
            if source.get("optional") or not family(source, "word"):
                continue
            titles = {entry.get("route_key", entry.get("title")) for entry in source["entries"]}
            targets = next((targets for forms, targets in rules if forms & titles), None)
            # The first matching rule owns this section even when its targets
            # are missing. A later rule must not silently reinterpret it.
            target = next((grammar[cid] for cid in targets if cid in grammar), None) if targets else None
            if target is None or source.get("unresolved_dependencies"):
                continue
            all_sections = flatten_sections(result)
            known = {section["id"] for section in all_sections}
            if any(dependency not in known for dependency in source.get("depends_on", ())):
                continue
            target_unit, target_section = target
            before_source = list(source_unit["sections"])
            before_target = list(target_unit["sections"])
            source_unit["sections"].remove(source)
            target_unit["sections"].insert(target_unit["sections"].index(target_section), source)
            if not ordered_safely(flatten_sections(result)):
                source_unit["sections"] = before_source
                if target_unit is not source_unit:
                    target_unit["sections"] = before_target
                continue
            source.update(unit_id=target_unit["id"], unit_title=target_unit["title"])
            if not source_unit["sections"]:
                stage["units"].remove(source_unit)
    titles = _PATH.get("UNIT_CONTEXT_TITLES", {})
    if isinstance(titles, dict):
        for stage in result["stages"]:
            if stage.get("optional"):
                continue
            for unit in stage["units"]:
                identities = {entry["id"] for section in unit["sections"] for entry in section["entries"]}
                title = next((value for key, value in titles.items() if key in identities
                              and isinstance(value, str) and value.strip()), None)
                if title:
                    unit["title"] = title
                    for section in unit["sections"]:
                        section["unit_title"] = title
    return result


def _core_section_identity(stage_id, unit_key, topic, depth):
    return "section:" + _digest(f"{stage_id}|{unit_key}|{topic}|{depth}")


def _core_unit_identity(stage_id, unit_key):
    return "unit:" + _digest(f"{stage_id}|{unit_key}")


def study_path_records(kana_rows, learning_items=None, extra_items=()):
    """Entry records in catalogue order, without sections or learner state.

    Each record is (stage_index, unit_key, unit_title, topic, depth, entry):
    stage_index indexes STAGES, entry carries the enrollment identity fields.
    Shared by the core route and parallel study routes (design 10 section 6.3).
    """
    from .domain import _eligible_kana
    from .kana_dependencies import practice_kana_dependencies
    from .content_library_index import isolated_copy
    from .learning_content import learning_item_views as bundled_items
    from .learning_dependencies import validate_catalog
    from .kana_reading_rules import rule_for_kana, dependencies_for_kana
    kana_rows = list(kana_rows)
    kana_dependencies = practice_kana_dependencies(kana_rows)
    # isolated_copy: independent lessons; templates of a catalogue view stay shared (never read here).
    items = isolated_copy(list(bundled_items() if learning_items is None else learning_items))
    order = validate_catalog(items)
    catalogue = {item["id"]: item for item in items}
    extras = {}
    for item in extra_items:
        if item["id"] not in catalogue:
            extras[item["id"]] = deepcopy(item)
    records, depths, stage_of = [], {}, {}
    topics = None
    for cid in order:
        item = catalogue[cid]
        if topics is None and item["family"] == "word":
            topics = _word_topic_index()  # once per build, only when a word needs it
        route = _learning_route(item, topics)
        deps = item.get("depends_on", [])
        stage = max([route[0], *(stage_of[d] for d in deps)])
        depths[cid] = 1 + max((depths[d] for d in deps), default=-1)
        stage_of[cid] = stage
        entry = {
            "id": cid, "kind": "learning", "content_id": cid, "title": item["title"],
            "family": item["family"], "domain": item.get("domain"),
            "teaching_group": _coherence(item) if item["family"] == "grammar" else route[3],
            "depends_on": list(deps)}
        if item["family"] == "word":
            key = _word_topic(item, topics)[1]
            if key and key != item["title"]:
                # Only a reading-specific placement carries its key, so weave
                # rules keyed by the plain form keep their old sections.
                entry["route_key"] = key
        records.append((stage, route[1], route[2], route[3], depths[cid], entry))
    for item in sorted(extras.values(), key=lambda i: i["id"]):
        records.append((7, "personal:" + item["family"], "自选词义" if item["family"] == "word" else "自选规则",
                        item["title"], 0, {"id": item["id"], "kind": "learning", "content_id": item["id"],
                        "title": item["title"], "family": item["family"], "domain": item.get("domain"),
                        "depends_on": list(item.get("depends_on", []))}))
    for row in kana_rows:
        for script in ("hiragana", "katakana"):
            if not row.get(script):
                continue
            eligible = _eligible_kana(row, script, True, audio_only=False)
            route = _kana_route(row, script, eligible)
            topic = route[3]
            if script == "katakana":
                topic = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in topic)
            route = (*route[:3], ("平假名" if script == "hiragana" else "片假名") + " · " + topic)
            entry = {"id": f"kana:{row['id']}:{script}", "kind": "kana" if eligible else "reference",
                     "content_id": row["id"], "script": script, "title": row[script], "depends_on": []}
            # Keep the original route and identities for saved enrollment. A
            # retired script remains a readable reference, never a dead quiz.
            if eligible and not direction_allowed(row["id"], "kana_choice", script,
                    category=row.get("category"), symbol=row[script]):
                entry.update(kind="reference", practice_retired=True)
            shared_rule = rule_for_kana(row)
            if shared_rule and shared_rule in catalogue:
                # Preserve the old identity for enrolled history and glyph
                # lookup. New enrollment belongs to one cross-script rule.
                entry.update(kind="reference", practice_retired=True,
                             reading_rule_id=shared_rule)
                alphabet = "平假名" if script == "hiragana" else "片假名"
                route = (6, "reference:" + script, alphabet + "与旧字形",
                         alphabet + " · 共用读音规则的字形")
            if entry["kind"] == "kana":
                entry["depends_on"] = list(kana_dependencies.get(entry["id"], ()))
                entry["depends_on"].extend(rule for rule in dependencies_for_kana(row)
                    if rule in catalogue and rule not in entry["depends_on"])
            # Keep depth zero: prerequisites may reorder existing sections, but
            # must not replace their stable enrollment identities.
            records.append((*route, 0, entry))
    return records


def assemble_study_path(records, *, version=None, scope=None, section_identity=None,
                        unit_identity=None, scope_key="", resolvable=frozenset()):
    """Group entry records into ordered stages/units/sections (pure, detached).

    Defaults reproduce the core route exactly. A parallel route supplies its
    own route-scoped section/unit identity functions and scope_key; entry
    dependencies in resolvable are outside this tree but not unresolved.
    """
    section_identity = _core_section_identity if section_identity is None else section_identity
    unit_identity = _core_unit_identity if unit_identity is None else unit_identity
    sections = {}
    identity_set = set()
    for position, (stage, unit_key, unit_title, topic, depth, entry) in enumerate(records):
        if entry["id"] in identity_set:
            raise ValueError("学习路径包含重复身份：" + entry["id"])
        identity_set.add(entry["id"])
        sid = section_identity(STAGES[stage][0], unit_key, topic, depth)
        section = sections.setdefault(sid, {"id": sid, "title": topic, "entries": [], "depends_on": [],
            "optional": stage >= 6, "stage_id": STAGES[stage][0], "stage_title": STAGES[stage][1],
            "unit_id": unit_identity(STAGES[stage][0], unit_key), "unit_title": unit_title,
            "_order": _unit_priority(stage, unit_key, position)})
        section["entries"].append(entry)
    owner = {entry["id"]: sid for sid, section in sections.items() for entry in section["entries"]}
    for sid, section in sections.items():
        dependencies = {owner[d] for entry in section["entries"] for d in entry["depends_on"] if d in owner and owner[d] != sid}
        section["depends_on"] = sorted(dependencies, key=lambda d: sections[d]["_order"])
        unresolved = sorted({d for entry in section["entries"] for d in entry["depends_on"]
                             if d not in owner and d not in resolvable})
        if unresolved:
            section["unresolved_dependencies"] = unresolved
    dependencies = {sid: section["depends_on"] for sid, section in sections.items()}
    section_order = {sid: section["_order"] for sid, section in sections.items()}
    result = {"version": PATH_VERSION if version is None else version, "stages": [],
              "scope": "bundled_learning_identities_and_kana_scripts" if scope is None else scope}
    for stage_id, title in STAGES:
        stage_sections = [s for s in sections.values() if s["stage_id"] == stage_id]
        if not stage_sections:
            continue
        units = {}
        for section in stage_sections:
            unit = units.setdefault(section["unit_id"], {"id": section["unit_id"], "title": section["unit_title"],
                                    "sections": [], "_order": section["_order"]})
            unit["sections"].append(section)
        units, edges = _merge_cyclic_units(units, dependencies)
        ordered = _topological(units, edges, {uid: u["_order"] for uid, u in units.items()})
        stage_units = []
        for uid in ordered:
            unit = units[uid]
            topic_order = {}
            signatures = {}
            for section in unit["sections"]:
                signature = tuple(sorted({str(e.get("teaching_group", section["title"])) for e in section["entries"]}))
                signatures[section["id"]] = signature
                topic_order[signature] = min(topic_order.get(signature, section_order[section["id"]]), section_order[section["id"]])
            pedagogical_order = {s["id"]: (topic_order[signatures[s["id"]]], section_order[s["id"]]) for s in unit["sections"]}
            order = _topological([s["id"] for s in unit["sections"]], dependencies,
                                 pedagogical_order)
            unit["sections"] = [sections[sid] for sid in order]
            for section in unit["sections"]:
                section.update(unit_id=uid, unit_title=unit["title"])
                section.pop("_order", None)
            unit.pop("_order", None)
            stage_units.append(unit)
        result["stages"].append({"id": stage_id, "title": title, "optional": stage_id in {"stage:reference", "stage:personal"}, "units": stage_units})
    return _weave_vocabulary(_combine_micro_lessons(result, scope_key))


def _ordered_study_path(records, order):
    """(route, trouble): the core route with the given beginning (main_route_order); today's route
    without one. ``trouble``: the route was repaired (prerequisite first, sections split, titles
    replaced; a content error order_problems reports) and keeps every lesson.

    The lessons of the beginning leave the records; the others are assembled
    as before (their dependencies on the beginning are resolvable), a section
    or unit of theirs that now holds other lessons than the one of the same id
    in today's route gets a new id (main_route_order.renew_rest_ids), and the
    beginning's units open the first stage after the sounds stage.
    """
    from . import main_route_order as given
    layout_of = given.structured_layout if given.is_structured(order) else given.flat_layout
    layout, trouble = layout_of(order, records, words_title=ORDER_WORDS_TITLE, opening_title=ORDER_OPENING_TITLE,
                                first_stage=1, optional_stage=_REFERENCE_STAGE)
    chosen = {cid for _, sections in layout for _, ids in sections for cid in ids}
    if not chosen:
        return assemble_study_path(records), trouble
    rest = [record for record in records if record[5]["id"] not in chosen]
    units = given.beginning_units(layout, records, STAGES[1], digest=_digest)
    path = given.renew_rest_ids(assemble_study_path(rest, resolvable=frozenset(chosen)),
                                assemble_study_path(records), digest=_digest)
    return given.join_beginning(path, units, STAGES[1], [stage_id for stage_id, _ in STAGES]), trouble


def build_study_path(kana_rows, learning_items=None, extra_items=(), *, library_of=None, main_route_order=None):
    """Return independent nested snapshots covering every supplied identity.

    Existing learner-added dictionary senses appear in optional self-selected
    extension units. Calling this function reads or changes no learner state.
    library_of (content id -> extension library id or None): an extension
    library's large sections are cut (design 11 section 16, M8(a)); without
    it, and when no library section is large, the tree is the one built before.
    main_route_order (tests, tools; None: the content's MAIN_ROUTE_ORDER): the
    given beginning of the route (main_route_order); absent or empty, the
    route is exactly the one built before. Whenever main_route_order.order_problems
    finds an error in it, one diagnostic is recorded (the route is built as it
    is built without the check).
    """
    from . import main_route_order as given
    records = study_path_records(kana_rows, learning_items, extra_items)
    value = _PATH.get(given.ORDER_KEY) if main_route_order is None else main_route_order
    order = value if given.is_structured(value) else given.order_ids(value)
    usable = given.order_ids(value) and len(STAGES) > 1
    path, trouble = _ordered_study_path(records, order) if usable else (assemble_study_path(records), False)
    if value is not None and (trouble or given.problem_count(given.order_problems(
            value, records, first_stage=1, optional_stage=_REFERENCE_STAGE, library_of=library_of))):
        # A content error the check reports (tools/check_main_route_order.py): one diagnostic per build.
        from .diagnostic_events import record_error
        record_error("main_route_order", "read", ValueError())
    if library_of is None:
        return path
    from .route_mix.sections import split_library_sections
    return split_library_sections(path, library_of, fixed_stage_ids=tuple(stage_id for stage_id, _ in STAGES[:1]),
                                  digest=_digest)
