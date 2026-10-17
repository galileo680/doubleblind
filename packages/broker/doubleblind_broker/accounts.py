import hashlib
import hmac
import secrets
import time
from typing import Any

from sqlalchemy import Engine
from sqlmodel import select

from doubleblind_broker.db import AccountRecord, LoginRecord, open_session
from doubleblind_broker.events import utc_now
from doubleblind_broker.service import BrokerError
from doubleblind_broker.tokens import hash_token, new_token

LOGIN_TTL_S = 14 * 24 * 3600
MAX_FAILURES = 5
FAILURE_WINDOW_S = 15 * 60


def hash_password(password: str, salt: bytes) -> str:
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}${digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    salt = bytes.fromhex(stored.split("$")[0])
    return hmac.compare_digest(hash_password(password, salt), stored)


UNKNOWN_ACCOUNT_HASH = hash_password(secrets.token_hex(16), secrets.token_bytes(16))


def account_view(account: AccountRecord) -> dict[str, Any]:
    return {"id": account.id, "email": account.email, "company": account.company}


class Accounts:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.failures: dict[str, list[float]] = {}

    def signup(self, email: str, password: str, company: str) -> tuple[dict[str, Any], str]:
        email = email.strip().lower()
        with open_session(self.engine) as db:
            if db.exec(select(AccountRecord).where(AccountRecord.email == email)).first():
                raise BrokerError(409, "EMAIL_TAKEN", "An account with this e-mail already exists.")
            account = AccountRecord(
                id=f"acc_{secrets.token_hex(6)}",
                email=email,
                company=company.strip(),
                password_hash=hash_password(password, secrets.token_bytes(16)),
                created_at=utc_now(),
            )
            db.add(account)
            db.commit()
        return account_view(account), self.open_login(account.id)

    def login(self, email: str, password: str) -> tuple[dict[str, Any], str]:
        email = email.strip().lower()
        recent = self.recent_failures(email)
        if len(recent) >= MAX_FAILURES:
            minutes = int(recent[0] + FAILURE_WINDOW_S - time.monotonic()) // 60 + 1
            raise BrokerError(
                429,
                "TOO_MANY_ATTEMPTS",
                f"Too many failed sign-ins. Try again in {minutes} min.",
            )
        with open_session(self.engine) as db:
            account = db.exec(select(AccountRecord).where(AccountRecord.email == email)).first()
        stored = account.password_hash if account else UNKNOWN_ACCOUNT_HASH
        if not password_matches(password, stored) or account is None:
            self.failures[email] = [*recent, time.monotonic()]
            raise BrokerError(401, "BAD_CREDENTIALS", "Wrong e-mail or password.")
        self.failures.pop(email, None)
        return account_view(account), self.open_login(account.id)

    def recent_failures(self, email: str) -> list[float]:
        cutoff = time.monotonic() - FAILURE_WINDOW_S
        recent = [moment for moment in self.failures.pop(email, []) if moment > cutoff]
        if recent:
            self.failures[email] = recent
        return recent

    def open_login(self, account_id: str) -> str:
        token = new_token()
        with open_session(self.engine) as db:
            db.add(
                LoginRecord(
                    token_hash=hash_token(token),
                    account_id=account_id,
                    expires_at=int(time.time()) + LOGIN_TTL_S,
                )
            )
            db.commit()
        return token

    def account(self, token: str | None) -> dict[str, Any]:
        if token:
            with open_session(self.engine) as db:
                login = db.get(LoginRecord, hash_token(token))
                if login is not None and login.expires_at > time.time():
                    account = db.get(AccountRecord, login.account_id)
                    if account is not None:
                        return account_view(account)
        raise BrokerError(401, "SIGNED_OUT", "Sign in first.")

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with open_session(self.engine) as db:
            login = db.get(LoginRecord, hash_token(token))
            if login is not None:
                db.delete(login)
                db.commit()
