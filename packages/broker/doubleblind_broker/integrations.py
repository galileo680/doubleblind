import json
import re
import secrets
import time
from typing import Any

from sqlmodel import Session, col, select

from doubleblind_broker.db import (
    AccountRecord,
    IntegrationRecord,
    IntegrationSessionRecord,
    MemberRecord,
    PartyRecord,
    PresetRecord,
    SessionRecord,
    open_session,
)
from doubleblind_broker.events import utc_now
from doubleblind_broker.presets import AllowlistSpec, Preset, PresetParty
from doubleblind_broker.recording import add_recorded_run
from doubleblind_broker.service import BrokerError, BrokerService
from doubleblind_broker.state_machine import TERMINAL_STATES, State
from doubleblind_broker.tokens import Principal, hash_token, new_token

INVITE_TTL_S = 7 * 24 * 3600
ONLINE_S = 30
AGENT_KEY_PREFIX = "dbk_"
ROLES = ("consumer", "provider")


def other_role(role: str) -> str:
    return "provider" if role == "consumer" else "consumer"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def online(member: MemberRecord) -> bool:
    return member.agent_seen_at is not None and time.time() - member.agent_seen_at < ONLINE_S


def record_scope(integration: IntegrationRecord, role: str, action: str) -> None:
    entry: dict[str, Any] = {
        "version": integration.scope_version,
        "action": action,
        "role": role,
        "at": utc_now(),
    }
    if action == "proposed":
        entry["text"] = integration.scope
    history = json.loads(integration.scope_history_json)
    integration.scope_history_json = json.dumps([*history, entry], ensure_ascii=False)


