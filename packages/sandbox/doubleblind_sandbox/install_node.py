import sys
import tarfile
import urllib.request
from pathlib import Path

TARGET = Path("/usr/local")


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
    install(f"v{sys.argv[1]}")
