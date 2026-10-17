import json
import sys
import tarfile
import urllib.request
from pathlib import Path

INDEX_URL = "https://nodejs.org/dist/index.json"
TARGET = Path("/usr/local")


def latest_release(major: str) -> str:
    with urllib.request.urlopen(INDEX_URL, timeout=60) as response:
        releases = json.load(response)
    for release in releases:
        if release["version"].startswith(f"v{major}."):
            return str(release["version"])
    raise SystemExit(f"no Node.js release for major version {major}")


def install(version: str) -> None:
    name = f"node-{version}-linux-x64"
    archive = Path(f"/tmp/{name}.tar.xz")
    urllib.request.urlretrieve(f"https://nodejs.org/dist/{version}/{name}.tar.xz", archive)
    with tarfile.open(archive, "r:xz") as bundle:
        bundle.extractall("/tmp", filter="tar")
    source = Path("/tmp") / name
    for folder in ("bin", "include", "lib", "share"):
        for item in (source / folder).iterdir():
            destination = TARGET / folder / item.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                continue
            item.rename(destination)


if __name__ == "__main__":
    install(latest_release(sys.argv[1] if len(sys.argv) > 1 else "24"))
