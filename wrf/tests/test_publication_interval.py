"""Validate 3-hour publication and compatibility with hourly GFS/old workflows."""
import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

WRF = Path(__file__).resolve().parents[1]


class IntervalTests(unittest.TestCase):
    def filter(self, interval, missing=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'icon').mkdir()
            tomorrow = dt.datetime.now(ZoneInfo('America/Sao_Paulo')).date() + dt.timedelta(days=1)
            start = dt.datetime.combine(tomorrow, dt.time(), ZoneInfo('America/Sao_Paulo'))
            frames = []
            for i in range(48):
                valid = start + dt.timedelta(hours=i)
                file = f'icon/f{i+27:03d}.json.gz'
                (root / file).write_bytes(b'fixture')
                frames.append(dict(file=file, forecastHour=i+27, validTime=valid.astimezone(dt.timezone.utc).isoformat(), reflectivitySource='REFL_10CM_NATIVE'))
            if missing:
                frames.pop(3)
            metadata = dict(model='icon', reflectivitySource='REFL_10CM_NATIVE', initTime=(start-dt.timedelta(hours=27)).isoformat(), frames=frames)
            (root / 'metadata.json').write_text(json.dumps(metadata))
            result = subprocess.run([sys.executable, str(WRF / 'filter_next_day.py'), '--root', str(root), '--days', '2', '--interval-hours', str(interval)], capture_output=True, text=True)
            if missing:
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(list((root / 'icon').glob('*.gz'))), 48)
                return
            self.assertEqual(result.returncode, 0, result.stderr)
            meta = json.loads((root / 'metadata.json').read_text())
            expected = 48 // interval
            self.assertEqual(meta['frameCount'], expected)
            self.assertEqual(meta['temporalResolutionMinutes'], interval*60)
            self.assertEqual(meta['localHours'], list(range(0,24,interval))*2)
            self.assertEqual(len(list((root / 'icon').glob('*.gz'))), expected)
            self.assertEqual([f['forecastHour'] for f in meta['frames']], list(range(27,75,interval)))

    def test_three_hour_two_days(self):
        self.filter(3)

    def test_hourly_compatibility(self):
        self.filter(1)

    def test_incomplete_day_fails_before_deleting_files(self):
        self.filter(3, missing=True)

    def test_restart_segments_keep_configurable_output_and_physics(self):
        for start, end in [(0,24),(24,48),(48,69)]:
            with self.subTest(start=start), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                shutil.copytree(WRF, root / 'wrf')
                restart = root / 'wrfrst_d01_fixture'
                restart.write_bytes(b'fixture')
                cmd=[sys.executable, str(WRF/'prepare_restart_segment.py'), '--root', str(root), '--start-hour', str(start), '--end-hour', str(end)]
                if start:
                    cmd += ['--restart-file', str(restart)]
                result=subprocess.run(cmd, capture_output=True, text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                text=(root/'wrf/run_wrf_with_source.sh').read_text()
                self.assertIn('history_interval = ${WRF_HISTORY_INTERVAL_MINUTES}',text)
                self.assertIn('time_step = 24,',text)
                self.assertIn('interval_seconds = 10800,',text)
                self.assertEqual(subprocess.run(['bash','-n',str(root/'wrf/run_wrf_with_source.sh')]).returncode,0)


if __name__ == '__main__':
    unittest.main()
