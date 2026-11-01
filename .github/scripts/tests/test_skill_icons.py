"""Offline coverage of game-table freshness and required skill icon publication."""

import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

SPEC = importlib.util.spec_from_file_location(
    "resource_build_icons", Path(__file__).resolve().parents[1] / "resource_build.py"
)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def png_bytes(fmt="PNG"):
    output = io.BytesIO()
    Image.new("RGBA", (64, 64), "red").save(output, fmt)
    return output.getvalue()


class SkillIconTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.resources = self.root / "ArknightsGameResource"
        excel = self.resources / "gamedata/excel"
        excel.mkdir(parents=True)
        (excel / "character_table.json").write_text(
            json.dumps(
                {
                    "char_new": {"name": "新干员", "itemObtainApproach": "GACHA"},
                    "char_skip": {"name": "不可获取", "itemObtainApproach": None},
                }
            ),
            "utf-8",
        )
        (excel / "building_data.json").write_text(
            json.dumps(
                {
                    "buffs": {
                        "old": {"skillIcon": "bskill_old"},
                        "new": {"skillIcon": "bskill_new&branch"},
                        "skip": {"skillIcon": "bskill_skip"},
                    },
                    "chars": {
                        "char_new": {
                            "buffChar": [
                                {
                                    "buffData": [
                                        {"buffId": "old"},
                                        {"buffId": "new"},
                                        {"buffId": "new"},
                                    ]
                                }
                            ]
                        },
                        "char_skip": {"buffChar": [{"buffData": [{"buffId": "skip"}]}]},
                    },
                }
            ),
            "utf-8",
        )
        (self.resources / "building_skill").mkdir()
        (self.resources / "building_skill/bskill_old.png").write_bytes(b"existing")
        mocked = patch.object(builder, "mower_dir", return_value=self.root)
        mocked.start()
        self.addCleanup(mocked.stop)

    def response(self, data):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = data
        return response

    def test_fallback_uses_pinned_revision_and_only_required_missing_icons(self):
        with patch.object(
            builder.urllib.request, "urlopen", return_value=self.response(png_bytes())
        ) as request:
            builder.fetch_missing_skill_icons(self.resources, "fixed-sha")
        request.assert_called_once_with(
            "https://raw.githubusercontent.com/ArknightsAssets/ArknightsAssets2/fixed-sha/"
            "assets/dyn/arts/building/skills/bskill_new%26branch.png",
            timeout=30,
        )
        self.assertEqual(
            (self.resources / "building_skill/bskill_old.png").read_bytes(), b"existing"
        )
        self.assertFalse((self.resources / "building_skill/bskill_skip.png").exists())

    def test_invalid_or_unavailable_images_stop_generation_without_writes(self):
        for data in (
            b"broken",
            png_bytes("WEBP"),
            png_bytes()[:40],
            b"x" * (builder.MAX_AVATAR_BYTES + 1),
        ):
            with (
                self.subTest(size=len(data)),
                patch.object(
                    builder.urllib.request, "urlopen", return_value=self.response(data)
                ),
            ):
                with self.assertRaises(RuntimeError):
                    builder.fetch_missing_skill_icons(self.resources, "sha")
                self.assertFalse(
                    (self.resources / "building_skill/bskill_new&branch.png").exists()
                )
        with (
            patch.object(builder.urllib.request, "urlopen", side_effect=TimeoutError),
            self.assertRaises(RuntimeError),
        ):
            builder.fetch_missing_skill_icons(self.resources, "sha")

    def test_missing_generated_icon_prevents_packaging_and_publication(self):
        with (
            patch.object(builder, "repo_head", return_value="sha"),
            patch.object(builder, "fetch_sources"),
            patch.object(builder, "run_generation"),
            patch.object(builder, "validate_mastery_branches"),
            patch.object(builder, "validate_avatars"),
            patch.object(builder, "build_zip") as package,
            patch.object(builder, "ensure_release") as release,
        ):
            with self.assertRaisesRegex(RuntimeError, "bskill_new&branch"):
                builder.cmd_build()
            package.assert_not_called()
            release.assert_not_called()

    def test_valid_outputs_are_decoded_without_modification(self):
        output = self.root / "ui/public/building_skill"
        output.mkdir(parents=True)
        for name in ("bskill_old", "bskill_new&branch"):
            (output / (name + ".webp")).write_bytes(png_bytes("WEBP"))
        before = {p.name: p.read_bytes() for p in output.iterdir()}
        builder.validate_skill_icons()
        self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})

    def test_game_table_source_matches_the_checked_country_directory(self):
        with (
            patch.object(builder, "work_dir", return_value=self.root / "work"),
            patch.object(builder, "default_branch", return_value="master"),
            patch.object(builder, "sparse_clone") as clone,
            patch.object(builder, "copy_tree") as copy,
            patch.object(builder, "copy_file") as copy_file,
            patch.object(builder, "fetch_missing_avatars"),
            patch.object(builder, "fetch_missing_skill_icons"),
            patch.object(builder.urllib.request, "urlretrieve"),
            patch.object(builder, "fetch_fonts"),
        ):
            builder.fetch_sources(
                {
                    builder.AVATAR_REPO: "fixed-sha",
                    builder.GAME_DATA_REPO: "game-sha",
                    "yuanyan3060/ArknightsGameResource": "image-sha",
                }
            )
        self.assertEqual(
            clone.call_args_list[0].args[0],
            "https://github.com/Kengxxiao/ArknightsGameData.git",
        )
        self.assertEqual(clone.call_args_list[0].args[3], ["zh_CN/gamedata/excel"])
        self.assertEqual(
            copy.call_args_list[0].args[0],
            self.root / "work/excel/zh_CN/gamedata/excel",
        )
        self.assertIn(builder.GAME_DATA_REPO, builder.VOLATILE_SOURCES)
        self.assertEqual(clone.call_args_list[0].kwargs["revision"], "game-sha")
        self.assertEqual(clone.call_args_list[1].kwargs["revision"], "image-sha")
        self.assertEqual(copy_file.call_args.args[0], self.root / "work/res/version")

    def test_sparse_checkout_uses_the_observed_commit_before_reading_tables(self):
        with patch.object(builder.subprocess, "run") as run:
            builder.sparse_clone(
                "https://example.invalid/game.git",
                "master",
                self.root / "clone",
                ["excel"],
                revision="observed-sha",
            )
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[1][-1], "observed-sha")
        self.assertEqual(commands[2][-3:], ["checkout", "--detach", "observed-sha"])
        self.assertEqual(commands[3][-3:], ["sparse-checkout", "set", "excel"])