class Integrations:
    def __init__(self, service: BrokerService) -> None:
        self.service = service
        self.engine = service.engine

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
        links = db.exec(
            select(IntegrationSessionRecord).where(
                IntegrationSessionRecord.integration_id == integration.id
            )
        )
        runs = [(link, db.get(SessionRecord, link.session_id)) for link in links]
        return {
            "id": integration.id,
            "name": integration.name,
            "created_at": integration.created_at,
            "your_role": mine.role,
            "companies": companies,
            "invite_expires_at": integration.invite_expires_at,
            "scope": {
                "text": integration.scope,
                "version": integration.scope_version,
                "proposed_by": integration.scope_by,
                "status": integration.scope_status,
                "history": json.loads(integration.scope_history_json),
            },
            "agent_key_issued": mine.agent_key_hash is not None,
            "agents": {
                role: {
                    "online": online(member),
                    **(
                        {"repo": member.agent_repo, "commit": member.agent_commit}
                        if member is mine
                        else {}
                    ),
                }
                for role, member in members.items()
            },
            "sessions": sorted(
                (
                    {
                        "id": session.id,
                        "recorded": link.recorded,
                        "state": session.state,
                        "created_at": session.created_at,
                    }
                    for link, session in runs
                    if session is not None
                ),
                key=lambda run: run["created_at"],
                reverse=True,
            ),
        }

    def create(self, account_id: str, name: str, role: str, scope: str) -> dict[str, Any]:
        integration = IntegrationRecord(
            id=f"int_{secrets.token_hex(6)}",
            name=name.strip(),
            created_at=utc_now(),
            scope=scope.strip(),
            scope_by=role,
        )
        record_scope(integration, role, "proposed")
        with open_session(self.engine) as db:
            db.add(integration)
            db.add(MemberRecord(integration_id=integration.id, role=role, account_id=account_id))
            db.commit()
            return self.view(db, integration, account_id)

    def for_account(self, account_id: str) -> list[dict[str, Any]]:
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
                "scope": integration.scope,
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
        key = f"{AGENT_KEY_PREFIX}{new_token()}"
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

    def change_scope(
        self, account_id: str, integration_id: str, action: str, text: str = "", version: int = 0
    ) -> dict[str, Any]:
        with open_session(self.engine) as db:
            integration, member = self.membership(db, integration_id, account_id)
            if action == "proposed":
                integration.scope = text.strip()
                integration.scope_version += 1
                integration.scope_by = member.role
                integration.scope_status = "proposed"
            else:
                if len(self.members(db, integration_id)) < len(ROLES):
                    raise BrokerError(409, "PARTNER_MISSING", "Your partner has not joined yet.")
                if member.role == integration.scope_by:
                    raise BrokerError(
                        409, "OWN_PROPOSAL", "The other company answers your proposal."
                    )
                if integration.scope_status != "proposed":
                    raise BrokerError(409, "SCOPE_CLOSED", "There is no open scope proposal.")
                if action == "accepted" and version != integration.scope_version:
                    raise BrokerError(
                        409, "SCOPE_CHANGED", "The scope changed meanwhile. Read the new version."
                    )
                integration.scope_status = "agreed" if action == "accepted" else "declined"
            record_scope(integration, member.role, action)
            db.add(integration)
            db.commit()
            return self.view(db, integration, account_id)

    def require_scope(self, integration: IntegrationRecord) -> None:
        if integration.scope_status != "agreed":
            raise BrokerError(
                409, "SCOPE_NOT_AGREED", "Both companies must accept the scope first."
            )

    def start_demo(self, account_id: str, integration_id: str, speed: int) -> dict[str, str]:
        with open_session(self.engine) as db:
            integration, _ = self.membership(db, integration_id, account_id)
            if len(self.members(db, integration_id)) < len(ROLES):
                raise BrokerError(409, "PARTNER_MISSING", "Your partner has not joined yet.")
            self.require_scope(integration)
            session_id = add_recorded_run(db, speed)
            db.add(
                IntegrationSessionRecord(
                    session_id=session_id, integration_id=integration_id, recorded=True
                )
            )
            db.commit()
        return {"session_id": session_id}

    def live_preset(
        self, db: Session, integration: IntegrationRecord, members: dict[str, MemberRecord]
    ) -> Preset:
        parties: dict[str, PresetParty] = {}
        declared = [json.loads(member.synthetic_json) for member in members.values()]
        for role, member in members.items():
            account = db.get(AccountRecord, member.account_id)
            company = account.company if account else role
            key = slug(company)
            if not key[:1].isalpha() or key in {party.party for party in parties.values()}:
                key = f"{role}-{key}".strip("-")
            parties[role] = PresetParty(party=key, display_name=company)
        return Preset(
            name=f"live-{secrets.token_hex(6)}",
            contract_id=slug(integration.name) or "integration",
            consumer=parties["consumer"],
            provider=parties["provider"],
            scope=integration.scope,
            synthetic_allowlist=AllowlistSpec(
                phones=list(dict.fromkeys(item for data in declared for item in data["phones"])),
                emails=list(dict.fromkeys(item for data in declared for item in data["emails"])),
            ),
        )

    def live_session(self, db: Session, integration_id: str) -> SessionRecord | None:
        links = db.exec(
            select(IntegrationSessionRecord).where(
                IntegrationSessionRecord.integration_id == integration_id,
                col(IntegrationSessionRecord.recorded).is_(False),
            )
        )
        sessions = [db.get(SessionRecord, link.session_id) for link in links]
        return next(
            (
                session
                for session in sessions
                if session is not None and State(session.state) not in TERMINAL_STATES
            ),
            None,
        )

    def start_live(self, account_id: str, integration_id: str) -> dict[str, str]:
        with open_session(self.engine) as db:
            integration, _ = self.membership(db, integration_id, account_id)
            members = self.members(db, integration_id)
            if len(members) < len(ROLES):
                raise BrokerError(409, "PARTNER_MISSING", "Your partner has not joined yet.")
            self.require_scope(integration)
            if not all(online(member) for member in members.values()):
                raise BrokerError(
                    409,
                    "AGENTS_OFFLINE",
                    "Both agents must be online. Each company starts its own with doubleblind "
                    "connect.",
                )
            if self.live_session(db, integration_id) is not None:
                raise BrokerError(409, "RUN_IN_PROGRESS", "A live run is already in progress.")
            preset = self.live_preset(db, integration, members)
            db.add(PresetRecord(name=preset.name, preset_json=preset.model_dump_json()))
            db.commit()
        admin = Principal("admin")
        created = self.service.create_session(admin, integration.name, preset.name)
        with open_session(self.engine) as db:
            db.add(
                IntegrationSessionRecord(
                    session_id=created["session_id"], integration_id=integration_id
                )
            )
            db.commit()
        self.service.events.append(
            created["session_id"],
            "scope.agreed",
            "broker",
            {
                "scope": integration.scope,
                "version": integration.scope_version,
                "proposed_by": integration.scope_by,
            },
        )
        self.service.start(admin, created["session_id"])
        return {"session_id": created["session_id"]}

    def stop_live(self, account_id: str, integration_id: str) -> None:
        with open_session(self.engine) as db:
            _, member = self.membership(db, integration_id, account_id)
            session = self.live_session(db, integration_id)
            party = self.party(db, session.id, member.role) if session else None
        if session is None or party is None:
            raise BrokerError(409, "NO_RUN", "No live run is in progress.")
        self.service.cancel(session.id, Principal("human", session.id, party.party_key).actor)

    def agent(self, db: Session, key: str) -> MemberRecord:
        member = db.exec(
            select(MemberRecord).where(MemberRecord.agent_key_hash == hash_token(key))
        ).first()
        if member is None:
            raise BrokerError(401, "UNKNOWN_AGENT_KEY", "This agent key is not valid any more.")
        member.agent_seen_at = time.time()
        db.add(member)
        db.commit()
        return member

    def agent_hello(
        self, key: str, repo: str, commit: str, synthetic: dict[str, list[str]]
    ) -> dict[str, str]:
        with open_session(self.engine) as db:
            member = self.agent(db, key)
            member.agent_repo, member.agent_commit = repo, commit
            member.synthetic_json = json.dumps(synthetic)
            db.add(member)
            db.commit()
            integration = db.get(IntegrationRecord, member.integration_id)
            return {"integration": integration.name if integration else "", "role": member.role}

    def agent_session(self, key: str) -> dict[str, str] | None:
        with open_session(self.engine) as db:
            member = self.agent(db, key)
            session = self.live_session(db, member.integration_id)
            party = self.party(db, session.id, member.role) if session else None
        if session is None or party is None:
            return None
        return {"session_id": session.id, "party": party.party_key}

    def party(self, db: Session, session_id: str, role: str | None) -> PartyRecord | None:
        return db.exec(
            select(PartyRecord).where(
                PartyRecord.session_id == session_id, PartyRecord.role == role
            )
        ).first()

    def agent_principal(self, key: str, session_id: str) -> Principal:
        with open_session(self.engine) as db:
            member = self.agent(db, key)
            link = db.get(IntegrationSessionRecord, session_id)
            party = None
            if link and not link.recorded and link.integration_id == member.integration_id:
                party = self.party(db, session_id, member.role)
        if party is None:
            raise BrokerError(403, "FORBIDDEN", "This agent key cannot act in this session.")
        return Principal("agent", session_id, party.party_key)

    def session_principal(self, account_id: str, session_id: str) -> Principal:
        with open_session(self.engine) as db:
            link = db.get(IntegrationSessionRecord, session_id)
            members = self.members(db, link.integration_id) if link else {}
            role = next(
                (role for role, member in members.items() if member.account_id == account_id), None
            )
            party = self.party(db, session_id, role)
        if party is None:
            raise BrokerError(403, "FORBIDDEN", "Your company does not take part in this session.")
        return Principal("human", session_id, party.party_key)
