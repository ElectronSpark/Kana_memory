"""A personal package from a clean public tree: the identity is data outside the tree, applied to the build copy.

Only placeholder identities appear here; a real identity file stays outside the repository.
"""
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("app_build_identity", ROOT / "tools/app_build_identity.py")
identity_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity_tool)

DEMO_PYPROJECT = ('[tool.briefcase]\nproject_name = "Kana Memory"\nbundle = "org.example.demo"\nversion = "0.2.0"\n\n'
                  '[tool.briefcase.app.kanamemory]\nformal_name = "KanaMemoryDemo"\nsources = ["src/kanamemory"]\n')
PLACEHOLDER = {"format": "kanamemory-build-identity-v1", "profile": "personal",
               "bundle": "org.example.yourname", "formal_name": "YourAppName"}


def identity_file(tmp_path, **changes):
    path = tmp_path / "outside/personal_identity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**PLACEHOLDER, **changes}), encoding="utf-8")
    return path


def app_tree(root):
    (root / "src/kanamemory").mkdir(parents=True)
    (root / "pyproject.toml").write_text(DEMO_PYPROJECT, encoding="utf-8")
    (root / "src/kanamemory/build_info.py").write_text('VERSION = "0.2.0"\nBUILD_NUMBER = 20000\nBUILD_DATE = "2026-10-11"\n',
                                                       encoding="utf-8")
    return root


def test_a_placeholder_identity_file_is_read():
    assert identity_tool.IDENTITY_KEYS == set(PLACEHOLDER)


@pytest.mark.parametrize("changes, message", [
    ({"format": "other"}, "format"), ({"profile": "demo"}, "profile"),
    ({"bundle": "Org.Example"}, "reverse domain"), ({"bundle": "example"}, "reverse domain"),
    ({"bundle": 'org.example"\nversion = "9'}, "reverse domain"), ({"formal_name": "Name/../x"}, "formal_name"),
    ({"formal_name": ""}, "formal_name"), ({"bundle": "org.example.demo"}, "demo or QA"),
    ({"bundle": "org.example.qa"}, "demo or QA")])
def test_unsafe_or_foreign_identities_are_refused(tmp_path, changes, message):
    with pytest.raises(ValueError, match=message):
        identity_tool.read_identity_file(identity_file(tmp_path, **changes))


