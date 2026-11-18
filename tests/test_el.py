#!/usr/bin/env python3
"""Tests for the ElevenLabs studio kit. HTTP is mocked; no real API calls."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import el  # noqa: E402

TZ = ZoneInfo("America/Chicago")
NOV = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
DEC = datetime(2026, 12, 1, 9, 0, tzinfo=TZ)
FAKE_AUDIO = b"ID3\x00\x00fake-mp3-bytes"


class StudioKitTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name)
        self.ledger = self.home / "ledger.json"
        os.environ["ELEVENLABS_HOME"] = str(self.home)
        os.environ.pop("ELEVENLABS_LEDGER", None)
        os.environ["ELEVENLABS_API_KEY"] = "test-key-not-real"
        self.addCleanup(self._tmpdir.cleanup)
        self.addCleanup(os.environ.pop, "ELEVENLABS_HOME", None)
        self.addCleanup(os.environ.pop, "ELEVENLABS_API_KEY", None)

    def write_entries(self, entries):
        data = el.empty_ledger()
        data["entries"] = entries
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def load(self):
        return json.loads(self.ledger.read_text(encoding="utf-8"))

    def test_tts_cost_is_character_count(self):
        self.assertEqual(el.estimate_tts("Hello"), 5)
        self.assertEqual(el.estimate_tts("a" * 30), 30)
        with self.assertRaises(el.StudioError):
            el.estimate_tts("   ")

    def test_sfx_cost_is_flat(self):
        self.assertEqual(el.estimate_sfx(), 200)

    def test_music_cost_scales_with_length(self):
        self.assertEqual(el.estimate_music(30), 450)
        self.assertEqual(el.estimate_music(60), 900)
        self.assertEqual(el.estimate_music(3), 45)
        with self.assertRaises(el.StudioError):
            el.estimate_music(2)
        with self.assertRaises(el.StudioError):
            el.estimate_music(601)

    def test_ledger_math_sums_only_this_month(self):
        self.write_entries(
            [
                {"date": "2026-11-01", "kind": "tts", "estimated_credits": 30,
                 "output_file": "a.mp3", "note": "nov a"},
                {"date": "2026-11-18", "kind": "sfx", "estimated_credits": 200,
                 "output_file": "b.mp3", "note": "nov b"},
                {"date": "2026-10-31", "kind": "music", "estimated_credits": 450,
                 "output_file": "c.mp3", "note": "october must not count"},
            ]
        )
        data = el.load_ledger()
        self.assertEqual(el.month_used(data, 2026, 11), 230)
        self.assertEqual(el.month_used(data, 2026, 10), 450)
        self.assertEqual(el.remaining_under_cap(data, now=NOV), el.MONTHLY_CAP - 230)

    def test_monthly_reset_in_december(self):
        self.write_entries(
            [
                {"date": "2026-11-18", "kind": "tts", "estimated_credits": 9400,
                 "output_file": "nov.mp3", "note": "almost at the cap in November"},
            ]
        )
        data = el.load_ledger()
        self.assertEqual(el.month_used(data, 2026, 11), 9400)
        self.assertEqual(el.month_used(data, 2026, 12), 0)
        self.assertEqual(el.remaining_under_cap(data, now=DEC), el.MONTHLY_CAP)
        # A 200-credit sfx is fine in December even though November was tight.
        el.assert_within_cap(200, data, now=DEC)
        with self.assertRaises(el.CapError):
            el.assert_within_cap(200, data, now=NOV)
        # A 501-credit call would still pass the December reset.
        el.assert_within_cap(501, data, now=DEC)

    def test_cap_refuses_before_http(self):
        self.write_entries(
            [
                {"date": "2026-11-18", "kind": "tts", "estimated_credits": 9400,
                 "output_file": "prior.mp3", "note": "near the cap"},
            ]
        )
        http = mock.Mock()
        with mock.patch.object(el, "http_request", http):
            with self.assertRaises(el.CapError):
                el.cmd_sfx("a cat purring", now=NOV)
            with self.assertRaises(el.CapError):
                el.cmd_music("a short lullaby", 30, now=NOV)
            # 100 characters would also overflow 9400 + 100 = 9500 is exact cap?
            # used + estimate > cap refuses; 9400+100=9500 is allowed (not over).
            with self.assertRaises(el.CapError):
                el.cmd_tts("x" * 101, now=NOV)
        http.assert_not_called()
        # Ledger must be unchanged after refusals.
        self.assertEqual(self.load()["entries"][0]["estimated_credits"], 9400)
        self.assertEqual(len(self.load()["entries"]), 1)

    def test_exact_cap_is_allowed_but_one_more_is_not(self):
        self.write_entries(
            [
                {"date": "2026-11-18", "kind": "tts", "estimated_credits": 9400,
                 "output_file": "prior.mp3", "note": "near the cap"},
            ]
        )
        data = el.load_ledger()
        el.assert_within_cap(100, data, now=NOV)  # 9400+100 == 9500
        with self.assertRaises(el.CapError):
            el.assert_within_cap(101, data, now=NOV)

    def test_successful_sfx_appends_ledger_and_writes_file(self):
        dest = self.home / "out" / "cat-purr.mp3"
        with mock.patch.object(el, "http_request", return_value=FAKE_AUDIO) as http:
            result = el.cmd_sfx(
                "A small house cat purring",
                duration=2.0,
                output=dest,
                note="test purr",
                now=NOV,
            )
        http.assert_called_once()
        args, kwargs = http.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "/v1/sound-generation")
        self.assertEqual(kwargs["body"]["text"], "A small house cat purring")
        self.assertEqual(kwargs["body"]["duration_seconds"], 2.0)
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.read_bytes(), FAKE_AUDIO)
        self.assertEqual(result["estimated_credits"], 200)
        data = self.load()
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(data["entries"][0]["kind"], "sfx")
        self.assertEqual(data["entries"][0]["estimated_credits"], 200)
        self.assertEqual(data["entries"][0]["date"], "2026-11-18")
        self.assertEqual(data["entries"][0]["output_file"], "out/cat-purr.mp3")

    def test_successful_tts_and_music_ledger_math(self):
        with mock.patch.object(el, "http_request", return_value=FAKE_AUDIO):
            el.cmd_tts("Hello there", output=self.home / "out" / "tts.mp3", now=NOV)
            el.cmd_music(
                "gentle piano",
                30,
                output=self.home / "out" / "music.mp3",
                now=NOV,
            )
        data = self.load()
        # 11 chars + 450 music
        self.assertEqual(el.month_used(data, 2026, 11), 11 + 450)
        self.assertEqual(el.remaining_under_cap(data, now=NOV), el.MONTHLY_CAP - 461)

    def test_test_home_is_not_auto_seeded(self):
        data = el.load_ledger()
        self.assertEqual(data["entries"], [])
        self.assertFalse(self.ledger.is_file())

    def test_refuses_hanging_endpoints(self):
        with self.assertRaises(el.StudioError) as ctx:
            el.http_request("GET", "/v1/user")
        self.assertIn("hang", str(ctx.exception).lower())
        with self.assertRaises(el.StudioError):
            el.http_request("GET", "/v1/user/subscription")
        with self.assertRaises(el.StudioError):
            el.http_request("GET", "/v1/voices")

    def test_ledger_and_errors_never_contain_the_key(self):
        with mock.patch.object(el, "http_request", return_value=FAKE_AUDIO):
            el.cmd_tts("secret-safe", output=self.home / "out" / "t.mp3", now=NOV)
        blob = self.ledger.read_text(encoding="utf-8")
        self.assertNotIn("test-key-not-real", blob)
        self.assertNotIn("ELEVENLABS_API_KEY", blob)
        report = el.cmd_ledger(now=NOV)
        self.assertNotIn("test-key-not-real", report)
        self.assertIn("30 credits" if False else "Used this month (estimated): 11", report)

    def test_music_request_shape(self):
        dest = self.home / "out" / "clip.mp3"
        with mock.patch.object(el, "http_request", return_value=FAKE_AUDIO) as http:
            el.cmd_music("sunbeam lullaby", 30, output=dest, now=NOV)
        body = http.call_args.kwargs["body"]
        self.assertEqual(http.call_args.args[1], "/v1/music")
        self.assertEqual(body["prompt"], "sunbeam lullaby")
        self.assertEqual(body["music_length_ms"], 30_000)
        self.assertEqual(body["model_id"], "music_v1")
        self.assertTrue(body["force_instrumental"])


if __name__ == "__main__":
    unittest.main()
