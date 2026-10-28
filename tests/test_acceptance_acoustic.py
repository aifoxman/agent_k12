"""Real-microphone recorder cleanup contracts using fake hardware only in tests."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from scripts.acceptance import acoustic


def test_evaluation_requests_user_gesture_and_private_recording_transfer(tmp_path):
    calls = []
    audio = b"real-recorder-fixture"

    def call(method, params):
        calls.append((method, params))
        value = base64.b64encode(audio).decode() if ".chunk(" in params["expression"] else None
        return {"result": {"value": value}}

    observer = SimpleNamespace(_connection=lambda: SimpleNamespace(call=call))
    metadata = {
        "id": "acoustic-test",
        "status": "stopped",
        "bytes": len(audio),
        "mime": "audio/webm",
    }
    result = acoustic._save(observer, tmp_path, "acoustic-test", metadata)
    assert result["file"] == "private/acoustic-test.webm"
    assert (tmp_path / result["file"]).read_bytes() == audio
    assert (
        json.loads((tmp_path / "private/acoustic-test.json").read_text())["human_audio_listening"]
        is False
    )
    acoustic._evaluate(observer, "test", gesture=True)
    assert calls[-1][1]["userGesture"] is True


def test_host_exception_stops_own_microphone_and_never_touches_native_voice(tmp_path, monkeypatch):
    expressions = []

    def evaluate(observer, expression, **kwargs):
        expressions.append(expression)
        if expression.startswith(acoustic.START_SCRIPT):
            identifier = expression.split('("acoustic-')[-1].split('"')[0]
            return {"id": "acoustic-" + identifier, "status": "recording"}
        if "snapshot()" in expression:
            raise RuntimeError("connection-lost")
        return None

    monkeypatch.setattr(acoustic, "_evaluate", evaluate)
    with pytest.raises(RuntimeError):
        acoustic.start_capture(object(), tmp_path)
    assert "host-cleanup" in expressions[-1]
    assert "?.id ===" in expressions[-1]
    assert not any("PaperVoice" in value or "localStorage" in value for value in expressions)
    report = next((tmp_path / "private").glob("*.json"))
    assert json.loads(report.read_text())["status"] == "blocked"


def test_node_recorder_stops_tracks_on_stop_timeout_failure_and_late_permission():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to execute recorder lifecycle tests")
    script = r"""
const vm = require('vm');
const assert = require('node:assert/strict');
const source = SOURCE;
function setup({failure=false,pending=false}={}) {
  const timers = new Map(); let timerId=0,stops=0,resolvePermission;
  const track = new EventTarget();
  Object.assign(track,{kind:'audio',enabled:true,muted:false,readyState:'live',
    getSettings:()=>({echoCancellation:false,sampleRate:48000}),stop:()=>{stops++;}});
  const stream={getTracks:()=>[track],getAudioTracks:()=>[track]};
  class Recorder extends EventTarget {
    static isTypeSupported(){return true;}
    constructor(){super();if(failure)throw new Error('unsupported');this.state='inactive';this.mimeType='audio/webm';}
    start(){this.state='recording';}
    stop(){this.state='inactive';const event=new Event('dataavailable');event.data=new Blob(['sound']);
      this.dispatchEvent(event);this.dispatchEvent(new Event('stop'));}
  }
  const window={addEventListener(){},removeEventListener(){}};window.top=window;
  const context=vm.createContext({window,location:{origin:'https://appassets.androidplatform.net'},
    document:{visibilityState:'visible'},navigator:{mediaDevices:{getUserMedia:()=>pending ?
      new Promise(resolve=>{resolvePermission=resolve;}) : Promise.resolve(stream)}},
    MediaRecorder:Recorder,Blob,Uint8Array,Date,Error,Number,Promise,String,
    btoa:value=>Buffer.from(value,'binary').toString('base64'),
    setTimeout:(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});return id;},
    clearTimeout:id=>timers.delete(id)});
  return {window,timers,start:()=>vm.runInContext(source,context)('acoustic-test',180),
    stops:()=>stops,grant:()=>resolvePermission(stream)};
}
(async()=>{
  const normal=setup();await normal.start();assert.equal(normal.window.__paperAcousticRecorderV1.status,'recording');
  const state=normal.window.__paperAcousticRecorderV1;
  const stopped=await state.stop();assert.equal(stopped.status,'stopped');assert.ok(normal.stops()>0);
  assert.equal(stopped.human_audio_listening,false);assert.equal(normal.timers.size,0);
  await state.stop();assert.equal(state.reason,'explicit-stop');state.dispose();
  assert.equal(normal.window.__paperAcousticRecorderV1,undefined);
  const timeout=setup();await timeout.start();
  const deadline=[...timeout.timers.values()].find(timer=>timer.delay===180000);
  assert.ok(deadline);deadline.fn();await timeout.window.__paperAcousticRecorderV1.done;
  assert.ok(timeout.stops()>0);assert.equal(timeout.window.__paperAcousticRecorderV1.reason,'hard-timeout');
  const failed=setup({failure:true});await assert.rejects(failed.start());
  assert.ok(failed.stops()>0);assert.equal(failed.window.__paperAcousticRecorderV1,undefined);
  const late=setup({pending:true});const starting=late.start();
  const permission=[...late.timers.values()].find(timer=>timer.delay===12000);
  permission.fn();await assert.rejects(starting);late.grant();await Promise.resolve();await Promise.resolve();
  assert.ok(late.stops()>0);assert.equal(late.window.__paperAcousticRecorderV1,undefined);
})().catch(error=>{console.error(error);process.exitCode=1;});
""".replace("SOURCE", json.dumps(acoustic.START_SCRIPT))
    result = subprocess.run(
        [node, "-"], input=script, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert result.returncode == 0, result.stderr
