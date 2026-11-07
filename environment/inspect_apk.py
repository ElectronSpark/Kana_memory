"""Inspect packaged ELF headers, including native modules inside Chaquopy ZIPs.

This complements SDK zipalign; it is not a replacement for a 16 KB device test.
"""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import struct
import sys
import zipfile


def inspect_elf(name: str, data: bytes) -> dict:
    if not data.startswith(b"\x7fELF") or data[4] != 2 or data[5] != 1:
        return {"name": name, "error": "not little-endian ELF64"}
    phoff = struct.unpack_from("<Q", data, 32)[0]
    entsize, count = struct.unpack_from("<HH", data, 54)
    load_alignments = []
    relro_aligned = []
    for i in range(count):
        p_type, _flags, _offset, vaddr, _paddr, _filesz, memsz, alignment = struct.unpack_from(
            "<IIQQQQQQ", data, phoff + entsize * i
        )
        if p_type == 1:
            load_alignments.append(alignment)
        elif p_type == 0x6474E552:
            relro_aligned.append((vaddr + memsz) % 16384 == 0)
    return {
        "name": name,
        "sha256": sha256(data).hexdigest(),
        "load_alignments": load_alignments,
        "load_segments_16kb_aligned": bool(load_alignments) and min(load_alignments) >= 16384,
        "relro_end_16kb_aligned": all(relro_aligned),
    }


def visit_archive(archive: zipfile.ZipFile, prefix: str = "", depth: int = 0) -> list[dict]:
    results = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = prefix + info.filename
        if info.filename.endswith(".so"):
            results.append(inspect_elf(name, archive.read(info)))
        elif depth < 2 and info.filename.endswith((".imy", ".zip")):
            data = archive.read(info)
            if data.startswith(b"PK"):
                with zipfile.ZipFile(BytesIO(data)) as nested:
                    results.extend(visit_archive(nested, name + "!", depth + 1))
    return results


def main() -> None:
    apk = Path(sys.argv[1])
    with zipfile.ZipFile(apk) as archive:
        native = visit_archive(archive)
    report = {
        "apk": str(apk.resolve()),
        "apk_size_bytes": apk.stat().st_size,
        "apk_sha256": sha256(apk.read_bytes()).hexdigest(),
        "native_library_count": len(native),
        "load_alignment_check_passed": bool(native) and all(
            item.get("load_segments_16kb_aligned") for item in native
        ),
        "relro_end_warning_count": sum(
            item.get("relro_end_16kb_aligned") is False for item in native
        ),
        "elf_header_checks_passed": bool(native) and all(
            item.get("load_segments_16kb_aligned") and item.get("relro_end_16kb_aligned") for item in native
        ),
        "libraries": native,
        "android_runtime_tested": False,
        "limitation": "Static inspection only. LOAD alignment and GNU_RELRO end alignment are reported separately. RELRO warnings require investigation; they alone do not establish that a device launch failed. Run SDK zipalign separately and test on a 16 KB device before claiming runtime compatibility.",
        "inspection_reference": "https://developer.android.com/guide/practices/page-sizes",
    }
    output = Path(__file__).with_name("apk_inspection.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "libraries"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
