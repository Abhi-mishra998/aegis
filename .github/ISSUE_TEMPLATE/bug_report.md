---
name: Bug report
about: Something isn't working correctly
title: '[BUG] '
labels: bug
assignees: Abhi-mishra998
---

## What happened

A clear description of the bug.

## Steps to reproduce

1. Start the stack: `docker compose up -d`
2. Run: `...`
3. See error

## Expected behavior

What should have happened?

## Actual behavior

What actually happened? Include error messages, stack traces, or log output.

## Environment

- OS: [e.g. macOS 14, Ubuntu 22.04]
- Docker version: [e.g. 25.0.3]
- Python version: [e.g. 3.11.8]
- Aegis version/commit: [e.g. v0.1.0 or git SHA]

## Which service is affected?

- [ ] Gateway (`:8000`)
- [ ] Identity (`:8001`)
- [ ] Registry (`:8002`)
- [ ] Policy/OPA (`:8003`)
- [ ] Audit (`:8004`)
- [ ] ARE (`:8005`)
- [ ] Billing (`:8006`)
- [ ] Behavior Engine (`:8007`)
- [ ] Decision Engine (`:8010`)
- [ ] Python SDK
- [ ] React UI
- [ ] Docker Compose / infra
- [ ] Other

## Logs

<details>
<summary>Relevant log output</summary>

```
paste logs here
```

</details>

## Additional context

Anything else that might help?
