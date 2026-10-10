import os

import uvicorn

from doubleblind_broker.app import create_app


def main() -> None:
    uvicorn.run(
        create_app(),
        host=os.environ.get("BROKER_HOST", "127.0.0.1"),
        port=int(os.environ.get("BROKER_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
