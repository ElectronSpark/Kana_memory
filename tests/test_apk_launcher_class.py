"""A packaged app must hold the class it launches (tools/app_build_identity.py, verify-apk).

A generated Android project that has lost its Java sources still builds: the manifest names the activity, Gradle
compiles what is left, and the app dies at its first start. Hand-made dex files only; no Android tool runs.
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from support.dex_files import minimal_dex, write_apk

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("app_build_identity_launcher", ROOT / "tools/app_build_identity.py")
identity_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity_tool)

MAIN, BRIDGE = "Lorg/beeware/android/MainActivity;", "Lorg/example/app/Bridge;"
IDENTITY = {"package_id": "org.example.app", "build_number": 10203, "version": "1.2.3"}


def badging(activity="org.beeware.android.MainActivity"):
    package = "package: name='org.example.app' versionCode='10203' versionName='1.2.3'\n"
    return package + (f"launchable-activity: name='{activity}'  label='' icon=''\n" if activity else "")


def test_only_defined_classes_count():
    assert identity_tool.dex_defined_classes(minimal_dex([MAIN, BRIDGE])) == {MAIN, BRIDGE}
    # a class that another class merely names is not in the file
    assert identity_tool.dex_defined_classes(minimal_dex([BRIDGE], referenced=[MAIN])) == {BRIDGE}
    assert identity_tool.dex_defined_classes(minimal_dex([])) == set()
    for data in (b"PK\x03\x04", b"", b"dex\n035\0"):
        with pytest.raises(ValueError, match="Not a dex file"):
            identity_tool.dex_defined_classes(data)


def test_an_apk_without_its_launcher_class_is_refused(tmp_path):
    # the class may sit in any of the dex files
    whole = write_apk(tmp_path / "whole.apk", {"classes.dex": minimal_dex([BRIDGE]), "classes2.dex": minimal_dex([MAIN]),
                                               "assets/other.dex.txt": b"not a dex member"})
    identity_tool.validate_launcher(whole, badging())
    # what happened: the sources were gone, the rest was built, and the manifest still named the activity
    broken = write_apk(tmp_path / "broken.apk", {"classes.dex": minimal_dex([BRIDGE], referenced=[MAIN])})
    with pytest.raises(ValueError, match="does not contain its launcher class org.beeware.android.MainActivity"):
        identity_tool.validate_launcher(broken, badging())
    with pytest.raises(ValueError, match="no launchable activity"):
        identity_tool.validate_launcher(whole, badging(activity=None))
    other = write_apk(tmp_path / "other.apk", {"classes.dex": minimal_dex(["Lorg/example/app/Start;"])})
    identity_tool.validate_launcher(other, badging(activity="org.example.app.Start"))


def test_the_package_check_runs_the_launcher_check_after_the_identity_check(tmp_path, monkeypatch):
    monkeypatch.setattr(identity_tool.subprocess, "run", lambda arguments, **kwargs: SimpleNamespace(stdout=badging()))
    good = write_apk(tmp_path / "good.apk", {"classes.dex": minimal_dex([MAIN])})
    identity_tool.verify_apk(good, tmp_path / "aapt.exe", IDENTITY)
    broken = write_apk(tmp_path / "broken.apk", {"classes.dex": minimal_dex([BRIDGE])})
    with pytest.raises(ValueError, match="launcher class"):
        identity_tool.verify_apk(broken, tmp_path / "aapt.exe", IDENTITY)
    with pytest.raises(ValueError, match="stale"):  # a stale identity is still reported first
        identity_tool.verify_apk(broken, tmp_path / "aapt.exe", {**IDENTITY, "version": "9.9.9"})
