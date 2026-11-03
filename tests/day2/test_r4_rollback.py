import sys
import unittest
from pathlib import Path
from unittest.mock import patch

DAY2 = Path(__file__).resolve().parents[2] / "automation/day2"
sys.path.insert(0, str(DAY2))

import configure_r4 as r4


class TestR4Rollback(unittest.TestCase):

    def setUp(self):
        self.before = {
            "interface": "original interface",
            "network_instance": "original network instance",
            "ospf": "original ospf",
        }

        patches = {
            "inspect": patch.object(
                r4, "inspect", side_effect=[False, True]
            ),
            "snapshot": patch.object(
                r4, "snapshot", return_value=self.before
            ),
            "sr_session": patch.object(r4, "sr_session"),
            "sr_read": patch.object(r4, "sr_read"),
            "check_neighbors": patch.object(r4, "check_neighbors"),
            "verify_rollback": patch.object(r4, "verify_rollback"),
        }

        self.mocks = {}
        for name, p in patches.items():
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)

    def test_success_accepts(self):
        r4.apply()

        self.mocks["check_neighbors"].assert_called_once()
        self.mocks["sr_read"].assert_called_once_with(
            "commit confirmed accept"
        )
        self.mocks["verify_rollback"].assert_not_called()

        commands = self.mocks["sr_session"].call_args.args[0]
        self.assertIn("commit validate", commands)
        self.assertIn("commit confirmed timeout 300", commands)

    def test_commit_failure_attempts_reject(self):
        self.mocks["sr_session"].side_effect = RuntimeError(
            "Commit failed"
        )

        with self.assertRaisesRegex(RuntimeError, "Commit failed"):
            r4.apply()

        self.mocks["sr_read"].assert_called_once_with(
            "commit confirmed reject"
        )
        self.mocks["verify_rollback"].assert_called_once_with(
            self.before
        )

    def test_neighbor_failure_rolls_back(self):
        self.mocks["check_neighbors"].side_effect = RuntimeError(
            "OSPF adjacency verification failed"
        )

        with self.assertRaisesRegex(
            RuntimeError, "OSPF adjacency verification failed"
        ):
            r4.apply()

        self.mocks["sr_read"].assert_called_once_with(
            "commit confirmed reject"
        )
        self.mocks["verify_rollback"].assert_called_once_with(
            self.before
        )

    def test_reject_failure_is_reported(self):
        self.mocks["check_neighbors"].side_effect = RuntimeError(
            "OSPF adjacency verification failed"
        )
        self.mocks["sr_read"].side_effect = RuntimeError(
            "Reject failed"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "ROLLBACK NOT VERIFIED: Reject failed"
        ):
            r4.apply()

        self.mocks["verify_rollback"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
