# Security Policy

## Supported Versions

Security fixes are generally applied to the latest `main` branch state.

## Reporting a Vulnerability

Please do **not** open public issues for sensitive vulnerabilities.

Instead, report security issues privately to the maintainers with:

- A clear description of the issue
- Steps to reproduce
- Expected impact
- Suggested remediation (if available)

You should receive an acknowledgement as soon as maintainers review the report.

## Security Best Practices for Deployments

- Set a strong `DATA_ENCRYPTION_KEY` in production.
- Protect `.env` and never commit secrets.
- Run behind TLS and restricted network access.
- Restrict Chatwoot/API tokens by environment and rotate periodically.
- Keep dependencies updated.

