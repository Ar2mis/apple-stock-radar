"""Versioned human-readable catalog and conservative official-page discovery."""
import copy
import html
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
from .config import RadarError
from .http import apple_url

CATEGORIES = ('iphone', 'mac', 'ipad', 'watch', 'airpods')
PART = re.compile(r'M[A-Z0-9]{4,11}/A\Z')


@dataclass(frozen=True)
class Product:
    part: str
    name: str
    category: str
    model: str
    storage: str | None
    color: str | None
    url: str


def load_catalog(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if data['schema_version'] != 1 or not isinstance(data['categories'], list):
            raise ValueError
        seen = set()
        for category in data['categories']:
            if category['id'] not in CATEGORIES or not isinstance(category['enabled'], bool):
                raise ValueError
            for model in category['models']:
                if not isinstance(model['enabled'], bool) or not model['name']:
                    raise ValueError
                for v in model['variants']:
                    if not isinstance(v['enabled'], bool) or v.get('status') not in {'current', 'upcoming'}:
                        raise ValueError
                    part = v.get('part_number')
                    if part is None:
                        if v['enabled'] or v['status'] != 'upcoming':
                            raise ValueError
                        continue
                    if not isinstance(part, str) or not PART.fullmatch(part) or part in seen:
                        raise ValueError
                    seen.add(part)
                    if not v.get('name'):
                        raise ValueError
                    apple_url(v['source_url'])
        return data
    except (OSError, ValueError, TypeError, KeyError):
        raise RadarError('Invalid products.json: check schema, unique part numbers and enabled flags') from None


def products(data, enabled_only=True):
    result = []
    for cat in data['categories']:
        for model in cat['models']:
            for v in model['variants']:
                enabled = cat['enabled'] and model['enabled'] and v['enabled']
                if v.get('part_number') and (enabled or not enabled_only):
                    result.append(Product(v['part_number'], v['name'], cat['id'], model['name'],
                                          v.get('storage'), v.get('color'), v['source_url']))
    return result


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.links = set()
        self.documents = []
        self.collect = False
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('href'):
            candidate = urljoin(self.source, html.unescape(attrs['href']))
            try:
                apple_url(candidate)
            except RadarError:
                return
            self.links.add(candidate.split('?')[0])
        if tag == 'script' and 'json' in attrs.get('type', ''):
            self.collect = True
            self.buffer = []

    def handle_data(self, data):
        if self.collect:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.collect:
            self.collect = False
            try:
                self.documents.append(json.loads(''.join(self.buffer)))
            except ValueError:
                pass


def classify(name):
    name = ' '.join(html.unescape(name).split())
    patterns = [
        ('iphone', r'^(?:Buy )?(iPhone\s+(?:Duo|Air|\d+(?:e)?)(?:\s+Pro(?:\s+Max)?|\s+Plus|\s+mini)?)\b'),
        ('ipad', r'^(?:Buy )?((?:\d+(?:\.\d+)?-inch\s+)?iPad(?:\s+Pro|\s+Air|\s+mini)?(?:\s*\([^)]*\))?)\b'),
        ('mac', r'^(?:Buy )?((?:\d+(?:\.\d+)?-inch\s+)?(?:MacBook\s+(?:Air|Pro|Neo)|iMac|Mac\s+(?:mini|Studio|Pro))(?:\s*\([^)]*\))?)\b'),
        ('watch', r'^(?:Buy )?(Apple Watch\s+(?:Series\s+\d+|Ultra(?:\s+\d+)?|SE(?:\s+\d+)?))\b'),
        ('airpods', r'^(?:Buy )?(AirPods(?:\s+Pro|\s+Max)?(?:\s+\d+)?)\b'),
    ]
    for category, pattern in patterns:
        match = re.search(pattern, name, re.I)
        if match and not re.search(r'^(?:AirPods.*(?:ear tips|replacement))|(?:case|cover|adapter|keyboard) for ', name, re.I):
            if category in {'iphone', 'ipad', 'mac'} and re.search(r'\b(case|cover|keyboard|cable|adapter)\b', name, re.I):
                return None
            return category, match.group(1)
    return None


def selector_label(value):
    """Keep selector names, removing explanatory copy and footnote markup."""
    value = re.sub(r'<as-footnote\b[^>]*>.*?</as-footnote>', '', value, flags=re.S)
    value = re.sub(r'<(span|div)\b[^>]*class="[^"]*(?:form-label-small|badge)[^"]*"[^>]*>.*?</\1>', '', value, flags=re.S)
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', value)).split())


def discover_page(source, markup):
    """Only accept named Apple JSON records. Never assign a random SKU from page text."""
    apple_url(source)
    page = Page(source)
    page.feed(markup)
    found = {}

    # Apple Mac/Watch selectors embed JSON inside a JS assignment. Decode only
    # the JSON value; never execute JavaScript or use eval.
    parent_name = next((d.get('name') for d in page.documents if isinstance(d, dict)
                        and d.get('@type') == 'Product' and isinstance(d.get('name'), str)), '')
    for match in re.finditer(r'\bproductSelectionData\s*:\s*(?=\{)', markup):
        try:
            selection = json.JSONDecoder().raw_decode(markup[match.end():])[0]
        except ValueError:
            continue
        if not isinstance(selection, dict) or not classify(parent_name):
            continue
        displays = selection.get('displayValues', selection.get('mainDisplayValues', {}))
        for variant in selection.get('products', []):
            if not isinstance(variant, dict):
                continue
            part = variant.get('btrOrFdPartNumber') or variant.get('part')
            if not isinstance(part, str) or not PART.fullmatch(part):
                continue
            dimensions = variant.get('dimensions', {})
            labels, color = [], None
            for dimension, value in dimensions.items():
                if not isinstance(value, str):
                    continue
                label = displays.get(dimension, {}).get(value, {}).get('header', value.replace('_', ' '))
                label = selector_label(label)
                labels.append(label)
                if 'Color' in dimension:
                    color = label
            page.documents.append({'name': parent_name + ' ' + ' '.join(labels),
                                   'partNumber': part, 'color': color})

    def visit(node, inherited_name=''):
        if isinstance(node, list):
            for item in node:
                visit(item, inherited_name)
        elif isinstance(node, dict):
            name = next((node[k] for k in ('name', 'productName', 'title') if isinstance(node.get(k), str)), '')
            if not name and node.get('@type') == 'Offer':
                name = inherited_name
            classification = classify(name)
            part = next((node[k] for k in ('partNumber', 'part', 'sku') if isinstance(node.get(k), str) and PART.fullmatch(node[k].upper())), None)
            if classification and part:
                category, model = classification
                name = ' '.join(html.unescape(name).split())
                capacities = re.findall(r'\b\d+(?:\.\d+)?\s?(?:GB|TB)\b', name, re.I)
                color = node.get('color') if isinstance(node.get('color'), str) else None
                if not color:
                    match = re.search(r'\b(Space Black|Space Gray|Space Grey|Sky Blue|Night Sky|Star White|Natural Titanium|Black Titanium|Desert Titanium|White Titanium|Silver|Black|White|Blue|Green|Pink|Purple|Yellow|Orange|Red|Burgundy|Glacier|Starlight|Midnight|Blush|Indigo)\b', name, re.I)
                    color = match.group(1) if match else None
                part = part.upper()
                product = Product(part, name, category, model, capacities[-1].upper().replace(' ', '') if capacities else None, color,
                                  'https://www.apple.com/shop/product/' + part)
                found[part] = product
            for value in node.values():
                if isinstance(value, (dict, list)):
                    visit(value, name if node.get('@type') == 'Product' else inherited_name)
    for document in page.documents:
        visit(document)
    return list(found.values()), page.links


def merge_catalog(data, discovered, enabled=False):
    """Add new variants only; never overwrite user toggles or delete existing variants."""
    result = copy.deepcopy(data)
    existing = {p.part for p in products(result, False)}
    additions = 0
    for p in sorted(discovered, key=lambda p: (p.category, p.model, p.part)):
        if p.part in existing:
            continue
        category = next(c for c in result['categories'] if c['id'] == p.category)
        model = next((m for m in category['models'] if m['name'] == p.model), None)
        if model is None:
            model = {'name': p.model, 'enabled': True, 'variants': []}
            category['models'].append(model)
        model['variants'].append({'name': p.name, 'part_number': p.part, 'storage': p.storage,
                                  'color': p.color, 'status': 'current', 'enabled': enabled, 'source_url': p.url})
        existing.add(p.part)
        additions += 1
    return result, additions


def write_catalog(path, data):
    path = Path(path)
    temporary = path.with_suffix('.tmp.json')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    load_catalog(temporary)
    temporary.replace(path)


def part_from_url(url):
    apple_url(url)
    match = re.search(r'/shop/product/(M[A-Z0-9]{4,11}/A)(?:/|$)', unquote(urlsplit(url).path), re.I)
    return match.group(1).upper() if match else None
