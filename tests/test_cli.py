import os
import unittest
from pathlib import Path
from unittest import mock

from easydictate import cli


class ResolveGuiPythonTests(unittest.TestCase):
    def test_prefers_current_python_when_it_can_import_gi(self) -> None:
        with mock.patch("easydictate.cli.python_can_import", side_effect=lambda exe, mod: exe == "/venv/python"):
            self.assertEqual(cli.resolve_gui_python("/venv/python"), "/venv/python")

    def test_falls_back_to_system_python(self) -> None:
        def can_import(exe: str, mod: str) -> bool:
            return exe == "python3"

        with mock.patch("easydictate.cli.python_can_import", side_effect=can_import):
            self.assertEqual(cli.resolve_gui_python("/venv/python"), "python3")

    def test_returns_none_when_no_python_has_gi(self) -> None:
        with mock.patch("easydictate.cli.python_can_import", return_value=False):
            self.assertIsNone(cli.resolve_gui_python("/venv/python"))


class BuildGuiEnvTests(unittest.TestCase):
    def test_prepends_src_to_pythonpath(self) -> None:
        with mock.patch("easydictate.cli.resolve_gui_python_paths", return_value=["/project/site-packages"]):
            env = cli.build_gui_env({"PYTHONPATH": "/tmp/existing"}, Path("/project/src"))
        self.assertEqual(
            env["PYTHONPATH"],
            "/project/src" + os.pathsep + "/project/site-packages" + os.pathsep + "/tmp/existing",
        )

    def test_sets_pythonpath_when_missing(self) -> None:
        with mock.patch("easydictate.cli.resolve_gui_python_paths", return_value=["/project/site-packages"]):
            env = cli.build_gui_env({}, Path("/project/src"))
        self.assertEqual(env["PYTHONPATH"], "/project/src" + os.pathsep + "/project/site-packages")

    def test_omits_duplicate_python_paths(self) -> None:
        with mock.patch(
            "easydictate.cli.resolve_gui_python_paths",
            return_value=["/project/src", "/project/site-packages", "/project/site-packages"],
        ):
            env = cli.build_gui_env({"PYTHONPATH": "/project/site-packages"}, Path("/project/src"))
        self.assertEqual(env["PYTHONPATH"], "/project/src" + os.pathsep + "/project/site-packages")


if __name__ == "__main__":
    unittest.main()
