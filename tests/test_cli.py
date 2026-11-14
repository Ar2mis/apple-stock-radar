"""Exercise the same CLI modes exposed by the GitHub Actions form."""
import contextlib
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from apple_stock_radar.cli import main
from apple_stock_radar.catalog import load_catalog, products


class WorkflowModeTests(unittest.TestCase):
    def run_cli(self, args, env):
        output = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch('sys.argv', ['radar', *args]), \
                patch('apple_stock_radar.cli.load_dotenv'), contextlib.redirect_stdout(output):
            result = main()
        return result, output.getvalue()

    def test_push_test_needs_no_location_catalog_or_state(self):
        with patch('apple_stock_radar.cli.Client.request', return_value={'code': 200}) as request, \
                patch('apple_stock_radar.cli.StateStore') as state:
            result, output = self.run_cli(['--test-notification'], {
                'BARK_KEY': 'synthetic', 'NOTIFY_CHANNELS': 'bark', 'GITHUB_ACTIONS': 'true',
                'CATALOG_PATH': 'does-not-exist.json'})
        self.assertEqual(result, 0)
        self.assertIn('accepted', output)
        request.assert_called_once()
        state.assert_not_called()

    def test_cloud_live_dry_run_selects_exact_part_without_credentials(self):
        catalog = Path(__file__).parents[1] / 'products.json'
        chosen = products(load_catalog(catalog), False)[-1]
        response = {'body': {'stores': [{'storeName': 'Example', 'storeNumber': 'TEST',
                    'storeDistance': 1, 'partsAvailability': {chosen.part: {'pickupDisplay': 'available'}}}]}}
        with patch('apple_stock_radar.cli.Client.request', return_value=response) as request, \
                patch('apple_stock_radar.cli.Notifier.send') as send, \
                patch('apple_stock_radar.cli.StateStore.save') as save:
            result, output = self.run_cli(['--dry-run'], {'ZIP_CODE': '00000',
                'GITHUB_ACTIONS': 'true', 'PRODUCT_PARTS': chosen.part, 'CATALOG_PATH': str(catalog)})
        self.assertEqual(result, 0)
        self.assertIn('"checked": 1', output)
        self.assertIn('"available": 1', output)
        request.assert_called_once()
        send.assert_not_called()
        save.assert_not_called()

    def test_unknown_manual_part_fails_without_network(self):
        with patch('apple_stock_radar.cli.Client.request') as request:
            result, _ = self.run_cli(['--dry-run'], {'ZIP_CODE': '00000',
                'PRODUCT_PARTS': 'MUNKNOWN/A', 'CATALOG_PATH': str(Path(__file__).parents[1] / 'products.json')})
        self.assertEqual(result, 2)
        request.assert_not_called()
