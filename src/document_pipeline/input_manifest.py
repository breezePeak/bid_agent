from __future__ import annotations

import hashlib
import mimetypes
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from control_plane import WorkspaceContext
from utils import read_json, write_json

from .contracts import ChangeSet, InputItem, InputManifest, InputRole


V3_ROOT = Path("workspace/v3")
MANIFEST_PATH = V3_ROOT / "input_manifest.json"
CHANGESET_PATH = V3_ROOT / "change_sets"


@dataclass(frozen=True)
class InputRegistration:
    manifest: InputManifest
    item: InputItem
    change_set: ChangeSet | None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_filename(filename: str) -> str:
    value = Path(filename).name.strip()
    if not value or value in {".", ".."}:
        raise ValueError("输入文件名无效")
    return value


class InputManifestService:
    """Role-explicit, append-only input registration for one workspace."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context
        self.root = context.root

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_PATH

    def load(self) -> InputManifest:
        if not self.manifest_path.exists():
            return InputManifest(revision=1)
        return InputManifest.model_validate(read_json(self.manifest_path))

    def register_local_file(
        self,
        source_path: Path,
        role: InputRole,
        *,
        replaces_input_id: str | None = None,
        issued_at: str | None = None,
        supersedes_input_ids: list[str] | None = None,
    ) -> InputRegistration:
        """Copy a local input into immutable V3 storage and record its role/version."""
        source_path = source_path.resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"输入文件不存在: {source_path}")
        filename = _safe_filename(source_path.name)
        manifest = self.load()
        old_item = next((item for item in manifest.inputs if item.input_id == replaces_input_id), None)
        if replaces_input_id and old_item is None:
            raise ValueError(f"不存在的替换目标: {replaces_input_id}")
        if old_item and old_item.role is not role:
            raise ValueError("替换文件必须保持相同输入角色")
        supersedes = [str(item).strip() for item in (supersedes_input_ids or [])]
        if any(not item for item in supersedes):
            raise ValueError("supersedes_input_ids 不能包含空值")
        known_ids = {item.input_id for item in manifest.inputs}
        if unknown := set(supersedes) - known_ids:
            raise ValueError(f"补遗替代目标不存在: {sorted(unknown)}")
        if role is InputRole.AMENDMENT and not issued_at:
            raise ValueError("补遗文件必须提供 issued_at")

        input_id = uuid4().hex
        version = max((item.version for item in manifest.inputs if item.role is role), default=0) + 1
        destination = self.root / V3_ROOT / "sources" / input_id / filename
        destination.parent.mkdir(parents=True, exist_ok=False)
        shutil.copy2(source_path, destination)
        destination.chmod(stat.S_IREAD)
        item = InputItem(
            input_id=input_id,
            role=role,
            filename=filename,
            mime_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
            sha256=_sha256(destination),
            version=version,
            replaces_input_id=replaces_input_id,
            issued_at=issued_at,
            supersedes_input_ids=supersedes,
        )
        inputs = list(manifest.inputs)
        change_set: ChangeSet | None = None
        if old_item:
            inputs = [old.model_copy(update={"active": False}) if old.input_id == old_item.input_id else old for old in inputs]
            change_set = self._replacement_change_set(old_item, item, revision=manifest.revision + 1)
        inputs.append(item)
        next_manifest = InputManifest(
            revision=manifest.revision + 1,
            source_hashes={entry.input_id: entry.sha256 for entry in inputs if entry.active},
            inputs=inputs,
        )
        write_json(self.manifest_path, next_manifest.model_dump(mode="json"))
        if change_set:
            change_path = self.root / CHANGESET_PATH / f"{change_set.change_id}.json"
            change_path.parent.mkdir(parents=True, exist_ok=True)
            write_json(change_path, change_set.model_dump(mode="json"))
        return InputRegistration(manifest=next_manifest, item=item, change_set=change_set)

    @staticmethod
    def _replacement_change_set(previous: InputItem, current: InputItem, *, revision: int) -> ChangeSet:
        if current.role in {InputRole.TENDER, InputRole.SCORE}:
            nodes = ["*"]
            units = ["*"]
        elif current.role is InputRole.TEMPLATE:
            nodes = ["*"]
            units = ["*"]
        elif current.role is InputRole.COMPANY:
            nodes = []
            units = []
        else:
            nodes = []
            units = []
        return ChangeSet(
            revision=revision,
            source="input.replace",
            change_id=uuid4().hex,
            changed_inputs=[previous.input_id, current.input_id],
            affected_contract_nodes=nodes,
            affected_content_units=units,
        )
