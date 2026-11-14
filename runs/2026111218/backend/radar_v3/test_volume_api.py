"""Multi-sweep regression tests for Brasil Scope V5."""
import gzip
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import urlopen
from http.server import ThreadingHTTPServer
import h5py
import numpy as np
from .odim import read_volume, read_volume_sweeps
from .server import Store, handler_for


def fixture(path):
    with h5py.File(path,'w') as f:
        f.create_group('where').attrs.update(lat=-27.,lon=-52.,height=800.)
        for i,el in enumerate((2.4,.5,1.3),1):
            s=f.create_group(f'dataset{i}')
            s.create_group('where').attrs.update(elangle=el,nrays=4,nbins=3,rscale=250.,rstart=.5)
            s.create_group('what').attrs.update(startdate='20260915',starttime='212400')
            s.create_group('how').attrs.update(startazA=[359.5,0.,.5,1.],stopazA=[0.,.5,1.,1.5])
            d=s.create_group('data1')
            d.create_group('what').attrs.update(quantity='DBZH',gain=.5,offset=-32.,nodata=255,undetect=0)
            d.create_dataset('data',data=np.array([[255,0,74],[84,94,104],[114,124,134],[144,154,164]],dtype='u1'))


class VolumeTests(unittest.TestCase):
    def test_all_sweeps_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'volume.h5';fixture(path)
            sweeps=read_volume_sweeps(path,'test-radar','reflectivity')
            self.assertEqual([s[0]['elevation'] for s in sweeps],[.5,1.3,2.4])
            self.assertEqual([s[0]['sweepIndex'] for s in sweeps],[0,1,2])
            self.assertTrue(all(s[0]['sweepCount']==3 for s in sweeps))
            low,_=read_volume(path,'test-radar','reflectivity')
            self.assertEqual(low['elevation'],.5)
            self.assertEqual(low['sweepCount'],3)

    def test_volume_http_manifest_and_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'input'; directory=root/'test-radar';directory.mkdir(parents=True)
            fixture(directory/'volume.h5')
            store=Store(root,Path(tmp)/'cache')
            volumes=store.volumes('test-radar','reflectivity')
            self.assertEqual(len(volumes),1)
            self.assertEqual(volumes[0]['elevations'],[.5,1.3,2.4])
            self.assertEqual(volumes[0]['sweepCount'],3)
            detailed=store.volume(volumes[0]['volumeId'])
            self.assertEqual(len(detailed['sweeps']),3)
            self.assertEqual(detailed['sweeps'][1]['elevation'],1.3)
            server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(store));threading.Thread(target=server.serve_forever,daemon=True).start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                listing=json.load(urlopen(base+'/api/radar/v3/volumes?radar=test-radar&product=reflectivity'))
                self.assertEqual(listing['volumes'][0]['sweepCount'],3)
                volume_id=listing['volumes'][0]['volumeId']
                manifest=json.load(urlopen(base+'/api/radar/v3/volume?id='+volume_id))
                self.assertEqual([s['elevation'] for s in manifest['sweeps']],[.5,1.3,2.4])
                first=manifest['sweeps'][0]
                binary=urlopen(base+first['dataUrl']).read()
                self.assertEqual(len(gzip.decompress(binary)),first['rayCount']*first['gateCount']*2)
            finally:
                server.shutdown();server.server_close()


if __name__=='__main__': unittest.main()
