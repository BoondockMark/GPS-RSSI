"""Offline data tools, shared by workspace jobs and the command line."""
import json
from pathlib import Path


def add_commands(commands):
    parser=commands.add_parser('offline',help='Prepare and transfer offline maps and queried terrain elevations.')
    parser.add_argument('--directory',type=Path,default=Path.cwd(),help='App folder (default: current folder)')
    actions=parser.add_subparsers(dest='offline_action',required=True)
    actions.add_parser('status',help='Show cached coverage counts, local map metadata and pending gaps.')
    mode=actions.add_parser('mode',help='Enable cache-only operation; no tile or elevation network requests.')
    mode.add_argument('mode',choices=('on','off'))
    save=actions.add_parser('export',help='Pin current cache entries and save a portable offline ZIP.')
    save.add_argument('--output',type=Path,required=True)
    load=actions.add_parser('import',help='Merge an offline ZIP without removing newer cached entries.')
    load.add_argument('input',type=Path)
    mb=actions.add_parser('map',help='Import a regional PNG/JPEG raster MBTiles file (TMS rows).')
    mb.add_argument('input',type=Path)
    fill=actions.add_parser('fill',help='Fill queued missing data with connectivity; does not prefetch regions.')
    fill.add_argument('--limit',type=int,default=1000)
    fill.add_argument('--output',type=Path,help='Optional job result directory')


def run(args):
    from .offline import OfflineData
    data=OfflineData(args.directory.resolve()/'data')
    action=args.offline_action
    if action=='mode':
        data.set_enabled(args.mode=='on');result=data.status()
    elif action=='export': result=data.export_pack(args.output)
    elif action=='import': result=data.import_pack(args.input)
    elif action=='map': result=data.import_mbtiles(args.input)
    elif action=='fill':
        result=data.fill(args.limit)
        if result['failed']:
            result['error']=f"Filled {result['filled']} queued requests; {result['failed']} lookups failed. Check connectivity and retry."
        if args.output:
            args.output.mkdir(parents=True,exist_ok=True)
            (args.output/'report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    else: result=data.status()
    print(json.dumps(result,indent=2,allow_nan=False))
    return 1 if result.get('failed') else 0
