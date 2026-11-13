"""Atomic state persistence; authenticated encryption is mandatory in Actions."""
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from .config import RadarError


def cipher(key):
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise RadarError('STATE_ENCRYPTION_KEY must be a Fernet key') from None


def scope(settings):
    # HMAC prevents guessing a ZIP code from a public cache key.
    material = json.dumps({'zip': settings.zip_code, 'radius': settings.radius,
                           'stores': settings.store_names, 'channels': settings.channels}, sort_keys=True).encode()
    return hmac.new(settings.encryption_key.encode() or b'local-state', material, hashlib.sha256).hexdigest()


class StateStore:
    def __init__(self, path, key='', cloud=False):
        if cloud and not key:
            raise RadarError('Set STATE_ENCRYPTION_KEY before enabling cloud monitoring')
        self.path = Path(path)
        self.cipher = cipher(key) if key else None

    def read(self):
        if not self.path.exists():
            return {'version': 1, 'stock': {}, 'cooldown_until': 0}
        try:
            raw = self.path.read_bytes()
            data = json.loads(self.cipher.decrypt(raw) if self.cipher else raw)
            if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data['stock'], dict):
                raise ValueError
            timestamps = [data.get('cooldown_until', 0)]
            for record in data['stock'].values():
                if not isinstance(record, dict) or not isinstance(record.get('available'), bool) or not isinstance(record.get('sent'), dict):
                    raise ValueError
                timestamps.extend(record['sent'].values())
            if any(not isinstance(t, (int, float)) or isinstance(t, bool) or not math.isfinite(t) or t < 0 for t in timestamps):
                raise ValueError
            return data
        except (InvalidToken, ValueError, KeyError, TypeError, OSError):
            raise RadarError('State is invalid or cannot be decrypted; preserve it and check the encryption key') from None

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(data, sort_keys=True).encode()
        if self.cipher:
            raw = self.cipher.encrypt(raw)
        temp = self.path.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
        os.chmod(temp, 0o600)
        temp.replace(self.path)
