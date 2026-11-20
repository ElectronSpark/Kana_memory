"""Design 08 phase 5: Ed25519 pack signatures, TOFU keys, source display, tools, alias/duplicate follow-ups."""
import asyncio
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import zipfile
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory import content_library_rules as rules
from kanamemory import content_library_signing as signing
from kanamemory import content_pack, learning_content
from support.app_fixtures import setup_app, question_app  # noqa: F401
from support.content_library_support import build_core_resources, extension_namespaces, pack_bytes
from support.fake_ui import descendants, text_of
from support.learning_fixtures import learning_app, click  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

# RFC 8032 §7.1 test vectors: (secret seed, public key, message, signature).
RFC8032 = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
    ("833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42",
     "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
     "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
     "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b58909351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704"),
]
SEED_A = bytes.fromhex(RFC8032[0][0])
SEED_B = bytes.fromhex(RFC8032[1][0])
KEY_A = RFC8032[0][1]
KEY_B = RFC8032[1][1]
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def _load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rewrite_manifest(data, change):
    """Pack bytes with manifest.json replaced by change(manifest dict)."""
    source = zipfile.ZipFile(io.BytesIO(data))
    manifest = change(json.loads(source.read(rules.MANIFEST_FILE).decode("utf-8")))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as target:
        target.writestr(rules.MANIFEST_FILE, json.dumps(manifest, ensure_ascii=False))
        for info in source.infolist():
            if info.filename != rules.MANIFEST_FILE:
                target.writestr(info.filename, source.read(info))
    return buffer.getvalue()


def _signed(data, seed=SEED_A, key_id="author:a"):
    return _rewrite_manifest(data, lambda m: signing.sign_manifest(m, seed, public_key_id=key_id))


