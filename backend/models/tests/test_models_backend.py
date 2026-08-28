from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend.models.api import ModelApi
from backend.models.processing import combine_fields, common_grid, probability_exceedance, regrid_to_common_grid
from backend.models.processing.field import Field
from backend.models.processing.units import normalize_units


PNG_1X1 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


class ModelsBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name)
        self.data=root/"data";self.cache=root/"cache"
        self.api=ModelApi(self.data,self.cache)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def publish_png(self,model: str="ecmwf",run: str="2026082700",product: str="qpf24",fh: int=24) -> Path:
        path=self.data/model/run/"frames"/"brazil"/product/f"f{fh:03d}.png"
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(PNG_1X1)
        return path

    def test_empty_catalog_never_invents_runs(self) -> None:
        response=self.api.dispatch("/api/models/runs",{"model":["ecmwf"]})
        self.assertEqual(response.status,200)
        self.assertIn(b'"runs":[]',response.body)

    def test_serves_only_existing_png(self) -> None:
        self.publish_png()
        response=self.api.dispatch("/api/models/frame",{"model":["ecmwf"],"run":["2026082700"],"product":["qpf24"],"region":["brazil"],"fh":["24"]})
        self.assertEqual(response.status,200);self.assertEqual(response.content_type,"image/png");self.assertEqual(response.body,PNG_1X1)
        missing=self.api.dispatch("/api/models/frame",{"model":["gfs"],"run":["2026082700"],"product":["qpf24"],"region":["brazil"],"fh":["24"]})
        self.assertEqual(missing.status,404)

    def test_run_and_product_discovery(self) -> None:
        self.publish_png()
        runs=self.api.dispatch("/api/models/runs",{"model":["ecmwf"]})
        products=self.api.dispatch("/api/models/products",{"model":["ecmwf"],"run":["2026082700"]})
        self.assertIn(b"2026082700",runs.body);self.assertIn(b"qpf24",products.body);self.assertIn(b"24",products.body)

    def test_unit_normalization_and_regrid(self) -> None:
        source=Field(lat=np.array([-34.,-33.,-32.]),lon=np.array([-52.,-51.,-50.]),values=np.full((3,3),.01),unit="m",valid_time="2026-08-28T00:00:00Z",model="ecmwf",product="qpf24",run="2026082700",forecast_hour=24)
        normalized=normalize_units(source,"precipitation","mm")
        self.assertTrue(np.allclose(normalized.values,10.0));self.assertEqual(normalized.unit,"mm")
        target_lat=np.array([-34.,-33.5,-33.,-32.5,-32.]);target_lon=np.array([-52.,-51.5,-51.,-50.5,-50.])
        output=regrid_to_common_grid(normalized,target_lat,target_lon)
        self.assertEqual(output.values.shape,(5,5));self.assertTrue(np.allclose(output.values,10.0))

    def test_multimodel_statistics_and_probability_require_members(self) -> None:
        lat=np.array([-34.,-33.]);lon=np.array([-52.,-51.])
        def field(model: str,value: float) -> Field:
            return Field(lat=lat,lon=lon,values=np.full((2,2),value),unit="mm",valid_time="2026-08-28T00:00:00Z",model=model,product="qpf24",run="2026082700",forecast_hour=24)
        fields=[field("ecmwf",20),field("gfs",60),field("icon",100)]
        median,members=combine_fields(fields,"median",2)
        probability,probability_members=probability_exceedance(fields,50,2)
        self.assertTrue(np.allclose(median.values,60));self.assertTrue(np.all(members==3))
        self.assertTrue(np.allclose(probability.values,200/3));self.assertTrue(np.all(probability_members==3))
        with self.assertRaises(ValueError):combine_fields(fields[:1],"mean",2)

    def test_common_grid_is_real_region_definition(self) -> None:
        lat,lon=common_grid("south_brazil",.25)
        self.assertAlmostEqual(lat[0],-34.5);self.assertAlmostEqual(lon[0],-58.5)
        self.assertGreater(lat.size,10);self.assertGreater(lon.size,10)


if __name__ == "__main__":
    unittest.main()
