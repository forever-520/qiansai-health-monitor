#!/usr/bin/env python3
"""Small offline tests for LubanCat IP discovery helpers."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def load_gateway_module():
    care_logic = types.ModuleType("common.care_logic")
    care_logic.evaluate_care = lambda **_: types.SimpleNamespace(to_dict=lambda: {})
    sys.modules.setdefault("common", types.ModuleType("common"))
    sys.modules["common.care_logic"] = care_logic

    rk_web_ui = types.ModuleType("rk_web_ui")
    rk_web_ui.__path__ = [str(ROOT)]
    yolo_detector = types.ModuleType("rk_web_ui.yolo_detector")
    yolo_detector.detect_bed_state = lambda **_: {"ok": False}
    sys.modules["rk_web_ui"] = rk_web_ui
    sys.modules["rk_web_ui.yolo_detector"] = yolo_detector

    spec = importlib.util.spec_from_file_location("gateway_under_test", ROOT / "lubancat_rk_gateway.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LubanCatDiscoveryTests(unittest.TestCase):
    def test_auto_discovery_scans_subnet_and_caches_host(self):
        gateway = load_gateway_module()
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "lubancat_last_ip.txt"

            host = gateway.discover_lubancat_http_host(
                configured_host="auto",
                networks=["192.168.8.10/29"],
                cache_path=cache_path,
                probe=lambda candidate, port, timeout: candidate == "192.168.8.12",
                max_workers=2,
                preferred_hosts="",
            )

            self.assertEqual(host, "192.168.8.12")
            self.assertEqual(cache_path.read_text(encoding="utf-8").strip(), "192.168.8.12")

    def test_auto_discovery_tries_preferred_host_before_cache_and_scan(self):
        gateway = load_gateway_module()
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "lubancat_last_ip.txt"
            cache_path.write_text("192.168.8.13\n", encoding="utf-8")
            tried = []

            host = gateway.discover_lubancat_http_host(
                configured_host="auto",
                networks=["192.168.8.10/29"],
                cache_path=cache_path,
                probe=lambda candidate, port, timeout: tried.append(candidate) or candidate == "192.168.8.12",
                max_workers=2,
                preferred_hosts="192.168.8.12",
            )

            self.assertEqual(host, "192.168.8.12")
            self.assertEqual(tried, ["192.168.8.12"])
            self.assertEqual(cache_path.read_text(encoding="utf-8").strip(), "192.168.8.12")

    def test_auto_discovery_tries_second_preferred_host_before_scan(self):
        gateway = load_gateway_module()
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "lubancat_last_ip.txt"
            tried = []

            host = gateway.discover_lubancat_http_host(
                configured_host="auto",
                networks=["192.168.8.10/29"],
                cache_path=cache_path,
                probe=lambda candidate, port, timeout: tried.append(candidate) or candidate == "192.168.8.13",
                max_workers=2,
                preferred_hosts="192.168.8.12,192.168.8.13",
            )

            self.assertEqual(host, "192.168.8.13")
            self.assertEqual(tried, ["192.168.8.12", "192.168.8.13"])

    def test_remote_urls_for_host(self):
        gateway = load_gateway_module()

        radar_url, camera_url = gateway.remote_urls_for_host("192.168.8.12")

        self.assertEqual(radar_url, "http://192.168.8.12:8000/radar/raw")
        self.assertEqual(camera_url, "http://192.168.8.12:8000/camera/capture")


if __name__ == "__main__":
    unittest.main()
