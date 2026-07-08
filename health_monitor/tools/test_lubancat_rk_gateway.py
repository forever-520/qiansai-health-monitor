import unittest

from health_monitor.tools import lubancat_rk_gateway as gateway


def radar_frame(ctrl, cmd, payload):
    head = bytes([0x53, 0x59, ctrl, cmd, 0x00, len(payload)]) + bytes(payload)
    checksum = sum(head) & 0xFF
    return head + bytes([checksum, 0x54, 0x43])


class LubanCatRkGatewayTests(unittest.TestCase):
    def test_update_from_radar_frame_updates_metrics(self):
        state = gateway.SharedState()
        state.update_from_radar_frame(radar_frame(0x85, 0x02, [78]))
        state.update_from_radar_frame(radar_frame(0x81, 0x02, [18]))
        state.update_from_radar_frame(radar_frame(0x80, 0x03, [26]))
        msg = state.build_vital_message()
        self.assertEqual(msg["data"]["hr"], 78.0)
        self.assertEqual(msg["data"]["br"], 18.0)
        self.assertEqual(msg["data"]["motion"], 26.0)

    def test_wave_message_uses_real_wave_when_available(self):
        state = gateway.SharedState()
        heart_payload = [(0x80 + (i % 5)) for i in range(24)]
        breath_payload = [(0x7E + (i % 5)) for i in range(24)]
        state.update_from_radar_frame(radar_frame(0x85, 0x85, heart_payload))
        state.update_from_radar_frame(radar_frame(0x81, 0x85, breath_payload))
        msg = state.build_waveform_message()
        self.assertEqual(msg["heart"][-3:], [129, 130, 131])
        self.assertEqual(msg["breath"][-3:], [127, 128, 129])

    def test_status_payload_updates_online_and_camera(self):
        state = gateway.SharedState()
        state.update_status(bytes([1, 1, 80, 0, 0, 0, 15]))
        stats = state.build_stats_message()
        self.assertTrue(stats["online"])


if __name__ == "__main__":
    unittest.main()
