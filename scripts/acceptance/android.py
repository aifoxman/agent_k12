"""Explicit Android/CDP acceptance observation; never print storage or JS results.

Commands: inspect, watch, evaluate --script FILE, backup-storage,
restore-storage --backup FILE, configure-synthetic --state FILE, install --apk FILE.
Use --run-dir for artifacts; private storage/JS results stay under private/ and
are excluded from the report. No command reads the student SQLite database.
Native playback callbacks prove callback delivery, not audible speech or human listening.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx
from websockets.sync.client import connect as websocket_connect

DEFAULT_SERIAL = "f864aef1"
DEFAULT_PACKAGE = "com.paperbetween.child"
DEFAULT_ADB = Path(__file__).resolve().parents[2] / ".tools/sdk/platform-tools/adb.exe"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SOURCE = {"device": "android", "input": "unknown", "model": "unknown", "audio": "unknown"}

OBSERVER_SCRIPT = r"""(() => {
  const key = '__paperAcceptanceObserverV1';
  if (window[key]) return {installed: true};
  const state = {events: [], callbacks: {}, listeners: []};
  const push = (event) => {
    state.events.push({at: new Date().toISOString(), ...event});
    if (state.events.length > 500) state.events.shift();
  };
  for (const name of ['paper-speech', 'paper-speech-progress']) {
    const listener = (event) => push({type:'speech', channel:name,
      label:'播报文字，尚未验证实听', text:String(event.detail?.spoken || event.detail?.text || '').slice(0,4000)});
    window.addEventListener(name, listener);
    state.listeners.push([name, listener]);
  }
  // PCM callbacks have an identity-based cleanup contract; leave that property untouched.
  for (const name of ['__speechOutputStart', '__speechOutputDone']) {
    const descriptor = Object.getOwnPropertyDescriptor(window, name);
    if (descriptor && !descriptor.configurable) {
      push({type:'error', text:'原生播报回调属性不可配置，未修改', callback:name});
      continue;
    }
    let assigned = descriptor?.get ? descriptor.get.call(window) : descriptor?.value;
    const wrapper = function(...args) {
      push({type:'speech', channel:name, label:'原生播报回调，非真人实听',
        text:'原生播报回调：' + name + '（非真人实听）',
        playback_id:String(args[0] ?? '').slice(0,80),
        success:typeof args[1] === 'boolean' ? args[1] : null});
      return typeof assigned === 'function' ? Reflect.apply(assigned, this, args) : undefined;
    };
    const getter = () => typeof assigned === 'function' ? wrapper : assigned;
    Object.defineProperty(window, name, {configurable:true,
      enumerable:descriptor?.enumerable ?? true, get:getter,
      set(value) { assigned = value; descriptor?.set?.call(window, value); }});
    state.callbacks[name] = {descriptor, getter, value:() => assigned};
  }
  state.drain = () => state.events.splice(0);
  state.stop = () => {
    for (const [name, listener] of state.listeners) window.removeEventListener(name, listener);
    for (const [name, saved] of Object.entries(state.callbacks)) {
      if (Object.getOwnPropertyDescriptor(window, name)?.get !== saved.getter) continue;
      const current = saved.value();
      if (saved.descriptor?.get || saved.descriptor?.set) {
        Object.defineProperty(window, name, saved.descriptor);
      } else {
        Object.defineProperty(window, name, {configurable:true, writable:true,
          enumerable:saved.descriptor?.enumerable ?? true, value:current});
      }
    }
    delete window[key];
    return {removed:true};
  };
  window[key] = state;
  return {installed:true};
})()"""

DOM_SCRIPT = """(() => ({title:document.title, text:document.body?.innerText?.slice(0,16000) || '',
  headings:[...document.querySelectorAll('h1,h2,h3')].map(el=>el.innerText).slice(0,40),
  buttons:[...document.querySelectorAll('button')].map(el=>({text:el.innerText,
    label:el.getAttribute('aria-label'),disabled:el.disabled})).slice(0,100)}))()"""


class AndroidObservationError(RuntimeError):
    """A safe operational error with no ADB, storage or evaluation payload dump."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def safe_url(value: str) -> str:
    parsed = urlsplit(value)
    mode = parse_qs(parsed.query).get("mode", [])
    query = urlencode({"mode": mode[0]}) if mode and mode[0] in {"child", "parent"} else ""
    host = parsed.hostname or ""
    if parsed.port:
        host += f":{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, query, ""))


