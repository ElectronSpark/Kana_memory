"""A generated project or a stale dist APK cannot silently retain old identity."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from support.dex_files import minimal_dex, write_apk

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("app_build_identity", ROOT / "tools/app_build_identity.py")
identity_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity_tool)


@pytest.fixture
def identity():
    return identity_tool.load_identity(ROOT / "app")


def test_real_app_identity_is_the_one_demo_edition(identity):
    # app/pyproject.toml exists in one edition, the demo; a personal identity is a build identity file.
    assert identity["package_id"] == identity_tool.DEMO_PACKAGE == "org.example.demo.kanamemory"
    assert identity["apk_name"] == f"KanaMemoryDemo-{identity['version']}.debug.apk"
    identity_tool.require_demo_edition((ROOT / "app/pyproject.toml").read_text("utf-8"))


def test_actual_android_36_badging_does_not_match_the_codename_suffix():
    # The shape of the badging of the 0.2.0 APK produced by build-tools 36.0.0.
    badging = "package: name='org.example.demo.kanamemory' versionCode='20000' versionName='0.2.0' platformBuildVersionName='16' platformBuildVersionCode='36' compileSdkVersion='36' compileSdkVersionCodename='16'\nsdkVersion:'26'\ntargetSdkVersion:'36'\n"
    identity_tool.validate_badging(badging, {"package_id": "org.example.demo.kanamemory", "version": "0.2.0", "build_number": 20000})


def test_duplicate_package_identity_fields_are_rejected(identity):
    badging = f"package: name='org.example.other' name='{identity['package_id']}' versionCode='{identity['build_number']}' versionName='{identity['version']}'"
    with pytest.raises(ValueError, match="duplicated"):
        identity_tool.validate_badging(badging, identity)


def test_gradle_update_preserves_other_configuration_and_is_idempotent(tmp_path, identity):
    path = tmp_path / "build.gradle"
    path.write_text('android {\n    defaultConfig {\n        versionCode 10000\n        versionName "0.1.0"\n        minSdkVersion 26\n    }\n}\n', encoding="utf-8")
    identity_tool.sync_gradle(path, identity)
    updated = path.read_bytes()
    assert f"versionCode {identity['build_number']}".encode() in updated
    assert f'versionName "{identity["version"]}"'.encode() in updated
    assert b"minSdkVersion 26" in updated
    identity_tool.sync_gradle(path, identity)
    assert path.read_bytes() == updated


@pytest.mark.parametrize("text", ['versionCode 10000\n', 'versionCode 1\nversionCode 2\nversionName "old"\n'])
def test_unrecognized_gradle_is_never_partially_modified(tmp_path, identity, text):
    path = tmp_path / "build.gradle"
    path.write_text(text, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="one generated"):
        identity_tool.sync_gradle(path, identity)
    assert path.read_bytes() == before


@pytest.mark.parametrize("changes", [{"versionName": "0.1.0"}, {"versionCode": "10000"}, {"name": "org.example.other"}])
def test_apk_badging_rejects_stale_name_code_or_package(identity, changes):
    fields = {"name": identity["package_id"], "versionCode": str(identity["build_number"]), "versionName": identity["version"]}
    good = "package: " + " ".join(f"{key}='{value}'" for key, value in fields.items())
    identity_tool.validate_badging(good, identity)
    fields.update(changes)
    with pytest.raises(ValueError, match="stale"):
        identity_tool.validate_badging("package: " + " ".join(f"{key}='{value}'" for key, value in fields.items()), identity)


def test_version_sources_must_agree_without_executing_python(tmp_path):
    (tmp_path / "src/kanamemory").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[tool.briefcase]\nversion="0.2.0"\nbundle="org.example.demo"\n[tool.briefcase.app.kanamemory]\nformal_name="KanaMemoryDemo"\n', encoding="utf-8")
    (tmp_path / "src/kanamemory/build_info.py").write_text('VERSION="0.1.0"\nBUILD_NUMBER=20000\nBUILD_DATE="2026-10-11"\nraise RuntimeError("must not execute")\n', encoding="utf-8")
    with pytest.raises(ValueError, match="disagree"):
        identity_tool.load_identity(tmp_path)


def badging_of(identity):
    return (f"package: name='{identity['package_id']}' versionCode='{identity['build_number']}' "
            f"versionName='{identity['version']}'\n"
            "launchable-activity: name='org.beeware.android.MainActivity'  label='' icon=''\n")


def test_aapt_receives_separate_arguments_for_paths_with_spaces(tmp_path, identity, monkeypatch):
    calls = []
    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(stdout=badging_of(identity))
    monkeypatch.setattr(identity_tool.subprocess, "run", run)
    aapt = tmp_path / "tools with spaces/aapt.exe"
    apk = write_apk(tmp_path / "目录 with spaces/current.apk",
                    {"classes.dex": minimal_dex(["Lorg/beeware/android/MainActivity;"])})
    identity_tool.verify_apk(apk, aapt, identity)
    assert calls[0][0] == [str(aapt), "dump", "badging", str(apk)]
    assert calls[0][1]["check"] is True
