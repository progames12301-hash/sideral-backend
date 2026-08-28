from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from .catalog import MODEL_BY_ID, PRODUCT_BY_ID, REGIONS
from .config import MODEL_DATA_DIR
from .processing import load_field


def publish_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix(f"{target.suffix}.{os.getpid()}.tmp")
    shutil.copyfile(source,temporary)
    os.replace(temporary,target)


def update_manifest(run_dir: Path, forecast_hour: int) -> None:
    path=run_dir/"manifest.json"
    try:payload=json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError,json.JSONDecodeError):payload={}
    if not isinstance(payload,dict):payload={}
    hours={int(value) for value in payload.get("forecast_hours",[]) if str(value).isdigit()}
    hours.add(forecast_hour);payload["forecast_hours"]=sorted(hours)
    temporary=path.with_suffix(f".json.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    os.replace(temporary,path)


def main() -> None:
    parser=argparse.ArgumentParser(description="Publica um campo meteorológico real no armazenamento Sideral.")
    parser.add_argument("--model",required=True,choices=sorted(key for key,value in MODEL_BY_ID.items() if not value.planned))
    parser.add_argument("--run",required=True,help="Rodada YYYYMMDDHH")
    parser.add_argument("--product",required=True,choices=sorted(PRODUCT_BY_ID))
    parser.add_argument("--region",required=True,choices=sorted(REGIONS))
    parser.add_argument("--fh",required=True,type=int)
    group=parser.add_mutually_exclusive_group(required=True);group.add_argument("--npz",type=Path);group.add_argument("--png",type=Path)
    parser.add_argument("--data-dir",type=Path,default=MODEL_DATA_DIR)
    args=parser.parse_args()
    if len(args.run)!=10 or not args.run.isdigit():parser.error("--run deve usar YYYYMMDDHH")
    if not 0<=args.fh<=384:parser.error("--fh deve estar entre 0 e 384")
    source=(args.npz or args.png).resolve()
    if not source.is_file():parser.error("arquivo de origem inexistente")
    run_dir=args.data_dir.resolve()/args.model/args.run
    if args.npz:
        load_field(source,model=args.model,product=args.product,run=args.run,forecast_hour=args.fh)
        target=run_dir/"fields"/args.region/args.product/f"f{args.fh:03d}.npz"
    else:
        header=source.read_bytes()[:8]
        if header!=b"\x89PNG\r\n\x1a\n":parser.error("--png não contém um PNG válido")
        target=run_dir/"frames"/args.region/args.product/f"f{args.fh:03d}.png"
    publish_file(source,target);update_manifest(run_dir,args.fh)
    print(target)


if __name__=="__main__":main()
