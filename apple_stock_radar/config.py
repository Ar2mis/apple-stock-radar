"""Environment-only configuration. Never include configuration values in errors."""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


class RadarError(Exception):
    """A safe, user-facing error without request URLs, credentials or response bodies."""


def load_dotenv(path=Path('.env')):
    """Load literal KEY=value pairs; no expansion or shell execution; env wins."""
    if not path.exists():
        return
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key.strip()):
            raise RadarError(f'Invalid .env entry on line {number}')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def number(env, key, default, minimum, maximum):
    try:
        value = float(env.get(key) or default)
        if not minimum <= value <= maximum:
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise RadarError(f'{key} must be between {minimum} and {maximum}') from None


def boolean(env, key, default=False):
    raw = str(env.get(key, str(default))).lower()
    if raw not in {'true', 'false', '1', '0'}:
        raise RadarError(f'{key} must be true or false')
    return raw in {'true', '1'}


@dataclass(repr=False)
class Settings:
    zip_code: str
    radius: float
    interval: float
    repeat: float
    timeout: float
    startup: bool
    channels: tuple
    catalog: Path
    state_path: Path
    encryption_key: str = field(repr=False)
    env: dict = field(repr=False)
    store_names: tuple = ()

    @classmethod
    def from_env(cls, env=None, offline=False):
        env = dict(os.environ if env is None else env)
        zip_code = env.get('ZIP_CODE', '')
        if not offline and not re.fullmatch(r'\d{5}', zip_code):
            raise RadarError('Set ZIP_CODE to a US five-digit ZIP code')
        channels = tuple(dict.fromkeys(x.strip() for x in env.get('NOTIFY_CHANNELS', 'bark').split(',') if x.strip()))
        if set(channels) - {'bark', 'telegram', 'pushover', 'discord', 'ntfy'}:
            raise RadarError('Unknown NOTIFY_CHANNELS entry')
        return cls(zip_code, number(env, 'SEARCH_RADIUS_MILES', 50, 0, 1000),
                   number(env, 'CHECK_INTERVAL_SECONDS', 60, 30, 86400),
                   number(env, 'REPEAT_NOTIFICATION_SECONDS', 180, 0, 86400),
                   number(env, 'REQUEST_TIMEOUT_SECONDS', 20, 1, 120),
                   boolean(env, 'STARTUP_NOTIFICATION', True), channels,
                   Path(env.get('CATALOG_PATH') or 'products.json'),
                   Path(env.get('STATE_PATH') or '.state/monitor.state'),
                   env.get('STATE_ENCRYPTION_KEY', ''), env,
                   tuple(x.strip().casefold() for x in env.get('STORE_NAMES', '').split(',') if x.strip()))
