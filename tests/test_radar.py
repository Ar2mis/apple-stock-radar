import copy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from cryptography.fernet import Fernet
from apple_stock_radar.catalog import CATEGORIES, Product, discover_page, load_catalog, merge_catalog, products, write_catalog, part_from_url
from apple_stock_radar.config import RadarError, Settings, load_dotenv
from apple_stock_radar.http import Client, Backoff, SafeRedirect, apple_url
from apple_stock_radar.monitor import cycle, parse_stock, reconcile
from apple_stock_radar.notifications import Notifier
from apple_stock_radar.state import StateStore, scope
from update_catalog import crawl

P = Product('MTEST01/A', 'iPhone Test 256GB Blue', 'iphone', 'iPhone Test', '256GB', 'Blue', 'https://www.apple.com/shop/product/MTEST01/A')


def catalog():
    return {'schema_version': 1, 'categories': [{'id': c, 'enabled': True, 'models': []} for c in CATEGORIES]}


def settings(**extra):
    return Settings.from_env({'ZIP_CODE': '00000', 'NOTIFY_CHANNELS': 'bark', 'BARK_KEY': 'synthetic-key', **extra})


def stock(available='available', distance='2.5 mi'):
    return {'body': {'stores': [{'storeName': 'Example Store', 'storeNumber': 'SYNTHETIC', 'storeDistance': distance,
                               'partsAvailability': {P.part: {'pickupDisplay': available}}}]}}


