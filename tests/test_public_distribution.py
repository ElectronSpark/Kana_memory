"""Real clean-source builds: no private tree, inherited content, or user state."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tomllib
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
from build_demo_content import build_demo, digest
from check_public_content import MANIFEST, validate_apk, validate_resources
from export_clean_source import _public_file, export_source, selected_files, validate_engine_sources


def test_demo_content_is_reproducible_and_usable_without_private_tree(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    assert build_demo(left) == build_demo(right)
    assert validate_resources(left, ROOT / "demo/content.json") == validate_resources(right)
    with sqlite3.connect(left / "content.sqlite3") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT COUNT(*) FROM kana").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM word").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {"card", "review", "learning_unit", "setting", "learning_event"} & tables
        assert db.execute("SELECT license FROM source WHERE id='original-demo'").fetchone()[0] == "MIT"
    notice = (left / "NOTICE.txt").read_text("utf-8")
    assert (ROOT / "LICENSE").read_text("utf-8") in notice
    with pytest.raises(ValueError, match="new or empty"):
        build_demo(left)


def test_public_guard_rejects_extra_files_and_private_profile_even_with_fresh_hash(tmp_path):
    resource = tmp_path / "resources"
    build_demo(resource)
    extra = resource / "unexpected-audio.mp3"
    extra.write_bytes(b"private corpus canary")
    with pytest.raises(ValueError, match="extra"):
        validate_resources(resource)
    extra.unlink()
    with sqlite3.connect(resource / "teaching_content.sqlite3") as db:
        db.execute("UPDATE metadata SET value='personal' WHERE key='profile'")
    manifest = json.loads((resource / MANIFEST).read_text("utf-8"))
    manifest["files"]["teaching_content.sqlite3"] = digest(resource / "teaching_content.sqlite3")
    (resource / MANIFEST).write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(ValueError, match="Private"):
        validate_resources(resource)


def _fake_apk(path, resource, extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as inner:
        for file in resource.iterdir():
            inner.writestr("kanamemory/resources/" + file.name, file.read_bytes())
        if extra:
            inner.writestr("kanamemory/resources/" + extra, b"stale personal file")
    with zipfile.ZipFile(path, "w") as outer:
        outer.writestr("assets/chaquopy/app.imy", stream.getvalue())


def test_apk_check_inspects_actual_packaged_resources(tmp_path):
    resource = tmp_path / "resources"
    build_demo(resource)
    good, stale = tmp_path / "good.apk", tmp_path / "stale.apk"
    _fake_apk(good, resource)
    _fake_apk(stale, resource, "old-corpus.sqlite3")
    validate_apk(good, resource)
    with pytest.raises(ValueError, match="stale"):
        validate_apk(stale, resource)


def test_source_export_is_deterministic_and_has_no_private_assets_or_git(tmp_path):
    left, right = tmp_path / "clean-a", tmp_path / "clean-b"
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    manifest = export_source(left, archive=a)
    # Re-export the frozen snapshot: parallel development in the original
    # workspace must not masquerade as nondeterministic serialization.
    assert export_source(right, root=left, allowlist=left / "tools/public_source_files.txt", archive=b) == manifest
    assert digest(a) == digest(b)
    assert manifest["package_id"] == "org.example.demo.kanamemory"
    assert {"LICENSE", "LICENSE-SCOPE.txt"} <= manifest["files"].keys()
    briefcase = tomllib.loads((left / "app/pyproject.toml").read_text("utf-8"))["tool"]["briefcase"]
    assert briefcase["license"] == "MIT" and briefcase["bundle"] == "org.example.demo"
    assert not (left / ".git").exists()
    assert not (left / "data").exists()
    assert not (left / "research").exists()
    assert not (left / "app/src/kanamemory/resources").exists()
    assert not (left / "tools/build_app_content.py").exists()
    for name, expected in manifest["files"].items():
        assert digest(left / name) == expected
    with pytest.raises(ValueError, match="new or empty"):
        export_source(left)


def test_source_allowlist_cannot_escape_or_silently_include_keys(tmp_path):
    allow = tmp_path / "allow.txt"
    allow.write_text("../outside.py\n", "utf-8")
    with pytest.raises(ValueError, match="Unsafe"):
        selected_files(tmp_path, allow)
    source = tmp_path / "engine.py"
    source.write_text("token = '" + "sk-" + "a" * 45 + "'", "utf-8")
    allow.write_text("engine.py\n", "utf-8")
    with pytest.raises(ValueError, match="Credential"):
        selected_files(tmp_path, allow)


def test_documents_drop_marked_private_blocks_and_refuse_delivery_paths():
    text = "design\n[private]\nround log\n[/private]\n[public-only] summary\nend\n"
    assert _public_file("docs/a.txt", text.encode("utf-8")) == b"design\nsummary\nend\n"
    for broken in ("[private]\nopen\n", "x\n[/private]\n", "[private]\n[private]\n[/private]\n",
                   "[private]\n[public-only] y\n[/private]\n"):
        with pytest.raises(ValueError, match="private block|Public-only"):
            _public_file("docs/a.txt", broken.encode("utf-8"))
    leak = "see ../g" + "drive/app.apk\n"
    with pytest.raises(ValueError, match="Personal delivery"):
        _public_file("docs/a.md", leak.encode("utf-8"))
    assert _public_file("docs/a.txt", ("[private]\n" + leak + "[/private]\n").encode("utf-8")) == b""
    # Code is exported byte for byte; markers only apply to documents.
    assert _public_file("tools/a.py", b"x = '[private]'\n") == b"x = '[private]'\n"


def test_public_engine_cannot_silently_omit_a_new_module(tmp_path):
    module = tmp_path / "app/src/kanamemory/routing.py"
    module.parent.mkdir(parents=True)
    module.write_text("# new engine module\n", "utf-8")
    with pytest.raises(ValueError, match="routing.py"):
        validate_engine_sources(tmp_path, [])
    validate_engine_sources(tmp_path, [("app/src/kanamemory/routing.py", module.read_bytes())])


def test_incomplete_allowlist_fails_before_creating_a_partial_export(tmp_path):
    allowlist = tmp_path / "reviewed.txt"
    entries = (ROOT / "tools/public_source_files.txt").read_text("utf-8").splitlines()
    allowlist.write_text("\n".join(line for line in entries if not line.endswith("/kana_routing.py")), "utf-8")
    target = tmp_path / "export"
    with pytest.raises(ValueError, match="kana_routing.py"):
        export_source(target, allowlist=allowlist)
    assert not target.exists()


def test_exported_demo_loads_lessons_queries_words_and_builds_questions(tmp_path):
    clean = tmp_path / "independent"
    export_source(clean)
    subprocess.run([sys.executable, "tools/build_demo_content.py"], cwd=clean, check=True, capture_output=True, text=True)
    environment = dict(os.environ, PYTHONPATH=str(clean / "app/src"))
    environment.pop("KANAMEMORY_TEACHING_PACK", None)
    script = r'''
from pathlib import Path
import json
from kanamemory.content import ContentRepository
from kanamemory.content_pack import validate_installed_pack
from kanamemory.learning_content import learning_items
from kanamemory.learning_questions import build_learning_question
from kanamemory.example_catalogue import bundled_example_pack
from kanamemory.study_path_catalog import build_study_path
from kanamemory.domain import StudyStore
from kanamemory import kana_routing
from tempfile import TemporaryDirectory
with TemporaryDirectory(dir=Path.cwd()) as directory, StudyStore(Path(directory), time_zone='UTC') as store:
    assert kana_routing.active_units(store) == {}
assert validate_installed_pack()['profile'] == 'demo'
with ContentRepository(Path('app/src/kanamemory/resources/content.sqlite3')) as repo:
    kana = repo.kana_list()
    assert len(kana) == 3 and repo.get_kana(kana[0]['id'])
    assert repo.search_words('ai')[0]['id'] == 'demo-word-ai'
    assert repo.get_word('demo-word-ai')['examples'][0]['text'] == 'これは愛です。'
    items = learning_items()
    assert [item['id'] for item in items] == ['grammar:demo:polite_noun']
    assert bundled_example_pack(items)
    for kind in ('grammar_meaning', 'grammar_cloze'):
        question = build_learning_question(items[0]['id'], kind, variant=0)
        assert question['answer'] in question['choices']
    path = build_study_path(kana, items)
    assert path
print('independent demo runtime verified')
'''
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", script], cwd=clean,
                            env=environment, text=True, encoding="utf-8", capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "independent demo runtime verified" in result.stdout


@pytest.mark.skipif(not shutil.which("powershell"), reason="Windows build entry point")
def test_public_build_entry_rejects_an_unexported_project_before_any_build(tmp_path):
    environment = tmp_path / "private-project/environment"
    environment.mkdir(parents=True)
    wrapper = environment / "build_public_app.ps1"
    wrapper.write_bytes((ROOT / "environment/build_public_app.ps1").read_bytes())
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                             "-File", str(wrapper), "-Stage", "create"],
                            text=True, capture_output=True)
    assert result.returncode != 0
    assert "fresh clean-source export" in result.stderr
