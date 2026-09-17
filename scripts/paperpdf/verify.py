"""Independent PDF checks. Text sequence and spatial geometry use separate readers."""
from copy import copy
from pathlib import Path
import difflib
import hashlib
import json
import re

import pdfplumber
from pypdf import PdfReader
from pypdf.generic import ContentStream, NameObject

from .build import pixel_hash
from .config import digest, read_json, write_json


def normalized(text):
    # Do NOT delete underscores, punctuation, minus signs, or normalize numbers.
    # Script transformations are specified in the build receipt, not guessed here.
    return re.sub(r'\s+', '', text)


def text_without_artifacts(page, reader):
    contents = ContentStream(page.get_contents(), reader, forced_encoding='bytes')
    operations = []; marked = []; suppressed = False
    for operands, operator in contents.operations:
        if operator in (b'BMC', b'BDC'):
            marked.append(suppressed)
            suppressed = suppressed or (bool(operands) and str(operands[0]) == '/Artifact')
            if not suppressed:
                operations.append((operands,operator))
        elif operator == b'EMC':
            if not suppressed:
                operations.append((operands,operator))
            suppressed = marked.pop() if marked else False
        elif not suppressed:
            operations.append((operands,operator))
    contents.operations = operations
    clone = copy(page); clone[NameObject('/Contents')] = contents
    return clone.extract_text() or ''


def _font_embedded(font):
    font = font.get_object()
    if font.get('/DescendantFonts'):
        return all(_font_embedded(f) for f in font['/DescendantFonts'])
    descriptor = font.get('/FontDescriptor')
    return bool(descriptor and any(k in descriptor.get_object() for k in ('/FontFile','/FontFile2','/FontFile3')))


def verify(paper, sample=False):
    pdf = paper.work/'font_sample.pdf' if sample else paper.output
    receipt = read_json(paper.work/('sample_receipt.json' if sample else 'build_receipt.json'))
    issues = []; report = {'schema_version': 1, 'pdf_sha256': digest(pdf)}
    if report['pdf_sha256'] != receipt['pdf_sha256']:
        issues.append({'kind': 'modified_pdf', 'message': 'PDF differs from the build receipt'})
    if paper.fingerprint() != receipt['input_sha256']:
        issues.append({'kind': 'stale_inputs', 'message': 'Inputs or module changed; rebuild a new version'})
    reader = PdfReader(pdf)
    texts = [text_without_artifacts(p, reader) for p in reader.pages]
    actual = normalized(''.join(texts))
    expected = normalized(''.join(x['text'] for x in receipt['expected_text']))
    if actual != expected:
        differences = [(tag, expected[a:b], actual[c:d]) for tag,a,b,c,d in
            difflib.SequenceMatcher(None,expected,actual,autojunk=False).get_opcodes() if tag != 'equal']
        issues.append({'kind':'text_mismatch','differences':differences[:30]})
    report['text_exact_match'] = actual == expected
    report['characters_checked'] = len(expected)
    report['pages'] = len(reader.pages)
    expected_images = receipt['layout']['images']; observed_count = 0; checked = []
    with pdfplumber.open(pdf) as doc:
        if len(doc.pages) != len(reader.pages):
            issues.append({'kind':'reader_page_count_disagreement','pypdf':len(reader.pages),'pdfplumber':len(doc.pages)})
        for pn,page in enumerate(doc.pages,1):
            images = list(page.images); observed_count += len(images)
            page_expected = [im for im in expected_images if im['page']==pn]
            # Match each placement AND image pixels, not just width/height.
            decoded = {im.name.rsplit('.',1)[0]: pixel_hash(im.image) for im in reader.pages[pn-1].images}
            for im in page_expected:
                matching = next((o for o in images if
                    abs(o['x0']-im['x']) <= .1 and abs(o['y0']-im['y']) <= .1
                    and abs(o['width']-im['width']) <= .1 and abs(o['height']-im['height']) <= .1
                    and decoded.get(o['name']) == im['pixel_sha256']),None)
                checked.append({'id':im['id'],'page':pn,'passed':bool(matching)})
                if matching:
                    images.remove(matching)
                else:
                    issues.append({'kind':'image_identity_or_geometry','page':pn,'id':im['id']})
            if images:
                issues.append({'kind':'unexpected_images','page':pn,'count':len(images)})
            for im in page.images:
                if im['x0'] < -.1 or im['x1'] > page.width+.1 or im['top'] < -.1 or im['bottom'] > page.height+.1:
                    issues.append({'kind':'image_outside_page','page':pn})
            for c in page.chars:
                if c['x0'] < -.1 or c['x1'] > page.width+.1 or c['top'] < -.1 or c['bottom'] > page.height+.1:
                    issues.append({'kind':'text_outside_page','page':pn,'text':c['text']})
                for im in page.images:
                    if c['x0'] < im['x1']-.5 and c['x1'] > im['x0']+.5 and c['top'] < im['bottom']-.5 and c['bottom'] > im['top']+.5:
                        issues.append({'kind':'text_image_overlap','page':pn,'text':c['text']})
            resources = reader.pages[pn-1]['/Resources'].get('/Font', {})
            fonts = {str(f.get_object().get('/BaseFont','')).lstrip('/'): _font_embedded(f)
                     for f in resources.values()}
            for used in {c['fontname'] for c in page.chars}:
                if not fonts.get(used, False):
                    issues.append({'kind':'used_font_not_embedded','page':pn,'font':used})
    if observed_count != len(expected_images):
        issues.append({'kind':'image_count','expected':len(expected_images),'actual':observed_count})
    issues.extend({'kind':'line_punctuation', **item} for item in receipt['layout']['line_issues'])
    report.update(images_checked=checked,image_count=observed_count,issues=issues,passed=not issues)
    name = 'sample_qa.json' if sample else 'qa_report.json'
    write_json(paper.work/name,report)
    (paper.work/('sample_text.txt' if sample else 'final_extracted_text.txt')).write_text('\n\n'.join(texts),encoding='utf8')
    return report


