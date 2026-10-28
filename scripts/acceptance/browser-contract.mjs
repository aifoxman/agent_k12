import { resolve } from "node:path";

export function isLoopbackUrl(value) {
  try {
    const url = new URL(value);
    return (
      ["http:", "https:"].includes(url.protocol) &&
      ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)
    );
  } catch {
    return false;
  }
}

export function validateBrowserConfig(options, state = {}) {
  if (!["controlled", "live"].includes(options.mode))
    throw new Error("Use --mode controlled or --mode live.");
  const frontend =
    state.frontend_url || options.baseUrl || "http://127.0.0.1:5186/app/";
  if (!isLoopbackUrl(frontend))
    throw new Error("Acceptance requires an explicit loopback frontend.");
  const api =
    options.mode === "controlled"
      ? `${new URL(frontend).origin}/acceptance-api`
      : state.base_url;
  if (options.mode === "live") {
    if (state.synthetic !== true || !isLoopbackUrl(api))
      throw new Error(
        "Live writes require synthetic:true and a loopback API in the private state file.",
      );
    if (new URL(api).port === "8003")
      throw new Error("The existing 8003 service is outside acceptance scope.");
    for (const field of [
      "parent_token",
      "child_token",
      "session_id",
      "family_id",
    ]) {
      if (!state[field])
        throw new Error(`Private live state is missing ${field}.`);
    }
  }
  return {
    ...state,
    mode: options.mode,
    frontend_url: frontend,
    base_url: api,
    output: resolve(options.output),
    synthetic: true,
  };
}

export function liveWriteAllowed(method, pathname) {
  if (/\/data\/delete|\/parent-review(?:\/|$)/.test(pathname)) return false;
  if (["GET", "HEAD", "OPTIONS"].includes(method)) return true;
  return /^\/v1\/(?:hints(?:\/stream|\/[^/]+\/attempt)?|setup\/check|realtime\/ticket|sessions(?:\/[^/]+(?:\/(?:timer(?:\/events)?|page-prep|pages\/scan|correction-request|correction-access|corrections|observations|reflections|finish))?)?|corrections\/[^/]+\/checks\/\d+\/recheck|tasks\/[^/]+\/(?:submissions|reminders))$/.test(
    pathname,
  );
}

export function teachingWriteAllowed(method, pathname, sessionId) {
  if (
    /correction|parent-review|data\/delete|submissions|voice\/transcribe/.test(
      pathname,
    )
  )
    return false;
  if (["GET", "HEAD", "OPTIONS"].includes(method)) return true;
  if (
    method === "POST" &&
    [
      "/v1/hints/stream",
      "/v1/audio/speech",
      "/v1/setup/check",
      "/v1/realtime/ticket",
    ].includes(pathname)
  )
    return true;
  const scope = "/v1/sessions/" + encodeURIComponent(sessionId);
  return (
    (method === "PATCH" && pathname === scope) ||
    (method === "POST" &&
      [scope + "/timer/events", scope + "/page-prep"].includes(pathname))
  );
}

export function taskCycleWriteAllowed(
  method,
  pathname,
  sessionId,
  taskIds = [],
) {
  if (
    /correction|parent-review|data\/delete|hints|voice\/transcribe|\/grade$|feedback-draft/.test(
      pathname,
    )
  )
    return false;
  if (["GET", "HEAD", "OPTIONS"].includes(method)) return true;
  if (
    method === "POST" &&
    ["/v1/audio/speech", "/v1/setup/check", "/v1/realtime/ticket"].includes(
      pathname,
    )
  )
    return true;
  const session = "/v1/sessions/" + encodeURIComponent(sessionId);
  if (method === "PATCH" && pathname === session) return true;
  if (
    method === "POST" &&
    [
      session + "/timer/events",
      session + "/page-prep",
      session + "/pages/scan",
    ].includes(pathname)
  )
    return true;
  return taskIds.some((id) => {
    const task = "/v1/tasks/" + encodeURIComponent(id);
    return (
      (method === "PATCH" && pathname === task) ||
      (method === "POST" &&
        [
          task + "/submissions",
          task + "/reviews",
          task + "/reminders",
        ].includes(pathname))
    );
  });
}

export function redactEvidence(value, secrets = []) {
  if (typeof value === "string") {
    let result = value;
    for (const secret of secrets.filter(Boolean))
      result = result.split(secret).join("[REDACTED]");
    return result.replace(/Bearer\s+[\w.\-]+/gi, "Bearer [REDACTED]");
  }
  if (Array.isArray(value))
    return value.map((item) => redactEvidence(item, secrets));
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [
      key,
      /token|secret|password|authorization|cookie|api_key|ticket/i.test(key)
        ? "[REDACTED]"
        : redactEvidence(item, secrets),
    ]),
  );
}

export function extractCliResult(output) {
  const match = output.match(/### Result\r?\n([\s\S]*?)(?:\r?\n### |$)/);
  if (!match)
    throw new Error(
      "Playwright CLI did not return structured acceptance evidence.",
    );
  return JSON.parse(match[1].trim());
}

export function finishBrowserReport(report) {
  const failed = report.stages.filter((stage) => stage.status === "failed");
  const browserErrors = report.page_errors.length;
  const blocked = report.safety_blocks?.length || 0;
  const incomplete = !report.stages.length || Boolean(report.workflow_error);
  return {
    ...report,
    status:
      failed.length || browserErrors || blocked || incomplete
        ? "failed"
        : "passed",
    passed: !failed.length && !browserErrors && !blocked && !incomplete,
    teacher_quality_evaluated: false,
    audio_listened_by_agent: false,
  };
}
