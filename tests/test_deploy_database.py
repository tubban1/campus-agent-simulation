import os
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts import deploy_database


class DeployDatabaseTest(unittest.TestCase):
    def test_required_postgres_rejects_sqlite_fallback(self):
        with (
            patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False),
            self.assertRaisesRegex(RuntimeError, "Refusing"),
        ):
            deploy_database.validate_target(require_postgres=True)

    def test_deployment_runs_each_step_and_final_revision_check(self):
        calls = []

        def record(command, check):
            calls.append((command, check))

        with (
            patch.object(deploy_database, "validate_target") as validate,
            patch.object(deploy_database, "deployment_lock"),
            patch.object(deploy_database.subprocess, "run", side_effect=record),
        ):
            deploy_database.run_deployment(require_postgres=True)

        validate.assert_called_once_with(require_postgres=True)
        self.assertTrue(
            all(Path(command[0]).name.startswith("python") for command, _ in calls)
        )
        self.assertEqual(
            [Path(command[1]).name for command, _ in calls[:-1]],
            list(deploy_database.DEPLOYMENT_SCRIPTS),
        )
        self.assertEqual(calls[-1][0][-1], "--check")
        self.assertTrue(all(check for _, check in calls))


if __name__ == "__main__":
    unittest.main()
