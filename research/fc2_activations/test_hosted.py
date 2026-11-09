"""Receipt/control-path tests only; subprocess.run is always replaced."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import hosted


class HostedControlTests(unittest.TestCase):
    def test_capture_records_one_exact_command_and_both_pipes(self):
        with TemporaryDirectory(prefix="fc2-host-mock-", dir=hosted.HERE.parents[2]) as name:
            output = Path(name)
            def fake(argv, **kwargs):
                kwargs["stdout"].write(b"synthetic-only\n")
                return SimpleNamespace(returncode=0)
            with patch.object(hosted, "OUT", output), patch.object(hosted.subprocess, "run", side_effect=fake) as call:
                stdout, stderr = hosted.capture(["invented-not-an-engine"], output, "synthetic", "quit\n")
            self.assertEqual((stdout, stderr), ("synthetic-only\n", ""))
            self.assertEqual(call.call_args.kwargs["input"], b"quit\n")
            call.assert_called_once()
            record = json.loads((output / "synthetic.record.json").read_bytes())
            self.assertEqual(record["returncode"], 0)
            self.assertFalse(record["timed_out"])
            self.assertEqual(record["logs"]["stdout"]["bytes"], len(b"synthetic-only\n"))

    def test_failure_preserves_receipts_and_never_retries(self):
        with TemporaryDirectory(prefix="fc2-host-mock-", dir=hosted.HERE.parents[2]) as name:
            output = Path(name)
            with patch.object(hosted, "OUT", output), patch.object(hosted.subprocess, "run", return_value=SimpleNamespace(returncode=1)) as call:
                with self.assertRaisesRegex(ValueError, "Hosted command failed"):
                    hosted.capture(["invented-not-a-compiler"], output, "failed")
            record = json.loads((output / "failed.record.json").read_bytes())
            self.assertEqual(record["returncode"], 1)
            self.assertEqual(record["error"]["type"], "ValueError")
            self.assertTrue((output / "failed.stdout.txt").exists())
            self.assertTrue((output / "failed.stderr.txt").exists())
            call.assert_called_once()


if __name__ == "__main__": unittest.main()
