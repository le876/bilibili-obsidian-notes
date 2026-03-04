import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import bili_subtitles as bs  # noqa: E402


class TestSubtitleFormats(unittest.TestCase):
    def test_vtt_roundtrip_basic(self):
        captions = [
            bs.Caption(start_s=0.0, end_s=1.2, text="hello"),
            bs.Caption(start_s=1.2, end_s=2.0, text="world"),
        ]
        vtt = bs.captions_to_vtt(captions)
        loaded = bs.load_vtt_text(vtt)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].text, "hello")
        self.assertEqual(loaded[1].text, "world")

    def test_srt_basic(self):
        captions = [
            bs.Caption(start_s=61.001, end_s=62.5, text="line1"),
        ]
        srt = bs.captions_to_srt(captions)
        loaded = bs.load_srt_text(srt)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].text, "line1")
        self.assertAlmostEqual(loaded[0].start_s, 61.001, places=2)

    def test_cookie_string_parse(self):
        jar = bs.cookiejar_from_cookie_string("SESSDATA=abc%2Cdef; bili_jct=xyz")
        self.assertIsNotNone(jar)
        # Ensure at least two cookies are present.
        cookies = list(jar) if jar is not None else []
        self.assertGreaterEqual(len(cookies), 2)


if __name__ == "__main__":
    unittest.main()

