#!/usr/bin/env python3
"""Discover new model links and named SKUs from Apple; all additions are disabled."""
import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from apple_stock_radar.catalog import discover_page, load_catalog, merge_catalog, write_catalog
from apple_stock_radar.config import RadarError
from apple_stock_radar.http import Backoff, Client


def crawl(sources, client, max_pages=40, pause=1):
    queue = list(dict.fromkeys(sources))
    seen, found, errors = set(), {}, 0
    while queue and len(seen) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            markup, final_url = client.request(url, apple_only=True, text=True)
            items, links = discover_page(final_url, markup)
        except Backoff:
            raise  # Never keep crawling after rate limiting / access denial.
        except RadarError:
            errors += 1
            continue
        for item in items:
            found[item.part] = item
        for link in sorted(links):
            path = urlsplit(link).path
            if re.fullmatch(r'/shop/buy-(?:iphone|mac|ipad|watch)/[a-z0-9-]+/?', path) or re.fullmatch(r'/shop/buy-airpods/[a-z0-9-]+/?', path):
                if link not in seen and link not in queue:
                    queue.append(link)
        if pause:
            time.sleep(pause)
    return list(found.values()), errors, len(seen), bool(queue)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog', type=Path, default=Path('products.json'))
    p.add_argument('--sources', type=Path, default=Path('catalog_sources.json'))
    p.add_argument('--max-pages', type=int, default=40)
    args = p.parse_args()
    try:
        if not 1 <= args.max_pages <= 100:
            raise RadarError('--max-pages must be 1–100')
        sources = json.loads(args.sources.read_text())['sources']
        found, errors, pages, truncated = crawl(sources, Client(), args.max_pages)
        if errors or not found:
            raise RadarError('Catalog discovery was incomplete; no catalog changes written')
        data, count = merge_catalog(load_catalog(args.catalog), found)
        if count:
            write_catalog(args.catalog, data)
        print(json.dumps({'new_variants': count, 'pages': pages, 'page_limit_reached': truncated}))
        return 0
    except (RadarError, OSError, ValueError, KeyError):
        print('Catalog update failed safely; inspect source availability and retry later.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
