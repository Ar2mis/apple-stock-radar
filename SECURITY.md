# Security

Do not put credentials, ZIP codes, webhook URLs, private topics, or raw API responses in issues or PRs. Report security vulnerabilities privately through GitHub private vulnerability reporting if enabled, or contact the repository owner without including credentials in the initial message.

Use environment variables or an ignored `.env`; encrypt cached cloud state with `STATE_ENCRYPTION_KEY`. No service credentials belong in `products.json`. Keep custom notification servers trusted. The monitor does not follow notification redirects. Apple catalog imports are restricted to HTTPS Apple Store URLs and parse data without executing scripts.

The public-file audit is a defense in depth check, not a proof that arbitrary future files contain no secrets. Review staged diffs and enable GitHub secret scanning/push protection. Rotate leaked credentials before rewriting history.