def _private_path(run_dir: Path, name: str) -> Path:
    root = run_dir.resolve()
    private = root / "private"
    private.mkdir(parents=True, exist_ok=True)
    if private.resolve().parent != root:
        raise AndroidObservationError("Private artifact directory must stay inside run directory")
    path = private / name
    if path.is_symlink():
        raise AndroidObservationError("Private artifact must not be a symlink")
    return path


def _write_private(path: Path, value: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


class CdpConnection:
    def __init__(self, url: str):
        self.manager = websocket_connect(url, proxy=None, open_timeout=10, max_size=8 * 1024 * 1024)
        self.socket = self.manager.__enter__()
        self.sequence = 0
        self.events: list[dict] = []

    def call(self, method: str, params: dict | None = None) -> dict:
        self.sequence += 1
        identifier = self.sequence
        self.socket.send(json.dumps({"id": identifier, "method": method, "params": params or {}}))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                message = json.loads(
                    self.socket.recv(timeout=max(0.01, deadline - time.monotonic()))
                )
            except TimeoutError as error:
                raise AndroidObservationError("CDP response timed out") from error
            if message.get("id") == identifier:
                if message.get("error"):
                    raise AndroidObservationError(f"CDP command failed: {method}")
                return message.get("result", {})
            if message.get("method"):
                self.events.append(message)
        raise AndroidObservationError("CDP response timed out")

    def evaluate(self, expression: str) -> object:
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        if result.get("exceptionDetails"):
            raise AndroidObservationError("JavaScript evaluation failed; no result was printed")
        return result.get("result", {}).get("value")

    def close(self) -> None:
        self.manager.__exit__(None, None, None)


class AndroidObserver:
    def __init__(
        self,
        adb_path: str | Path = DEFAULT_ADB,
        serial: str = DEFAULT_SERIAL,
        package: str = DEFAULT_PACKAGE,
        port: int = 9226,
    ):
        self.adb_path, self.serial, self.package, self.port = str(adb_path), serial, package, port
        self.cdp: CdpConnection | None = None
        self.target: dict = {}
        self.pid = ""

    def _adb(self, *arguments: str, timeout: float = 20) -> bytes:
        try:
            result = subprocess.run(
                [self.adb_path, "-s", self.serial, *arguments],
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AndroidObservationError("ADB unavailable or timed out") from error
        if result.returncode:
            raise AndroidObservationError(
                f"ADB operation failed ({result.returncode}); output withheld"
            )
        return result.stdout

    def connect(self) -> dict:
        pid = (
            self._adb("shell", "pidof", self.package)
            .decode("ascii", errors="ignore")
            .strip()
            .split()
        )
        if not pid or not pid[0].isdigit():
            raise AndroidObservationError("Target app process is not running")
        self.pid = pid[0]
        self._adb(
            "forward", f"tcp:{self.port}", f"localabstract:webview_devtools_remote_{self.pid}"
        )
        try:
            response = httpx.get(
                f"http://127.0.0.1:{self.port}/json/list", timeout=10, trust_env=False
            )
            response.raise_for_status()
            targets = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise AndroidObservationError("Cannot discover the app's debug WebView") from error
        expected_mode = "child" if self.package.endswith(".child") else "parent"
        target = next(
            (
                item
                for item in targets
                if item.get("type") == "page"
                and item.get("webSocketDebuggerUrl")
                and urlsplit(item.get("url", "")).hostname == "appassets.androidplatform.net"
                and parse_qs(urlsplit(item["url"]).query).get("mode") == [expected_mode]
            ),
            None,
        )
        if target is None:
            raise AndroidObservationError("No matching paper app WebView target")
        self.target = target
        endpoint = urlsplit(target["webSocketDebuggerUrl"])
        # Forward only the discovered path to the explicitly selected local port.
        url = f"ws://127.0.0.1:{self.port}{endpoint.path}"
        self.cdp = CdpConnection(url)
        self.cdp.call("Runtime.enable")
        return self.inspect()

    def inspect(self) -> dict:
        return {
            "serial": self.serial,
            "package": self.package,
            "pid": self.pid,
            "port": self.port,
            "target_id": self.target.get("id"),
            "url": safe_url(self.target.get("url", "")),
        }

    def _connection(self) -> CdpConnection:
        if not self.cdp:
            raise AndroidObservationError("Connect to the selected debug WebView first")
        return self.cdp

    def evaluate_file(self, script: str | Path, run_dir: str | Path) -> dict:
        result = self._connection().evaluate(Path(script).read_text(encoding="utf-8"))
        path = _private_path(Path(run_dir), f"evaluation-{time.time_ns()}.json")
        _write_private(path, result)
        return {"private_result": str(path), "result_printed": False}

    def backup_storage(self, run_dir: str | Path) -> dict:
        storage = self._connection().evaluate(
            "JSON.stringify(Object.fromEntries(Object.keys(localStorage).map(key=>[key,localStorage.getItem(key)])))"
        )
        try:
            entries = json.loads(storage)
        except (ValueError, TypeError) as error:
            raise AndroidObservationError("Storage backup could not be decoded") from error
        path = _private_path(Path(run_dir), f"storage-backup-{time.time_ns()}.json")
        _write_private(
            path,
            {
                "schema_version": 1,
                "kind": "android-storage-backup",
                "origin": "https://appassets.androidplatform.net",
                "package": self.package,
                "storage": entries,
            },
        )
        return {"private_backup": str(path), "backup_created": True}

    def restore_storage(self, backup: str | Path) -> dict:
        data = json.loads(Path(backup).read_text(encoding="utf-8"))
        if data.get("kind") != "android-storage-backup" or data.get("package") != self.package:
            raise AndroidObservationError("Backup does not match the selected app")
        storage = data.get("storage")
        if not isinstance(storage, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in storage.items()
        ):
            raise AndroidObservationError("Backup storage must contain string entries")
        self._connection().evaluate(
            "(() => {if(location.origin!=='https://appassets.androidplatform.net')throw Error('origin');"
            "const entries="
            + json.dumps(storage, ensure_ascii=True)
            + ";localStorage.clear();for(const [key,value] of Object.entries(entries))localStorage.setItem(key,value);"
            "return {restored:true};})()"
        )
        return {"restored": True, "reload_required": True}

    def configure_synthetic(self, state_file: str | Path, run_dir: str | Path) -> dict:
        state = json.loads(Path(state_file).read_text(encoding="utf-8"))
        if state.get("source") != "synthetic" or not isinstance(state.get("storage"), dict):
            raise AndroidObservationError(
                "Test state must explicitly declare source=synthetic and storage"
            )
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in state["storage"].items()
        ):
            raise AndroidObservationError("Synthetic storage must contain string entries")
        backup = self.backup_storage(run_dir)
        mode = "child" if self.package.endswith(".child") else "parent"
        storage = {**state["storage"], f"{mode}_acceptance_source": "synthetic"}
        self._connection().evaluate(
            "(() => {if(location.origin!=='https://appassets.androidplatform.net')throw Error('origin');"
            "localStorage.clear();const entries="
            + json.dumps(storage, ensure_ascii=True)
            + ";for(const [key,value] of Object.entries(entries))localStorage.setItem(key,value);"
            "return {configured:true,source:'synthetic'};})()"
        )
        return {**backup, "configured": True, "source": "synthetic", "reload_required": True}

    def install(self, apk: str | Path) -> dict:
        path = Path(apk).resolve()
        if path.suffix.lower() != ".apk" or not path.is_file():
            raise AndroidObservationError("APK file does not exist")
        self._adb("install", "-r", str(path), timeout=120)
        return {"installed": True, "apk": path.name, "serial": self.serial}

    def watch(
        self,
        run_dir: str | Path,
        duration: float = 30,
        interval: float = 2,
        source: dict | None = None,
        stop_event: Event | None = None,
    ) -> dict:
        if duration <= 0 or interval < 0.1:
            raise AndroidObservationError(
                "Duration must be positive and interval at least 0.1 seconds"
            )
        cdp = self._connection()
        root = Path(run_dir).resolve()
        directory = root / "android"
        directory.mkdir(parents=True, exist_ok=True)
        if directory.resolve().parent != root:
            raise AndroidObservationError("Android artifacts must stay inside run directory")
        capture_id = f"watch-{time.time_ns()}"
        screenshots = directory / capture_id
        screenshots.mkdir()
        timeline = directory / f"{capture_id}.jsonl"
        provenance = {**SOURCE, **(source or {}), "device": "android", "audio": "unknown"}
        started, sequence, captured = time.monotonic(), 0, 0
        cdp.evaluate(OBSERVER_SCRIPT)
        try:
            with timeline.open("w", encoding="utf-8", buffering=1) as stream:

                def emit(event: dict) -> None:
                    stream.write(
                        json.dumps(
                            {"at": utc_now(), "source": provenance, **event}, ensure_ascii=False
                        )
                        + "\n"
                    )

                while time.monotonic() - started < duration and not (
                    stop_event and stop_event.is_set()
                ):
                    tick = time.monotonic()
                    sequence += 1
                    cdp.evaluate(OBSERVER_SCRIPT)
                    for event in cdp.evaluate("window.__paperAcceptanceObserverV1.drain()") or []:
                        emit(event)
                    for notification in cdp.events:
                        if notification.get("method") == "Runtime.exceptionThrown":
                            details = notification.get("params", {}).get("exceptionDetails", {})
                            emit(
                                {
                                    "type": "error",
                                    "text": "页面 JavaScript 异常（不转储任意控制台内容）",
                                    "line": details.get("lineNumber"),
                                    "column": details.get("columnNumber"),
                                }
                            )
                    cdp.events.clear()
                    try:
                        if cdp.evaluate("document.visibilityState") != "visible":
                            raise AndroidObservationError(
                                "Target app is not visible; screenshot omitted"
                            )
                        png = self._adb("exec-out", "screencap", "-p")
                        if not png.startswith(PNG_SIGNATURE):
                            raise AndroidObservationError("Device screenshot is not a PNG")
                        screenshot = screenshots / f"{sequence:05d}.png"
                        screenshot.write_bytes(png)
                        captured += 1
                        emit(
                            {
                                "type": "screenshot",
                                "text": "真实 Android 应用画面",
                                "screenshot": screenshot.relative_to(root).as_posix(),
                            }
                        )
                        dom = cdp.evaluate(DOM_SCRIPT)
                        dom_path = screenshots / f"{sequence:05d}-dom.json"
                        dom_path.write_text(
                            json.dumps(dom, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                        emit(
                            {
                                "type": "action",
                                "text": "页面 DOM 已保存（不采集输入框值或本地存储）",
                                "evidence": [dom_path.relative_to(root).as_posix()],
                            }
                        )
                    except AndroidObservationError as error:
                        emit({"type": "error", "text": str(error)})
                    remaining = min(
                        interval - (time.monotonic() - tick),
                        duration - (time.monotonic() - started),
                    )
                    if remaining > 0:
                        if stop_event:
                            stop_event.wait(remaining)
                        else:
                            time.sleep(remaining)
                for event in cdp.evaluate("window.__paperAcceptanceObserverV1.drain()") or []:
                    emit(event)
        finally:
            try:
                cdp.evaluate("window.__paperAcceptanceObserverV1?.stop()")
            except Exception:
                pass
        return {
            "timeline": timeline.relative_to(root).as_posix(),
            "screenshots": captured,
            "ticks": sequence,
            "source": provenance,
            "native_callbacks_are_human_listening": False,
        }

    def close(self) -> None:
        if self.cdp:
            self.cdp.close()
            self.cdp = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Observe one explicitly selected Android debug app"
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--adb", type=Path, default=DEFAULT_ADB)
    parser.add_argument("--serial", default=DEFAULT_SERIAL)
    parser.add_argument("--package", default=DEFAULT_PACKAGE)
    parser.add_argument("--port", type=int, default=9226)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect")
    watch = commands.add_parser("watch")
    watch.add_argument("--duration", type=float, default=30)
    watch.add_argument("--interval", type=float, default=2)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--script", type=Path, required=True)
    commands.add_parser("backup-storage")
    restore = commands.add_parser("restore-storage")
    restore.add_argument("--backup", type=Path, required=True)
    configure = commands.add_parser("configure-synthetic")
    configure.add_argument("--state", type=Path, required=True)
    install = commands.add_parser("install")
    install.add_argument("--apk", type=Path, required=True)
    args = parser.parse_args(argv)
    observer = AndroidObserver(args.adb, args.serial, args.package, args.port)
    try:
        if args.command == "install":
            result = observer.install(args.apk)
        else:
            observer.connect()
            if args.command == "inspect":
                result = observer.inspect()
            elif args.command == "watch":
                result = observer.watch(args.run_dir, args.duration, args.interval)
            elif args.command == "evaluate":
                result = observer.evaluate_file(args.script, args.run_dir)
            elif args.command == "backup-storage":
                result = observer.backup_storage(args.run_dir)
            elif args.command == "restore-storage":
                result = observer.restore_storage(args.backup)
            else:
                result = observer.configure_synthetic(args.state, args.run_dir)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "detail": "Android operation failed; private payloads withheld.",
                }
            )
        )
        return 1
    finally:
        observer.close()


if __name__ == "__main__":
    raise SystemExit(main())
