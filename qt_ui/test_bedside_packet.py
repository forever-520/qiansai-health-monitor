import unittest

import bedside_packet


class BedsidePacketTest(unittest.TestCase):
    def test_round_trip_metadata_and_jpeg(self):
        metadata = {
            "radar": {
                "heart": {"rate": 78},
                "breath": {"rate": 18},
                "system": {"frame_count": 42},
            },
            "source": "rk3576_slave",
        }
        jpeg = b"\xff\xd8fake-jpeg\xff\xd9"

        packet = bedside_packet.pack_bedside_frame(metadata, jpeg)
        parsed_metadata, parsed_jpeg = bedside_packet.unpack_bedside_frame(packet)

        self.assertEqual(parsed_metadata, metadata)
        self.assertEqual(parsed_jpeg, jpeg)

    def test_unpack_rejects_bad_magic(self):
        with self.assertRaises(ValueError):
            bedside_packet.unpack_bedside_frame(b"BAD!" + b"\x00" * 8)


if __name__ == "__main__":
    unittest.main()
