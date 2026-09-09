import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

WRF = Path(__file__).resolve().parents[1]


class PublicationRegressions(unittest.TestCase):
    def invoke(self, script, *args):
        return subprocess.run([sys.executable, str(WRF / script), *map(str, args)], capture_output=True, text=True)

    def segment(self, root, hours, marker, init='2026-09-08T12:00:00Z'):
        (root / 'ecmwf').mkdir(parents=True)
        frames = []
        for hour in hours:
            file = f'ecmwf/f{hour:03d}.json.gz'
            (root / file).write_bytes(marker)
            valid = dt.datetime.fromisoformat(init) + dt.timedelta(hours=hour)
            frames.append(dict(file=file, forecastHour=hour, validTime=valid.isoformat(), reflectivitySource='REFL_10CM_NATIVE'))
        (root / 'metadata.json').write_text(json.dumps(dict(model='ecmwf', initTime=init, reflectivitySource='REFL_10CM_NATIVE', frames=frames)))

    def test_midnight_does_not_shift_publication_window(self):
        for cycle in (0, 6, 12, 18):
            with self.subTest(cycle=cycle), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.segment(root, range(0, 73, 3), b'fixture', f'2026-09-08T{cycle:02d}:00:00Z')
                result = self.invoke('filter_next_day.py', '--root', root, '--days', 2, '--interval-hours', 3)
                self.assertEqual(result.returncode, 0, result.stderr)
                metadata = json.loads((root / 'metadata.json').read_text())
                self.assertEqual(metadata['forecastLocalDates'], ['2026-09-09', '2026-09-10'])
                self.assertEqual(metadata['frameCount'], 16)
                self.assertEqual(len(list((root / 'ecmwf').glob('*.gz'))), 25)

    def test_overlap_keeps_integrated_frame_in_either_input_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / 'a', root / 'b'
            self.segment(a, [0, 3, 6, 9], b'integrated')
            self.segment(b, [9, 12, 15, 18], b'cold-start')
            for first, second in [(a, b), (b, a)]:
                result = self.invoke('merge_wrf_segments.py', '--input', first, '--input', second, '--output', root / 'out')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((root / 'out/ecmwf/f009.json.gz').read_bytes(), b'integrated')

    def test_mixed_runs_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.segment(root / 'a', [0, 3], b'a')
            self.segment(root / 'b', [3, 6], b'b', '2026-09-09T12:00:00Z')
            result = self.invoke('merge_wrf_segments.py', '--input', root / 'a', '--input', root / 'b', '--output', root / 'out')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('rodadas diferentes', result.stderr)


if __name__ == '__main__':
    unittest.main()
