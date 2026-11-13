"""Bounded HTTPS transport with redacted failures and redirect validation."""
import email.utils
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from .config import RadarError


class Backoff(RadarError):
    def __init__(self, status, seconds):
        self.seconds = seconds
        super().__init__(f'Remote service returned HTTP {status}; cooldown required')


def retry_seconds(value):
    try:
        return max(60, min(float(value), 86400))
    except (ValueError, TypeError):
        try:
            return max(60, min(email.utils.parsedate_to_datetime(value).timestamp() - time.time(), 86400))
        except (ValueError, TypeError, OverflowError):
            return 300


def apple_url(url):
    try:
        p = urllib.parse.urlsplit(url)
        valid = p.scheme == 'https' and p.hostname in {'www.apple.com', 'apple.com'} and p.port in (None, 443)
        if not valid or p.username or p.password or p.fragment:
            raise ValueError
        if not (p.path.startswith('/shop/') or p.path in {'/shop', '/store', '/airpods/'}):
            raise ValueError
    except ValueError:
        raise RadarError('Use a US https://www.apple.com/shop/ product URL') from None
    return url


def https_url(url):
    try:
        p = urllib.parse.urlsplit(url)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment:
            raise ValueError
        _ = p.port
    except ValueError:
        raise RadarError('Notification endpoints must be valid HTTPS URLs without embedded credentials') from None
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, apple_only):
        self.apple_only = apple_only

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.apple_only:
            # Never forward notification credentials or POST bodies through redirects.
            raise RadarError('Notification redirect refused')
        apple_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, timeout=20):
        self.timeout = timeout

    def request(self, url, payload=None, *, headers=None, form=False, apple_only=False, text=False):
        (apple_url if apple_only else https_url)(url)
        h = {'User-Agent': 'Mozilla/5.0 (compatible; AppleStockRadar/0.1)', 'Accept': '*/*' if text else 'application/json'}
        h.update(headers or {})
        body = None
        if payload is not None:
            body = urllib.parse.urlencode(payload).encode() if form else json.dumps(payload).encode()
            h['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        req = urllib.request.Request(url, data=body, headers=h)
        try:
            with urllib.request.build_opener(SafeRedirect(apple_only)).open(req, timeout=self.timeout) as response:
                raw = response.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise RadarError('Remote response exceeded size limit')
                if text:
                    return raw.decode('utf-8'), response.url
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 541):
                raise Backoff(exc.code, 900) from None
            if exc.code == 429:
                raise Backoff(429, retry_seconds(exc.headers.get('Retry-After'))) from None
            raise RadarError(f'Remote service returned HTTP {exc.code}') from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise RadarError('Network connection failed or timed out') from None
        except (ValueError, UnicodeError):
            raise RadarError('Remote response was not valid JSON/text') from None
