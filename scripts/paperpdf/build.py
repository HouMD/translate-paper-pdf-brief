"""Configured layout. Source extraction and scientific interpretation stay outside."""
from pathlib import Path
import hashlib
import json

from PIL import Image as PILImage
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
    Image, Spacer, KeepTogether, NextPageTemplate, PageBreak)
from reportlab.lib.styles import ParagraphStyle

from .config import digest, write_json
from .typography import Typography, chinese_wrapping, CLOSE, OPEN


def pixel_hash(image):
    im = image.convert('RGB')
    return hashlib.sha256(str(im.size).encode()+im.tobytes()).hexdigest()


def crop(paper):
    import pypdfium2 as pdfium
    source = paper.path(paper.config['source_pdf'])
    source_hash = digest(source)
    cache_path = paper.work/'crop_cache.json'
    cache = json.loads(cache_path.read_text(encoding='utf8')) if cache_path.exists() else {}
    result = []
    with pdfium.PdfDocument(str(source)) as doc:
        for asset in paper.assets.values():
            path = paper.asset_path(asset)
            key = hashlib.sha256(json.dumps([source_hash, asset['page'], asset['bbox'],
                                             asset.get('dpi', 600), 'draw_annots=True']).encode()).hexdigest()
            if path.exists() and cache.get(asset['id'], {}).get('key') == key and cache[asset['id']].get('sha256') == digest(path):
                result.append({'id': asset['id'], 'cached': True}); continue
            if path.exists():
                raise FileExistsError(f'Changed/untracked crop already exists: {path}; choose a new path')
            if asset['page'] > len(doc):
                raise ValueError('Crop page exceeds source PDF')
            page = doc[asset['page']-1]; w, h = page.get_size()
            x0, y0, x1, y1 = asset['bbox']
            if x1 > w or y1 > h:
                raise ValueError(f"Crop outside displayed page: {asset['id']}")
            dpi = asset.get('dpi', 600)
            if dpi < 72:
                raise ValueError('Crop DPI must be at least 72')
            image = page.render(scale=dpi/72, crop=(x0,h-y1,w-x1,y0), draw_annots=True).to_pil()
            path.parent.mkdir(parents=True, exist_ok=True); image.save(path, format='PNG')
            cache[asset['id']] = {'key': key, 'sha256': digest(path), 'pixels': list(image.size)}
            result.append({'id': asset['id'], 'cached': False})
    write_json(cache_path, cache)
    return result


def styles(paper, fonts):
    size = paper.layout['body_size_pt']; leading = paper.layout['leading_pt']
    base = dict(fontName=fonts['song'], fontSize=size, leading=leading, spaceAfter=7,
                wordWrap='CJK', allowWidows=0, allowOrphans=0)
    result = {}
    for name, changes in {
        'body': {'firstLineIndent': size*2}, 'abstract': {'fontName': fonts['kai']},
        'small': {'fontName': fonts['kai'], 'fontSize': 7.5, 'leading': 11.5, 'spaceAfter': 3},
        'caption': {'fontName': fonts['kai'], 'spaceAfter': 5},
        'heading': {'fontSize': 13, 'leading': 20, 'spaceBefore': 12, 'spaceAfter': 9, 'keepWithNext': True},
        'title_en': {'fontName': fonts['bold'], 'fontSize': 15, 'leading': 18, 'keepWithNext': True},
        'title_cn': {'fontSize': 18, 'leading': 24, 'keepWithNext': True},
        'meta': {'fontSize': 9, 'leading': 13, 'spaceAfter': 3},
        'references': {'fontName': fonts['roman'], 'fontSize': 9, 'leading': 12,
                       'spaceAfter': 5, 'leftIndent': 12, 'firstLineIndent': -12}
    }.items():
        result[name] = ParagraphStyle(name, **(base | changes))
    return result


