"""Optional data is shipped when present and remains compatible with old generators."""

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "resource_build_optional", Path(__file__).resolve().parents[1] / "resource_build.py"
)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class OptionalDataTests(unittest.TestCase):
    def test_real_zip_includes_present_optional_shop_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "arknights_mower/data"
            data.mkdir(parents=True)
            (data / "version.json").write_text('{"res_version":"test"}', "utf-8")
            (data / "event_shop.json").write_text('[{"activity":"活动"}]', "utf-8")
            spec = SimpleNamespace(
                RES_PACKAGE_DIRS=(),
                RES_PACKAGE_MODELS=(),
                RES_PACKAGE_DATA=(),
                RES_PACKAGE_OPTIONAL_DATA=("arknights_mower/data/event_shop.json",),
            )
            with (
                patch.object(builder, "mower_dir", return_value=root),
                patch.object(builder, "load_res_version", return_value=spec),
            ):
                builder.build_zip(root / "resource.zip")
                with zipfile.ZipFile(root / "resource.zip") as archive:
                    self.assertEqual(
                        json.loads(
                            archive.read("arknights_mower/data/event_shop.json")
                        ),
                        [{"activity": "活动"}],
                    )
                (data / "event_shop.json").unlink()
                builder.build_zip(root / "old.zip")
                with zipfile.ZipFile(root / "old.zip") as archive:
                    self.assertNotIn(
                        "arknights_mower/data/event_shop.json", archive.namelist()
                    )

    def test_old_generator_without_optional_data_keeps_original_spec(self):
        spec = SimpleNamespace(
            RES_PACKAGE_DIRS=("images",),
            RES_PACKAGE_MODELS=("model",),
            RES_PACKAGE_DATA=("data",),
        )
        with patch.object(builder, "load_res_version", return_value=spec):
            self.assertEqual(
                builder.load_package_spec(), (("images",), ("model",), ("data",))
            )
