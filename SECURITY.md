# Security Policy

## Supported Versions

This project is in active early development. Security fixes are applied to the
latest `main` branch and the most recent release.

| Version | Supported |
|---------|-----------|
| `main` (latest) | ✅ |
| `0.1.x` | ✅ |
| older | ❌ |

## Reporting a Vulnerability

**Please do not open a public issue for security vulnerabilities.**

Report privately via GitHub's
[Security Advisories](https://github.com/RishabhhG/codereview-agent/security/advisories/new).
Include:

- A description of the vulnerability and its impact.
- Steps to reproduce (proof-of-concept if possible).
- Affected files/endpoints and any suggested remediation.

We aim to acknowledge reports within **72 hours** and to provide a remediation
plan or fix timeline after triage. Please give us a reasonable window to address
the issue before any public disclosure.

## Handling secrets and credentials

This project touches several sensitive credentials. Keep them out of version
control and treat them as secrets:

- **`GEMINI_API_KEY`** — your Google Gemini API key.
- **`DATABASE_URL`** — may contain database credentials.
- **`GITHUB_WEBHOOK_SECRET`** — verifies inbound webhook authenticity.
- **GitHub App private key** (`GITHUB_PRIVATE_KEY_PATH`, a `.pem` file) — grants
  the app's permissions; treat it like a password.

Safeguards already in place:

- `.env`, `*.pem`, and `logs/` are listed in `.gitignore`.
- The webhook verifies the `X-Hub-Signature-256` HMAC before processing any
  payload (`routers/webhook.py`).

If you believe a secret has been committed to the repository history, rotate it
immediately (revoke the key / regenerate the webhook secret) and report it via
the private channel above.

## Scope and expectations

This is a developer tool that sends source code to the Google Gemini API for
embeddings and generation. Do **not** ingest repositories containing secrets or
data you are not permitted to share with a third-party LLM provider. Review your
provider's data-handling terms before indexing private or regulated code.
