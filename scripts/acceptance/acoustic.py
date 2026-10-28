"""Bounded physical Android microphone capture; no playback, ASR or model calls.

start stays running until stop or the 180-second deadline and writes only private
artifacts. Run stop in another terminal. A recording is not human listening and
may include room sounds; neither callbacks nor TTS files are substituted for it.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.acceptance.android import (  # noqa: E402
    DEFAULT_ADB,
    DEFAULT_PACKAGE,
    DEFAULT_SERIAL,
    AndroidObservationError,
    AndroidObserver,
)

KEY = "__paperAcousticRecorderV1"
MAX_SECONDS = 180
START_SCRIPT = r"""(async (id, maxSeconds) => {
  const key = '__paperAcousticRecorderV1';
  if (window.top !== window || location.origin !== 'https://appassets.androidplatform.net' ||
      document.visibilityState !== 'visible') throw new Error('Capture requires the visible app origin');
  if (window[key]) throw new Error('A capture already exists; stop and save it first');
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder !== 'function')
    throw new Error('Physical microphone recording unavailable');
  const limit = Math.min(180, Math.max(1, Number(maxSeconds) || 180));
  const state = {id, status:'starting', startedAt:null, endedAt:null, mime:null,
    stream:null, recorder:null, chunks:[], bytes:0, stopped:false, timeline:[],
    settings:[], requested:{echoCancellation:false,noiseSuppression:false,autoGainControl:false}};
  const emit = (type, detail={}) => state.timeline.push({at:new Date().toISOString(),type,...detail});
  let finish;
  state.done = new Promise(resolve => {finish=resolve;});
  state.snapshot = () => ({id:state.id,status:state.status,mime:state.mime,bytes:state.bytes,
    started_at:state.startedAt,ended_at:state.endedAt,max_seconds:limit,reason:state.reason || null,
    requested:state.requested,settings:state.settings,timeline:state.timeline,
    physical_microphone:true,environment_recording:true,human_audio_listening:false});
  const release = () => {
    for (const track of state.stream?.getTracks() || []) { try {track.stop();} catch {} }
    clearTimeout(state.deadline);
    clearTimeout(state.permissionTimer);
    window.removeEventListener('pagehide', state.onPageHide);
  };
  const complete = () => {
    release();
    if (state.status === 'stopped' || state.status === 'blocked') return;
    state.status = state.bytes ? 'stopped' : 'blocked';
    state.endedAt = new Date().toISOString();
    emit('capture-stopped',{reason:state.reason || 'recorder-stop',bytes:state.bytes});
    state.blob = new Blob(state.chunks,{type:state.mime || 'application/octet-stream'});
    finish(state.snapshot());
  };
  state.stop = async (reason='explicit-stop') => {
    if (!state.stopped) {
      state.stopped = true; state.reason = reason;
      emit('stop-requested',{reason});
      state.permissionReject?.(new Error('Capture stopped while requesting microphone'));
      try {
        if (state.recorder && state.recorder.state !== 'inactive') state.recorder.stop();
        else complete();
      } finally {release();}
    }
    return state.done;
  };
  state.chunk = async (offset,size) => {
    if (!state.blob || state.status !== 'stopped') throw new Error('Recording not ready');
    const bytes = new Uint8Array(await state.blob.slice(offset,offset + Math.min(size,131072)).arrayBuffer());
    let binary=''; for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary);
  };
  state.dispose = () => {release(); if (window[key] === state) delete window[key];};
  state.onPageHide = () => {state.stop('page-hidden-or-unloaded');};
  window[key] = state;
  window.addEventListener('pagehide',state.onPageHide,{once:true});
  state.deadline = setTimeout(() => {state.stop('hard-timeout');},limit * 1000);
  emit('microphone-requested');
  try {
    let rejected;
    const permissionFailure = new Promise((_, reject) => {rejected=reject;});
    state.permissionReject = rejected;
    state.permissionTimer = setTimeout(() => {rejected(new Error('Microphone permission timed out'));},12000);
    const pending = navigator.mediaDevices.getUserMedia({audio:state.requested,video:false});
    pending.then(stream => {if (state.stopped) for (const track of stream.getTracks()) track.stop();},() => {});
    state.stream = await Promise.race([pending,permissionFailure]);
    clearTimeout(state.permissionTimer);
    if (state.stopped) {release(); throw new Error('Capture stopped while requesting microphone');}
    state.settings = state.stream.getAudioTracks().map(track => ({
      kind:track.kind,enabled:track.enabled,muted:track.muted,
      readyState:track.readyState,settings:track.getSettings()}));
    const mime = ['audio/webm;codecs=opus','audio/webm','audio/mp4']
      .find(value => MediaRecorder.isTypeSupported(value));
    if (!mime) throw new Error('No supported audio recording format');
    state.recorder = new MediaRecorder(state.stream,{mimeType:mime,audioBitsPerSecond:64000});
    state.mime = state.recorder.mimeType || mime;
    state.recorder.addEventListener('dataavailable',event => {
      if (event.data?.size) {state.chunks.push(event.data);state.bytes += event.data.size;}
    });
    state.recorder.addEventListener('stop',complete,{once:true});
    state.recorder.addEventListener('error',() => {state.stop('recorder-error');},{once:true});
    for (const track of state.stream.getTracks()) {
      track.addEventListener('mute',() => emit('microphone-muted'));
      track.addEventListener('unmute',() => emit('microphone-unmuted'));
      track.addEventListener('ended',() => {state.stop('microphone-ended');},{once:true});
    }
    state.recorder.start(1000);
    state.status = 'recording'; state.startedAt = new Date().toISOString();
    emit('recording-started',{mime:state.mime});
    return state.snapshot();
  } catch (error) {
    await state.stop('start-blocked');
    state.errorType = error?.name || 'Error';
    state.dispose();
    throw new Error('Physical microphone capture blocked: ' + state.errorType);
  }
})"""


def _evaluate(observer: AndroidObserver, expression: str, *, gesture: bool = False):
    result = observer._connection().call(
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
            "userGesture": gesture,
        },
    )
    if result.get("exceptionDetails"):
        raise AndroidObservationError("Physical microphone evaluation blocked; details withheld")
    return result.get("result", {}).get("value")


def _private(directory: Path, name: str) -> Path:
    root = directory.resolve()
    private = root / "private"
    private.mkdir(parents=True, exist_ok=True)
    if private.resolve().parent != root:
        raise ValueError("Private directory must stay inside the run")
    path = private / name
    if path.is_symlink():
        raise ValueError("Private recording must not be a symlink")
    return path


def _write_json(path: Path, value: dict):
    pending = path.with_suffix(".tmp")
    with os.fdopen(
        os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8"
    ) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    pending.replace(path)


def stop_capture(observer: AndroidObserver) -> dict:
    value = _evaluate(
        observer, f"window.{KEY} ? window.{KEY}.stop('explicit-stop') : null", gesture=True
    )
    if not isinstance(value, dict):
        raise AndroidObservationError("No acoustic recording exists in this page")
    return {
        "status": "stopped"
        if value.get("status") == "stopped" and value.get("bytes")
        else "blocked",
        "capture_id": value["id"],
        "bytes": value.get("bytes", 0),
        "human_audio_listening": False,
    }


def _save(observer: AndroidObserver, directory: Path, identifier: str, metadata: dict) -> dict:
    if (
        metadata.get("id") != identifier
        or metadata.get("status") != "stopped"
        or not metadata.get("bytes")
    ):
        raise AndroidObservationError("No complete physical microphone recording was available")
    suffix = ".m4a" if metadata.get("mime", "").startswith("audio/mp4") else ".webm"
    audio = _private(directory, identifier + suffix)
    with os.fdopen(os.open(audio, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        for offset in range(0, metadata["bytes"], 131072):
            value = _evaluate(observer, f"window.{KEY}.chunk({offset},131072)")
            stream.write(base64.b64decode(value, validate=True))
    if audio.stat().st_size != metadata["bytes"]:
        raise AndroidObservationError("Physical recording transfer was incomplete")
    metadata.update(
        status="captured-unreviewed",
        file=audio.relative_to(directory.resolve()).as_posix(),
        source={"device": "android", "audio": "live", "input": "unknown", "model": "none"},
        human_audio_listening=False,
        transcript_status="not-requested",
    )
    _write_json(_private(directory, identifier + ".json"), metadata)
    _evaluate(observer, f"window.{KEY}?.dispose()")
    return {
        "status": "captured-unreviewed",
        "capture_id": identifier,
        "bytes": metadata["bytes"],
        "file": metadata["file"],
        "human_audio_listening": False,
    }


def start_capture(
    observer: AndroidObserver,
    directory: Path,
    *,
    max_seconds: float = MAX_SECONDS,
    ready=None,
    poll_seconds: float = 1.0,
) -> dict:
    if not 1 <= max_seconds <= MAX_SECONDS:
        raise ValueError("Recording duration must be between 1 and 180 seconds")
    identifier = "acoustic-" + uuid.uuid4().hex
    report = _private(directory, identifier + ".json")
    started = time.monotonic()
    try:
        initial = _evaluate(
            observer,
            START_SCRIPT + "(" + json.dumps(identifier) + "," + str(max_seconds) + ")",
            gesture=True,
        )
        if not isinstance(initial, dict) or initial.get("status") != "recording":
            raise AndroidObservationError("Physical microphone did not start")
        _write_json(report, initial)
        if ready:
            ready(
                {
                    "status": "recording",
                    "capture_id": identifier,
                    "max_seconds": max_seconds,
                    "human_audio_listening": False,
                    "environment_recording": True,
                }
            )
        metadata = initial
        while time.monotonic() - started < max_seconds:
            metadata = _evaluate(observer, f"window.{KEY}?.snapshot()")
            if not isinstance(metadata, dict) or metadata.get("id") != identifier:
                raise AndroidObservationError("Page changed during physical recording")
            if metadata.get("status") != "recording":
                break
            time.sleep(max(0, min(poll_seconds, max_seconds - (time.monotonic() - started))))
        if metadata.get("status") == "recording":
            metadata = _evaluate(observer, f"window.{KEY}.stop('host-hard-timeout')", gesture=True)
        return _save(observer, directory, identifier, metadata)
    except BaseException as error:
        _write_json(
            report,
            {
                "id": identifier,
                "status": "blocked",
                "error_type": type(error).__name__,
                "human_audio_listening": False,
                "environment_recording": True,
                "completed_at": datetime.now(UTC).isoformat(),
            },
        )
        raise
    finally:
        try:
            _evaluate(
                observer,
                f"window.{KEY}?.id === {json.dumps(identifier)} ? window.{KEY}.stop('host-cleanup') : null",
                gesture=True,
            )
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--adb", type=Path, default=DEFAULT_ADB)
    parser.add_argument("--serial", default=DEFAULT_SERIAL)
    parser.add_argument("--package", default=DEFAULT_PACKAGE)
    parser.add_argument("--port", type=int, default=9226)
    parser.add_argument("action", choices=["start", "stop"])
    args = parser.parse_args(argv)
    directory = args.run_dir.resolve()
    allowed = (ROOT / "output/acceptance").resolve()
    if directory == allowed or not directory.is_relative_to(allowed):
        parser.error("Recording run must be inside output/acceptance")
    observer = AndroidObserver(args.adb, args.serial, args.package, args.port)
    try:
        observer.connect()
        result = (
            start_capture(
                observer, directory, ready=lambda value: print(json.dumps(value), flush=True)
            )
            if args.action == "start"
            else stop_capture(observer)
        )
        print(json.dumps(result), flush=True)
        return 1 if result.get("status") == "blocked" else 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_type": type(error).__name__,
                    "detail": "Physical microphone recording unavailable; private details withheld.",
                }
            ),
            flush=True,
        )
        return 1
    finally:
        observer.close()


if __name__ == "__main__":
    raise SystemExit(main())
