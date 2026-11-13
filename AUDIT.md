# Publication audit

The public repository is a clean export of refactored source, not a copy of the legacy runtime directory or its history.

- All notification credentials, ZIP code, radius, store filters, and polling/reminder settings are environment-driven.
- `.env.example` contains blank credential/location fields and public service endpoints only.
- Legacy personal JSON configurations, private store-query snapshots, logs, verification reports, state, bytecode, and local absolute paths are excluded.
- HTTP failures omit request URLs and response bodies. Notification requests refuse redirects. Catalog fetches validate Apple HTTPS hosts on every redirect.
- CI uses synthetic fixtures and mocked pushes. Cloud caches contain authenticated encrypted state; the encryption key stays in GitHub Secrets.
- New catalog entries are disabled. Weekly updates require review and do not change an existing user's enabled choices.
- Actions use full commit pins and scoped permissions. Monitoring requires explicit `MONITOR_ENABLED=true`.

Run `python scripts/audit_public.py` against staged/tracked files before each publication. This scanner supplements manual review; it cannot guarantee that future contributions are secret-free.
