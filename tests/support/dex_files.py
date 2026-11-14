"""Hand-made dex files and APK archives for tests of the package checks (no Android tool needed)."""
import struct
import zipfile


def minimal_dex(defined, referenced=()):
    """A dex file holding only what a reader of class definitions needs: the header and the string, type and
    class tables. ``defined``: descriptors (Lorg/example/Main;) with a class_def; ``referenced``: descriptors that
    are only named as types."""
    names = sorted({*defined, *referenced})
    string_ids = 0x70
    type_ids = string_ids + 4 * len(names)
    class_defs = type_ids + 4 * len(names)
    text_at = class_defs + 32 * len(defined)
    texts, offsets = b"", []
    for name in names:
        offsets.append(text_at + len(texts))
        raw = name.encode("utf-8")
        texts += bytes([len(raw)]) + raw + b"\0"
    out = bytearray(b"dex\n035\0" + b"\0" * (0x70 - 8))
    struct.pack_into("<II", out, 0x38, len(names), string_ids)
    struct.pack_into("<II", out, 0x40, len(names), type_ids)
    struct.pack_into("<II", out, 0x60, len(defined), class_defs)
    out += b"".join(struct.pack("<I", offset) for offset in offsets)
    out += b"".join(struct.pack("<I", index) for index in range(len(names)))
    out += b"".join(struct.pack("<I", names.index(name)) + b"\0" * 28 for name in defined)
    return bytes(out) + texts


def write_apk(path, members):
    """A zip archive at ``path`` with the given members {name: bytes}; returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path
