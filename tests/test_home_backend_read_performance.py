"""Read-only home projections preserve ordering, state and refresh visibility."""
from datetime import timedelta
import random

from support.learning_store_fixtures import START, item, question, ready, stores
from kanamemory import study_path_catalog


def old_topological(nodes, dependencies, order):
    remaining, result = set(nodes), []
    while remaining:
        choices = [node for node in remaining if not (set(dependencies.get(node, ())) & remaining)]
        if not choices:
            raise ValueError("学习路径依赖成环")
        chosen = min(choices, key=lambda node: order[node])
        result.append(chosen)
        remaining.remove(chosen)
    return result


def test_topology_matches_original_order_and_errors_for_diverse_graphs():
    rng = random.Random(20261030)
    for index in range(800):
        nodes = [f"n:{n}" for n in range(rng.randrange(80))]
        dependencies = {node: [prior for prior in nodes[:n] if rng.random() < .08]
                        for n, node in enumerate(nodes)}
        for node in nodes:
            if rng.random() < .2:
                dependencies[node].append("outside")
            if dependencies[node] and rng.random() < .2:
                dependencies[node].append(dependencies[node][0])
        ranks = list(range(len(nodes)))
        rng.shuffle(ranks)
        order = dict(zip(nodes, ranks))
        if index % 17 == 0:
            order = {node: ((rank//5, rank//2), (rank//4, rank)) for node, rank in order.items()}
        elif index % 5 == 0:
            order = {node: (rank//5, rank) for node, rank in order.items()}
        if index % 4 == 0:
            order = dict(zip(nodes, [rank//4 for rank in ranks]))
        if index % 7 == 0 and nodes:
            dependencies[nodes[0]].append(nodes[-1])
            dependencies[nodes[-1]].append(nodes[0])
        if index % 11 == 0:
            order = {node: [rank] for node, rank in order.items()}
        if index % 13 == 0 and nodes:
            order.pop(nodes[-1])
        def outcome(function):
            try:
                return ("return", function(nodes, dependencies, order))
            except Exception as exc:
                return ("raise", type(exc), str(exc))
        assert outcome(study_path_catalog._topological) == outcome(old_topological)


def test_counts_does_not_hydrate_unneeded_unit_history_and_never_changes_rows(stores, monkeypatch):
    store, learning = stores
    learning.enroll([item("base"), item("child", depends_on=["base"])], now=START)
    for cid in ("base", "child"):
        learning.append_questions(question(cid, cid+":meaning"), now=START)
        learning.append_questions(question(cid, cid+":application", ability="application", kind="grammar_cloze"), now=START)
    before = tuple(store._db.iterdump())
    def no_unit_hydration(*args, **kwargs):
        raise AssertionError("Homepage prerequisite counts must not load per-unit histories")
    monkeypatch.setattr(learning, "get_unit", no_unit_hydration)
    result = learning.counts(now=START)
    assert (result["pending"], result["instruction"], result["blocked"], result["blocked_content"]) == (4,1,2,1)
    assert tuple(store._db.iterdump()) == before


def test_counts_refreshes_after_real_input_acknowledgement_pause_and_rollback(stores):
    store, learning = stores
    learning.enroll([item("base"), item("child", depends_on=["base"])], now=START)
    learning.append_questions(question("child", "q"), now=START)
    assert learning.counts(now=START)["blocked_content"] == 1
    learning.acknowledge_prerequisites("child", now=START)
    assert learning.counts(now=START)["instruction"] == 1
    learning.set_suspended("child", "understanding", True)
    assert learning.counts(now=START)["disabled"] == 1
    learning.set_suspended("child", "understanding", False)
    when = ready(learning, "child")
    assert learning.counts(now=START)["waiting"] == 1
    learning.save_draft("q", {"text": "unfinished"})
    snapshot = learning.get_question("q")
    assert learning.counts(now=when)["ready"] == 1
    assert learning.get_question("q") == snapshot
    learning.submit("q", "right", True, now=when)
    frozen = learning.get_question("q")
    assert learning.counts(now=when+timedelta(seconds=1))["ready"] == 1
    assert learning.get_question("q") == frozen
    before = tuple(store._db.iterdump())
    store._db.execute("BEGIN")
    store._db.execute("UPDATE learning_unit SET suspended=1 WHERE content_id='child'")
    assert learning.counts(now=when)["disabled"] == 1
    store._db.rollback()
    assert learning.counts(now=when)["ready"] == 1
    assert tuple(store._db.iterdump()) == before
