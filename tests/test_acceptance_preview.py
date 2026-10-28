"""The preview never becomes a general run-directory static file server."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx

from scripts.acceptance.preview import make_handler, public_paths, safe_file


def test_public_list_only_contains_report_and_explicit_safe_evidence(tmp_path):
    for name in (
        "report.html",
        "notes.txt",
        "unlisted.txt",
        "state.json",
        "android-state.json",
        ".env",
        "data.sqlite-wal",
    ):
        (tmp_path / name).write_text("test", encoding="utf-8")
    (tmp_path / "private").mkdir()
    (tmp_path / "private/backup.json").write_text("secret", encoding="utf-8")
    (tmp_path / "shot.png").write_bytes(b"image")
    (tmp_path / "audit.json").write_text(
        json.dumps(
            {
                "rounds": [
                    {
                        "checks": [
                            {
                                "evidence": [
                                    "notes.txt",
                                    "state.json",
                                    "android-state.json",
                                    ".env",
                                    "data.sqlite-wal",
                                    "private/backup.json",
                                    "../outside.txt",
                                    "C:/secret.txt",
                                    "https://example.test/a",
                                    "private\\backup.json",
                                ]
                            }
                        ],
                        "timeline": [{"screenshot": "shot.png"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert set(public_paths(tmp_path)) == {"report.html", "audit.json", "notes.txt", "shot.png"}
    assert safe_file(tmp_path, "private/backup.json") is None


def test_resolved_aliases_cannot_publish_private_files_or_escape_run(tmp_path, monkeypatch):
    (tmp_path / "private").mkdir()
    secret = tmp_path / "private/backup.json"
    secret.write_text("secret", encoding="utf-8")
    outside = tmp_path.parent / "outside-secret.json"
    outside.write_text("secret", encoding="utf-8")
    # Exercise resolution without requiring Windows symlink privileges.
    original = Path.resolve
    aliases = {tmp_path / "alias.json": secret, tmp_path / "outside.json": outside}
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda path, *args, **kwargs: aliases.get(path) or original(path, *args, **kwargs),
    )
    assert safe_file(tmp_path, "alias.json") is None
    assert safe_file(tmp_path, "outside.json") is None


def test_http_reloads_allowlist_and_denies_unlisted_encoded_paths_and_directories(tmp_path):
    (tmp_path / "report.html").write_text("report", encoding="utf-8")
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio/speech.wav").write_bytes(b"RIFFtest")
    (tmp_path / "state.json").write_text("secret", encoding="utf-8")
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"rounds": []}), encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            assert client.get("/report.html").status_code == 200
            assert client.get("/audit.json").status_code == 200
            for path in (
                "/",
                "/audio/",
                "/audio/speech.wav",
                "/state.json",
                "/%73tate.json",
                "/%2e%2e/state.json",
            ):
                assert client.get(path).status_code == 404
            assert client.get("/report.html", headers={"Host": "untrusted.test"}).status_code == 404
            audit.write_text(
                json.dumps({"rounds": [{"checks": [{"evidence": ["audio/speech.wav"]}]}]}),
                encoding="utf-8",
            )
            response = client.get("/audio/speech.wav")
            assert response.status_code == 200
            assert response.content == b"RIFFtest"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert client.head("/audio/speech.wav").status_code == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
