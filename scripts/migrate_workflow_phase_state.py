from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.workspace_snapshot import V3WorkspaceSnapshotBuilder  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate persisted operation workflow phase fields.")
    parser.add_argument("--runs-root", type=Path, default=ROOT / "runs")
    args = parser.parse_args()
    runs_root = args.runs_root.resolve()
    results: list[dict[str, object]] = []
    for database in sorted(runs_root.glob("*/workspace/control.db")):
        workspace_id = database.parent.parent.name
        store = ControlStore(WorkspaceContext.resolve(runs_root, workspace_id))
        states = store.workflow_phase_states()
        if states["materials"]["phase_status"] == "not_started":
            input_manifest = store.v3_active_artifact("InputManifest")
            if input_manifest is not None:
                store.record_migrated_phase_state(
                    "materials",
                    "completed",
                    message="Migrated from the promoted InputManifest authority.",
                    occurred_at=str(input_manifest.get("created_at") or "") or None,
                )
                states = store.workflow_phase_states()
        snapshot = V3WorkspaceSnapshotBuilder(store.context).build()
        results.append({
            "workspace_id": workspace_id,
            "phase_states": states,
            "workflow": {
                "phase": (snapshot.get("workflow") or {}).get("phase"),
                "status": (snapshot.get("workflow") or {}).get("status"),
            },
            "planning": {
                "status": (snapshot.get("planning") or {}).get("status"),
                "warnings": (snapshot.get("planning") or {}).get("warnings", []),
            },
        })
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
