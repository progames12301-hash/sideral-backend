"""Regressions for native GFS REFC publication (network replaced by GRIB fixtures)."""
import datetime as dt
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import numpy as np
from backend.models import publish_remote as pub


class NativeGfsTests(unittest.TestCase):
    model_run = dt.datetime(2026, 8, 31, tzinfo=dt.timezone.utc)

    def messages(self, include_refc=True):
        values = {'2t': [300]*4, '2d': [290]*4, '10u': [2]*4, '10v': [1]*4,
                  'tp': [3]*4, 'cape': [100]*4, 'pwat': [20]*4}
        if include_refc:
            values['refc'] = [-10, 50.5, 99, 0]
        return [dict(shortName=name, values=np.array(data), endStep=27,
                     latitudes=np.array([-25, -25, -24, -24]),
                     longitudes=np.array([-50, -49, -50, -49]))
                for name, data in values.items()]

    def build(self, output, messages):
        grid = np.meshgrid(np.array([-50, -49]), np.array([-25, -24]))
        with patch.object(pub, 'discover_gfs', return_value=(self.model_run, dt.date(2026, 9, 1), [27, 30])), \
             patch.object(pub, 'download_gfs', return_value=messages), \
             patch.object(pub, 'target_grid', return_value=(grid[1], grid[0])), \
             patch.object(pub, 'nearest_indices', return_value=np.arange(4)), \
             patch.object(pub, 'GRID_X', 2), patch.object(pub, 'GRID_Y', 2):
            pub.build_gfs(output)

    def test_native_values_and_source_in_frames_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.build(root, self.messages())
            meta = json.loads((root / 'metadata.json').read_text())
            self.assertTrue(meta['capabilities']['reflectivity'])
            self.assertEqual(meta['reflectivitySource'], 'GFS_REFC_NATIVE')
            self.assertEqual(meta['temporalResolutionMinutes'], 180)
            self.assertEqual([f['forecastHour'] for f in meta['frames']], [27, 30])
            for frame in meta['frames']:
                with gzip.open(root / frame['file'], 'rt') as stream:
                    payload = json.load(stream)
                self.assertEqual(payload['fields']['reflectivity'], [0, 50.5, 95, 0])
                self.assertEqual(payload['reflectivitySource'], 'GFS_REFC_NATIVE')
                self.assertEqual(payload['validTime'], frame['validTime'])

    def test_missing_refc_does_not_publish_a_blank_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'metadata.json').write_text('previous publication')
            with self.assertRaisesRegex(RuntimeError, 'refc'):
                self.build(root, self.messages(False))
            self.assertEqual((root / 'metadata.json').read_text(), 'previous publication')
            self.assertEqual(list(root.glob('gfs/*.gz')), [])

    def test_refc_nan_is_rejected(self):
        messages = self.messages()
        messages[-1]['values'][0] = np.nan
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(RuntimeError, 'dados ausentes'):
            self.build(Path(tmp), messages)

    def test_nomads_request_includes_composite_entire_atmosphere(self):
        response = Mock(content=b'GRIBfixture')
        with patch.object(pub.requests, 'get', return_value=response) as get, \
             patch.object(pub, 'read_grib_bytes', return_value=[]) as decode:
            pub.download_gfs(self.model_run, 27)
        params = get.call_args.kwargs['params']
        self.assertEqual(params['var_REFC'], 'on')
        self.assertEqual(params['lev_entire_atmosphere'], 'on')
        self.assertNotIn('var_REFD', params)
        decode.assert_called_once_with(b'GRIBfixture')


if __name__ == '__main__':
    unittest.main()
