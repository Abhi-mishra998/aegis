# Publishing the Aegis SDKs to PyPI

Five packages ship from this repo:

| Package          | Source                                | PyPI status         |
|------------------|---------------------------------------|---------------------|
| `aegis-anthropic`| `integrations/aegis-anthropic/`       | live, bump to 1.0.1 |
| `aegis-openai`   | `integrations/aegis-openai/`          | live, bump to 1.0.1 |
| `aegis-langchain`| `integrations/aegis-langchain/`       | live, bump to 1.0.1 |
| `aegis-bedrock`  | `integrations/aegis-bedrock/`         | **first publish**   |
| `aegis-aevf`     | `tools/aegis_verify/` (pyproject)     | live (1.0.0)        |

## What changed (2026-06-15)

Pip-install audit found four cosmetic gaps on the integrations SDKs:

1. No `license` field → PyPI showed "UNKNOWN."
2. No `url` / `project_urls` → no Homepage / Repo / Bug Tracker links.
3. Only a generic `Programming Language :: Python :: 3` classifier → PyPI
   didn't show 3.10 / 3.11 / 3.12 / 3.13 explicitly.
4. `aegis-bedrock` was buildable locally but had never been uploaded to PyPI,
   so `pip install aegis-bedrock` failed.

Fix: setup.py upgraded across all four SDKs with `license="Apache-2.0"`,
`url`, `project_urls`, expanded keywords, and per-version classifiers.
LICENSE file copied into `aegis-bedrock/`. Versions on the three live
SDKs bumped 1.0.0 → 1.0.1; `aegis-bedrock` stays at 1.0.0 for its first
upload.

## Building locally

```bash
python -m pip install build twine

for sdk in aegis-anthropic aegis-openai aegis-langchain aegis-bedrock; do
  rm -rf integrations/$sdk/dist
  python -m build --outdir integrations/$sdk/dist integrations/$sdk
done

# Validate before upload (catches missing README, broken markup, etc.):
for sdk in aegis-anthropic aegis-openai aegis-langchain aegis-bedrock; do
  twine check integrations/$sdk/dist/*
done
```

All four currently pass `twine check`.

## Uploading

PyPI requires a maintainer token. The repo doesn't ship credentials;
put yours in `~/.pypirc` or pass via `TWINE_USERNAME=__token__
TWINE_PASSWORD=pypi-…`.

**Test first on TestPyPI:**

```bash
twine upload --repository testpypi integrations/aegis-bedrock/dist/*
pip install --index-url https://test.pypi.org/simple/ aegis-bedrock
```

**Then production:**

```bash
# aegis-bedrock — first publish
twine upload integrations/aegis-bedrock/dist/*

# the three existing SDKs — bumped 1.0.0 → 1.0.1
twine upload integrations/aegis-anthropic/dist/*
twine upload integrations/aegis-openai/dist/*
twine upload integrations/aegis-langchain/dist/*
```

## Verifying after upload

```bash
python -m venv /tmp/aegis-pypi-smoke
/tmp/aegis-pypi-smoke/bin/pip install \
  aegis-anthropic aegis-openai aegis-langchain aegis-bedrock aegis-aevf

/tmp/aegis-pypi-smoke/bin/python -c "
import aegis_anthropic, aegis_openai, aegis_langchain, aegis_bedrock, aegis_verify
print('all 5 import OK')
"

/tmp/aegis-pypi-smoke/bin/aegis-verify --bundle docs/AEVF/reference-bundle-2026-06.json
# expected: *** PASS *** every signature, hash chain, and Merkle root verifies.
```

## `aegis-aevf` — already shipped

`tools/aegis_verify/` already uses a modern `pyproject.toml` with the
full metadata block. It does not need a re-publish for the 2026-06-15
audit — only the four `integrations/` SDKs do.
