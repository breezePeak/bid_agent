"""Standalone V3 HTTP application.  It deliberately imports no legacy web modules."""

from __future__ import annotations

import asyncio
import hmac
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from control_plane import CommandEnvelope, CommandGateway, ControlPlaneError, ControlStore, WorkspaceContext
from document_pipeline.canonicalization import canonical_hash, chapter_context_hash
from document_pipeline.contracts import InputRole
from document_pipeline.execution_controller import V3ExecutionController
from document_pipeline.document_preview import DocumentPreviewService
from document_pipeline.input_manifest import InputManifestService, V3_ROOT
from document_pipeline.legacy_bid_source import LegacyBidSourceService
from document_pipeline.renderers.render_verifier import RENDER_OUTPUT_PATH, RENDER_QUALITY_PATH
from document_pipeline.source_normalizer import NORMALIZABLE_EXTENSIONS, SourceNormalizer
from document_pipeline.workspace_snapshot import V3WorkspaceSnapshotBuilder
from utils import read_json

from .settings_service import SettingsService

ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "runs"
VUE_DIST_DIR = ROOT / "frontend" / "dist"
_SESSIONS: dict[str, dict[str, Any]] = {}
_COOKIE, _CSRF = "bid_agent_session", "bid_agent_csrf"
_AUTH_SESSION_SECONDS = 12 * 60 * 60
_SESSION_STORE_VERSION = 1
_SESSION_LOCK = threading.Lock()
SETTINGS = SettingsService(ROOT)


def _remove_tree_with_retries(root: Path, *, attempts: int = 8) -> OSError | None:
    """Return the final filesystem error, if Windows still holds a handle."""
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            shutil.rmtree(root)
            return None
        except FileNotFoundError:
            return None
        except OSError as exc:
            last_error = exc
            time.sleep(0.15 * (attempt + 1))
    return last_error


def _purge_tombstone_in_background(root: Path) -> None:
    """Finish a deletion after transient SQLite/antivirus file handles close."""
    for _ in range(60):
        if not root.exists():
            return
        if _remove_tree_with_retries(root, attempts=4) is None:
            return
        time.sleep(1)


def _schedule_tombstone_purge(root: Path) -> None:
    threading.Thread(
        target=_purge_tombstone_in_background,
        args=(root,),
        daemon=True,
        name=f"workspace-purge-{root.name[-12:]}",
    ).start()


def _cleanup_pending_workspace_deletions() -> None:
    """Resume interrupted deletions left by a previous server process."""
    if not RUNS_DIR.is_dir():
        return
    for root in RUNS_DIR.glob(".deleting-*"):
        if not root.is_dir():
            continue
        if _remove_tree_with_retries(root, attempts=4) is not None:
            _schedule_tombstone_purge(root)


def _reconcile_interrupted_workspaces() -> list[dict[str, Any]]:
    """Close durable operations whose worker lease died before this boot."""
    recovered: list[dict[str, Any]] = []
    if not RUNS_DIR.is_dir():
        return recovered
    for workspace_root in RUNS_DIR.iterdir():
        if not workspace_root.is_dir() or workspace_root.name.startswith("."):
            continue
        if not (workspace_root / "workspace" / "control.db").is_file():
            continue
        try:
            context = WorkspaceContext.resolve(RUNS_DIR, workspace_root.name)
            store = ControlStore(context)
            for item in store.reconcile_expired_operations():
                recovered.append({"workspace_id": workspace_root.name, **item})
            from document_pipeline.chapter_batch import ChapterBatchService

            for job_id in ChapterBatchService.recover(context):
                recovered.append(
                    {
                        "workspace_id": workspace_root.name,
                        "job_id": job_id,
                        "kind": "chapter_batch_resumed",
                    }
                )
        except Exception:
            # A damaged or concurrently migrated workspace must not prevent the
            # HTTP service from starting; its own health diagnostics can expose
            # the issue and the next boot will retry reconciliation.
            continue
    return recovered


@asynccontextmanager
async def _runtime_settings_lifespan(_app: FastAPI):
    """Apply persisted settings only while this application is running."""
    previous = SETTINGS.capture_runtime_environment("BID_AGENT_CONFIG_ROOT")
    os.environ["BID_AGENT_CONFIG_ROOT"] = str(ROOT)
    SETTINGS.apply_runtime_settings()
    _cleanup_pending_workspace_deletions()
    _reconcile_interrupted_workspaces()
    try:
        yield
    finally:
        SETTINGS.restore_runtime_environment(previous)


app = FastAPI(
    title="标书 Agent V3",
    docs_url=None,
    redoc_url=None,
    lifespan=_runtime_settings_lifespan,
)

if (VUE_DIST_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(VUE_DIST_DIR / "assets")), name="assets")


def _error(exc: ControlPlaneError) -> JSONResponse:
    return JSONResponse({"ok": False, "error": exc.as_dict(), "message": exc.message}, status_code=exc.status_code)


def _context(workspace_id: str) -> WorkspaceContext:
    return WorkspaceContext.resolve(RUNS_DIR, workspace_id)


def _principal(request: Request) -> dict[str, Any]:
    return dict(getattr(request.state, "principal", {}) or {})


def _acl(context: WorkspaceContext, principal: dict[str, Any], *, write: bool) -> None:
    store, user_id = ControlStore(context), str(principal.get("id") or "")
    if not store.workspace_acl() and principal.get("role") == "admin":
        store.grant_workspace_access(user_id, role="owner")
    store.require_workspace_access(user_id, write=write)


def _session_store_path() -> Path:
    return RUNS_DIR / ".auth" / "sessions.json"


def _session_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _read_session_store() -> tuple[dict[str, dict[str, Any]], bool]:
    path = _session_store_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return {}, False
    if (
        not isinstance(payload, dict)
        or payload.get("version") != _SESSION_STORE_VERSION
        or not isinstance(payload.get("sessions"), dict)
    ):
        return {}, False
    sessions: dict[str, dict[str, Any]] = {}
    dirty = False
    now = time.time()
    for key, raw in payload["sessions"].items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[0-9a-f]{64}", key)
            or not isinstance(raw, dict)
            or not isinstance(raw.get("principal"), dict)
            or not isinstance(raw.get("csrf"), str)
        ):
            dirty = True
            continue
        try:
            expires = float(raw.get("expires") or 0)
        except (TypeError, ValueError):
            dirty = True
            continue
        principal = raw["principal"]
        if (
            expires <= now
            or not raw["csrf"]
            or not isinstance(principal.get("id"), str)
            or not principal["id"]
        ):
            dirty = True
            continue
        sessions[key] = {
            "principal": dict(principal),
            "csrf": raw["csrf"],
            "expires": expires,
        }
    return sessions, dirty