def _pack_file(tmp_path, data, name="pack.zip"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _registry(*keys):
    registry = rules.default_registry()
    registry["trusted_keys"] = [{"public_key_id": key_id, "public_key": key, "confirmed_at_utc": "2026-11-01T00:00:00+00:00"}
                                for key_id, key in keys]
    return rules.validate_registry(registry)


# ---------------------------------------------------------------- Ed25519

@pytest.mark.parametrize("seed,public,message,signature", RFC8032)
def test_rfc8032_vectors_sign_and_verify(seed, public, message, signature):
    seed, message = bytes.fromhex(seed), bytes.fromhex(message)
    assert signing.public_key_from_seed(seed).hex() == public
    assert signing.sign(seed, message).hex() == signature
    assert signing.verify(bytes.fromhex(public), message, bytes.fromhex(signature))


def test_verify_rejects_changed_message_signature_key_and_malformed_input():
    seed, public, message, signature = (bytes.fromhex(v) for v in RFC8032[2])
    assert not signing.verify(public, message + b"x", signature)
    assert not signing.verify(public, message, signature[:-1] + bytes([signature[-1] ^ 1]))
    assert not signing.verify(bytes.fromhex(KEY_A), message, signature)
    assert not signing.verify(public[:31], message, signature)
    assert not signing.verify(public, message, signature[:63])
    high_s = signature[:32] + (int.from_bytes(signature[32:], "little") + signing._L).to_bytes(32, "little")
    assert not signing.verify(public, message, high_s)  # non-canonical S >= L
    assert not signing.verify(b"\xff" * 32, message, signature)  # y >= p is not a point
    with pytest.raises(signing.SigningError):
        signing.sign(b"short", b"")


def test_canonical_bytes_ignore_signature_and_key_order():
    a = {"b": "値", "a": [1, {"y": 2, "x": 1}]}
    b = {"a": [1, {"x": 1, "y": 2}], "b": "値", "signature": {"anything": "x"}}
    assert signing.canonical_manifest_bytes(a) == signing.canonical_manifest_bytes(b)
    assert signing.canonical_manifest_bytes(a) == '{"a":[1,{"x":1,"y":2}],"b":"値"}'.encode("utf-8")
    signed = signing.sign_manifest(a, SEED_A)
    assert signed["signature"]["public_key"] == KEY_A and signed["signature"]["public_key_id"].startswith("ed25519:")
    assert signing.check_manifest_signature(signed).status == "untrusted_key"
    assert signing.parse_seed(" " + RFC8032[0][0].upper() + "\n") == SEED_A
    with pytest.raises(signing.SigningError):
        signing.parse_seed("not a key")


# ---------------------------------------------------------------- four kinds of packs

def test_validate_library_pack_reports_four_signature_kinds(tmp_path):
    base = pack_bytes("author.n3", extension_namespaces())
    trusted = _registry(("author:a", KEY_A))
    unsigned = rules.validate_library_pack(_pack_file(tmp_path, base, "unsigned.zip"), registry=trusted)
    assert unsigned.signature.status == "unsigned" and not unsigned.signed and rules.install_refusal(unsigned) is None

    signed_path = _pack_file(tmp_path, _signed(base), "signed.zip")
    valid = rules.validate_library_pack(signed_path, registry=trusted)
    assert valid.signature.status == "valid" and valid.signed and valid.signature.trust.action == "trusted"
    assert valid.manifest["signature"]["public_key_id"] == "author:a"
    assert rules.validate_library_pack(signed_path).signature.status == "untrusted_key"  # no registry: first seen

    tampered = _rewrite_manifest(_signed(base), lambda m: {**m, "title": "被改过的标题"})
    bad = rules.validate_library_pack(_pack_file(tmp_path, tampered, "tampered.zip"), registry=trusted)
    assert bad.signature.status == "invalid" and "签名与清单内容不符" in bad.signature.reason
    assert rules.install_refusal(bad).startswith("签名无效，不能安装")

    swapped = _rewrite_manifest(_signed(base), lambda m: {**m, "signature": {**m["signature"], "public_key": KEY_B}})
    assert rules.validate_library_pack(_pack_file(tmp_path, swapped, "swapped.zip"), registry=trusted).signature.status == "invalid"

    other_key = rules.validate_library_pack(_pack_file(tmp_path, _signed(base, SEED_B), "other.zip"), registry=trusted)
    assert other_key.signature.status == "untrusted_key" and other_key.signature.trust.action == "key_id_conflict"
    assert "可能是作者换了密钥，也可能是冒用" in other_key.signature.trust.message()

    malformed = _rewrite_manifest(base, lambda m: {**m, "signature": {"algorithm": "rsa", "public_key_id": "x",
                                                                         "public_key": KEY_A, "signature": "00"}})
    check = rules.validate_library_pack(_pack_file(tmp_path, malformed, "malformed.zip")).signature
    assert check.status == "invalid" and check.reason == "不支持的签名算法"
    with pytest.raises(rules.ContentLibraryError):
        rules.validate_library_pack(_pack_file(tmp_path, _rewrite_manifest(base, lambda m: {**m, "signature": "x"}), "s.zip"))


def test_precheck_blocks_invalid_signature_and_source_fields_follow_status(tmp_path):
    base = pack_bytes("author.n3", extension_namespaces())
    tampered = rules.validate_library_pack(_pack_file(tmp_path, _rewrite_manifest(_signed(base), lambda m: {**m, "title": "x"})))
    kwargs = dict(installed_fingerprints={}, learned_ids=set(), installed_library_ids=[rules.CORE_LIBRARY_ID])
    check = rules.precheck_pack(tampered.manifest, signature=tampered.signature, **kwargs)
    assert check.blocking_reasons()[0].startswith("签名无效，不能安装")
    assert rules.precheck_pack(tampered.manifest, **kwargs).blocking_reasons() == ()  # old callers unchanged
    with pytest.raises(rules.ContentLibraryError):
        rules.source_signature_fields(tampered)
    signed = rules.validate_library_pack(_pack_file(tmp_path, _signed(base), "ok.zip"))
    fields = rules.source_signature_fields(signed, _registry(("author:a", KEY_A)))
    assert fields == {"signature_status": "valid", "public_key_id": "author:a",
                      "key_fingerprint": signing.key_fingerprint(KEY_A)}
    assert rules.source_signature_fields(signed)["signature_status"] == "untrusted_key"
    unsigned = rules.validate_library_pack(_pack_file(tmp_path, base, "u.zip"))
    assert rules.source_signature_fields(unsigned) == {"signature_status": "unsigned"}


# ---------------------------------------------------------------- TOFU

def test_trust_decision_table():
    block = signing.sign_manifest({"a": 1}, SEED_A, public_key_id="author:a")["signature"]
    fingerprint = signing.key_fingerprint(KEY_A)
    cases = [
        (rules.default_registry(), "untrusted_key", "confirm_new_key"),          # first seen
        (_registry(("author:a", KEY_A)), "valid", "trusted"),                   # same key again
        (_registry(("author:a", KEY_B)), "untrusted_key", "key_id_conflict"),   # same id, other key
        (_registry(("someone:else", KEY_A)), "untrusted_key", "confirm_new_key"),  # same key under a new id
        (None, "untrusted_key", "confirm_new_key"),
    ]
    for registry, status, action in cases:
        decision = signing.trust_decision(registry, block)
        assert (decision.status, decision.action, decision.fingerprint) == (status, action, fingerprint)
        assert decision.can_confirm == (action == "confirm_new_key")
    conflict = signing.trust_decision(_registry(("author:a", KEY_B)), block)
    assert conflict.known_fingerprint == signing.key_fingerprint(KEY_B)


def test_trusted_keys_registry_shape_and_confirmation():
    registry = rules.with_trusted_key(rules.default_registry(), "author:a", KEY_A, now=NOW)
    assert registry["trusted_keys"] == [{"public_key_id": "author:a", "public_key": KEY_A,
                                         "confirmed_at_utc": "2026-11-08T12:00:00+00:00"}]
    assert rules.with_trusted_key(registry, "author:a", KEY_A, now=NOW) == registry
    with pytest.raises(rules.ContentLibraryError, match="不能自动替换"):
        rules.with_trusted_key(registry, "author:a", KEY_B, now=NOW)
    for bad in ([{"public_key_id": "k", "public_key": "zz", "confirmed_at_utc": "2026-11-08T12:00:00+00:00"}],
                [{"public_key_id": "k", "public_key": KEY_A, "confirmed_at_utc": "2026-11-08T12:00:00"}],
                [{"public_key_id": "k", "public_key": KEY_A}],
                [{"public_key_id": "k", "public_key": KEY_A, "confirmed_at_utc": "2026-11-08T12:00:00+00:00"}] * 2,
                [{"public_key_id": " k", "public_key": KEY_A, "confirmed_at_utc": "2026-11-08T12:00:00+00:00"}]):
        with pytest.raises(rules.ContentLibraryError):
            rules.validate_registry({**rules.default_registry(), "trusted_keys": bad})


def test_registry_source_keeps_old_statuses_and_accepts_key_fields():
    entry = {"library_id": "author.n3", "library_version": "20260102", "profile": "extension",
             "content_sha256": "a" * 64, "enabled": True, "installed_at_utc": "2026-11-08T12:00:00+00:00",
             "source": {"kind": "file", "name": "n3.zip", "sha256": "b" * 64, "signature_status": "unverified"},
             "path": "author.n3/20260102", "state": "ready"}
    registry = rules.default_registry()
    registry["libraries"].append(entry)
    registry["priority"].append("author.n3")
    assert rules.validate_registry(registry)["libraries"][1]["source"] == entry["source"]  # old records unchanged
    entry["source"] = {**entry["source"], "signature_status": "untrusted_key", "public_key_id": "author:a",
                       "key_fingerprint": signing.key_fingerprint(KEY_A)}
    assert rules.validate_registry(registry)["libraries"][1]["source"]["public_key_id"] == "author:a"
    for source in ({**entry["source"], "signature_status": "invalid"},
                   {**entry["source"], "signature_status": "unsigned"},
                   {**entry["source"], "key_fingerprint": "xyz"},
                   {**entry["source"], "extra": 1}):
        entry["source"] = source
        with pytest.raises(rules.ContentLibraryError):
            rules.validate_registry(registry)


# ---------------------------------------------------------------- tools

def test_sign_tool_round_trip_and_key_files(tmp_path, monkeypatch):
    tool = _load_tool("sign_library_pack")
    pack = _pack_file(tmp_path, pack_bytes("author.n3", extension_namespaces()))
    key_file = tmp_path / "author.key"
    key_file.write_text(RFC8032[0][0] + "\n", encoding="ascii")
    result = tool.sign_pack(pack, tmp_path / "signed.zip", tool.read_seed(key_file), public_key_id="author:a")
    assert result["signature_status"] == "untrusted_key" and result["trust"] == "confirm_new_key"
    assert tool.verify_pack(tmp_path / "signed.zip", trusted=["author:a=" + KEY_A])["signature_status"] == "valid"
    assert tool.verify_pack(pack)["signature_status"] == "unsigned"
    original = zipfile.ZipFile(pack)
    signed = zipfile.ZipFile(tmp_path / "signed.zip")
    assert all(original.read(n) == signed.read(n) for n in original.namelist() if n != rules.MANIFEST_FILE)
    with pytest.raises(ValueError):
        tool.sign_pack(pack, tmp_path / "signed.zip", SEED_A)  # never overwrites
    with pytest.raises(ValueError, match="outside the repository"):
        tool.generate_key(ROOT / ".test_runs" / "should-not-exist.key")
    monkeypatch.setattr(tool, "ROOT", tmp_path / "repository")  # the test folder itself lives in the checkout
    described = tool.generate_key(tmp_path / "new.key")
    assert described["public_key"] == signing.public_key_from_seed(tool.read_seed(tmp_path / "new.key")).hex()
    with pytest.raises(FileExistsError):
        tool.generate_key(tmp_path / "new.key")
    tampered = _pack_file(tmp_path, _rewrite_manifest((tmp_path / "signed.zip").read_bytes(),
                                                      lambda m: {**m, "title": "x"}), "bad.zip")
    assert tool.main(["verify", str(tampered)]) == 1


def test_package_tool_signs_with_the_same_implementation(tmp_path):
    tool = _load_tool("package_library_pack")
    resources = build_core_resources(tmp_path / "resources")
    result = tool.package(resources, tmp_path / "out" / "core.zip", sign_seed=SEED_A, key_id="author:a")
    assert result["signature_status"] == "untrusted_key" and result["public_key_id"] == "author:a"
    report = rules.validate_library_pack(tmp_path / "out" / "core.zip", registry=_registry(("author:a", KEY_A)))
    assert report.signature.status == "valid"
    plain = tool.package(resources, tmp_path / "out" / "plain.zip")
    assert plain["signature_status"] == "unsigned"


# ---------------------------------------------------------------- learning_content follow-ups

class _FakeCatalogue:
    def __init__(self, *, aliases=None, payloads=None, core=()):
        self._aliases = aliases or {}
        self.payloads = payloads or {}
        self._core = frozenset(core)
        self.extension_ids = tuple(self.payloads) or (("author.ext",) if aliases else ())

    def alias_target(self, content_id):
        return self._aliases.get(content_id)

    def core_ids(self):
        return self._core


def test_get_learning_item_follows_extension_alias_only(monkeypatch):
    items = {"grammar:author.ext:new": {"id": "grammar:author.ext:new", "family": "grammar", "title": "新"},
             "grammar:core": {"id": "grammar:core", "family": "grammar", "title": "核"}}
    monkeypatch.setattr(learning_content, "_ITEMS", items)
    monkeypatch.setattr("kanamemory.material_review.reviewed_item", lambda item: dict(item))
    monkeypatch.setattr(content_pack, "library_layers", lambda: ())
    assert learning_content.get_learning_item("grammar:old") is None  # core only: unchanged
    catalogue = _FakeCatalogue(aliases={"grammar:old": "grammar:author.ext:new", "grammar:core": "grammar:author.ext:new"})
    monkeypatch.setattr(content_pack, "library_layers", lambda: ("layer",))
    monkeypatch.setattr(content_pack, "effective_catalogue", lambda: catalogue)
    assert learning_content.get_learning_item("grammar:old")["id"] == "grammar:author.ext:new"
    assert learning_content.get_learning_item("grammar:core")["title"] == "核"  # an existing id never aliases
    assert learning_content.get_learning_item("grammar:missing") is None


def test_cross_namespace_duplicate_rejects_extension_but_core_duplicate_still_raises(monkeypatch):
    core_item = {"id": "grammar:core:a"}
    ext_item = {"id": "grammar:ext:b"}
    ext_copy = {"id": "grammar:ext:b"}
    core_copy = {"id": "grammar:core:a", "copy": True}
    monkeypatch.setattr(learning_content, "_ITEMS", {"word:core:w": {"id": "word:core:w"}, "word:ext:x": {"id": "word:ext:x"}})
    monkeypatch.setattr(learning_content, "_DIAGNOSTICS", [])
    payloads = {"author.ext": {"grammar_curriculum": {"_ITEMS": {"grammar:ext:b": ext_item}},
                               "usage_grammar_curriculum": {"_ITEMS": {"grammar:ext:b": ext_copy, "grammar:core:a": core_copy}},
                               "learning_content": {"_WORDS": [{"id": "word:ext:x"}]}}}
    catalogue = _FakeCatalogue(payloads=payloads, core={"grammar:core:a", "word:core:w"})
    monkeypatch.setattr(content_pack, "library_layers", lambda: ("layer",))
    monkeypatch.setattr(content_pack, "effective_catalogue", lambda: catalogue)
    sources = [("grammar_curriculum", [core_item, ext_item]), ("usage_grammar_curriculum", [ext_copy, core_copy])]
    kept = learning_content._without_extension_duplicates(sources)
    assert kept == [core_item]  # one core copy; every extension-only item left out
    assert "word:ext:x" not in learning_content._ITEMS and "word:core:w" in learning_content._ITEMS
    assert learning_content.catalogue_diagnostics() == (
        ("duplicate_identity_rejected_extension", ("grammar:ext:b", "grammar:core:a"), ("author.ext",)),)
    # A duplicate inside core content is returned as is: the caller raises as before.
    monkeypatch.setattr(learning_content, "_ITEMS", {})
    core_twice = [("grammar_curriculum", [core_item]), ("exam_grammar_curriculum", [core_copy])]
    assert learning_content._without_extension_duplicates(core_twice) == [core_item, core_copy]
    monkeypatch.setattr(content_pack, "library_layers", lambda: ())
    assert learning_content._without_extension_duplicates(sources) == [core_item, ext_item, ext_copy, core_copy]


# ---------------------------------------------------------------- fake UI

@pytest.fixture
def library_app(learning_app, tmp_path, monkeypatch):
    app = learning_app
    resources = build_core_resources(tmp_path / "resources")
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(tmp_path / "libraries"))
    content_pack.clear_cache()
    app.go_back = Mock()
    service = app.content_library_service()
    service._catalogue_ids = lambda: set()
    service._catalogue_fingerprints = lambda: {}
    yield app
    content_pack.clear_cache()


