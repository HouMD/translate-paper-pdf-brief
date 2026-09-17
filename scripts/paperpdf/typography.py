"""Explicit font routing, inline symbols, and Chinese punctuation-aware wrapping."""
from contextlib import contextmanager
from pathlib import Path
import html
import os
import re
import unicodedata

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import reportlab.platypus.paragraph as rp

FONT_FILES = {'song': 'simsun.ttc', 'kai': 'simkai.ttf', 'roman': 'times.ttf',
              'bold': 'timesbd.ttf', 'italic': 'timesi.ttf', 'bolditalic': 'timesbi.ttf'}
CLOSE = '，。；：！？、）》】」』”’.,;:!?)]}°%‰'
OPEN = '（《【「『“‘([{'
SUPERS = str.maketrans('⁻⁺⁰¹²³⁴⁵⁶⁷⁸⁹˒', '−+0123456789,')


def font_dirs(paper):
    dirs = []
    if paper.config.get('font_dir'):
        dirs.append(paper.path(paper.config['font_dir']))
    if os.environ.get('PAPERPDF_FONT_DIR'):
        dirs.append(Path(os.environ['PAPERPDF_FONT_DIR']).expanduser())
    if os.environ.get('WINDIR'):
        dirs.append(Path(os.environ['WINDIR'])/'Fonts')
    dirs += [Path.home()/'.fonts', Path.home()/'.local/share/fonts',
             Path.home()/'Library/Fonts', Path('/Library/Fonts'),
             Path('/usr/share/fonts/truetype/msttcorefonts')]
    return dirs


class Typography:
    def __init__(self, paper):
        self.fonts = {}
        self.files = {}
        self.missing = set()
        self.symbols = paper.config.get('symbols', {})
        for role, filename in FONT_FILES.items():
            spec = paper.config.get('fonts', {}).get(role)
            index = 0
            if spec:
                spec = {'path': spec} if isinstance(spec, str) else spec
                path = paper.path(spec['path']); index = spec.get('index', 0)
            else:
                path = next((d/filename for d in font_dirs(paper) if (d/filename).is_file()), None)
            if path is None or not path.is_file():
                raise ValueError(f'Missing {role} ({filename}); supply fonts.{role}.path or font_dir. No fallback used.')
            name = f'PP_{role}'
            pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=index))
            self.fonts[role] = name; self.files[role] = str(path)
        self.pattern = re.compile('|'.join([re.escape(s) for s in sorted(self.symbols, key=len, reverse=True)] + [r'[⁻⁺⁰¹²³⁴⁵⁶⁷⁸⁹˒]+']))

    def run(self, text, role):
        name = self.fonts[role]
        for ch in text:
            if not ch.isspace() and ord(ch) not in pdfmetrics.getFont(name).face.charToGlyph:
                self.missing.add((role, ch, f'U+{ord(ch):04X}'))
        return f'<font name="{name}">{html.escape(text)}</font>'

    def mixed(self, text, cn, bold=False):
        runs = []; last = None; buf = ''
        for ch in text:
            # Fullwidth punctuation is Chinese; Latin, Greek and mathematical
            # minus signs remain in Times. Do not route all non-ASCII to Chinese.
            cjk = ('\u2e80' <= ch <= '\uffef' or '\U00020000' <= ch <= '\U000323af'
                   or ch in '—“”‘’')
            role = cn if cjk else ('bold' if bold else 'roman')
            if role != last and buf:
                runs.append(self.run(buf, last)); buf = ''
            buf += ch; last = role
        if buf:
            runs.append(self.run(buf, last))
        return ''.join(runs)

    def format(self, text, cn='song', bold=False, size=10.5):
        markup = []; plain = []; start = 0
        for m in self.pattern.finditer(text):
            prefix = text[start:m.start()]
            markup.append(self.mixed(prefix, cn, bold)); plain.append(prefix)
            token = m.group()
            if token in self.symbols:
                spec = self.symbols[token]
                base = spec['text']; role = 'italic' if spec.get('italic', True) else 'roman'
                part = self.run(base, role); plain.append(base)
                for key, tag, rise in [('sub', 'sub', 2), ('super', 'super', 3)]:
                    if spec.get(key):
                        part += f'<{tag} size="{size*2/3:.3f}" rise="{rise*size/10.5:.3f}">{self.run(spec[key], "roman")}</{tag}>'
                        plain.append(spec[key])
                markup.append(part)
            else:
                sup = token.translate(SUPERS)
                markup.append(f'<super size="{size*2/3:.3f}" rise="{3*size/10.5:.3f}">{self.run(sup, "roman")}</super>')
                plain.append(sup)
            start = m.end()
        suffix = text[start:]
        markup.append(self.mixed(suffix, cn, bold)); plain.append(suffix)
        return ''.join(markup), ''.join(plain)

    def assert_coverage(self):
        if self.missing:
            raise ValueError('Missing glyphs (no silent substitution): '+repr(sorted(self.missing)))


def _latin(ch):
    return bool(ch) and ord(ch) < 0x3000 and (ch.isalnum() or ch in '−-./_°–—')


def _invalid_boundary(left, right):
    return (right in CLOSE or left in OPEN or (_latin(left) and _latin(right))
            or bool(getattr(right.frag, 'rise', 0))
            or unicodedata.category(right).startswith('M'))


def chinese_split(frags, max_widths, calc_bounds, encoding='utf8'):
    """Break by glyph width, backing up to a legal boundary (no punctuation spill)."""
    units = []
    for frag in frags:
        if getattr(frag, 'lineBreak', False) or getattr(frag, 'cbDefn', None):
            raise ValueError('Use separate plain-text paragraphs, not HTML breaks/callbacks')
        units.extend(rp.cjkU(ch, frag, encoding) for ch in frag.text)
    lines = []; start = 0
    while start < len(units):
        width = max_widths[min(len(lines), len(max_widths)-1)]
        end = start; used = 0
        while end < len(units) and used + units[end].width <= width + 1e-7:
            used += units[end].width; end += 1
        if end == start:
            raise ValueError('A glyph is wider than the text frame')
        if end < len(units):
            while end > start and _invalid_boundary(units[end-1], units[end]):
                end -= 1
            if end == start:
                raise ValueError('Unbreakable word/symbol exceeds line width; enlarge page or edit explicit paragraph boundary')
        seq = units[start:end]; used = sum(ch.width for ch in seq)
        lines.append(rp.makeCJKParaLine(seq, width, used, width-used, False, calc_bounds))
        start = end
    return rp.ParaLines(kind=1, lines=lines)


@contextmanager
def chinese_wrapping():
    # ReportLab exposes no public mixed-CJK breaker hook. Keep the override
    # scoped to one synchronous build; restore it even after failure.
    required = ['cjkU', 'makeCJKParaLine', 'ParaLines', 'cjkFragSplit']
    if not all(hasattr(rp, x) for x in required):
        raise RuntimeError('Incompatible ReportLab CJK API; run regression tests before upgrading')
    old = rp.cjkFragSplit
    rp.cjkFragSplit = chinese_split
    try:
        yield
    finally:
        rp.cjkFragSplit = old
