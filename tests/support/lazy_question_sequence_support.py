"""Shared lazy question sequence fixtures and assertions; no collected scenarios."""
import sqlite3


def clone(source, target):
    target.mkdir()
    with sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True) as src:
        with sqlite3.connect(target/'progress.sqlite3') as dst:
            src.backup(dst)
