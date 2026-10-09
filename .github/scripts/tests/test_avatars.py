"""Offline regressions for avatar source completeness and publication."""

import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

SPEC = importlib.util.spec_from_file_location(
    "resource_build_avatars", Path(__file__).resolve().parents[1] / "resource_build.py"
)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def image_bytes(fmt="PNG", size=(180, 180)):
    stream = io.BytesIO()
    Image.new("RGBA", size, (255, 0, 0, 255)).save(stream, fmt)
    return stream.getvalue()


class AvatarTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        root_mock = patch.object(builder, "mower_dir", return_value=self.root)
        root_mock.start()
        self.addCleanup(root_mock.stop)
        self.resources = self.root / "ArknightsGameResource"
        excel = self.resources / "gamedata/excel"
        excel.mkdir(parents=True)
        (excel / "character_table.json").write_text(
            json.dumps(
                {
                    "char_old": {"name": "已有", "itemObtainApproach": "SHOP"},
                    "char_new": {"name": "旅骨", "itemObtainApproach": "GACHA"},
                    "npc_skip": {"name": "非干员", "itemObtainApproach": None},
                }
            ),
            encoding="utf-8",
        )
        (self.resources / "avatar").mkdir()
        (self.resources / "avatar/char_old.png").write_bytes(b"existing-source")

    def fetch(self, data):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = data
        with patch.object(
            builder.urllib.request, "urlopen", return_value=response
        ) as request:
            builder.fetch_missing_avatars(self.resources, "pinned-sha")
        return request, response

    def test_fallback_preserves_existing_and_fetches_only_obtainable_missing_avatar(
        self,
    ):
        data = image_bytes()
        request, response = self.fetch(data)
        request.assert_called_once_with(
            "https://raw.githubusercontent.com/ArknightsAssets/ArknightsAssets2/pinned-sha/"
            "assets/dyn/arts/charavatars/char_new.png",
            timeout=30,
        )
        response.read.assert_called_once_with(builder.MAX_AVATAR_BYTES + 1)
        self.assertEqual(
            (self.resources / "avatar/char_old.png").read_bytes(), b"existing-source"
        )
        self.assertEqual((self.resources / "avatar/char_new.png").read_bytes(), data)
        self.assertFalse((self.resources / "avatar/npc_skip.png").exists())

    def test_invalid_or_oversized_fallback_does_not_write_avatar(self):
        for data in (
            b"not an image",
            image_bytes("WEBP"),
            image_bytes(size=(513, 10)),
            b"x" * (builder.MAX_AVATAR_BYTES + 1),
            image_bytes()[:40],
        ):
            with self.subTest(size=len(data)):
                with self.assertRaisesRegex(RuntimeError, "旅骨"):
                    self.fetch(data)
                self.assertFalse((self.resources / "avatar/char_new.png").exists())

    def test_unavailable_fallback_stops_build(self):
        with patch.object(builder.urllib.request, "urlopen", side_effect=TimeoutError):
            with self.assertRaisesRegex(RuntimeError, "char_new"):
                builder.fetch_missing_avatars(self.resources, "sha")

    def write_agent(self):
        data = self.root / "arknights_mower/data"
        data.mkdir(parents=True)
        (data / "agent.json").write_text('["旅骨"]', encoding="utf-8")
        target = self.root / "ui/public/avatar/旅骨.webp"
        target.parent.mkdir(parents=True)
        return target

    def test_publication_accepts_complete_decoded_webp_without_modification(self):
        target = self.write_agent()
        data = image_bytes("WEBP", (96, 96))
        target.write_bytes(data)
        builder.validate_avatars()
        self.assertEqual(target.read_bytes(), data)

    def test_missing_or_corrupt_output_blocks_release(self):
        target = self.write_agent()
        for data in (
            None,
            b"broken",
            image_bytes("PNG", (96, 96)),
            image_bytes("WEBP"),
        ):
            with self.subTest(data_type=type(data).__name__):
                if data is not None:
                    target.write_bytes(data)
                with (
                    patch.object(builder, "repo_head", return_value="snapshot"),
                    patch.object(builder, "fetch_sources"),
                    patch.object(builder, "run_generation"),
                    patch.object(builder, "validate_mastery_branches"),
                    patch.object(builder, "build_zip") as package,
                    patch.object(builder, "ensure_release") as release,
                    patch.object(builder, "commit_and_push") as push,
                ):
                    with self.assertRaisesRegex(RuntimeError, "旅骨"):
                        builder.cmd_build()
                    package.assert_not_called()
                    release.assert_not_called()
                    push.assert_not_called()

    def test_cn_asset_change_alone_triggers_scheduled_build(self):
        previous = {repo: "old" for repo in builder.VOLATILE_SOURCES}

        def head(repo, branch):
            if repo == builder.AVATAR_REPO:
                self.assertEqual(branch, "cn")
                return "new"
            return "old"

        with (
            patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule"}),
            patch.object(builder, "repo_head", side_effect=head),
            patch.object(builder, "load_state", return_value={"sources": previous}),
        ):
            self.assertEqual(builder.cmd_check(), 0)
        with (
            patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule"}),
            patch.object(builder, "repo_head", return_value="old"),
            patch.object(builder, "load_state", return_value={"sources": previous}),
        ):
            self.assertEqual(builder.cmd_check(), 1)

    def test_explicit_branch_does_not_use_default_branch(self):
        with (
            patch.object(builder, "default_branch") as default,
            patch.object(builder, "api_json", return_value={"sha": "cn-sha"}) as api,
        ):
            self.assertEqual(builder.repo_head(builder.AVATAR_REPO, "cn"), "cn-sha")
            default.assert_not_called()
            api.assert_called_once_with(
                "https://api.github.com/repos/ArknightsAssets/ArknightsAssets2/commits/cn"
            )