class CatalogTests(unittest.TestCase):
    def test_metrics_named_records_only(self):
        markup = '<script type="application/json">' + json.dumps({'data': {'products': [
            {'partNumber': 'MTEST01/A', 'name': 'iPhone 18 Pro 256GB Blue'},
            {'partNumber': 'MTEST02/A', 'name': 'iPhone 18 Pro Case'},
            {'partNumber': 'MTEST03/A', 'name': 'USB-C Cable'}]}}) + '</script>'
        found, _ = discover_page('https://www.apple.com/shop/buy-iphone/iphone-18-pro', markup)
        self.assertEqual(found[0].storage, '256GB')
        self.assertEqual(found[0].color, 'Blue')
        self.assertNotIn('MTEST03/A', [p.part for p in found])
        self.assertNotIn('MTEST02/A', [p.part for p in found])

    def test_jsonld_and_escaped_slash(self):
        markup = '<script type="application/ld+json">{"@type":"Product","name":"AirPods Pro 3","sku":"MTEST04\\/A"}</script>'
        found, _ = discover_page('https://www.apple.com/shop/product/MTEST04/A', markup)
        self.assertEqual(found[0].category, 'airpods')
        self.assertIsNone(found[0].storage)

    def test_bare_part_is_not_guessed(self):
        found, _ = discover_page('https://www.apple.com/shop', '<p>MTEST01/A</p>')
        self.assertEqual(found, [])

    def test_offer_inherits_product_name(self):
        markup = '<script type="application/ld+json">' + json.dumps({
            '@type': 'Product', 'name': 'AirPods Pro 3',
            'offers': [{'@type': 'Offer', 'sku': 'MTEST04/A'}]}) + '</script>'
        found, _ = discover_page('https://www.apple.com/shop', markup)
        self.assertEqual([(p.part, p.model) for p in found], [('MTEST04/A', 'AirPods Pro 3')])

    def test_mac_and_watch_selector_json(self):
        for model, category, field in [('MacBook Pro', 'mac', 'btrOrFdPartNumber'), ('Apple Watch Series 12', 'watch', 'part')]:
            selection = {'products': [{field: 'MTEST04/A', 'dimensions': {'chassis-dimensionColor': 'space_black'}}],
                         'displayValues': {'chassis-dimensionColor': {'space_black': {
                             'header': 'Space Black<div class="form-label-small">Marketing copy</div>'}}}}
            markup = '<script type="application/ld+json">' + json.dumps({'@type': 'Product', 'name': model}) + '</script>'
            markup += '<script>window.config = {productSelectionData: ' + json.dumps(selection) + '};</script>'
            found, _ = discover_page('https://www.apple.com/shop', markup)
            self.assertEqual(found[0].category, category)
            self.assertEqual(found[0].name, model + ' Space Black')
            self.assertEqual(found[0].color, 'Space Black')

    def test_malformed_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'state'
            for data in [[], {'version': 1, 'stock': {'a': {'available': True, 'sent': {'bark': 'bad'}}}},
                         {'version': 1, 'stock': {}, 'cooldown_until': float('nan')}]:
                p.write_text(json.dumps(data))
                with self.assertRaises(RadarError): StateStore(p).read()

    def test_merge_preserves_choices_and_is_idempotent(self):
        data, count = merge_catalog(catalog(), [P], enabled=True)
        again, count = merge_catalog(data, [P, Product('MTEST02/A', 'Another', 'iphone', 'Test', None, None, P.url)])
        self.assertTrue(again['categories'][0]['models'][0]['variants'][0]['enabled'])
        self.assertEqual(count, 1)
        self.assertEqual(merge_catalog(again, [P])[1], 0)
        self.assertEqual(len(products(again)), 1)

    def test_upcoming_cannot_be_enabled_without_part(self):
        data = catalog()
        data['categories'][0]['models'] = [{'name': 'Announced model', 'enabled': True, 'variants': [
            {'name': 'Awaiting official SKU', 'part_number': None, 'status': 'upcoming', 'enabled': True}]}]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'products.json'; p.write_text(json.dumps(data))
            with self.assertRaises(RadarError): load_catalog(p)
            data['categories'][0]['models'][0]['variants'][0]['enabled'] = False
            p.write_text(json.dumps(data)); self.assertEqual(products(load_catalog(p)), [])

    def test_url_validation_and_part_extraction(self):
        self.assertEqual(part_from_url('https://www.apple.com/shop/product/mtest01/a'), P.part)
        for url in ['http://www.apple.com/shop', 'https://apple.com.evil.test/shop', 'https://u:p@apple.com/shop', 'https://www.apple.com:8080/shop']:
            with self.subTest(url=url), self.assertRaises(RadarError): apple_url(url)

    def test_new_model_links_are_followed_and_backoff_stops(self):
        client = Mock()
        client.request.side_effect = [('<a href="/shop/buy-iphone/new-phone">New</a>', 'https://www.apple.com/shop'), Backoff(429, 60)]
        with self.assertRaises(Backoff): crawl(['https://www.apple.com/shop'], client, pause=0)
        self.assertEqual(client.request.call_count, 2)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = StateStore(Path(self.temp.name)/'state')
        self.state = self.store.read(); self.stop = threading.Event()
        self.notifier = Mock(); self.s = settings()

    def test_available_unavailable_and_unknown(self):
        self.assertTrue(parse_stock(stock(), P, self.s)[0]['available'])
        self.assertFalse(parse_stock(stock('unavailable'), P, self.s)[0]['available'])
        for data in [stock('unknown'), {}, {'body': {'stores': []}}]:
            with self.assertRaises(RadarError): parse_stock(data, P, self.s)

    def test_radius_and_missing_distance(self):
        self.assertEqual(parse_stock(stock(distance='100 mi'), P, self.s), [])
        with self.assertRaises(RadarError): parse_stock(stock(distance=None), P, self.s)
        self.assertEqual(len(parse_stock(stock(distance=None), P, settings(SEARCH_RADIUS_MILES='0'))), 1)
        self.assertEqual(len(parse_stock(stock(distance='60 km'), P, self.s)), 1)

    def test_store_filter(self):
        self.assertEqual(parse_stock(stock(), P, settings(STORE_NAMES='different store')), [])

    def test_repeat_persistence_restock_and_stop(self):
        rows = parse_stock(stock(), P, self.s)
        for stamp in [1000, 1179, 1180]:
            reconcile(rows, self.state, self.s, self.notifier, self.store, self.stop, lambda: stamp)
            self.state = self.store.read()
        self.assertEqual(self.notifier.send.call_count, 2)
        reconcile(parse_stock(stock('unavailable'), P, self.s), self.state, self.s, self.notifier, self.store, self.stop)
        reconcile(rows, self.state, self.s, self.notifier, self.store, self.stop, lambda: 1181)
        self.assertEqual(self.notifier.send.call_count, 3)
        self.stop.set()
        reconcile(rows, self.state, self.s, self.notifier, self.store, self.stop, lambda: 2000)
        self.assertEqual(self.notifier.send.call_count, 3)

    def test_failed_delivery_retries(self):
        self.notifier.send.side_effect = RadarError('synthetic failure')
        rows = parse_stock(stock(), P, self.s)
        reconcile(rows, self.state, self.s, self.notifier, self.store, self.stop, lambda: 1000)
        self.notifier.send.side_effect = None
        reconcile(rows, self.state, self.s, self.notifier, self.store, self.stop, lambda: 1001)
        self.assertEqual(self.notifier.send.call_count, 2)

    def test_no_stock_and_dry_run_never_send_or_persist(self):
        report = cycle([P], self.s, Mock(), self.notifier, self.state, self.store, self.stop, True,
                       fetch=lambda *a: parse_stock(stock(), P, self.s))
        self.assertEqual(report['available'], 1)
        self.notifier.send.assert_not_called(); self.assertFalse(self.store.path.exists())
        cycle([P], self.s, Mock(), self.notifier, self.state, self.store, self.stop,
              fetch=lambda *a: parse_stock(stock('unavailable'), P, self.s))
        self.notifier.send.assert_not_called()

    def test_cooldown_survives_cloud_run(self):
        fetch = Mock(side_effect=Backoff(541, 900))
        cycle([P, P], self.s, Mock(), self.notifier, self.state, self.store, self.stop, now=lambda: 1000, fetch=fetch)
        fetch.assert_called_once()
        report = cycle([P], self.s, Mock(), self.notifier, self.store.read(), self.store, self.stop, now=lambda: 1001, fetch=fetch)
        self.assertTrue(report['cooldown']); self.assertEqual(fetch.call_count, 1)
        self.notifier.send.assert_not_called()


