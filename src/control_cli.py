from __future__ import annotations

"""Workspace-scoped V2 CLI client.

The CLI intentionally talks to the authenticated V2 HTTP application instead
of importing stage runners, so Chat, buttons and CLI share CommandGateway,
Policy, confirmations, leases and audit state.
"""

import argparse
import getpass
import http.cookiejar
import json
import os
import uuid
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPCookieProcessor, Request, build_opener


class ControlCliError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 1, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


class ControlApiClient:
    def __init__(self, server: str, *, timeout: float = 30.0) -> None:
        self.server = str(server or "").strip().rstrip("/")
        if not self.server.startswith(("http://", "https://")):
            raise ControlCliError("--server 必须是 http:// 或 https:// 地址。")
        self.timeout = max(1.0, float(timeout))
        self.cookies = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.csrf_token = ""

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        method = method.upper()
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if method not in {"GET", "HEAD", "OPTIONS"} and path != "/api/auth/login":
            if not self.csrf_token:
                raise ControlCliError("尚未取得 CSRF 令牌，请先登录。")
            headers["X-CSRF-Token"] = self.csrf_token
        request = Request(f"{self.server}{path}", data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                value = json.loads(raw) if raw else {}
        except HTTPError as exc:
            try:
                value = json.loads(exc.read().decode("utf-8"))
            except Exception:
                value = {}
            message = str(value.get("message") or (value.get("error") or {}).get("message") or exc.reason)
            raise ControlCliError(message, status_code=int(exc.code), payload=value) from exc
        except (URLError, OSError) as exc:
            raise ControlCliError(f"无法连接 V2 服务: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ControlCliError(f"V2 服务返回非 JSON 响应: {exc}") from exc
        if not isinstance(value, dict):
            raise ControlCliError("V2 服务返回格式无效。")
        return value

    def login(self, username: str, password: str) -> dict[str, Any]:
        result = self._request(
            "POST",
            "/api/auth/login",
            {"username": username, "password": password},
        )
        self.csrf_token = str(result.get("csrf_token") or "")
        if not result.get("ok") or not self.csrf_token:
            raise ControlCliError(str(result.get("message") or "登录未返回有效 CSRF 令牌。"), payload=result)
        return result

    def snapshot(self, workspace_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v2/workspaces/{quote(workspace_id, safe='')}/snapshot")

    def migration_dry_run(self, workspace_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v2/workspaces/{quote(workspace_id, safe='')}/migration/dry-run",
        )

    def migration_backups(self, workspace_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v2/workspaces/{quote(workspace_id, safe='')}/migration/backups",
        )

    def migration_report(self, workspace_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v2/workspaces/{quote(workspace_id, safe='')}/migration/report",
        )

    def drill_migration_backup(self, workspace_id: str, path: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v2/workspaces/{quote(workspace_id, safe='')}/migration/backups/drill",
            {"path": path},
        )

    def submit(
        self,
        workspace_id: str,
        *,
        kind: str,
        payload: dict[str, Any],
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v2/workspaces/{quote(workspace_id, safe='')}/commands",
            {
                "kind": kind,
                "payload": payload,
                "expected_revision": expected_revision,
                "idempotency_key": idempotency_key,
            },
        )

    def action(self, workspace_id: str, action_id: str, *, decision: str) -> dict[str, Any]:
        if decision not in {"confirm", "decline"}:
            raise ControlCliError(f"无效 Action 决定: {decision}")
        return self._request(
            "POST",
            f"/api/v2/workspaces/{quote(workspace_id, safe='')}/actions/"
            f"{quote(action_id, safe='')}/{decision}",
            {},
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="标书 Agent V2 控制面 CLI")
    parser.add_argument(
        "--server",
        default=os.environ.get("BID_AGENT_SERVER_URL", "http://127.0.0.1:8000"),
        help="正在运行的 Web 控制面地址",
    )
    parser.add_argument("--username", default=os.environ.get("BID_AGENT_AUTH_USER", "admin"))
    parser.add_argument("--password", default=os.environ.get("BID_AGENT_AUTH_PASSWORD", ""))
    parser.add_argument("--timeout", type=float, default=30.0)
    commands = parser.add_subparsers(dest="control_command", required=True)

    snapshot = commands.add_parser("snapshot", help="读取工作区 V2 Snapshot")
    snapshot.add_argument("--workspace", required=True)

    migration = commands.add_parser("migration-dry-run", help="只读盘点 V1 导入、冲突和 orphan")
    migration.add_argument("--workspace", required=True)

    backups = commands.add_parser("migration-backups", help="读取并校验迁移 SQLite 备份")
    backups.add_argument("--workspace", required=True)

    report = commands.add_parser("migration-report", help="读取工作区迁移审计报告")
    report.add_argument("--workspace", required=True)

    drill = commands.add_parser("migration-backup-drill", help="无破坏性验证迁移备份可恢复")
    drill.add_argument("--workspace", required=True)
    drill.add_argument("--path", required=True)

    scan = commands.add_parser("migration-scan", help="创建管理员旧工作区迁移扫描 Action")
    scan.add_argument("--workspace", required=True)

    cutover = commands.add_parser("migration-cutover", help="创建管理员 V2 控制面切换 Action")
    cutover.add_argument("--workspace", required=True)

    reconcile = commands.add_parser("reconcile", help="创建管理员迁移冲突处理 Action")
    reconcile.add_argument("--workspace", required=True)
    reconcile.add_argument("--conflict-id", required=True)
    reconcile.add_argument(
        "--resolution",
        required=True,
        choices=("bind_legacy", "mark_failed", "keep_orphan"),
    )
    reconcile.add_argument("--reason", required=True)

    submit = commands.add_parser("submit", help="提交 Command；高风险命令返回持久化 Action")
    submit.add_argument("--workspace", required=True)
    submit.add_argument("--kind", required=True)
    submit.add_argument("--payload", default="{}", help="Command payload JSON 对象")
    submit.add_argument("--expected-revision", type=int, default=None)
    submit.add_argument("--idempotency-key", default="")

    for decision in ("confirm", "decline"):
        action = commands.add_parser(decision, help=f"{decision} 持久化 Action")
        action.add_argument("--workspace", required=True)
        action.add_argument("--action-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    password = str(args.password or "")
    if not password:
        password = getpass.getpass("BID Agent password: ")
    client = ControlApiClient(args.server, timeout=args.timeout)
    try:
        client.login(str(args.username), password)
        if args.control_command == "snapshot":
            result = client.snapshot(args.workspace)
        elif args.control_command == "migration-dry-run":
            result = client.migration_dry_run(args.workspace)
        elif args.control_command == "migration-backups":
            result = client.migration_backups(args.workspace)
        elif args.control_command == "migration-report":
            result = client.migration_report(args.workspace)
        elif args.control_command == "migration-backup-drill":
            result = client.drill_migration_backup(args.workspace, args.path)
        elif args.control_command == "migration-scan":
            snapshot = client.snapshot(args.workspace)
            revision = int((snapshot.get("snapshot") or {}).get("revision") or 0)
            result = client.submit(
                args.workspace,
                kind="migration.scan",
                payload={},
                expected_revision=revision,
                idempotency_key=f"cli:migration-scan:{uuid.uuid4()}",
            )
        elif args.control_command == "migration-cutover":
            snapshot = client.snapshot(args.workspace)
            revision = int((snapshot.get("snapshot") or {}).get("revision") or 0)
            result = client.submit(
                args.workspace,
                kind="migration.cutover",
                payload={},
                expected_revision=revision,
                idempotency_key=f"cli:migration-cutover:{uuid.uuid4()}",
            )
        elif args.control_command == "reconcile":
            snapshot = client.snapshot(args.workspace)
            revision = int((snapshot.get("snapshot") or {}).get("revision") or 0)
            result = client.submit(
                args.workspace,
                kind="migration.reconcile",
                payload={
                    "conflict_id": args.conflict_id,
                    "resolution": args.resolution,
                    "reason": args.reason,
                },
                expected_revision=revision,
                idempotency_key=f"cli:migration-reconcile:{uuid.uuid4()}",
            )
        elif args.control_command == "submit":
            try:
                payload = json.loads(args.payload or "{}")
            except json.JSONDecodeError as exc:
                raise ControlCliError(f"--payload 不是合法 JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise ControlCliError("--payload 必须是 JSON 对象。")
            revision = args.expected_revision
            if revision is None:
                snapshot = client.snapshot(args.workspace)
                revision = int((snapshot.get("snapshot") or {}).get("revision") or 0)
            result = client.submit(
                args.workspace,
                kind=args.kind,
                payload=payload,
                expected_revision=revision,
                idempotency_key=args.idempotency_key or f"cli:{uuid.uuid4()}",
            )
        else:
            result = client.action(
                args.workspace,
                args.action_id,
                decision=args.control_command,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    except ControlCliError as exc:
        payload = exc.payload or {"ok": False, "message": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
