"""Behavioral regression suite. Generates only synthetic paper data in --work-dir."""
import argparse
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from paperpdf.config import Paper,write_json,read_json
from paperpdf.build import build,preflight,crop
from paperpdf.verify import verify,render,delivery,record_review
from PIL import Image
from pypdf import PdfReader,PdfWriter
from pypdf.generic import DecodedStreamObject,NameObject

WORK=None


class Regression(unittest.TestCase):
    def make_paper(self, extra=None, blocks=None):
        root=Path(tempfile.mkdtemp(prefix='case_',dir=WORK))
        for color in ['red','blue']:
            Image.new('RGB',(100,50),color).save(root/(color+'.png'))
        assets=[dict(id=color,path=color+'.png',page=1,bbox=[0,0,100,50],dpi=72) for color in ['red','blue']]
        blocks=blocks or [
            dict(id='title',type='title_cn',text='可移植排版回归验证'),
            dict(id='start_number',type='body',text='1开头的正文不可被误当页码。left_right 保留字面下划线；−0.44不是0.44。'),
            dict(id='mixed',type='body',text=('中文标点，（括号不能孤立）。“引号完整”，I_TEST与Q_flux保留上下标，40°N不拆开。'*12)),
            dict(id='abstract',type='abstract',text='El Niño、λ、φ、ω、©，m s⁻¹，面积km²。'),
            dict(id='red',type='figure',asset='red',caption='图1 红色图块。',notes='red → 红色。'),
            dict(id='blue',type='table',asset='blue',caption='表1 蓝色图块。',notes='blue → 蓝色。'),
            dict(id='refs',type='references',text='参考文献',entries=['Author A (2020) A title with upper-tropospheric and DOI 10.1000/example.'])]
        write_json(root/'blocks.json',blocks);write_json(root/'assets.json',assets)
        config=dict(schema_version=1,blocks='blocks.json',assets='assets.json',output='result.pdf',work_dir='work',
            sample_ids=['title','mixed','red'],symbols={'I_TEST':{'text':'I','sub':'TEST'},'Q_flux':{'text':'Q','sub':'flux'}})
        if extra:config.update(extra)
        write_json(root/'paper.json',config)
        return Paper(root/'paper.json')

    def test_complete_text_fonts_images_and_footer(self):
        p=self.make_paper();build(p);q=verify(p)
        self.assertTrue(q['passed'],q);self.assertEqual(q['image_count'],2)
        self.assertTrue(all(x['passed'] for x in q['images_checked']))
        receipt=read_json(p.work/'build_receipt.json')
        self.assertEqual(receipt['layout']['line_issues'],[])
        self.assertIn('left_right',(p.work/'final_extracted_text.txt').read_text(encoding='utf8'))
        self.assertIn('1开头',(p.work/'final_extracted_text.txt').read_text(encoding='utf8'))

    def test_missing_font_and_glyph_fail_before_output(self):
        p=self.make_paper({'fonts':{'song':'does-not-exist.ttf'}})
        with self.assertRaisesRegex(ValueError,'Missing song'):build(p)
        self.assertFalse(p.output.exists())
        p=self.make_paper(blocks=[dict(id='bad',type='body',text='缺字检查\U0010ffff')])
        with self.assertRaisesRegex(ValueError,'Missing glyph'):preflight(p)
        self.assertFalse(p.output.exists())

    def test_relocation_and_different_cwd(self):
        p=self.make_paper();moved=p.root/'relocated skill'
        scripts=Path(__file__).resolve().parents[1]
        shutil.copytree(scripts/'paperpdf',moved/'paperpdf',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(scripts/'paper_pdf.py',moved/'paper_pdf.py')
        result=subprocess.run([sys.executable,'-X','utf8',str(moved/'paper_pdf.py'),'build',str(p.filename)],
                              cwd=WORK,capture_output=True,text=True,encoding='utf8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(verify(p)['passed'])

    def test_same_sized_images_swapped_are_rejected(self):
        p=self.make_paper(blocks=[dict(id=c,type='figure',asset=c,caption='图 '+c,notes='颜色对照。') for c in ['red','blue']]);build(p)
        reader=PdfReader(p.output);writer=PdfWriter()
        for pg in reader.pages:writer.add_page(pg)
        replaced=False
        for pg in writer.pages:
            data=pg.get_contents().get_data()
            names=[n for n in pg['/Resources'].get('/XObject',{}) if n.encode()+b' Do' in data]
            if len(names)>=2:
                data=pg.get_contents().get_data();a=names[0].encode();b=names[1].encode()
                data=data.replace(a,b'/TMP_SWAP').replace(b,a).replace(b'/TMP_SWAP',b)
                stream=DecodedStreamObject();stream.set_data(data);pg.replace_contents(stream)
                replaced=True;break
        self.assertTrue(replaced)
        with p.output.open('wb') as f:writer.write(f)
        q=verify(p)
        self.assertFalse(q['passed']);self.assertIn('image_identity_or_geometry',{x['kind'] for x in q['issues']})

    def test_literal_underscore_loss_is_not_normalized_away(self):
        p=self.make_paper();build(p)
        receipt=read_json(p.work/'build_receipt.json')
        # Simulate a lost glyph in the expected/observed comparison without any
        # numeric, punctuation, or underscore-tolerant normalization.
        for t in receipt['expected_text']:
            t['text']=t['text'].replace('left_right','leftright')
        write_json(p.work/'build_receipt.json',receipt)
        q=verify(p)
        self.assertFalse(q['text_exact_match'])

    def test_oversized_figure_uses_larger_page_without_scaling(self):
        p=self.make_paper({'layout':{'width_pt':180,'height_pt':240,'margin_pt':45}},
            blocks=[dict(id='large',type='figure',asset='red',caption='图1 原尺寸图片。',notes='红色。')])
        build(p);q=verify(p)
        self.assertTrue(q['passed'],q)
        receipt=read_json(p.work/'build_receipt.json');im=receipt['layout']['images'][0]
        self.assertEqual(im['width'],100);self.assertEqual(im['height'],50)
        self.assertGreater(receipt['layout']['page_sizes'][str(im['page'])][0],180)

    def test_render_cache_and_visual_gate(self):
        p=self.make_paper();build(p)
        first=render(p);second=render(p)
        self.assertFalse(any(x['cached'] for x in first['pages']))
        self.assertTrue(all(x['cached'] for x in second['pages']))
        self.assertFalse(delivery(p)['ready'])
        # This only tests state transitions; synthetic test acknowledgement is
        # not a claim that a human reviewed this fixture.
        record_review(p,'all');self.assertTrue(delivery(p)['ready'])
        Image.new('RGB',(1,1),'white').save(p.work/first['pages'][0]['image'])
        self.assertFalse(delivery(p)['ready'])

    def test_changed_inputs_invalidate_checks(self):
        p=self.make_paper();build(p)
        blocks=read_json(p.blocks_path);blocks[0]['text']='修改后的标题';write_json(p.blocks_path,blocks)
        q=verify(Paper(p.filename))
        self.assertFalse(q['passed']);self.assertIn('stale_inputs',{x['kind'] for x in q['issues']})

    def test_crop_preserves_coordinates_and_cache(self):
        p=self.make_paper();build(p)
        cfg=read_json(p.filename);cfg['source_pdf']='result.pdf'
        assets=read_json(p.assets_path)
        for a in assets:a['path']='cropped/'+a['id']+'.png'
        write_json(p.assets_path,assets);write_json(p.filename,cfg)
        fresh=Paper(p.filename)
        self.assertFalse(any(x['cached'] for x in crop(fresh)))
        self.assertTrue(all(x['cached'] for x in crop(fresh)))
        with Image.open(fresh.asset_path(fresh.assets['red'])) as im:self.assertEqual(im.size,(100,50))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--work-dir',required=True)
    args=parser.parse_args();WORK=Path(args.work_dir).resolve();WORK.mkdir(parents=True,exist_ok=True)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Regression)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    write_json(WORK/'test_report.json',dict(tests=result.testsRun,passed=result.wasSuccessful(),
        failures=[str(x) for x in result.failures],errors=[str(x) for x in result.errors]))
    raise SystemExit(0 if result.wasSuccessful() else 1)
