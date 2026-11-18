"""Keep the app's displayed build identity, Gradle config and APK in agreement.

A build identity file (an identity given as data, kept outside the repository) names the package that a build of
a clean public tree produces instead of the demo:

    {"format": "kanamemory-build-identity-v1", "profile": "personal",
     "bundle": "org.example.yourname", "formal_name": "YourAppName"}

The package id is bundle + ".kanamemory"; formal_name gives the APK file name and the launcher label. It is never
written into the repository: environment/run_app.ps1 applies it (``apply``) to the build copy of
app/pyproject.toml in the toolchain, as the export applies the demo identity, and every later check reads the
identity through the same file (``--identity-file``).
"""
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

IDENTITY_FORMAT = "kanamemory-build-identity-v1"
IDENTITY_KEYS = frozenset({"format", "profile", "bundle", "formal_name"})
DEMO_PACKAGE = "org.example.demo.kanamemory"
QA_PACKAGE = "org.example.qa.kanamemory"
_BUNDLE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")
_FORMAL_NAME = re.compile(r"[A-Za-z0-9 _-]+")


def read_identity_file(path):
    """The checked contents of a build identity file: {"bundle", "formal_name"}.

    Refused: anything but a JSON object with exactly the four keys, another format or profile, a bundle that is
    not a lower-case reverse domain name, an unsafe formal name, and the package ids of the demo and of QA (those
    have their own build paths)."""
    try:
        value = json.loads(Path(path).read_text("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Build identity file is unreadable: {error}") from None
    if not isinstance(value, dict) or set(value) != IDENTITY_KEYS:
        raise ValueError("Build identity file must hold exactly: " + ", ".join(sorted(IDENTITY_KEYS)))
    if value["format"] != IDENTITY_FORMAT or value["profile"] != "personal":
        raise ValueError(f'Build identity file needs format "{IDENTITY_FORMAT}" and profile "personal"')
    bundle, name = value["bundle"], value["formal_name"]
    if not isinstance(bundle, str) or len(bundle) > 200 or not _BUNDLE.fullmatch(bundle):
        raise ValueError("Build identity bundle must be a lower-case reverse domain name such as org.example")
    if not isinstance(name, str) or len(name) > 60 or not _FORMAL_NAME.fullmatch(name):
        raise ValueError("Build identity formal_name may hold only letters, digits, space, _ and -")
    if bundle + ".kanamemory" in (DEMO_PACKAGE, QA_PACKAGE):
        raise ValueError("Build identity file must not name the demo or QA package")
    return {"bundle": bundle, "formal_name": name}


def applied_pyproject(text, identity):
    """The pyproject text with the identity's bundle and formal name (exactly one declaration of each)."""
    for key, value in (("bundle", identity["bundle"]), ("formal_name", identity["formal_name"])):
        text, count = re.subn(rf"(?m)^{key}\s*=.*$", lambda _match: f'{key} = "{value}"', text)
        if count != 1:
            raise ValueError(f"Expected one Briefcase {key} declaration")
    return text


def apply_identity(app_build, identity_file):
    """Write the identity into the BUILD COPY app_build/pyproject.toml, never into a source tree."""
    app_build = Path(app_build)
    if any((app_build.parent / marker).exists() for marker in (".git", "clean-source-manifest.json")):
        raise ValueError("The identity is applied to the build copy in the toolchain, never to a source tree")
    path = app_build / "pyproject.toml"
    original = path.read_bytes().decode("utf-8")
    result = applied_pyproject(original, read_identity_file(identity_file))
    if result != original:
        path.write_bytes(result.encode("utf-8"))


def load_identity(app_source, identity_file=None):
    app_source = Path(app_source)
    text = (app_source / "pyproject.toml").read_text("utf-8")
    if identity_file is not None:
        text = applied_pyproject(text, read_identity_file(identity_file))
    config = tomllib.loads(text)["tool"]["briefcase"]
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
    parser.add_argument("action", choices=("read", "sync-gradle", "verify-apk", "apply"))
    parser.add_argument("app_source", type=Path)
    parser.add_argument("--gradle", type=Path)
    parser.add_argument("--apk", type=Path)
    parser.add_argument("--aapt", type=Path)
    parser.add_argument("--identity-file", type=Path,
                        help="a build identity file (see the module text); apply writes it into the build copy")
    args = parser.parse_args()
    if args.action == "apply":
        if not args.identity_file:
            parser.error("--identity-file is required")
        apply_identity(args.app_source, args.identity_file)
    identity = load_identity(args.app_source, args.identity_file)
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
