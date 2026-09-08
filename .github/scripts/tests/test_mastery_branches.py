import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
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
                "name": "桑葚", "profession": "MEDIC", "subProfessionId": "wandermedic"
            }
        }

    def write_inputs(self):
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
        for source in ({}, {"char_473_mberry": {}}, {"char_473_mberry": {"subProfessionId": ""}}):
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

    def test_build_rejects_old_generator_output_before_release(self):
        del self.characters["char_473_mberry"]["subProfessionId"]
        self.write_inputs()
        with (
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


if __name__ == "__main__":
    unittest.main()
