# Security policy

## Project status and safe use

This repository is an early development baseline. It has not been security-reviewed and is not suitable for production deployment or operational investigative decisions. The current Compose services are intended for local development only.

ChainGuard AI is intended to analyze public blockchain information. Do not submit wallet seed phrases, private keys, production credentials, personal data, or confidential case evidence to this repository or its issue tracker.

## Reporting a vulnerability

Please do not publish exploitable vulnerability details in a public issue. Use GitHub's private vulnerability reporting feature for this repository, if enabled. If private reporting is unavailable, contact the repository maintainers privately through an established channel and provide only the minimum detail needed to reproduce and assess the issue.

Reports should include affected commit/version, impact, prerequisites, and a safe reproduction description. Do not access or modify data that is not yours.

## Security boundaries

- `.env.example` contains placeholders only. Keep real secrets in ignored local files or a deployment secret manager.
- The Compose database ports bind to loopback for local development. Do not reuse this Compose configuration as a production deployment without a security review.
- Future API and UI controls must enforce authorization on the server, including tenant isolation.
- Future risk findings are triage signals, not proof of criminal activity or identity attribution.
