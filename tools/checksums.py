"""Print SHA-256 checksums for the version's wheel and source distribution."""

from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("version")
    args = parser.parse_args()
    paths = [
        args.directory / f"openaudiokit-{args.version}-py3-none-any.whl",
        args.directory / f"openaudiokit-{args.version}.tar.gz",
    ]
    for path in paths:
        digest = sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        print(f"{digest.hexdigest()}  {path.name}")


if __name__ == "__main__":
    main()
