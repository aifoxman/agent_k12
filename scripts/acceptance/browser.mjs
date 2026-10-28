import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { createHash, randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";
import {
  extractCliResult,
  finishBrowserReport,
  redactEvidence,
  validateBrowserConfig,
  liveWriteAllowed,
  teachingWriteAllowed,
  taskCycleWriteAllowed,
} from "./browser-contract.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const options = {
  mode: "controlled",
  flow: "workflow",
  output: join(
    root,
    "output/playwright/acceptance-browser-" +
      new Date().toISOString().replace(/[:.]/g, "-"),
  ),
};
for (let index = 2; index < process.argv.length; index++) {
  const flag = process.argv[index];
  if (flag === "--help") {
    console.log(
      "node scripts/acceptance/browser.mjs --mode controlled --base-url http://127.0.0.1:5186/app/ [--output DIR]\nnode scripts/acceptance/browser.mjs --mode live --state-file PRIVATE_JSON --paper SYNTHETIC_IMAGE --output DIR\nnode scripts/acceptance/browser.mjs --mode live --flow teaching-continuous --state-file PRIVATE_JSON --paper SAVED_IMAGE --output DIR [--dwell-ms 3000] [--entry new|replay] [--cold-review true]\nnode scripts/acceptance/browser.mjs --mode live --flow task-cycle --state-file PRIVATE_JSON --paper SAVED_IMAGE --output DIR [--entry new|resume]\nRequires Node/npm, a running frontend, and Playwright CLI Chromium. Controlled responses/camera/speech are simulated; live API/model output is not mocked. teaching-continuous checks default automatic steps, explicit Pause, Replay, Slower, Resume and Exit with only the saved-image camera simulated. teaching-paced retains the older manual-boundary checks for archived builds. replay uses the isolated API's previously saved real output and forbids another teacher request. task-cycle only submits/returns/resubmits/confirms its own new synthetic tasks; resume requires a previously returned task and skips the first submission/return. Neither entry grades or establishes student correctness.",
    );
    process.exit(0);
  }
  const names = {
    "--mode": "mode",
    "--base-url": "baseUrl",
    "--state-file": "stateFile",
    "--output": "output",
    "--paper": "paper",
    "--flow": "flow",
    "--dwell-ms": "dwellMs",
    "--max-steps": "maxSteps",
    "--entry": "entry",
    "--cold-review": "coldReview",
    "--question": "question",
  };
  if (!names[flag] || !process.argv[index + 1])
    throw new Error("Unknown or incomplete option: " + flag);
  options[names[flag]] = process.argv[++index];
}
const state = options.stateFile
  ? JSON.parse(
      (await readFile(resolve(options.stateFile), "utf8")).replace(
        /^\uFEFF/,
        "",
      ),
    )
  : {};
const config = validateBrowserConfig(options, state);
if (
  !["workflow", "teaching-paced", "teaching-continuous", "task-cycle"].includes(
    options.flow,
  )
)
  throw new Error(
    "Use --flow workflow, teaching-paced, teaching-continuous, or task-cycle.",
  );
config.flow = options.flow;
const teachingFlow =
  config.flow === "teaching-paced" || config.flow === "teaching-continuous";
if (
  config.flow === "task-cycle" &&
  (config.mode !== "live" || !options.paper || !state.task_ids?.length)
)
  throw new Error(
    "Task cycle requires live mode, an explicit saved --paper image, and isolated task_ids.",
  );