def _buttons(app):
    return {view.text: view for view in descendants(app.body) if view.on_press}


def _precheck(app, data):
    app.platform.import_library_pack = AsyncMock(return_value=data)
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    assert app.page == "content_library_precheck"
    return text_of(app.body)


def test_precheck_shows_unsigned_source_warning_and_still_installs(library_app):
    app = library_app
    data = pack_bytes("author.n3", extension_namespaces())
    text = _precheck(app, data)
    import hashlib
    assert "文件名：导入文件" in text and "导入时间：" in text
    assert f"文件 sha256：{hashlib.sha256(data).hexdigest()[:12]}（前 12 位）" in text
    assert "签名状态：未签名" in text and "警告：这个内容库没有签名" in text and "安装" in _buttons(app)
    click(app, "安装")
    text = text_of(app.body)
    assert "来源：导入文件 导入文件 · 导入时间 " in text and "签名：未签名" in text and "警告：未签名" in text
    assert "来源：内置（随应用安装与校验）" in text


def test_precheck_shows_untrusted_key_fingerprint_and_trust_confirmation(library_app):
    app = library_app
    service = app.content_library_service()
    text = _precheck(app, _signed(pack_bytes("author.n3", extension_namespaces())))
    shown = signing.display_fingerprint(signing.key_fingerprint(KEY_A))
    assert "签名状态：签名有效但公钥未信任" in text and f"公钥指纹：{shown}（author:a）" in text
    assert "首次见到公钥 author:a" in text and "警告：签名与内容一致，但公钥未信任" in text
    # 16.5: the service provides trust_signing_key, so the confirmation button is offered.
    assert "安装" in _buttons(app) and "核对指纹无误，信任此公钥" in _buttons(app)
    assert callable(service.trust_signing_key)
    click(app, "核对指纹无误，信任此公钥")
    text = text_of(app.body)
    assert app.page == "content_library_precheck" and "签名状态：签名有效（公钥已信任）" in text
    assert "公钥 author:a 已信任" in text and "警告" not in text.split("签名状态")[1].split("预检结果")[0]
    assert app.store.get_setting(rules.REGISTRY_SETTING)["trusted_keys"][0]["public_key_id"] == "author:a"
    click(app, "安装")
    source = rules.registry_entry(app.store.get_setting(rules.REGISTRY_SETTING), "author.n3")["source"]
    assert source["signature_status"] == "valid" and source["public_key_id"] == "author:a"
    assert source["key_fingerprint"] == signing.key_fingerprint(KEY_A)
    text = text_of(app.body)
    assert f"签名：签名有效（公钥已信任） · 公钥指纹 {shown}（author:a）" in text


def test_precheck_refuses_invalid_signature_with_reason(library_app):
    app = library_app
    data = _rewrite_manifest(_signed(pack_bytes("author.n3", extension_namespaces())), lambda m: {**m, "title": "改过"})
    text = _precheck(app, data)
    assert "签名状态：签名无效" in text and "签名无效，不能安装：签名与清单内容不符" in text
    buttons = _buttons(app)
    assert "安装" not in buttons and not any(name.startswith("仍然安装") for name in buttons) and "取消" in buttons
    asyncio.run(app.install_content_library())
    assert "签名无效，不能安装" in app.alert.call_args[0][0]
    assert [e["library_id"] for e in app.store.get_setting(rules.REGISTRY_SETTING)["libraries"]] == [rules.CORE_LIBRARY_ID]
