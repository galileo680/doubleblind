import hashlib
import hmac

DEFAULT_SIGNATURE_FORMAT = "sha256={hex}"


def as_bytes(value: str | bytes) -> bytes:
    return value.encode("utf-8") if isinstance(value, str) else value


def hmac_sha256_hex(secret: str | bytes, raw_body: str | bytes) -> str:
    return hmac.new(as_bytes(secret), as_bytes(raw_body), hashlib.sha256).hexdigest()


def sign(
    secret: str | bytes,
    raw_body: str | bytes,
    signature_format: str = DEFAULT_SIGNATURE_FORMAT,
) -> str:
    return signature_format.replace("{hex}", hmac_sha256_hex(secret, raw_body))


def verify(
    secret: str | bytes,
    raw_body: str | bytes,
    header_value: str | None,
    signature_format: str = DEFAULT_SIGNATURE_FORMAT,
) -> bool:
    if header_value is None:
        return False
    expected = sign(secret, raw_body, signature_format)
    return hmac.compare_digest(as_bytes(header_value), as_bytes(expected))