def test_extra_or_missing_keys_and_unreadable_files_are_refused(tmp_path):
    path = identity_file(tmp_path)
    path.write_text(json.dumps({**PLACEHOLDER, "signing": "x"}), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly"):
        identity_tool.read_identity_file(path)
    path.write_text(json.dumps({key: PLACEHOLDER[key] for key in ("format", "profile", "bundle")}), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly"):
        identity_tool.read_identity_file(path)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        identity_tool.read_identity_file(path)
    with pytest.raises(ValueError, match="unreadable"):
        identity_tool.read_identity_file(tmp_path / "missing.json")


def test_the_identity_replaces_bundle_and_formal_name_only(tmp_path):
    text = identity_tool.applied_pyproject(DEMO_PYPROJECT, identity_tool.read_identity_file(identity_file(tmp_path)))
    assert text == DEMO_PYPROJECT.replace('"org.example.demo"', '"org.example.yourname"').replace(
        '"KanaMemoryDemo"', '"YourAppName"')
    with pytest.raises(ValueError, match="one Briefcase bundle"):
        identity_tool.applied_pyproject(DEMO_PYPROJECT + 'bundle = "x"\n', {"bundle": "a.b", "formal_name": "A"})


def test_the_identity_is_read_through_the_file_and_the_tree_stays_the_demo(tmp_path):
    app = app_tree(tmp_path / "public/app")
    before = (app / "pyproject.toml").read_bytes()
    assert identity_tool.load_identity(app)["package_id"] == "org.example.demo.kanamemory"
    personal = identity_tool.load_identity(app, identity_file(tmp_path))
    assert (personal["package_id"], personal["apk_name"]) == ("org.example.yourname.kanamemory",
                                                              "YourAppName-0.2.0.debug.apk")
    assert (app / "pyproject.toml").read_bytes() == before


def test_apply_writes_the_build_copy_once_and_never_a_source_tree(tmp_path):
    copy = app_tree(tmp_path / "km/app")
    path = identity_file(tmp_path)
    identity_tool.apply_identity(copy, path)
    applied = (copy / "pyproject.toml").read_bytes()
    assert b'bundle = "org.example.yourname"' in applied and b'formal_name = "YourAppName"' in applied
    identity_tool.apply_identity(copy, path)
    assert (copy / "pyproject.toml").read_bytes() == applied
    assert identity_tool.load_identity(copy)["package_id"] == "org.example.yourname.kanamemory"
    for marker in ("clean-source-manifest.json", ".git"):
        source = app_tree(tmp_path / marker.strip(".") / "tree/app")
        (source.parent / marker).write_text("{}", encoding="utf-8")
        before = (source / "pyproject.toml").read_bytes()
        with pytest.raises(ValueError, match="never to a source tree"):
            identity_tool.apply_identity(source, path)
        assert (source / "pyproject.toml").read_bytes() == before


def test_only_the_demo_edition_of_the_app_configuration_is_accepted(tmp_path):
    assert identity_tool.require_demo_edition(DEMO_PYPROJECT) == DEMO_PYPROJECT
    personal = identity_tool.applied_pyproject(DEMO_PYPROJECT, identity_tool.read_identity_file(identity_file(tmp_path)))
    qa = DEMO_PYPROJECT.replace('"org.example.demo"', '"org.example.qa"').replace('"KanaMemoryDemo"', '"KanaMemoryQA"')
    for text in (personal, qa, DEMO_PYPROJECT.replace('"KanaMemoryDemo"', '"Other"')):
        with pytest.raises(ValueError, match="demo identity"):
            identity_tool.require_demo_edition(text)
    with pytest.raises(ValueError, match="one Briefcase bundle"):
        identity_tool.require_demo_edition(DEMO_PYPROJECT + 'bundle = "org.example.demo"\n')


def test_a_checkout_with_crlf_line_endings_is_the_same_edition(tmp_path):
    """A Windows checkout may hold the file with CRLF line endings (git's autocrlf): the demo edition is still the
    demo edition, and applying an identity changes the two declarations only, never a line ending."""
    assert "\r" not in DEMO_PYPROJECT
    windows = DEMO_PYPROJECT.replace("\n", "\r\n")
    assert identity_tool.require_demo_edition(windows) == windows
    personal = identity_tool.applied_pyproject(windows, identity_tool.read_identity_file(identity_file(tmp_path)))
    assert personal.count("\r\n") == windows.count("\r\n") and "\n" not in personal.replace("\r\n", "")
    assert personal.replace("\r\n", "\n") == identity_tool.applied_pyproject(
        DEMO_PYPROJECT, identity_tool.read_identity_file(identity_file(tmp_path)))
    with pytest.raises(ValueError, match="demo identity"):
        identity_tool.require_demo_edition(personal)


def test_the_command_line_reads_with_the_identity_file(tmp_path):
    app = app_tree(tmp_path / "public/app")
    result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools/app_build_identity.py"), "read", str(app),
                             "--identity-file", str(identity_file(tmp_path))],
                            capture_output=True, text=True, encoding="utf-8", check=True)
    assert json.loads(result.stdout)["package_id"] == "org.example.yourname.kanamemory"


def test_every_identity_check_of_the_runner_reads_through_the_identity_file():
    runner = (ROOT / "environment/run_app.ps1").read_text("utf8")
    calls = re.findall(r"\$[Ii]dentityTool (read|sync-gradle|verify-apk|apply) [^\n]*", runner)
    assert sorted({call for call in calls}) == ["apply", "read", "sync-gradle", "verify-apk"]
    for line in runner.splitlines():
        # The one bare read only tells a development tree from a QA project; no check uses its result.
        if re.search(r"\$[Ii]dentityTool (read|sync-gradle|verify-apk) ", line) and "$treeIdentity = " not in line:
            assert "@identityArgs" in line, line
    assert runner.count("$treeIdentity = ") == 1
    # A development run reads and applies the private identity file (the learner data directory follows it).
    assert "$identityArgs = @('--identity-file', $developmentIdentity)" in runner
    assert "$identityTool apply $appBuild --identity-file $developmentIdentity" in runner
    # The identity goes into the build copy, after it was copied and before any Briefcase stage.
    apply = runner.index("$identityTool apply $appBuild")
    assert runner.index("Copy-Item -LiteralPath (Join-Path $appSource 'pyproject.toml')") < apply
    assert apply < runner.index("briefcase create android")
    # The demo guard stays: a clean public tree without both values is the demo.
    assert "Public demo requires its separate org.example.demo.kanamemory package ID" in runner


def test_the_personal_audit_expects_the_package_of_the_identity_file(tmp_path):
    spec = importlib.util.spec_from_file_location("verify_distribution_apk", ROOT / "environment/verify_distribution_apk.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    copy = app_tree(tmp_path / "km/app")
    path = identity_file(tmp_path)
    other = identity_file(tmp_path / "other", bundle="org.example.someone")
    for profile, file, message in (("personal", other, "does not match"), ("demo", path, "belongs to the personal"),
                                   ("personal", None, "does not match")):
        with pytest.raises(ValueError, match=message):
            audit.verify(tmp_path / "none.apk", copy, tmp_path / "aapt.exe", profile=profile, identity_file=file)
    identity_tool.apply_identity(copy, path)
    with pytest.raises(Exception) as raised:  # past the package check: the missing aapt is reached
        audit.verify(tmp_path / "none.apk", copy, tmp_path / "aapt.exe", profile="personal", identity_file=path)
    assert "does not match" not in str(raised.value)


def run_script(script, *arguments):
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *arguments],
                          text=True, capture_output=True, timeout=120)


