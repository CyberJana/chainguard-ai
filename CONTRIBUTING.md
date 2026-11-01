# Contributing

ChainGuard AI is being built in explicit phases. Keep changes scoped to the current implementation phase, and do not describe planned behavior as working functionality.

## Development expectations

- Read the current implementation status in `README.md` and the trust boundaries in `docs/architecture.md`.
- Keep live provider observations, synthetic demonstration data, derived risk findings, and analyst conclusions clearly distinguishable.
- Preserve source and retrieval provenance for blockchain and intelligence data.
- Enforce access controls on the server and scope data access to the authenticated organization.
- Never introduce private-key or seed-phrase handling.
- Add focused tests for behavior and authorization changes. Record the exact commands run and their outcomes; do not claim checks that were not executed.
- Pin direct application dependencies and commit a generated transitive lockfile before claiming reproducible builds.
- Do not commit `.env`, secrets, credentials, real investigative evidence, or generated local datasets.

## Current development setup

The current Compose file starts PostgreSQL and Redis only. Follow the Windows setup instructions in `README.md`. API and frontend setup instructions will be added with their respective implementation phases.

## Pull requests

Explain the behavior changed, security/privacy impact, tests actually run, and any provider credentials or infrastructure prerequisites. Include screenshots only for implemented UI behavior.
