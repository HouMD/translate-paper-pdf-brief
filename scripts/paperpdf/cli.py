import argparse
import json
import sys


def main(argv=None):
    parser=argparse.ArgumentParser(description='Portable paper PDF layout/QA; all paper data comes from config JSON')
    parser.add_argument('command',choices=['preflight','crop','build','verify','render','review','delivery'])
    parser.add_argument('config')
    parser.add_argument('--sample',action='store_true',help='Use configured sample_ids for build/verify/render/review')
    parser.add_argument('--pages',default='',help='Visually inspected pages for review, e.g. 1,2,3 or all')
    parser.add_argument('--scale',type=float,default=1.5)
    args=parser.parse_args(argv)
    try:
        from .config import Paper
        paper=Paper(args.config)
        if args.command in {'preflight','crop','build'}:
            from .build import preflight,crop,build
            result={'preflight':lambda:preflight(paper),'crop':lambda:crop(paper),
                    'build':lambda:build(paper,args.sample)}[args.command]()
        else:
            from .verify import verify,render,record_review,delivery
            if args.command=='review' and not args.pages:
                raise ValueError('Review requires --pages after actually viewing the rendered pages')
            result={'verify':lambda:verify(paper,args.sample),'render':lambda:render(paper,args.sample,args.scale),
                    'review':lambda:record_review(paper,args.pages,args.sample),'delivery':lambda:delivery(paper)}[args.command]()
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 2 if isinstance(result,dict) and (result.get('passed') is False or result.get('ready') is False) else 0
    except (ValueError,FileNotFoundError,FileExistsError,ImportError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        return 2
