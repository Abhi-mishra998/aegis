"""Sprint 5 — Package the OWASP corpus into a deterministic public bundle.

Produces a tarball containing:

    corpus.jsonl       — verbatim copy of the in-repo corpus
    benchmark.md       — the public benchmark doc
    LICENSE            — Apache 2.0 (matches the parent repo)
    README.md          — minimal "how to read this archive" pointer
    MANIFEST.json      — file → sha256 lookup so consumers can verify

Run from the repo root:

    python3 -m tests.eval.corpus.export --out=/tmp/aegis-benchmark.tar.gz

The bundle is BYTE-DETERMINISTIC: same corpus + same docs → same sha256.
We sort entries by filename and zero the mtime/uid/gid so two operators
building the same release produce identical archives.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORPUS_PATH = ROOT / "tests" / "eval" / "corpus" / "corpus.jsonl"
BENCHMARK_PATH = ROOT / "docs" / "benchmark.md"
LICENSE_PATH = ROOT / "LICENSE"


_README = """Aegis OWASP Detection Benchmark — public bundle
================================================

Files
-----
corpus.jsonl    one JSON object per line; the labelled attack + benign cases
benchmark.md    methodology, slice counts, how to run it yourself
LICENSE         Apache 2.0
MANIFEST.json   sha256 of every file in this archive

This bundle is byte-deterministic: same code → same sha256. If two
operators produce different archives, the inputs are different — file a
ticket against the source repo. See benchmark.md §5 for context.
"""


def _read_bytes(path: Path) -> bytes:
    if not path.exists():
        raise FileNotFoundError(f"missing input: {path}")
    return path.read_bytes()


def _add_entry(
    tar: tarfile.TarFile, name: str, data: bytes, manifest: dict
) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mtime = 0  # deterministic
    info.uid = 0
    info.gid = 0
    info.uname = "aegis"
    info.gname = "aegis"
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))
    manifest[name] = hashlib.sha256(data).hexdigest()


def build_bundle(out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    corpus = _read_bytes(CORPUS_PATH)
    benchmark = _read_bytes(BENCHMARK_PATH)
    license_text = _read_bytes(LICENSE_PATH) if LICENSE_PATH.exists() else (
        b"Apache License 2.0\n"
        b"https://www.apache.org/licenses/LICENSE-2.0\n"
    )

    manifest: dict[str, str] = {}
    # Use an explicit GzipFile(mtime=0) so the gzip header is deterministic.
    # tarfile.open(..., "w:gz") writes the current wall-clock time into the
    # gzip header which breaks reproducible builds.
    with open(out_path, "wb") as raw, gzip.GzipFile(
        filename="", mode="wb", fileobj=raw, mtime=0,
    ) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        for name, payload in sorted(
            [
                ("corpus.jsonl", corpus),
                ("benchmark.md", benchmark),
                ("LICENSE", license_text),
                ("README.md", _README.encode("utf-8")),
            ]
        ):
            _add_entry(tar, name, payload, manifest)

        manifest_blob = json.dumps(
            {"files": manifest, "format_version": "1"}, indent=2, sort_keys=True
        ).encode("utf-8")
        _add_entry(tar, "MANIFEST.json", manifest_blob, manifest={})

    return out_path


def _cli() -> int:
    parser = argparse.ArgumentParser(prog="tests.eval.corpus.export")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("dist/aegis-benchmark.tar.gz"),
        help="Output tarball path",
    )
    args = parser.parse_args()
    out = build_bundle(args.out)
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
    print("sha256:", hashlib.sha256(out.read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
