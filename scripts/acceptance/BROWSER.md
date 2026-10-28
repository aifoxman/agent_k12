# Browser acceptance

Run from the repository root with Node/npm, Playwright CLI Chromium, and a running frontend. Each command uses its own temporary browser session and writes screenshots, API results, console errors, and a timestamped timeline to the chosen output directory.

## Controlled workflow

```powershell
node scripts/acceptance/browser.mjs --mode controlled --base-url http://127.0.0.1:5186/app/ --output output/acceptance/controlled-browser
```

This exercises real React components with simulated camera, model responses, and speech callbacks. It checks tutoring, correction permission, correction expansion, parent review, cancellation, and pause. It cannot establish model quality, voice quality, or physical camera behavior.

## Saved-image teaching with a real model

First start a dedicated acceptance API and frontend and bootstrap a new synthetic family with `serve.py` and `bootstrap.py`. Keep the generated credentials in the run's `private/` directory. The browser runner requires `synthetic: true`, loopback URLs, and a matching `X-K12-Acceptance-Run` health header; it rejects the original service on port 8003.

The current `teaching-continuous` flow checks normal automatic progression across logical steps, explicit Pause with at least three seconds of stationary content, Replay and Slower staying on the current step, Resume to completion, normal/slower speech rates, and Exit hiding both teaching surfaces. The saved-image camera is simulated; model output and speech lifecycle are unmodified. A provider or real browser audio failure remains a failed/blocked run, never a synthesized completion.

Use `--question TEXT` to submit a specific, answer-free question through the normal text input when the saved image contains multiple problems. A legitimate request to clarify the target is not a completed lesson and cannot pass the lesson-playback checks. `--entry replay` reads the isolated family's genuinely saved lesson, makes no new teacher request, and compares the initial archive with the archive after any requested cold reload.

```powershell
node scripts/acceptance/browser.mjs --mode live --flow teaching-continuous --state-file output/acceptance/RUN/private/browser-state.json --paper output/acceptance/fixtures/camera-20261023-060842/source-camera.jpg --output output/acceptance/RUN/browser --cold-review true
```

`teaching-paced` below retains the older mandatory Next/dwell checks for archived builds. It does not describe the current default teaching behavior; `browser-paced-summary.mjs` also summarizes that historical manual-boundary contract.

```powershell
node scripts/acceptance/browser.mjs --mode live --flow teaching-paced --state-file output/acceptance/RUN/private/browser-state.json --paper output/acceptance/fixtures/camera-20261023-060842/source-camera.jpg --output output/acceptance/RUN/browser --dwell-ms 3000 --max-steps 48
node scripts/acceptance/browser-paced-summary.mjs output/acceptance/RUN/browser
```

Only the camera input is replaced with the specified saved image. Real API/model output and browser speech/audio lifecycle events are observed without completion mocks. The tutoring flow disallows grading, parent review, deletion, and microphone transcription. It submits one teacher request, then checks at least three seconds of stationary waiting at each boundary, explicit Next, Replay, Slower during playback, and Exit. Next is scoped to `.teaching-step-controls` to distinguish it from scratch history navigation.

Use `--entry replay` only to inspect previously saved output in that isolated family; this forbids another teacher request and is not a new model evaluation. Use a fresh synthetic family and output directory for a new model run. Preserve failed runs.

`report.json` and `paced-summary.json` describe workflow behavior. Independently review every model statement and the screenshots before issuing a teaching-quality verdict. Client SSE receipt, speech start, DOM observation, and screenshot capture have separate timestamps; none is a word-level audio timestamp. Audio events do not prove human listening, voice quality, or hardware operation. A document reload invalidates the current run rather than silently counting as a product failure.

Run the independent guard checks with `node --test scripts/acceptance/browser-contract.test.mjs`.

## Grading API and parent/child UI

Use separate synthetic families for tutoring, grading API, and grading UI. Never copy the original Pad family or port 8003 credentials. The API runner verifies the run health identity before credentials; the UI runner additionally requires `private/grading-ui-state.json` and limits writes to its session and correction IDs returned by that flow.

After starting the dedicated API and frontend, each prepared flow has a single command:

```powershell
# API: creates its own synthetic family and simulated answer sheet.
uv run python scripts/acceptance/grading_flow.py run --run-dir output/acceptance/RUN --base-url http://127.0.0.1:8017 --frontend-url http://127.0.0.1:5187/app/ --source-camera output/acceptance/fixtures/camera-20261023-060842/source-camera.jpg

# UI: use a fresh grading-ui family prepared as below, not the API family's state.
node scripts/acceptance/grading-ui.mjs --run-dir output/acceptance/RUN --state-file output/acceptance/RUN/private/grading-ui-state.json --paper output/acceptance/RUN/grading/simulated-answers.jpg
```

The API flow covers unauthorized access, parent approval, blank recognition, one correct and one incorrect written answer, independent recheck, parent review history, revocation, pause, visual preflight, renewed approval, and finish. Read `grading/report.json`; a successful Python process alone is not a quality verdict. The existing policy counts confirmed blank written tasks as unfinished/incorrect, while keeping `answer_state=blank`; it must not invent a child's wrong calculation from printed givens.