def _write_session_store(sessions: dict[str, dict[str, Any]]) -> None:
    path = _session_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(
                {"version": _SESSION_STORE_VERSION, "sessions": sessions},
                stream,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_sessions() -> dict[str, dict[str, Any]]:
    sessions, dirty = _read_session_store()
    if dirty:
        _write_session_store(sessions)
    _SESSIONS.clear()
    _SESSIONS.update(sessions)
    return sessions


def _session_record(token: str) -> dict[str, Any] | None:
    value = str(token or "").strip()
    if not value:
        return None
    key = _session_key(value)
    with _SESSION_LOCK:
        session = _SESSIONS.get(key)
        if not session:
            session = _load_sessions().get(key)
            if not session:
                return None
        if float(session.get("expires") or 0) <= time.time():
            sessions = _load_sessions()
            sessions.pop(key, None)
            _SESSIONS.pop(key, None)
            _write_session_store(sessions)
            return None
        return dict(session)


def _issue_session(username: str) -> JSONResponse:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    principal = {"type": "user", "id": username[:128], "role": "admin"}
    with _SESSION_LOCK:
        sessions = _load_sessions()
        sessions[_session_key(token)] = {
            "principal": principal,
            "csrf": csrf,
            "expires": time.time() + _AUTH_SESSION_SECONDS,
        }
        _SESSIONS.clear()
        _SESSIONS.update(sessions)
        _write_session_store(sessions)
    response = JSONResponse(
        {"ok": True, "principal": principal, "csrf_token": csrf}
    )
    secure = SETTINGS.auth_secure_cookie()
    response.set_cookie(
        _COOKIE,
        token,
        max_age=_AUTH_SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        secure=secure,
        path="/",
    )
    response.set_cookie(
        _CSRF,
        csrf,
        max_age=_AUTH_SESSION_SECONDS,
        httponly=False,
        samesite="strict",
        secure=secure,
        path="/",
    )
    return response


@app.middleware("http")
async def auth(request: Request, call_next):
    if request.url.path == "/api/auth/login":
        return await call_next(request)
    # The Vue shell and its static assets must stay reachable before login so
    # the client-side router can render /login.  Authentication remains
    # fail-closed for every API route.
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    session = _session_record(request.cookies.get(_COOKIE, ""))
    if not session:
        return JSONResponse({"ok": False, "message": "请先登录。"}, status_code=401)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        expected = str(session.get("csrf") or "")
        header_token = str(request.headers.get("x-csrf-token") or "")
        cookie_token = str(request.cookies.get(_CSRF, "") or "")
        if not (
            expected
            and hmac.compare_digest(header_token, expected)
            and hmac.compare_digest(cookie_token, expected)
        ):
            return JSONResponse({"ok": False, "message": "请求缺少有效的 CSRF 令牌。"}, status_code=403)
    request.state.principal = session["principal"]
    workspace = request.url.path.removeprefix("/api/v3/workspaces/").split("/", 1)[0]
    if request.url.path.startswith("/api/v3/workspaces/") and workspace:
        try:
            _acl(_context(workspace), session["principal"], write=request.method not in {"GET", "HEAD", "OPTIONS"})
        except ControlPlaneError as exc:
            return _error(exc)
    return await call_next(request)


@app.post("/api/auth/login")
async def login(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    user, password = SETTINGS.auth_credentials()
    if not password:
        return JSONResponse(
            {"ok": False, "message": "服务端尚未配置 BID_AGENT_AUTH_PASSWORD。"},
            status_code=503,
        )
    supplied_user = str(body.get("username") or "")
    supplied_password = str(body.get("password") or "")
    if not (
        hmac.compare_digest(supplied_user, user)
        and hmac.compare_digest(supplied_password, password)
    ):
        return JSONResponse({"ok": False, "message": "用户名或密码错误。"}, status_code=401)
    return _issue_session(user)


@app.post("/api/auth/logout")
def logout(request: Request) -> JSONResponse:
    token = str(request.cookies.get(_COOKIE, "") or "").strip()
    with _SESSION_LOCK:
        sessions = _load_sessions()
        if token:
            sessions.pop(_session_key(token), None)
        _SESSIONS.clear()
        _SESSIONS.update(sessions)
        _write_session_store(sessions)
    response = JSONResponse({"ok": True})
    response.delete_cookie(_COOKIE, path="/")
    response.delete_cookie(_CSRF, path="/")
    return response


@app.get("/api/auth/me")
def me(request: Request) -> JSONResponse: return JSONResponse({"ok": True, "principal": _principal(request)})


@app.get("/api/llm-settings")
def get_llm_settings() -> JSONResponse:
    store = SETTINGS.read_models_store()
    return JSONResponse(
        SETTINGS.public_result(
            {
                "ok": True,
                "models": store.get("models", []),
                "active_id": store.get("active_id", ""),
                "config_revision": SETTINGS.config_revision(),
            }
        )
    )


@app.post("/api/llm-settings")
async def set_llm_settings(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"ok": False, "message": "请求体必须是 JSON。"},
            status_code=400,
        )
    if not isinstance(body, dict) or not isinstance(body.get("model"), dict):
        return JSONResponse(
            {"ok": False, "message": "缺少 model 字段。"},
            status_code=400,
        )
    try:
        result = SETTINGS.save_model(
            body["model"],
            set_active=bool(body.get("set_active")),
        )
    except LookupError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return JSONResponse(SETTINGS.public_result({"ok": True, **result}))


@app.post("/api/llm-settings/activate")
async def activate_llm_model(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    model_id = str(body.get("id") or "") if isinstance(body, dict) else ""
    try:
        result = SETTINGS.activate_model(model_id)
    except LookupError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=404)
    return JSONResponse(SETTINGS.public_result({"ok": True, **result}))


@app.post("/api/llm-settings/delete")
async def delete_llm_model(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    model_id = str(body.get("id") or "") if isinstance(body, dict) else ""
    try:
        result = SETTINGS.delete_model(model_id)
    except LookupError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=404)
    return JSONResponse(SETTINGS.public_result({"ok": True, **result}))


@app.post("/api/llm-settings/test")
async def test_llm_settings(request: Request) -> JSONResponse:
    """Probe must never surface as a bare 500 to the settings UI.

    Connectivity failures (bad key, Cloudflare 1010, timeout) are returned as
    HTTP 200 with ``ok: false`` and a human-readable message.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    raw_model = body.get("model") if isinstance(body.get("model"), dict) else None
    try:
        model = SETTINGS.resolve_probe_model(
            raw_model,
            use_active=bool(body.get("use_active")),
        )
    except Exception as exc:
        return JSONResponse(
            {
                "ok": False,
                "message": f"无法解析模型配置: {exc}",
            },
            status_code=400,
        )
    if not model:
        return JSONResponse(
            {"ok": False, "message": "没有可测试的模型，请先填写配置。"},
            status_code=400,
        )
    try:
        result = await run_in_threadpool(SETTINGS.probe_model, model)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse(
            {
                "ok": False,
                "message": f"测试过程异常: {type(exc).__name__}: {exc}",
                "model": str(model.get("model") or ""),
                "provider": str(model.get("provider") or ""),
                "base_url": str(model.get("base_url") or ""),
            },
            status_code=200,
        )
    if not isinstance(result, dict):
        return JSONResponse(
            {"ok": False, "message": "测试返回格式异常。"},
            status_code=200,
        )
    # Always 200 for structured probe outcomes so axios surfaces data.message.
    return JSONResponse(result, status_code=200)


@app.get("/api/flow-settings")
def get_flow_settings() -> JSONResponse:
    return JSONResponse({"ok": True, "settings": SETTINGS.flow_settings()})


@app.post("/api/flow-settings")
async def set_flow_settings(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    settings = body.get("settings") if isinstance(body, dict) else None
    if not isinstance(settings, dict):
        return JSONResponse(
            {"ok": False, "message": "缺少 settings 对象。"},
            status_code=400,
        )
    try:
        updated = SETTINGS.write_flow_settings(settings)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return JSONResponse(
        {"ok": True, "settings": updated, "applied_live": True}
    )


def _workspace_id(name: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", name).strip("._-")[:48]
    if not safe: raise ControlPlaneError("WORKSPACE_NAME_REQUIRED", "请先设置工作空间名称。", status_code=400)
    base = f"{safe}_{time.strftime('%Y%m%d_%H%M%S')}"; candidate = base; suffix = 1
    while (RUNS_DIR / candidate).exists(): suffix += 1; candidate = f"{base}_{suffix}"
    return candidate


def _validate_upload_type(role: InputRole, filename: str) -> None:
    suffix = Path(filename).suffix.lower()
    if role is InputRole.TEMPLATE:
        if suffix != ".docx":
            raise ControlPlaneError(
                "UPLOAD_TYPE_UNSUPPORTED",
                "Word 模板只支持 .docx 文件。",
                status_code=400,
            )
        return
    if suffix not in NORMALIZABLE_EXTENSIONS:
        supported = "、".join(sorted(NORMALIZABLE_EXTENSIONS))
        raise ControlPlaneError(
            "UPLOAD_TYPE_UNSUPPORTED",
            f"该输入无法进入 V3 解析链；支持的格式为 {supported}。",
            status_code=400,
        )


def _delete_workspace_root(workspace_id: str) -> None:
    """Delete only a real V3 workspace contained by ``RUNS_DIR``."""
    context = _context(workspace_id)
    root = context.root
    runs_root = RUNS_DIR.resolve()
    try:
        root.resolve().relative_to(runs_root)
    except ValueError as exc:
        raise ControlPlaneError(
            "WORKSPACE_DELETE_FORBIDDEN",
            "工作空间目录不在允许删除的范围内",
            status_code=403,
        ) from exc
    if not root.is_dir() or not (root / "workspace" / "v3").is_dir():
        raise ControlPlaneError(
            "WORKSPACE_NOT_FOUND",
            "工作空间不存在或已被删除",
            status_code=404,
        )
    # Move the directory out of its public location first. This makes every
    # subsequent snapshot/poll request fail before it can recreate or lock a
    # file while Windows is deleting the workspace tree.
    tombstone = RUNS_DIR / f".deleting-{workspace_id}-{uuid.uuid4().hex}"
    rename_error: OSError | None = None
    for attempt in range(8):
        try:
            root.replace(tombstone)
            rename_error = None
            break
        except OSError as exc:
            rename_error = exc
            time.sleep(0.15 * (attempt + 1))
    if rename_error is not None:
        raise ControlPlaneError(
            "WORKSPACE_DELETE_FAILED",
            "工作空间正在被使用，暂时无法开始删除",
            status_code=409,
            details={"error": f"{type(rename_error).__name__}: {rename_error}"[:500]},
        ) from rename_error

    # A request that began before the rename may still be closing a SQLite/WAL
    # handle. The workspace is already hidden from every public API at this
    # point, so complete any delayed filesystem cleanup in the background.
    if _remove_tree_with_retries(tombstone) is not None:
        _schedule_tombstone_purge(tombstone)


@app.post("/api/v3/workspaces")
async def create_workspace(request: Request) -> JSONResponse:
    try:
        body = await request.json()
        mode = str((body if isinstance(body, dict) else {}).get("project_mode") or "full_write")
        if mode not in {"full_write", "bid_rewrite"}:
            raise ControlPlaneError(
                "PROJECT_MODE_INVALID",
                f"不支持的项目模式: {mode}",
                status_code=400,
            )
        workspace_id = _workspace_id(str((body if isinstance(body, dict) else {}).get("name") or "")); root = RUNS_DIR / workspace_id
        (root / "workspace" / "v3").mkdir(parents=True); (root / "outputs" / "v3").mkdir(parents=True)
        store = ControlStore(_context(workspace_id))
        store.grant_workspace_access(str(_principal(request)["id"]), role="owner")
        profile = store.initialize_workspace_profile(mode)
        return JSONResponse({"ok": True, "workspace": {"id": workspace_id, "name": workspace_id, **profile}}, status_code=201)
    except ControlPlaneError as exc: return _error(exc)


@app.get("/api/v3/workspaces")
def list_workspaces(request: Request) -> JSONResponse:
    items = []
    for root in sorted(
        (
            path
            for path in RUNS_DIR.glob("*")
            if not path.name.startswith(".")
            and (path / "workspace" / "v3").is_dir()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ):
        try: _acl(_context(root.name), _principal(request), write=False)
        except ControlPlaneError: continue
        snapshot = V3WorkspaceSnapshotBuilder(_context(root.name)).build()
        document = snapshot.get("document") or {}
        chapters = snapshot.get("chapters") if isinstance(snapshot.get("chapters"), dict) else {}
        chapter_items = chapters.get("items") if isinstance(chapters.get("items"), list) else []
        latest_chapter_update = max(
            (
                str(item.get("updated_at") or "")
                for item in chapter_items
                if isinstance(item, dict) and str(item.get("updated_at") or "")
            ),
            default="",
        )
        items.append(
            {
                "id": root.name,
                "name": root.name,
                "project_mode": ControlStore(_context(root.name)).workspace_profile()["project_mode"],
                "mode": document.get("mode"),
                "delivery_status": (document.get("delivery") or {}).get("status", "new"),
                "chapters": {
                    "total": int(chapters.get("total") or 0),
                    "materialized": int(chapters.get("materialized") or 0),
                    "active": int(chapters.get("active") or 0),
                    "archived": int(chapters.get("archived") or 0),
                    "updated_at": latest_chapter_update,
                },
            }
        )
    return JSONResponse({"ok": True, "workspaces": items})


@app.delete("/api/v3/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: str) -> JSONResponse:
    try:
        # The authentication middleware has already verified write access for
        # this workspace before the destructive operation reaches this route.
        await run_in_threadpool(_delete_workspace_root, workspace_id)
        return JSONResponse({"ok": True, "workspace_id": workspace_id})
    except ControlPlaneError as exc:
        return _error(exc)
    except OSError as exc:
        return _error(
            ControlPlaneError(
                "WORKSPACE_DELETE_FAILED",
                "工作空间删除失败，请关闭占用该工作空间的程序后重试",
                status_code=500,
                details={"error": f"{type(exc).__name__}: {exc}"[:500]},
            )
        )


@app.post("/api/v3/workspaces/{workspace_id}/uploads")
async def upload(workspace_id: str, role: str = Form(...), file: UploadFile = File(...), replaces_input_id: str = Form("")) -> JSONResponse:
    temporary = None
    try:
        try:
            input_role = InputRole(role)
        except ValueError as exc:
            raise ControlPlaneError(
                "INPUT_ROLE_INVALID",
                f"无效输入角色: {role}",
                status_code=400,
            ) from exc
        if input_role is InputRole.LEGACY_BID:
            raise ControlPlaneError(
                "LEGACY_BID_UPLOAD_ISOLATED",
                "旧投标书必须使用专用上传入口，不能进入通用材料清单。",
                status_code=400,
            )
        context = _context(workspace_id)
        filename = Path(file.filename or "input").name
        _validate_upload_type(input_role, filename)
        temporary = (
            context.root
            / V3_ROOT
            / "uploads"
            / f"{uuid.uuid4().hex}_{filename}"
        )
        temporary.parent.mkdir(parents=True, exist_ok=True)
        max_bytes = SETTINGS.source_upload_max_bytes()
        data = await file.read(max_bytes + 1)
        if not data or len(data) > max_bytes:
            limit_mb = max_bytes // (1024 * 1024)
            raise ControlPlaneError(
                "UPLOAD_INVALID",
                f"输入文件为空或超过 {limit_mb} MB。",
                status_code=400,
            )
        temporary.write_bytes(data)
        registration = InputManifestService(context).register_local_file(
            temporary,
            input_role,
            replaces_input_id=replaces_input_id or None,
        )
        temporary.unlink()
        temporary = None
        # Keep every upload path consistent: once the file is safely registered,
        # immediately parse all active inputs and promote the refreshed source
        # index.  Downstream planning can still run later, but the UI can report
        # the real per-file parse result as soon as uploading finishes.
        source_index = await run_in_threadpool(SourceNormalizer(context).normalize_active_inputs)
        return JSONResponse(
            {
                "ok": True,
                "input": registration.item.model_dump(mode="json"),
                "source_index": source_index,
            },
            status_code=201,
        )
    except ControlPlaneError as exc:
        return _error(exc)
    except ValueError as exc:
        return _error(ControlPlaneError("UPLOAD_INVALID", str(exc), status_code=400))
    finally:
        if temporary and temporary.exists(): temporary.unlink()
        await file.close()


@app.post("/api/v3/workspaces/{workspace_id}/legacy-bids")
async def upload_legacy_bid(workspace_id: str, file: UploadFile = File(...)) -> JSONResponse:
    temporary = None
    try:
        context = _context(workspace_id)
        filename = Path(file.filename or "legacy-bid").name
        suffix = Path(filename).suffix.lower()
        if suffix not in NORMALIZABLE_EXTENSIONS:
            raise ControlPlaneError(
                "LEGACY_BID_TYPE_UNSUPPORTED",
                "旧投标书仅支持 .docx、.pdf、.md、.txt。",
                status_code=400,
            )
        data = await file.read(SETTINGS.source_upload_max_bytes() + 1)
        if not data or len(data) > SETTINGS.source_upload_max_bytes():
            raise ControlPlaneError(
                "LEGACY_BID_UPLOAD_INVALID",
                "旧投标书为空或超过上传大小限制。",
                status_code=400,
            )
        temporary = context.root / V3_ROOT / "legacy_bid_uploads" / f"{uuid.uuid4().hex}_{filename}"
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(data)
        source = await run_in_threadpool(
            LegacyBidSourceService(context).register_local_file,
            temporary,
            filename,
        )
        index = LegacyBidSourceService(context).index(source.legacy_bid_id)
        return JSONResponse(
            {
                "ok": True,
                "legacy_bid": source.model_dump(mode="json"),
                "index": index.model_dump(mode="json"),
            },
            status_code=201,
        )
    except ControlPlaneError as exc:
        return _error(exc)
    except ValueError as exc:
        return _error(ControlPlaneError("LEGACY_BID_PARSE_FAILED", str(exc), status_code=400))
    except Exception as exc:
        return _error(
            ControlPlaneError(
                "LEGACY_BID_PARSE_FAILED",
                f"旧投标书解析失败: {exc}",
                status_code=400,
            )
        )
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
        await file.close()


@app.get("/api/v3/workspaces/{workspace_id}/legacy-bids")
def list_legacy_bids(workspace_id: str) -> JSONResponse:
    try:
        sources = LegacyBidSourceService(_context(workspace_id)).list_sources()
        return JSONResponse(
            {"ok": True, "legacy_bids": [item.model_dump(mode="json") for item in sources]}
        )
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/legacy-bids/{legacy_bid_id}/index")
def get_legacy_bid_index(workspace_id: str, legacy_bid_id: str) -> JSONResponse:
    try:
        index = LegacyBidSourceService(_context(workspace_id)).index(legacy_bid_id)
        return JSONResponse({"ok": True, "index": index.model_dump(mode="json")})
    except ControlPlaneError as exc:
        return _error(exc)


def _gateway(context: WorkspaceContext) -> CommandGateway: return CommandGateway(context, V3ExecutionController(context).handlers())

@app.post("/api/v3/workspaces/{workspace_id}/commands")
async def command(workspace_id: str, request: Request) -> JSONResponse:
    try:
        context = _context(workspace_id)
        payload = await request.json()
        payload["actor"] = {"type": "user", "id": str(_principal(request).get("id") or "")}
        envelope = CommandEnvelope.from_mapping(payload, workspace_id=workspace_id)
        # Stage handlers may wait several minutes for a model response. Keep
        # the event loop free so the UI can poll the snapshot and observe real
        # progress while the command is being finalized.
        receipt = await run_in_threadpool(_gateway(context).submit, envelope)
        return JSONResponse(
            {
                "ok": receipt.status != "rejected",
                "receipt": receipt.as_dict(),
                "message": receipt.message,
            },
            status_code=202,
        )
    except ControlPlaneError as exc: return _error(exc)


@app.post("/api/v3/workspaces/{workspace_id}/chapter-batch-jobs")
async def create_chapter_batch_job(workspace_id: str, request: Request) -> JSONResponse:
    """Create a durable batch; generation continues after this request returns."""
    try:
        from document_pipeline.chapter_batch import ChapterBatchService

        body = await request.json()
        chapter_ids = body.get("chapter_ids") if isinstance(body, dict) else None
        if not isinstance(chapter_ids, list):
            raise ControlPlaneError("CHAPTER_BATCH_INVALID", "chapter_ids 必须是数组。", status_code=400)
        actor = {"type": "user", "id": str(_principal(request).get("id") or "")}
        job = ChapterBatchService(_context(workspace_id)).create(
            [str(item) for item in chapter_ids],
            actor=actor,
            idempotency_key=str(body.get("idempotency_key") or ""),
        )
        events = ControlStore(_context(workspace_id)).batch_events(str(job.get("job_id") or ""))
        return JSONResponse(
            {
                "ok": True,
                "job": job,
                "job_id": job.get("job_id"),
                "operation_id": job.get("operation_id"),
                "initial_sequence": max((int(item.get("sequence") or 0) for item in events), default=0),
            },
            status_code=202,
        )
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/chapter-batch-jobs/current")
def get_current_chapter_batch_job(workspace_id: str) -> JSONResponse:
    job = ControlStore(_context(workspace_id)).latest_batch_job()
    return JSONResponse({"ok": True, "job": job})


@app.get("/api/v3/workspaces/{workspace_id}/chapter-batch-jobs/{job_id}")
def get_chapter_batch_job(workspace_id: str, job_id: str) -> JSONResponse:
    try:
        job = ControlStore(_context(workspace_id)).batch_job(job_id)
        if not job:
            raise ControlPlaneError("CHAPTER_BATCH_NOT_FOUND", "批量编写任务不存在。", status_code=404)
        return JSONResponse({"ok": True, "job": job})
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/chapter-batch-jobs/{job_id}/events")
def get_chapter_batch_events(
    workspace_id: str,
    job_id: str,
    after_sequence: int = 0,
) -> JSONResponse:
    try:
        from document_pipeline.chapter_batch import ChapterBatchService

        events = ChapterBatchService(_context(workspace_id)).events(
            job_id,
            after_sequence=max(0, int(after_sequence)),
        )
        return JSONResponse(
            {
                "ok": True,
                "events": events,
                "last_sequence": max(
                    (int(item.get("sequence") or 0) for item in events),
                    default=max(0, int(after_sequence)),
                ),
            }
        )
    except ControlPlaneError as exc:
        return _error(exc)


@app.post("/api/v3/workspaces/{workspace_id}/chapter-batch-jobs/{job_id}/{action}")
def act_on_chapter_batch_job(workspace_id: str, job_id: str, action: str) -> JSONResponse:
    try:
        from document_pipeline.chapter_batch import ChapterBatchService

        job = ChapterBatchService(_context(workspace_id)).action(job_id, action)
        return JSONResponse({"ok": True, "job": job}, status_code=202)
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/planning/confirmation")
def planning_confirmation(workspace_id: str) -> JSONResponse:
    try:
        from document_pipeline.artifact_promotion import HumanGateService
        return JSONResponse({"ok": True, "planning_snapshot": HumanGateService(_context(workspace_id)).planning_snapshot()})
    except ControlPlaneError as exc: return _error(exc)


@app.post("/api/v3/workspaces/{workspace_id}/chat/turn")
async def chat_turn(workspace_id: str, request: Request) -> JSONResponse:
    body = await request.json(); message = str(body.get("message") or "").strip()
    if not message: return JSONResponse({"ok": False, "message": "请输入要处理的问题。"}, status_code=400)
    from document_pipeline.workspace_chat import WorkspaceChatService

    actor = {"type": "user", "id": str(_principal(request).get("id") or "")}
    try:
        result = await run_in_threadpool(
            WorkspaceChatService(_context(workspace_id)).answer,
            message,
            actor=actor,
        )
    except RuntimeError as exc:
        return JSONResponse(
            {"ok": False, "message": str(exc), "error": {"code": "WORKSPACE_AGENT_LLM_FAILED"}},
            status_code=502,
        )
    return JSONResponse({"ok": True, **result})

@app.get("/api/v3/workspaces/{workspace_id}/snapshot")
def snapshot(workspace_id: str) -> JSONResponse:
    context = _context(workspace_id)
    payload = V3WorkspaceSnapshotBuilder(context).build()
    try:
        from document_pipeline.global_project_context import GlobalProjectContextService

        payload["global_project_context"] = GlobalProjectContextService(
            context
        ).load()
    except ControlPlaneError:
        payload["global_project_context"] = None
    return JSONResponse({"ok": True, "snapshot": payload})


@app.get("/api/v3/workspaces/{workspace_id}/stream")
async def workspace_stream(
    workspace_id: str,
    request: Request,
) -> StreamingResponse:
    """Push workspace snapshots over one long-lived SSE connection."""
    context = _context(workspace_id)

    def encode(event: str, payload: dict[str, Any]) -> str:
        return (
            f"event: {event}\n"
            f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
        )

    async def events():
        last_seq = 0
        try:
            initial = await run_in_threadpool(
                V3WorkspaceSnapshotBuilder(context).build
            )
            yield encode("snapshot", {"ok": True, "snapshot": initial})
            latest = await run_in_threadpool(
                ControlStore(context).recent_events, limit=1
            )
            last_seq = max((int(row.get("seq") or 0) for row in latest), default=0)
        except Exception:
            yield encode("closed", {"ok": False, "reason": "workspace_unavailable"})
            return

        while not await request.is_disconnected():
            # The browser keeps one connection open; it never polls this
            # endpoint.  We only build and send a new snapshot after a durable
            # workspace event has been committed.
            if not context.root.is_dir():
                yield encode("closed", {"ok": False, "reason": "workspace_deleted"})
                return
            try:
                changed = await run_in_threadpool(
                    ControlStore(context).events,
                    last_seq,
                    limit=200,
                )
                if changed:
                    last_seq = max(int(row.get("seq") or 0) for row in changed)
                    current = await run_in_threadpool(
                        V3WorkspaceSnapshotBuilder(context).build
                    )
                    yield encode("snapshot", {"ok": True, "snapshot": current})
                await asyncio.sleep(0.25)
            except Exception:
                yield encode("closed", {"ok": False, "reason": "workspace_unavailable"})
                return

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/v3/workspaces/{workspace_id}/global-project-context")
def get_global_project_context(workspace_id: str) -> JSONResponse:
    try:
        from document_pipeline.global_project_context import GlobalProjectContextService

        return JSONResponse({
            "ok": True,
            "global_project_context": GlobalProjectContextService(
                _context(workspace_id)
            ).load(),
        })
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/chapters")
def list_chapters(
    workspace_id: str,
    include_archived: bool = Query(True),
) -> JSONResponse:
    try:
        from document_pipeline.chapter_workspace import ChapterWorkspaceService

        chapters = ChapterWorkspaceService(_context(workspace_id)).list_chapters(
            include_archived=include_archived
        )
        return JSONResponse({"ok": True, "chapters": chapters})
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}")
def get_chapter(workspace_id: str, chapter_id: str) -> JSONResponse:
    try:
        from document_pipeline.chapter_workspace import ChapterWorkspaceService

        context = _context(workspace_id)
        chapter = ChapterWorkspaceService(context).get_chapter(chapter_id)
        try:
            from document_pipeline.global_project_context import GlobalProjectContextService

            shared = GlobalProjectContextService(context).load_model()
            chapter["global_context_ref"] = {
                "global_context_id": shared.global_context_id,
                "global_context_revision": shared.global_context_revision,
                "global_context_hash": shared.global_context_hash,
            }
        except ControlPlaneError:
            chapter["global_context_ref"] = None
        context_record = chapter.get("context")
        context_record = (
            context_record if isinstance(context_record, dict) else {}
        )
        chapter["chapter_context_ref"] = {
            "chapter_context_id": f"chapter-context:{chapter_id}",
            "chapter_context_revision": int(
                context_record.get("context_revision") or 0
            ),
            "chapter_context_hash": chapter_context_hash(
                chapter_id,
                int(context_record.get("context_revision") or 0),
                context_record.get("items") or [],
            ),
        }
        try:
            from document_pipeline.chapter_semantics import (
                project_chapter_semantic_requirements,
            )

            requirements, scoring = project_chapter_semantic_requirements(
                context, chapter
            )
            chapter["chapter_requirements"] = requirements
            chapter["chapter_scoring_requirements"] = scoring
        except (ControlPlaneError, ValueError):
            chapter["chapter_requirements"] = []
            chapter["chapter_scoring_requirements"] = []
        try:
            from document_pipeline.sibling_chapter_context import (
                SiblingChapterContextService,
            )

            chapter["sibling_chapter_context"] = SiblingChapterContextService(
                context
            ).build_for_chapter(chapter)
        except Exception:
            chapter["sibling_chapter_context"] = {
                "siblings": [],
                "missing_upstream": [],
                "ready_for_dependent_writing": True,
                "writing_policy": {"rules": [], "guidance": ""},
            }
        try:
            from document_pipeline.document_outline_context import (
                DocumentOutlineContextService,
            )

            chapter["document_outline_context"] = DocumentOutlineContextService(
                context
            ).build_for_chapter(chapter)
        except Exception:
            chapter["document_outline_context"] = {
                "outline": [],
                "outline_tree": [],
                "related_summaries": [],
                "position": {},
                "access": {
                    "mode": "read_only_outline",
                    "can_edit_other_chapters": False,
                },
            }
        try:
            from document_pipeline.writing_orientation import (
                WritingOrientationService,
            )

            chapter["writing_orientation"] = WritingOrientationService(
                context
            ).build_for_chapter(
                chapter,
                outline_context=chapter.get("document_outline_context"),
                sibling_context=chapter.get("sibling_chapter_context"),
                tender_requirements=chapter.get("chapter_requirements") or [],
                scoring_requirements=chapter.get("chapter_scoring_requirements")
                or [],
            )
        except Exception:
            chapter["writing_orientation"] = None
        return JSONResponse({"ok": True, "chapter": chapter})
    except ControlPlaneError as exc:
        return _error(exc)


@app.get(
    "/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/readonly/{target_chapter_id}"
)
def chapter_readonly_view(
    workspace_id: str,
    chapter_id: str,
    target_chapter_id: str,
) -> JSONResponse:
    """Read-only inspection of another chapter from the active chapter's workbench."""
    try:
        from document_pipeline.document_outline_context import (
            DocumentOutlineContextService,
        )

        context = _context(workspace_id)
        # Ensure the viewer chapter exists in the blueprint/workspace chain.
        from document_pipeline.chapter_workspace import ChapterWorkspaceService

        ChapterWorkspaceService(context).get_chapter(chapter_id)
        view = DocumentOutlineContextService(context).readonly_chapter_view(
            target_chapter_id,
            viewer_chapter_id=chapter_id,
        )
        return JSONResponse({"ok": True, "chapter_view": view})
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/chat/history")
def chapter_chat_history(
    workspace_id: str,
    chapter_id: str,
    limit: int = Query(40, ge=1, le=200),
) -> JSONResponse:
    """Return the isolated dialogue history for one chapter."""
    try:
        from document_pipeline.chapter_chat import ChapterChatService
        from document_pipeline.chapter_workspace import ChapterWorkspaceService

        context = _context(workspace_id)
        chapter = ChapterWorkspaceService(context).get_chapter(chapter_id)
        service = ChapterChatService(context)
        turns = service.load_history(chapter_id, limit=limit)
        batch_turns = service.load_batch_history(chapter_id)
        return JSONResponse(
            {
                "ok": True,
                "chapter_id": str(chapter.get("chapter_id") or chapter_id),
                "title": str(chapter.get("title") or ""),
                "turns": turns,
                "batch_turns": batch_turns,
            }
        )
    except ControlPlaneError as exc:
        return _error(exc)


@app.put("/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/chat/history")
async def chapter_chat_history_update(
    workspace_id: str,
    chapter_id: str,
    request: Request,
) -> JSONResponse:
    """Edit one persisted chapter-chat turn. Collaboration log only."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        from document_pipeline.chapter_chat import ChapterChatService
        from document_pipeline.chapter_workspace import ChapterWorkspaceService

        context = _context(workspace_id)
        chapter = ChapterWorkspaceService(context).get_chapter(chapter_id)
        updated = ChapterChatService(context).update_turn(
            chapter_id,
            turn_id=str((body or {}).get("turn_id") or ""),
            created_at=str((body or {}).get("created_at") or ""),
            role=str((body or {}).get("role") or ""),
            content=(
                None
                if not isinstance(body, dict) or "content" not in body
                else body.get("content")
            ),
            thinking=(
                None
                if not isinstance(body, dict) or "thinking" not in body
                else body.get("thinking")
            ),
        )
        return JSONResponse(
            {
                "ok": True,
                "chapter_id": str(chapter.get("chapter_id") or chapter_id),
                "turn": updated,
            }
        )
    except ControlPlaneError as exc:
        return _error(exc)


@app.post("/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/chat/history")
async def chapter_chat_history_append(
    workspace_id: str,
    chapter_id: str,
    request: Request,
) -> JSONResponse:
    """Persist an Agent execution record so it survives page or service restarts."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        from document_pipeline.chapter_chat import ChapterChatService
        from document_pipeline.chapter_workspace import ChapterWorkspaceService

        context = _context(workspace_id)
        chapter = ChapterWorkspaceService(context).get_chapter(chapter_id)
        record = ChapterChatService(context).append_turn(
            chapter_id,
            role="assistant",
            content=str((body or {}).get("content") or ""),
            thinking=str((body or {}).get("thinking") or ""),
            research_steps=list((body or {}).get("research_steps") or []),
            elapsed_seconds=(body or {}).get("elapsed_seconds"),
            operation_id=str((body or {}).get("operation_id") or ""),
            status=str((body or {}).get("status") or ""),
        )
        return JSONResponse(
            {
                "ok": True,
                "chapter_id": str(chapter.get("chapter_id") or chapter_id),
                "turn": record,
            }
        )
    except ControlPlaneError as exc:
        return _error(exc)


@app.delete("/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/chat/history")
async def chapter_chat_history_delete(
    workspace_id: str,
    chapter_id: str,
    request: Request,
) -> JSONResponse:
    """Permanently delete one persisted chapter-chat turn."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        from document_pipeline.chapter_chat import ChapterChatService
        from document_pipeline.chapter_workspace import ChapterWorkspaceService

        context = _context(workspace_id)
        chapter = ChapterWorkspaceService(context).get_chapter(chapter_id)
        service = ChapterChatService(context)
        if bool((body or {}).get("clear_all")):
            deleted_count = service.clear_history(chapter_id)
        else:
            service.delete_turn(
                chapter_id,
                turn_id=str((body or {}).get("turn_id") or ""),
                created_at=str((body or {}).get("created_at") or ""),
                role=str((body or {}).get("role") or ""),
            )
            deleted_count = 1
        return JSONResponse(
            {
                "ok": True,
                "chapter_id": str(chapter.get("chapter_id") or chapter_id),
                "deleted_count": deleted_count,
            }
        )
    except ControlPlaneError as exc:
        return _error(exc)


def _chapter_chat_runtime(workspace_id: str, chapter_id: str) -> dict[str, Any]:
    """Load chapter-scoped chat inputs shared by turn and stream endpoints."""
    from document_pipeline.chapter_chat import ChapterAgentService
    from document_pipeline.chapter_workspace import ChapterWorkspaceService

    context = _context(workspace_id)
    chapter = ChapterWorkspaceService(context).get_chapter(chapter_id)
    try:
        from document_pipeline.chapter_semantics import (
            project_chapter_semantic_requirements,
        )

        requirements, scoring = project_chapter_semantic_requirements(
            context, chapter
        )
    except (ControlPlaneError, ValueError):
        requirements, scoring = [], []
    try:
        from document_pipeline.chapter_semantics import (
            load_chapter_project_context,
        )

        global_project_context = load_chapter_project_context(context)
    except ControlPlaneError:
        global_project_context = None
    try:
        from document_pipeline.sibling_chapter_context import (
            SiblingChapterContextService,
        )

        sibling_context = SiblingChapterContextService(context).build_for_chapter(
            chapter
        )
    except Exception:
        sibling_context = None
    try:
        from document_pipeline.document_outline_context import (
            DocumentOutlineContextService,
        )

        outline_context = DocumentOutlineContextService(context).build_for_chapter(
            chapter
        )
    except Exception:
        outline_context = None
    try:
        from document_pipeline.writing_orientation import WritingOrientationService

        writing_orientation = WritingOrientationService(context).build_for_chapter(
            chapter,
            outline_context=outline_context,
            sibling_context=sibling_context,
            tender_requirements=requirements,
            scoring_requirements=scoring,
        )
    except Exception:
        writing_orientation = None
    return {
        "context": context,
        "chapter": chapter,
        "service": ChapterAgentService(context),
        "requirements": requirements,
        "scoring": scoring,
        "global_project_context": global_project_context,
        "sibling_context": sibling_context,
        "outline_context": outline_context,
        "writing_orientation": writing_orientation,
    }


@app.post("/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/chat/turn")
async def chapter_chat_turn(
    workspace_id: str,
    chapter_id: str,
    request: Request,
) -> JSONResponse:
    """Chapter-scoped chat: history and context never cross chapter boundaries."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    message = str((body or {}).get("message") or "").strip()
    if not message:
        return JSONResponse(
            {"ok": False, "message": "请输入要处理的问题。"},
            status_code=400,
        )
    try:
        runtime = _chapter_chat_runtime(workspace_id, chapter_id)
        result = runtime["service"].answer(
            chapter_id,
            message,
            chapter=runtime["chapter"],
            global_project_context=runtime["global_project_context"],
            tender_requirements=runtime["requirements"],
            scoring_requirements=runtime["scoring"],
            sibling_context=runtime["sibling_context"],
            outline_context=runtime["outline_context"],
            writing_orientation=runtime["writing_orientation"],
            actor=dict(_principal(request)),
        )
        snapshot_data = V3WorkspaceSnapshotBuilder(runtime["context"]).build()
        return JSONResponse(
            {
                "ok": True,
                "reply": result["reply"],
                "thinking": result.get("thinking") or "",
                "chapter_id": result["chapter_id"],
                "turns": result.get("history_tail") or [],
                "document_write_requested": bool(
                    result.get("document_write_requested")
                ),
                "document_approval_requested": bool(
                    result.get("document_approval_requested")
                ),
                "document_write_completed": bool(
                    result.get("document_write_completed")
                ),
                "chapter": result.get("chapter"),
                "content": result.get("content"),
                "workspace_revision": snapshot_data.get("workspace_revision", 0),
            }
        )
    except ControlPlaneError as exc:
        return _error(exc)


@app.post("/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/chat/stream")
async def chapter_chat_stream(
    workspace_id: str,
    chapter_id: str,
    request: Request,
) -> StreamingResponse:
    """Stream chapter chat thinking + answer deltas as NDJSON events."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    message = str((body or {}).get("message") or "").strip()
    if not message:
        return JSONResponse(
            {"ok": False, "message": "请输入要处理的问题。"},
            status_code=400,
        )

    def generate():
        try:
            runtime = _chapter_chat_runtime(workspace_id, chapter_id)
            for event in runtime["service"].iter_answer_events(
                chapter_id,
                message,
                chapter=runtime["chapter"],
                global_project_context=runtime["global_project_context"],
                tender_requirements=runtime["requirements"],
                scoring_requirements=runtime["scoring"],
                sibling_context=runtime["sibling_context"],
                outline_context=runtime["outline_context"],
                writing_orientation=runtime["writing_orientation"],
                actor=dict(_principal(request)),
            ):
                event_type = str(event.get("type") or "delta")
                payload = {key: value for key, value in event.items() if key != "type"}
                if event_type == "done":
                    try:
                        snapshot_data = V3WorkspaceSnapshotBuilder(
                            runtime["context"]
                        ).build()
                        payload["workspace_revision"] = snapshot_data.get(
                            "workspace_revision", 0
                        )
                    except Exception:
                        payload["workspace_revision"] = 0
                yield _ndjson_event(event_type, **payload)
        except ControlPlaneError as exc:
            code = str(exc.code or "CHAPTER_CHAT_FAILED")
            error_message = str(exc.message or "章节对话失败")
            details = dict(exc.details or {})
            if code == "WRITER_RESEARCH_ACTION_REQUIRED":
                research = details.get("research") if isinstance(details.get("research"), dict) else {}
                queries = research.get("queries") if isinstance(research.get("queries"), list) else []
                candidate_count = sum(
                    int(item.get("evidence_count") or 0)
                    for item in queries
                    if isinstance(item, dict)
                )
                errors = [
                    str(item.get("error") or "").strip()
                    for item in queries
                    if isinstance(item, dict) and str(item.get("error") or "").strip()
                ]
                attempt_count = sum(
                    len(item.get("attempts") or [])
                    for item in queries
                    if isinstance(item, dict) and isinstance(item.get("attempts"), list)
                ) or 1
                code = "CHAPTER_RESEARCH_UNAVAILABLE"
                provider_failed = any(
                    value == "provider_failed" or value.startswith("SEARCH_FAILED:")
                    for value in errors
                )
                error_message = (
                    "本章 WritingPlan 存在必须由可核验公开原始来源补齐的资料缺口，"
                    f"系统已执行 {attempt_count} 轮不同策略的 Tavily 检索，但 Tavily 请求连续失败，"
                    "并非检索结果不合格；已在正文生成前停止。"
                    if provider_failed
                    else (
                        "本章 WritingPlan 存在必须由可核验公开原始来源补齐的资料缺口，"
                        f"系统已执行 {attempt_count} 轮不同策略的 Tavily 检索，仍未取得合格来源，"
                        "已在正文生成前停止。"
                    )
                )
                details = {
                    **details,
                    "error": errors[0] if errors else str(research.get("decision_status") or exc.message),
                    "candidate_count": candidate_count,
                    "attempt_count": attempt_count,
                    "original_code": exc.code,
                }
            yield _ndjson_event(
                "error",
                chapter_id=str(chapter_id or ""),
                code=code,
                message=error_message,
                details=details,
            )
        except Exception as exc:
            yield _ndjson_event(
                "error",
                chapter_id=str(chapter_id or ""),
                code="CHAPTER_CHAT_STREAM_FAILED",
                message=str(exc) or "章节对话流式失败",
            )

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _ndjson_event(event_type: str, **payload: Any) -> bytes:
    return (
        json.dumps(
            {"type": event_type, **payload},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


@app.post(
    "/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/draft/stream"
)
async def stream_chapter_draft(
    workspace_id: str,
    chapter_id: str,
    request: Request,
) -> StreamingResponse:
    """Forward ChapterWritingService events; HTTP owns no writing orchestration."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    principal = _principal(request)
    normalized_chapter_id = str(chapter_id or "").strip()
    operation_id = str(body.get("idempotency_key") or "").strip() or (
        f"chapter-write:{normalized_chapter_id}:{uuid.uuid4()}"
    )

    def generate():
        try:
            from document_pipeline.chapter_writing_service import (
                ChapterWritingRequest,
                ChapterWritingService,
            )
            from document_pipeline.chapter_workspace import ChapterWorkspaceService

            context = _context(workspace_id)
            chapter = ChapterWorkspaceService(context).get_chapter(
                normalized_chapter_id
            )
            expected_workspace_revision = int(body.get("expected_revision"))
            expected_chapter_revision = int(
                body.get("expected_chapter_revision")
            )
            is_rewrite = ControlStore(context).workspace_profile().get("project_mode") == "bid_rewrite"
            write_request = ChapterWritingRequest(
                unit_id=f"chapter-{normalized_chapter_id}",
                node_ids=(normalized_chapter_id,),
                operation_id=operation_id,
                operation=(
                    "rewrite"
                    if int(chapter.get("head_content_revision") or 0) > 0
                    else "create"
                ),
                user_instruction=str(body.get("instruction") or "").strip(),
                overwrite_locked=bool(body.get("overwrite_locked")),
                chapter_id=normalized_chapter_id,
                expected_workspace_revision=expected_workspace_revision,
                expected_chapter_revision=expected_chapter_revision,
                actor=dict(principal),
                run_research=not is_rewrite,
                commit_drafts=True,
            )
            if is_rewrite:
                from document_pipeline.bid_rewrite_execution import BidRewriteExecutionService

                events = BidRewriteExecutionService(context).iter_events(
                    chapter_id=normalized_chapter_id,
                    operation_id=operation_id,
                    expected_workspace_revision=expected_workspace_revision,
                    expected_chapter_revision=expected_chapter_revision,
                    actor=dict(principal),
                    overwrite_locked=bool(body.get("overwrite_locked")),
                )
            else:
                events = ChapterWritingService(context).iter_events(write_request)
            for event in events:
                payload = dict(event)
                event_type = str(payload.pop("type", "message"))
                yield _ndjson_event(event_type, **payload)
        except ControlPlaneError as exc:
            code = str(exc.code or "CHAPTER_WRITING_FAILED")
            message = str(exc.message or "章节写作失败。")
            details = dict(exc.details or {})
            if code == "WRITER_RESEARCH_ACTION_REQUIRED":
                research = details.get("research") if isinstance(details.get("research"), dict) else {}
                queries = research.get("queries") if isinstance(research.get("queries"), list) else []
                candidate_count = sum(
                    int(item.get("evidence_count") or 0)
                    for item in queries
                    if isinstance(item, dict)
                )
                errors = [
                    str(item.get("error") or "").strip()
                    for item in queries
                    if isinstance(item, dict) and str(item.get("error") or "").strip()
                ]
                attempt_count = sum(
                    len(item.get("attempts") or [])
                    for item in queries
                    if isinstance(item, dict) and isinstance(item.get("attempts"), list)
                ) or 1
                code = "CHAPTER_RESEARCH_UNAVAILABLE"
                provider_failed = any(
                    value == "provider_failed" or value.startswith("SEARCH_FAILED:")
                    for value in errors
                )
                message = (
                    "本章 WritingPlan 存在必须由可核验公开原始来源补齐的资料缺口，"
                    f"系统已执行 {attempt_count} 轮不同策略的 Tavily 检索，但 Tavily 请求连续失败，"
                    "并非检索结果不合格；已在正文生成前停止。"
                    if provider_failed
                    else (
                        "本章 WritingPlan 存在必须由可核验公开原始来源补齐的资料缺口，"
                        f"系统已执行 {attempt_count} 轮不同策略的 Tavily 检索，仍未取得合格来源，"
                        "已在正文生成前停止。"
                    )
                )
                details = {
                    **details,
                    "error": errors[0] if errors else str(research.get("decision_status") or exc.message),
                    "candidate_count": candidate_count,
                    "attempt_count": attempt_count,
                    "original_code": exc.code,
                }
            yield _ndjson_event(
                "error",
                chapter_id=normalized_chapter_id,
                code=code,
                message=message,
                details=details,
            )
        except (TypeError, ValueError) as exc:
            raw_reason = str(exc).strip()
            if raw_reason == "G4_CONTENT_TOO_SHORT_OR_HOLLOW":
                user_message = (
                    "本次生成的正文过短或缺少实质内容，已停止写入草稿。"
                    "请补充本章写作要点后重试。"
                )
            else:
                user_message = "正文生成请求未通过校验，请检查本章提纲和上下文后重试。"
            yield _ndjson_event(
                "error",
                chapter_id=normalized_chapter_id,
                code="CHAPTER_WRITE_REQUEST_INVALID",
                message=user_message,
                details={"reason_code": raw_reason} if raw_reason else {},
            )
        except Exception as exc:
            yield _ndjson_event(
                "error",
                chapter_id=normalized_chapter_id,
                code="CHAPTER_WRITING_FAILED",
                message=str(exc) or "章节写作失败。",
            )

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )

