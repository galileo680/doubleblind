import hashlib
import secrets
from dataclasses import dataclass
from typing import Literal

PrincipalKind = Literal["agent", "human", "observer", "admin"]
TOKEN_BYTES = 32


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    session_id: str | None = None
    party_key: str | None = None

    @property
    def actor(self) -> str:
        if self.kind == "agent" and self.party_key:
            return self.party_key
        if self.kind == "human" and self.party_key:
            return f"human:{self.party_key}"
        return self.kind

    def can_see(self, visibility: str) -> bool:
        if self.kind in ("observer", "admin"):
            return True
        return visibility == "shared" or visibility == self.party_key
