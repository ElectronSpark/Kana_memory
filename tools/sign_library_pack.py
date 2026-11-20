"""Sign or verify a kanamemory-library-pack v1 ZIP with Ed25519 (design 08 §4.5).

The signature lives in manifest.json as ``signature: {algorithm, public_key_id,
public_key, signature}`` and covers the canonical JSON of the manifest without
that field (which in turn lists every file's sha256 and content_sha256). The
implementation is the App's own pure-Python RFC 8032 code
(kanamemory.content_library_signing), so the tool needs no third-party crypto.

Key files are local files you name on the command line: 64 hex digits of an
Ed25519 private seed. No key is built in, read from a default location or
written into the repository; ``generate-key`` refuses paths inside it.

  python tools/sign_library_pack.py generate-key <new key file>
  python tools/sign_library_pack.py public-key <key file>
  python tools/sign_library_pack.py sign <pack.kmlib> <signed.kmlib | directory> --key <key file> [--key-id ID]
  python tools/sign_library_pack.py verify <pack.kmlib> [--trusted ID=HEXKEY ...]

A pack is read by its content, whatever its name (.kmlib, or an older .zip). The signed copy goes to the
file named (kept as given, .zip too) or, given a directory, to <directory>/<pack name without suffix>.kmlib.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "src"))
from kanamemory import content_library_rules as rules  # noqa: E402
from kanamemory import content_library_signing as signing  # noqa: E402
from kanamemory.library_file_open import EXTENSION as PACK_SUFFIX  # noqa: E402

FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def encoded_manifest(manifest):
    """Manifest bytes as the packaging tool writes them (compact UTF-8 JSON)."""
    return json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def read_seed(key_file):
    """The private seed from a local key file (64 hex digits)."""
    return signing.parse_seed(Path(key_file).read_text(encoding="ascii"))


def _inside_repository(path):
    try:
        Path(path).resolve().relative_to(ROOT)
    except ValueError:
        return False
    return True


def generate_key(key_file):
    """Write a new random seed to key_file (a new file outside the repository)."""
    path = Path(key_file)
    if _inside_repository(path):
        raise ValueError("Keep signing keys outside the repository")
    seed = os.urandom(32)
    with open(path, "x", encoding="ascii") as stream:
        stream.write(seed.hex() + "\n")
    return describe_key(seed)


def describe_key(seed):
    public = signing.public_key_from_seed(seed).hex()
    fingerprint = signing.key_fingerprint(public)
    return {"public_key": public, "public_key_id": signing.default_key_id(public), "fingerprint": fingerprint,
            "display_fingerprint": signing.display_fingerprint(fingerprint)}


def sign_pack(source, output, seed, *, public_key_id=None):
    """Write a signed copy of a validated pack; other entries are copied byte for byte."""
    source, output = Path(source), Path(output)
    if output.is_dir():
        output = output / (source.stem + PACK_SUFFIX)
    if output.exists() or output.resolve() == source.resolve():
        raise ValueError("Choose a new output file")
    rules.validate_library_pack(source)
    with zipfile.ZipFile(source) as archive:
        manifest = json.loads(archive.read(rules.MANIFEST_FILE).decode("utf-8"))
        signed = signing.sign_manifest(manifest, seed, public_key_id=public_key_id)
        output.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=".signing-", suffix=PACK_SUFFIX, dir=output.parent)
        os.close(handle)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as target:
                target.writestr(zipfile.ZipInfo(rules.MANIFEST_FILE, date_time=FIXED_TIME), encoded_manifest(signed),
                                compress_type=zipfile.ZIP_DEFLATED)
                for info in archive.infolist():
                    if info.is_dir() or info.filename == rules.MANIFEST_FILE:
                        continue
                    target.writestr(zipfile.ZipInfo(info.filename, date_time=FIXED_TIME), archive.read(info),
                                    compress_type=zipfile.ZIP_DEFLATED)
            os.replace(temporary, output)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    return verify_pack(output, trusted=())


def _trusted_registry(trusted):
    rows = []
    for value in trusted:
        key_id, _, public_key = str(value).partition("=")
        rows.append({"public_key_id": key_id, "public_key": public_key.lower(),
                     "confirmed_at_utc": "1970-01-01T00:00:00+00:00"})
    return {"trusted_keys": signing.validate_trusted_keys(rows)}


def verify_pack(path, *, trusted=()):
    """Signature status of a pack; trusted is a list of 'ID=HEXKEY' strings."""
    report = rules.validate_library_pack(path, registry=_trusted_registry(trusted))
    check = report.signature
    return {"path": str(path), "library_id": report.manifest["library_id"],
            "library_version": report.manifest["library_version"], "content_sha256": report.manifest["content_sha256"],
            "signature_status": check.status, "reason": check.reason, "public_key_id": check.public_key_id,
            "fingerprint": check.fingerprint,
            "trust": check.trust.action if check.trust is not None else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate-key", help="Write a new private seed to a new local file")
    generate.add_argument("key_file", type=Path)
    public = commands.add_parser("public-key", help="Show the public key and fingerprint of a key file")
    public.add_argument("key_file", type=Path)
    sign_command = commands.add_parser("sign", help="Write a signed copy of a library pack")
    sign_command.add_argument("pack", type=Path)
    sign_command.add_argument("output", type=Path)
    sign_command.add_argument("--key", type=Path, required=True, help="Local key file (64 hex digits)")
    sign_command.add_argument("--key-id", help="public_key_id (default ed25519:<fingerprint prefix>)")
    verify_command = commands.add_parser("verify", help="Report unsigned / valid / invalid / untrusted_key")
    verify_command.add_argument("pack", type=Path)
    verify_command.add_argument("--trusted", action="append", default=[], help="Trusted key as ID=HEXKEY (repeatable)")
    args = parser.parse_args(argv)
    if args.command == "generate-key":
        result = generate_key(args.key_file)
    elif args.command == "public-key":
        result = describe_key(read_seed(args.key_file))
    elif args.command == "sign":
        result = sign_pack(args.pack, args.output, read_seed(args.key), public_key_id=args.key_id)
    else:
        result = verify_pack(args.pack, trusted=args.trusted)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if args.command == "verify" and result["signature_status"] == "invalid" else 0


if __name__ == "__main__":
    sys.exit(main())