@app.get("/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/rewrite-match")
def get_chapter_rewrite_match(
    workspace_id: str,
    chapter_id: str,
) -> JSONResponse:
    try:
        from document_pipeline.chapter_rewrite_match import ChapterRewriteMatchService

        rewrite_match = ChapterRewriteMatchService(_context(workspace_id)).latest(
            chapter_id
        )
        return JSONResponse({"ok": True, "rewrite_match": rewrite_match})
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/rewrite-plan")
def get_chapter_rewrite_plan(
    workspace_id: str,
    chapter_id: str,
    revision: int | None = Query(default=None),
) -> JSONResponse:
    try:
        from document_pipeline.chapter_rewrite_plan import ChapterRewritePlanService

        rewrite_plan = ChapterRewritePlanService(_context(workspace_id)).get(
            chapter_id, revision
        )
        return JSONResponse({"ok": True, "rewrite_plan": rewrite_plan})
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/rewrite-plan/revisions")
def list_chapter_rewrite_plan_revisions(
    workspace_id: str,
    chapter_id: str,
) -> JSONResponse:
    try:
        from document_pipeline.chapter_rewrite_plan import ChapterRewritePlanService

        revisions = ChapterRewritePlanService(_context(workspace_id)).history(chapter_id)
        return JSONResponse({"ok": True, "revisions": revisions})
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/rewrite-plan/events")
def list_chapter_rewrite_plan_events(
    workspace_id: str,
    chapter_id: str,
) -> JSONResponse:
    try:
        context = _context(workspace_id)
        if ControlStore(context).workspace_profile().get("project_mode") != "bid_rewrite":
            raise ControlPlaneError("REWRITE_MODE_REQUIRED", "当前工作空间不支持改写方案。", status_code=409)
        events = ControlStore(context).chapter_rewrite_events(chapter_id)
        return JSONResponse({"ok": True, "events": events})
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/context/revisions")
def list_chapter_context_revisions(
    workspace_id: str,
    chapter_id: str,
    limit: int = Query(100),
) -> JSONResponse:
    try:
        from document_pipeline.chapter_workspace import ChapterWorkspaceService

        payload = ChapterWorkspaceService(_context(workspace_id)).list_context_revisions(
            chapter_id,
            limit=limit,
        )
        return JSONResponse({"ok": True, **payload})
    except ControlPlaneError as exc:
        return _error(exc)


