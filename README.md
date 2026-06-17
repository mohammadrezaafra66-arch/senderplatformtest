# Sender Platform Test

Self-hosted multi-channel messaging platform testbed for campaign orchestration, queueing, consent, audit, monitoring, and delivery hardening.

## Project status

- **Phase 6 completed** — testing, hardening, and release evidence in place
- **GitHub Actions passing** on `main`
- **Not production-deployed yet** — environment-specific review still required

## Main application path

All application code, tests, and Docker setup live under:

[`multi-messaging-platform/`](multi-messaging-platform/)

See [`multi-messaging-platform/README.md`](multi-messaging-platform/README.md) for setup, env vars, and feature details.

## Key documents

- [Phase 6 release evidence](docs/release/phase6_release_evidence.md)
- [Production readiness checklist](docs/release/production_readiness_checklist.md)

## Test commands

```bash
cd multi-messaging-platform
pytest
pytest -m stress
pytest -m chaos
```

Routine CI runs `pytest -m "not stress and not chaos"` (see `.github/workflows/tests.yml`).

## Important note

Phase 6 is test and hardening complete, but production deployment still requires environment-specific review.