class NotificationTests(unittest.TestCase):
    def test_all_adapters_and_accepted_responses(self):
        e = {'NOTIFY_CHANNELS': 'bark,telegram,pushover,discord,ntfy', 'TELEGRAM_BOT_TOKEN': 'synthetic',
             'TELEGRAM_CHAT_ID': 'synthetic', 'PUSHOVER_API_TOKEN': 'synthetic', 'PUSHOVER_USER_KEY': 'synthetic',
             'DISCORD_WEBHOOK_URL': 'https://discord.com/api/webhooks/1/synthetic', 'NTFY_TOPIC': 'synthetic'}
        client = Mock(); client.request.side_effect = [{'code':200}, {'ok':True}, {'status':1}, {'id':'1'}, {'id':'1'}]
        notifier = Notifier(settings(**e), client); notifier.validate()
        for channel in notifier.settings.channels: notifier.send(channel, 'Test', 'Body', 'https://www.apple.com/shop')
        self.assertEqual(client.request.call_count, 5)
        discord = client.request.call_args_list[3].args[1]
        self.assertEqual(discord['allowed_mentions'], {'parse': []})

    def test_dry_run_works_without_tokens(self):
        s = Settings.from_env({'NOTIFY_CHANNELS':'bark,telegram,pushover,discord,ntfy'}, offline=True)
        client = Mock(); notifier = Notifier(s, client)
        for channel in s.channels:
            self.assertTrue(notifier.send(channel,'test','body','https://www.apple.com/shop',True)['dry_run'])
        client.request.assert_not_called()

    def test_rejected_service_response_is_failure(self):
        client = Mock(); client.request.return_value = {'code':400, 'message':'secret must never be printed'}
        with self.assertRaises(RadarError) as error: Notifier(settings(), client).send('bark','a','b',P.url)
        self.assertNotIn('secret must', str(error.exception))

    def test_redirect_does_not_forward_secrets(self):
        with self.assertRaises(RadarError): SafeRedirect(False).redirect_request(None,None,302,'',{},'https://evil.test')
        with self.assertRaises(RadarError): SafeRedirect(True).redirect_request(None,None,302,'',{},'https://evil.test')


class StateAndConfigTests(unittest.TestCase):
    def test_encryption_roundtrip_and_wrong_key(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state'; key=Fernet.generate_key().decode(); store=StateStore(p,key,cloud=True)
            data={'version':1,'stock':{'private selection':{'available': True, 'sent': {'synthetic': 120}}},'cooldown_until':123}
            store.save(data); self.assertNotIn(b'private selection',p.read_bytes()); self.assertEqual(store.read(),data)
            with self.assertRaises(RadarError): StateStore(p,Fernet.generate_key().decode()).read()
            with self.assertRaises(RadarError): StateStore(p,cloud=True)

    def test_dotenv_is_literal_and_environment_wins(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'ZIP_CODE':'00000'}, clear=True):
            p=Path(d)/'.env'; p.write_text('ZIP_CODE=11111\nBARK_KEY="$(not-executed)"\n')
            load_dotenv(p); self.assertEqual(os.environ['ZIP_CODE'],'00000'); self.assertEqual(os.environ['BARK_KEY'],'$(not-executed)')

    def test_settings_validate_without_leaking_values(self):
        for e in [{'ZIP_CODE':'secret-value'}, {'CHECK_INTERVAL_SECONDS':'nan'}, {'REPEAT_NOTIFICATION_SECONDS':'-1'}]:
            with self.assertRaises(RadarError) as error: settings(**e)
            self.assertNotIn('secret-value',str(error.exception))
        self.assertNotIn('synthetic-key',repr(settings()))

    def test_scope_hides_location_and_changes_on_settings(self):
        a=settings(STATE_ENCRYPTION_KEY=Fernet.generate_key().decode())
        b=copy.deepcopy(a); b.radius=10
        self.assertNotEqual(scope(a),scope(b)); self.assertEqual(len(scope(a)),64)


if __name__ == '__main__': unittest.main()
