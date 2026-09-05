"""Synthetic fixtures are used only in unit tests, never in the live service."""
import gzip
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import h5py
import numpy as np
from .odim import read_volume, beamHeight, radialVelocity, calculateVelocityDelta
from .server import Store, handler_for

def fixture(path, velocity=True):
    with h5py.File(path,'w') as f:
        f.create_group('where').attrs.update(lat=-27.,lon=-52.,height=800.)
        for i,el in enumerate((1.5,.5),1):
            s=f.create_group(f'dataset{i}')
            s.create_group('where').attrs.update(elangle=el,nrays=4,nbins=3,rscale=250.,rstart=.5)
            s.create_group('what').attrs.update(startdate='20260905',starttime='212400')
            s.create_group('how').attrs.update(startazA=[359.5,0.,.5,1.],stopazA=[0.,.5,1.,1.5])
            for k,q in enumerate(['DBZH']+(['VRADH'] if velocity else []),1):
                d=s.create_group(f'data{k}')
                d.create_group('what').attrs.update(quantity=q,gain=.5,offset=-32.,nodata=255,undetect=0)
                d.create_dataset('data',data=np.array([[255,0,64],[74,84,94],[100,110,120],[10,20,30]],dtype='u1'))

class Tests(unittest.TestCase):
    def test_physics(self):
        self.assertAlmostEqual(float(beamHeight(0,.5,800)),800)
        self.assertAlmostEqual(float(radialVelocity(10,0,0,90,0)),10)
        self.assertAlmostEqual(float(radialVelocity(10,0,0,270,0)),-10)
        self.assertEqual(calculateVelocityDelta([-28,31]),59)

    def test_decode_cache_and_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'input'; directory=root/'test-radar';directory.mkdir(parents=True)
            path=directory/'volume.h5';fixture(path)
            meta,raw=read_volume(path,'test-radar','reflectivity')
            self.assertEqual(meta['elevation'],.5);self.assertEqual(meta['rangeStart'],500)
            self.assertEqual(np.frombuffer(raw,dtype='<i2')[:4].tolist(),[-32768,-32768,-32768,500])
            meta,raw=read_volume(path,'test-radar','velocity')
            self.assertEqual(meta['unit'],'m/s');self.assertEqual(np.frombuffer(raw,dtype='<i2')[2],0)
            store=Store(root,Path(tmp)/'cache');frames=store.frames('test-radar','velocity')
            cached=store.cache/(frames[0]['frameId']+'.bin.gz');modified=cached.stat().st_mtime_ns
            store.frames('test-radar','velocity');self.assertEqual(modified,cached.stat().st_mtime_ns)
            self.assertEqual(gzip.decompress(cached.read_bytes()),raw)
            server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(store));threading.Thread(target=server.serve_forever,daemon=True).start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                self.assertEqual(json.load(urlopen(base+'/health'))['status'],'ok')
                self.assertEqual(json.load(urlopen(base+'/api/radar/v3/health'))['service'],'Brasil Scope V3')
                self.assertEqual(json.load(urlopen(base+'/api/radar/v3/products?radar=test-radar'))['products'],['reflectivity','velocity'])
                req=Request(base+'/api/radar/v3/latest?radar=test-radar&product=velocity',headers={'Origin':'http://localhost:8000'})
                response=urlopen(req);self.assertEqual(response.headers['Access-Control-Allow-Origin'],'http://localhost:8000')
                m=json.load(response);self.assertEqual(gzip.decompress(urlopen(base+m['dataUrl']).read()),raw)
                with self.assertRaises(HTTPError):urlopen(base+'/api/radar/v3/data?id=../../secret')
                fixture(path,False)
                with self.assertRaises(HTTPError) as error:urlopen(base+'/api/radar/v3/latest?radar=test-radar&product=velocity')
                self.assertEqual(error.exception.code,404)
                self.assertEqual(json.load(urlopen(base+'/api/radar/v3/latest?radar=test-radar&product=reflectivity'))['unit'],'dBZ')
            finally:server.shutdown();server.server_close()

    def test_cemaden_inspection_variants_and_nyquist(self):
        from .adapters.cemaden import inspect_volume, read
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'V.vol.h5';fixture(path)
            with h5py.File(path,'a') as f:
                for key in ('dataset1','dataset2'):
                    f[key+'/data2/what'].attrs.update(quantity='VRADHC',unit='kts')
                    f[key+'/how'].attrs.update(NI=24.5,highprf=1000.,wavelength=5.3)
            metadata,binary=read(path,'manual-test','velocity')
            self.assertEqual(metadata['source'],'CEMADEN');self.assertEqual(metadata['nyquistVelocity'],24.5)
            self.assertEqual(metadata['quantity'],'VRADHC');self.assertFalse(metadata['dealiasedBySideral'])
            self.assertEqual(np.frombuffer(binary,dtype='<i2')[3],257)
            self.assertTrue(any(i['path']=='/dataset2/data2/data' for i in inspect_volume(path)))
            with h5py.File(path,'a') as f:
                for key in ('dataset1','dataset2'):f[key+'/data2/what'].attrs['quantity']='WRAD'
            with self.assertRaises(LookupError):read(path,'manual-test','velocity')

    def test_cptec_worldfile_pixel_centers(self):
        import io
        from PIL import Image
        from .adapters.cptec import reproject_png
        arr=np.zeros((4,4,4),dtype=np.uint8);arr[1:3,1:3]=[10,240,0,255]
        stream=io.BytesIO();Image.fromarray(arr).save(stream,format='PNG')
        binary,corners=reproject_png(stream.getvalue(),'0.1\n0\n0\n-0.1\n-52\n-27')
        self.assertAlmostEqual(corners[0][0],-52.05);self.assertAlmostEqual(corners[0][1],-26.95)
        im=np.asarray(Image.open(io.BytesIO(binary)));self.assertTrue(np.any(im[:,:,3]==0));self.assertTrue(np.any(im[:,:,3]>0))
        self.assertEqual(set(map(tuple,im.reshape(-1,4))),{(0,0,0,0),(10,240,0,255)})

if __name__=='__main__':unittest.main()
