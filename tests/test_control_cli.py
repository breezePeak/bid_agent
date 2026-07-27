from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import control_cli  # noqa: E402


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class ControlCliTests(unittest.TestCase):
    def test_client_uses_csrf_and_workspace_v3_routes(self) -> None:
        client = control_cli.ControlApiClient("http://127.0.0.1:8000")
        client.opener = mock.Mock()
        client.opener.open.side_effect = [
            _Response({"ok": True, "csrf_token": "csrf-1"}),
            _Response({"ok": True, "snapshot": {"workspace_revision": 4}}),
            _Response({"ok": True, "receipt": {"status": "accepted"}}),
        ]

        client.login("admin", "secret")
        client.snapshot("alpha workspace")
        client.submit(
            "alpha workspace",
            kind="document.run_pipeline",
            payload={},
            expected_revision=4,
            idempotency_key="cli-key",
        )

        requests = [call.args[0] for call in client.opener.open.call_args_list]
        self.assertTrue(requests[0].full_url.endswith("/api/auth/login"))
        self.assertIn("/api/v3/workspaces/alpha%20workspace/snapshot", requests[1].full_url)
        self.assertEqual(requests[2].get_header("X-csrf-token"), "csrf-1")
        command = json.loads(requests[2].data.decode("utf-8"))
        self.assertEqual(command["kind"], "document.run_pipeline")
        self.assertNotIn("actor", command)

    def test_submit_fetches_revision_and_prints_receipt(self) -> None:
        client = mock.Mock()
        client.login.return_value = {"ok": True}
        client.snapshot.return_value = {"ok": True, "snapshot": {"workspace_revision": 7}}
        client.submit.return_value = {"ok": True, "receipt": {"status": "accepted"}}
        output = io.StringIO()
        with mock.patch.object(control_cli, "ControlApiClient", return_value=client):
            with mock.patch("sys.stdout", output):
                exit_code = control_cli.main(
                    [
                        "--password",
                        "secret",
                        "submit",
                        "--workspace",
                        "alpha",
                        "--kind",
                        "document.run_pipeline",
                        "--payload",
                        '{}',
                    ]
                )
        self.assertEqual(exit_code, 0)
        client.submit.assert_called_once()
        self.assertEqual(client.submit.call_args.kwargs["expected_revision"], 7)
        self.assertEqual(client.submit.call_args.kwargs["payload"], {})
        self.assertEqual(json.loads(output.getvalue())["receipt"]["status"], "accepted")

    def test_invalid_payload_fails_without_submission(self) -> None:
        client = mock.Mock()
        client.login.return_value = {"ok": True}
        with mock.patch.object(control_cli, "ControlApiClient", return_value=client):
            with mock.patch("sys.stderr", io.StringIO()):
                exit_code = control_cli.main(
                    [
                        "--password",
                        "secret",
                        "submit",
                        "--workspace",
                        "alpha",
                        "--kind",
                        "document.run_pipeline",
                        "--payload",
                        "[]",
                    ]
                )
        self.assertEqual(exit_code, 1)
        client.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