class TrackedParagraph(Paragraph):
    def draw(self):
        if self.style.name in {'title_en', 'title_cn', 'heading'}:
            # SimSun has no bundled bold face. Fill + stroke preserves its
            # glyphs and searchable text while making every heading bold.
            self.canv.saveState()
            try:
                self.canv.setStrokeColor(self.style.textColor)
                self.canv.setLineWidth(self.style.fontSize * 0.018)
                self.canv._code.append('BT 2 Tr ET')
                super().draw()
            finally:
                self.canv.restoreState()
        else:
            super().draw()
        # Split fragments keep the same class. Content-derived IDs are logged
        # separately in the receipt; line checks also cover split fragments.
        for line in self.blPara.lines:
            if hasattr(line, 'words'):
                text = ''.join(x.text for x in line.words).strip()
            else:
                text = ' '.join(line[1]).strip()
            if not text:
                continue
            if text[0] in CLOSE or text[-1] in OPEN:
                self.canv._paper_log['line_issues'].append({'page': self.canv.getPageNumber(), 'text': text})


class TrackedImage(Image):
    def __init__(self, filename, asset):
        scale = float(asset.get('scale', 1.0))
        if not 0.8 <= scale <= 1.2:
            raise ValueError(f"Asset scale must be between 0.8 and 1.2: {asset['id']}")
        super().__init__(str(filename), width=asset['width_pt']*scale, height=asset['height_pt']*scale)
        self.asset = asset
        with PILImage.open(filename) as im:
            self.pixel_sha = pixel_hash(im)

    def draw(self):
        super().draw()
        x, y = self.canv.absolutePosition(0, 0)
        self.canv._paper_log['images'].append({'id': self.asset['id'], 'page': self.canv.getPageNumber(),
            'x': x, 'y': y, 'width': self.drawWidth, 'height': self.drawHeight, 'pixel_sha256': self.pixel_sha})


def preflight(paper):
    typography = Typography(paper)
    for b in paper.blocks:
        typ = b['type']; size = paper.layout['body_size_pt']
        strings = [b.get('text', ''), b.get('caption', ''), b.get('notes', '')]+b.get('entries', [])
        if typ in {'figure','table'}:
            strings.append('表内文字对应：' if typ=='table' else '图中说明：')
        for text in strings:
            typography.format(text, cn='kai' if typ in {'abstract','small','figure','table'} else 'song', size=size)
    typography.assert_coverage()
    for a in paper.assets.values():
        with PILImage.open(paper.asset_path(a)) as im:
            if im.format != 'PNG':
                raise ValueError(f"Expected lossless PNG: {a['id']}")
            # One or two pixels of PDF renderer rounding are expected.
            dpi = a.get('dpi', 600)
            if abs(im.width-a['width_pt']*dpi/72)>2 or abs(im.height-a['height_pt']*dpi/72)>2:
                raise ValueError(f"Crop pixel dimensions/DPI do not match bbox: {a['id']}")
    return {'passed': True, 'fonts': typography.files, 'blocks': len(paper.blocks), 'assets': len(paper.assets)}


