"""Small authored synthetic multi-gap cases and generic control doubles."""
from types import SimpleNamespace

from kanamemory import multi_cloze


def question():
    prompt = "私（①）学校（②）行きます。"
    slots = []
    for index, (choices, role, target) in enumerate((
        (["は", "を", "も"], "primary", "grammar:synthetic:topic"),
        (["へ", "に", "で"], "background", "grammar:synthetic:destination"),
    )):
        quote = "（" + chr(0x2460 + index) + "）"
        start = prompt.index(quote)
        slots.append({"id": "slot" + str(index + 1), "start": start, "end": start + len(quote),
                      "quote": quote, "choices": choices, "target_id": target,
                      "ability": "application", "role": role})
    result = {"id": "synthetic:multi", "target_id": slots[0]["target_id"], "ability": "application",
              "kind": "grammar_cloze", "family": "grammar", "input_mode": "multi_cloze",
              "prompt": prompt, "feedback": "以我为话题，说明去学校。", "choices": [], "options": [],
              "multi_cloze": {"version": 1, "slots": slots,
                              "accepted_combinations": [["は", "へ"], ["は", "に"]]}}
    result["answer"] = multi_cloze.canonical_answer(result)
    result["accepted_answers"] = [result["answer"]]
    return result


class Node:
    def __init__(self, *children, text="", handler=None, **style):
        self.children, self.text, self.style = list(children), text, style
        self.handler, self.enabled = handler, True

    def add(self, *children):
        self.children.extend(children)

    def clear(self):
        self.children.clear()

    def press(self):
        if self.enabled and self.handler:
            return self.handler(self)


def ui(scale=1):
    return SimpleNamespace(col=lambda *nodes, **kw: Node(*nodes, **kw),
        row=lambda *nodes, **kw: Node(*nodes, **kw),
        label=lambda text, **kw: Node(text=text, **kw),
        button=lambda text, handler, **kw: Node(text=text, handler=handler, **kw),
        MUTED="#456", GREEN="#156", WHITE="#fff", font_scale=lambda: scale)


def descendants(node):
    yield node
    for child in node.children:
        yield from descendants(child)
