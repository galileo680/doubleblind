import argparse
import json

from sqlmodel import SQLModel, select

from doubleblind_broker.db import SessionRecord, build_engine, open_session
from doubleblind_broker.recording import RECORDED_RUN, TABLES

SECRET_FIELDS = {
    "observer_token_hash",
    "public_token",
    "secrets_json",
    "agent_token_hash",
    "human_token_hash",
}


def generated(row: SQLModel) -> set[str]:
    return {key for key in ("id", "seq") if isinstance(getattr(row, key, None), int)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Save a certified session as the recorded run")
    parser.add_argument("database", help="SQLite file of the broker that ran the session")
    parser.add_argument("session_id")
    arguments = parser.parse_args()
    engine = build_engine(f"sqlite:///{arguments.database}")
    tables = {}
    with open_session(engine) as db:
        for model in TABLES:
            owner = getattr(model, "id" if model is SessionRecord else "session_id")
            rows = db.exec(select(model).where(owner == arguments.session_id)).all()
            tables[model.__tablename__] = [
                row.model_dump(exclude=SECRET_FIELDS | generated(row)) for row in rows
            ]
    if [row["state"] for row in tables["sessions"]] != ["CERTIFIED"]:
        raise SystemExit(f"{arguments.session_id} is not a certified session")
    RECORDED_RUN.write_text(json.dumps(tables, ensure_ascii=False), encoding="utf-8")
    print(f"saved {sum(map(len, tables.values()))} rows to {RECORDED_RUN}")


if __name__ == "__main__":
    main()
