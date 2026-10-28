// Executed by Playwright CLI. This file intentionally has no imports or secrets.
export async function runBrowserAcceptance(page, config, writeAllowed) {
  const now = Math.floor(Date.now() / 1000);
  const controlled = config.mode === "controlled";
  const report = {
    schema_version: 1,
    mode: config.mode,
    started_at: new Date().toISOString(),
    isolated_api_identity_verified:
      controlled || config.api_identity_verified === true,
    acceptance_run_id: config.acceptance_run_id || null,
    evidence: {
      frontend: "real React application",
      api: controlled ? "controlled route fixtures" : "isolated live API",
      model: controlled
        ? "fixture; no model call"
        : "configured live provider; output retained without expected-answer matching",
      camera: "synthetic paper canvas stream",
      microphone: "not exercised",
      speech: controlled
        ? "simulated native bridge callbacks"
        : "browser playback lifecycle; no human or agent audio listening",
      stream_timing:
        "client-observed cloned response consumption; not server emission time",
    },
    stages: [],
    screenshots: [],
    timeline: [],
    console: [],
    page_errors: [],
    network_failures: [],
    safety_blocks: [],
    limitations: [
      "Controlled output does not establish teacher quality.",
      "Synthetic camera and speech callbacks do not validate Pad hardware, microphone, accent recognition, or real TTS sound.",
    ],
  };
  const fixture = {
    allowed: false,
    requested: false,
    paused: false,
    finished: false,
    corrections: [],
    hintCalls: 0,
    correctionCalls: 0,
    recheckCalls: 0,
    sessionId: config.session_id || "acceptance-session",
  };
  const timer = {
    learning_seconds: 45,
    focus_seconds: 35,
    assisted_seconds: 10,
    grading_wait_seconds: 0,
    paused_seconds: 0,
    by_subject: [{ subject: "数学", seconds: 45 }],
    last_seq: 0,
    goal_seconds: 2400,
  };
  const check = {
    question: "第1题",
    status: "check_again",
    answer_state: "written",
    answer: "4",
    box: [0.19, 0.3, 0.2, 0.07],
    evidence: "受控示例：清楚写着3+2=4。",
    reason: "受控示例：请重新数一数两个部分。",
    next_step: "可以先数3个，再接着数2个。",
    confidence: 0.96,
    reference_answer: "5",
    solution_idea: "把两个部分合起来。",
    solution_steps: ["先数3个。", "再接着数2个。"],
    comparison: "受控示例作答为4，建议重新点数。",
    steps: [],
  };
  const task = {
    id: "acceptance-task",
    title: "受控样例数学作业",
    subject: "数学",
    status: "doing",
    version: 1,
    created_at: now - 60,
    due_at: now + 86400,
  };
  const dashboard = () => ({
    server_now: Math.floor(Date.now() / 1000),
    family: {
      id: "acceptance-family",
      child_name: "合成测试同学",
      age_band: "primary",
      grade: "三年级",
      textbook: null,
      proactive_help_mode: "off",
      audio_transcription_allowed: 0,
      premium_voice_enabled: 0,
      premium_voice_used_chars: 0,
      premium_voice_trial_chars: 20000,
    },
    tasks: [task],
    task_reminders: [],
    expiring_tasks: [],
    archived_unconfirmed_count: 0,
    session: fixture.finished
      ? null
      : {
          id: fixture.sessionId,
          status: fixture.paused ? "paused" : "active",
          started_at: now - 45,
          timer_last_seq: timer.last_seq,
          timer_goal_seconds: 2400,
          proactive_help_enabled: 0,
          correction_requested_at: fixture.requested ? now : null,
          correction_authorized_until: fixture.allowed ? now + 600 : null,
        },
    timer,
    timer_suggestion: { goal_seconds: 2400, by_subject: [] },
    hints: [],
    corrections: fixture.corrections,
    observations: [],
    reflections: [],
    live: null,
    ai_usage: null,
    page_preparation: null,
    tts_available: false,
    asr_available: false,
  });
  const paperSvg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="960"><rect width="1280" height="960" fill="#fffdf4"/><g fill="#20382b" font-family="sans-serif"><text x="150" y="130" font-size="42">Synthetic acceptance paper</text><text x="180" y="310" font-size="46">1. 3 + 2 = 4</text><text x="180" y="440" font-size="30">Explain the two parts in your own words.</text></g></svg>';
  const apiPath = (url) => {
    const parsed = new URL(url);
    const offset = parsed.pathname.indexOf("/v1/");
    return offset < 0 ? parsed.pathname : parsed.pathname.slice(offset);
  };
  await page.context().route("**/v1/**", async (route) => {
    const request = route.request();
    const path = apiPath(request.url());
    if (!writeAllowed(request.method(), path)) {
      report.safety_blocks.push({ method: request.method(), path });
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "Acceptance guard blocked this action.",
        }),
      });
      return;
    }
    if (!controlled) {
      if (new URL(request.url()).origin !== new URL(config.base_url).origin) {
        report.safety_blocks.push({
          method: request.method(),
          path,
          reason: "outside isolated API",
        });
        await route.abort("blockedbyclient");
      } else await route.continue();
      return;
    }
    let body = {};
    if (path === "/v1/families/dashboard") body = dashboard();
    else if (path === "/v1/ice") body = { iceServers: [] };
    else if (path === "/v1/realtime/ticket")
      body = { ticket: "controlled-ticket" };
    else if (path.endsWith("/timer/events")) {
      timer.last_seq += 100;
      body = { ...timer };
    } else if (path === "/v1/setup/check")
      body = {
        ready: true,
        label: "受控纸面检查通过",
        setup_ticket: "controlled-setup-ticket",
      };
    else if (path.endsWith("/pages/scan"))
      body = {
        pages: [
          {
            box: [0, 0, 1, 1],
            subject: "数学",
            printed_page: "合成样例第1页",
            confidence: 0.98,
          },
        ],
      };
    else if (path.endsWith("/correction-request")) {
      fixture.requested = true;
      body = { requested: true };
    } else if (path.endsWith("/correction-access")) {
      fixture.allowed = JSON.parse(request.postData() || "{}").allowed;
      body = { allowed: fixture.allowed };
    } else if (path === "/v1/hints/stream") {
      fixture.hintCalls++;
      const speeches = [
        "先圈出题目里的两个部分。",
        "已找到条件，可以把它们对应起来。",
        "现在请自己在纸上试一步。",
      ];
      const beats = [
        {
          speech: speeches[0],
          page: [
            {
              kind: "circle",
              anchor_id: "part-a",
              anchor_text: "3",
              box: [0.21, 0.27, 0.04, 0.06],
            },
          ],
          scratch: [],
          reply_mode: "none",
          teaching_move: "read_conditions",
        },
        {
          speech: speeches[1],
          page: [
            {
              kind: "underline",
              anchor_id: "part-b",
              anchor_text: "2",
              box: [0.3, 0.27, 0.04, 0.06],
            },
            {
              kind: "check",
              anchor_id: "part-b",
              anchor_text: "2",
              box: [0.3, 0.27, 0.04, 0.06],
              purpose: "condition_found",
              label: "已找到条件",
            },
          ],
          scratch: [
            {
              kind: "text",
              role: "prose",
              x: 8,
              y: 16,
              text: "先找到两个部分，再自己尝试合起来。",
            },
          ],
          reply_mode: "none",
          teaching_move: "scaffold",
        },
        {
          speech: speeches[2],
          page: [],
          scratch: [],
          reply_mode: "paper",
          teaching_move: "invite_attempt",
        },
      ];
      const frame = (event, data) =>
        `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
      const result = {
        id: "acceptance-hint-" + fixture.hintCalls,
        session_id: fixture.sessionId,
        level: 1,
        response: speeches.join(""),
        source: "controlled-fixture",
        located: true,
        target_box: [0.13, 0.2, 0.67, 0.35],
        target_label: "第1题",
        reply_mode: "paper",
        visual_beats: beats,
      };
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body:
          frame("target", { box: result.target_box, label: "第1题" }) +
          beats.map((beat) => frame("beat", beat)).join("") +
          frame("done", result),
      });
      return;
    } else if (path.endsWith("/corrections") && request.method() === "POST") {
      fixture.correctionCalls++;
      if (!fixture.allowed) {
        await route.fulfill({
          status: 403,
          contentType: "application/json",
          body: '{"detail":"Parent approval required"}',
        });
        return;
      }
      const correction = {
        id: "acceptance-correction",
        session_id: fixture.sessionId,
        task_id: task.id,
        created_at: now,
        summary: "受控样例：请核对第1题的计算。",
        subject_label: "数学",
        page_label: "合成样例第1页",
        source: "controlled-fixture",
        image_available: true,
        review_status: "unreviewed",
        checks: [structuredClone(check)],
      };
      fixture.corrections = [correction];
      body = correction;
    } else if (/\/corrections\/[^/]+\/image$/.test(path)) {
      await route.fulfill({
        status: 200,
        contentType: "image/svg+xml",
        body: paperSvg,
      });
      return;
    } else if (path.endsWith("/recheck")) {
      fixture.recheckCalls++;
      const recheck = {
        ...check,
        status: "check_again",
        attempt: fixture.recheckCalls,
        disagrees: false,
        reason: "受控复检样例：仍需核对这一步。",
        created_at: now,
      };
      fixture.corrections[0].checks[0].recheck = recheck;
      body = { recheck };
    } else if (path === "/v1/reports/daily") {
      const rows = (items) => ({
        items,
        total: items.length,
        truncated: false,
      });
      body = {
        ...timer,
        duration_seconds: 45,
        hint_count: fixture.hintCalls,
        correction_count: fixture.corrections.length,
        unreviewed_count: 0,
        period: {
          since: now - 3600,
          until: now + 86400,
          date: "2026-10-23",
          offset_minutes: 480,
        },
        task_activity: { submitted: 0, confirmed: 0, returned: 0 },
        pending_review_count: 0,
        daily_records: {
          corrections: rows(fixture.corrections),
          observations: rows([]),
          reflections: rows([]),
        },
      };
    } else if (
      path === `/v1/sessions/${fixture.sessionId}` &&
      request.method() === "PATCH"
    ) {
      fixture.paused =
        JSON.parse(request.postData() || "{}").status === "paused";
      if (fixture.paused) fixture.allowed = false;
      body = {
        id: fixture.sessionId,
        status: fixture.paused ? "paused" : "active",
      };
    } else if (path.endsWith("/finish")) {
      fixture.finished = true;
      body = {
        id: fixture.sessionId,
        status: "finished",
        duration_seconds: 45,
        review: {
          hint_count: fixture.hintCalls,
          submitted_task_count: 0,
          task_counts: {},
          record: { text: "受控样例，没有掌握度结论。" },
          next_step: { text: "先自己说明采用的方法。" },
        },
      };
    } else if (path.endsWith("/attempt"))
      body = {
        status: "no_visible_change",
        response: "受控样例：还没有可靠的新书写证据。",
      };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });

  const observe = async (target, role) => {
    target.acceptanceRole = role;
    target.on("console", (message) =>
      report.console.push({
        at: new Date().toISOString(),
        role,
        type: message.type(),
        text: message.text(),
      }),
    );
    target.on("pageerror", (error) =>
      report.page_errors.push({
        at: new Date().toISOString(),
        role,
        message: error.message,
      }),
    );
    target.on("requestfailed", (request) =>
      report.network_failures.push({
        at: new Date().toISOString(),
        role,
        path: apiPath(request.url()),
        failure: request.failure()?.errorText,
      }),
    );
    await target.exposeFunction("__acceptanceEmit", (event) => {
      report.timeline.push({ ...event, role });
    });
  };
  await page.context().addInitScript(
    ({ config, paperSvg }) => {
      const events = [];
      let seq = 0;
      const emit = (kind, data = {}) => {
        const event = {
          seq: ++seq,
          kind,
          at: new Date().toISOString(),
          mono_ms: performance.now(),
          ...data,
        };
        events.push(event);
        window.__acceptanceEmit?.(event).catch(() => {});
      };
      window.__acceptanceTimeline = events;
      window.__acceptanceCameraStreams = [];
      for (const mode of ["child", "parent"]) {
        localStorage.setItem(
          mode + "_token",
          config.mode === "controlled"
            ? "controlled-" + mode
            : config[mode + "_token"],
        );
        localStorage.setItem(mode + "_api", config.base_url);
      }
      for (const name of [
        "paper-speech",
        "paper-speech-progress",
        "paper-activity",
        "paper-speech-choice-used",
      ]) {
        window.addEventListener(name, (event) =>
          emit(name, { detail: event.detail || null }),
        );
      }
      const realFetch = window.fetch.bind(window);
      window.fetch = async (input, init = {}) => {
        const url = typeof input === "string" ? input : input.url;
        const parsed = new URL(url, location.href);
        const path = parsed.pathname.replace(/^.*(?=\/v1\/)/, "");
        const requestId = "fetch-" + ++seq;
        const started = performance.now();
        emit("api-request", {
          request_id: requestId,
          path,
          method: init.method || "GET",
        });
        let response;
        try {
          response = await realFetch(input, init);
        } catch (error) {
          emit("api-error", {
            request_id: requestId,
            path,
            message: error.message,
          });
          throw error;
        }
        emit("api-headers", {
          request_id: requestId,
          path,
          status: response.status,
          elapsed_ms: performance.now() - started,
        });
        const copy = response.clone();
        const consume = async () => {
          const contentType = copy.headers.get("content-type") || "";
          if (contentType.includes("text/event-stream")) {
            const reader = copy.body.getReader();
            const decoder = new TextDecoder();
            let pending = "";
            while (true) {
              const { done, value } = await reader.read();
              pending += decoder.decode(value || new Uint8Array(), {
                stream: !done,
              });
              const frames = pending.split(/\r?\n\r?\n/);
              pending = frames.pop() || "";
              for (const frame of frames) {
                const event = frame.match(/^event:\s*(.+)$/m)?.[1];
                const data = frame.match(/^data:\s*(.+)$/m)?.[1];
                if (event && data) {
                  try {
                    emit("sse-event", {
                      request_id: requestId,
                      path,
                      event,
                      data: JSON.parse(data),
                    });
                  } catch {
                    emit("sse-parse-error", { path });
                  }
                }
              }
              if (done) break;
            }
          } else if (contentType.includes("json"))
            emit("api-result", {
              request_id: requestId,
              path,
              status: copy.status,
              data: await copy.json(),
            });
          else
            emit("api-binary-result", {
              request_id: requestId,
              path,
              status: copy.status,
              content_type: contentType,
            });
        };
        consume().catch((error) =>
          emit("api-evidence-error", {
            request_id: requestId,
            path,
            message: error.message,
          }),
        );
        return response;
      };
      if (config.mode === "controlled") {
        const sockets = [];
        class ControlledSocket extends EventTarget {
          constructor() {
            super();
            this.readyState = 1;
            sockets.push(this);
            setTimeout(() => this.onopen?.({}), 0);
          }
          send() {}
          close() {
            this.readyState = 3;
            emit("socket-closed");
          }
        }
        ControlledSocket.OPEN = 1;
        ControlledSocket.CLOSED = 3;
        window.WebSocket = ControlledSocket;
        const pendingSpeech = new Map();
        window.PaperVoice = {
          hasMicrophonePermission: () => false,
          speak: (text, id) => {
            emit("speech-request", { text, id, simulation: true });
            const pending = { text, id, simulation: true, timers: [] };
            pendingSpeech.set(id, pending);
            pending.timers.push(
              setTimeout(() => {
                emit("speech-start", { text, id, simulation: true });
                window.__speechOutputStart?.(id);
              }, 20),
            );
            pending.timers.push(
              setTimeout(
                () => {
                  pendingSpeech.delete(id);
                  emit("speech-done", { text, id, simulation: true });
                  window.__speechOutputDone?.(id);
                },
                Math.min(500, 100 + text.length * 8),
              ),
            );
          },
          stop: () => {
            emit("speech-stop", { simulation: true });
            for (const pending of pendingSpeech.values()) {
              pending.timers.forEach(clearTimeout);
              emit("speech-error", {
                text: pending.text,
                id: pending.id,
                simulation: true,
                error: "interrupted",
                cancellation_stack: new Error().stack,
              });
            }
            pendingSpeech.clear();
          },
          cancelListening: () => {},
          listen: () => {},
          listenHold: () => {},
          listenReply: () => {},
          stopListening: () => {},
        };
      } else if (window.speechSynthesis) {
        const speak = window.speechSynthesis.speak.bind(window.speechSynthesis);
        window.speechSynthesis.speak = (utterance) => {
          emit("speech-request", { text: utterance.text, simulation: false });
          utterance.addEventListener("start", () =>
            emit("speech-start", { text: utterance.text, simulation: false }),
          );
          utterance.addEventListener("end", () =>
            emit("speech-done", { text: utterance.text, simulation: false }),
          );
          utterance.addEventListener("error", (event) =>
            emit("speech-error", {
              text: utterance.text,
              error: event.error,
              simulation: false,
            }),
          );
          return speak(utterance);
        };
      }
      navigator.mediaDevices.getUserMedia = async (constraints) => {
        if (constraints.audio && !constraints.video)
          throw new DOMException(
            "Acceptance does not simulate microphone input",
            "NotAllowedError",
          );
        const canvas = document.createElement("canvas");
        canvas.width = 1280;
        canvas.height = 960;
        const image = new Image();
        image.src =
          config.camera_data_url ||
          "data:image/svg+xml;charset=utf-8," + encodeURIComponent(paperSvg);
        await image.decode();
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#fffdf5";
        ctx.fillRect(0, 0, 1280, 960);
        ctx.drawImage(image, 0, 0, 1280, 960);
        const stream = canvas.captureStream(5);
        window.__acceptanceCameraStreams.push(stream);
        for (const track of stream.getTracks()) {
          const stop = track.stop.bind(track);
          track.stop = () => {
            emit("camera-stop", { track_kind: track.kind, synthetic: true });
            stop();
          };
        }
        emit("camera-start", { synthetic: true });
        return stream;
      };
      let frameScheduled = false;
      const frame = () => {
        frameScheduled = false;
        const marks = [
          ...document.querySelectorAll(".teaching-page-stroke"),
        ].map((element) => element.getAttribute("d"));
        const labels = [
          ...document.querySelectorAll(
            ".teaching-point-label,.teaching-scratch-line,.teaching-scratch-diagram text",
          ),
        ].map((element) => element.textContent);
        emit("dom-frame", {
          marks,
          labels,
          caption: document.querySelector(".speech-caption")?.textContent || "",
          camera_active: window.__acceptanceCameraStreams.some((stream) =>
            stream.getTracks().some((track) => track.readyState === "live"),
          ),
        });
      };
      new MutationObserver(() => {
        if (!frameScheduled) {
          frameScheduled = true;
          requestAnimationFrame(frame);
        }
      }).observe(document, {
        childList: true,
        subtree: true,
        characterData: true,
      });
    },
    { config, paperSvg },
  );

  await observe(page, "child");
  await page.setViewportSize({ width: 1366, height: 900 });
  let activePage = page;
  let screenshotSeq = 0;
  const screenshot = async (name, target = activePage) => {
    const filename =
      String(++screenshotSeq).padStart(3, "0") + "-" + name + ".png";
    const started_at = new Date().toISOString();
    const clock = await target.evaluate(() => ({
      at: new Date().toISOString(),
      mono_ms: performance.now(),
    }));
    await target.screenshot({ path: config.output + "/" + filename });
    report.screenshots.push({
      name,
      path: filename,
      role: target.acceptanceRole,
      started_at,
      finished_at: new Date().toISOString(),
      browser_clock: clock,
    });
  };
  const stage = async (name, action) => {
    const entry = {
      name,
      status: "running",
      started_at: new Date().toISOString(),
    };
    report.stages.push(entry);
    try {
      entry.checks = (await action()) || {};
      await screenshot(name);
      entry.status = "passed";
    } catch (error) {
      entry.status = "failed";
      entry.error = error.message;
      await screenshot(name + "-failed").catch(() => {});
      throw error;
    } finally {
      entry.finished_at = new Date().toISOString();
    }
  };
  const expect = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  const applicationUrl = (mode) => {
    const url = new URL(config.frontend_url);
    url.searchParams.set("mode", mode);
    return url.href;
  };
  let parent;
  try {
    await stage("child-active", async () => {
      await page.goto(applicationUrl("child"));
      await page
        .getByRole("button", { name: "求一步提示", exact: true })
        .waitFor();
      await page.waitForFunction(() =>
        window.__acceptanceCameraStreams.some((stream) =>
          stream.getTracks().some((track) => track.readyState === "live"),
        ),
      );
      await page.waitForFunction(() =>
        [...document.querySelectorAll(".camera-media video")].some(
          (video) => video.videoWidth > 0,
        ),
      );
      return { real_react_entry: true, synthetic_camera_running: true };
    });
    await stage("new-task-reminder-preserves-controls", async () => {
      const checks = [];
      for (const viewport of [
        { width: 1366, height: 900 },
        { width: 800, height: 1280 },
        { width: 540, height: 960 },
      ]) {
        await page.setViewportSize(viewport);
        const accessibility = await page
          .locator(".camera-controls button")
          .evaluateAll((buttons) =>
            buttons
              .filter((button) => button.getClientRects().length)
              .map((button) => {
                const rect = button.getBoundingClientRect();
                const top = document.elementFromPoint(
                  rect.x + rect.width / 2,
                  rect.y + rect.height / 2,
                );
                return {
                  text: button.textContent.trim(),
                  clickable: top === button || button.contains(top),
                };
              }),
          );
        expect(
          accessibility.every((button) => button.clickable),
          "A task reminder or other overlay blocks a permanent camera control.",
        );
        checks.push({
          viewport,
          buttons: accessibility,
          reminder_visible:
            (await page.locator(".task-reminder-banner").count()) > 0,
        });
        await screenshot(
          "reminder-controls-" + viewport.width + "x" + viewport.height,
        );
      }
      await page.setViewportSize({ width: 1366, height: 900 });
      return { viewports: checks, reminder_dismissed: false };
    });
    await stage("tutoring-stream-and-drawing", async () => {
      const before = report.timeline.length;
      await page
        .getByRole("button", { name: "求一步提示", exact: true })
        .click();
      let taking = false;
      const shots = setInterval(async () => {
        if (taking) return;
        taking = true;
        await screenshot("tutoring-frame", page).catch(() => {});
        taking = false;
      }, 900);
      try {
        await page.locator(".hint-paper-return").waitFor({ timeout: 90000 });
        await page.waitForFunction(
          () =>
            window.__acceptanceTimeline.some(
              (event) => event.kind === "sse-event" && event.event === "done",
            ),
          null,
          { timeout: 90000 },
        );
        await page.waitForFunction(
          () =>
            ![...document.querySelectorAll("button")].some((button) =>
              button.textContent.includes("正在准备提示"),
            ),
          null,
          { timeout: 90000 },
        );
      } finally {
        clearInterval(shots);
        while (taking) await page.waitForTimeout(50);
      }
      const events = report.timeline
        .slice(before)
        .filter((event) => event.role === "child");
      const beats = events.filter(
        (event) => event.kind === "sse-event" && event.event === "beat",
      );
      expect(beats.length > 0, "No validated teaching beat was observed.");
      const starts = events.filter((event) => event.kind === "speech-start");
      const alignment = beats.map((beat) => {
        const start = starts.find((event) => event.text === beat.data.speech);
        const frame =
          start &&
          events.find(
            (event) =>
              event.kind === "dom-frame" &&
              event.mono_ms >= start.mono_ms &&
              event.caption.includes(beat.data.speech),
          );
        const previous =
          start &&
          events
            .filter(
              (event) =>
                event.kind === "dom-frame" && event.mono_ms < start.mono_ms,
            )
            .at(-1);
        const expectsVisual = Boolean(
          beat.data.page?.length || beat.data.scratch?.length,
        );
        const changed =
          frame &&
          JSON.stringify([frame.marks, frame.labels]) !==
            JSON.stringify([previous?.marks, previous?.labels]);
        return {
          text: beat.data.speech,
          sse_observed_mono_ms: beat.mono_ms,
          speech_start_mono_ms: start?.mono_ms,
          presentation_frame_mono_ms: frame?.mono_ms,
          ink_frame_mono_ms: expectsVisual && changed ? frame.mono_ms : null,
          expects_visual: expectsVisual,
          speech_after_sse_ms: start ? start.mono_ms - beat.mono_ms : null,
          frame_after_speech_ms:
            frame && start ? frame.mono_ms - start.mono_ms : null,
        };
      });
      if (controlled)
        expect(
          alignment.every((item) => item.speech_start_mono_ms !== undefined),
          "A controlled teaching segment was never played.",
        );
      return {
        beats: beats.length,
        alignment,
        playback_observed_segments: alignment.filter(
          (item) => item.speech_start_mono_ms !== undefined,
        ).length,
        teacher_quality_evaluated: false,
      };
    });
    await stage("hide-and-restore-teaching-marks", async () => {
      await page.getByRole("button", { name: "隐藏标记，看原题" }).click();
      expect(
        (await page.locator(".teaching-ink,.teaching-scratch").count()) === 0,
        "Hidden teacher overlays remained.",
      );
      await screenshot("original-paper-without-marks");
      await page.getByRole("button", { name: "显示辅导标记" }).click();
      expect(
        (await page.locator(".teaching-ink").count()) === 1,
        "Teacher marks did not return.",
      );
      await page.getByRole("button", { name: "返回实时画面" }).click();
      return { original_image_retained: true, restored: true };
    });
    await stage("cancel-correction-capture", async () => {
      const before = fixture.correctionCalls;
      await page
        .locator(".camera-controls button")
        .filter({ hasText: /申请批改|批改作业/ })
        .click();
      await page.getByRole("dialog", { name: "拍摄作业" }).waitFor();
      await screenshot("capture-before-cancel");
      await page.getByRole("button", { name: "取消拍摄作业" }).click();
      expect(
        (await page.getByRole("dialog", { name: "拍摄作业" }).count()) === 0,
        "Cancel left the capture modal open.",
      );
      if (controlled)
        expect(
          fixture.correctionCalls === before,
          "Cancel submitted a correction.",
        );
      return { cancelled: true };
    });
    await stage("child-requests-parent-approval", async () => {
      await page
        .locator(".camera-controls button")
        .filter({ hasText: /申请批改|批改作业/ })
        .click();
      await page
        .getByRole("button", { name: "确认批改这张" })
        .waitFor({ timeout: 90000 });
      await page.getByRole("button", { name: "确认批改这张" }).click();
      await page
        .getByText("已请家长授权；获批后将自动批改已确认的截图")
        .waitFor();
      if (controlled)
        expect(
          fixture.correctionCalls === 0,
          "Grading bypassed parent authorization.",
        );
      return { request_sent: true, grading_waits_for_approval: true };
    });
    await stage("parent-authorizes-correction", async () => {
      parent = await page.context().newPage();
      await observe(parent, "parent");
      activePage = parent;
      await parent.setViewportSize({ width: 800, height: 1000 });
      await parent.goto(applicationUrl("parent"));
      await parent
        .getByRole("button", { name: "允许 10 分钟", exact: true })
        .click();
      await parent
        .getByText("已允许孩子在 10 分钟内批改作业", { exact: true })
        .waitFor();
      return { explicit_parent_action: true };
    });
    await stage("child-expands-correction-result", async () => {
      activePage = page;
      await page.bringToFront();
      await page
        .locator(".child-correction-card .correction-result")
        .waitFor({ timeout: 180000 });
      const overlayChecks = [];
      for (const viewport of [
        { width: 1366, height: 900 },
        { width: 800, height: 1280 },
        { width: 540, height: 960 },
      ]) {
        await page.setViewportSize(viewport);
        await page.locator(".child-correction-card").evaluate((card) => {
          card.scrollTop = 0;
        });
        const visibility = await page
          .locator(
            ".child-correction-card .correction-result-heading strong,.child-correction-card .correction-summary,.child-correction-card .correction-drawer-close",
          )
          .evaluateAll((elements) =>
            elements.map((element) => {
              const range = document.createRange();
              range.selectNodeContents(element);
              const points = [...range.getClientRects()].flatMap((rect) => [
                [
                  rect.left + Math.min(4, rect.width / 2),
                  rect.top + rect.height / 2,
                ],
                [rect.left + rect.width / 2, rect.top + rect.height / 2],
                [
                  rect.right - Math.min(4, rect.width / 2),
                  rect.top + rect.height / 2,
                ],
              ]);
              return {
                text: element.textContent.trim(),
                unobstructed: points.every(([x, y]) => {
                  const top = document.elementFromPoint(x, y);
                  return !!top?.closest(".child-correction-card");
                }),
              };
            }),
          );
        expect(
          visibility.every((element) => element.unobstructed),
          "An overlay obscures the correction heading, summary, or close control.",
        );
        overlayChecks.push({ viewport, elements: visibility });
        await screenshot(
          "correction-reminder-layer-" + viewport.width + "x" + viewport.height,
        );
      }
      await page.setViewportSize({ width: 1366, height: 900 });
      const review = page.locator(
        ".child-correction-card .correction-written-review",
      );
      if (!(await review.getAttribute("open")))
        await review.locator("summary").click();
      await page
        .locator(".child-correction-card .correction-check")
        .first()
        .waitFor();
      expect(
        (await page.locator(".child-correction-card").innerText()).includes(
          "待核对",
        ),
        "Correction result lacked uncertainty context.",
      );
      return {
        checks_visible: await page
          .locator(".child-correction-card .correction-check")
          .count(),
        response_source: controlled ? "fixture" : "live-provider",
        overlay_visibility: overlayChecks,
      };
    });
    await stage("parent-rechecks-one-question", async () => {
      activePage = parent;
      await parent.bringToFront();
      await parent.reload();
      await parent.getByRole("button", { name: "每日回顾" }).click();
      const toggle = parent.locator(".correction-review-toggle").first();
      await toggle.waitFor();
      if ((await toggle.getAttribute("aria-expanded")) !== "true")
        await toggle.click();
      await parent
        .locator(".correction-review .correction-paper img")
        .waitFor();
      const marker = parent
        .locator(".correction-review .paper-mark-ink")
        .first();
      const located = (await marker.count()) > 0;
      if (located) {
        await marker.click();
        await parent
          .getByRole("button", { name: "AI 复检本题", exact: true })
          .click();
      } else {
        const written = parent
          .locator(".correction-review .correction-written-review")
          .first();
        if (!(await written.getAttribute("open")))
          await written.locator("summary").click();
        await parent
          .getByRole("button", { name: "AI 复检这题", exact: true })
          .first()
          .click();
      }
      await parent.waitForFunction(
        () =>
          window.__acceptanceTimeline.some(
            (event) =>
              event.kind === "api-result" &&
              event.path.endsWith("/recheck") &&
              event.status === 200,
          ),
        null,
        { timeout: 180000 },
      );
      if (controlled)
        expect(
          fixture.recheckCalls === 1,
          "Expected exactly one question recheck.",
        );
      await screenshot("parent-recheck-result");
      if (located) {
        await parent
          .getByRole("button", { name: "家长校对／更正这题", exact: true })
          .click();
        await parent
          .getByRole("button", { name: "收起家长校对", exact: true })
          .click();
      }
      expect(
        report.safety_blocks.length === 0,
        "An unexpected prohibited write was attempted.",
      );
      return {
        recheck_completed: true,
        parent_calibration_editor_cancelled: located,
        parent_calibration_editor_unavailable_without_location: !located,
        parent_calibration_saved: false,
      };
    });
    await stage("pause-stops-camera-and-session", async () => {
      activePage = page;
      await page.bringToFront();
      await page.getByRole("button", { name: "关闭批改结果" }).click();
      const speechCount = report.timeline.filter(
        (event) => event.role === "child" && event.kind === "speech-start",
      ).length;
      await page
        .locator(".camera-controls button")
        .filter({ hasText: "暂停" })
        .click();
      await page
        .getByRole("button", { name: "继续作业", exact: true })
        .waitFor();
      await page.waitForFunction(
        () =>
          window.__acceptanceTimeline.some(
            (event) =>
              event.kind === "speech-done" &&
              event.text === "已暂停，相机已关闭",
          ),
        null,
        { timeout: 10000 },
      );
      const tracks = await page.evaluate(() =>
        window.__acceptanceCameraStreams.flatMap((stream) =>
          stream.getTracks().map((track) => track.readyState),
        ),
      );
      expect(
        tracks.length > 0 && tracks.every((value) => value === "ended"),
        "Pause left a camera track running.",
      );
      if (controlled)
        expect(
          fixture.paused,
          "Pause did not synchronize the controlled session.",
        );
      await page.waitForTimeout(1300);
      const latest = report.timeline
        .filter(
          (event) => event.role === "child" && event.kind === "speech-start",
        )
        .slice(speechCount);
      expect(
        !latest.some((event) =>
          /先圈出题目|已找到条件|现在请自己/.test(event.text || ""),
        ),
        "A stale teaching beat played after pause.",
      );
      return {
        all_camera_tracks_ended: true,
        session_paused: true,
        pause_announcement_completed: true,
        stale_teaching_after_pause: false,
      };
    });
  } catch (error) {
    report.workflow_error = error.message;
  }
  await page.waitForTimeout(150);
  report.finished_at = new Date().toISOString();
  report.controlled_counts = controlled
    ? {
        hints: fixture.hintCalls,
        corrections: fixture.correctionCalls,
        rechecks: fixture.recheckCalls,
      }
    : null;
  return report;
}