@app.get(
    "/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/context/revisions/{revision}"
)
def get_chapter_context_revision(
    workspace_id: str,
    chapter_id: str,
    revision: int,
) -> JSONResponse:
    try:
        from document_pipeline.chapter_workspace import ChapterWorkspaceService

        context = ChapterWorkspaceService(_context(workspace_id)).get_context_revision(
            chapter_id,
            revision,
        )
        return JSONResponse({"ok": True, "context": context})
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/revisions")
def list_chapter_content_revisions(
    workspace_id: str,
    chapter_id: str,
    limit: int = Query(100),
) -> JSONResponse:
    try:
        store = ControlStore(_context(workspace_id))
        workspace = store.chapter_workspace(chapter_id)
        if workspace is None:
            raise ControlPlaneError(
                "CHAPTER_NOT_FOUND",
                f"章节 Workspace 不存在: {chapter_id}",
                status_code=404,
            )
        revisions = store.chapter_content_revisions(chapter_id, limit=limit)
        return JSONResponse(
            {
                "ok": True,
                "chapter_id": chapter_id,
                "head_content_revision": int(workspace.get("head_content_revision") or 0),
                "formal_content_revision": int(workspace.get("formal_content_revision") or 0),
                "chapter_revision": int(workspace.get("chapter_revision") or 0),
                "revisions": revisions,
            }
        )
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/revisions/compare")
def compare_chapter_content_revisions(
    workspace_id: str,
    chapter_id: str,
    from_revision: int = Query(..., alias="from"),
    to_revision: int = Query(..., alias="to"),
) -> JSONResponse:
    try:
        from document_pipeline.chapter_editing import ChapterEditingService

        payload = ChapterEditingService(_context(workspace_id)).compare_revisions(
            chapter_id,
            from_revision=from_revision,
            to_revision=to_revision,
        )
        return JSONResponse({"ok": True, "compare": payload})
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/revisions/{revision}")
def get_chapter_content_revision(
    workspace_id: str,
    chapter_id: str,
    revision: int,
) -> JSONResponse:
    try:
        content = ControlStore(_context(workspace_id)).chapter_content_revision(
            chapter_id, revision
        )
        if content is None:
            raise ControlPlaneError(
                "CHAPTER_CONTENT_NOT_FOUND",
                f"Content revision 不存在: {chapter_id}@{revision}",
                status_code=404,
            )
        return JSONResponse({"ok": True, "content": content})
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/document/compose")
def compose_formal_document(workspace_id: str) -> JSONResponse:
    try:
        from document_pipeline.chapter_editing import ChapterEditingService

        document = ChapterEditingService(_context(workspace_id)).compose_formal_document()
        return JSONResponse({"ok": True, "document": document})
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/content-units/{unit_id}")
def content_unit_detail(workspace_id: str, unit_id: str) -> JSONResponse:
    try:
        detail = V3WorkspaceSnapshotBuilder(
            _context(workspace_id)
        ).content_unit_detail(unit_id)
        return JSONResponse({"ok": True, "content_unit": detail})
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/generation-stages/{stage_id}")
def generation_stage_detail(
    workspace_id: str,
    stage_id: str,
) -> JSONResponse:
    try:
        detail = V3WorkspaceSnapshotBuilder(
            _context(workspace_id)
        ).generation_stage_detail(stage_id)
        return JSONResponse({"ok": True, "stage": detail})
    except ControlPlaneError as exc:
        return _error(exc)