def object_hash(obj, seen=None):
    """Hash page resource values and decoded streams, not unstable object numbers."""
    seen = set() if seen is None else seen
    obj = obj.get_object() if hasattr(obj,'get_object') else obj
    if isinstance(obj,(dict,list)):
        key = id(obj)
        if key in seen:
            return b'cycle'
        seen = seen | {key}
    h = hashlib.sha256()
    if hasattr(obj,'get_data'):
        h.update(obj.get_data())
    if isinstance(obj,dict):
        for k in sorted(obj):
            if str(k) not in {'/Length','/Filter','/DecodeParms'}:
                h.update(str(k).encode());h.update(object_hash(obj[k],seen))
    elif isinstance(obj,list):
        for item in obj:
            h.update(object_hash(item,seen))
    else:
        h.update(str(obj).encode('utf8'))
    return h.digest()


def render(paper, sample=False, scale=1.5):
    import pypdfium2 as pdfium
    if scale <= 0:
        raise ValueError('Render scale must be positive')
    pdf = paper.work/'font_sample.pdf' if sample else paper.output
    reader = PdfReader(pdf); folder = paper.work/'review_pages';folder.mkdir(parents=True,exist_ok=True)
    cache_path=paper.work/'render_cache.json'
    cache=read_json(cache_path) if cache_path.exists() else {}
    manifest = {'pdf_sha256':digest(pdf),'sample':sample,'scale':scale,'pages':[]}
    with pdfium.PdfDocument(str(pdf)) as doc:
        for i,page in enumerate(reader.pages):
            h=hashlib.sha256()
            for obj in [page.get_contents(),page['/Resources'],list(page.mediabox),page.get('/Rotate',0),page.get('/Annots',[])]:
                h.update(object_hash(obj))
            h.update(f'{scale}:{pdfium.PYPDFIUM_INFO.version}:{pdfium.PDFIUM_INFO.version}'.encode())
            key=h.hexdigest();path=folder/(key+'.png')
            cached=path.exists() and cache.get(key)==digest(path)
            if not cached:
                doc[i].render(scale=scale,draw_annots=True).to_pil().save(path)
            manifest['pages'].append({'page':i+1,'key':key,'image':str(path.relative_to(paper.work)),
                                      'sha256':digest(path),'cached':cached})
            cache[key]=digest(path)
    write_json(cache_path,cache)
    write_json(paper.work/('sample_render.json' if sample else 'render_manifest.json'),manifest)
    return manifest


def record_review(paper, pages, sample=False):
    manifest = read_json(paper.work/('sample_render.json' if sample else 'render_manifest.json'))
    pdf = paper.work/'font_sample.pdf' if sample else paper.output
    if digest(pdf) != manifest['pdf_sha256']:
        raise ValueError('Rendered pages are stale')
    path=paper.work/'visual_review.json'
    review=read_json(path) if path.exists() else {'reviewed':{}}
    numbers=set(range(1,len(manifest['pages'])+1)) if pages=='all' else {int(x) for x in pages.split(',')}
    if not numbers or not numbers <= {x['page'] for x in manifest['pages']}:
        raise ValueError('Invalid page selection')
    for item in manifest['pages']:
        if item['page'] in numbers:
            if digest(paper.work/item['image']) != item['sha256']:
                raise ValueError('Review image was modified')
            review['reviewed'][item['key']]={'image_sha256':item['sha256'], 'acknowledgement':'visually inspected'}
    write_json(path,review)
    return {'recorded_pages':sorted(numbers)}


def delivery(paper):
    report=verify(paper)
    manifest_path=paper.work/'render_manifest.json'
    review_path=paper.work/'visual_review.json'
    missing=[]
    if not manifest_path.exists() or not review_path.exists():
        missing=['render all pages, inspect them, and record visual review']
    else:
        manifest=read_json(manifest_path);review=read_json(review_path)
        if manifest['pdf_sha256']!=digest(paper.output):
            missing.append('stale render manifest')
        for item in manifest['pages']:
            ack=review['reviewed'].get(item['key'],{})
            if ack.get('image_sha256')!=item['sha256'] or digest(paper.work/item['image'])!=item['sha256']:
                missing.append(item['page'])
        if len(manifest['pages']) != report['pages']:
            missing.append('page count changed')
    result={'ready':report['passed'] and not missing,'automated_checks_passed':report['passed'],
            'visual_review_missing':missing,'pdf_sha256':digest(paper.output)}
    write_json(paper.work/'delivery_report.json',result)
    return result
