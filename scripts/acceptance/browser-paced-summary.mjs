import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { teachingSteps } from "../../src/client/src/teaching-steps.js";

const directory = resolve(process.argv[2]);
const report = JSON.parse(await readFile(directory + "/report.json", "utf8"));
const timeline = report.timeline || [];
const beats = report.teacher_output?.beats || [];
const steps = teachingSteps(beats);
const starts = timeline.filter((event) => event.kind === "speech-start");
const ends = timeline.filter((event) => event.kind === "speech-end");
const requests = timeline.filter((event) => event.kind === "speech-request");
const dwell = report.stages.flatMap((stage) =>
  stage.checks?.manual_advance_only
    ? [{ name: stage.name, ...stage.checks }]
    : [],
);
const pageKinds = {};
for (const mark of beats.flatMap((beat) => beat.page || []))
  pageKinds[mark.kind] = (pageKinds[mark.kind] || 0) + 1;
const alignment = steps.map((step) => {
  const start = starts.find((event) => event.text === step.speech);
  const end = ends.find(
    (event) => event.text === step.speech && (!start || event.at >= start.at),
  );
  const frame =
    start &&
    timeline.find(
      (event) =>
        event.kind === "dom-frame" &&
        event.at >= start.at &&
        event.caption?.includes(step.speech),
    );
  return {
    source_beat: step.sourceBeatIndex,
    substep: step.substepIndex,
    speech: step.speech,
    logical_step_id: step.step_id,
    speech_start_at: start?.at || null,
    speech_end_at: end?.at || null,
    display_frame_at: frame?.at || null,
    frame_after_speech_start_ms:
      start && frame ? Date.parse(frame.at) - Date.parse(start.at) : null,
    expected_page_kinds: step.page.map((mark) => mark.kind),
    displayed_path_count: frame?.marks?.length ?? null,
    displayed_note_count: frame?.notes?.length ?? null,
  };
});
const summary = {
  evidence: report.evidence,
  fixture: report.camera_fixture,
  workflow_status: report.status,
  workflow_error: report.workflow_error || null,
  teacher_requests: report.teacher_requests,
  stages: report.stages.map(({ name, status, error }) => ({
    name,
    status,
    ...(error ? { error } : {}),
  })),
  manual_boundaries: dwell,
  minimum_dwell_ms: dwell.length
    ? Math.min(...dwell.map((item) => item.dwell_ms))
    : null,
  speech_observation: {
    requests: requests.length,
    starts: starts.length,
    ends: ends.length,
    rates: [...new Set(requests.map((event) => event.rate))],
    errors: timeline
      .filter((event) => event.kind === "speech-error")
      .map(({ text, error, at }) => ({ text, error, at })),
    sound_listening: "unknown; not performed",
  },
  teacher_output: {
    source: report.teacher_output?.source || "real model SSE",
    raw_beats: beats.length,
    logical_step_ids: [...new Set(beats.map((beat) => beat.step_id))],
    page_kinds: pageKinds,
    source_characters: beats.reduce(
      (sum, beat) => sum + [...beat.speech].length,
      0,
    ),
    unobserved_spoken_fragments: alignment
      .filter((item) => !item.speech_start_at)
      .map((item) => item.speech),
  },
  alignment,
  screenshots: report.screenshots.length,
  page_errors: report.page_errors,
  safety_blocks: report.safety_blocks,
  environment_changes: report.environment_changes || [],
  teacher_quality_verdict: "not assessed by this rule-based pace runner",
  quality_boundaries: [
    "A paced workflow pass does not certify mathematical accuracy, age suitability, geometry anchor alignment, or learning.",
    ...(pageKinds.line || pageKinds.arrow
      ? [
          "Actual line/arrow screenshot alignment still requires independent visual review.",
        ]
      : [
          "No original-page line/arrow in this model output; geometric annotation quality is not certified.",
        ]),
    "Saved-image simulation is not a new live physical worksheet or Pad camera test; speech lifecycle is not human listening.",
  ],
};
await writeFile(
  directory + "/paced-summary.json",
  JSON.stringify(summary, null, 2),
);
console.log(
  JSON.stringify({
    status: summary.workflow_status,
    teacher_requests: summary.teacher_requests,
    raw_beats: beats.length,
    page_kinds: pageKinds,
    boundaries: dwell.length,
    minimum_dwell_ms: summary.minimum_dwell_ms,
    speech_starts: starts.length,
    unobserved_fragments:
      summary.teacher_output.unobserved_spoken_fragments.length,
    screenshots: summary.screenshots,
  }),
);
