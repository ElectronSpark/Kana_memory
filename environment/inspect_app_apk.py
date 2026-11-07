"""Read-only application APK audit, reusing inspect_apk's recursive ELF checks.

Usage: python inspect_app_apk.py APK SDK_BUILD_TOOLS JAVA_HOME
Writes app_apk_inspection.json only; never changes the APK or probe reports.
"""
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import importlib.util
import marshal
import re
import sqlite3
import struct
import subprocess
import sys
import types
import zipfile

from inspect_apk import visit_archive


def run(command):
    result = subprocess.run([str(x) for x in command], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    return {"command": [str(x) for x in command], "exit_code": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def dex_definitions(data):
    """Resolve class definitions (not just string references) from standard DEX."""
    if not data.startswith(b"dex\n"):
        raise ValueError("Unexpected DEX format")
    string_count, string_offset, type_count, type_offset = struct.unpack_from("<4I", data, 56)
    strings = []
    for index in range(string_count):
        offset = struct.unpack_from("<I", data, string_offset + 4 * index)[0]
        while data[offset] & 0x80:
            offset += 1
        offset += 1  # skip modified-UTF8's ULEB128 character length
        strings.append(data[offset:data.index(b"\0", offset)].decode("utf-8", errors="replace"))
    types = [strings[struct.unpack_from("<I", data, type_offset + 4 * i)[0]] for i in range(type_count)]
    count, offset = struct.unpack_from("<II", data, 96)
    defined = {types[struct.unpack_from("<I", data, offset + i * 32)[0]] for i in range(count)}
    method_count, method_offset = struct.unpack_from("<II", data, 88)
    methods = {}
    for index in range(method_count):
        class_index, _prototype, name_index = struct.unpack_from("<HHI", data, method_offset + 8 * index)
        descriptor = types[class_index]
        if descriptor in defined and descriptor.startswith("Lorg/example/kanamemory/"):
            methods.setdefault(descriptor, set()).add(strings[name_index])
    return defined, {key: sorted(value) for key, value in methods.items()}


def canonical_code(value):
    """Stable compiler-output fingerprint, independent of marshal interning."""
    if isinstance(value, types.CodeType):
        names = ("co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals",
                 "co_stacksize", "co_flags", "co_code", "co_consts", "co_names", "co_varnames",
                 "co_filename", "co_name", "co_qualname", "co_firstlineno", "co_linetable",
                 "co_exceptiontable", "co_freevars", "co_cellvars")
        return {name: canonical_code(getattr(value, name)) for name in names}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, tuple):
        return {"tuple": [canonical_code(item) for item in value]}
    if isinstance(value, frozenset):
        items = [canonical_code(item) for item in value]
        return {"frozenset": sorted(items, key=lambda item: json.dumps(item, sort_keys=True))}
    if isinstance(value, (str, bool, int, float, complex)) or value is None or value is Ellipsis:
        return {"type": type(value).__name__, "value": repr(value)}
    raise TypeError(f"Unexpected compiler constant: {type(value)}")


def code_hash(code):
    return sha256(json.dumps(canonical_code(code), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def inspect_python_module(member, data, source_root):
    source = source_root / Path(member).with_suffix(".py")
    result = {"member": "assets/chaquopy/app.imy!" + member, "packaged_pyc_sha256": sha256(data).hexdigest(),
              "workspace_source": str(source), "workspace_source_exists": source.is_file(),
              "comparison_method": "Recompile workspace source and compare canonical code-object hashes, including nested code and line tables. APK contains bytecode, not raw source; comments and source-byte equality are not certified.",
              "bytecode_magic_matches_inspector": data[:4] == importlib.util.MAGIC_NUMBER}
    if not source.is_file():
        result.update(workspace_source_sha256=None, compiled_code_matches_workspace=False,
                      error="Packaged Python module has no corresponding workspace source")
        return result
    raw = source.read_bytes()
    result["workspace_source_sha256"] = sha256(raw).hexdigest()
    if not result["bytecode_magic_matches_inspector"]:
        result["compiled_code_matches_workspace"] = None
        return result
    packaged = marshal.loads(data[16:])  # deserialize only; do not execute APK code
    result["packaged_canonical_code_sha256"] = code_hash(packaged)
    result["workspace_compile_variants"] = []
    for optimization in (0, 1, 2):
        compiled = compile(raw, packaged.co_filename, "exec", dont_inherit=True, optimize=optimization)
        fingerprint = code_hash(compiled)
        result["workspace_compile_variants"].append({"optimization": optimization, "canonical_code_sha256": fingerprint,
            "matches_packaged_code": fingerprint == result["packaged_canonical_code_sha256"]})
    result["compiled_code_matches_workspace"] = any(item["matches_packaged_code"] for item in result["workspace_compile_variants"])
    return result


def back_manifest_attributes(xmltree):
    """Keep explicit app/activity overrides distinct from an omitted default."""
    stack, result = [], []
    for line in xmltree.splitlines():
        indent = len(line) - len(line.lstrip())
        element = re.match(r"\s*E: (\S+)", line)
        if element:
            while stack and stack[-1][0] >= indent:
                stack.pop()
            stack.append((indent, element[1]))
        if "android:enableOnBackInvokedCallback(" in line:
            match = re.search(r"=\(type 0x12\)0x([0-9a-fA-F]+)", line)
            result.append({"scope": "/".join(item[1] for item in stack), "raw": line.strip(),
                           "value": bool(int(match[1], 16)) if match else "unresolved"})
    return result


def inspect_origin_diagrams(app, resources):
    """Inspect primary and optional teaching PNGs inside the packaged app archive."""
    checks = []
    manifest_member = "kanamemory/resources/glyph_origins/manifest.json"
    members = set(app.namelist())
    if manifest_member not in members:
        return checks, None
    raw_manifest = app.read(manifest_member)
    manifest_matches = raw_manifest == (resources / "glyph_origins/manifest.json").read_bytes()
    for diagram in json.loads(raw_manifest)["records"]:
        variants = [("primary", "image", "sha256", "width", "height")]
        reference_fields = ("reference_image", "reference_sha256", "reference_width", "reference_height")
        if any(key in diagram for key in reference_fields):
            if not all(key in diagram for key in reference_fields):
                raise ValueError("Incomplete packaged origin teaching reference metadata")
            variants.append(("teaching_reference", *reference_fields))
        for role, image_key, hash_key, width_key, height_key in variants:
            filename = diagram[image_key]
            if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.png", filename):
                raise ValueError("Unsafe packaged origin diagram name")
            member = "kanamemory/resources/glyph_origins/" + filename
            present = member in members
            data = app.read(member) if present else b""
            valid_png = len(data) >= 24 and data[:16] == b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            dimensions = list(struct.unpack(">II", data[16:24])) if valid_png else None
            expected_dimensions = [diagram[width_key], diagram[height_key]]
            checks.append({"member":member, "kana_id":diagram["kana_id"], "script":diagram["script"],
                "role":role, "present":present,
                "matches_manifest_hash":sha256(data).hexdigest() == diagram[hash_key],
                "matches_workspace_resource":data == (resources / "glyph_origins" / filename).read_bytes(),
                "valid_png_header":valid_png, "bytes":len(data), "dimensions":dimensions,
                "expected_dimensions":expected_dimensions,
                "matches_manifest_dimensions":dimensions == expected_dimensions})
    return checks, manifest_matches


def main():
    apk, build_tools, java_home = map(Path, sys.argv[1:4])
    before = apk.read_bytes()
    digest = sha256(before).hexdigest()
    badging = run([build_tools / "aapt.exe", "dump", "badging", apk])
    manifest = run([build_tools / "aapt.exe", "dump", "xmltree", apk, "AndroidManifest.xml"])
    alignment = run([build_tools / "zipalign.exe", "-c", "-P", "16", "-v", "4", apk])
    signature = run([java_home / "bin" / "java.exe", "-jar", build_tools / "lib" / "apksigner.jar", "verify", "--verbose", "--print-certs", apk])
    with zipfile.ZipFile(BytesIO(before)) as archive:
        native = visit_archive(archive)
        abis = sorted({name.split("/")[1] for name in archive.namelist() if name.startswith("lib/") and name.endswith(".so")})
        backup_rule_files = [name for name in archive.namelist() if name.startswith("res/xml") and name.endswith(".xml") and ("backup" in name or "extraction" in name)]
        activity_version_member = "META-INF/androidx.activity_activity.version"
        activity_version = archive.read(activity_version_member).decode("utf-8").strip() if activity_version_member in archive.namelist() else None
        definitions, method_map = set(), {}
        for name in archive.namelist():
            if re.fullmatch(r"classes\d*\.dex", name):
                classes, methods = dex_definitions(archive.read(name))
                definitions |= classes
                method_map.update(methods)
        with zipfile.ZipFile(BytesIO(archive.read("assets/chaquopy/app.imy"))) as app:
            embedded = {}
            resources = Path(__file__).resolve().parents[1] / "app" / "src" / "kanamemory" / "resources"
            for filename in ("content.sqlite3", "teaching_content.sqlite3", "order_translations.sqlite3", "natural_translations.sqlite3", "NOTICE.txt"):
                member = f"kanamemory/resources/{filename}"
                data = app.read(member)
                embedded[filename] = {"member": "assets/chaquopy/app.imy!" + member,
                    "bytes": len(data), "sha256": sha256(data).hexdigest(),
                    "matches_workspace_resource": sha256(data).digest() == sha256((resources / filename).read_bytes()).digest()}
                if filename == "content.sqlite3":
                    with sqlite3.connect(":memory:") as db:
                        db.deserialize(data)
                        db.execute("PRAGMA query_only=ON")
                        embedded[filename]["integrity_check"] = db.execute("PRAGMA integrity_check").fetchone()[0]
                        embedded[filename]["counts"] = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                            for table in ("kana", "word", "example", "source", "origin", "dialect")}
                        embedded[filename]["content_version"] = json.loads(db.execute("SELECT value FROM metadata WHERE key='content_version'").fetchone()[0])
                elif filename == "teaching_content.sqlite3":
                    with sqlite3.connect(":memory:") as db:
                        db.deserialize(data)
                        db.execute("PRAGMA query_only=ON")
                        embedded[filename]["integrity_check"] = db.execute("PRAGMA integrity_check").fetchone()[0]
                        embedded[filename]["metadata"] = dict(db.execute("SELECT key,value FROM metadata"))
                        embedded[filename]["namespaces"] = [row[0] for row in db.execute("SELECT name FROM namespace ORDER BY name")]
                elif filename in {"order_translations.sqlite3", "natural_translations.sqlite3"}:
                    with sqlite3.connect(":memory:") as db:
                        db.deserialize(data)
                        db.execute("PRAGMA query_only=ON")
                        embedded[filename]["integrity_check"] = db.execute("PRAGMA integrity_check").fetchone()[0]
                        embedded[filename]["metadata"] = dict(db.execute("SELECT key,value FROM metadata"))
                        embedded[filename]["counts"] = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                            for table in ("translation", "pending")}
                else:
                    embedded[filename]["license_mentions"] = {term: term in data.decode("utf-8")
                        for term in ("CC BY-SA 4.0", "CC BY 2.0 FR", "Unicode", "JMdict", "Wiktionary", "Tatoeba")}
            python_modules = [name for name in app.namelist() if name.endswith(".pyc") and name.startswith("kanamemory/")]
            media_checks = []
            for path in sorted((resources / "media").glob("*")):
                member = "kanamemory/resources/media/" + path.name
                exists = member in app.namelist()
                data = app.read(member) if exists else b""
                media_checks.append({"member":member, "present":exists, "bytes":len(data),
                    "sha256":sha256(data).hexdigest() if exists else None,
                    "matches_workspace_resource":exists and data == path.read_bytes()})
            source_bundle_checks = []
            for path in sorted((resources / "source_bundles").glob("*.zip")):
                member = "kanamemory/resources/source_bundles/" + path.name
                exists = member in app.namelist()
                data = app.read(member) if exists else b""
                source_bundle_checks.append({"member":member, "present":exists, "bytes":len(data),
                    "sha256":sha256(data).hexdigest() if exists else None,
                    "matches_workspace_resource":exists and data == path.read_bytes()})
            resource_members = sorted(i.filename for i in app.infolist() if not i.is_dir() and i.filename.startswith("kanamemory/resources/"))
            diagram_checks, diagram_manifest_matches = inspect_origin_diagrams(app, resources)
            expected_resources = sorted("kanamemory/resources/" + p.relative_to(resources).as_posix() for p in resources.rglob("*") if p.is_file())
            resource_inventory = {"expected":expected_resources, "packaged":resource_members,
                "missing":sorted(set(expected_resources)-set(resource_members)),
                "unexpected":sorted(set(resource_members)-set(expected_resources))}
            source_root = resources.parent.parent
            expected_modules = sorted(path.relative_to(source_root).with_suffix(".pyc").as_posix()
                                      for path in (source_root / "kanamemory").rglob("*.py"))
            python_module_inventory = {"expected_from_workspace": expected_modules,
                "packaged": sorted(python_modules),
                "missing_from_apk": sorted(set(expected_modules) - set(python_modules)),
                "unexpected_in_apk": sorted(set(python_modules) - set(expected_modules))}
            python_module_checks = [inspect_python_module(name, app.read(name), resources.parent.parent) for name in python_modules]
        chaquopy_build = json.loads(archive.read("assets/chaquopy/build.json"))
    bridge_names = ["Lorg/example/kanamemory/KanaTtsListener;", "Lorg/example/kanamemory/KanaTtsListener$Events;"]
    back_bridge_names = ["Lorg/example/kanamemory/KanaBackHandler;", "Lorg/example/kanamemory/KanaBackHandler$Handler;"]
    kana_link_bridge_names = ["Lorg/example/kanamemory/KanaLinkSpan;", "Lorg/example/kanamemory/KanaLinkSpan$Handler;"]
    required_bridges = {bridge_names[0]: ["<init>", "onStart", "onDone", "onError", "onStop"],
                        bridge_names[1]: ["onStart", "onDone", "onError", "onStop"],
                        back_bridge_names[0]: ["<init>", "handleOnBackPressed", "setPageBackEnabled", "onStateChanged", "unregister"],
                        back_bridge_names[1]: ["onBack"],
                        kana_link_bridge_names[0]: ["<init>", "onClick"],
                        kana_link_bridge_names[1]: ["openKana"],
                        "Lorg/example/kanamemory/KanaAISecrets;": ["put", "get", "remove", "prepareImport", "completeImport"],
                        "Lorg/example/kanamemory/KanaBackupDocuments;": ["<init>", "exportJson", "exportZipBase64", "importJson", "cancel"],
                        "Lorg/example/kanamemory/KanaSelectionAdapter;": ["<init>", "getView", "getDropDownView", "setContentWidth"],
                        "Lorg/example/kanamemory/KanaSelectionAdapter$DropDownLabel;": ["onMeasure", "setHorizontallyScrolling"]}
    bridge_checks = {name: {"defined_in_dex": name in definitions, "method_names": method_map.get(name, []),
                           "missing_required_methods": sorted(set(methods) - set(method_map.get(name, [])))}
                     for name, methods in required_bridges.items()}
    badge = badging["stdout"]
    permissions = re.findall(r"uses-permission: name='([^']+)'", badge)
    summary = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Read-only static inspection of the real learning application APK; no emulator invoked.",
        "apk": str(apk.resolve()), "apk_size_bytes": len(before), "apk_sha256": digest,
        "apk_unchanged_during_inspection": True,
        "manifest": {"package_badging": badge.splitlines()[0],
            "abis": abis, "dual_abi_check_passed": abis == ["arm64-v8a", "x86_64"],
            "min_sdk": int(re.search(r"(?:minS|s)dkVersion:'(\d+)'", badge)[1]),
            "target_sdk": int(re.search(r"targetSdkVersion:'(\d+)'", badge)[1]),
            "compile_sdk": int(re.search(r"compileSdkVersion='(\d+)'", badge)[1]),
            "permissions": permissions,
            "has_microphone_permission": "android.permission.RECORD_AUDIO" in permissions,
            "has_storage_permissions": any("STORAGE" in p or "MEDIA_" in p for p in permissions),
            "tts_service_query_present": 'android.intent.action.TTS_SERVICE' in manifest["stdout"],
            "enable_on_back_invoked_callback": {
                "explicit_attributes": back_manifest_attributes(manifest["stdout"]),
                "declaration_status": "explicit" if "android:enableOnBackInvokedCallback(" in manifest["stdout"] else "not_set_platform_default",
                "note": "An omitted attribute is the platform default, not an explicit false; Activity overrides and target/runtime SDK also matter."},
            "androidx_activity_version": activity_version,
            "debuggable": "application-debuggable" in badge,
            "allows_android_backup": 'android:allowBackup(0x01010280)=(type 0x12)0xffffffff' in manifest["stdout"],
            "allow_backup_attribute": next((line.strip() for line in manifest["stdout"].splitlines() if "android:allowBackup(" in line), None),
            "data_extraction_rules_attribute": next((line.strip() for line in manifest["stdout"].splitlines() if "android:dataExtractionRules(" in line), None),
            "full_backup_content_attribute": next((line.strip() for line in manifest["stdout"].splitlines() if "android:fullBackupContent(" in line), None),
            "extract_native_libs_attribute": next((line.strip() for line in manifest["stdout"].splitlines() if "android:extractNativeLibs(" in line), None),
            "backup_rule_xml": {name: run([build_tools / "aapt.exe", "dump", "xmltree", apk, name]) for name in backup_rule_files}},
        "native_library_count": len(native),
        "load_alignment_check_passed": bool(native) and all(i.get("load_segments_16kb_aligned") for i in native),
        "relro_end_warning_count": sum(i.get("relro_end_16kb_aligned") is False for i in native),
        "elf_header_checks_passed": bool(native) and all(i.get("load_segments_16kb_aligned") and i.get("relro_end_16kb_aligned") for i in native),
        "zipalign_16kb_passed": alignment["exit_code"] == 0,
        "signature_verified": signature["exit_code"] == 0,
        "embedded_resources": embedded, "embedded_python_modules": python_modules,
        "embedded_recordings":media_checks,
        "embedded_audio_source_bundles":source_bundle_checks,
        "all_audio_source_bundles_match_workspace":all(r["matches_workspace_resource"] for r in source_bundle_checks),
        "embedded_origin_diagrams":diagram_checks,
        "origin_diagram_primary_count":sum(d["role"] == "primary" for d in diagram_checks),
        "origin_diagram_reference_count":sum(d["role"] == "teaching_reference" for d in diagram_checks),
        "origin_diagram_manifest_matches_workspace":diagram_manifest_matches,
        "all_origin_diagrams_match_workspace":bool(diagram_checks) and diagram_manifest_matches and all(
            d["matches_manifest_hash"] and d["matches_workspace_resource"] and d["valid_png_header"]
            and d["matches_manifest_dimensions"] for d in diagram_checks),
        "resource_inventory":resource_inventory,
        "resource_inventory_matches_workspace":not resource_inventory["missing"] and not resource_inventory["unexpected"],
        "all_recordings_match_workspace":bool(media_checks) and all(r["matches_workspace_resource"] for r in media_checks),
        "python_module_checks": python_module_checks,
        "python_module_inventory": python_module_inventory,
        "python_module_inventory_matches_workspace": not python_module_inventory["missing_from_apk"] and not python_module_inventory["unexpected_in_apk"],
        "all_python_modules_match_workspace_compilation": bool(python_module_checks) and all(item["compiled_code_matches_workspace"] is True for item in python_module_checks) and not python_module_inventory["missing_from_apk"] and not python_module_inventory["unexpected_in_apk"],
        "java_tts_bridge": {name: {"defined_in_dex": name in definitions, "method_names": method_map.get(name, [])} for name in bridge_names},
        "java_back_bridge": {name: bridge_checks[name] for name in back_bridge_names},
        "java_kana_link_bridge": {name: bridge_checks[name] for name in kana_link_bridge_names},
        "java_app_class_definitions": {name: {"method_names": method_map.get(name, [])} for name in sorted(definitions) if name.startswith("Lorg/example/kanamemory/")},
        "java_required_bridge_checks": bridge_checks,
        "all_required_java_bridges_present": all(item["defined_in_dex"] and not item["missing_required_methods"] for item in bridge_checks.values()),
        "chaquopy_build": chaquopy_build,
        "runtime_validation": {"performed_by_this_inspection": False,
            "4kb_emulator": "Separate runtime validation by the Android/UI agents; this static inspection does not certify it.",
            "16kb_device_or_emulator": "Not run; full 16 KB runtime compatibility remains unconfirmed."},
        "limitations": ["ZIP alignment, ELF LOAD alignment and GNU_RELRO end alignment are distinct checks.",
            "GNU_RELRO warnings require investigation; a static warning alone does not prove device launch failure.",
            "This debug APK is not a release-signed distribution build.",
            "Presence of Java methods or a TTS query does not verify voice availability, callbacks or ML Kit recognition on a device."],
        "inspection_reference": "https://developer.android.com/guide/practices/page-sizes",
        "sdk_tools": {"badging": badging, "manifest": manifest,
            "zipalign": {**alignment, "stdout": alignment["stdout"].splitlines()[-3:]}, "signature": signature},
        "libraries": native,
    }
    if sha256(apk.read_bytes()).hexdigest() != digest:
        raise RuntimeError("APK changed during inspection; retry after build completes")
    summary["workspace_sources_unchanged_during_inspection"] = all(
        Path(item["workspace_source"]).is_file() and sha256(Path(item["workspace_source"]).read_bytes()).hexdigest() == item["workspace_source_sha256"]
        for item in python_module_checks)
    output = Path(__file__).with_name("app_apk_inspection.json")
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("apk_sha256", "apk_size_bytes", "manifest", "native_library_count", "load_alignment_check_passed", "relro_end_warning_count", "zipalign_16kb_passed", "signature_verified", "embedded_resources", "java_tts_bridge")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
