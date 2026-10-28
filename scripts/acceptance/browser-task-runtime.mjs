// Real React task lifecycle using one saved-photo camera; API/model output is never mocked.
export async function runBrowserAcceptance(page, config, writeAllowed) {
  const report = {
    schema_version: 1,
    mode: "live",
    flow: "task-cycle",
    entry: config.entry || "new",
    started_at: new Date().toISOString(),
    acceptance_run_id: config.acceptance_run_id,
    isolated_api_identity_verified: config.api_identity_verified,
    camera_fixture: config.camera_fixture,
    evidence: {
      frontend: "production React",
      api: "isolated real API",
      model: "real visual page scan and submission completeness assessment",
      camera: "saved-photo simulated camera",
      microphone: "not exercised",
      speech: "unmodified browser lifecycle observation; no listening claim",
    },
    stages: [],
    screenshots: [],
    timeline: [],
    console: [],
    page_errors: [],
    network_failures: [],
    safety_blocks: [],
    limitations: [
      "The supplied sheet has no student answer. Reusing it does not simulate a correction or prove learning.",
      "The final parent acceptance tests an authorized UI decision only; it is not a mathematical correctness verdict.",
      "No full grading, parent check editing, feedback drafting, device, original family, or microphone use.",
    ],
  };
  const resuming = config.entry === "resume";
  let imageIndex = 0;
  const expect = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  const watch = async (target, role) => {
    target.on("console", (message) =>
      report.console.push({
        role,
        at: new Date().toISOString(),
        type: message.type(),
        text: message.text(),
      }),
    );
    target.on("pageerror", (error) =>
      report.page_errors.push({
        role,
        at: new Date().toISOString(),
        message: error.message,
      }),
    );
    target.on("requestfailed", (request) =>
      report.network_failures.push({
        role,
        path: new URL(request.url()).pathname,
        error: request.failure()?.errorText,
      }),
    );
    target.on("response", async (response) => {
      const url = new URL(response.url());
      if (!url.pathname.startsWith("/v1/")) return;
      const item = {
        kind: "api-result",
        role,
        at: new Date().toISOString(),
        path: url.pathname,
        status: response.status(),
      };
      try {
        if ((response.headers()["content-type"] || "").includes("json"))
          item.data = await response.json();
      } catch (error) {
        item.consumption_error = error.message;
      }
      report.timeline.push(item);
    });
    await target.exposeFunction("__taskAcceptanceEmit", (event) =>
      report.timeline.push({ ...event, role }),
    );
  };
  await page.context().route("**/v1/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    if (
      url.origin !== new URL(config.base_url).origin ||
      !writeAllowed(
        request.method(),
        url.pathname,
        config.session_id,
        config.task_ids,
      )
    ) {
      report.safety_blocks.push({
        method: request.method(),
        path: url.pathname,
      });
      return route.abort("blockedbyclient");
    }
    return route.continue();
  });
  await page.context().addInitScript(
    ({ config }) => {
      for (const role of ["parent", "child"]) {
        localStorage.setItem(role + "_token", config[role + "_token"]);
        localStorage.setItem(role + "_api", config.base_url);
      }
      const emit = (kind, data = {}) =>
        window
          .__taskAcceptanceEmit?.({
            kind,
            at: new Date().toISOString(),
            mono_ms: performance.now(),
            ...data,
          })
          .catch(() => {});
      navigator.mediaDevices.getUserMedia = async (constraints) => {
        if (!constraints.video)
          throw new DOMException("Microphone not exercised", "NotAllowedError");
        const image = new Image();
        image.src = config.camera_data_url;
        await image.decode();
        const canvas = document.createElement("canvas");
        canvas.width = image.naturalWidth;
        canvas.height = image.naturalHeight;
        canvas.getContext("2d").drawImage(image, 0, 0);
        emit("camera-start", {
          simulation: true,
          width: canvas.width,
          height: canvas.height,
        });
        return canvas.captureStream(5);
      };
      if (window.speechSynthesis) {
        const speak = window.speechSynthesis.speak.bind(window.speechSynthesis);
        window.speechSynthesis.speak = (utterance) => {
          emit("speech-request", {
            text: utterance.text,
            rate: utterance.rate,
          });
          for (const type of ["start", "end", "error"])
            utterance.addEventListener(type, (event) =>
              emit("speech-" + type, {
                text: utterance.text,
                error: event.error || null,
              }),
            );
          return speak(utterance);
        };
      }
      for (const name of ["paper-speech", "paper-speech-progress"])
        window.addEventListener(name, (event) =>
          emit(name, { detail: event.detail }),
        );
    },
    { config },
  );
  await watch(page, "child");
  await page.setViewportSize({ width: 1366, height: 900 });
  let active = page;
  const screenshot = async (name) => {
    const file = String(++imageIndex).padStart(3, "0") + "-" + name + ".png";
    await active.screenshot({ path: config.output + "/" + file });
    report.screenshots.push({ name, path: file, at: new Date().toISOString() });
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
  const app = (mode) => {
    const url = new URL(config.frontend_url);
    url.searchParams.set("mode", mode);
    return url.href;
  };
  const readDashboard = (target) =>
    target.evaluate(async () => {
      const role = new URLSearchParams(location.search).get("mode");
      const response = await fetch(
        localStorage.getItem(role + "_api") + "/v1/families/dashboard",
        {
          headers: {
            Authorization: "Bearer " + localStorage.getItem(role + "_token"),
          },
        },
      );
      if (!response.ok)
        throw new Error("Dashboard read failed: " + response.status);
      return response.json();
    });
  const pollTask = async (target, status) => {
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      const data = await readDashboard(target),
        task = data.tasks.find((item) => item.id === config.task_ids[0]);
      if (task?.status === status) return task;
      await target.waitForTimeout(500);
    }
    throw new Error("Task did not reach " + status);
  };
  let title, parent, firstSubmission;
  const childTask = () =>
    page
      .locator(".child-task")
      .filter({ has: page.locator("strong", { hasText: title }) });
  const parentTask = () =>
    parent
      .locator(".task-row-wrap")
      .filter({ has: parent.locator(".task-row strong", { hasText: title }) });
  const expandChildTasks = async () => {
    const toggle = page.locator(".task-panel-toggle");
    if ((await toggle.getAttribute("aria-expanded")) === "false")
      await toggle.click();
  };
  const submit = async (attempt) => {
    active = page;
    await page.bringToFront();
    await expandChildTasks();
    await childTask()
      .getByRole("button", { name: "提交作业", exact: true })
      .click();
    const dialog = page.getByRole("dialog", { name: "拍摄作业" });
    await dialog.waitFor();
    const scanStarted = Date.now();
    while (!(await dialog.locator(".batch-page").count())) {
      const scan = report.timeline
        .filter(
          (event) =>
            event.kind === "api-result" &&
            event.role === "child" &&
            event.path.endsWith("/pages/scan"),
        )
        .at(-1);
      if (scan?.data?.pages?.length === 0)
        throw new Error(
          "Actual page scan rejected the saved input: " +
            (scan.data.reason || "No readable page"),
        );
      expect(
        Date.now() - scanStarted < 120000,
        "Actual page scan did not produce a captured page within 120 seconds",
      );
      await page.waitForTimeout(250);
    }
    await screenshot("attempt-" + attempt + "-actual-captured-paper");
    const count = await dialog.locator(".batch-page").count();
    expect(count > 0, "No real scanned page");
    const confirm = dialog.getByRole("button", {
      name: count === 1 ? "确认提交这张" : new RegExp("拍完了，提交给家长"),
    });
    await confirm.click();
    if (count > 1)
      await dialog
        .getByRole("button", { name: "确认提交", exact: true })
        .click();
    await dialog.waitFor({ state: "hidden", timeout: 120000 });
    const task = await pollTask(page, "awaiting_parent");
    expect(task.latest_submission_id, "Submission not persisted");
    return { task, page_count: count };
  };
  const openEvidence = async (attempt = 1) => {
    active = parent;
    await parent.bringToFront();
    await pollTask(parent, "awaiting_parent");
    const toggle = parentTask().getByRole("button", {
      name: /^(查看提交与审核|收起提交依据)$/,
    });
    await toggle.waitFor({ timeout: 30000 });
    if ((await toggle.innerText()).trim() === "查看提交与审核")
      await toggle.click();
    const currentAttempt = parentTask()
      .locator("section.task-attempt")
      .filter({ hasText: `第 ${attempt} 次提交` });
    await currentAttempt.waitFor({ timeout: 30000 });
    await currentAttempt
      .locator(".task-submission-page img")
      .first()
      .waitFor({ timeout: 30000 });
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      const text = await currentAttempt.innerText();
      if (!text.includes("正在核对清晰度与明显遗漏")) return text;
      await parent.waitForTimeout(750);
    }
    throw new Error("Real submission completeness assessment did not settle");
  };
  try {
    await stage(
      resuming ? "resume-returned-isolated-task" : "new-isolated-task-family",
      async () => {
        await page.goto(app("child"));
        await page
          .getByRole("button", { name: "老师讲解", exact: true })
          .waitFor({ timeout: 30000 });
        const data = await readDashboard(page);
        expect(data.family.id === config.family_id, "Wrong isolated family");
        const task = data.tasks.find((task) => task.id === config.task_ids[0]);
        title = task?.title;
        expect(title, "Missing isolated task");
        if (resuming) {
          expect(
            task.status === "needs_revision",
            "Resume requires an already returned task",
          );
          expect(
            task.latest_submission_id,
            "Resume requires an existing submission",
          );
          firstSubmission = task.latest_submission_id;
        }
        parent = await page.context().newPage();
        await watch(parent, "parent");
        await parent.setViewportSize({ width: 1366, height: 900 });
        await parent.goto(app("parent"));
        return {
          new_synthetic_family: !resuming,
          resumed_existing_synthetic_family: resuming,
          first_submission_and_return_skipped: resuming,
          existing_task_status: task.status,
          existing_submission_id: task.latest_submission_id || null,
          worksheet_answer_present: false,
          task_id: config.task_ids[0],
        };
      },
    );
    if (!resuming) {
      await stage("child-first-real-photo-submission", async () => {
        const result = await submit(1);
        firstSubmission = result.task.latest_submission_id;
        return result;
      });
      await stage("parent-reads-real-assessment-and-returns", async () => {
        const text = await openEvidence();
        await screenshot("first-attempt-parent-assessment");
        await parentTask()
          .getByLabel("退回原因", { exact: true })
          .fill("保存照片未见孩子作答；本次为隔离验收，不能作为完成依据。");
        await parentTask()
          .getByLabel("孩子可见的补做说明（家长确认后发送）", { exact: true })
          .fill(
            "这张测试照片没有你的解答。真实作业请补写过程后再拍照；本次测试将复用照片，只检查提交是否可达。",
          );
        await parentTask()
          .getByRole("button", { name: "退回补做", exact: true })
          .click();
        return {
          task: await pollTask(parent, "needs_revision"),
          actual_assessment_text: text,
        };
      });
    }
    await stage(
      resuming
        ? "resume-returned-task-and-resubmit"
        : "child-sees-return-and-resubmits-same-fixture",
      async () => {
        active = page;
        await page.bringToFront();
        const returned = await pollTask(page, "needs_revision");
        await expandChildTasks();
        await childTask()
          .getByRole("button", { name: "提交作业", exact: true })
          .waitFor();
        const notice = page
          .locator(".task-reminder-banner")
          .filter({ hasText: title });
        if (!resuming) await notice.waitFor({ timeout: 30000 });
        const returnNoticeVisible = await notice.isVisible();
        if (returnNoticeVisible) {
          const noticeText = await notice.innerText();
          expect(
            noticeText.includes("家长请你补做"),
            "Wrong visible task reminder",
          );
          expect(
            !!returned.rejection_reason &&
              noticeText.includes(returned.rejection_reason),
            "Visible return notice did not include this task's actual parent instruction",
          );
        }
        await screenshot("child-reads-parent-return");
        const result = await submit(2);
        expect(
          result.task.latest_submission_id !== firstSubmission,
          "Second submission reused first attempt",
        );
        return {
          ...result,
          returned_status_verified: true,
          resubmission_button_visible: true,
          return_notice_visible_this_entry: returnNoticeVisible,
          prior_return_evidence_required: resuming && !returnNoticeVisible,
          same_saved_photo: true,
          no_student_answer_added: true,
        };
      },
    );
    await stage("parent-confirms-task-authority-only", async () => {
      // The card may remain expanded after its refresh; reuse it rather than closing the evidence.
      active = parent;
      await parent.bringToFront();
      const text = await openEvidence(2);
      await screenshot("second-attempt-parent-evidence");
      await parentTask()
        .getByRole("button", { name: "确认完成", exact: true })
        .click();
      return {
        task: await pollTask(parent, "confirmed"),
        actual_assessment_text: text,
        interpretation:
          "Synthetic parent decision only; unchanged unanswered paper is not a correct homework result",
      };
    });
    await stage("child-observes-parent-confirmation", async () => {
      active = page;
      await page.bringToFront();
      await pollTask(page, "confirmed");
      await expandChildTasks();
      await childTask()
        .locator(".task-circle.confirmed")
        .waitFor({ timeout: 30000 });
      expect(
        (await childTask()
          .getByRole("button", { name: "提交作业", exact: true })
          .count()) === 0,
        "Confirmed task still offers submission",
      );
      return {
        confirmed_visible: true,
        confirmation_cue:
          "visible confirmed circle and no submission button; own API status confirmed",
        student_correctness: "not established",
      };
    });
  } catch (error) {
    report.workflow_error = error.message;
  }
  report.finished_at = new Date().toISOString();
  report.audio_listened_by_agent = false;
  report.teacher_quality_evaluated = false;
  return report;
}
