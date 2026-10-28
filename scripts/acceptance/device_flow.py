"""Run synthetic-paper tutoring and grading on an actual Android debug app.

Native speech playback is delegated unchanged. Microphone capture is disabled.
Always stop screenshots before restoring the original app's local storage.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.acceptance.android import AndroidObserver  # noqa: E402
from scripts.acceptance.bootstrap import bootstrap  # noqa: E402

INIT = r"""(() => {
  if(window.top!==window || location.origin!=='https://appassets.androidplatform.net')return;
  const config = CONFIG;
  if(config.source!=='synthetic')throw Error('Explicit synthetic source required');
  for(const [key,value] of Object.entries(config.storage)) localStorage.setItem(key,value);
  window.__deviceAcceptance={speech:[],requests:[],responses:[],done:false,camera_requests:0};
  const audit=window.__deviceAcceptance;
  const disableMicrophone = () => {
    const native=window.PaperVoice;
    if(!native) {setTimeout(disableMicrophone,10);return;}
    native.cancelListening?.();
    const bridge={hasMicrophonePermission:()=>false};
    for(const name of ['speak','stop','startPcm','pushPcm','finishPcm','stopPcm'])
      if(typeof native[name]==='function')bridge[name]=(...args)=>native[name](...args);
    for(const name of ['listen','listenHold','listenReply','stopListening','cancelListening'])
      bridge[name]=()=>{};
    window.PaperVoice=bridge;
    audit.microphone_disabled=window.PaperVoice.hasMicrophonePermission()===false;
  };
  document.addEventListener('DOMContentLoaded',disableMicrophone,{once:true});
  window.addEventListener('paper-speech',e=>audit.speech.push(e.detail));
  navigator.mediaDevices.getUserMedia=async constraints=>{
    if(!constraints.video) throw new DOMException('Microphone not exercised','NotAllowedError');
    audit.camera_requests++;
    const image=new Image();image.src=config.paper;await image.decode();
    const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;
    canvas.getContext('2d').drawImage(image,0,0);return canvas.captureStream(5);
  };
  const original=window.fetch.bind(window);
  window.fetch=async(input,init={})=>{
    const url=new URL(typeof input==='string'?input:input.url,location.href);
    if(url.pathname.startsWith('/v1/') && url.origin!==config.api) throw Error('Outside isolated API');
    audit.requests.push({path:url.pathname,method:init.method||'GET'});
    const response=await original(input,init);
    if(url.pathname.includes('/hints/stream')) {
      response.clone().text().then(text=>{audit.hint_stream=text;audit.done=true;});
    } else if(url.pathname.includes('/corrections') && (init.method||'GET')==='POST') {
      response.clone().json().then(body=>audit.responses.push({path:url.pathname,status:response.status,body}));
    }
    return response;
  };
})()"""


def isolated_port(base_url: str) -> int:
    address = urlsplit(base_url)
    if (
        address.scheme not in {"http", "https"}
        or address.hostname not in {"127.0.0.1", "localhost"}
        or address.username
        or address.password
        or address.path not in {"", "/"}
        or address.query
        or address.fragment
    ):
        raise ValueError("A dedicated loopback API origin is required")
    port = address.port or (443 if address.scheme == "https" else 80)
    if port == 8003:
        raise ValueError("The family API port cannot be used for synthetic acceptance")
    return port


def ensure_reverse(observer, port: int) -> bool:
    endpoint = f"tcp:{port}"
    for line in observer._adb("reverse", "--list").decode("utf-8", errors="replace").splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[-2] == endpoint:
            if fields[-1] != endpoint:
                raise RuntimeError("Requested reverse port is already mapped elsewhere")
            return False
    observer._adb("reverse", endpoint, endpoint)
    return True


def successful_hint_stream(value: str) -> bool:
    done, speech = None, False
    for frame in value.replace("\r\n", "\n").split("\n\n"):
        lines = frame.splitlines()
        event = next((line[6:].strip() for line in lines if line.startswith("event:")), "")
        raw = next((line[5:].strip() for line in lines if line.startswith("data:")), "")
        if event == "error":
            return False
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if event in {"beat", "sentence"} and (data.get("speech") or data.get("text")):
            speech = True
        if event == "done":
            done = data
    return bool(done and done.get("located") is not False and done.get("response") and speech)


def cleanup_device(
    observer, monitor, thread, stop, backup, identifier, reverse_port, reverse_added
):
    """Join observation before exposing restored family data; always close connections."""
    stop.set()
    try:
        if thread:
            thread.join(timeout=30)
            if thread.is_alive():
                raise RuntimeError("Observer did not stop; original storage was not restored")
        monitor.close()
        if backup:
            cdp = observer._connection()
            if identifier:
                cdp.call("Page.removeScriptToEvaluateOnNewDocument", {"identifier": identifier})
            cdp.evaluate("window.PaperVoice?.stop?.();window.PaperVoice?.stopPcm?.();true")
            observer.restore_storage(backup)
            cdp.call("Page.reload", {"ignoreCache": True})
    finally:
        # Do not close an in-flight monitoring socket from another thread.
        if not thread or not thread.is_alive():
            monitor.close()
        if reverse_added:
            try:
                observer._adb("reverse", "--remove", f"tcp:{reverse_port}")
            finally:
                observer.close()
        else:
            observer.close()


def wait(cdp, expression: str, timeout: float = 150):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = cdp.evaluate(expression)
        if value:
            return value
        time.sleep(0.5)
    raise RuntimeError("Device UI condition timed out")


def click(cdp, label: str):
    return cdp.evaluate(
        """(() => {const b=[...document.querySelectorAll('button')].find(
      el=>el.textContent.trim()===LABEL&&!el.disabled);if(!b)return false;
      const r=b.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
      if(!b.contains(hit))throw Error('Button obstructed');b.click();return true;})()""".replace(
            "LABEL", json.dumps(label)
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apk", type=Path)
    parser.add_argument("--serial", default="f864aef1")
    parser.add_argument("--base-url", default="http://127.0.0.1:8016")
    args = parser.parse_args()
    args.base_url = args.base_url.rstrip("/")
    api_port = isolated_port(args.base_url)
    directory = args.output.resolve()
    allowed = (ROOT / "output/acceptance").resolve()
    if directory == allowed or not directory.is_relative_to(allowed):
        parser.error("output must be a run directory under output/acceptance")
    paper = directory / "model/papers/arithmetic_wrong.jpg"
    state = bootstrap(
        args.base_url, "http://127.0.0.1:5186/app/", directory, paper, "android-state.json"
    )
    observer = AndroidObserver(serial=args.serial)
    monitor = AndroidObserver(serial=args.serial, port=9227)
    stop = threading.Event()
    thread = None
    backup = None
    identifier = None
    reverse_added = False
    checks = []
    android = directory / "android"
    android.mkdir(exist_ok=True)

    def check(id_, label, passed, detail=""):
        checks.append(
            {
                "id": id_,
                "label": label,
                "status": "passed" if passed else "failed",
                "required": True,
                "detail": detail,
            }
        )
        (android / "report.json").write_text(
            json.dumps({"checks": checks}, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    try:
        observer.connect()
        backup = observer.backup_storage(directory)["private_backup"]
        if args.apk:
            observer.close()
            observer.install(args.apk)
            observer._adb(
                "shell",
                "am",
                "start",
                "-n",
                "com.paperbetween.child/com.paperbetween.app.MainActivity",
            )
            time.sleep(2)
            observer.connect()
        reverse_added = ensure_reverse(observer, api_port)
        cdp = observer._connection()
        config = {
            "source": "synthetic",
            "api": args.base_url,
            "paper": "data:image/jpeg;base64," + base64.b64encode(paper.read_bytes()).decode(),
            "storage": {
                "child_api": args.base_url,
                "child_token": state["child_token"],
                "child_device_id": state["device_id"],
                "child_device_secret": state["device_secret"],
                "child_acceptance_source": "synthetic",
            },
        }
        observer.restore_storage(backup)
        cdp.evaluate("localStorage.clear();true")
        cdp.call("Page.enable")
        identifier = cdp.call(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": INIT.replace("CONFIG", json.dumps(config))},
        )["identifier"]
        cdp.call("Page.reload", {"ignoreCache": True})
        wait(cdp, "Boolean(document.querySelector('video')?.videoWidth)", 30)
        wait(
            cdp,
            "window.__deviceAcceptance?.camera_requests>0 && window.__deviceAcceptance?.microphone_disabled",
            10,
        )
        monitor.connect()
        thread = threading.Thread(
            target=monitor.watch,
            kwargs={
                "run_dir": directory,
                "duration": 240,
                "interval": 2,
                "source": {"input": "synthetic", "model": "live"},
                "stop_event": stop,
            },
        )
        thread.start()
        check("camera", "平板真实界面加载合成纸面", True)
        wait(
            cdp,
            "[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='求一步提示'&&!b.disabled)",
            30,
        )
        check("hint-click", "求助按钮可达", click(cdp, "求一步提示"))
        wait(cdp, "window.__deviceAcceptance?.done", 180)
        stream = cdp.evaluate("window.__deviceAcceptance.hint_stream || ''")
        if not successful_hint_stream(stream):
            raise RuntimeError("Tutoring stream did not complete successfully")
        wait(cdp, "Boolean(document.querySelector('.hint-paper-return'))", 30)
        check("hint", "真实模型辅导在平板呈现", True)
        time.sleep(12)  # Capture actual native playback and visual progression.
        wait(
            cdp,
            "[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='返回实时画面')",
            20,
        )
        click(cdp, "返回实时画面")
        with httpx.Client(base_url=args.base_url, timeout=20) as api:
            approval = api.patch(
                f"/v1/sessions/{state['session_id']}/correction-access",
                json={"allowed": True},
                headers={"Authorization": "Bearer " + state["parent_token"]},
            )
            approval.raise_for_status()
        time.sleep(3)
        check(
            "correction-click", "平板打开批改拍摄", click(cdp, "批改作业") or click(cdp, "申请批改")
        )
        wait(
            cdp,
            "[...document.querySelectorAll('button')].some(b=>b.textContent.includes('确认批改这张')&&!b.disabled)",
            90,
        )
        click(cdp, "确认批改这张")
        wait(cdp, "window.__deviceAcceptance.responses.length>0", 150)
        observer.evaluate_file(
            _save_script(directory, "JSON.stringify(window.__deviceAcceptance)"), directory
        )
        response = cdp.evaluate("window.__deviceAcceptance.responses.at(-1)")
        # Only this synthetic response is public evidence.
        (android / "correction.json").write_text(
            json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        check("correction", "真实模型批改返回平板", response["status"] == 200)
        time.sleep(5)
    except Exception as error:
        try:
            cdp = observer._connection()
            diagnostic = cdp.evaluate("""(() => {
              const synthetic=localStorage.getItem('child_acceptance_source')==='synthetic';
              return {synthetic,init_applied:Boolean(window.__deviceAcceptance),
                text:synthetic ? document.body?.innerText : undefined,
                errors:synthetic ? window.__deviceAcceptance : undefined};})()""")
            (directory / "private/device-diagnostic.json").write_text(
                json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass
        check("flow", "平板自动流程", False, type(error).__name__ + "（未打印私密错误载荷）")
    finally:
        cleanup_device(observer, monitor, thread, stop, backup, identifier, api_port, reverse_added)
        if backup:
            check("restore", "恢复平板原有家庭配置", True)
    events = []
    for timeline in android.glob("*.jsonl"):
        for line in timeline.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    starts = {
        event.get("playback_id")
        for event in events
        if event.get("channel") == "__speechOutputStart"
    }
    finishes = {
        event.get("playback_id") for event in events if event.get("channel") == "__speechOutputDone"
    }
    check(
        "native-tts",
        "原生播报开始与结束回调",
        bool(starts & finishes),
        "只证明原生回调交付；音色、外放可听性和真人收音仍未验收。",
    )
    print(
        json.dumps(
            {
                "report": str(android / "report.json"),
                "checks": len(checks),
                "failed": sum(item["status"] == "failed" for item in checks),
                "human_audio_listening": False,
            }
        )
    )
    raise SystemExit(int(any(item["status"] == "failed" for item in checks)))


def _save_script(directory: Path, source: str) -> Path:
    path = directory / "private/device-result.js"
    path.write_text(source, encoding="utf-8")
    return path


if __name__ == "__main__":
    main()
