"""Local, non-interactive glyph-origin illustrations and explicit inheritance.

This catalog does not infer etymology from Unicode decomposition. The content
database supplies the base IDs; the packaged manifest supplies image provenance.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import re
import struct

from .detail_content import COMBINATION_CATEGORIES
from .presentation import payload


_KANA_ID = re.compile(r"kana-[0-9a-f]{4,6}(?:-[0-9a-f]{4,6})*\Z")
_IMAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.png\Z", re.IGNORECASE)
_INHERITED_SINGLE_CATEGORIES = frozenset({
    "dakuten", "handakuten", "foreign_voiced", "small", "rare_legacy_katakana",
})


def _data(record):
    return {**(payload(record) or {}), **{k: v for k, v in (record or {}).items() if k != "payload"}}


@dataclass(frozen=True)
class OriginImage:
    kana_id: str
    script: str
    han_character: str
    path: Path
    source_ids: tuple[str, ...]
    width: int
    height: int
    reference_path: Path | None = None
    reference_width: int = 0
    reference_height: int = 0
    review_note: str = ""
    source_status: str = ""
    is_teaching_reference: bool = False

    @property
    def label(self):
        if self.is_teaching_reference:
            return "现代教学示意 · 中间笔形待核"
        if self.script == "hiragana" and self.review_note:
            return "平假名 · 字源有争议" if self.source_status == "contested" else "平假名 · 字源对照"
        return "平假名" if self.script == "hiragana" else "片假名"

    def teaching_reference(self):
        if self.reference_path is None:
            return None
        return replace(self, path=self.reference_path, width=self.reference_width,
                       height=self.reference_height, is_teaching_reference=True)


class OriginImageCatalog:
    """Read only same-directory PNG assets; missing artwork has no text fallback."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory).resolve()
        self.records: tuple[OriginImage, ...] = ()
        self.errors: tuple[str, ...] = ()
        manifest = self.directory / "manifest.json"
        if not manifest.is_file():
            return
        try:
            if manifest.resolve().parent != self.directory or manifest.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("manifest is outside the asset directory or too large")
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("records"), list):
                raise ValueError("unsupported glyph-origin manifest")
        except (OSError, ValueError, UnicodeError) as error:
            self.errors = (str(error),)
            return
        records, errors, seen = [], [], set()
        for number, record in enumerate(data["records"]):
            try:
                image = self._read_record(record, errors)
            except (OSError, ValueError, TypeError) as error:
                errors.append(f"record {number}: {error}")
                continue
            identity = (image.kana_id, image.script, image.han_character, image.path)
            if identity not in seen:
                records.append(image)
                seen.add(identity)
        self.records, self.errors = tuple(records), tuple(errors)

    def _read_record(self, record, errors):
        if not isinstance(record, dict):
            raise ValueError("image record must be an object")
        kana_id, script = record.get("kana_id"), record.get("script")
        character, name = record.get("han_character"), record.get("image")
        sources = record.get("source_ids")
        if not isinstance(kana_id, str) or not _KANA_ID.fullmatch(kana_id):
            raise ValueError("invalid kana ID")
        if not isinstance(script, str) or script not in {"hiragana", "katakana"}:
            raise ValueError("invalid script")
        if not isinstance(character, str) or not character.strip():
            raise ValueError("missing source character")
        if not isinstance(name, str) or not _IMAGE_NAME.fullmatch(name):
            raise ValueError("image must be a local PNG basename")
        if not isinstance(sources, list) or not sources or any(not isinstance(s, str) or not s.strip() for s in sources):
            raise ValueError("missing source provenance")
        path, width, height = self._read_png(name)
        reference_path, reference_width, reference_height = None, 0, 0
        if record.get("reference_image"):
            try:
                reference_path, reference_width, reference_height = self._read_png(record["reference_image"])
            except (OSError, ValueError, TypeError) as error:
                # Optional, unverified artwork must not hide the supported
                # source-character comparison or its evidence notes.
                errors.append(f"{kana_id}: teaching reference disabled: {error}")
        review = record.get("review", {})
        if not isinstance(review, dict):
            raise ValueError("invalid origin review")
        note, status = review.get("note_zh", ""), review.get("source_status", "")
        if not isinstance(note, str) or len(note) > 2000 or not isinstance(status, str):
            raise ValueError("invalid origin review text")
        return OriginImage(kana_id, script, character, path, tuple(dict.fromkeys(sources)), width, height,
                           reference_path, reference_width, reference_height, note, status)

    def _read_png(self, name):
        if not isinstance(name, str) or not _IMAGE_NAME.fullmatch(name):
            raise ValueError("image must be a local PNG basename")
        path = (self.directory / name).resolve(strict=True)
        if path.parent != self.directory or not path.is_file():
            raise ValueError("image is outside the asset directory")
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("image is too large")
        with path.open("rb") as stream:
            header = stream.read(24)
        if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[8:16] != b"\x00\x00\x00\rIHDR":
            raise ValueError("image has no PNG dimensions")
        width, height = struct.unpack(">II", header[16:24])
        if not 0 < width <= 8192 or not 0 < height <= 8192:
            raise ValueError("invalid PNG dimensions")
        return path, width, height

    def for_kana(self, record) -> tuple[OriginImage, ...]:
        data = _data(record)
        category = data.get("category")
        if category in COMBINATION_CATEGORIES:
            return ()
        if category in _INHERITED_SINGLE_CATEGORIES:
            base_ids = list((data.get("enrichment") or {}).get("origin_base_kana_ids", ()))
        else:
            base_ids = [data.get("id")]
        # Some future repository versions may materialize inherited origin rows.
        base_ids.extend(_data(origin).get("kana_id") for origin in data.get("origins", ()))
        base_ids = list(dict.fromkeys(base_ids))
        scripts = [script for script in ("hiragana", "katakana") if data.get(script)]
        return tuple(image for script in scripts for base_id in base_ids for image in self.records
                     if image.kana_id == base_id and image.script == script)
