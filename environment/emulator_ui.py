"""Small ADB helper for repeatable manual checks of the development emulator."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

DEFAULT_ADB = Path.home() / "AppData/Local/Temp/km/briefcase/tools/android_sdk/platform-tools/adb.exe"
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"


class Emulator:
    def __init__(self, serial="emulator-5580", adb=DEFAULT_ADB):
        if not re.fullmatch(r"emulator-\d+", serial):
            raise ValueError("Use an emulator serial; this helper never targets a phone.")
        self.serial, self.adb = serial, str(adb)

    def run(self, *args):
        return subprocess.run([self.adb, "-s", self.serial, *map(str, args)], check=True, capture_output=True).stdout

    def tree(self):
        # uiautomator can return exit status 0 after failing to reach an idle
        # state. Never read a previous dump and treat stale coordinates as fresh.
        path = "/sdcard/kana-check-" + uuid.uuid4().hex + ".xml"
        try:
            for _ in range(3):
                result = self.run("shell", "uiautomator", "dump", path)
                if b"dumped to:" in result:
                    raw = self.run("shell", "cat", path)
                    return ET.fromstring(raw), raw
                time.sleep(1)
            raise RuntimeError("UI hierarchy did not become idle: " + result.decode("utf-8", "replace"))
        finally:
            self.run("shell", "rm", "-f", path)

    def snapshot(self, name):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("Use a simple artifact name.")
        ARTIFACTS.mkdir(exist_ok=True)
        tree, raw = self.tree()
        (ARTIFACTS / f"{name}.xml").write_bytes(raw)
        self.run("shell", "screencap", "-p", "/sdcard/kana-check.png")
        self.run("pull", "/sdcard/kana-check.png", ARTIFACTS / f"{name}.png")
        for node in tree.iter("node"):
            text = node.get("text") or node.get("content-desc")
            if text:
                print(f"{node.get('bounds')} {'*' if node.get('clickable') == 'true' else ' '} {text}")

    def tap(self, text, occurrence=0):
        tree, _ = self.tree()
        nodes = [n for n in tree.iter("node") if n.get("text") == text or n.get("content-desc") == text]
        node = nodes[occurrence]
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.get("bounds")))
        self.run("shell", "input", "tap", (x1 + x2) // 2, (y1 + y2) // 2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["snapshot", "tap"])
    parser.add_argument("value")
    parser.add_argument("--occurrence", type=int, default=0)
    args = parser.parse_args()
    emulator = Emulator()
    if args.action == "snapshot":
        emulator.snapshot(args.value)
    else:
        emulator.tap(args.value, args.occurrence)
