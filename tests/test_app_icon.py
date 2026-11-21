"""The launcher icon: app/pyproject.toml names it, every file it implies exists at its exact pixel size, and all
of them are public source files."""
import os
import struct
import tomllib
from pathlib import Path

import pytest

from tools.render_app_icon import SIZES

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
PUBLIC = set((ROOT / "tools/public_source_files.txt").read_text("utf-8").splitlines())


def icon_setting():
    return tomllib.loads((APP / "pyproject.toml").read_text("utf-8"))["tool"]["briefcase"]["app"]["kanamemory"]["icon"]


def png_header(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    width, height, depth, colour = struct.unpack(">IIBB", data[16:26])
    return width, height, depth, colour, data


def test_icon_setting_is_shared_by_both_editions_and_every_implied_file_exists():
    icon = icon_setting()
    assert icon == "icons/kanamemory"
    android = tomllib.loads((APP / "pyproject.toml").read_text("utf-8"))["tool"]["briefcase"]["app"]["kanamemory"]["android"]
    assert "icon" not in android  # one setting for the demo and the personal edition alike
    files = []
    for variant, sizes in SIZES.items():
        for size in sizes:
            path = APP / f"{icon}-{variant}-{size}.png"
            assert path.is_file(), path
            width, height, depth, colour, data = png_header(path)
            assert (width, height, depth, colour) == (size, size, 8, 6), path  # 8-bit RGBA
            assert data[33:41] == b"\x00\x00\x00\x01sRGB", path  # colour space stated right after IHDR
            files.append(path)
    for name in ("source/kanamemory_icon.svg", "source/kanamemory_icon_mono.svg", "README.txt"):
        files.append(APP / "icons" / name)
        assert files[-1].is_file()
    listed = {path.relative_to(ROOT).as_posix() for path in files} | {"tools/render_app_icon.py"}
    assert listed <= PUBLIC, sorted(listed - PUBLIC)
    on_disk = {p.relative_to(ROOT).as_posix() for p in (APP / "icons").rglob("*") if p.is_file()}
    assert on_disk == listed - {"tools/render_app_icon.py"}, sorted(on_disk ^ (listed - {"tools/render_app_icon.py"}))


def test_icon_drawings_are_drawn_not_typeset():
    for name in ("kanamemory_icon.svg", "kanamemory_icon_mono.svg"):
        text = (APP / "icons/source" / name).read_text("utf-8")
        assert "<text" not in text and "font" not in text and "<image" not in text
        assert text.count('stroke-linecap="round"') == 2
    assert 'fill="#E0452F"' in (APP / "icons/source/kanamemory_icon.svg").read_text("utf-8")


def test_build_stage_refreshes_icons_of_an_already_generated_project():
    runner = (ROOT / "environment/run_app.ps1").read_text("utf-8")
    assert "briefcase update android -r --update-resources --no-input --log" in runner
    copy_icons = runner.index("Copy-Item -LiteralPath (Join-Path $appSource 'icons') -Destination $appBuild")
    assert runner.index("Copy-Item -LiteralPath (Join-Path $appSource 'src')") < copy_icons


TEMPLATE_INDEX = (Path(os.environ.get("LOCALAPPDATA", "")) / "km/briefcase/pinned-template"
                  / "briefcase-android-gradle-template/{{ cookiecutter.format }}/briefcase.toml")


@pytest.mark.skipif(not TEMPLATE_INDEX.is_file(), reason="pinned Android template not rolled out on this machine")
def test_icon_sizes_match_the_pinned_template_index():
    paths = tomllib.loads(TEMPLATE_INDEX.read_text("utf-8"))["paths"]
    declared = {variant: tuple(sorted(int(size) for size in sizes)) for variant, sizes in paths["icon"].items()}
    assert declared == {variant: tuple(sorted(sizes)) for variant, sizes in SIZES.items()}
