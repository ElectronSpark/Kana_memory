"""Minimal native-widget and SQLite runtime diagnostic for later device testing."""

import sqlite3
import sys

import toga
from toga.style import Pack
from toga.style.pack import COLUMN


class KanaProbe(toga.App):
    def startup(self):
        with sqlite3.connect(":memory:") as connection:
            value = connection.execute("SELECT ?", ("日本語 あいうえお アイウエオ",)).fetchone()[0]
        message = (
            "ENVIRONMENT PROBE\n"
            f"Python {sys.version.split()[0]}\n"
            f"SQLite {sqlite3.sqlite_version}\n"
            f"Unicode round-trip: {value}\n"
            "ML Kit dependency is included; model/recognition has NOT been tested."
        )
        self.main_window = toga.MainWindow(title=self.formal_name)
        self.main_window.content = toga.Box(
            children=[toga.Label(message, style=Pack(margin=20))],
            style=Pack(direction=COLUMN),
        )
        self.main_window.show()


def main():
    return KanaProbe()
