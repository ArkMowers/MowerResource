import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "resource_build.py"
SPEC = importlib.util.spec_from_file_location("resource_build", SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class MasteryBranchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        mocked_root = patch.object(builder, "mower_dir", return_value=self.root)
        mocked_root.start()
        self.addCleanup(mocked_root.stop)
        self.source = {"char_473_mberry": {"subProfessionId": "wandermedic"}}
        self.characters = {
            "char_473_mberry": {
                "name": "桑葚",
                "profession": "MEDIC",
                "subProfessionId": "wandermedic",
            }
        }

    def write_inputs(self, patch_chars=None):
        patch_file = (
            self.root / "ArknightsGameResource/gamedata/excel/char_patch_table.json"
        )
        if patch_chars is None:
            patch_file.unlink(missing_ok=True)
        else:
            patch_file.parent.mkdir(parents=True, exist_ok=True)
            patch_file.write_text(
                json.dumps({"patchChars": patch_chars}, ensure_ascii=False),
                encoding="utf-8",
            )
        for relative, data in (
            ("ArknightsGameResource/gamedata/excel/character_table.json", self.source),
            ("arknights_mower/data/skill_data.json", {"characters": self.characters}),
        ):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def validate(self):
        with contextlib.redirect_stdout(io.StringIO()):
            builder.validate_mastery_branches()

    def test_valid_branches_leave_hashed_resource_unchanged(self):
        self.write_inputs()
        resource = self.root / "arknights_mower/data/skill_data.json"
        before = resource.read_bytes()
        self.validate()
        self.assertEqual(resource.read_bytes(), before)

    def test_missing_wrong_and_empty_branches_are_rejected(self):
        for value in (None, "", "MEDIC", "chainhealer", 1):
            with self.subTest(value=value):
                self.characters["char_473_mberry"]["subProfessionId"] = value
                self.write_inputs()
                with self.assertRaisesRegex(RuntimeError, "char_473_mberry"):
                    self.validate()
        del self.characters["char_473_mberry"]["subProfessionId"]
        self.write_inputs()
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_unknown_operator_or_missing_source_branch_is_rejected(self):
        for source in (
            {},
            {"char_473_mberry": {}},
            {"char_473_mberry": {"subProfessionId": ""}},
        ):
            with self.subTest(source=source):
                self.source = source
                self.write_inputs()
                with self.assertRaises(RuntimeError):
                    self.validate()

    def test_empty_or_malformed_characters_are_rejected(self):
        for characters in ({}, [], None, {"char_473_mberry": None}):
            with self.subTest(characters=characters):
                self.characters = characters
                self.write_inputs()
                with self.assertRaises(RuntimeError):
                    self.validate()

    def test_patch_form_branches_come_from_char_patch_table(self):
        """阿米娅近卫/医疗形态只在 char_patch_table.patchChars 里。

        主仓库 alpha 的生成器用 `干员表 | patchChars` 造 characters，校验必须查同一组
        源表，否则合法形态会被判成「分支缺失」并中止出包。
        """
        self.characters = {
            "char_1001_amiya2": {
                "name": "阿米娅",
                "profession": "WARRIOR",
                "subProfessionId": "artsfghter",
            }
        }
        self.write_inputs(
            patch_chars={"char_1001_amiya2": {"subProfessionId": "artsfghter"}}
        )
        self.validate()

    def test_patch_only_branch_is_rejected_without_patch_table(self):
        self.characters = {
            "char_1001_amiya2": {
                "name": "阿米娅",
                "profession": "WARRIOR",
                "subProfessionId": "artsfghter",
            }
        }
        for patch_chars in (None, {}, []):
            with self.subTest(patch_chars=patch_chars):
                self.write_inputs(patch_chars=patch_chars)
                with self.assertRaisesRegex(RuntimeError, "char_1001_amiya2"):
                    self.validate()

    def test_patch_form_branch_mismatch_is_rejected(self):
        self.characters = {
            "char_1001_amiya2": {
                "name": "阿米娅",
                "profession": "WARRIOR",
                "subProfessionId": "artsfghter",
            }
        }
        for branch in ("incantationmedic", "", None, 1):
            with self.subTest(branch=branch):
                self.write_inputs(
                    patch_chars={"char_1001_amiya2": {"subProfessionId": branch}}
                )
                with self.assertRaisesRegex(RuntimeError, "char_1001_amiya2"):
                    self.validate()

    def test_build_rejects_old_generator_output_before_release(self):
        del self.characters["char_473_mberry"]["subProfessionId"]
        self.write_inputs()
        with (
            patch.object(builder, "repo_head", return_value="snapshot"),
            patch.object(builder, "fetch_sources"),
            patch.object(builder, "run_generation"),
            patch.object(builder, "read_res_version") as read_version,
            patch.object(builder, "ensure_release") as release,
            patch.object(builder, "commit_and_push") as push,
        ):
            with self.assertRaises(RuntimeError):
                builder.cmd_build()
            read_version.assert_not_called()
            release.assert_not_called()
            push.assert_not_called()


class PackageSpecTests(unittest.TestCase):
    """打包文件集必须与生成器算内容哈希用的 package_file_paths 对齐。"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        mocked_root = patch.object(builder, "mower_dir", return_value=self.root)
        mocked_root.start()
        self.addCleanup(mocked_root.stop)

    def write_res_version(self, optional=True):
        target = self.root / "arknights_mower/utils/res_version.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        optional_line = (
            "RES_PACKAGE_OPTIONAL_MODELS = "
            "('arknights_mower/models/mastery_panel.model',)\n"
            if optional
            else ""
        )
        target.write_text(
            "RES_PACKAGE_DIRS = ()\n"
            "RES_PACKAGE_MODELS = ('arknights_mower/models/NORMAL.pkl',)\n"
            f"{optional_line}"
            "RES_PACKAGE_DATA = ()\n",
            encoding="utf-8",
        )

    def write_payload(self):
        for relative in (
            "arknights_mower/models/NORMAL.pkl",
            "arknights_mower/models/mastery_panel.model",
            builder.VERSION_JSON,
        ):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")

    def build(self):
        out_zip = self.root / "resbuild/resource.zip"
        out_zip.parent.mkdir(parents=True, exist_ok=True)
        builder.build_zip(out_zip)
        with zipfile.ZipFile(out_zip) as archive:
            return set(archive.namelist())

    def test_optional_models_are_packaged(self):
        self.write_res_version()
        self.write_payload()
        names = self.build()
        self.assertIn("arknights_mower/models/mastery_panel.model", names)
        self.assertIn("arknights_mower/models/NORMAL.pkl", names)
        self.assertIn(builder.VERSION_JSON, names)

    def test_old_res_version_without_optional_models_still_builds(self):
        self.write_res_version(optional=False)
        self.write_payload()
        names = self.build()
        self.assertNotIn("arknights_mower/models/mastery_panel.model", names)
        self.assertIn("arknights_mower/models/NORMAL.pkl", names)


if __name__ == "__main__":
    unittest.main()
