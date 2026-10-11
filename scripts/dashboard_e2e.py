import argparse
import threading

import httpx
import uvicorn
from demo_workspace import REPO_ROOT
from scripted_party import ReportedGreen, ScriptedParty
from scripted_session import ReportedGreenE2E

from doubleblind_broker.app import create_app
from doubleblind_broker.autopilot import DefaultOptionSimulator
from doubleblind_broker.db import build_engine
from doubleblind_broker.service import BrokerService, BrokerSettings


def serve_broker(port: int) -> None:
    settings = BrokerSettings(
        presets_dir=REPO_ROOT / "demo" / "presets",
        autopilot=False,
        next_action_wait_s=2.0,
    )
    service = BrokerService(
        build_engine("sqlite://"), settings, ReportedGreenE2E(), DefaultOptionSimulator()
    )
    uvicorn.run(create_app(service=service), host="127.0.0.1", port=port, log_level="warning")


def play_parties(url: str, session_id: str, tokens: dict[str, str]) -> None:
    def play(party: str) -> None:
        headers = {"Authorization": f"Bearer {tokens[party]}"}
        with httpx.Client(base_url=url, timeout=60.0, headers=headers) as client:
            ScriptedParty(client, session_id, party, ReportedGreen(party)).run(600.0)

    threads = [threading.Thread(target=play, args=(party,)) for party in tokens]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def main() -> None:
    parser = argparse.ArgumentParser(description="Broker and scripted agents for dashboard tests")
    commands = parser.add_subparsers(dest="command", required=True)
    broker = commands.add_parser("broker", help="serve the broker and dashboard, humans manual")
    broker.add_argument("--port", type=int, default=8790)
    parties = commands.add_parser("parties", help="play both agents of a session with scripts")
    parties.add_argument("url")
    parties.add_argument("session_id")
    parties.add_argument("spokes_token")
    parties.add_argument("parcelly_token")
    arguments = parser.parse_args()
    if arguments.command == "broker":
        serve_broker(arguments.port)
    else:
        play_parties(
            arguments.url,
            arguments.session_id,
            {"spokes": arguments.spokes_token, "parcelly": arguments.parcelly_token},
        )


if __name__ == "__main__":
    main()
