import sys
import unittest
from pathlib import Path
from unittest.mock import patch


DAY2 = (
    Path(__file__).resolve().parents[2]
    / "automation"
    / "day2"
)

sys.path.insert(
    0,
    str(DAY2),
)


import configure_r4 as r4


class TestR4Rollback(
    unittest.TestCase
):

    def setUp(self):

        self.before = {
            "/interface ethernet-1/4":
                "original interface",

            "/network-instance default":
                "original network instance",

            "/routing-policy":
                "original routing policy",
        }

        self.verify_config_patch = (
            patch.object(
                r4,
                "verify_config",
                side_effect=[
                    RuntimeError(
                        "configuration missing"
                    ),
                    None,
                ],
            )
        )

        self.snapshot_patch = (
            patch.object(
                r4,
                "snapshot",
                return_value=self.before,
            )
        )

        self.batch_patch = (
            patch.object(
                r4,
                "batch",
                return_value="OK",
            )
        )

        self.cli_patch = (
            patch.object(
                r4,
                "cli",
                return_value="OK",
            )
        )

        self.check_neighbors_patch = (
            patch.object(
                r4,
                "check_neighbors",
                return_value=None,
            )
        )

        self.verify_rollback_patch = (
            patch.object(
                r4,
                "verify_rollback",
                return_value=None,
            )
        )

        self.verify_config = (
            self.verify_config_patch.start()
        )

        self.snapshot = (
            self.snapshot_patch.start()
        )

        self.batch = (
            self.batch_patch.start()
        )

        self.cli = (
            self.cli_patch.start()
        )

        self.check_neighbors = (
            self.check_neighbors_patch.start()
        )

        self.verify_rollback = (
            self.verify_rollback_patch.start()
        )

        self.addCleanup(
            self.verify_config_patch.stop
        )

        self.addCleanup(
            self.snapshot_patch.stop
        )

        self.addCleanup(
            self.batch_patch.stop
        )

        self.addCleanup(
            self.cli_patch.stop
        )

        self.addCleanup(
            self.check_neighbors_patch.stop
        )

        self.addCleanup(
            self.verify_rollback_patch.stop
        )


    def test_success_accepts(
        self,
    ):

        r4.apply()

        self.snapshot.assert_called_once()

        self.batch.assert_called_once()

        commands = (
            self.batch
            .call_args
            .args[0]
        )

        self.assertIn(
            "commit validate",
            commands,
        )

        self.assertIn(
            "diff",
            commands,
        )

        self.assertIn(
            (
                "commit confirmed "
                f"timeout {r4.CONFIRM_TIMEOUT}"
            ),
            commands,
        )

        self.check_neighbors \
            .assert_called_once_with(
                timeout=120
            )

        self.cli \
            .assert_called_once_with(
                "commit confirmed accept"
            )

        self.verify_rollback \
            .assert_not_called()


    def test_commit_failure_attempts_reject(
        self,
    ):

        self.batch.side_effect = (
            RuntimeError(
                "Commit failed"
            )
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Commit failed",
        ):

            r4.apply()

        self.cli \
            .assert_called_once_with(
                "commit confirmed reject"
            )

        self.verify_rollback \
            .assert_called_once_with(
                self.before
            )


    def test_neighbor_failure_rolls_back(
        self,
    ):

        self.check_neighbors.side_effect = (
            RuntimeError(
                "OSPF adjacency verification failed"
            )
        )

        with self.assertRaisesRegex(
            RuntimeError,
            (
                "OSPF adjacency "
                "verification failed"
            ),
        ):

            r4.apply()

        self.cli \
            .assert_called_once_with(
                "commit confirmed reject"
            )

        self.verify_rollback \
            .assert_called_once_with(
                self.before
            )


    def test_reject_failure_is_reported(
        self,
    ):

        self.check_neighbors.side_effect = (
            RuntimeError(
                "OSPF adjacency verification failed"
            )
        )

        self.cli.side_effect = (
            RuntimeError(
                "Reject failed"
            )
        )

        with self.assertRaisesRegex(
            RuntimeError,
            (
                "ROLLBACK NOT VERIFIED: "
                "Reject failed"
            ),
        ):

            r4.apply()

        self.verify_rollback \
            .assert_not_called()


    def test_already_configured_skips_changes(
        self,
    ):

        self.verify_config.side_effect = None

        r4.apply()

        self.snapshot \
            .assert_not_called()

        self.batch \
            .assert_not_called()

        self.cli \
            .assert_not_called()

        self.verify_rollback \
            .assert_not_called()

        self.check_neighbors \
            .assert_called_once_with(
                timeout=20
            )


if __name__ == "__main__":
    unittest.main()