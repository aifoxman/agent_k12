"""Preview only the report and audit-listed public evidence on 127.0.0.1.

No directory listing or general static-file service. Credentials, databases,
private directories and aliases resolving to them remain inaccessible even if
an audit accidentally lists them. The allowlist is refreshed for each request.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


def _private_part(part: str) -> bool:
    value = part.casefold()
    return (
        value in {"private", "_private"}
        or value.startswith(".env")
        or ".sqlite" in value
        or (
            value.endswith(".json")
            and (value.startswith("state") or value.endswith(("-state.json", "_state.json")))
        )
    )


def safe_file(directory: Path, value: object) -> Path | None:
    """Resolve a public file while checking both requested and resolved names."""
    if not isinstance(value, str) or not value or any(char in value for char in "\\:\x00"):
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {".", ".."} or _private_part(part) for part in path.parts):
        return None
    try:
        root = directory.resolve()
        target = (root / Path(*path.parts)).resolve(strict=True)
        relative = target.relative_to(root)
        if not target.is_file() or any(_private_part(part) for part in relative.parts):
            return None
        return target
    except (OSError, RuntimeError, ValueError):
        return None


def public_paths(directory: Path) -> dict[str, Path]:
    """Expose just report/audit plus checks.evidence and timeline.screenshot."""
    public = {}
    candidates: list[object] = ["report.html", "audit.json"]
    audit = safe_file(directory, "audit.json")
    if audit:
        try:
            document = json.loads(audit.read_text(encoding="utf-8"))
            rounds = document.get("rounds", []) if isinstance(document, dict) else []
            for round_ in rounds if isinstance(rounds, list) else []:
                if not isinstance(round_, dict):
                    continue
                for check in round_.get("checks", []):
                    if isinstance(check, dict) and isinstance(check.get("evidence"), list):
                        candidates.extend(check["evidence"])
                for event in round_.get("timeline", []):
                    if isinstance(event, dict):
                        candidates.append(event.get("screenshot"))
        except (OSError, UnicodeError, ValueError, TypeError):
            pass
    for value in candidates:
        target = safe_file(directory, value)
        if target:
            public[PurePosixPath(value).as_posix()] = target
            # Report links use resolved names; retain the same safe-file policy.
            public[target.relative_to(directory.resolve()).as_posix()] = target
    return public


def make_handler(directory: Path) -> type[BaseHTTPRequestHandler]:
    class PreviewHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            # Request URLs may contain private query strings. Do not log them.
            pass

        def do_GET(self):
            self._serve(False)

        def do_HEAD(self):
            self._serve(True)

        def _serve(self, head_only: bool):
            expected = {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }
            if self.headers.get("Host") not in expected:
                self.send_error(404)
                return
            try:
                name = unquote(urlsplit(self.path).path, errors="strict").removeprefix("/")
            except (UnicodeError, ValueError):
                self.send_error(404)
                return
            target = public_paths(directory).get(name)
            # Check again immediately before opening, including a moved symlink.
            if not target or safe_file(directory, name) != target:
                self.send_error(404)
                return
            try:
                with target.open("rb") as content:
                    content_type = (
                        mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                    )
                    if target.suffix.lower() in {".html", ".htm", ".svg"} and name != "report.html":
                        content_type = "text/plain; charset=utf-8"
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(target.stat().st_size))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header(
                        "Content-Security-Policy",
                        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; media-src 'self'; base-uri 'none'; frame-ancestors 'none'",
                    )
                    self.end_headers()
                    if not head_only:
                        shutil.copyfileobj(content, self.wfile)
            except (OSError, ValueError):
                self.send_error(404)

    return PreviewHandler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--port", type=int, default=5191)
    args = parser.parse_args(argv)
    directory = args.output.resolve()
    allowed = (Path(__file__).resolve().parents[2] / "output/acceptance").resolve()
    if (
        directory == allowed
        or not directory.is_relative_to(allowed)
        or not safe_file(directory, "audit.json")
    ):
        parser.error("Output must be an existing acceptance run with audit.json")
    with ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(directory)) as server:
        print(f"Acceptance preview: http://127.0.0.1:{server.server_port}/report.html", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
