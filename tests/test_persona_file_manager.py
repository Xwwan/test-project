import tempfile
import unittest
from pathlib import Path

from src.persona import file_manager


class PersonaFileManagerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "data"
        file_manager.set_data_dir(self.data_dir)

    def tearDown(self):
        file_manager.reset_data_dir()
        self.temp_dir.cleanup()

    def test_read_profiles_creates_missing_files(self):
        self.assertEqual(file_manager.read_model_profile(), "")
        self.assertEqual(file_manager.read_user_profile(), "")
        self.assertTrue((self.data_dir / "Model.md").is_file())
        self.assertTrue((self.data_dir / "User.md").is_file())

    def test_write_and_patch_user_profile(self):
        file_manager.write_user_profile("## Preferences\n- Prefers concise answers")

        updated = file_manager.apply_user_profile_patch(
            {"append": "- Prefers Chinese for this project"}
        )

        self.assertIn("Prefers concise answers", updated)
        self.assertIn("Prefers Chinese", file_manager.read_user_profile())

        replaced = file_manager.apply_user_profile_patch(
            {
                "replace": {
                    "old": "concise answers",
                    "new": "structured answers",
                }
            }
        )
        self.assertIn("structured answers", replaced)

    def test_apply_full_content_patch(self):
        updated = file_manager.apply_user_profile_patch({"content": "stable profile"})
        self.assertEqual(updated, "stable profile")
        self.assertEqual(file_manager.read_user_profile(), "stable profile")


if __name__ == "__main__":
    unittest.main()
