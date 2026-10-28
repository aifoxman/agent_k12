"""ADB/CDP observation contracts without connecting to a real device."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/acceptance/android.py"
SPEC = importlib.util.spec_from_file_location("acceptance_android", MODULE_PATH)
android = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(android)


class FakeCdp:
    def __init__(self):
        self.expressions = []
        self.events = []
        self.closed = False
        self.visible = True

    def evaluate(self, expression):
        self.expressions.append(expression)
        if expression.startswith("JSON.stringify(Object.fromEntries"):
            return json.dumps(
                {"child_token": "private-test-token", "child_api": "http://example.test"}
            )
        if expression == "window.__paperAcceptanceObserverV1.drain()":
            return [
                {
                    "at": "2026-10-23T12:00:00Z",
                    "type": "speech",
                    "text": "原生播报回调：__pcmOutputDone（非真人实听）",
                }
            ]
        if expression == "document.visibilityState":
            return "visible" if self.visible else "hidden"
        if expression == android.DOM_SCRIPT:
            return {"title": "纸间", "text": "本次作业回顾", "buttons": []}
        return {"result": "private-evaluation-result"}

    def call(self, method, params=None):
        return {}

    def close(self):
        self.closed = True


@pytest.fixture
def observer():
    instance = android.AndroidObserver("adb.exe")
    instance.cdp = FakeCdp()
    return instance


def test_connect_targets_current_pid_and_explicit_local_forward(monkeypatch):
    instance = android.AndroidObserver("adb.exe", port=9226)
    commands = []

    def fake_adb(*args, **kwargs):
        commands.append(args)
        return b"31834\n" if args[:2] == ("shell", "pidof") else b""

    instance._adb = fake_adb
    target = {
        "id": "target",
        "type": "page",
        "url": "https://appassets.androidplatform.net/app/index.html?mode=child&token=secret",
        "webSocketDebuggerUrl": "ws://localhost:9999/devtools/page/target",
    }
    monkeypatch.setattr(
        android.httpx,
        "get",
        lambda *args, **kwargs: SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: [target]
        ),
    )
    endpoints = []
    monkeypatch.setattr(
        android, "CdpConnection", lambda endpoint: endpoints.append(endpoint) or FakeCdp()
    )
    result = instance.connect()
    assert commands == [
        ("shell", "pidof", "com.paperbetween.child"),
        ("forward", "tcp:9226", "localabstract:webview_devtools_remote_31834"),
    ]
    assert endpoints == ["ws://127.0.0.1:9226/devtools/page/target"]
    assert "token" not in json.dumps(result) and "secret" not in json.dumps(result)


def test_storage_backup_restore_and_script_results_are_private(observer, tmp_path, capsys):
    backup = observer.backup_storage(tmp_path)
    path = Path(backup["private_backup"])
    assert path.parent == tmp_path / "private"
    assert (
        json.loads(path.read_text(encoding="utf-8"))["storage"]["child_token"]
        == "private-test-token"
    )
    assert "private-test-token" not in json.dumps(backup)
    assert observer.restore_storage(path) == {"restored": True, "reload_required": True}
    assert "location.origin" in observer.cdp.expressions[-1]
    script = tmp_path / "inspect.js"
    script.write_text("({safe:'test'})", encoding="utf-8")
    result = observer.evaluate_file(script, tmp_path)
    assert Path(result["private_result"]).parent == tmp_path / "private"
    assert "private-evaluation-result" not in json.dumps(result)
    assert capsys.readouterr().out == ""


def test_synthetic_state_requires_label_and_backups_before_mutation(observer, tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"storage": {"child_token": "fake"}}), encoding="utf-8")
    with pytest.raises(android.AndroidObservationError, match="source=synthetic"):
        observer.configure_synthetic(state, tmp_path)
    assert not observer.cdp.expressions
    state.write_text(
        json.dumps({"source": "synthetic", "storage": {"child_token": "fake"}}), encoding="utf-8"
    )
    result = observer.configure_synthetic(state, tmp_path)
    assert Path(result["private_backup"]).is_file()
    assert observer.cdp.expressions[0].startswith("JSON.stringify(Object.fromEntries")
    assert "child_acceptance_source" in observer.cdp.expressions[1]
    assert result["source"] == "synthetic"


def test_restore_rejects_other_app_and_non_string_storage(observer, tmp_path):
    path = tmp_path / "backup.json"
    path.write_text(
        json.dumps({"kind": "android-storage-backup", "package": "wrong", "storage": {}}),
        encoding="utf-8",
    )
    with pytest.raises(android.AndroidObservationError, match="does not match"):
        observer.restore_storage(path)
    path.write_text(
        json.dumps(
            {
                "kind": "android-storage-backup",
                "package": android.DEFAULT_PACKAGE,
                "storage": {"token": {"nested": "value"}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(android.AndroidObservationError, match="string entries"):
        observer.restore_storage(path)


def test_watch_records_real_device_frames_and_callback_is_not_human_listening(observer, tmp_path):
    observer._adb = lambda *args, **kwargs: android.PNG_SIGNATURE + b"synthetic-test-png"
    result = observer.watch(
        tmp_path,
        duration=0.01,
        interval=0.1,
        source={"input": "synthetic", "model": "live", "audio": "live"},
    )
    events = [
        json.loads(line)
        for line in (tmp_path / result["timeline"]).read_text(encoding="utf-8").splitlines()
    ]
    assert result["screenshots"] == 1
    assert result["source"]["device"] == "android" and result["source"]["audio"] == "unknown"
    assert not result["native_callbacks_are_human_listening"]
    screenshot = next(event["screenshot"] for event in events if event["type"] == "screenshot")
    assert (tmp_path / screenshot).read_bytes().startswith(android.PNG_SIGNATURE)
    assert any("非真人实听" in event["text"] for event in events)
    assert observer.cdp.expressions[-1] == "window.__paperAcceptanceObserverV1?.stop()"
    assert "localStorage" not in (tmp_path / result["timeline"]).read_text(encoding="utf-8")


def test_background_app_is_not_screenshot(observer, tmp_path):
    observer.cdp.visible = False

    def unexpected(*args, **kwargs):
        raise AssertionError("Must not capture other applications")

    observer._adb = unexpected
    result = observer.watch(tmp_path, duration=0.01, interval=0.1)
    events = [
        json.loads(line)
        for line in (tmp_path / result["timeline"]).read_text(encoding="utf-8").splitlines()
    ]
    assert not any(event["type"] == "screenshot" for event in events)
    assert result["screenshots"] == 0
    assert any("not visible" in event["text"] for event in events)


def test_watch_stop_event_prevents_capture_before_storage_restore(observer, tmp_path):
    stop = Event()
    stop.set()

    def unexpected(*args, **kwargs):
        raise AssertionError("A stopped observer must not capture a frame")

    observer._adb = unexpected
    result = observer.watch(tmp_path, duration=30, interval=2, stop_event=stop)
    assert result["screenshots"] == 0
    assert observer.cdp.expressions[-1] == "window.__paperAcceptanceObserverV1?.stop()"


def test_cdp_skips_notifications_and_never_uses_undeclared_websocket_client(monkeypatch):
    messages = iter(
        [
            json.dumps({"method": "Runtime.exceptionThrown", "params": {}}),
            json.dumps({"id": 1, "result": {"result": {"value": 4}}}),
        ]
    )
    sent, kwargs = [], {}

    def connect(url, **options):
        kwargs.update(options)
        socket = SimpleNamespace(send=sent.append, recv=lambda **kw: next(messages))
        return SimpleNamespace(
            __enter__=lambda: socket, __exit__=lambda *args: kwargs.update(closed=True)
        )

    monkeypatch.setattr(android, "websocket_connect", connect)
    connection = android.CdpConnection("ws://127.0.0.1:9226/devtools/page/test")
    assert connection.evaluate("2+2") == 4
    assert connection.events[0]["method"] == "Runtime.exceptionThrown"
    assert kwargs["proxy"] is None
    assert json.loads(sent[0])["method"] == "Runtime.evaluate"
    connection.close()
    assert kwargs["closed"]


def test_native_callback_hook_delegates_reassignment_null_and_uninstalls():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to evaluate the same observer JavaScript")
    harness = """
      global.window = new EventTarget();
      let calls=[];
      const pcm = function(){};
      window.__pcmOutputDone = pcm;
      window.__speechOutputDone = function(id, ok) { calls.push(['first', id, ok]); };
      const setup=OBSERVER;
      window.__speechOutputDone('one',true);
      const replacement=function(id,ok){calls.push(['second',id,ok]);};
      window.__speechOutputDone=replacement;
      window.__speechOutputDone('two',false);
      window.__speechOutputDone=null;
      if(window.__speechOutputDone!==null)throw Error('null assignment broken');
      window.__speechOutputDone=replacement;
      const pending=window.__paperAcceptanceObserverV1.drain();
      window.__paperAcceptanceObserverV1.stop();
      if(window.__speechOutputDone!==replacement)throw Error('latest callback not restored');
      if(window.__pcmOutputDone!==pcm)throw Error('PCM identity changed');
      if(calls.length!==2 || pending.length!==2)throw Error('callback delegation failed');
      if(!pending.every(event=>event.label.includes('非真人实听')))throw Error('provenance missing');
      process.stdout.write('passed');
    """.replace("OBSERVER", android.OBSERVER_SCRIPT)
    result = subprocess.run(
        [node, "--input-type=module", "-"],
        input=harness,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "passed"


def test_cli_failure_does_not_print_secret_exception(monkeypatch, tmp_path, capsys):
    def fail(self):
        raise RuntimeError("Bearer private-test-token")

    monkeypatch.setattr(android.AndroidObserver, "connect", fail)
    assert android.main(["--run-dir", str(tmp_path), "inspect"]) == 1
    output = capsys.readouterr().out
    assert "private-test-token" not in output and "blocked" in output
