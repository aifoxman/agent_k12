import test from "node:test";
import assert from "node:assert/strict";
import {
  extractCliResult,
  finishBrowserReport,
  liveWriteAllowed,
  teachingWriteAllowed,
  taskCycleWriteAllowed,
  redactEvidence,
  validateBrowserConfig,
} from "./browser-contract.mjs";

test("task cycle scopes real writes to its own tasks and never grades or changes another family", () => {
  const allowed = (method, path) =>
    taskCycleWriteAllowed(method, path, "own-session", ["own-task"]);
  assert.equal(allowed("POST", "/v1/tasks/own-task/submissions"), true);
  assert.equal(allowed("POST", "/v1/tasks/own-task/reviews"), true);
  assert.equal(allowed("POST", "/v1/sessions/own-session/pages/scan"), true);
  assert.equal(allowed("POST", "/v1/tasks/other-task/reviews"), false);
  assert.equal(allowed("PATCH", "/v1/sessions/other-session"), false);
  assert.equal(
    allowed("POST", "/v1/tasks/own-task/submissions/attempt/grade"),
    false,
  );
  assert.equal(allowed("POST", "/v1/hints/stream"), false);
  assert.equal(
    allowed("POST", "/v1/submission-pages/page/checks/0/parent-review"),
    false,
  );
  assert.equal(allowed("POST", "/v1/data/delete"), false);
});

test("live writes require an isolated synthetic loopback state and never use the old service", () => {
  const options = { mode: "live", output: "output/test" };
  const state = {
    synthetic: true,
    frontend_url: "http://127.0.0.1:5186/app/",
    base_url: "http://127.0.0.1:8016",
    parent_token: "parent",
    child_token: "child",
    session_id: "session",
    family_id: "family",
  };
  assert.equal(validateBrowserConfig(options, state).base_url, state.base_url);
  assert.throws(
    () => validateBrowserConfig(options, { ...state, synthetic: false }),
    /synthetic/,
  );
  assert.throws(
    () =>
      validateBrowserConfig(options, {
        ...state,
        base_url: "https://real.example",
      }),
    /loopback/,
  );
  assert.throws(
    () =>
      validateBrowserConfig(options, {
        ...state,
        base_url: "http://127.0.0.1:8003",
      }),
    /8003/,
  );
});

test("live API guard allows intended test actions and denies deletion and parent calibration", () => {
  assert.equal(liveWriteAllowed("POST", "/v1/hints/stream"), true);
  assert.equal(
    liveWriteAllowed("PATCH", "/v1/sessions/example/correction-access"),
    true,
  );
  assert.equal(
    liveWriteAllowed("POST", "/v1/corrections/example/checks/0/recheck"),
    true,
  );
  assert.equal(liveWriteAllowed("POST", "/v1/data/delete"), false);
  assert.equal(
    liveWriteAllowed("PATCH", "/v1/corrections/example/checks/0/parent-review"),
    false,
  );
  assert.equal(liveWriteAllowed("POST", "/v1/auth/register"), false);
});

test("logs redact nested credentials and token values in free text", () => {
  assert.deepEqual(
    redactEvidence(
      {
        token: "secret",
        nested: { device_secret: "device" },
        message: "Bearer abc.def and secret",
      },
      ["secret"],
    ),
    {
      token: "[REDACTED]",
      nested: { device_secret: "[REDACTED]" },
      message: "Bearer [REDACTED] and [REDACTED]",
    },
  );
});

test("paced teaching scope allows one isolated session and no grading or parent writes", () => {
  assert.equal(
    teachingWriteAllowed("POST", "/v1/hints/stream", "session"),
    true,
  );
  assert.equal(
    teachingWriteAllowed("POST", "/v1/audio/speech", "session"),
    true,
  );
  assert.equal(
    teachingWriteAllowed(
      "POST",
      "/v1/sessions/session/timer/events",
      "session",
    ),
    true,
  );
  assert.equal(
    teachingWriteAllowed("PATCH", "/v1/sessions/session", "session"),
    true,
  );
  assert.equal(
    teachingWriteAllowed("PATCH", "/v1/sessions/another", "session"),
    false,
  );
  for (const path of [
    "/v1/corrections",
    "/v1/sessions/session/corrections",
    "/v1/sessions/session/correction-access",
    "/v1/corrections/x/checks/0/parent-review",
    "/v1/data/delete",
    "/v1/tasks/x/submissions",
  ])
    assert.equal(teachingWriteAllowed("POST", path, "session"), false);
});

test("structured failures stay failures and simulations never certify teacher or audio quality", () => {
  const report = finishBrowserReport({
    stages: [{ name: "hint", status: "failed" }],
    page_errors: [],
  });
  assert.equal(report.passed, false);
  assert.equal(report.teacher_quality_evaluated, false);
  assert.equal(report.audio_listened_by_agent, false);
  assert.deepEqual(
    extractCliResult(
      '### Result\n{"passed":true}\n### Ran Playwright code\nnot json',
    ),
    { passed: true },
  );
});
