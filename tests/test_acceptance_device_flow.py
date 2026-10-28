"""Device flow isolation and restoration ordering without accessing a device."""

from __future__ import annotations

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from scripts.acceptance.device_flow import (
    INIT,
    cleanup_device,
    ensure_reverse,
    isolated_port,
    successful_hint_stream,
)


def test_document_start_camera_override_survives_a_late_android_bridge():
    node = shutil.which("node")
    if not node:
        pytest.skip("Device acceptance requires Node.js")
    config = {
        "source": "synthetic",
        "storage": {},
        "api": "http://127.0.0.1:8016",
        "paper": "synthetic",
    }
    source = INIT.replace("CONFIG", json.dumps(config))
    script = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const listeners={},calls=[];
const context={location:{origin:'https://appassets.androidplatform.net'},
 localStorage:{setItem(){}},navigator:{mediaDevices:{getUserMedia:()=>({real:true})}},
 document:{addEventListener:(name,fn)=>listeners[name]=fn,
   createElement:()=>({getContext:()=>({drawImage(){}}),captureStream:()=>({synthetic:true})})},
 Image:class {decode(){return Promise.resolve()}},setTimeout,URL,DOMException};
context.window={addEventListener(){},fetch:()=>Promise.resolve({})};
context.window.top=context.window;
vm.runInNewContext(SOURCE,context);
(async()=>{
 assert.ok(context.window.__deviceAcceptance,'Instrumentation must survive an absent native bridge');
 const stream=await context.navigator.mediaDevices.getUserMedia({video:true});
 assert.equal(stream.synthetic,true,'Never fall back to the real camera for synthetic acceptance');
 const native={cancelListening(){},speak(text,id){assert.equal(this,native);calls.push([text,id])}};
 context.window.PaperVoice=native;
 listeners.DOMContentLoaded();
 assert.equal(context.window.__deviceAcceptance.microphone_disabled,true);
 context.window.PaperVoice.speak('teacher','utterance');
 assert.deepEqual(calls,[['teacher','utterance']],'Delegate speech to the original native bridge');
})().catch(error=>{console.error(error);process.exitCode=1});
""".replace("SOURCE", json.dumps(source))
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_api_port_is_derived_and_existing_reverse_is_preserved():
    assert isolated_port("http://127.0.0.1:8022") == 8022
    assert isolated_port("https://localhost") == 443
    for address in (
        "http://127.0.0.1:8003",
        "http://remote.test:8022",
        "http://localhost:8022/path",
    ):
        with pytest.raises(ValueError):
            isolated_port(address)
    commands = []
    observer = SimpleNamespace(
        _adb=lambda *args: commands.append(args) or b"device tcp:8022 tcp:8022\n"
    )
    assert ensure_reverse(observer, 8022) is False
    assert commands == [("reverse", "--list")]


def test_reverse_conflict_is_never_overwritten():
    observer = SimpleNamespace(_adb=lambda *args: b"device tcp:8022 tcp:9999\n")
    with pytest.raises(RuntimeError, match="already mapped"):
        ensure_reverse(observer, 8022)


def test_restore_only_occurs_after_monitor_join_and_cleanup_always_closes():
    events = []
    cdp = SimpleNamespace(
        call=lambda method, params: events.append(method),
        evaluate=lambda expression: events.append("stop-native"),
    )
    observer = SimpleNamespace(
        _connection=lambda: cdp,
        restore_storage=lambda path: events.append("restore"),
        _adb=lambda *args: events.append("remove-reverse"),
        close=lambda: events.append("close-main"),
    )
    monitor = SimpleNamespace(close=lambda: events.append("close-monitor"))
    stop = SimpleNamespace(set=lambda: events.append("stop"))
    thread = SimpleNamespace(join=lambda **kwargs: events.append("join"), is_alive=lambda: False)
    cleanup_device(observer, monitor, thread, stop, "private/backup.json", "script", 8022, True)
    assert events.index("restore") > events.index("join")
    assert events.index("restore") > events.index("close-monitor")
    assert "Page.removeScriptToEvaluateOnNewDocument" in events
    assert events[-2:] == ["remove-reverse", "close-main"]


def test_running_monitor_blocks_restore_and_does_not_close_its_socket():
    events = []
    observer = SimpleNamespace(
        restore_storage=lambda path: events.append("restore"),
        close=lambda: events.append("close-main"),
    )
    monitor = SimpleNamespace(close=lambda: events.append("close-monitor"))
    thread = SimpleNamespace(join=lambda **kwargs: events.append("join"), is_alive=lambda: True)
    with pytest.raises(RuntimeError, match="was not restored"):
        cleanup_device(
            observer,
            monitor,
            thread,
            SimpleNamespace(set=lambda: None),
            "private/backup.json",
            "script",
            8022,
            False,
        )
    assert "restore" not in events and "close-monitor" not in events
    assert events[-1] == "close-main"


def test_hint_stream_requires_successful_done_and_actual_speech():
    successful = (
        'event: beat\ndata: {"speech":"先看条件。"}\n\n'
        'event: done\ndata: {"located":true,"response":"先看条件。"}\n\n'
    )
    assert successful_hint_stream(successful)
    assert not successful_hint_stream(successful + 'event: error\ndata: {"detail":"failed"}\n\n')
    assert not successful_hint_stream('event: done\ndata: {"located":true,"response":"解释"}\n\n')
    assert not successful_hint_stream(successful.replace('"located":true', '"located":false'))
