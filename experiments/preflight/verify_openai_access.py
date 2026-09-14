#!/usr/bin/env python3
"""Verify configured OpenAI model access without making a billed generation call."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import urllib.error
import urllib.request

from dotenv import dotenv_values
import yaml

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "experiments" / "validation" / "openai-access.json"


def main() -> int:
    """Query the model catalog and record only secret-free access metadata."""
    plan = yaml.safe_load((ROOT / "experiments" / "pp" / "pilot" / "plan.yml").read_text(encoding="utf-8"))
    model = str(plan["fixed_runtime"]["model"])
    key = str(dotenv_values(ROOT / "config" / ".env").get("OPENAI_API_KEY") or "")
    report: dict[str, object] = {
        "schema_version": 1,
        "checked_at": datetime.now(UTC).astimezone().isoformat(),
        "endpoint": "GET /v1/models",
        "generation_calls_made": False,
        "key_present": bool(key),
        "requested_model": model,
        "model_available": False,
    }
    if not key:
        report["error"] = "OPENAI_API_KEY is absent"
    else:
        request = urllib.request.Request(  # noqa: S310
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                payload = json.load(response)
            model_ids = {str(item.get("id")) for item in payload.get("data", []) if isinstance(item, dict)}
            report["http_status"] = 200
            report["model_available"] = model in model_ids
            report["available_model_count"] = len(model_ids)
        except urllib.error.HTTPError as error:
            report["http_status"] = error.code
            report["error"] = "OpenAI model catalog request failed"
        except (OSError, ValueError) as error:
            report["error"] = f"{type(error).__name__}: model catalog request failed"
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"key_present={report['key_present']} model={model} "
        f"available={report['model_available']} generation_calls_made=False",
    )
    return 0 if report["model_available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
