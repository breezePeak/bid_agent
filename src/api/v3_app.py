"""Standalone V3 HTTP application.  It deliberately imports no legacy web modules."""

from __future__ import annotations

import hmac
import hashlib
import json
import os
import re
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from control_plane import CommandEnvelope, CommandGateway, ControlPlaneError, ControlStore, WorkspaceContext
from document_pipeline.contracts import InputRole
from document_pipeline.execution_controller import V3ExecutionController
from document_pipeline.document_preview import DocumentPreviewService
from document_pipeline.input_manifest import InputManifestService, V3_ROOT
from document_pipeline.renderers.render_verifier import RENDER_OUTPUT_PATH, RENDER_QUALITY_PATH
from document_pipeline.source_normalizer import NORMALIZABLE_EXTENSIONS
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


@app.post("/api/v3/workspaces")
async def create_workspace(request: Request) -> JSONResponse:
    try:
        workspace_id = _workspace_id(str((await request.json()).get("name") or "")); root = RUNS_DIR / workspace_id
        (root / "workspace" / "v3").mkdir(parents=True); (root / "outputs" / "v3").mkdir(parents=True)
        ControlStore(_context(workspace_id)).grant_workspace_access(str(_principal(request)["id"]), role="owner")
        return JSONResponse({"ok": True, "workspace": {"id": workspace_id, "name": workspace_id}}, status_code=201)
    except ControlPlaneError as exc: return _error(exc)


@app.get("/api/v3/workspaces")
def list_workspaces(request: Request) -> JSONResponse:
    items = []
    for root in sorted((p for p in RUNS_DIR.glob("*") if (p / "workspace" / "v3").is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
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
        return JSONResponse({"ok": True, "input": registration.item.model_dump(mode="json")}, status_code=201)
    except ControlPlaneError as exc:
        return _error(exc)
    except ValueError as exc:
        return _error(ControlPlaneError("UPLOAD_INVALID", str(exc), status_code=400))
    finally:
        if temporary and temporary.exists(): temporary.unlink()
        await file.close()


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
    context = _context(workspace_id); snapshot_data = V3WorkspaceSnapshotBuilder(context).build()
    history_path = context.root / V3_ROOT / "chat_history.jsonl"; history_path.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if history_path.is_file():
        for line in history_path.read_text(encoding="utf-8").splitlines()[-12:]:
            try: history.append(json.loads(line))
            except json.JSONDecodeError: pass
    prompt = "你是正在编制标书的协作 Agent。用自然、直接的中文回答，不复述问题，不说套话。基于工作区状态给出判断和下一步；不确定就明确缺什么证据。不得把外部信息当企业资质。"
    try:
        from llm_client import chat
        answer = chat([{"role": "system", "content": prompt}, {"role": "user", "content": f"最近对话：{history}\n工作区状态：{snapshot_data}\n\n用户：{message}"}]).strip()
    except Exception:
        document = snapshot_data.get("document") or {}; needs = snapshot_data.get("evidence_needs") or []
        answer = f"当前文档状态：{(document.get('delivery') or {}).get('status', 'new')}。"
        if needs: answer += f" 还缺 {len(needs)} 项证据，先补“{needs[0].get('question', '')}”。"
    with history_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"role": "user", "content": message}, ensure_ascii=False) + "\n")
        stream.write(json.dumps({"role": "assistant", "content": answer}, ensure_ascii=False) + "\n")
    return JSONResponse({"ok": True, "reply": answer, "workspace_revision": snapshot_data.get("workspace_revision", 0)})

@app.get("/api/v3/workspaces/{workspace_id}/snapshot")
def snapshot(workspace_id: str) -> JSONResponse: return JSONResponse({"ok": True, "snapshot": V3WorkspaceSnapshotBuilder(_context(workspace_id)).build()})


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

        chapter = ChapterWorkspaceService(_context(workspace_id)).get_chapter(chapter_id)
        return JSONResponse({"ok": True, "chapter": chapter})
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
        DocumentPreviewService(context).build()
    except ControlPlaneError as exc:
        return _error(exc)
    report = read_json(context.root / RENDER_QUALITY_PATH) if (context.root / RENDER_QUALITY_PATH).is_file() else {}
    artifact = context.root / RENDER_OUTPUT_PATH
    if report.get("status") not in {"ready", "ready_with_warnings"} or not artifact.is_file(): return JSONResponse({"ok": False, "message": "V3 交付门禁未通过。"}, status_code=409)
    return FileResponse(artifact, filename="final.docx")


@app.get("/")
def index() -> HTMLResponse:
    path = VUE_DIST_DIR / "index.html"
    return HTMLResponse(path.read_text(encoding="utf-8") if path.is_file() else "<h1>请先构建 frontend</h1>")


@app.get("/{path:path}")
def spa(path: str) -> HTMLResponse:
    if path.startswith("api/"):
        return HTMLResponse("<h1>Not Found</h1>", status_code=404)
    return index()
