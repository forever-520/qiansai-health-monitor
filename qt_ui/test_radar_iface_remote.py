import json
import unittest
from unittest import mock

from qt.qt import radar_iface


class RadarIfaceRemoteTests(unittest.TestCase):
    def setUp(self):
        radar_iface._reset_display_state()

    def _response(self, payload):
        response = mock.Mock()
        response.read.return_value = json.dumps(payload).encode("utf-8")
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        return response

    def test_remote_mode_flag_reads_from_environment(self):
        with mock.patch.dict("os.environ", {"RADAR_REMOTE_URL": "http://10.216.239.84:8000/radar/raw"}, clear=False):
            self.assertEqual(
                radar_iface._remote_url_from_env(),
                "http://10.216.239.84:8000/radar/raw",
            )

    def test_snapshot_from_remote_maps_core_fields_and_waves(self):
        payload = {
            "human": {"exist": 1, "motion_state": 2, "motion_val": 7, "distance": 42, "x": 3, "y": 4, "z": 0},
            "heart": {"rate": 78, "wave": list(range(32))},
            "breath": {"rate": 18, "state": 1, "wave": list(range(-16, 16))},
            "sleep": {
                "bed": 1, "state": 2, "awake_time": 1, "light_time": 2, "deep_time": 3,
                "score": 80, "avg_breath": 16, "avg_heart": 70, "turn_over": 1,
                "big_motion_ratio": 2, "small_motion_ratio": 3, "apnea": 0, "total_sleep": 6,
                "awake_ratio": 4, "light_ratio": 5, "deep_ratio": 6, "out_bed_time": 0,
                "out_bed_count": 0, "exception": 3, "rating": 1, "struggle": 0, "nobody_timer": 0,
            },
            "system": {"frame_count": 12, "parse_error_count": 0, "checksum_error_count": 0, "last_frame_hex": "53 59"},
        }

        with mock.patch("radar_iface.urlopen", return_value=self._response(payload)):
            vitals = radar_iface._remote_vitals("http://example/radar/raw")

        self.assertEqual(vitals.exist, 1)
        self.assertEqual(vitals.motion_state, 2)
        self.assertEqual(vitals.distance, 42)
        self.assertEqual(vitals.heart_rate, 78)
        self.assertEqual(vitals.heart_wave, list(range(32)))
        self.assertEqual(vitals.breath_wave, list(range(-16, 16)))
        self.assertEqual(vitals.frame_count, 12)

    def test_get_vitals_builds_display_waves_when_remote_wave_is_flat(self):
        payload = {
            "human": {"exist": 1, "motion_state": 1, "motion_val": 8, "distance": 35, "x": 0, "y": 30, "z": 0},
            "heart": {"rate": 82, "wave": [0] * 15},
            "breath": {"rate": 16, "state": 1, "wave": [0] * 10},
            "sleep": {"bed": 1, "state": 2},
            "system": {"frame_count": 101, "parse_error_count": 0, "checksum_error_count": 0, "last_frame_hex": "53 59"},
        }

        with mock.patch.dict("os.environ", {"RADAR_REMOTE_URL": "http://example/radar/raw"}, clear=False):
            with mock.patch("radar_iface.urlopen", return_value=self._response(payload)):
                vitals = radar_iface.get_vitals()

        self.assertEqual(vitals.heart_rate, 82)
        self.assertEqual(vitals.breath_rate, 16)
        self.assertEqual(len(vitals.heart_wave), 300)
        self.assertEqual(len(vitals.breath_wave), 300)
        self.assertGreater(len(set(vitals.heart_wave)), 1)
        self.assertGreater(len(set(vitals.breath_wave)), 1)

    def test_get_vitals_ignores_remote_rate_drops_to_implausible_values(self):
        payload_ok = {
            "human": {"exist": 1, "motion_state": 1, "motion_val": 8, "distance": 35, "x": 0, "y": 30, "z": 0},
            "heart": {"rate": 79, "wave": [0] * 15},
            "breath": {"rate": 17, "state": 1, "wave": [0] * 10},
            "sleep": {"bed": 1, "state": 2},
            "system": {"frame_count": 102, "parse_error_count": 0, "checksum_error_count": 0, "last_frame_hex": "53 59"},
        }
        payload_bad = {
            "human": {"exist": 1, "motion_state": 1, "motion_val": 8, "distance": 35, "x": 0, "y": 30, "z": 0},
            "heart": {"rate": 5, "wave": [0] * 15},
            "breath": {"rate": 1, "state": 1, "wave": [0] * 10},
            "sleep": {"bed": 1, "state": 2},
            "system": {"frame_count": 103, "parse_error_count": 0, "checksum_error_count": 0, "last_frame_hex": "53 59"},
        }

        with mock.patch.dict("os.environ", {"RADAR_REMOTE_URL": "http://example/radar/raw"}, clear=False):
            with mock.patch(
                "radar_iface.urlopen",
                side_effect=[self._response(payload_ok), self._response(payload_bad)],
            ):
                first = radar_iface.get_vitals()
                second = radar_iface.get_vitals()

        self.assertEqual(first.heart_rate, 79)
        self.assertEqual(first.breath_rate, 17)
        self.assertEqual(second.heart_rate, 79)
        self.assertEqual(second.breath_rate, 17)


if __name__ == "__main__":
    unittest.main()
