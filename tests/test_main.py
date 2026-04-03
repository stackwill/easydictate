import subprocess
import unittest
from unittest import mock


class MainModuleTests(unittest.TestCase):
    def test_module_execution_delegates_to_cli_main(self) -> None:
        with mock.patch("easydictate.cli.main") as cli_main:
            import runpy

            runpy.run_module("easydictate", run_name="__main__")

        cli_main.assert_called_once_with()

    def test_package_module_help_executes(self) -> None:
        result = subprocess.run(
            ["python", "-m", "easydictate", "--help"],
            capture_output=True,
            text=True,
            check=False,
            env={"PYTHONPATH": "src"},
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("usage: easydictate", result.stdout)


if __name__ == "__main__":
    unittest.main()