@app.get("/api/v3/workspaces/{workspace_id}/document-preview")
def document_preview(workspace_id: str) -> JSONResponse:
    try:
        preview = DocumentPreviewService(_context(workspace_id)).build()
        return JSONResponse({"ok": True, "preview": preview})
    except ControlPlaneError as exc:
        return _error(exc)

@app.get("/api/v3/workspaces/{workspace_id}/events")
def events(workspace_id: str, after_seq: int = Query(0), limit: int = Query(200)) -> JSONResponse: return JSONResponse({"ok": True, "events": ControlStore(_context(workspace_id)).events(after_seq, limit=min(limit, 2000))})

@app.get("/api/v3/workspaces/{workspace_id}/evidence")
def evidence(workspace_id: str) -> JSONResponse:
    context = _context(workspace_id); snapshot_data = V3WorkspaceSnapshotBuilder(context).build(); directory = context.root / V3_ROOT / "evidence" / "batches"
    return JSONResponse({"ok": True, "needs": snapshot_data.get("evidence_needs", []), "batches": [read_json(path) for path in directory.glob("*.json")] if directory.is_dir() else []})

@app.get("/api/v3/workspaces/{workspace_id}/gates/latest")
def latest_gate(workspace_id: str) -> JSONResponse:
    snapshot_data = V3WorkspaceSnapshotBuilder(_context(workspace_id)).build()
    return JSONResponse({"ok": True, "quality": snapshot_data.get("quality", {}), "delivery": (snapshot_data.get("document") or {}).get("delivery", {})})

