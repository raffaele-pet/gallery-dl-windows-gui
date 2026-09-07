from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import build_gallery_command, looks_like_gallery_input, split_nonempty_lines  # noqa: E402


class HelpersTest(unittest.TestCase):
    def test_split_lines_ignores_blanks_and_comments(self) -> None:
        self.assertEqual(split_nonempty_lines("https://a\n\n # nota\nhttps://b "), ["https://a", "https://b"])

    def test_gallery_inputs(self) -> None:
        self.assertTrue(looks_like_gallery_input("https://example.com/a"))
        self.assertTrue(looks_like_gallery_input("r:https://example.com/list"))
        self.assertTrue(looks_like_gallery_input("pixiv:https://example.com/item"))
        self.assertTrue(looks_like_gallery_input("oauth:reddit"))
        self.assertFalse(looks_like_gallery_input("example.com/no-scheme"))
        self.assertFalse(looks_like_gallery_input(""))


class CommandTest(unittest.TestCase):
    def test_minimal_command(self) -> None:
        values = {"destination": r"C:\Downloads", "action": "Scarica file", "verbosity": "Normale"}
        command = build_gallery_command(["python", "-m", "gallery_dl"], values, ["https://example.com/gallery"])
        self.assertEqual(command[:3], ["python", "-m", "gallery_dl"])
        self.assertIn("--no-colors", command)
        self.assertIn("--destination", command)
        self.assertEqual(command[-1], "https://example.com/gallery")

    def test_structured_options_are_mapped(self) -> None:
        values = {
            "destination": r"C:\Downloads",
            "action": "Simula (nessun download)",
            "verbosity": "Dettagliata",
            "browser_cookies": "firefox:default-release",
            "archive": r"C:\Downloads\archive.sqlite3",
            "range": "1-10",
            "filter": "width >= 1000",
            "container": "ZIP",
            "write_metadata": True,
            "ugoira": "mp4",
            "custom_options": "instagram.videos=true\nbase-directory=\"D:/Media\"",
            "postprocessors": "classify\nmetadata",
            "postprocessor_options": "mode=custom",
            "raw_arguments": '--date-after "2026-01-01"',
        }
        command = build_gallery_command(["gallery-dl"], values, ["https://example.com/x"])
        for flag in (
            "--simulate", "--verbose", "--cookies-from-browser", "--download-archive", "--range",
            "--filter", "--zip", "--write-metadata", "--ugoira", "--option", "--postprocessor",
            "--postprocessor-option", "--date-after",
        ):
            self.assertIn(flag, command)
        self.assertEqual(command.count("--option"), 2)
        self.assertEqual(command.count("--postprocessor"), 2)

    def test_input_file_mode(self) -> None:
        command = build_gallery_command(
            ["gallery-dl"],
            {"destination": ".", "action": "Scarica file", "verbosity": "Normale", "input_file": "urls.txt", "input_mode": "Commenta gli URL completati"},
            [],
        )
        self.assertIn("--input-file-comment", command)
        self.assertEqual(command[-1], "urls.txt")


if __name__ == "__main__":
    unittest.main()
