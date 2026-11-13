# Contributing

Use Python 3.11+, create a virtual environment, and install with `python -m pip install -e .`.
Run `python -m unittest discover -s tests -v`, both dry-run commands in the README, and `python scripts/audit_public.py` before opening a PR.

Keep tests offline with synthetic identifiers. A parser regression should include the smallest redacted fixture that reproduces the problem, never a whole page containing session data or a personal stock response. Add new notification channels behind the `Notifier` interface and test both accepted/rejected responses plus no-network dry runs.

Catalog additions need an official Apple Store source. Preserve existing enabled switches. New discoveries should remain disabled. Unknown future SKUs must remain null. Never circumvent rate limits, challenges, or access controls.