@app.get("/api/v3/workspaces/{workspace_id}/exports/final")
def export(workspace_id: str):
    context = _context(workspace_id)
    try:
        from document_pipeline.chapter_editing import ChapterEditingService

        composed = ChapterEditingService(context).compose_formal_document()
        if composed.get("pending_chapters"):
            return JSONResponse(
                {
                    "ok": False,
                    "message": "存在未确认章节，仅允许草稿预览，阻断 final export。",
                    "error": {
                        "code": "CHAPTER_FORMAL_PENDING",
                        "message": "存在未确认章节，仅允许草稿预览，阻断 final export。",
                        "details": {"pending_chapters": composed.get("pending_chapters")},
                    },
                },
                status_code=409,
            )
        from document_pipeline.rewrite_delivery_audit import RewriteDeliveryAuditService

        RewriteDeliveryAuditService(context).require_clean(composed, delivery_kind="final")
        DocumentPreviewService(context).build()
    except ControlPlaneError as exc:
        return _error(exc)
    report = read_json(context.root / RENDER_QUALITY_PATH) if (context.root / RENDER_QUALITY_PATH).is_file() else {}
    artifact = context.root / RENDER_OUTPUT_PATH
    if report.get("status") != "ready" or not artifact.is_file(): return JSONResponse({"ok": False, "message": "V3 交付门禁未通过：存在未解决校验错误。"}, status_code=409)
    return FileResponse(artifact, filename="final.docx")


