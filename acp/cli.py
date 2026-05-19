"""Console script entry point — wired in pyproject.toml as `acp` + `acp-archive`.

Re-exports the SDK CLI so `pip install acp && acp validate ...` works without
exposing the internal `sdk.acp_client.cli` path.
"""
import sys

from sdk.acp_client.cli import main


def archive_entry() -> int:
    """Shorter alias: `acp-archive ...` is equivalent to `acp archive ...`."""
    return main(["archive", *sys.argv[1:]])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
