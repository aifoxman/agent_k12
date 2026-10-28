"""Provision synthetic accounts through an explicitly isolated acceptance API."""

from __future__ import annotations

import argparse
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def verify_acceptance_api(client: httpx.Client, run_id: str) -> None:
    """Prove the dedicated run before sending any account credentials."""
    response = client.get("/health")
    response.raise_for_status()
    if not run_id or response.headers.get("X-K12-Acceptance-Run") != run_id:
        raise ValueError("API did not prove ownership of this synthetic run")


def bootstrap(
    base_url: str, frontend_url: str, directory: Path, image: Path, state_name: str = "state.json",
    setup_result_name: str = "setup-result.json",
) -> dict:
    address = urlsplit(base_url)
    if (
        address.scheme not in {"http", "https"}
        or address.hostname not in {"localhost", "127.0.0.1"}
        or address.port == 8003
        or address.username
        or address.password
        or address.query
        or address.fragment
    ):
        raise ValueError("Only a dedicated loopback acceptance API is allowed")
    directory = directory.resolve()
    with httpx.Client(base_url=base_url, timeout=180) as client:
        verify_acceptance_api(client, directory.name)
        if Path(state_name).name != state_name or not state_name.endswith(".json"):
            raise ValueError("state_name must be a JSON filename")
        if Path(setup_result_name).name != setup_result_name or not setup_result_name.endswith(".json"):
            raise ValueError("setup_result_name must be a JSON filename")
        private = directory / state_name
        if private.exists():
            try:
                state = json.loads(private.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state = {}
            if not isinstance(state, dict):
                state = {}
            if (
                state.get("synthetic") is True
                and state.get("run_id") == directory.name
                and state.get("base_url") == base_url
            ):
                headers = {"Authorization": f"Bearer {state.get('child_token', '')}"}
                current = client.get("/v1/families/dashboard", headers=headers)
                active = (current.json().get("session") or {}) if current.is_success else {}
                if active.get("id") == state.get("session_id") and active.get("status") == "active":
                    return {**state, "frontend_url": frontend_url}

        parent = client.post(
            "/v1/auth/register",
            json={
                "email": f"acceptance-{secrets.token_hex(8)}@example.invalid",
                "password": secrets.token_urlsafe(28),
                "child_name": "自动验收合成学生",
                "age_band": "primary",
                "grade": "三年级",
                "textbook": None,
            },
        )
        parent.raise_for_status()
        p = parent.json()
        parent_headers = {"Authorization": f"Bearer {p['token']}"}
        claim = client.post("/v1/devices/claim", json={})
        claim.raise_for_status()
        device = claim.json()
        pair = client.post(
            "/v1/families/pair", json={"code": device["pairing_code"]}, headers=parent_headers
        )
        pair.raise_for_status()
        child = client.post(
            "/v1/devices/status",
            json={
                "device_id": device["device_id"],
                "device_secret": device["device_secret"],
            },
        )
        child.raise_for_status()
        token = child.json()["token"]
        child_headers = {"Authorization": f"Bearer {token}"}
        preferences = client.patch(
            "/v1/families/help-preferences", json={"mode": "off"}, headers=parent_headers
        )
        preferences.raise_for_status()
        tasks = client.post(
            "/v1/tasks/batch",
            json={
                "due_at": int(time.time()) + 86400,
                "timezone_offset_minutes": 480,
                "request_id": "acceptance-" + secrets.token_hex(12),
                "items": [
                    {"title": "合成数学验收作业", "subject": "数学"},
                    {"title": "合成语文验收作业", "subject": "语文"},
                ],
            },
            headers=parent_headers,
        )
        tasks.raise_for_status()
        # A genuine vision preflight must pass; no locally forged setup ticket.
        with image.open("rb") as paper:
            setup = client.post(
                "/v1/setup/check",
                files={"image": (image.name, paper, "image/jpeg")},
                headers=child_headers,
            )
        setup.raise_for_status()
        check = setup.json()
        (directory / setup_result_name).write_text(
            json.dumps(
                {key: value for key, value in check.items() if key != "setup_ticket"},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if not check.get("ready") or not check.get("setup_ticket"):
            raise RuntimeError("Real visual preflight did not accept the synthetic paper")
        session = client.post(
            "/v1/sessions", json={"setup_ticket": check["setup_ticket"]}, headers=child_headers
        )
        session.raise_for_status()
        state = {
            "synthetic": True,
            "run_id": directory.name,
            "base_url": base_url,
            "frontend_url": frontend_url,
            "parent_token": p["token"],
            "child_token": token,
            "family_id": p["family_id"],
            "session_id": session.json()["id"],
            "device_id": device["device_id"],
            "device_secret": device["device_secret"],
            "task_ids": [task["id"] for task in tasks.json()["tasks"]],
        }
        private.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        private.chmod(0o600)
        return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8016")
    parser.add_argument("--frontend-url", default="http://127.0.0.1:5186/app/")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--state-name", default="state.json")
    args = parser.parse_args()
    bootstrap(args.base_url, args.frontend_url, args.output, args.image, args.state_name)
    print(
        json.dumps(
            {
                "state_file": str(args.output / args.state_name),
                "synthetic": True,
                "setup": "live_vision_passed",
            }
        )
    )


if __name__ == "__main__":
    main()
