#!/usr/bin/env python3
import argparse, json, glob, os
from pathlib import Path
import numpy as np
from netCDF4 import Dataset, num2date


def native_reflectivity(ds):
    for name in ("REFL_10CM", "REFL_10CM_NATIVE"):
        if name in ds.variables:
            a = np.asarray(ds.variables[name][:])
            if a.ndim == 3:
                return np.nanmax(a, axis=0).astype(float)
            if a.ndim == 2:
                return a.astype(float)
    raise RuntimeError("REFL_10CM/REFL_10CM_NATIVE ausente no wrfout; refletividade aproximada nao e permitida")


def scalar(v):
    if np.ma.isMaskedArray(v): v = v.filled(np.nan)
    a = np.asarray(v)
    if a.size == 1: return float(a.reshape(-1)[0])
    return None


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--run-dir',required=True); ap.add_argument('--output-dir',required=True)
    ap.add_argument('--grid-x',type=int,required=True); ap.add_argument('--grid-y',type=int,required=True)
    ap.add_argument('--run-env'); args=ap.parse_args()
    files=sorted(glob.glob(os.path.join(args.run_dir,'wrfout_d01_*')))
    if not files: raise SystemExit('Nenhum wrfout_d01 encontrado')
    frames=[]; init_time=None
    for fn in files:
        with Dataset(fn) as ds:
            refl=native_reflectivity(ds)
            if refl.shape != (args.grid_y,args.grid_x):
                raise SystemExit(f'Grade inesperada em {fn}: {refl.shape}, esperado {(args.grid_y,args.grid_x)}')
            times=ds.variables.get('Times')
            t=''
            if times is not None:
                raw=times[:]
                if raw.ndim==2: t=''.join(x.decode() if isinstance(x,bytes) else str(x) for x in raw[0]).replace('_','T')+'Z'
            if init_time is None: init_time=t
            frame={'time':t,'reflectivityDbz':np.nan_to_num(refl,nan=-9999.0).tolist()}
            frames.append(frame)
    out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    data={'schemaVersion':'1.0','model':'WRF Brasil','resolutionKm':21,'grid':{'nx':args.grid_x,'ny':args.grid_y},'frames':frames,'frameCount':len(frames),'temporalResolutionMinutes':60,'reflectivitySource':'REFL_10CM_NATIVE','nativeGrid':True,'initTime':init_time}
    (out/'wrf_brasil_21km.json').write_text(json.dumps(data,separators=(',',':')),encoding='utf-8')
    (out/'metadata.json').write_text(json.dumps({'resolutionKm':21,'nx':args.grid_x,'ny':args.grid_y,'frameCount':len(frames),'temporalResolutionMinutes':60,'reflectivitySource':'REFL_10CM_NATIVE','nativeGrid':True,'model':'WRF Brasil'},separators=(',',':')),encoding='utf-8')
    print(f'Publicado extrator nativo: {len(frames)} frames, {args.grid_x}x{args.grid_y}')
if __name__=='__main__': main()
