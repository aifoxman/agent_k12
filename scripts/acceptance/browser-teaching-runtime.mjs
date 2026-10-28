// Playwright CLI runtime: real production React/API/model with one saved-image camera.
// No oracle, fixture model output, native voice bridge, or synthetic speech event is installed.
export async function runBrowserAcceptance(page, config, writeAllowed) {
  const report = {
    schema_version: 1,
    mode: "live",
    flow: config.flow || "teaching-paced",
    started_at: new Date().toISOString(),
    isolated_api_identity_verified: config.api_identity_verified === true,
    acceptance_run_id: config.acceptance_run_id,
    camera_fixture: config.camera_fixture,
    evidence: {
      frontend: "production React UI",
      model:
        config.entry === "replay"
          ? "stored output from the preceding real API/provider request; no new model call or fixture response"
          : "isolated real API/provider; no output mock",
      camera: "saved-image canvas simulation; not live device camera",
      microphone: "not exercised",
      speech:
        "unmodified browser/audio lifecycle observation; sound quality and listening unknown",
      stream_timing:
        "client-observed cloned SSE; not server emission or word timestamps",
    },
    stages: [],
    screenshots: [],
    timeline: [],
    console: [],
    page_errors: [],
    network_failures: [],
    safety_blocks: [],
    environment_changes: [],
    limitations: [
      "No Pad/hardware/camera/microphone or human/agent listening claim.",
      "Rules verify pace/state delivery, not mathematical truth or teacher quality.",
      "Only one teacher request; no grading, parent approval, correction, or deletion.",
    ],
  };
  const expect = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  let hints = 0,
    screenshotSeq = 0,
    screenshotQueue = Promise.resolve(),
    lastFrame = "";
  const expectedHints = config.entry === "replay" ? 0 : 1;
  let replayBaseline = null;
  const paceButton = (name) =>
    page
      .locator(".teaching-step-controls")
      .getByRole("button", { name, exact: true });
  let mainNavigations = 0;
  let intentionalColdReload = false;
  page.on("framenavigated", (frame) => {
    if (frame !== page.mainFrame()) return;
    mainNavigations++;
    const event = {
      kind: "page-navigation",
      at: new Date().toISOString(),
      count: mainNavigations,
      path: new URL(frame.url()).pathname,
    };
    report.timeline.push(event);
    if (intentionalColdReload) {
      intentionalColdReload = false;
      report.timeline.push({
        kind: "intentional-cold-reload",
        at: new Date().toISOString(),
      });
    } else if (mainNavigations > 1)
      report.environment_changes.push({
        ...event,
        detail:
          "Document reloaded during acceptance; dev HMR or external reload invalidates continuous playback evidence",
      });
  });
  const snapshot = () =>
    page.evaluate(() => {
      const root = document.querySelector("[data-teaching-mode]");
      const controls = document.querySelector(".teaching-step-controls");
      const body = document.querySelector(".teaching-scratch-body");
      const diagram = body?.querySelector(".teaching-scratch-diagram");
      const paragraph =
        body?.querySelector('[aria-current="step"]') ||
        [...(body?.querySelectorAll(".teaching-scratch-step") || [])].at(-1);
      const focusKind =
        document.querySelector(".teaching-scratch")?.dataset
          .teachingScratchFocus;
      const bounds = (node) => {
        const rect = node?.getBoundingClientRect();
        return rect
          ? {
              top: rect.top,
              bottom: rect.bottom,
              left: rect.left,
              right: rect.right,
              height: rect.height,
            }
          : null;
      };
      const focusedNodes =
        focusKind === "diagram"
          ? [
              ...(diagram?.querySelectorAll(
                '[data-teaching-focus="current"]',
              ) || []),
            ]
          : paragraph
            ? [paragraph]
            : [];
      const parts = focusedNodes.map((node) => ({
        tag: node.tagName,
        text: node.textContent,
        beat: node.getAttribute("data-teaching-diagram-beat"),
        ...bounds(node),
      }));
      const currentBounds = parts.length
        ? {
            top: Math.min(...parts.map((part) => part.top)),
            bottom: Math.max(...parts.map((part) => part.bottom)),
            left: Math.min(...parts.map((part) => part.left)),
            right: Math.max(...parts.map((part) => part.right)),
          }
        : null;
      if (currentBounds)
        currentBounds.height = currentBounds.bottom - currentBounds.top;
      const bodyBounds = bounds(body);
      const fonts = [...(diagram?.querySelectorAll("text") || [])].map(
        (node) => {
          const matrix = node.getScreenCTM();
          return (
            parseFloat(getComputedStyle(node).fontSize) *
            Math.hypot(matrix.a, matrix.b)
          );
        },
      );
      const fullyVisible = Boolean(
        currentBounds &&
        currentBounds.top >= bodyBounds.top - 1 &&
        currentBounds.bottom <= bodyBounds.bottom + 1 &&
        currentBounds.left >= bodyBounds.left - 1 &&
        currentBounds.right <= bodyBounds.right + 1,
      );
      const scratchVisibility = body
        ? {
            kind: focusKind,
            body: bodyBounds,
            focus: currentBounds,
            focus_parts: parts,
            scrollTop: body.scrollTop,
            scrollHeight: body.scrollHeight,
            minimum_label_font: fonts.length ? Math.min(...fonts) : null,
            visible: fullyVisible,
            fully_visible: fullyVisible,
          }
        : null;
      return {
        at: new Date().toISOString(),
        mono_ms: performance.now(),
        mode: root?.dataset.teachingMode,
        phase: root?.dataset.teachingPhase,
        step: root?.dataset.teachingStep,
        heading:
          document.querySelector(".teaching-step-controls-heading")
            ?.textContent || "",
        status:
          document.querySelector(".teaching-step-controls-status")
            ?.textContent || "",
        caption: document.querySelector(".speech-caption p")?.textContent || "",
        scratch: document.querySelector(".teaching-scratch")?.textContent || "",
        scratch_visibility: scratchVisibility,
        marks: [...document.querySelectorAll(".teaching-page-stroke")].map(
          (node) => node.getAttribute("d"),
        ),
        notes: [...document.querySelectorAll(".teaching-page-note")].map(
          (node) => node.textContent,
        ),
        frozen: Boolean(
          document.querySelector(".hint-paper-guide.frozen > img"),
        ),
        controls: controls
          ? [...controls.querySelectorAll("button")].map((button) => {
              const rect = button.getBoundingClientRect(),
                hit = document.elementFromPoint(
                  rect.left + rect.width / 2,
                  rect.top + rect.height / 2,
                );
              return {
                text: button.textContent,
                disabled: button.disabled,
                height: rect.height,
                hit: hit === button || button.contains(hit),
              };
            })
          : [],
        videos: [...document.querySelectorAll("video")].map((video) => ({
          width: video.videoWidth,
          height: video.videoHeight,
          tracks: [...(video.srcObject?.getTracks() || [])].map((track) => ({
            kind: track.kind,
            state: track.readyState,
          })),
        })),
      };
    });
  const screenshot = async (name, requested = null) => {
    const filename =
      String(++screenshotSeq).padStart(3, "0") + "-" + name + ".png";
    const actual = await snapshot();
    await page.screenshot({ path: config.output + "/" + filename });
    report.screenshots.push({
      name,
      path: filename,
      requested_frame: requested,
      captured_frame: actual,
      finished_at: new Date().toISOString(),
    });
  };
  const stage = async (name, action) => {
    const item = {
      name,
      status: "running",
      started_at: new Date().toISOString(),
    };
    report.stages.push(item);
    try {
      item.checks = await action();
      await screenshot(name);
      item.status = "passed";
    } catch (error) {
      item.status = "failed";
      item.error = error.message;
      await screenshot(name + "-failed").catch(() => {});
      throw error;
    } finally {
      item.finished_at = new Date().toISOString();
    }
  };
  page.on("console", (message) =>
    report.console.push({
      at: new Date().toISOString(),
      type: message.type(),
      text: message.text(),
    }),
  );
  page.on("pageerror", (error) =>
    report.page_errors.push({
      at: new Date().toISOString(),
      message: error.message,
    }),
  );
  page.on("requestfailed", (request) =>
    report.network_failures.push({
      at: new Date().toISOString(),
      path: new URL(request.url()).pathname,
      error: request.failure()?.errorText,
    }),
  );
  await page.exposeFunction("__pacedAcceptanceEmit", (event) => {
    report.timeline.push(event);
    if (event.kind !== "dom-frame" || !event.controls || !event.caption) return;
    const key = JSON.stringify([
      event.step,
      event.phase,
      event.caption,
      event.marks,
      event.notes,
    ]);
    if (key === lastFrame) return;
    lastFrame = key;
    screenshotQueue = screenshotQueue
      .then(() => screenshot("short-step-frame", event))
      .catch((error) =>
        report.timeline.push({
          kind: "screenshot-error",
          message: error.message,
          at: new Date().toISOString(),
        }),
      );
  });
  await page.context().route("**/v1/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    const allowed =
      url.origin === new URL(config.base_url).origin &&
      writeAllowed(request.method(), url.pathname, config.session_id);
    if (
      !allowed ||
      (url.pathname === "/v1/hints/stream" &&
        request.method() === "POST" &&
        ++hints > (config.entry === "replay" ? 0 : 1))
    ) {
      report.safety_blocks.push({
        method: request.method(),
        path: url.pathname,
        reason: allowed
          ? "Only one teacher request is authorized for this paced run"
          : "Outside tutoring-only isolated scope",
      });
      return route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({
          error: { message: "Acceptance scope blocked this action" },
        }),
      });
    }
    return route.continue();
  });
  await page.context().addInitScript(
    ({ config }) => {
      const events = [];
      let seq = 0;
      const emit = (kind, data = {}) => {
        const event = {
          seq: ++seq,
          kind,
          at: new Date().toISOString(),
          mono_ms: performance.now(),
          document_time_origin: performance.timeOrigin,
          ...data,
        };
        events.push(event);
        window.__pacedAcceptanceEmit?.(event).catch(() => {});
      };
      window.__pacedAcceptanceTimeline = events;
      localStorage.setItem("child_token", config.child_token);
      localStorage.setItem("child_api", config.base_url);
      for (const name of [
        "paper-speech",
        "paper-speech-progress",
        "paper-activity",
      ])
        window.addEventListener(name, (event) =>
          emit(name, { detail: event.detail || null }),
        );
      const nativeFetch = window.fetch.bind(window);
      window.fetch = async (input, init = {}) => {
        const url = new URL(
            typeof input === "string" ? input : input.url,
            location.href,
          ),
          requestId = "fetch-" + ++seq,
          started = performance.now();
        if (!url.pathname.startsWith("/v1/")) return nativeFetch(input, init);
        emit("api-request", {
          request_id: requestId,
          path: url.pathname,
          method: init.method || "GET",
          ...(url.pathname === "/v1/audio/speech"
            ? { voice_request: JSON.parse(init.body || "{}") }
            : {}),
        });
        const response = await nativeFetch(input, init);
        emit("api-headers", {
          request_id: requestId,
          path: url.pathname,
          status: response.status,
          elapsed_ms: performance.now() - started,
        });
        const copy = response.clone();
        (async () => {
          const type = copy.headers.get("content-type") || "";
          if (type.includes("text/event-stream")) {
            const reader = copy.body.getReader(),
              decoder = new TextDecoder();
            let pending = "";
            while (true) {
              const { done, value } = await reader.read();
              pending += decoder.decode(value || new Uint8Array(), {
                stream: !done,
              });
              const frames = pending.split(/\r?\n\r?\n/);
              pending = frames.pop() || "";
              for (const frame of frames) {
                const event = frame.match(/^event:\s*(.+)$/m)?.[1],
                  raw = frame.match(/^data:\s*(.+)$/m)?.[1];
                if (event && raw)
                  emit("sse-event", {
                    request_id: requestId,
                    event,
                    data: JSON.parse(raw),
                  });
              }
              if (done) break;
            }
          } else if (type.includes("json"))
            emit("api-result", {
              request_id: requestId,
              path: url.pathname,
              status: copy.status,
              data: await copy.json(),
            });
          else
            emit("api-binary-result", {
              request_id: requestId,
              path: url.pathname,
              status: copy.status,
              content_type: type,
            });
        })().catch((error) =>
          emit("evidence-consumption-error", {
            path: url.pathname,
            message: error.message,
          }),
        );
        return response;
      };
      if (window.speechSynthesis) {
        const speak = window.speechSynthesis.speak.bind(window.speechSynthesis);
        window.speechSynthesis.speak = (utterance) => {
          emit("speech-request", {
            text: utterance.text,
            rate: utterance.rate,
            simulation: false,
          });
          for (const type of ["start", "end", "error"])
            utterance.addEventListener(type, (event) =>
              emit("speech-" + type, {
                text: utterance.text,
                rate: utterance.rate,
                error: event.error || null,
                simulation: false,
              }),
            );
          return speak(utterance);
        };
      }
      for (const type of ["playing", "ended", "error"])
        document.addEventListener(
          type,
          (event) => {
            if (event.target instanceof HTMLMediaElement)
              emit("media-" + type, {
                tag: event.target.tagName,
                rate: event.target.playbackRate,
                simulation: false,
              });
          },
          true,
        );
      navigator.mediaDevices.getUserMedia = async (constraints) => {
        if (!constraints.video)
          throw new DOMException(
            "Microphone not exercised in saved-image acceptance",
            "NotAllowedError",
          );
        const image = new Image();
        image.src = config.camera_data_url;
        await image.decode();
        const canvas = document.createElement("canvas");
        canvas.width = image.naturalWidth;
        canvas.height = image.naturalHeight;
        canvas.getContext("2d").drawImage(image, 0, 0);
        const stream = canvas.captureStream(5);
        emit("camera-start", {
          simulation: true,
          width: canvas.width,
          height: canvas.height,
          source_sha256: config.camera_fixture.sha256,
        });
        return stream;
      };
      let scheduled = false;
      const observe = () => {
        if (scheduled) return;
        scheduled = true;
        requestAnimationFrame(() => {
          scheduled = false;
          const root = document.querySelector("[data-teaching-mode]");
          emit("dom-frame", {
            mode: root?.dataset.teachingMode,
            phase: root?.dataset.teachingPhase,
            step: root?.dataset.teachingStep,
            caption:
              document.querySelector(".speech-caption p")?.textContent || "",
            controls: Boolean(
              document.querySelector(".teaching-step-controls"),
            ),
            marks: [...document.querySelectorAll(".teaching-page-stroke")].map(
              (node) => node.getAttribute("d"),
            ),
            notes: [...document.querySelectorAll(".teaching-page-note")].map(
              (node) => node.textContent,
            ),
          });
        });
      };
      new MutationObserver(observe).observe(document, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeFilter: [
          "data-teaching-mode",
          "data-teaching-phase",
          "data-teaching-step",
        ],
      });
    },
    { config },
  );
  await page.setViewportSize({ width: 1182, height: 698 });
  const settled = async () => {
    const started = Date.now();
    while (true) {
      expect(
        !report.environment_changes.length,
        "Document reloaded during acceptance; continuous playback run is invalidated",
      );
      const phase = await page.evaluate(
        () =>
          document.querySelector("[data-teaching-mode]")?.dataset.teachingPhase,
      );
      if (["waiting", "complete", "interrupted"].includes(phase)) break;
      expect(
        Date.now() - started < 180000,
        "Teaching did not reach a manual boundary within 180 seconds",
      );
      await page.waitForTimeout(250);
    }
    const state = await snapshot();
    expect(
      state.phase !== "interrupted",
      "Browser playback interrupted; audio capability unknown, no synthetic callback substituted",
    );
    return state;
  };
  const hold = async () => {
    const before = await snapshot(),
      started = Date.now();
    expect(
      !before.scratch_visibility || before.scratch_visibility.visible,
      "Current scratch diagram/reason is outside its scroll viewport: " +
        JSON.stringify(before.scratch_visibility),
    );
    expect(
      !before.scratch_visibility ||
        before.scratch_visibility.minimum_label_font === null ||
        before.scratch_visibility.minimum_label_font >= 16,
      "Scratch label is smaller than 16 physical pixels: " +
        JSON.stringify(before.scratch_visibility),
    );
    await page.waitForTimeout(config.dwell_ms);
    const after = await snapshot();
    expect(
      !after.scratch_visibility || after.scratch_visibility.visible,
      "Current scratch diagram/reason disappeared during dwell: " +
        JSON.stringify(after.scratch_visibility),
    );
    expect(
      before.step === after.step &&
        ["waiting", "complete"].includes(after.phase),
      "Without clicking Next, teaching advanced automatically",
    );
    expect(
      JSON.stringify([before.caption, before.marks, before.notes]) ===
        JSON.stringify([after.caption, after.marks, after.notes]),
      "A future teaching action appeared during manual dwell",
    );
    return {
      step: after.step,
      phase: after.phase,
      dwell_ms: Date.now() - started,
      manual_advance_only: true,
      scratch_visibility: after.scratch_visibility,
    };
  };
  try {
    await stage("isolated-child-and-simulated-camera", async () => {
      const url = new URL(config.frontend_url);
      url.searchParams.set("mode", "child");
      await page.goto(url.href);
      await page
        .getByRole("button", { name: "老师讲解", exact: true })
        .waitFor({ timeout: 60000 });
      await page.waitForFunction(
        () => document.querySelector(".camera-media video")?.videoWidth > 0,
        null,
        { timeout: 30000 },
      );
      const state = await snapshot();
      report.frontend_assets = await page.evaluate(() =>
        [...document.scripts].map((script) => script.src).filter(Boolean),
      );
      if (config.entry === "replay") {
        replayBaseline = await page.evaluate(async (sessionId) => {
          const response = await fetch(
            localStorage.getItem("child_api") + "/v1/families/dashboard",
            {
              headers: {
                Authorization: "Bearer " + localStorage.getItem("child_token"),
              },
            },
          );
          if (!response.ok) throw new Error("Cannot read replay archive");
          return (await response.json()).hints.find(
            (hint) =>
              hint.session_id === sessionId &&
              hint.teaching_scene?.beats?.length,
          )?.teaching_scene;
        }, config.session_id);
        expect(
          replayBaseline?.beats?.length,
          "No genuine saved lesson is available for replay",
        );
      }
      expect(
        state.mode === "idle" && !state.frozen && !state.scratch,
        "Historical scene opened before explicit teaching",
      );
      return {
        synthetic_family: true,
        simulated_saved_camera: true,
        original_device_storage_accessed: false,
        model_mocked: false,
      };
    });
    if (config.flow === "teaching-continuous") {
      await stage("continuous-lesson-start", async () => {
        if (config.entry === "replay") {
          await page
            .getByRole("button", { name: "回看上次辅导", exact: true })
            .click();
          await page
            .getByRole("button", { name: "分步重讲", exact: true })
            .click();
        } else if (config.question) {
          await page
            .getByRole("button", { name: "打开文字提问", exact: true })
            .click();
          await page
            .getByRole("textbox", { name: "我的问题", exact: true })
            .fill(config.question);
          await page.getByTitle("发送问题", { exact: true }).click();
        } else {
          await page
            .getByRole("button", { name: "老师讲解", exact: true })
            .click();
        }
        await page
          .locator(".teaching-step-controls")
          .waitFor({ timeout: 180000 });
        await page.waitForFunction(
          () =>
            ["playing", "reading"].includes(
              document.querySelector("[data-teaching-mode]")?.dataset
                .teachingPhase,
            ),
          null,
          { timeout: 180000 },
        );
        return { teacher_requests: hints, manual_next_clicks: 0 };
      });
      await stage("automatic-logical-step-and-explicit-pause", async () => {
        const before = await snapshot();
        await page.waitForFunction(
          (previous) => {
            const root = document.querySelector("[data-teaching-mode]");
            return (
              Number(root?.dataset.teachingStep) > Number(previous) &&
              ["playing", "reading"].includes(root?.dataset.teachingPhase)
            );
          },
          before.step,
          { timeout: 180000 },
        );
        const advanced = await snapshot();
        await paceButton("暂停讲解").click();
        await settled();
        const held = await hold();
        return {
          ...held,
          automatic_advance_observed: true,
          previous_step: before.step,
          advanced_step: advanced.step,
          manual_next_clicks: 0,
        };
      });
      await stage("replay-and-normal-slower-stay-on-current-step", async () => {
        const initial = await snapshot();
        await paceButton("再讲这步").click();
        await page.waitForFunction(() =>
          ["playing", "reading"].includes(
            document.querySelector("[data-teaching-mode]")?.dataset
              .teachingPhase,
          ),
        );
        await settled();
        expect(
          (await snapshot()).step === initial.step,
          "Replay advanced the logical step",
        );
        await paceButton("慢一点").click();
        await page.waitForFunction(() =>
          ["playing", "reading"].includes(
            document.querySelector("[data-teaching-mode]")?.dataset
              .teachingPhase,
          ),
        );
        await settled();
        expect(
          (await snapshot()).step === initial.step,
          "Slower advanced the logical step",
        );
        expect(
          hints === expectedHints,
          "Playback controls requested another lesson",
        );
        return {
          ...(await hold()),
          replay_stays_on_step: true,
          slower_stays_on_step: true,
        };
      });
      await stage("resume-and-complete-continuous-lesson", async () => {
        await paceButton("继续讲解").click();
        const started = Date.now();
        while (true) {
          expect(
            !report.environment_changes.length,
            "Document reloaded during continuous playback",
          );
          const state = await snapshot();
          if (state.phase === "complete") break;
          expect(
            state.phase !== "interrupted",
            "Real audio playback was interrupted",
          );
          expect(
            state.phase !== "waiting",
            "Lesson unexpectedly required another manual Next",
          );
          expect(
            Date.now() - started < 300000,
            "Continuous lesson did not finish within five minutes",
          );
          await page.waitForTimeout(250);
        }
        const speech = report.timeline.filter(
          (event) => event.kind === "speech-request",
        );
        const cloud = report.timeline.filter(
          (event) =>
            event.kind === "api-request" && event.path === "/v1/audio/speech",
        );
        const rates = [
          ...speech.map((event) => event.rate),
          ...cloud.map((event) => event.voice_request?.speed),
        ];
        expect(
          rates.includes(1),
          "No observed real normal-speed speech request",
        );
        expect(
          rates.includes(0.85),
          "No observed real slower-speed speech request",
        );
        return {
          completed_without_next: true,
          teacher_requests: hints,
          observed_speech_rates: [...new Set(rates)],
        };
      });
    } else {
      await stage(
        config.entry === "replay"
          ? "stored-real-output-first-step-hold"
          : "real-teacher-request-and-first-step-hold",
        async () => {
          if (config.entry === "replay") {
            await page
              .getByRole("button", { name: "回看上次辅导", exact: true })
              .click();
            await page
              .getByRole("button", { name: "分步重讲", exact: true })
              .click();
          } else
            await page
              .getByRole("button", { name: "老师讲解", exact: true })
              .click();
          await page
            .locator(".teaching-step-controls")
            .waitFor({ timeout: 180000 });
          await settled();
          return { ...(await hold()), teacher_requests: hints };
        },
      );
      await stage("replay-and-slower-keep-current-step", async () => {
        const initial = await snapshot();
        await paceButton("再讲这步").click();
        await page.waitForFunction(() =>
          ["playing", "reading", "preparing"].includes(
            document.querySelector("[data-teaching-mode]")?.dataset
              .teachingPhase,
          ),
        );
        await settled();
        expect(
          (await snapshot()).step === initial.step,
          "Replay advanced the logical step",
        );
        await screenshot("replayed-current-step");
        await paceButton("再讲这步").click();
        await page.waitForFunction(
          () =>
            document.querySelector("[data-teaching-mode]")?.dataset
              .teachingPhase === "playing",
          null,
          { timeout: 60000 },
        );
        const slow = paceButton("慢一点");
        expect(await slow.isEnabled(), "Slower is unavailable while playing");
        await slow.click();
        await settled();
        const final = await snapshot();
        expect(final.step === initial.step, "Slower advanced the logical step");
        expect(
          hints === expectedHints,
          "Replay or Slower made another teacher request",
        );
        return {
          replay_stays_on_step: true,
          slower_during_playback_stays_on_step: true,
          teacher_requests: hints,
          ...(await hold()),
        };
      });
      let complete = false;
      for (let count = 0; count < config.max_steps; count++) {
        const before = await snapshot();
        if (before.phase === "complete") {
          complete = true;
          break;
        }
        await stage(
          "next-logical-step-" + String(count + 1).padStart(2, "0"),
          async () => {
            expect(
              before.phase === "waiting",
              "Next requested before the manual boundary",
            );
            const next = paceButton("下一步");
            expect(
              await next.isEnabled(),
              "Next button is disabled at the waiting boundary",
            );
            await next.click();
            await page.waitForFunction(
              (step) =>
                document.querySelector("[data-teaching-mode]")?.dataset
                  .teachingStep !== step,
              before.step,
              { timeout: 60000 },
            );
            const after = await settled();
            expect(
              Number(after.step) > Number(before.step),
              "Explicit Next did not advance the logical step",
            );
            return { ...(await hold()), previous_step: before.step };
          },
        );
      }
      expect(
        complete || (await snapshot()).phase === "complete",
        "Maximum bounded steps reached before the lesson completed",
      );
    }
    await stage("exit-hides-scene-and-stays-live", async () => {
      await page.locator(".teaching-exit").click();
      await page.waitForTimeout(config.dwell_ms);
      const state = await snapshot();
      expect(
        state.mode === "idle" &&
          !state.frozen &&
          !state.scratch &&
          !state.controls.length &&
          !state.marks.length,
        "Exit left teaching layers visible",
      );
      expect(
        state.videos.some((video) =>
          video.tracks.some((track) => track.state === "live"),
        ),
        "Exit lost the simulated live camera",
      );
      return {
        idle: true,
        scene_hidden: true,
        simulated_camera_live: true,
        observed_after_ms: config.dwell_ms,
      };
    });
    if (config.cold_review) {
      await stage("cold-reload-default-hides-saved-lesson", async () => {
        intentionalColdReload = true;
        await page.reload();
        await page
          .getByRole("button", { name: "老师讲解", exact: true })
          .waitFor({ timeout: 60000 });
        await page.waitForTimeout(config.dwell_ms);
        const state = await snapshot();
        expect(
          state.mode === "idle" &&
            !state.scratch &&
            !state.frozen &&
            !state.controls.length &&
            !state.marks.length,
          "Cold reload automatically opened a historical lesson",
        );
        return { idle: true, historical_layers_hidden: true };
      });
      await stage("explicit-cold-review-retains-full-lesson", async () => {
        const saved = await page.evaluate(async () => {
          const response = await fetch(
            localStorage.getItem("child_api") + "/v1/families/dashboard",
            {
              headers: {
                Authorization: "Bearer " + localStorage.getItem("child_token"),
              },
            },
          );
          if (!response.ok) throw new Error("Archive read failed");
          return (await response.json()).hints || [];
        });
        const expected =
          config.entry === "replay"
            ? replayBaseline.beats.map((beat) => beat.speech).join("")
            : report.timeline
                .filter(
                  (event) =>
                    event.kind === "sse-event" && event.event === "beat",
                )
                .map((event) => event.data.speech)
                .join("");
        const normalizeSpeech = (text) => text.replace(/\s/g, "");
        const archive = saved.find(
          (hint) =>
            hint.session_id === config.session_id &&
            normalizeSpeech(
              hint.teaching_scene?.beats?.map((beat) => beat.speech).join("") ||
                "",
            ) === normalizeSpeech(expected),
        );
        expect(
          archive,
          "Cold server archive lost or truncated actual lesson speech",
        );
        await page
          .getByRole("button", { name: "回看上次辅导", exact: true })
          .click();
        await page.locator(".teaching-scratch").waitFor();
        const state = await snapshot();
        expect(
          state.mode === "review" &&
            !state.frozen &&
            !state.marks.length &&
            !state.controls.length,
          "Cold review placed historical page coordinates onto the live camera or started playback",
        );
        const next = page
          .locator(".teaching-scratch")
          .getByRole("button", { name: "下一步", exact: true });
        let advances = 0;
        while (
          (await next.count()) &&
          (await next.isEnabled()) &&
          advances < 48
        ) {
          await next.click();
          advances++;
        }
        expect(
          advances < 48,
          "Scratch history did not reach its last saved step",
        );
        return {
          server_raw_beats: archive.teaching_scene.beats.length,
          server_speech_characters: [...expected].length,
          saved_speech_exact: true,
          history_advances: advances,
          original_photo_overlay_restored: false,
          automatic_playback: false,
        };
      });
      await stage("close-explicit-cold-review", async () => {
        await page.locator(".teaching-exit").click();
        await page.waitForTimeout(config.dwell_ms);
        const state = await snapshot();
        expect(
          state.mode === "idle" &&
            !state.scratch &&
            !state.controls.length &&
            !state.marks.length,
          "Closing cold review left lesson layers visible",
        );
        return { idle: true, saved_scene_hidden: true };
      });
    }
    expect(
      config.entry === "replay" ||
        report.timeline.some(
          (event) => event.kind === "sse-event" && event.event === "done",
        ),
      "No final real model SSE done event was retained",
    );
    expect(
      hints === expectedHints && report.safety_blocks.length === 0,
      "Unexpected extra or out-of-scope request",
    );
  } catch (error) {
    report.workflow_error = error.message;
    // A local audio/UI failure must not silently discard a still-streaming real
    // teacher response. Retain it independently; never synthesize playback.
    if (hints > 0)
      await page
        .waitForFunction(
          () =>
            window.__pacedAcceptanceTimeline.some(
              (event) =>
                event.kind === "sse-event" &&
                ["done", "error"].includes(event.event),
            ),
          null,
          { timeout: 180000 },
        )
        .catch(() => {});
  } finally {
    await screenshotQueue;
    const beats = report.timeline.filter(
      (event) => event.kind === "sse-event" && event.event === "beat",
    );
    report.teacher_output = {
      beats: beats.map((event) => ({ at: event.at, ...event.data })),
      speech_chars: beats.map((event) => [...event.data.speech].length),
      done_observed: report.timeline.some(
        (event) => event.kind === "sse-event" && event.event === "done",
      ),
    };
    if (config.entry === "replay") {
      const hint = report.timeline
        .filter(
          (event) =>
            event.kind === "api-result" &&
            event.path === "/v1/families/dashboard",
        )
        .flatMap((event) => event.data.hints || [])
        .find(
          (item) =>
            item.session_id === config.session_id &&
            item.teaching_scene?.beats?.length,
        );
      report.teacher_output = {
        source: "stored preceding real API/model output",
        hint_id: hint?.id,
        beats: hint?.teaching_scene?.beats || [],
        new_model_call: false,
        original_image_overlay_restored: false,
      };
    }
    report.finished_at = new Date().toISOString();
    report.teacher_requests = hints;
    report.audio_listened_by_agent = false;
    report.teacher_quality_evaluated = false;
  }
  return report;
}
