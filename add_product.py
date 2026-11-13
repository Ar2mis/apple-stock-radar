#!/usr/bin/env python3
"""Import only official, named Apple product metadata; resolve ambiguous pages explicitly."""
import argparse
from pathlib import Path
from apple_stock_radar.catalog import discover_page, load_catalog, merge_catalog, part_from_url, write_catalog
from apple_stock_radar.config import RadarError
from apple_stock_radar.http import Client


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('url')
    p.add_argument('--catalog', type=Path, default=Path('products.json'))
    p.add_argument('--part', help='Select one official SKU when a page contains multiple variants')
    p.add_argument('--all', action='store_true', help='Import every named supported variant on this page')
    p.add_argument('--enable', action='store_true', help='Enable newly added variants (default: disabled)')
    args = p.parse_args()
    try:
        markup, final_url = Client().request(args.url, apple_only=True, text=True)
        found, _ = discover_page(final_url, markup)
        wanted = args.part.upper() if args.part else part_from_url(args.url)
        if wanted:
            found = [item for item in found if item.part == wanted]
        if not found:
            raise RadarError('No named official part found; choose a specific configured Apple Store product URL')
        if len(found) > 1 and not args.all:
            print('\n'.join(f'{p.part}\t{p.name}' for p in found))
            raise RadarError('Multiple variants found; rerun with --part or --all')
        data, count = merge_catalog(load_catalog(args.catalog), found, args.enable)
        write_catalog(args.catalog, data)
        print(f'Added {count} official variants; existing choices preserved.')
        return 0
    except (RadarError, OSError) as exc:
        print(str(exc) if isinstance(exc, RadarError) else 'Catalog file operation failed')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
