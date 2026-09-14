"""Durable request admission and action-timeout evidence, without prompt contents."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload) + "\n")
    temporary.replace(path)


def begin(config, request):
    if not config.get("experiment", {}).get("request_audit"):
        return None
    path = Path(config["log"]["output_dir"]) / "request_audit" / f"{uuid4().hex}.json"
    save(path, {"status": "pending", "request": str(request), "started_at": datetime.now(timezone.utc).isoformat()})
    return path


def finish(path, error=None):
    if path is not None:
        payload = json.loads(path.read_text())
        payload.update(status="failed" if error else "complete", error_type=type(error).__name__ if error else None)
        save(path, payload)


def timed_out(config, request):
    path = begin(config, request)
    if path is not None:
        payload = json.loads(path.read_text())
        payload.update(status="action_timeout")
        save(path, payload)
