#!/usr/bin/env python3
"""Scan tracked text, without printing matching secret values."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    r'gh[pousr]_[A-Za-z0-9]{30,}', r'github_pat_[A-Za-z0-9_]{30,}',
    r'https://(?:discord\.com|discordapp\.com)/api/webhooks/\d+/[A-Za-z0-9_-]{20,}',
    r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b',
    r'https://api\.day\.app/[A-Za-z0-9_-]{10,}',
    r'/Users/[A-Za-z0-9._-]+/',
]
FORBIDDEN = {'.env', 'state.json', 'status.json', 'monitor.log', 'verification.json', 'verification-stock.json'}


def main():
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True)
    failures = []
    for name in result.stdout.decode().split('\0'):
        if not name:
            continue
        path = Path(name)
        if path.name in FORBIDDEN or '.state' in path.parts or '__pycache__' in path.parts:
            failures.append((name, 'private runtime file'))
        data = (ROOT / path).read_text(encoding='utf-8', errors='replace')
        if any(re.search(pattern, data) for pattern in PATTERNS):
            failures.append((name, 'possible embedded secret or local path'))
    example = ROOT / '.env.example'
    for line in example.read_text().splitlines():
        key, sep, value = line.partition('=')
        if sep and any(x in key for x in ('TOKEN', 'KEY', 'WEBHOOK', 'ZIP_CODE', 'NTFY_TOPIC', 'CHAT_ID')) and value:
            failures.append(('.env.example', 'sensitive example value must be blank'))
    for name, reason in failures:
        print(f'{name}: {reason}')
    print(f'Public-file audit: {len(failures)} findings')
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