def build(paper, sample=False):
    preflight(paper)
    font = Typography(paper); sty = styles(paper, font.fonts)
    paper.work.mkdir(parents=True, exist_ok=True)
    selected = paper.config.get('sample_ids') if sample else None
    if sample and not selected:
        raise ValueError('Set sample_ids: title, actual abstract, script-bearing paragraph, and caption/figure')
    blocks = [b for b in paper.blocks if not sample or b['id'] in selected]
    if sample and set(selected) != {b['id'] for b in blocks}:
        raise ValueError('Unknown sample_ids')
    target = paper.work/'font_sample.pdf' if sample else paper.output
    if target.exists():
        raise FileExistsError(f'Output exists: {target}; choose a new version/work_dir')
    target.parent.mkdir(parents=True, exist_ok=True)
    log = {'images': [], 'line_issues': [], 'page_sizes': {}}
    expected = []; story = []; templates = []
    W = paper.layout['width_pt']; H = paper.layout['height_pt']; margin = paper.layout['margin_pt']

    def footer(canvas, doc):
        canvas._paper_log = log
        w,h = canvas._pagesize
        log['page_sizes'][str(doc.page)] = [w,h]
        # A marked PDF artifact, not a guessed leading/trailing number.
        canvas._code.append('/Artifact BMC')
        canvas.saveState(); canvas.setFont(font.fonts['roman'], 9)
        canvas.drawCentredString(w/2, 24, str(doc.page))
        canvas.restoreState(); canvas._code.append('EMC')

    def template(name, w, h):
        return PageTemplate(id=name, pagesize=(w,h), frames=[Frame(margin, margin,
            w-2*margin,h-2*margin,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)],onPage=footer)

    templates.append(template('normal', W, H))

    def paragraph(text, style, bid, cn=None):
        cn = cn or ('kai' if style in {'abstract','small','caption'} else 'song')
        markup, plain = font.format(text, cn=cn, bold=style in {'heading','title_en','title_cn'}, size=sty[style].fontSize)
        expected.append({'id': bid, 'text': plain})
        return TrackedParagraph(markup, sty[style])

    for b in blocks:
        typ = b['type']; bid = b['id']
        if typ in {'figure','table','equation'}:
            asset = paper.assets[b['asset']]
            image = TrackedImage(paper.asset_path(asset), asset)
            parts = [image, Spacer(1,7)]
            if typ != 'equation':
                parts.append(paragraph(b['caption'], 'caption', bid+':caption'))
                notes = b.get('notes')
                if notes:
                    label = '表内文字对应：' if typ == 'table' else '图中说明：'
                    parts.append(paragraph(label+notes, 'caption', bid+':notes'))
            parts.append(Spacer(1,7))
            inner_width = max(W-2*margin, image.drawWidth)
            with chinese_wrapping():
                needed_height = sum(p.wrap(inner_width, 100000)[1]+p.getSpaceBefore()+p.getSpaceAfter() for p in parts)
            if image.drawWidth > W-2*margin+.01 or needed_height > H-2*margin:
                if paper.layout['oversize'] != 'page':
                    raise ValueError(f"Asset and caption need a larger page: {asset['id']}")
                name = f'asset_{bid}'
                w = max(W, image.drawWidth+2*margin)
                h = max(H, needed_height+2*margin+2)
                templates.append(template(name,w,h))
                story += [NextPageTemplate(name), PageBreak(), *parts,
                          NextPageTemplate('normal'), PageBreak()]
            else:
                story.append(KeepTogether(parts))
        elif typ == 'references':
            if b.get('text'):
                story.append(paragraph(b['text'], 'heading', bid+':heading'))
            for i,entry in enumerate(b['entries']):
                story.append(paragraph(entry, 'references', f'{bid}:{i+1}'))
        else:
            for i,text in enumerate(b['text'].splitlines()):
                if text.strip():
                    story.append(paragraph(text, typ, f'{bid}:{i+1}'))
    while story and isinstance(story[-1], (PageBreak, NextPageTemplate)):
        story.pop()
    font.assert_coverage()
    doc = BaseDocTemplate(str(target), pagesize=(W,H), title=paper.config.get('title',''),
        author=paper.config.get('author',''), pageCompression=1)
    doc.addPageTemplates(templates)
    with chinese_wrapping():
        doc.build(story)
    receipt = {'schema_version': 1, 'sample': sample, 'pdf_sha256': digest(target),
        'input_sha256': paper.fingerprint(), 'expected_text': expected, 'layout': log,
        'font_sha256': {role: digest(path) for role,path in font.files.items()},
        'block_ids': [b['id'] for b in blocks]}
    write_json(paper.work/('sample_receipt.json' if sample else 'build_receipt.json'), receipt)
    if log['line_issues']:
        raise ValueError('Line-start/end punctuation failure; PDF retained for diagnosis, not ready for delivery')
    return {'output': str(target), 'pages': len(log['page_sizes']), 'sample': sample}