The default UI flow exercises actual parent/child controls for approval, result expansion, recheck, saving a review, pause, and finish. `--pause-focus` instead tests immediate local hiding before the next dashboard response, paused reload, real visual preflight, and renewed authorization. It briefly delays real dashboard requests to prove local hiding; response bodies are unchanged. Only the camera is simulated. Neither flow installs a voice bridge or completion callbacks. Each UI execution preserves a separate `grading-ui/attempt-*/` directory and writes `grading-ui/latest.json`; prior attempts remain evidence. Use a new family for each execution because the flow finishes its session.

## Run the current flows sequentially

Start a dedicated API in one terminal with `serve.py --output output/acceptance/YOUR_NEW_RUN --port 8017 --frontend-port 5187 --live`. Serve a frozen frontend in another terminal with `npm run build --prefix src/client` then `npm run preview --prefix src/client -- --host 127.0.0.1 --port 5187`. Use the exact same new run name below. These commands invoke the configured provider and incur model usage.

The following single PowerShell script block provisions distinct families and runs paced tutoring, grading API, full grading UI, the pause/reauthorization UI specialization, and task submission/return/confirmation. It leaves all credentials private and stops on workflow failure. Use a new run name instead of overwriting evidence; a tutoring workflow pass still needs independent mathematical and visual review. The exact combined invocation has not been executed as a whole.

```powershell
& {
  $run = 'output/acceptance/YOUR_NEW_RUN'
  $paper = 'output/acceptance/fixtures/camera-20261023-060842/source-camera.jpg'
  $api = 'http://127.0.0.1:8017'
  $frontend = 'http://127.0.0.1:5187/app/'
  if (@("$run/browser", "$run/grading", "$run/grading-ui", "$run/task-ui") | Where-Object { Test-Path -LiteralPath $_ }) {
    throw 'Choose a new run directory; prior evidence must be preserved.'
  }
  New-Item -ItemType Directory -Force -Path "$run/private" | Out-Null
  function New-AcceptanceSyntheticFamily($name, $setupName) {
    uv run python scripts/acceptance/bootstrap.py --base-url $api --frontend-url $frontend --output $run --image $paper --state-name "$name.json"
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic-family visual preflight failed.' }
    Move-Item -LiteralPath "$run/$name.json" -Destination "$run/private/$name.json"
    Move-Item -LiteralPath "$run/setup-result.json" -Destination "$run/$setupName.json"
  }
  New-AcceptanceSyntheticFamily 'browser-state' 'teaching-setup-result'
  node scripts/acceptance/browser.mjs --mode live --flow teaching-continuous --state-file "$run/private/browser-state.json" --paper $paper --output "$run/browser" --dwell-ms 3000 --max-steps 48
  if ($LASTEXITCODE -ne 0) { throw 'Paced tutoring workflow failed; inspect its report.' }
  uv run python scripts/acceptance/grading_flow.py run --run-dir $run --base-url $api --frontend-url $frontend --source-camera $paper
  if ($LASTEXITCODE -ne 0 -or (Get-Content "$run/grading/report.json" -Raw | ConvertFrom-Json).status -ne 'passed') {
    throw 'Grading API has a failure or unresolved check.'
  }
  New-AcceptanceSyntheticFamily 'grading-ui-state' 'grading-ui-full-setup-result'
  node scripts/acceptance/grading-ui.mjs --run-dir $run --state-file "$run/private/grading-ui-state.json" --paper "$run/grading/simulated-answers.jpg"
  if ($LASTEXITCODE -ne 0) { throw 'Grading UI failed; preserve its attempt directory.' }
  Move-Item -LiteralPath "$run/private/grading-ui-state.json" -Destination "$run/private/grading-ui-full-state.json"
  New-AcceptanceSyntheticFamily 'grading-ui-state' 'grading-ui-pause-setup-result'
  node scripts/acceptance/grading-ui.mjs --run-dir $run --state-file "$run/private/grading-ui-state.json" --paper "$run/grading/simulated-answers.jpg" --pause-focus
  if ($LASTEXITCODE -ne 0) { throw 'Pause/reauthorization UI failed.' }
  New-AcceptanceSyntheticFamily 'task-state' 'task-cycle-setup-result'
  node scripts/acceptance/browser.mjs --mode live --flow task-cycle --state-file "$run/private/task-state.json" --paper $paper --output "$run/task-ui"
  if ($LASTEXITCODE -ne 0) { throw 'Task submission/return/confirmation UI failed.' }
  Write-Host 'Flow artifacts saved. Independently review teaching content and source boundaries.'
}
```

The combined block is a documented composition of the existing entry points, not a claim that this exact combined invocation has already passed. `run.py --browser both` remains the older general smoke orchestrator and does not collect these specialized folders into its HTML automatically. Do not publish an entire run directory: it contains private account state and an isolated database.

## Task submission, return, and confirmation

