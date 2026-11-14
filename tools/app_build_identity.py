"""Keep the app's displayed build identity, Gradle config and APK in agreement."""
from __future__ import annotations

import argparse
import ast
from datetime import date
import json
from pathlib import Path
import re
import struct
import subprocess
import tomllib
import zipfile


def load_identity(app_source):
    app_source = Path(app_source)
    config = tomllib.loads((app_source / "pyproject.toml").read_text("utf-8"))["tool"]["briefcase"]
    syntax = ast.parse((app_source / "src/kanamemory/build_info.py").read_text("utf-8"))
    fields = {}
    for statement in syntax.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            fields[statement.targets[0].id] = ast.literal_eval(statement.value)
    version, number, build_date = fields.get("VERSION"), fields.get("BUILD_NUMBER"), fields.get("BUILD_DATE")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:[A-Za-z0-9.+-]*)", version):
        raise ValueError("Invalid app version")
    if config["version"] != version:
        raise ValueError("pyproject.toml and build_info.VERSION disagree")
    if type(number) is not int or not 1 <= number <= 2_100_000_000:
        raise ValueError("Invalid Android build number")
    if not isinstance(build_date, str) or date.fromisoformat(build_date).isoformat() != build_date:
        raise ValueError("Invalid build date")
    name = config["app"]["kanamemory"]["formal_name"]
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9 _-]+", name):
        raise ValueError("Unsafe package filename")
    return {"version": version, "build_number": number, "build_date": build_date,
            "package_id": config["bundle"] + ".kanamemory", "apk_name": f"{name}-{version}.debug.apk"}


def sync_gradle(path, identity):
    """Update only the two generated version declarations, or leave it intact."""
    path = Path(path)
    original = path.read_text("utf-8")
    changes = ((r'(?m)^(\s*versionCode\s+)\d+\s*$', str(identity["build_number"])),
               (r'(?m)^(\s*versionName\s+)["\'][^"\'\r\n]+["\']\s*$', '"' + identity["version"] + '"'))
    result = original
    for pattern, value in changes:
        result, count = re.subn(pattern, lambda match: match[1] + value, result)
        if count != 1:
            raise ValueError("Expected one generated Gradle version declaration")
    if result != original:
        path.write_text(result, encoding="utf-8", newline="\n")


def validate_badging(text, identity):
    package = next((line for line in text.splitlines() if line.startswith("package: ")), "")
    # aapt also emits compileSdkVersionCodename; its trailing "name" must
    # never replace the application's name field in the parsed dictionary.
    pairs = re.findall(r"(?:^|\s)(name|versionCode|versionName)='([^']*)'", package)
    fields = dict(pairs)
    if len(pairs) != 3:
        raise ValueError("APK package identity fields are missing or duplicated")
    if fields != {"name": identity["package_id"], "versionCode": str(identity["build_number"]), "versionName": identity["version"]}:
        raise ValueError("APK version or application ID is stale; rebuild before packaging/installing")


def dex_defined_classes(data):
    """Descriptors (Lorg/example/Main;) of the classes one dex file DEFINES: its class_defs, not every type it names."""
    if data[:4] != b"dex\n" or len(data) < 0x70:
        raise ValueError("Not a dex file")
    string_ids, type_ids = struct.unpack_from("<I", data, 0x3C)[0], struct.unpack_from("<I", data, 0x44)[0]
    class_count, class_defs = struct.unpack_from("<II", data, 0x60)
    found = set()
    for index in range(class_count):
        class_index = struct.unpack_from("<I", data, class_defs + 32 * index)[0]
        string_index = struct.unpack_from("<I", data, type_ids + 4 * class_index)[0]
        offset = struct.unpack_from("<I", data, string_ids + 4 * string_index)[0]
        while data[offset] & 0x80:  # the uleb128 length before the text
            offset += 1
        found.add(data[offset + 1:data.index(b"\0", offset + 1)].decode("utf-8", errors="replace"))
    return found


def validate_launcher(path, badging):
    """The activity the APK launches must be a class the APK holds.

    A generated project that has lost its Java sources still builds: Gradle compiles what is left, the manifest
    still names the activity, and the app dies at its first start (seen 2026-11-14, after a clean-up of temporary
    files had removed sources from a toolchain kept in the temporary directory).
    """
    match = re.search(r"(?m)^launchable-activity: name='([^']+)'", badging)
    if match is None:
        raise ValueError("APK declares no launchable activity")
    descriptor = "L" + match[1].replace(".", "/") + ";"
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if re.fullmatch(r"classes\d*\.dex", name) and descriptor in dex_defined_classes(archive.read(name)):
                return
    raise ValueError(f"APK does not contain its launcher class {match[1]}: the generated Android project is "
                     "incomplete; re-create it")


def verify_apk(path, aapt, identity):
    result = subprocess.run([str(aapt), "dump", "badging", str(path)], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=60, check=True)
    validate_badging(result.stdout, identity)
    validate_launcher(path, result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("read", "sync-gradle", "verify-apk"))
    parser.add_argument("app_source", type=Path)
    parser.add_argument("--gradle", type=Path)
    parser.add_argument("--apk", type=Path)
    parser.add_argument("--aapt", type=Path)
    args = parser.parse_args()
    identity = load_identity(args.app_source)
    if args.action == "sync-gradle":
        if not args.gradle:
            parser.error("--gradle is required")
        sync_gradle(args.gradle, identity)
    elif args.action == "verify-apk":
        if not args.apk or not args.aapt:
            parser.error("--apk and --aapt are required")
        verify_apk(args.apk, args.aapt, identity)
    print(json.dumps(identity))


if __name__ == "__main__":
    main()
