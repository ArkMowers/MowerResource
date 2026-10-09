"""Offline resource OTA publishing and release-order regressions."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZIP_DEFLATED, ZipFile

SCRIPT = Path(__file__).resolve().parents[1] / "resource_build.py"
SPEC = importlib.util.spec_from_file_location("resource_build_ota", SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
ROOT = Path(
    os.environ.get("MOWER_OTA_TEST_ROOT", Path(__file__).resolve().parents[3] / "mower")
)
BASE = "v2026.08.23-aaaaaaa"
TARGET = "v2026.08.24-bbbbbbb"


class ResourceOTAPublisherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("mower_dir", "work_dir"):
            mocked = patch.object(builder, name, return_value=self.root)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.full = self.root / "resource.zip"
        with ZipFile(self.full, "w", ZIP_DEFLATED) as archive:
            archive.writestr(
                "arknights_mower/data/version.json", json.dumps({"res_version": TARGET})
            )
            archive.writestr("ui/public/avatar/a.webp", os.urandom(16000))

    def prepare_codec(self):
        source = ROOT / "arknights_mower/utils/resource_ota.py"
        if not source.exists():
            self.skipTest("主仓检出尚未包含资源 OTA 模块")
        target = self.root / "arknights_mower/utils"
        target.mkdir(parents=True)
        shutil.copy2(source, target / source.name)
        shutil.copy2(
            ROOT / "arknights_mower/utils/res_version.py", target / "res_version.py"
        )

    def previous(self, output, tag=BASE, changed=False):
        with (
            ZipFile(self.full) as source,
            ZipFile(output, "w", ZIP_DEFLATED) as archive,
        ):
            for name in source.namelist():
                value = source.read(name)
                if name.endswith("version.json"):
                    value = json.dumps({"res_version": tag}).encode()
                elif changed:
                    value = os.urandom(16000)
                archive.writestr(name, value)

    def run_gh(self, command, **kwargs):
        if "list" in command:
            return Mock(
                stdout=json.dumps(
                    [{"tagName": BASE, "isDraft": False, "isPrerelease": False}]
                )
            )
        self.previous(Path(command[command.index("--dir") + 1]) / "resource.zip")
        return Mock(returncode=0)

    def test_old_generator_publishes_full_hash_index_without_network(self):
        with patch.object(
            builder.subprocess,
            "run",
            side_effect=AssertionError("old generator requires no OTA network"),
        ):
            assets = builder.build_update_assets(TARGET, self.full)
        self.assertEqual([path.name for path in assets], [builder.UPDATE_INDEX_ASSET])
        index = json.loads(assets[-1].read_bytes())
        self.assertEqual(
            index["full"]["sha256"], hashlib.sha256(self.full.read_bytes()).hexdigest()
        )
        self.assertEqual(index["ota"], [])

    def test_small_direct_ota_and_target_descriptor_are_published(self):
        self.prepare_codec()
        with patch.object(builder.subprocess, "run", side_effect=self.run_gh) as gh:
            assets = builder.build_update_assets(TARGET, self.full)
        index = json.loads(assets[-1].read_bytes())
        self.assertEqual(len(index["ota"]), 1)
        self.assertEqual(index["ota"][0]["from"], BASE)
        self.assertLess(index["ota"][0]["size"], index["full"]["size"] * 0.85)
        self.assertEqual(
            index["ota"][0]["sha256"],
            hashlib.sha256(assets[0].read_bytes()).hexdigest(),
        )
        for call in gh.call_args_list:
            self.assertLessEqual(call.kwargs["timeout"], 300)

    def test_large_delta_is_omitted(self):
        self.prepare_codec()

        def gh(command, **kwargs):
            if "list" in command:
                return self.run_gh(command, **kwargs)
            self.previous(
                Path(command[command.index("--dir") + 1]) / "resource.zip", changed=True
            )
            return Mock(returncode=0)

        with patch.object(builder.subprocess, "run", side_effect=gh):
            assets = builder.build_update_assets(TARGET, self.full)
        self.assertEqual(json.loads(assets[-1].read_bytes())["ota"], [])
        self.assertEqual(len(assets), 1)

    def test_missing_previous_asset_keeps_full_publication(self):
        self.prepare_codec()

        def gh(command, **kwargs):
            if "list" in command:
                return self.run_gh(command, **kwargs)
            raise subprocess.CalledProcessError(1, command)

        with patch.object(builder.subprocess, "run", side_effect=gh):
            assets = builder.build_update_assets(TARGET, self.full)
        self.assertEqual(json.loads(assets[-1].read_bytes())["ota"], [])

    def test_unavailable_release_discovery_keeps_full_publication(self):
        self.prepare_codec()
        for result in (
            subprocess.TimeoutExpired("gh release list", 60),
            subprocess.CalledProcessError(1, "gh release list"),
            Mock(stdout="not-json"),
            Mock(stdout='{"tagName": "invalid"}'),
            Mock(stdout='["invalid"]'),
        ):
            with self.subTest(result=result):
                kwargs = (
                    {"side_effect": result}
                    if isinstance(result, Exception)
                    else {"return_value": result}
                )
                with patch.object(builder.subprocess, "run", **kwargs) as gh:
                    assets = builder.build_update_assets(TARGET, self.full)
                self.assertEqual(gh.call_count, 1)
                index = json.loads(assets[-1].read_bytes())
                self.assertEqual(index["ota"], [])
                self.assertEqual(
                    index["full"]["sha256"],
                    hashlib.sha256(self.full.read_bytes()).hexdigest(),
                )
                self.assertEqual(len(assets), 1)

    def test_only_four_nondraft_distinct_bases_are_requested(self):
        self.prepare_codec()
        releases = [
            {"tagName": "invalid"},
            {"tagName": TARGET},
            {"tagName": "v2026.08.30-ccccccc", "isDraft": True},
            *[{"tagName": f"v2026.08.{day:02d}-aaaaaaa"} for day in range(29, 20, -1)],
        ]
        downloaded = []

        def gh(command, **kwargs):
            if "list" in command:
                return Mock(stdout=json.dumps(releases))
            downloaded.append(command[3])
            self.previous(
                Path(command[command.index("--dir") + 1]) / "resource.zip",
                tag=command[3],
            )
            return Mock(returncode=0)

        with patch.object(builder.subprocess, "run", side_effect=gh):
            builder.build_update_assets(TARGET, self.full)
        self.assertEqual(len(downloaded), 4)
        self.assertEqual(downloaded[0], "v2026.08.29-aaaaaaa")

    def test_release_is_draft_until_all_assets_are_uploaded(self):
        ota, index = self.root / "delta.zip", self.root / builder.UPDATE_INDEX_ASSET
        with (
            patch.object(builder, "run") as run,
            patch.object(builder, "build_release_notes", return_value="notes"),
        ):
            builder.ensure_release(TARGET, self.full, {}, [ota, index])
        create = run.call_args_list[1].args[0]
        publish = run.call_args_list[2].args[0]
        self.assertIn("--draft", create)
        self.assertIn(str(ota), create)
        self.assertIn(str(index), create)
        self.assertEqual(
            publish, ["gh", "release", "edit", TARGET, "--draft=false", "--latest"]
        )
