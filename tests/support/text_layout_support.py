"""Shared text layout fixtures and assertions; no collected scenarios."""
from travertino.node import Node
from types import SimpleNamespace
from travertino.size import at_least
import math


def leaf(style, text_length=90, font_scale=1):
    node = Node(style)
    node.style._assigned_applicator = SimpleNamespace(node=node)
    widths = []

    def measure(width):
        widths.append(width)
        node.intrinsic.width = at_least(0)
        node.intrinsic.height = math.ceil(text_length * 7 * font_scale / width) * 18 * font_scale

    node._impl = SimpleNamespace(_measure_text_width=measure)
    node.intrinsic.width = at_least(0)
    node.intrinsic.height = 18
    return node, widths
