import hashlib
import hmac

from doubleblind_contract.signing import hmac_sha256_hex, sign, verify

SECRET = "whsec_test"
BODY = b'{"event":"shipment.status_changed","status":"DELIVERED"}'


def test_sign_uses_hmac_sha256_over_the_raw_body() -> None:
    expected = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()

    assert hmac_sha256_hex(SECRET, BODY) == expected
    assert sign(SECRET, BODY) == f"sha256={expected}"


def test_sign_follows_the_format_from_the_contract() -> None:
    assert sign(SECRET, BODY, "v1={hex}").startswith("v1=")
    assert sign(SECRET, BODY, "{hex}") == hmac_sha256_hex(SECRET, BODY)


def test_text_and_bytes_give_the_same_signature() -> None:
    assert sign(SECRET, BODY.decode()) == sign(SECRET.encode(), BODY)


def test_verify_accepts_only_the_right_signature() -> None:
    header = sign(SECRET, BODY)

    assert verify(SECRET, BODY, header)
    assert not verify("other-secret", BODY, header)
    assert not verify(SECRET, BODY + b" ", header)
    assert not verify(SECRET, BODY, header.upper())
    assert not verify(SECRET, BODY, None)
