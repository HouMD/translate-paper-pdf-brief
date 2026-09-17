"""All paper paths resolve relative to the config, never the process cwd."""
from pathlib import Path
import hashlib
import json
import math


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


class Paper:
    def __init__(self, filename):
        self.filename = Path(filename).resolve()
        self.root = self.filename.parent
        self.config = read_json(self.filename)
        if self.config.get('schema_version') != 1:
            raise ValueError('Expected schema_version: 1')
        self.blocks_path = self.path(self.config['blocks'])
        self.blocks = read_json(self.blocks_path)
        self.assets_path = self.path(self.config['assets']) if self.config.get('assets') else None
        assets = read_json(self.assets_path) if self.assets_path else []
        self.assets = {a['id']: a for a in assets}
        if len(assets) != len(self.assets):
            raise ValueError('Duplicate asset IDs')
        self.output = self.path(self.config['output'])
        self.work = self.path(self.config.get('work_dir', 'pdf_work'))
        self.layout = {'width_pt': 595.2756, 'height_pt': 841.8898,
                       'margin_pt': 56.6929, 'body_size_pt': 10.5, 'leading_pt': 15.75,
                       'oversize': 'page'}
        self.layout.update(self.config.get('layout', {}))
        for key in ['width_pt', 'height_pt', 'margin_pt', 'body_size_pt', 'leading_pt']:
            value = self.layout[key]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'Invalid layout.{key}')
        if min(self.layout['width_pt'], self.layout['height_pt']) <= 2*self.layout['margin_pt']:
            raise ValueError('Margins leave no page area')
        self._validate()

    def path(self, value):
        path = Path(value).expanduser()
        return (self.root / path).resolve() if not path.is_absolute() else path.resolve()

    def asset_path(self, asset):
        # Asset paths have one unambiguous base: the config directory.
        return self.path(asset['path'])

    def _validate(self):
        supported = {'title_en', 'title_cn', 'meta', 'heading', 'body', 'abstract',
                     'small', 'figure', 'table', 'equation', 'references'}
        ids = set()
        for b in self.blocks:
            if not b.get('id') or b['id'] in ids:
                raise ValueError('Missing or duplicate block ID')
            ids.add(b['id'])
            if b.get('type') not in supported:
                raise ValueError(f'Unsupported block type: {b}')
            if b['type'] in {'figure', 'table', 'equation'}:
                if b.get('asset') not in self.assets:
                    raise ValueError(f"Unknown asset in {b['id']}")
                if b['type'] != 'equation' and not b.get('caption'):
                    raise ValueError(f"Missing translated caption: {b['id']}")
                if b['type'] != 'equation' and not b.get('notes'):
                    raise ValueError(f"Missing figure/table label translations: {b['id']}")
            elif b['type'] == 'references':
                if not b.get('entries') or not all(isinstance(x, str) and x.strip() for x in b['entries']):
                    raise ValueError('References must be complete nonempty entries, in source order')
            elif not isinstance(b.get('text'), str) or not b['text'].strip():
                raise ValueError(f"Empty text block: {b['id']}")
        for a in self.assets.values():
            box = a.get('bbox')
            if not box or len(box) != 4 or not all(math.isfinite(x) for x in box):
                raise ValueError(f"Invalid top-left PDF-point bbox: {a['id']}")
            if box[2] <= box[0] or box[3] <= box[1] or min(box) < 0:
                raise ValueError(f"Invalid bbox order: {a['id']}")
            if not isinstance(a.get('page'), int) or a['page'] < 1:
                raise ValueError(f"Invalid source page: {a['id']}")
            a['width_pt'], a['height_pt'] = box[2]-box[0], box[3]-box[1]
        source_file = self.config.get('source_blocks')
        if source_file:
            source = read_json(self.path(source_file))
            source_ids = [x['id'] for x in source]
            if len(source_ids) != len(set(source_ids)) or set(source_ids) != ids:
                raise ValueError('Source/translation block IDs are not one-to-one')
        expected = self.config.get('expected_counts', {})
        actual = {t: sum(b['type'] == t for b in self.blocks) for t in supported}
        actual['reference_entries'] = sum(len(b['entries']) for b in self.blocks if b['type'] == 'references')
        for k, value in expected.items():
            if actual.get(k) != value:
                raise ValueError(f'Count mismatch {k}: expected {value}, got {actual.get(k)}')

    def fingerprint(self):
        files = [self.filename, self.blocks_path]
        if self.assets_path:
            files.append(self.assets_path)
        if self.config.get('source_blocks'):
            files.append(self.path(self.config['source_blocks']))
        files += [self.asset_path(a) for a in self.assets.values()]
        # Include package code so changes invalidate previous build/review reports.
        files += sorted(Path(__file__).parent.glob('*.py'))
        return hashlib.sha256(''.join(digest(p) for p in files).encode()).hexdigest()
