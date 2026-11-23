"""Write the version stamp of the bundled core content (design 08 section 5.7).

``stamp_bundled_library.py <resources folder> --pack <core pack file>`` checks that the pack is a core library
pack whose teaching database has the same metadata.content_sha256 as the resources' teaching_content.sqlite3,
then writes bundled_library.json beside it with the pack's library_version and version_scheme. Anything else
is refused and nothing is written. The stamp never enters a library pack (package_library_pack.py).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/src"))
from kanamemory import content_library_rules as rules  # noqa: E402
from kanamemory.content_pack import validate_pack  # noqa: E402


def _teaching_sha256(path):
    return validate_pack(path)["content_sha256"]


def stamp(resources, pack):
    """Write <resources>/bundled_library.json for ``pack``; returns the stamp. ValueError when refused."""
    resources, pack = Path(resources), Path(pack)
    teaching = resources / "teaching_content.sqlite3"
    if not teaching.is_file():
        raise ValueError("The resources folder has no teaching_content.sqlite3")
    bundled = _teaching_sha256(teaching)
    report = rules.validate_library_pack(pack)
    manifest = report.manifest
    if manifest["profile"] != "core" or manifest["library_id"] != rules.CORE_LIBRARY_ID:
        raise ValueError("Not a core library pack: " + manifest["library_id"])
    if not report.has_teaching_pack:
        raise ValueError("The core pack carries no teaching database")
    with tempfile.TemporaryDirectory(prefix="kanamemory-stamp-") as folder:
        extracted = Path(folder) / "teaching_content.sqlite3"
        with zipfile.ZipFile(pack) as archive, archive.open(rules.TEACHING_PACK_FILE) as source:
            extracted.write_bytes(source.read())
        packed = _teaching_sha256(extracted)
    if packed != bundled:
        raise ValueError("The pack's teaching database is not the bundled one (content_sha256 differs)")
    value = rules.make_bundled_stamp(manifest["library_version"], manifest["version_scheme"], bundled)
    target = resources / rules.BUNDLED_STAMP_FILE
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=resources, prefix=".stamp-",
                                         suffix=".json", delete=False)
    try:
        with handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resources", type=Path, help="Runtime resources folder (teaching_content.sqlite3)")
    parser.add_argument("--pack", type=Path, required=True, help="The core library pack built from this content")
    args = parser.parse_args()
    try:
        value = stamp(args.resources, args.pack)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print("refused: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(value, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
