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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from control_plane import CommandEnvelope, CommandGateway, ControlPlaneError, ControlStore, WorkspaceContext
from document_pipeline.canonicalization import canonical_hash
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
        try:
            from document_pipeline.research_adapters import close_web_sessions

            close_web_sessions()
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
            "chapter_context_hash": str(
                context_record.get("context_hash")
                or canonical_hash(
                    {
                        "chapter_id": chapter_id,
                        "chapter_context_revision": int(
                            context_record.get("context_revision") or 0
                        ),
                        "items": list(context_record.get("items") or []),
                    }
                )
            ),
        }
        try:
            requirements, scoring = _chapter_semantic_requirements(
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
        return JSONResponse(
            {
                "ok": True,
                "chapter_id": str(chapter.get("chapter_id") or chapter_id),
                "title": str(chapter.get("title") or ""),
                "turns": turns,
            }
        )
    except ControlPlaneError as exc:
        return _error(exc)


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
        from document_pipeline.chapter_chat import ChapterChatService
        from document_pipeline.chapter_workspace import ChapterWorkspaceService

        context = _context(workspace_id)
        chapter_service = ChapterWorkspaceService(context)
        chapter = chapter_service.get_chapter(chapter_id)
        try:
            requirements, scoring = _chapter_semantic_requirements(context, chapter)
        except (ControlPlaneError, ValueError):
            requirements, scoring = [], []
        try:
            global_project_context = _chapter_project_context(context)
        except ControlPlaneError:
            global_project_context = None

        try:
            from document_pipeline.sibling_chapter_context import (
                SiblingChapterContextService,
            )

            sibling_context = SiblingChapterContextService(
                context
            ).build_for_chapter(chapter)
        except Exception:
            sibling_context = None
        try:
            from document_pipeline.document_outline_context import (
                DocumentOutlineContextService,
            )

            outline_context = DocumentOutlineContextService(
                context
            ).build_for_chapter(chapter)
        except Exception:
            outline_context = None
        result = ChapterChatService(context).answer(
            chapter_id,
            message,
            chapter=chapter,
            global_project_context=global_project_context,
            tender_requirements=requirements,
            scoring_requirements=scoring,
            sibling_context=sibling_context,
            outline_context=outline_context,
        )
        snapshot_data = V3WorkspaceSnapshotBuilder(context).build()
        return JSONResponse(
            {
                "ok": True,
                "reply": result["reply"],
                "chapter_id": result["chapter_id"],
                "turns": result.get("history_tail") or [],
                "workspace_revision": snapshot_data.get("workspace_revision", 0),
            }
        )
    except ControlPlaneError as exc:
        return _error(exc)


def _ndjson_event(event_type: str, **payload: Any) -> bytes:
    return (
        json.dumps(
            {"type": event_type, **payload},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _chapter_draft_messages(
    chapter: dict[str, Any],
    *,
    instruction: str = "",
    research_sources: list[dict[str, Any]] | None = None,
    project_context: dict[str, Any] | None = None,
    chapter_grounding_context: dict[str, Any] | None = None,
    tender_requirements: list[dict[str, Any]] | None = None,
    scoring_requirements: list[dict[str, Any]] | None = None,
    sibling_context: dict[str, Any] | None = None,
    outline_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    from document_pipeline.content_grounding import chapter_opening_policy
    from document_pipeline.document_outline_context import compact_outline_for_prompt
    from document_pipeline.sibling_chapter_context import _chapter_role

    node = chapter.get("blueprint_node")
    node = node if isinstance(node, dict) else {}
    chapter_context = chapter.get("context")
    chapter_context = chapter_context if isinstance(chapter_context, dict) else {}
    context_items = [
        {
            "kind": str(item.get("kind") or ""),
            "title": str(item.get("title") or ""),
            "body": str(item.get("body") or ""),
            "source": str(item.get("source") or ""),
        }
        for item in (chapter_context.get("items") or [])
        if isinstance(item, dict)
    ]
    sibling_payload = dict(sibling_context or {})
    outline_payload = compact_outline_for_prompt(dict(outline_context or {}))
    title = str(chapter.get("title") or node.get("title") or "")
    purpose = str(node.get("purpose") or "")
    chapter_role = str(
        outline_payload.get("current_role")
        or sibling_payload.get("chapter_role")
        or _chapter_role(title, purpose)
    )
    is_visual = chapter_role == "visual"
    writing_input = {
        "chapter_id": str(chapter.get("chapter_id") or ""),
        "chapter_title": title,
        "purpose": purpose,
        "writing_objectives": list(node.get("writing_objectives") or []),
        "content_format": "technical_roadmap_diagram" if is_visual else "prose",
        "tender_requirements": list(tender_requirements or []),
        "scoring_requirements": list(scoring_requirements or []),
        "chapter_context": context_items,
        "global_project_context": dict(project_context or {}),
        "chapter_grounding_context": dict(chapter_grounding_context or {}),
        # Full outline + bounded related chapter read-only summaries.
        "document_outline_context": outline_payload,
        # Kept for compatibility with older visual policies.
        "sibling_chapter_context": sibling_payload,
        "user_instruction": instruction,
        "verified_public_sources": list(research_sources or []),
        "opening_policy": chapter_opening_policy(chapter),
    }
    structure_rules = ""
    policy = (
        (outline_payload.get("writing_policy") if isinstance(outline_payload, dict) else None)
        or (sibling_payload.get("writing_policy") if isinstance(sibling_payload, dict) else None)
    )
    if isinstance(policy, dict):
        rules = [str(item).strip() for item in (policy.get("rules") or []) if str(item).strip()]
        guidance = str(policy.get("guidance") or "").strip()
        if rules or guidance:
            structure_rules = (
                "目录处境约束："
                + "；".join(rules)
                + (f"。补充说明：{guidance}" if guidance else "")
                + "。"
            )
    if is_visual:
        system = (
            "你是技术标书中的「技术路线图/流程图」撰写器，不是普通论述写作器。"
            "本章 content_format=technical_roadmap_diagram：输出必须以图示结构为主，"
            "禁止写成总体技术路线或关键技术方法的长文复述。"
            "固定输出顺序（不要输出章节标题本身）："
            "1) 一句话图题（说明本图展示什么阶段/节点关系）；"
            "2) 用 Mermaid flowchart 或清晰 ASCII/文本流程图画出阶段、先后/并行、"
            "关键质控节点与主要输入输出（节点命名对齐目录中上游总体技术路线骨架）；"
            "3) 图注不超过 5 条短要点（每条一行，只解释读图，不展开方法细则）。"
            "不得虚构企业资质、业绩、人员、报价或承诺。不要解释写作过程，不要输出 JSON，"
            "不要使用 Markdown 代码围栏包裹全文（Mermaid 代码块本身除外）。"
            "若提供已核验公开资料，仅可补充通用阶段命名或质控节点习惯，不得改写项目事实。"
            "document_outline_context 提供整份目录与他章只读摘要，用于定位本章边界；"
            "不得修改或搬空其他章节主责内容。"
            + structure_rules
        )
    else:
        system = (
            "你是技术标书正文写作器。请直接撰写当前章节的完整中文正文。"
            "内容必须具体、专业、可执行，只使用输入中提供的事实，不得虚构企业资质、"
            "业绩、人员、报价或承诺。不要解释写作过程，不要输出 JSON，不要使用 Markdown"
            "代码围栏，也不要输出章节标题；只输出可直接保存的正文。若提供了“已核验公开资料”，"
            "只能依据其中的原文摘要归纳政策、标准或通用方法；资料不足时使用条件化表述，"
            "不得把公开资料推断成项目或投标人的既有事实。项目背景、任务范围、建设目标、"
            "标记为同类项目资料或行业标准的来源只能支持方法、质量、风险和验收思路，"
            "不得改写当前项目的采购人、范围、任务或成果。"
            "成果和约束必须优先取自 global_project_context 与 chapter_context；尤其是“项目背景/"
            "任务背景”章节，开篇必须先说明本招标项目的具体对象、任务和需求，再补充与其"
            "直接相关的政策、标准或行业依据。禁止用泛化政策介绍替代项目事实。"
            "严格执行输入中的 opening_policy：只有 mode=project_overview 的章节可以用项目概况"
            "开篇；mode=chapter_focus 的章节必须从本章主题直接起笔，禁止重复介绍覆盖区域、"
            "总体任务和成果清单，禁止在多个子章节套用同一段项目总述。"
            "document_outline_context 给出整份目录位置与他章只读摘要：用它判断本章处境与边界，"
            "可做交叉引用，但不得改写或整段复制其他章节主责正文。"
            + structure_rules
        )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(writing_input, ensure_ascii=False),
        },
    ]


def _chapter_research_question(
    chapter: dict[str, Any],
    instruction: str,
    project_context: dict[str, Any] | None = None,
    *,
    sibling_context: dict[str, Any] | None = None,
    tender_requirements: list[dict[str, Any]] | None = None,
    scoring_requirements: list[dict[str, Any]] | None = None,
) -> str:
    """Return model-decided search query from distilled chapter-relevant facts."""
    plan = _chapter_research_plan(
        chapter,
        instruction=instruction,
        project_context=project_context,
        sibling_context=sibling_context,
        tender_requirements=tender_requirements,
        scoring_requirements=scoring_requirements,
    )
    return str(plan.get("search_query") or "")


def _chapter_research_plan(
    chapter: dict[str, Any],
    *,
    instruction: str = "",
    project_context: dict[str, Any] | None = None,
    sibling_context: dict[str, Any] | None = None,
    tender_requirements: list[dict[str, Any]] | None = None,
    scoring_requirements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from document_pipeline.chapter_research_planner import plan_chapter_research

    return plan_chapter_research(
        chapter,
        project_context=project_context,
        sibling_context=sibling_context,
        tender_requirements=tender_requirements,
        scoring_requirements=scoring_requirements,
        instruction=instruction,
    )


def _chapter_project_context(context: WorkspaceContext) -> dict[str, Any]:
    """Load the promoted tender facts available to the chapter writer.

    Public research is supplementary.  The promoted project model is the source
    of truth for what this particular procurement is about.
    """
    from document_pipeline.global_project_context import GlobalProjectContextService

    return GlobalProjectContextService(context).load()


def _chapter_semantic_requirements(
    context: WorkspaceContext,
    chapter: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve chapter IDs to the actual tender and scoring text."""
    from document_pipeline.requirement_ledger import load_promoted_requirement_ledger
    from document_pipeline.score_model import load_promoted_score_model

    node = chapter.get("blueprint_node")
    node = node if isinstance(node, dict) else {}
    requirement_ids = {str(item) for item in node.get("requirement_ids") or []}
    score_ids = {str(item) for item in node.get("score_point_ids") or []}
    condition_ids = {str(item) for item in node.get("score_condition_ids") or []}
    ledger = load_promoted_requirement_ledger(context)
    scores = load_promoted_score_model(context)
    requirements = [
        {
            "requirement_id": item.requirement_id,
            "text": str(item.normalized_requirement or ""),
            "severity": item.severity,
        }
        for item in ledger.requirements
        if item.requirement_id in requirement_ids
    ]
    scoring: list[dict[str, Any]] = []
    for point in scores.points:
        selected_conditions = [
            condition.model_dump(mode="json")
            for condition in point.score_conditions
            if condition.condition_id in condition_ids
        ]
        if point.score_point_id in score_ids or selected_conditions:
            scoring.append({
                "score_point_id": point.score_point_id,
                "title": point.title,
                "response_expectation": point.response_expectation,
                "conditions": selected_conditions,
            })
    return requirements, scoring


def _assert_requested_global_context(
    body: dict[str, Any],
    global_context: dict[str, Any],
) -> None:
    requested_id = str(body.get("global_context_id") or "").strip()
    requested_hash = str(body.get("global_context_hash") or "").strip()
    try:
        requested_revision = int(body.get("global_context_revision"))
    except (TypeError, ValueError) as exc:
        raise ControlPlaneError(
            "GLOBAL_PROJECT_CONTEXT_REQUIRED",
            "章节生成必须携带全局项目上下文版本。",
            status_code=409,
        ) from exc
    if not requested_id or not requested_hash:
        raise ControlPlaneError(
            "GLOBAL_PROJECT_CONTEXT_REQUIRED",
            "章节生成必须携带全局项目上下文标识和哈希。",
            status_code=409,
        )
    expected = (
        str(global_context.get("global_context_id") or ""),
        int(global_context.get("global_context_revision") or 0),
        str(global_context.get("global_context_hash") or ""),
    )
    actual = (requested_id, requested_revision, requested_hash)
    if actual != expected:
        raise ControlPlaneError(
            "GLOBAL_PROJECT_CONTEXT_CONFLICT",
            "全局项目事实已更新，请刷新后重新生成本章。",
            status_code=409,
            details={
                "requested": {
                    "global_context_id": requested_id,
                    "global_context_revision": requested_revision,
                    "global_context_hash": requested_hash,
                },
                "current": {
                    "global_context_id": expected[0],
                    "global_context_revision": expected[1],
                    "global_context_hash": expected[2],
                },
            },
        )


def _assert_requested_chapter_context(
    body: dict[str, Any],
    chapter_context: dict[str, Any],
) -> None:
    requested_id = str(body.get("chapter_context_id") or "").strip()
    requested_hash = str(body.get("chapter_context_hash") or "").strip()
    try:
        requested_revision = int(body.get("chapter_context_revision"))
    except (TypeError, ValueError) as exc:
        raise ControlPlaneError(
            "CHAPTER_CONTEXT_REQUIRED",
            "章节生成必须携带本章上下文版本。",
            status_code=409,
        ) from exc
    expected = (
        str(chapter_context.get("chapter_context_id") or ""),
        int(chapter_context.get("chapter_context_revision") or 0),
        str(chapter_context.get("chapter_context_hash") or ""),
    )
    actual = (requested_id, requested_revision, requested_hash)
    if not requested_id or not requested_hash:
        raise ControlPlaneError(
            "CHAPTER_CONTEXT_REQUIRED",
            "章节生成必须携带本章上下文标识和哈希。",
            status_code=409,
        )
    if actual != expected:
        raise ControlPlaneError(
            "CHAPTER_CONTEXT_CONFLICT",
            "本章上下文已更新，请刷新后重新生成。",
            status_code=409,
            details={"requested": actual, "current": expected},
        )


def _research_anchors(
    global_context: dict[str, Any],
) -> tuple[list[str], list[str]]:
    from document_pipeline.global_project_context import GlobalProjectContextService

    return GlobalProjectContextService.research_anchors(global_context)


def _research_source_rows(batch: Any) -> list[dict[str, Any]]:
    """Project immutable evidence into a small, prompt-safe source list."""
    rows: list[dict[str, Any]] = []
    for item in list(getattr(batch, "items", []) or []):
        url = str(getattr(item, "source_url", "") or "").strip()
        content = str(getattr(item, "content", "") or "").strip()
        if not url or not content:
            continue
        relevance_tier = getattr(item, "relevance_tier", "")
        relevance_tier = getattr(relevance_tier, "value", relevance_tier)
        rows.append({
            "batch_id": str(getattr(batch, "batch_id", "") or ""),
            "evidence_id": str(getattr(item, "evidence_id", "") or ""),
            "title": str(getattr(item, "title", "") or "公开资料"),
            "publisher": str(getattr(item, "publisher", "") or ""),
            "source_url": url,
            "snippet": content[:3000],
            "relevance_tier": str(
                relevance_tier or "general_reference"
            ),
            "matched_project_anchors": list(
                getattr(item, "matched_project_anchors", []) or []
            ),
            "matched_task_anchors": list(
                getattr(item, "matched_task_anchors", []) or []
            ),
            "usage_constraints": list(
                getattr(item, "usage_constraints", []) or []
            ),
        })
    return rows


@app.post(
    "/api/v3/workspaces/{workspace_id}/chapters/{chapter_id}/draft/stream"
)
async def stream_chapter_draft(
    workspace_id: str,
    chapter_id: str,
    request: Request,
) -> StreamingResponse:
    """Stream visible draft text, then commit the complete draft through CommandGateway."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    principal_id = str(_principal(request).get("id") or "")
    instruction = str(body.get("instruction") or "").strip()
    overwrite_locked = bool(body.get("overwrite_locked"))
    idempotency_key = str(body.get("idempotency_key") or "").strip() or (
        f"chapter-draft-stream:{chapter_id}:{uuid.uuid4()}"
    )

    def generate():
        context = _context(workspace_id)
        normalized_chapter_id = str(chapter_id or "").strip()
        try:
            expected_workspace_revision = int(body.get("expected_revision"))
        except (TypeError, ValueError):
            yield _ndjson_event(
                "error",
                chapter_id=normalized_chapter_id,
                code="WORKSPACE_REVISION_INVALID",
                message="expected_revision 必须是整数。",
            )
            return
        try:
            expected_chapter_revision = int(body.get("expected_chapter_revision"))
        except (TypeError, ValueError):
            yield _ndjson_event(
                "error",
                chapter_id=normalized_chapter_id,
                code="CHAPTER_REVISION_INVALID",
                message="expected_chapter_revision 必须是整数。",
            )
            return

        try:
            from document_pipeline.chapter_workspace import ChapterWorkspaceService
            from document_pipeline.contracts import EvidenceNeed
            from document_pipeline.research_adapters import create_research_adapter
            from document_pipeline.research_service import ResearchService
            from llm_client import chat_stream_chunks

            chapter = ChapterWorkspaceService(context).get_chapter(
                normalized_chapter_id
            )
            if chapter.get("is_leaf") is False:
                raise ControlPlaneError(
                    "CHAPTER_BODY_REQUIRES_LEAF",
                    "目录父节点只作为结构标题，不生成正文；请选择其下级叶子章节。",
                    status_code=409,
                )
            if not chapter.get("materialized"):
                raise ControlPlaneError(
                    "CHAPTER_NOT_MATERIALIZED",
                    f"章节 Workspace 尚未创建: {normalized_chapter_id}",
                    status_code=409,
                )
            current_revision = int(chapter.get("chapter_revision") or 0)
            if current_revision != expected_chapter_revision:
                raise ControlPlaneError(
                    "CHAPTER_REVISION_CONFLICT",
                    "章节已被其他操作更新，请刷新后重试。",
                    status_code=409,
                    details={
                        "expected_chapter_revision": expected_chapter_revision,
                        "actual_chapter_revision": current_revision,
                    },
                )

            yield _ndjson_event(
                "meta",
                chapter_id=normalized_chapter_id,
                operation_id=idempotency_key,
                title=str(chapter.get("title") or normalized_chapter_id),
                expected_revision=expected_workspace_revision,
                expected_chapter_revision=expected_chapter_revision,
            )
            research_sources: list[dict[str, Any]] = []
            project_context = _chapter_project_context(context)
            _assert_requested_global_context(body, project_context)
            tender_requirements, scoring_requirements = _chapter_semantic_requirements(
                context, chapter
            )
            from document_pipeline.global_project_context import (
                GlobalProjectContextService,
            )

            chapter_context_record = chapter.get("context")
            chapter_context_record = (
                chapter_context_record
                if isinstance(chapter_context_record, dict)
                else {}
            )
            chapter_grounding_context = GlobalProjectContextService(
                context
            ).build_chapter_context(
                normalized_chapter_id,
                requirement_excerpts=tender_requirements,
                score_obligations=scoring_requirements,
                chapter_context_items=list(
                    chapter_context_record.get("items") or []
                ),
                chapter_context_revision=int(
                    chapter_context_record.get("context_revision") or 0
                ),
                chapter_context_hash=str(
                    chapter_context_record.get("context_hash") or ""
                ),
            )
            _assert_requested_chapter_context(body, chapter_grounding_context)
            prompt_project_context = GlobalProjectContextService.prompt_projection(
                project_context,
                chapter_grounding_context,
            )
            from document_pipeline.sibling_chapter_context import (
                SiblingChapterContextService,
            )

            sibling_context = SiblingChapterContextService(
                context
            ).build_for_chapter(chapter)
            from document_pipeline.document_outline_context import (
                DocumentOutlineContextService,
            )

            outline_context = DocumentOutlineContextService(
                context
            ).build_for_chapter(chapter)
            if (
                sibling_context.get("chapter_role") == "visual"
                and sibling_context.get("missing_upstream")
            ):
                missing = sibling_context["missing_upstream"]
                titles = "、".join(
                    str(item.get("title") or item.get("chapter_id") or "")
                    for item in missing
                )
                yield _ndjson_event(
                    "research",
                    chapter_id=normalized_chapter_id,
                    status="sibling_hint",
                    message=(
                        f"根据整份目录位置，本章依赖上游章节骨架。建议先完成：{titles}。"
                        "将以目录中相关章节只读摘要作为图示骨架继续生成，不展开方法细节。"
                    ),
                    sources=[],
                )

            # Chapter agent plans research from distilled relevant facts only —
            # never paste the full tender / raw project_context dump into search.
            yield _ndjson_event(
                "research",
                chapter_id=normalized_chapter_id,
                status="planning",
                message="章节 Agent 正在整理本章相关要点并判断是否需要公开检索…",
            )
            research_plan = _chapter_research_plan(
                chapter,
                instruction=instruction,
                project_context=prompt_project_context,
                sibling_context=sibling_context,
                tender_requirements=tender_requirements,
                scoring_requirements=scoring_requirements,
            )
            research_question = str(research_plan.get("search_query") or "").strip()
            if not research_plan.get("need_research") or not research_question:
                yield _ndjson_event(
                    "research",
                    chapter_id=normalized_chapter_id,
                    status="skipped",
                    message=(
                        str(research_plan.get("reason") or "").strip()
                        or "本章已有足够要点，跳过公开检索，直接写作。"
                    ),
                    sources=[],
                    decision_source=str(
                        research_plan.get("decision_source") or ""
                    ),
                )
            else:
                brief = research_plan.get("brief") if isinstance(research_plan.get("brief"), dict) else {}
                yield _ndjson_event(
                    "research",
                    chapter_id=normalized_chapter_id,
                    status="searching",
                    message=(
                        "已整理本章相关要点，开始检索："
                        + str(research_plan.get("reason") or "补充公开依据")
                    ),
                    brief={
                        "project_name": brief.get("project_name"),
                        "related_tasks": list(brief.get("related_tasks") or [])[:4],
                        "chapter_title": brief.get("chapter_title"),
                        "focus_keywords": list(brief.get("focus_keywords") or [])[:8],
                    },
                    decision_source=str(
                        research_plan.get("decision_source") or ""
                    ),
                )
                project_anchors, task_anchors = _research_anchors(
                    project_context
                )
                need = EvidenceNeed(
                    need_id="EN-STREAM-" + hashlib.sha256(
                        f"{normalized_chapter_id}:{research_question}".encode("utf-8")
                    ).hexdigest()[:16],
                    question=research_question,
                    topic_id=f"chapter-stream:{normalized_chapter_id}",
                    priority="high",
                    blocking_scope="content_unit",
                    deadline_stage="chapter_draft_stream",
                    query_budget=3,
                    project_anchors=project_anchors,
                    task_anchors=task_anchors,
                    max_adopted_items=3,
                )
                batch = ResearchService(context, create_research_adapter()).resolve(need)
                research_sources = _research_source_rows(batch)
                if batch.status == "failed":
                    yield _ndjson_event(
                        "error",
                        chapter_id=normalized_chapter_id,
                        code="CHAPTER_RESEARCH_UNAVAILABLE",
                        message=(
                            "公开资料检索未完成，已停止本章写作。"
                            "请完成浏览器中的登录或验证后重试。"
                        ),
                        details={
                            "batch_id": str(batch.batch_id or ""),
                            "error": str(
                                batch.error or "研究 Provider 未返回成功结果。"
                            ),
                        },
                    )
                    return
                if batch.status == "gap" or not research_sources:
                    yield _ndjson_event(
                        "research",
                        chapter_id=normalized_chapter_id,
                        status="gap",
                        message=(
                            "未发现满足项目相关性要求的公开资料；"
                            "将以已整理的项目要点与本章上下文继续写作。"
                        ),
                        sources=[],
                    )
                else:
                    yield _ndjson_event(
                        "research",
                        chapter_id=normalized_chapter_id,
                        status="ready",
                        message=f"已找到 {len(research_sources)} 条与本章相关资料，开始核验并写作。",
                        sources=[
                            {
                                key: row[key]
                                for key in (
                                    "evidence_id", "title", "publisher",
                                    "source_url", "relevance_tier",
                                )
                            }
                            for row in research_sources
                        ],
                    )

            text_parts: list[str] = []
            for kind, value in chat_stream_chunks(
                _chapter_draft_messages(
                    chapter,
                    instruction=instruction,
                    research_sources=research_sources,
                    project_context=prompt_project_context,
                    chapter_grounding_context=chapter_grounding_context,
                    tender_requirements=tender_requirements,
                    scoring_requirements=scoring_requirements,
                    sibling_context=sibling_context,
                    outline_context=outline_context,
                ),
                temperature=0.25,
            ):
                # Provider reasoning is intentionally neither persisted nor exposed.
                if kind != "content" or not value:
                    continue
                delta = str(value)
                text_parts.append(delta)
                yield _ndjson_event(
                    "delta",
                    chapter_id=normalized_chapter_id,
                    delta=delta,
                )

            complete_text = "".join(text_parts).strip()
            if not complete_text:
                raise ControlPlaneError(
                    "CHAPTER_DRAFT_EMPTY",
                    "写作模型未返回可保存的正文。",
                    status_code=502,
                )

            from document_pipeline.content_grounding import ContentGroundingGate

            grounding_report = ContentGroundingGate.evaluate(
                global_context=project_context,
                chapter=chapter,
                content=complete_text,
                requirement_texts=[
                    *(
                        str(item.get("text") or "")
                        for item in tender_requirements
                    ),
                    *(
                        str(item.get("response_expectation") or "")
                        for item in scoring_requirements
                    ),
                    *(
                        str(condition.get("text") or "")
                        for item in scoring_requirements
                        for condition in item.get("conditions") or []
                        if isinstance(condition, dict)
                    ),
                ],
                chapter_grounding_context=chapter_grounding_context,
                evidence_sources=research_sources,
                require_evidence_use=bool(research_sources),
            )

            envelope = CommandEnvelope.from_mapping(
                {
                    "kind": "chapter.generate_draft",
                    "payload": {
                        "chapter_id": normalized_chapter_id,
                        "expected_chapter_revision": expected_chapter_revision,
                        "text": complete_text,
                        "overwrite_locked": overwrite_locked,
                        "global_context_id": project_context["global_context_id"],
                        "global_context_revision": project_context[
                            "global_context_revision"
                        ],
                        "global_context_hash": project_context["global_context_hash"],
                        "chapter_context_id": chapter_grounding_context[
                            "chapter_context_id"
                        ],
                        "chapter_context_revision": chapter_grounding_context[
                            "chapter_context_revision"
                        ],
                        "chapter_context_hash": chapter_grounding_context[
                            "chapter_context_hash"
                        ],
                        "evidence_batch_ids": sorted(
                            {
                                str(item.get("batch_id") or "")
                                for item in research_sources
                                if str(item.get("batch_id") or "")
                            }
                        ),
                        "grounding_report": grounding_report,
                    },
                    "actor": {"type": "user", "id": principal_id},
                    "expected_revision": expected_workspace_revision,
                    "idempotency_key": idempotency_key,
                },
                workspace_id=workspace_id,
            )
            receipt = _gateway(context).submit(envelope)
            if receipt.status == "rejected":
                error = receipt.error if isinstance(receipt.error, dict) else {}
                raise ControlPlaneError(
                    str(error.get("code") or "CHAPTER_DRAFT_COMMIT_REJECTED"),
                    str(error.get("message") or receipt.message or "章节草稿保存失败。"),
                    status_code=409,
                    details=(
                        error.get("details")
                        if isinstance(error.get("details"), dict)
                        else {}
                    ),
                )
            result = receipt.result if isinstance(receipt.result, dict) else {}
            yield _ndjson_event(
                "done",
                chapter_id=normalized_chapter_id,
                text=complete_text,
                receipt=receipt.as_dict(),
                chapter=result.get("chapter"),
                content=result.get("content"),
            )
        except ControlPlaneError as exc:
            yield _ndjson_event(
                "error",
                chapter_id=normalized_chapter_id,
                code=exc.code,
                message=exc.message,
                details=exc.details,
            )
        except Exception as exc:
            yield _ndjson_event(
                "error",
                chapter_id=normalized_chapter_id,
                code="CHAPTER_DRAFT_STREAM_FAILED",
                message=str(exc) or "章节正文流式生成失败。",
            )

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


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
    headers = {"Cache-Control": "no-cache, no-store, must-revalidate"}
    return HTMLResponse(
        path.read_text(encoding="utf-8") if path.is_file() else "<h1>请先构建 frontend</h1>",
        headers=headers,
    )


@app.get("/{path:path}")
def spa(path: str) -> HTMLResponse:
    if path.startswith("api/"):
        return HTMLResponse("<h1>Not Found</h1>", status_code=404)
    return index()