The separate task-cycle runner uses another freshly bootstrapped family. It exercises actual child page capture/submission, parent review of the real completeness advice, a return reason and child-visible follow-up, re-submission of the same saved fixture, parent confirmation, and the resulting child task state:

```powershell
node scripts/acceptance/browser.mjs --mode live --flow task-cycle --state-file output/acceptance/RUN/private/task-state.json --paper output/acceptance/fixtures/camera-20261023-060842/source-camera.jpg --output output/acceptance/RUN/task-ui
```

Prepare `task-state.json` with a new family as in the provisioning example; do not reuse the tutoring or grading session. The guard permits only that family's task IDs/session and rejects tutoring, grading, and parent-review writes. Re-submitting an unchanged saved image tests task transitions and feedback delivery; it does not establish that a child corrected work.

If an execution already submitted and returned the task but stopped on a runner observation error, `--entry resume` requires that same isolated task to be `needs_revision` with an existing submission. It skips the first submission and return, then uses normal UI for the second capture/submission, parent confirmation, and child verification. Use a new output directory and retain the failed first report:

```powershell
node scripts/acceptance/browser.mjs --mode live --flow task-cycle --entry resume --state-file output/acceptance/RUN/private/task-state.json --paper output/acceptance/fixtures/camera-20261023-060842/source-camera.jpg --output output/acceptance/RUN/task-ui-resumed
```

Verify the actual visible return banner and own task state, not the small status text deliberately hidden during immersive camera mode. On resumed entry a notice may already have been delivered; the report explicitly records whether the instruction was visible this time or requires the prior evidence. Confirmation uses the visible confirmed circle, absence of the submission button, and the task's confirmed API state. A split execution pass is not an uninterrupted fresh-family CLI pass. The preserved sixth-run summary is `output/acceptance/complete-sim-20261023-065900/task-cycle-summary.json`.

Page scanning accepts clear, complete electronic assignments, screen-displayed problems, and screenshots according to their content. Blur, occlusion, incomplete problems, or unreliable page boundaries still require a better image; the scanner must not guess boxes. Task subject matching removes explicit K12 grade/stage prefixes and volume suffixes, then compares whole names. Only reliable aliases such as `奥数` → `数学` and `英文` → `英语` are mapped. Custom subjects remain exact names; `数学与语文` is not `数学`. A partially accepted batch says how many different-subject pages were not added; a wholly mismatched batch asks for the right page rather than claiming it was already photographed.

The preserved 8021 task baseline accepted one electronic page but rejected `四年级奥数` for task `数学`, then displayed a false duplicate message with zero collected pages. No submission or later parent steps occurred. The later 8022 run passed first submission and return, then completed the remaining stages with the explicit resume entry above. Its two raw observation-error reports remain failed and unchanged.

## Numeric cues and submitted notes

Original-image circle/underline anchors match complete equivalent Arabic and Chinese numbers, with the actual spoken phrase returned as `anchor_text` for short-clip timing. Non-numeric wording remains strict. A submitted note can retain its full equivalent wording or only a complete arithmetic equation already present in that note and explicitly spoken in this clip. For example, an unspoken title or reason is removed from `检查：2×6＝12` when only the equation is said. The server does not create another equation, infer one from a word problem, or invent a location. Current reliable anchors and worked-solution/no-answer permissions still apply; the rationale remains in scratch.

## Saved fixture and verdict boundaries

`output/acceptance/fixtures/camera-20261023-060842/source.json` records the original 1280×960 Pad capture and its external-screen medium. Later canvas use is **saved-image simulation**, even though the file originated from a real camera. `grading_flow.py prepare` draws a separate sheet labeled as simulated student work; it never edits the saved camera image. Evaluator truth in `grading/manifest.json` stays outside production prompts.

The current consolidated report is `output/acceptance/full-simulation-20261023-063600/report.html`, previewed at `http://127.0.0.1:5196/report.html`. At this update the fifth live-model run had 18 mathematically correct clips and 3 original-image circles, 2 lines, and 3 equations, but its related local diagram was clipped. The complete visual lesson therefore still failed; a sixth run was pending. Keep these findings separate from any later result.

The subsequently completed sixth run is independently reviewed in `output/acceptance/complete-sim-20261023-065900/quality-review.json`: 18 clips, 17 UI stages and 10 required quality checks passed, including complete active diagrams, 2 circles, 1 underline, 2 reliable lines and 4 spoken equation notes across time. One oral phrasing ambiguity and all unassessed human/device outcomes remain explicit. It uses a saved-image camera and real provider output; it does not erase the fifth-run clipping failure or certify a child's understanding.

Keep workflow, mathematical accuracy, original-image alignment, actual sound, and student learning verdicts separate. A response containing lines is insufficient without checking where they land; a complete playback can still contain a false final check. The preserved 8018/static-5197 run has a pacing pass and an independent mathematical failure. Earlier 5195-era baseline and HMR-interrupted attempts do not become successful current runs. Real TTS bytes or playback lifecycle events are not evidence of human listening; these browser flows do not validate a new physical worksheet, Pad hardware, microphone, or learning mastery.
