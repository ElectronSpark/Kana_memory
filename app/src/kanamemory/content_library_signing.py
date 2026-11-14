"""Library-pack signatures and trust-on-first-use keys (design 08 §4.5, phase 5).

Pure rules: Ed25519 (RFC 8032, pure Python so the Android runtime needs no
third-party crypto), the canonical bytes a manifest signature covers, the
signature status of a pack and the TOFU trust decision against the registry's
trusted_keys. Nothing here touches Toga, the platform, the network or a
database; key material is only ever passed in by the caller (the signing
tool reads a local key file given on its command line).

A manifest may carry ``signature: {algorithm: "ed25519", public_key_id,
public_key, signature}`` (hex public key and signature). The signed message
is the manifest without its ``signature`` field, serialized as canonical JSON
(sort_keys, ensure_ascii=False, separators (",", ":")) in UTF-8. The manifest
lists every file's sha256 and the content_sha256, so the signature covers the
whole pack. First-version policy (open question Q6): signatures are not
required; unsigned and untrusted_key packs install with an explicit source
warning, invalid signatures are refused.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
import re

ALGORITHM = "ed25519"
SIGNATURE_FIELD = "signature"
SIGNATURE_KEYS = frozenset({"algorithm", "public_key_id", "public_key", "signature"})
PACK_SIGNATURE_STATUSES = ("unsigned", "valid", "invalid", "untrusted_key")
TRUST_ACTIONS = ("trusted", "confirm_new_key", "key_id_conflict")
PUBLIC_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+@-]{0,127}\Z")
HEX_KEY = re.compile(r"[0-9a-f]{64}\Z")
HEX_SIGNATURE = re.compile(r"[0-9a-f]{128}\Z")
MAX_TRUSTED_KEYS = 256
STATUS_TEXT = {"unsigned": "未签名", "valid": "签名有效（公钥已信任）", "invalid": "签名无效",
               "untrusted_key": "签名有效但公钥未信任"}


class SigningError(ValueError):
    """Malformed key material, signature block or trusted-key list."""


# ---------------------------------------------------------------- Ed25519 (RFC 8032 §5.1, §6)

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _sha512(data):
    return hashlib.sha512(data).digest()


def _sha512_mod_l(data):
    return int.from_bytes(_sha512(data), "little") % _L


def _point_add(a, b):
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    k = (y1 - x1) * (y2 - x2) % _P
    m = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    d = z1 * 2 * z2 % _P
    e, f, g, h = m - k, d - c, d + c, m + k
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _point_mul(scalar, point):
    result = (0, 1, 1, 0)
    while scalar > 0:
        if scalar & 1:
            result = _point_add(result, point)
        point = _point_add(point, point)
        scalar >>= 1
    return result


def _point_equal(a, b):
    x1, y1, z1, _ = a
    x2, y2, z2, _ = b
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _compress(point):
    x, y, z, _ = point
    zinv = pow(z, _P - 2, _P)
    x, y = x * zinv % _P, y * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(raw):
    if len(raw) != 32:
        return None
    y = int.from_bytes(raw, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _expand_secret(seed):
    if not isinstance(seed, (bytes, bytearray)) or len(seed) != 32:
        raise SigningError("An Ed25519 private key seed has 32 bytes")
    digest = _sha512(bytes(seed))
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def public_key_from_seed(seed):
    """The 32-byte public key of a 32-byte private seed."""
    scalar, _ = _expand_secret(seed)
    return _compress(_point_mul(scalar, _G))


def sign(seed, message):
    """RFC 8032 Ed25519 signature (64 bytes) of message with a 32-byte seed."""
    scalar, prefix = _expand_secret(seed)
    public = _compress(_point_mul(scalar, _G))
    message = bytes(message)
    r = _sha512_mod_l(prefix + message)
    encoded_r = _compress(_point_mul(r, _G))
    h = _sha512_mod_l(encoded_r + public + message)
    s = (r + h * scalar) % _L
    return encoded_r + int.to_bytes(s, 32, "little")


def verify(public_key, message, signature):
    """True only for a valid RFC 8032 Ed25519 signature; malformed input is False."""
    if not isinstance(public_key, (bytes, bytearray)) or not isinstance(signature, (bytes, bytearray)):
        return False
    if len(public_key) != 32 or len(signature) != 64:
        return False
    point_a = _decompress(bytes(public_key))
    point_r = _decompress(bytes(signature[:32]))
    if point_a is None or point_r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = _sha512_mod_l(bytes(signature[:32]) + bytes(public_key) + bytes(message))
    return _point_equal(_point_mul(s, _G), _point_add(point_r, _point_mul(h, point_a)))


# ---------------------------------------------------------------- keys and fingerprints

def key_fingerprint(public_key_hex):
    """sha256 hex of the raw public key bytes."""
    return hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()


def display_fingerprint(fingerprint):
    """The first 16 hex digits in groups of four, as shown to the learner."""
    head = fingerprint[:16]
    return " ".join(head[i:i + 4] for i in range(0, len(head), 4))


def default_key_id(public_key_hex):
    return "ed25519:" + key_fingerprint(public_key_hex)[:16]


def parse_seed(text):
    """A private seed from key-file text: 64 hex digits (whitespace ignored)."""
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode("ascii")
        except UnicodeDecodeError as error:
            raise SigningError("A key file holds 64 hex digits") from error
    compact = "".join(str(text).split()).lower()
    if not HEX_KEY.fullmatch(compact):
        raise SigningError("A key file holds 64 hex digits")
    return bytes.fromhex(compact)


# ---------------------------------------------------------------- manifest signatures

def canonical_manifest_bytes(manifest):
    """The bytes a manifest signature covers: the manifest without its signature."""
    if not isinstance(manifest, dict):
        raise SigningError("A manifest is a JSON object")
    body = {key: value for key, value in manifest.items() if key != SIGNATURE_FIELD}
    try:
        text = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise SigningError("Manifest is not canonical JSON") from error
    return text.encode("utf-8")


def validate_signature_block(value):
    """A normalized signature block or SigningError (shape, algorithm, hex lengths)."""
    if not isinstance(value, dict) or set(value) != SIGNATURE_KEYS:
        raise SigningError("签名字段格式不正确")
    if value["algorithm"] != ALGORITHM:
        raise SigningError("不支持的签名算法")
    key_id, public_key, signature = value["public_key_id"], value["public_key"], value["signature"]
    if not isinstance(key_id, str) or not PUBLIC_KEY_ID.fullmatch(key_id):
        raise SigningError("公钥 ID 格式不正确")
    if not isinstance(public_key, str) or not HEX_KEY.fullmatch(public_key):
        raise SigningError("公钥格式不正确")
    if not isinstance(signature, str) or not HEX_SIGNATURE.fullmatch(signature):
        raise SigningError("签名值格式不正确")
    return {"algorithm": ALGORITHM, "public_key_id": key_id, "public_key": public_key, "signature": signature}


def sign_manifest(manifest, seed, *, public_key_id=None):
    """A copy of manifest with a fresh signature block (any old one replaced)."""
    public_key = public_key_from_seed(seed).hex()
    key_id = public_key_id or default_key_id(public_key)
    if not PUBLIC_KEY_ID.fullmatch(key_id):
        raise SigningError("Invalid public_key_id")
    signature = sign(seed, canonical_manifest_bytes(manifest)).hex()
    body = {key: value for key, value in manifest.items() if key != SIGNATURE_FIELD}
    body[SIGNATURE_FIELD] = {"algorithm": ALGORITHM, "public_key_id": key_id, "public_key": public_key,
                             "signature": signature}
    return body


@dataclass(frozen=True)
class TrustDecision:
    """TOFU result for a cryptographically valid signature."""
    status: str            # "valid" | "untrusted_key"
    action: str            # "trusted" | "confirm_new_key" | "key_id_conflict"
    public_key_id: str
    public_key: str
    fingerprint: str
    known_fingerprint: str | None = None

    @property
    def can_confirm(self):
        return self.action == "confirm_new_key"

    def message(self):
        shown = display_fingerprint(self.fingerprint)
        if self.action == "trusted":
            return f"公钥 {self.public_key_id} 已信任（指纹 {shown}）。"
        if self.action == "key_id_conflict":
            known = display_fingerprint(self.known_fingerprint or "")
            return (f"警告：公钥 ID {self.public_key_id} 以前确认的是另一把公钥（指纹 {known}），"
                    f"这次是 {shown}。可能是作者换了密钥，也可能是冒用；未信任，来源未验证。")
        return f"首次见到公钥 {self.public_key_id}（指纹 {shown}）；请核对指纹后再决定是否信任。"


def validate_trusted_keys(rows):
    """Normalized registry trusted_keys (section 5.6) or SigningError."""
    if not isinstance(rows, list) or len(rows) > MAX_TRUSTED_KEYS:
        raise SigningError("Invalid trusted keys")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"public_key_id", "public_key", "confirmed_at_utc"}:
            raise SigningError("Invalid trusted key")
        key_id, public_key, confirmed = row["public_key_id"], row["public_key"], row["confirmed_at_utc"]
        if not isinstance(key_id, str) or not PUBLIC_KEY_ID.fullmatch(key_id):
            raise SigningError("Invalid trusted public_key_id")
        if not isinstance(public_key, str) or not HEX_KEY.fullmatch(public_key):
            raise SigningError("Invalid trusted public_key")
        if not isinstance(confirmed, str) or len(confirmed) > 64:
            raise SigningError("Invalid confirmed_at_utc")
        try:
            parsed = datetime.fromisoformat(confirmed)
        except ValueError as error:
            raise SigningError("Invalid confirmed_at_utc") from error
        if parsed.tzinfo is None:
            raise SigningError("Invalid confirmed_at_utc")
        if key_id in seen:
            raise SigningError("Duplicate trusted public_key_id")
        seen.add(key_id)
        result.append({"public_key_id": key_id, "public_key": public_key, "confirmed_at_utc": confirmed})
    return result


def _trusted_rows(registry):
    if registry is None:
        return []
    rows = registry.get("trusted_keys", []) if isinstance(registry, dict) else registry
    return validate_trusted_keys(list(rows))


def trust_decision(registry, signature):
    """TOFU decision for a cryptographically valid signature block.

    registry: the content-library registry (or its trusted_keys list).
    Same (public_key_id, public_key) as a confirmed key -> valid; a key id
    never seen -> untrusted_key until the learner confirms it; a known key id
    with a different public key -> untrusted_key with a conflict warning
    (never replaced automatically).
    """
    block = validate_signature_block(signature)
    fingerprint = key_fingerprint(block["public_key"])
    for row in _trusted_rows(registry):
        if row["public_key_id"] != block["public_key_id"]:
            continue
        if row["public_key"] == block["public_key"]:
            return TrustDecision("valid", "trusted", block["public_key_id"], block["public_key"], fingerprint)
        return TrustDecision("untrusted_key", "key_id_conflict", block["public_key_id"], block["public_key"],
                             fingerprint, key_fingerprint(row["public_key"]))
    return TrustDecision("untrusted_key", "confirm_new_key", block["public_key_id"], block["public_key"], fingerprint)


def with_trusted_key(trusted_keys, public_key_id, public_key, confirmed_at_utc):
    """trusted_keys plus one learner-confirmed key; a conflicting key id is refused."""
    rows = validate_trusted_keys(list(trusted_keys))
    for row in rows:
        if row["public_key_id"] == public_key_id:
            if row["public_key"] == public_key:
                return rows
            raise SigningError("这个公钥 ID 已确认为另一把公钥，不能自动替换。")
    return validate_trusted_keys(rows + [{"public_key_id": public_key_id, "public_key": public_key,
                                          "confirmed_at_utc": confirmed_at_utc}])


@dataclass(frozen=True)
class SignatureCheck:
    """Signature status of one pack manifest (status in PACK_SIGNATURE_STATUSES)."""
    status: str
    reason: str | None = None
    public_key_id: str | None = None
    public_key: str | None = None
    fingerprint: str | None = None
    trust: TrustDecision | None = None
    cryptographically_valid: bool = False

    @property
    def refused(self):
        return self.status == "invalid"

    def refusal_reason(self):
        return ("签名无效，不能安装：" + (self.reason or "签名与内容不符")) if self.refused else None

    def with_trust(self, registry):
        """Re-decide trust for a valid signature against a (newer) registry."""
        if not self.cryptographically_valid:
            return self
        decision = trust_decision(registry, {"algorithm": ALGORITHM, "public_key_id": self.public_key_id,
                                             "public_key": self.public_key, "signature": "0" * 128})
        return replace(self, status=decision.status, trust=decision)

    def status_text(self):
        return STATUS_TEXT[self.status]

    def lines(self):
        """Source lines for the precheck page and the library list."""
        if self.status == "unsigned":
            return ("警告：这个内容库没有签名，无法确认作者和内容是否被改动；仍可安装，来源显示为未签名。",)
        if self.status == "invalid":
            return (self.refusal_reason(),)
        rows = [f"公钥指纹：{display_fingerprint(self.fingerprint)}（{self.public_key_id}）"]
        if self.trust is not None:
            rows.append(self.trust.message())
        if self.status == "untrusted_key":
            rows.append("警告：签名与内容一致，但公钥未信任；仍可安装，来源显示为未信任的公钥。")
        return tuple(rows)


def check_manifest_signature(manifest, registry=None):
    """SignatureCheck for a raw (as read) manifest against the registry's trusted keys."""
    if not isinstance(manifest, dict) or SIGNATURE_FIELD not in manifest:
        return SignatureCheck("unsigned")
    try:
        block = validate_signature_block(manifest[SIGNATURE_FIELD])
    except SigningError as error:
        return SignatureCheck("invalid", reason=str(error))
    fingerprint = key_fingerprint(block["public_key"])
    common = {"public_key_id": block["public_key_id"], "public_key": block["public_key"], "fingerprint": fingerprint}
    try:
        message = canonical_manifest_bytes(manifest)
    except SigningError as error:
        return SignatureCheck("invalid", reason=str(error), **common)
    if not verify(bytes.fromhex(block["public_key"]), message, bytes.fromhex(block["signature"])):
        return SignatureCheck("invalid", reason="签名与清单内容不符（内容可能被改动，或签名来自另一把密钥）", **common)
    decision = trust_decision(registry, block)
    return SignatureCheck(decision.status, trust=decision, cryptographically_valid=True, **common)


def source_signature_fields(check):
    """Registry source fields for an installed pack (never for an invalid one)."""
    if check.refused:
        raise SigningError(check.refusal_reason())
    fields = {"signature_status": check.status}
    if check.status != "unsigned":
        fields["public_key_id"] = check.public_key_id
        fields["key_fingerprint"] = check.fingerprint
    return fields