def project(tmp_path, *, public):
    root = tmp_path / ("public-tree" if public else "development-tree")
    (root / "environment").mkdir(parents=True)
    for name in ("run_app.ps1", "build_personal_app.ps1"):
        (root / "environment" / name).write_bytes((ROOT / "environment" / name).read_bytes())
    if public:
        (root / "clean-source-manifest.json").write_text("{}", encoding="utf-8")
    return root


@pytest.mark.skipif(not shutil.which("powershell"), reason="Windows build entry point")
def test_the_runner_refuses_a_half_given_or_misplaced_personal_build_before_any_build(tmp_path):
    public = project(tmp_path, public=True)
    content = tmp_path / "content"
    content.mkdir()
    (content / "teaching_content.sqlite3").write_bytes(b"")
    runner = public / "environment/run_app.ps1"
    path = identity_file(tmp_path)
    cases = [
        (runner, ["-IdentityFile", str(path)], "needs both"),
        (runner, ["-ContentRoot", str(content)], "needs both"),
        (runner, ["-IdentityFile", str(path), "-ContentRoot", str(content), "-PublicBuild"], "takes no"),
        (runner, ["-IdentityFile", str(public / "id.json"), "-ContentRoot", str(content)], "stay outside"),
        (runner, ["-IdentityFile", str(path), "-ContentRoot", str(tmp_path)], "no teaching pack"),
        (project(tmp_path, public=False) / "environment/run_app.ps1",
         ["-IdentityFile", str(path), "-ContentRoot", str(content)], "clean public tree"),
        (project(tmp_path / "x", public=False) / "environment/build_personal_app.ps1",
         ["-IdentityFile", str(path), "-ContentRoot", str(content)], "clean public tree"),
    ]
    for script, arguments, message in cases:
        result = run_script(script, "-Stage", "create", *arguments)
        assert result.returncode != 0, arguments
        assert message in result.stderr, (arguments, result.stderr[-600:])


def development_tree(root, *, identity=None, personal_file=None):
    """A tree without clean-source-manifest.json: the runner, the identity tool and an app of the given identity."""
    root = project(root, public=False)
    (root / "tools").mkdir()
    (root / "tools/app_build_identity.py").write_bytes((ROOT / "tools/app_build_identity.py").read_bytes())
    app = app_tree(root / "app")
    if identity is not None:
        (app / "pyproject.toml").write_text(identity_tool.applied_pyproject(DEMO_PYPROJECT, identity), encoding="utf-8")
    if personal_file is not None:
        (root / "config").mkdir()
        (root / "config/personal_identity.json").write_bytes(personal_file.read_bytes())
    return root / "environment/run_app.ps1"


@pytest.mark.skipif(not shutil.which("powershell"), reason="Windows build entry point")
def test_a_development_tree_builds_no_package_and_its_desktop_run_keeps_the_personal_identity(tmp_path):
    path = identity_file(tmp_path)
    python = ["-Python", sys.executable]
    refused = [
        (development_tree(tmp_path / "a", personal_file=path), ["-Stage", "create"], "builds no package"),
        (development_tree(tmp_path / "b", personal_file=path), ["-Stage", "package"], "builds no package"),
        (development_tree(tmp_path / "c", personal_file=path), ["-Stage", "desktop", "-PublicBuild"], "builds no package"),
        (development_tree(tmp_path / "d"), ["-Stage", "desktop"], "needs config/personal_identity.json"),
        (development_tree(tmp_path / "e", identity=identity_tool.read_identity_file(path), personal_file=path),
         ["-Stage", "desktop"], "must be the public edition"),
    ]
    for script, arguments, message in refused:
        result = run_script(script, *arguments, *python)
        assert result.returncode != 0, arguments
        assert message in result.stderr, (arguments, result.stderr[-600:])
        if not message.startswith("needs"):
            assert "export_clean_source.py" in result.stderr, result.stderr[-600:]
    # Past the tree check (and stopped by the missing environment check of this fake tree, before any build): the
    # desktop run with the identity file, and a QA project with its own identity.
    qa = {"bundle": "org.example.qa", "formal_name": "KanaMemoryQA"}
    for script, stage in ((development_tree(tmp_path / "f", personal_file=path), "desktop"),
                          (development_tree(tmp_path / "g", identity=qa), "create")):
        result = run_script(script, "-Stage", stage, *python)
        assert result.returncode != 0
        assert "Build environment differs" in result.stderr, result.stderr[-600:]
