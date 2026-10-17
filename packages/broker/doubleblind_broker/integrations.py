import secrets
import time
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, select

from doubleblind_broker.db import (
    AccountRecord,
    IntegrationRecord,
    IntegrationSessionRecord,
    MemberRecord,
    PartyRecord,
    open_session,
)
from doubleblind_broker.events import utc_now
from doubleblind_broker.service import BrokerError
from doubleblind_broker.tokens import Principal, hash_token, new_token

INVITE_TTL_S = 7 * 24 * 3600
ROLES = ("consumer", "provider")


def other_role(role: str) -> str:
    return "provider" if role == "consumer" else "consumer"


class Integrations:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def members(self, db: Session, integration_id: str) -> dict[str, MemberRecord]:
        rows = db.exec(select(MemberRecord).where(MemberRecord.integration_id == integration_id))
        return {member.role: member for member in rows}

    def membership(
        self, db: Session, integration_id: str, account_id: str
    ) -> tuple[IntegrationRecord, MemberRecord]:
        integration = db.get(IntegrationRecord, integration_id)
        member = next(
            (
                member
                for member in self.members(db, integration_id).values()
                if member.account_id == account_id
            ),
            None,
        )
        if integration is None or member is None:
            raise BrokerError(404, "NO_SUCH_INTEGRATION", "This integration does not exist.")
        return integration, member

    def view(self, db: Session, integration: IntegrationRecord, account_id: str) -> dict[str, Any]:
        members = self.members(db, integration.id)
        mine = next(member for member in members.values() if member.account_id == account_id)
        companies: dict[str, str | None] = {role: None for role in ROLES}
        for role, member in members.items():
            account = db.get(AccountRecord, member.account_id)
            companies[role] = account.company if account else None
        return {
            "id": integration.id,
            "name": integration.name,
            "created_at": integration.created_at,
            "your_role": mine.role,
            "companies": companies,
            "invite_expires_at": integration.invite_expires_at,
            "agent_key_issued": mine.agent_key_hash is not None,
        }

    def create(self, account_id: str, name: str, role: str) -> dict[str, Any]:
        integration = IntegrationRecord(
            id=f"int_{secrets.token_hex(6)}", name=name.strip(), created_at=utc_now()
        )
        with open_session(self.engine) as db:
            db.add(integration)
            db.add(MemberRecord(integration_id=integration.id, role=role, account_id=account_id))
            db.commit()
            return self.view(db, integration, account_id)

    def list(self, account_id: str) -> list[dict[str, Any]]:
        with open_session(self.engine) as db:
            rows = db.exec(select(MemberRecord).where(MemberRecord.account_id == account_id))
            integrations = [db.get(IntegrationRecord, row.integration_id) for row in rows]
            return sorted(
                (self.view(db, item, account_id) for item in integrations if item is not None),
                key=lambda view: view["created_at"],
                reverse=True,
            )

    def get(self, account_id: str, integration_id: str) -> dict[str, Any]:
        with open_session(self.engine) as db:
            integration, _ = self.membership(db, integration_id, account_id)
            return self.view(db, integration, account_id)

    def renew_invite(self, account_id: str, integration_id: str) -> dict[str, Any]:
        with open_session(self.engine) as db:
            integration, _ = self.membership(db, integration_id, account_id)
            if len(self.members(db, integration_id)) == len(ROLES):
                raise BrokerError(409, "PARTNER_JOINED", "Your partner has already joined.")
            token = new_token()
            integration.invite_token_hash = hash_token(token)
            integration.invite_expires_at = int(time.time()) + INVITE_TTL_S
            db.add(integration)
            db.commit()
            return {**self.view(db, integration, account_id), "invite_token": token}

    def invited(self, db: Session, token: str) -> IntegrationRecord:
        integration = db.exec(
            select(IntegrationRecord).where(
                IntegrationRecord.invite_token_hash == hash_token(token)
            )
        ).first()
        if integration is None or integration.invite_expires_at is None:
            raise BrokerError(
                404,
                "INVITE_INVALID",
                "This invite link is not valid. It was used already or replaced by a newer one.",
            )
        if integration.invite_expires_at < time.time():
            raise BrokerError(
                410,
                "INVITE_EXPIRED",
                "This invite link has expired. Ask your partner for a new one.",
            )
        return integration

    def invite(self, token: str) -> dict[str, Any]:
        with open_session(self.engine) as db:
            integration = self.invited(db, token)
            inviter = next(iter(self.members(db, integration.id).values()))
            account = db.get(AccountRecord, inviter.account_id)
            return {
                "name": integration.name,
                "invited_by": account.company if account else None,
                "your_role": other_role(inviter.role),
                "expires_at": integration.invite_expires_at,
            }

    def accept(self, account_id: str, token: str) -> dict[str, Any]:
        with open_session(self.engine) as db:
            integration = self.invited(db, token)
            inviter = next(iter(self.members(db, integration.id).values()))
            if inviter.account_id == account_id:
                raise BrokerError(
                    409,
                    "OWN_INVITE",
                    "This is your own invite. Send the link to your partner company.",
                )
            db.add(
                MemberRecord(
                    integration_id=integration.id,
                    role=other_role(inviter.role),
                    account_id=account_id,
                )
            )
            integration.invite_token_hash = None
            integration.invite_expires_at = None
            db.add(integration)
            db.commit()
            return self.view(db, integration, account_id)

    def issue_agent_key(self, account_id: str, integration_id: str) -> dict[str, str]:
        key = f"dbk_{new_token()}"
        with open_session(self.engine) as db:
            _, member = self.membership(db, integration_id, account_id)
            member.agent_key_hash = hash_token(key)
            db.add(member)
            db.commit()
        return {"agent_key": key}

    def revoke_agent_key(self, account_id: str, integration_id: str) -> None:
        with open_session(self.engine) as db:
            _, member = self.membership(db, integration_id, account_id)
            member.agent_key_hash = None
            db.add(member)
            db.commit()

    def session_principal(self, account_id: str, session_id: str) -> Principal:
        with open_session(self.engine) as db:
            link = db.get(IntegrationSessionRecord, session_id)
            members = self.members(db, link.integration_id) if link else {}
            role = next(
                (role for role, member in members.items() if member.account_id == account_id), None
            )
            party = db.exec(
                select(PartyRecord).where(
                    PartyRecord.session_id == session_id, PartyRecord.role == role
                )
            ).first()
        if party is None:
            raise BrokerError(403, "FORBIDDEN", "Your company does not take part in this session.")
        return Principal("human", session_id, party.party_key)
