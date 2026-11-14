from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from backend import metbr_skewt_batch as batch


class GlobalSkewTClickPointTests(unittest.TestCase):
    def test_registry_contains_only_the_three_conventional_global_keys(self):
        expected = {
            "gfs": (
                "GLOBAL_GFS_SKEWT_CLICKPOINT.yml",
                "gfs-skewt-click",
                384,
            ),
            "icon_dwd": (
                "GLOBAL_ICON_DWD_SKEWT_CLICKPOINT.yml",
                "icon-dwd-skewt-click",
                180,
            ),
            "ecmwf_ifs": (
                "GLOBAL_ECMWF_IFS_SKEWT_CLICKPOINT.yml",
                "ecmwf-ifs-skewt-click",
                360,
            ),
        }
        for model_key, (workflow, prefix, maximum) in expected.items():
            with self.subTest(model_key=model_key):
                key, config = batch._model_config(model_key)
                self.assertEqual(key, model_key)
                self.assertEqual(config["workflow"], workflow)
                self.assertEqual(config["release_prefix"], prefix)
                self.assertEqual(config["max_forecast_hour"], maximum)
                self.assertTrue(config["uses_forecast_hour"])

        for unsupported in ("gfs_ai", "icon_ai", "aifs", "ecmwf_aifs"):
            with self.subTest(unsupported=unsupported):
                with self.assertRaises(ValueError):
                    batch._model_config(unsupported)

    def test_dispatch_forwards_selected_forecast_hour_to_each_global_workflow(self):
        expected = {
            "gfs": "GLOBAL_GFS_SKEWT_CLICKPOINT.yml",
            "icon_dwd": "GLOBAL_ICON_DWD_SKEWT_CLICKPOINT.yml",
            "ecmwf_ifs": "GLOBAL_ECMWF_IFS_SKEWT_CLICKPOINT.yml",
        }
        with patch.dict("os.environ", {"METBR_SKEWT_GITHUB_TOKEN": "test-token"}):
            with patch.object(batch, "_check_dispatch_rate"):
                with patch.object(
                    batch.requests,
                    "post",
                    return_value=Mock(status_code=204),
                ) as post:
                    for index, (model_key, workflow) in enumerate(expected.items()):
                        with self.subTest(model_key=model_key):
                            result = batch.dispatch_request(
                                {
                                    "model_key": model_key,
                                    "latitude": -23.55,
                                    "longitude": -46.63,
                                    "forecast_hour": 12 + index * 3,
                                },
                                client_ip=f"test-{model_key}",
                            )
                            self.assertEqual(result["workflow"], workflow)
                            self.assertEqual(result["model_key"], model_key)
                            self.assertEqual(result["forecast_hour"], 12 + index * 3)
                            body = post.call_args.kwargs["json"]
                            self.assertEqual(body["ref"], "main")
                            self.assertEqual(body["inputs"]["latitude"], "-23.550000")
                            self.assertEqual(body["inputs"]["longitude"], "-46.630000")
                            self.assertEqual(body["inputs"]["forecast_hour"], str(12 + index * 3))
                            self.assertEqual(body["inputs"]["request_id"], result["request_id"])
                            self.assertEqual(
                                post.call_args.args[0].rsplit("/", 2)[-2],
                                workflow,
                            )

    def test_global_dispatch_rejects_missing_or_out_of_range_forecast_hour(self):
        cases = (
            ({"model_key": "gfs", "latitude": 0, "longitude": 0}, "forecast_hour"),
            (
                {
                    "model_key": "icon_dwd",
                    "latitude": 0,
                    "longitude": 0,
                    "forecast_hour": 181,
                },
                "F000–F180",
            ),
            (
                {
                    "model_key": "ecmwf_ifs",
                    "latitude": 91,
                    "longitude": 0,
                    "forecast_hour": 3,
                },
                "Latitude",
            ),
        )
        for payload, message in cases:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, message):
                    batch.dispatch_request(payload, client_ip="validation-test")

    def test_profile_lookup_is_bound_to_request_model_and_forecast_hour(self):
        request_id = "a" * 32
        model_key = "icon_dwd"
        status = {
            "status": "completed",
            "request_id": request_id,
            "model_key": model_key,
            "available_hours": [0, 3, 6],
        }
        profile = {
            "schema": "sideral-global-nwp-skewt-v1",
            "request_id": request_id,
            "model_key": model_key,
            "forecast_hour": 3,
            "profile": [{"pressure_hpa": 1000, "temperature_c": 20}],
        }
        with patch.object(batch, "_get_json_asset", side_effect=[status, profile]) as get_asset:
            result = batch.get_profile(request_id, 3, model_key)
        self.assertEqual(result, profile)
        self.assertEqual(get_asset.call_args_list[0].args[1], "status.json")
        self.assertEqual(get_asset.call_args_list[1].args[1], "f003.json")

    def test_profile_lookup_rejects_unavailable_hour(self):
        request_id = "b" * 32
        status = {
            "status": "completed",
            "request_id": request_id,
            "model_key": "gfs",
            "available_hours": [0, 1, 2],
        }
        with patch.object(batch, "_get_json_asset", return_value=status):
            with self.assertRaisesRegex(FileNotFoundError, "F006"):
                batch.get_profile(request_id, 6, "gfs")

    def test_legacy_wrf_dispatch_does_not_gain_a_new_required_input(self):
        with patch.dict("os.environ", {"METBR_SKEWT_GITHUB_TOKEN": "test-token"}):
            with patch.object(batch, "_check_dispatch_rate"):
                with patch.object(
                    batch.requests,
                    "post",
                    return_value=Mock(status_code=204),
                ) as post:
                    result = batch.dispatch_request(
                        {
                            "model_key": "tarc_wrf_3km",
                            "latitude": -23.5,
                            "longitude": -46.6,
                        },
                        client_ip="legacy-wrf-test",
                    )
        self.assertEqual(result["model_key"], "tarc_wrf_3km")
        self.assertNotIn("forecast_hour", post.call_args.kwargs["json"]["inputs"])


if __name__ == "__main__":
    unittest.main()
