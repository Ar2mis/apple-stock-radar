"""Command-line entry point; secrets never appear in output or exception text."""
import argparse
import json
import logging
import os
import signal
import threading
import time
from .catalog import Product, load_catalog, products
from .config import RadarError, Settings, load_dotenv
from .http import Client
from .monitor import cycle, parse_stock
from .notifications import Notifier
from .state import StateStore, scope


def main():
    parser = argparse.ArgumentParser(description='Monitor US Apple Store pickup stock')
    parser.add_argument('--once', action='store_true', help='Run one check (for cron/Actions)')
    parser.add_argument('--dry-run', action='store_true', help='Check stock without notifications or state writes')
    parser.add_argument('--offline', action='store_true', help='Use synthetic stock; requires --dry-run')
    parser.add_argument('--notification-dry-run', action='store_true', help='Preview selected adapters without network or credentials')
    parser.add_argument('--test-notification', action='store_true', help='Send one explicitly labeled test and exit')
    parser.add_argument('--validate-catalog', action='store_true')
    parser.add_argument('--list-products', action='store_true')
    parser.add_argument('--cache-scope', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(message)s')
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        load_dotenv()
        if args.offline and not args.dry_run:
            raise RadarError('--offline requires --dry-run')
        if args.validate_catalog or args.list_products:
            data = load_catalog(os.environ.get('CATALOG_PATH') or 'products.json')
            all_products = products(data, False)
            if args.list_products:
                print('\n'.join(f'{p.part}\t{p.name}' for p in all_products))
            else:
                print(f'Catalog valid: {len(all_products)} official variants')
            return 0
        no_stock = args.notification_dry_run or args.test_notification
        settings = Settings.from_env(offline=args.offline or no_stock)
        client = Client(settings.timeout)
        notifier = Notifier(settings, client)
        if args.notification_dry_run:
            print(json.dumps([notifier.send(c, 'Startup test', 'No stock claim', 'https://www.apple.com/shop', True) for c in settings.channels]))
            return 0
        if args.cache_scope:
            StateStore(settings.state_path, settings.encryption_key, cloud=True)
            print(scope(settings))
            return 0
        if not args.dry_run:
            notifier.validate()
        if args.test_notification:
            for channel in settings.channels:
                notifier.send(channel, 'Apple Stock Radar startup test', 'Test notification only; this does not indicate stock.', 'https://www.apple.com/shop')
            print('Test accepted by notification service(s); check your device.')
            return 0
        if args.offline:
            selected = [Product('MTEST01/A', 'Synthetic iPhone', 'iphone', 'Synthetic', '256GB', 'Blue', 'https://www.apple.com/shop')]
        else:
            selected = products(load_catalog(settings.catalog))
            if not selected:
                raise RadarError('No enabled variants: enable products in products.json')
        store = StateStore(settings.state_path, settings.encryption_key, cloud=os.environ.get('GITHUB_ACTIONS') == 'true' and not args.dry_run)
        state = {'version': 1, 'stock': {}, 'cooldown_until': 0} if args.dry_run else store.read()
        current_scope = scope(settings)
        if state.get('scope', current_scope) != current_scope:
            state = {'version': 1, 'stock': {}, 'cooldown_until': 0}
        state['scope'] = current_scope
        if settings.startup and not args.once and not args.dry_run:
            for channel in settings.channels:
                try:
                    notifier.send(channel, 'Apple Stock Radar startup test', 'Monitoring is starting. This is a test, not a stock alert.', 'https://www.apple.com/shop')
                except RadarError:
                    logging.warning('Startup test failed for %s; continuing monitoring', channel)
        options = {}
        if args.offline:
            def synthetic(client, p, s):
                return parse_stock({'body': {'stores': [{'storeName': 'Example Store', 'storeNumber': 'SYNTHETIC',
                    'storeDistance': 1, 'partsAvailability': {p.part: {'pickupDisplay': 'available'}}}]}}, p, s)
            options['fetch'] = synthetic
        while not stop.is_set():
            report = cycle(selected, settings, client, notifier, state, store, stop, args.dry_run, **options)
            if args.once or args.dry_run:
                print(json.dumps(report))
                return 1 if report['errors'] else 0
            delay = max(settings.interval, state.get('cooldown_until', 0) - time.time())
            if report['errors']:
                delay = max(delay, 120)
            stop.wait(delay)
        return 0
    except (RadarError, OSError) as exc:
        message = str(exc) if isinstance(exc, RadarError) else 'Local file operation failed'
        logging.error('%s', message)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
