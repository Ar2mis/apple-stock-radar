"""Apple pickup adapter and fresh-result-only notification state machine."""
import hashlib
import json
import logging
import math
import re
import time
from urllib.parse import urlencode
from .config import RadarError
from .http import Backoff

LOG = logging.getLogger('apple_stock_radar')
ENDPOINT = 'https://www.apple.com/shop/retail/pickup-message'


def distance_miles(store):
    raw = store.get('storeDistanceWithUnit', store.get('storeDistance'))
    if raw is None:
        return None
    if isinstance(raw, (float, int)):
        return float(raw) if not isinstance(raw, bool) and math.isfinite(raw) and raw >= 0 else None
    match = re.fullmatch(r'\s*([\d,.]+)\s*(mi|mile|miles|km|kilometers)?\s*', str(raw), re.I)
    if not match:
        return None
    value = float(match.group(1).replace(',', ''))
    return value / 1.609344 if (match.group(2) or '').lower() in {'km', 'kilometers'} else value


def parse_stock(data, product, settings):
    try:
        if data.get('head', {}).get('status', 200) not in (200, '200'):
            raise ValueError
        body = data['body']
        stores = body['stores']
        if body.get('errorMessage') or not isinstance(stores, list) or not stores:
            raise ValueError
        rows = []
        for store in stores:
            name, identifier = store.get('storeName'), store.get('storeNumber')
            if not isinstance(name, str) or not identifier:
                raise ValueError
            if settings.store_names and not any(p in name.casefold() for p in settings.store_names):
                continue
            distance = distance_miles(store)
            if settings.radius and distance is None:
                raise RadarError('Apple omitted store distance; cannot enforce SEARCH_RADIUS_MILES (0 disables radius filtering)')
            if settings.radius and distance > settings.radius:
                continue
            info = store['partsAvailability'][product.part]
            availability = info['pickupDisplay']
            if availability not in {'available', 'unavailable', 'ineligible'}:
                raise ValueError
            rows.append({'key': f'{identifier}|{product.part}', 'name': product.name, 'store': name,
                         'available': availability == 'available', 'url': product.url})
        return rows  # No matching stores is not an affirmative out-of-stock observation.
    except (KeyError, TypeError, AttributeError, ValueError):
        raise RadarError('Unrecognized Apple pickup response; availability is unknown') from None


def fetch_stock(client, product, settings):
    query = urlencode({'pl': 'true', 'mts.0': 'regular', 'parts.0': product.part, 'location': settings.zip_code})
    return parse_stock(client.request(ENDPOINT + '?' + query, apple_only=True), product, settings)


def delivery_id(settings, channel):
    # Invalidate delivery state if a destination changes, without storing credentials.
    from .notifications import REQUIRED
    material = [settings.env.get(k, '') for k in REQUIRED[channel]]
    if channel in {'bark', 'ntfy'}:
        default = 'https://api.day.app' if channel == 'bark' else 'https://ntfy.sh'
        material.append((settings.env.get(channel.upper() + '_SERVER') or default).rstrip('/'))
    return channel + ':' + hashlib.sha256(json.dumps(material).encode()).hexdigest()


def reconcile(rows, state, settings, notifier, store, stop, now=time.time):
    failures = 0
    for row in rows:
        if stop.is_set():
            break
        previous = state['stock'].get(row['key'], {})
        if not row['available']:
            state['stock'][row['key']] = {'available': False, 'sent': {}}
            store.save(state)
            continue
        sent = dict(previous.get('sent', {})) if previous.get('available') else {}
        state['stock'][row['key']] = {'available': True, 'sent': sent}
        for channel in settings.channels:
            if stop.is_set():
                break
            identity = delivery_id(settings, channel)
            last = sent.get(identity)
            if last is not None and (settings.repeat == 0 or 0 <= now() - last < settings.repeat):
                continue
            try:
                notifier.send(channel, 'Apple pickup stock available', f"{row['name']}\n{row['store']} — available for pickup", row['url'])
            except RadarError:
                LOG.warning('Notification submission failed for %s; will retry on a fresh available result', channel)
                failures += 1
            else:
                sent[identity] = now()
            store.save(state)
    return failures


def cycle(products, settings, client, notifier, state, store, stop, dry_run=False, now=time.time, fetch=fetch_stock):
    if now() < state.get('cooldown_until', 0):
        return {'checked': 0, 'available': 0, 'errors': 0, 'cooldown': True}
    rows, failures, checked = {}, 0, 0
    for product in products:
        if stop.is_set():
            break
        try:
            found = fetch(client, product, settings)
            checked += 1
        except Backoff as exc:
            if not dry_run:
                state['cooldown_until'] = now() + exc.seconds
                store.save(state)
            LOG.warning('%s', exc)
            failures += 1
            break
        except RadarError as exc:
            LOG.warning('%s', exc)
            failures += 1
            continue
        fresh = []
        for row in found:
            if row['key'] not in rows or row['available']:
                rows[row['key']] = row
                fresh.append(row)
        if not dry_run:
            failures += reconcile(fresh, state, settings, notifier, store, stop, now)
    return {'checked': checked, 'available': sum(r['available'] for r in rows.values()), 'errors': failures, 'cooldown': False}
