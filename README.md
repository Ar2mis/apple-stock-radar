# Apple Stock Radar

[![CI](https://github.com/Ar2mis/apple-stock-radar/actions/workflows/ci.yml/badge.svg)](https://github.com/Ar2mis/apple-stock-radar/actions/workflows/ci.yml)
[![Catalog](https://github.com/Ar2mis/apple-stock-radar/actions/workflows/update-catalog.yml/badge.svg)](https://github.com/Ar2mis/apple-stock-radar/actions/workflows/update-catalog.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

An extensible, unofficial **US Apple Store pickup monitor** for iPhone, Mac, iPad, Apple Watch, and AirPods. Run it locally or on GitHub Actions. Keep your location and notification credentials out of the repository.

- A readable `products.json`, grouped by category → model → storage/color variant.
- Bark, Telegram, Pushover, Discord, and ntfy notifications.
- Immediate alerts on fresh available results; repeat after 180 seconds while stock remains available. No out-of-stock messages. Failed queries never count as sold out.
- One optional startup test locally. Cloud checks send startup tests only when explicitly requested.
- Official Apple URL importer and weekly catalog PRs; new variants are disabled for review.
- Encrypted cloud state, persistent cooldowns, bounded HTTPS requests, and secret-safe errors.

## Three-minute quickstart

Requires Python 3.11 or later. From a checkout of this repository:

```sh
python3 -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
cp .env.example .env
```

1. Edit `.env`: set `ZIP_CODE`, `NOTIFY_CHANNELS`, and the credentials for your chosen channel. Adjust `SEARCH_RADIUS_MILES` if needed. Never commit `.env`.
2. In `products.json`, set `enabled: true` on the variants you want. Category and model switches must also be true. **All shipped variants are disabled by default.**
3. Check the configuration, then start:

```sh
python -m apple_stock_radar --validate-catalog
python -m apple_stock_radar --dry-run       # live lookup; no pushes or state writes
python -m apple_stock_radar                # Ctrl+C stops monitoring
```

Offline smoke tests need no credentials or location:

```sh
python -m apple_stock_radar --offline --dry-run
python -m apple_stock_radar --notification-dry-run
```

`STARTUP_NOTIFICATION=true` sends one clearly labeled test on local continuous startup. It does not require a phone verification code. A service accepting a push does not prove that a phone received it. To send a test explicitly: `python -m apple_stock_radar --test-notification`.

## Notification configuration

Set `NOTIFY_CHANNELS` to a comma-separated list. Only selected channels require credentials.

| Channel | Required environment variables / GitHub Secrets | Optional settings |
| --- | --- | --- |
| Bark | `BARK_KEY` | `BARK_SERVER` |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | — |
| Pushover | `PUSHOVER_API_TOKEN`, `PUSHOVER_USER_KEY` | — |
| Discord | `DISCORD_WEBHOOK_URL` | — |
| ntfy | `NTFY_TOPIC` | `NTFY_TOKEN`, `NTFY_SERVER` |

Bark uses the **device key**, the first path segment after `https://api.day.app/` in the app's test URL. Keep the entire URL private too. Custom Bark/ntfy servers must use HTTPS. Discord messages disable mentions. Pushover account/client pricing is determined by Pushover.

All user settings are documented in [.env.example](.env.example). `CHECK_INTERVAL_SECONDS` controls local polling. `REPEAT_NOTIFICATION_SECONDS=180` repeats each available product/store/channel no sooner than three minutes after a successful push, on the next fresh available result. `0` means once per stock episode. Missing distance data fails closed when a radius is set; use `SEARCH_RADIUS_MILES=0` to accept all returned stores. `STORE_NAMES` optionally filters store-name substrings.

## Run in GitHub Actions

1. Fork this repository, or use your own copy. Enable the product variants you want and commit that catalog.
2. Open **Settings → Secrets and variables → Actions**. Add these repository **Secrets**:
   - `ZIP_CODE` — your US ZIP code.
   - `STATE_ENCRYPTION_KEY` — a fresh Fernet key, generated locally with the command below.
   - The credential secrets from the channel table above (for Bark, just `BARK_KEY`).
   - Optional: `STORE_NAMES` for private store filtering.
3. Add repository **Variables**: `MONITOR_ENABLED=true` and `NOTIFY_CHANNELS=bark` (or your selected channels). Optional variables: `SEARCH_RADIUS_MILES`, `REPEAT_NOTIFICATION_SECONDS`, `CHECK_INTERVAL_SECONDS`, `BARK_SERVER`, `NTFY_SERVER`.
4. Run **Actions → Stock Monitor → Run workflow**. Select `test_notification` to send just one test; leave it false for a stock check.

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep the encryption key in Secrets. Only encrypted state enters the Actions cache; it contains delivery timestamps and cooldowns. No `.env`, raw responses, logs, or location records are uploaded as artifacts. A keyed cache scope separates different location/filter settings. State is best-effort: cache eviction or process interruption can cause another alert. Use one runner per state file and keep the workflow concurrency setting.

The workflow runs every **five minutes**, offset from the top of the hour. GitHub's shortest schedule is five minutes; runs can be delayed/dropped, and schedules on inactive public repositories can be disabled after 60 days. Three-minute repeats therefore apply approximately to local mode; Actions repeats happen on the next scheduled successful check. [GitHub scheduling documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

Cloud monitoring stays disabled until `MONITOR_ENABLED=true`. Stop cloud checks by setting it to `false` or disabling the workflow. GitHub-hosted IPs may be blocked by Apple; the workflow respects 403/541 cooldowns and 429 `Retry-After`, including across runs. It never bypasses access controls. A scheduled workflow is not a real-time stock feed.

## Add Apple products

Paste a specific Apple Store URL:

```sh
python add_product.py 'https://www.apple.com/shop/product/YOUR_OFFICIAL_PART/A'
# For a multi-variant model page, list candidates first:
python add_product.py 'https://www.apple.com/shop/buy-iphone/iphone-18-pro'
# Then select a returned part, or import the whole page disabled:
python add_product.py 'https://www.apple.com/shop/buy-iphone/iphone-18-pro' --all
```

The importer reads named product records, JSON-LD offers, and Mac/Watch selector data. It verifies the part against Apple page data rather than assigning the first number found. Ambiguous pages require `--part OFFICIAL_PART/A` or `--all`. Add `--enable` only to enable new entries immediately. Existing toggles are preserved.

The catalog is a **discovered snapshot, not an exhaustive compatibility guarantee**. Some storage/color details are `null` when Apple does not provide an unambiguous mapping. Mac custom configurations and Apple Watch case/band combinations may require a more specific configured URL; confirm the selected configuration before enabling it. SKU existence is not evidence of stock.

Upcoming products are supported with `status: "upcoming"`, `part_number: null`, and `enabled: false`. Add only publicly announced models with an official source. Unknown or rumored future part numbers are never invented. Once Apple publishes named purchase metadata, discovery adds the official variants disabled for review.

## Weekly catalog updates

[update-catalog.yml](.github/workflows/update-catalog.yml) crawls the public category pages in `catalog_sources.json`, follows newly linked model pages, and opens/updates one PR on `automation/apple-catalog`. All additions are disabled, existing selections are preserved, and nothing is auto-merged. The crawl is bounded to 40 pages and stops on access denial/rate limiting; incomplete requests do not write a partial update.

In **Settings → Actions → General → Workflow permissions**, enable **Allow GitHub Actions to create and approve pull requests** for automated PR creation. No personal access token is required. The workflow grants only its job `contents: write` and `pull-requests: write`; it validates and tests the proposed catalog before opening a PR. PRs created with `GITHUB_TOKEN` may not trigger a separate CI run automatically; the updater already runs those checks.

```sh
python update_catalog.py
python -m apple_stock_radar --validate-catalog
```

Store layouts can change. Discovery is best-effort and cannot promise every variant or detect unpublished products. Review PRs before merging.

## Development and security

```sh
python -m unittest discover -s tests -v
python scripts/audit_public.py
```

Modules: `config`, `catalog`, `http`, `monitor`, `notifications`, `state`, and `cli`. Tests use synthetic product/store data and mocked notification services. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [AUDIT.md](AUDIT.md).

The repository uses a fresh Git history and excludes personal legacy configs, logs, verification reports, stock snapshots, cached bytecode, and notification credentials. Never publish your `.env` or unencrypted local state. Rotate a credential immediately if it is ever committed. Errors deliberately omit URLs and service response bodies, since those may contain tokens.

Apple's pickup endpoint is an undocumented website interface and can change. This project only checks availability and sends notifications; it does not log in, reserve, or buy products. Not affiliated with Apple. **MIT licensed.**
