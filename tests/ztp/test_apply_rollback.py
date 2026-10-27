import sys
import unittest
from pathlib import Path
from unittest.mock import patch, call

ZTP_DIR = Path(__file__).resolve().parents[2] / "automation/ztp"
sys.path.insert(0, str(ZTP_DIR))

import apply_config as app


class TestZTPRollback(unittest.TestCase):

    def setUp(self):
        self.device = "R6"
        self.container = "clab-5840-lab1-R6"
        self.checkpoint = "ztp_pre_R6_abcdef12"
        self.config = "hostname R6\n!\n"
        self.info = {
            "container": self.container,
            "container_id": "abcdef1234567890",
            "sha256": "testhash",
            "ready_for_deployment": True,
        }

        self.patches = [
            patch.object(app, "preflight", return_value=self.info),
            patch.object(app, "wait_until_ready", return_value=True),
            patch.object(app, "is_configured", return_value=False),
            patch.object(app, "stage"),
            patch.object(app, "set_state"),
            patch.object(app, "set_configured"),
            patch.object(app, "verify_device",
                         return_value=self.config),
            patch.object(app, "cli", return_value=self.config),
        ]

        self.mocks = [p.start() for p in self.patches]
        self.addCleanup(self.stop_patches)

        (
            self.preflight, self.ready, self.configured,
            self.stage, self.set_state, self.set_configured,
            self.verify, self.cli
        ) = self.mocks

    def stop_patches(self):
        for p in reversed(self.patches):
            p.stop()

    def test_success(self):
        app.apply(self.device)

        self.set_configured.assert_called_once_with(
            self.device, self.info["container_id"], "testhash"
        )
        self.cli.assert_any_call(
            self.container,
            f"configure checkpoint save {self.checkpoint}",
            timeout=60,
        )
        self.cli.assert_any_call(
            self.container,
            "copy running-config startup-config",
            timeout=60,
        )

    def test_replace_failure_rolls_back(self):
        def fake_cli(container, command, timeout=60):
            if command.startswith("configure replace"):
                raise RuntimeError("Replace failed")
            return self.config

        self.cli.side_effect = fake_cli

        with self.assertRaisesRegex(RuntimeError, "Replace failed"):
            app.apply(self.device)

        self.cli.assert_any_call(
            self.container,
            f"configure checkpoint restore {self.checkpoint}",
            timeout=120,
        )
        self.set_configured.assert_not_called()
        self.assertEqual(
            self.set_state.call_args.args[1], "FAILED"
        )

    def test_verification_failure_rolls_back(self):
        self.verify.side_effect = RuntimeError(
            "Missing Ethernet1 configuration"
        )

        with self.assertRaisesRegex(
            RuntimeError, "Missing Ethernet1"
        ):
            app.apply(self.device)

        self.cli.assert_any_call(
            self.container,
            f"configure checkpoint restore {self.checkpoint}",
            timeout=120,
        )
        self.set_configured.assert_not_called()

    def test_rollback_failure_reported(self):
        def fake_cli(container, command, timeout=60):
            if command.startswith("configure replace"):
                raise RuntimeError("Replace failed")
            if command.startswith("configure checkpoint restore"):
                raise RuntimeError("Restore failed")
            return self.config

        self.cli.side_effect = fake_cli

        with self.assertRaisesRegex(
            RuntimeError, "ROLLBACK FAILED: Restore failed"
        ):
            app.apply(self.device)

        self.set_configured.assert_not_called()
        self.assertEqual(
            self.set_state.call_args.args[1], "FAILED"
        )


if __name__ == "__main__":
    unittest.main()