@app.get("/api/v3/workspaces/{workspace_id}/exports/word")
def export_current_word(workspace_id: str):
    """Export the current workbench state; confirmation is not required."""
    try:
        from document_pipeline.current_word_export import build_current_word

        context = _context(workspace_id)
        from document_pipeline.chapter_editing import ChapterEditingService
        from document_pipeline.rewrite_delivery_audit import RewriteDeliveryAuditService

        RewriteDeliveryAuditService(context).require_clean(
            ChapterEditingService(context).compose_current_document(),
            delivery_kind="current_word",
        )
        artifact = build_current_word(context)
        return FileResponse(artifact, filename="标书当前稿.docx")
    except ControlPlaneError as exc:
        return _error(exc)
    except RuntimeError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=503)


@app.get("/")
def index() -> HTMLResponse:
    path = VUE_DIST_DIR / "index.html"
    headers = {"Cache-Control": "no-cache, no-store, must-revalidate"}
    return HTMLResponse(
        (
            path.read_text(encoding="utf-8")
            if path.is_file()
            else '<div id="app"><h1>请先构建 frontend</h1></div>'
        ),
        headers=headers,
    )


@app.get("/{path:path}")
def spa(path: str) -> HTMLResponse:
    if path.startswith("api/"):
        return HTMLResponse("<h1>Not Found</h1>", status_code=404)
    return index()