if (config.flow === "task-cycle") {
  config.entry = options.entry || "new";
  if (!["new", "resume"].includes(config.entry))
    throw new Error("Task cycle supports --entry new or resume.");
}
if (teachingFlow) {
  if (config.mode !== "live" || !options.paper)
    throw new Error(
      "Paced teaching acceptance requires live mode and an explicit saved --paper image.",
    );
  config.dwell_ms = Math.max(3000, Number(options.dwellMs) || 3000);
  config.max_steps = Math.min(48, Math.max(1, Number(options.maxSteps) || 48));
  config.entry = options.entry || "new";
  config.cold_review = options.coldReview === "true";
  config.question = options.question?.trim() || "";
  if (!["new", "replay"].includes(config.entry))
    throw new Error("Use --entry new or replay.");
}
if (config.mode === "live") {
  const expected =
    state.acceptance_run_id ||
    state.run_id ||
    basename(dirname(resolve(options.stateFile)));
  const health = await fetch(config.base_url.replace(/\/$/, "") + "/health", {
    redirect: "error",
  });
  if (!health.ok || health.headers.get("x-k12-acceptance-run") !== expected) {
    throw new Error(
      "Isolated API identity mismatch; no browser or API writes were started.",
    );
  }
  config.acceptance_run_id = expected;
  config.api_identity_verified = true;
}
if (options.paper) {
  const filename = resolve(options.paper);
  const image = await readFile(filename);
  const mime = /\.png$/i.test(filename) ? "image/png" : "image/jpeg";
  config.camera_data_url = `data:${mime};base64,${image.toString("base64")}`;
  config.camera_fixture = {
    filename: basename(filename),
    sha256: createHash("sha256").update(image).digest("hex"),
    input: "saved-image simulation",
    live_camera: false,
  };
}
await mkdir(config.output, { recursive: true });
const session = "acceptance-" + randomBytes(5).toString("hex");
const nonce = randomBytes(24).toString("hex");
const bootstrap = createServer((request, response) => {
  if (request.url !== "/" + nonce) {
    response.writeHead(404).end();
    return;
  }
  response.writeHead(200, {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
  });
  response.end(JSON.stringify(config));
});
await new Promise((resolve) => bootstrap.listen(0, "127.0.0.1", resolve));
const bootstrapUrl = `http://127.0.0.1:${bootstrap.address().port}/${nonce}`;
const npxScript = join(
  dirname(process.execPath),
  "node_modules/npm/bin/npx-cli.js",
);
const command =
  process.platform === "win32" && existsSync(npxScript)
    ? process.execPath
    : "npx";
const prefix = command === process.execPath ? [npxScript] : [];
const secrets = [state.parent_token, state.child_token, state.device_secret];
const cli = (args) =>
  new Promise((resolve, reject) => {
    const child = spawn(
      command,
      [
        ...prefix,
        "--yes",
        "--package",
        "@playwright/cli",
        "playwright-cli",
        "-s=" + session,
        ...args,
      ],
      { cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] },
    );
    let output = "";
    child.stdout.on("data", (chunk) => {
      output += chunk;
    });
    child.stderr.on("data", (chunk) => {
      output += chunk;
    });
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, output }));
  });
let report;
try {
  const runtime = await readFile(
    join(
      root,
      teachingFlow
        ? "scripts/acceptance/browser-teaching-runtime.mjs"
        : config.flow === "task-cycle"
          ? "scripts/acceptance/browser-task-runtime.mjs"
          : "scripts/acceptance/browser-runtime.mjs",
    ),
    "utf8",
  );
  const script = `async (page) => {\n${liveWriteAllowed.toString()}\n${teachingWriteAllowed.toString()}\n${taskCycleWriteAllowed.toString()}\n${runtime.replace(/^export /gm, "")}\nconst config = await (await page.request.get(${JSON.stringify(bootstrapUrl)})).json();\nreturn await runBrowserAcceptance(page, config, ["teaching-paced", "teaching-continuous"].includes(config.flow) ? teachingWriteAllowed : config.flow === "task-cycle" ? taskCycleWriteAllowed : liveWriteAllowed);\n}`;
  const runner = join(config.output, "browser-run.js");
  await writeFile(runner, script);
  const opened = await cli(["open", "about:blank"]);
  if (opened.code) throw new Error(redactEvidence(opened.output, secrets));
  const result = await cli(["run-code", "--filename", runner]);
  await writeFile(
    join(config.output, "browser-cli.log"),
    redactEvidence(result.output, secrets),
  );
  report = finishBrowserReport(extractCliResult(result.output));
} catch (error) {
  report = {
    schema_version: 1,
    mode: config.mode,
    status: "failed",
    passed: false,
    teacher_quality_evaluated: false,
    audio_listened_by_agent: false,
    infrastructure_error: error.message,
    stages: [],
    page_errors: [],
  };
} finally {
  await cli(["close"]).catch(() => {});
  await new Promise((resolve) => bootstrap.close(resolve));
}
report = redactEvidence(report, secrets);
await writeFile(
  join(config.output, "report.json"),
  JSON.stringify(report, null, 2),
);
console.log(
  JSON.stringify(
    {
      status: report.status,
      mode: report.mode,
      stages: report.stages.map(({ name, status }) => ({ name, status })),
      report: join(config.output, "report.json"),
      teacher_quality_evaluated: false,
      audio_listened_by_agent: false,
    },
    null,
    2,
  ),
);
process.exitCode = report.passed ? 0 : 1;
