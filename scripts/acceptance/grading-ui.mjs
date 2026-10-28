import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { extractCliResult, redactEvidence, validateBrowserConfig } from './browser-contract.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const options = {};
for (let i = 2; i < process.argv.length; i++) {
  if (process.argv[i] === '--pause-focus') { options.pauseFocus = true; continue; }
  const key = { '--run-dir': 'runDir', '--state-file': 'stateFile', '--paper': 'paper' }[process.argv[i]];
  if (!key || !process.argv[i + 1]) throw new Error('Use --run-dir DIR --state-file PRIVATE_JSON --paper SIMULATED_JPG');
  options[key] = process.argv[++i];
}
const runDir = resolve(options.runDir);
const stateFile = resolve(options.stateFile);
if (!runDir.startsWith(resolve(root, 'output/acceptance') + '/') &&
    !runDir.startsWith(resolve(root, 'output/acceptance') + '\\')) throw new Error('Dedicated acceptance output required');
if (basename(stateFile) !== 'grading-ui-state.json' || dirname(stateFile) !== join(runDir, 'private'))
  throw new Error('Use only the separate private grading-ui state');
const state = JSON.parse((await readFile(stateFile, 'utf8')).replace(/^\uFEFF/, ''));
if (state.run_id !== basename(runDir)) throw new Error('State/run mismatch');
const output = join(runDir, 'grading-ui', 'attempt-' + new Date().toISOString().replace(/[:.]/g, '-'));
const config = validateBrowserConfig({ mode: 'live', output }, state);
const health = await fetch(config.base_url + '/health', { redirect: 'error' });
if (!health.ok || health.headers.get('x-k12-acceptance-run') !== basename(runDir))
  throw new Error('Isolated service identity mismatch; no browser started');
config.camera_data_url = 'data:image/jpeg;base64,' + (await readFile(resolve(options.paper))).toString('base64');
config.simulated_camera_path = resolve(options.paper);
config.run_id = basename(runDir);
config.pause_focus = options.pauseFocus === true;
const secrets = [state.parent_token, state.child_token, state.device_secret];
await mkdir(output, { recursive: true });
const nonce = randomBytes(24).toString('hex');
let lastReport;
const sink = createServer(async (request, response) => {
  if (request.url === '/' + nonce && request.method === 'GET') {
    response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    response.end(JSON.stringify(config));
  } else if (request.url === '/' + nonce + '/report' && request.method === 'POST') {
    let body = '';
    for await (const chunk of request) body += chunk;
    lastReport = redactEvidence(JSON.parse(body), secrets);
    await writeFile(join(output, 'report.json'), JSON.stringify(lastReport, null, 2));
    response.writeHead(200).end('ok');
  } else response.writeHead(404).end();
});
await new Promise((done) => sink.listen(0, '127.0.0.1', done));
const bootstrapUrl = `http://127.0.0.1:${sink.address().port}/${nonce}`;
config.sink_url = bootstrapUrl + '/report';
const session = 'grading-ui-' + randomBytes(5).toString('hex');
const npx = join(dirname(process.execPath), 'node_modules/npm/bin/npx-cli.js');
const command = process.platform === 'win32' && existsSync(npx) ? process.execPath : 'npx';
const prefix = command === process.execPath ? [npx] : [];
const cli = (args) => new Promise((done, reject) => {
  const child = spawn(command, [...prefix, '--yes', '--package', '@playwright/cli', 'playwright-cli', '-s=' + session, ...args],
    { cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let output = '';
  child.stdout.on('data', (chunk) => { output += chunk; });
  child.stderr.on('data', (chunk) => { output += chunk; });
  child.on('error', reject);
  child.on('close', (code) => done({ code, output }));
});
let finalReport;
try {
  const runtime = await readFile(join(root, 'scripts/acceptance/grading-ui-runtime.mjs'), 'utf8');
  const script = `async (page) => {\n${runtime.replace(/^export /gm, '')}\nconst config = await (await page.request.get(${JSON.stringify(bootstrapUrl)})).json();\nreturn await runGradingUi(page, config);\n}`;
  const filename = join(output, 'runner.js');
  await writeFile(filename, script);
  const opened = await cli(['open', 'about:blank']);
  if (opened.code) throw new Error(redactEvidence(opened.output, secrets));
  const result = await cli(['run-code', '--filename', filename]);
  finalReport = extractCliResult(result.output);
  await writeFile(join(output, 'cli.log'), JSON.stringify(redactEvidence(finalReport, secrets), null, 2));
} catch (error) {
  finalReport = { ...(lastReport || {}), status: 'failed', infrastructure_error: redactEvidence(error.message, secrets) };
} finally {
  await cli(['close']).catch(() => {});
  await new Promise((done) => sink.close(done));
}
finalReport = redactEvidence(finalReport, secrets);
await writeFile(join(output, 'report.json'), JSON.stringify(finalReport, null, 2));
await writeFile(join(runDir, 'grading-ui/latest.json'), JSON.stringify({ output, status: finalReport.status }, null, 2));
console.log(JSON.stringify({ status: finalReport.status, report: join(output, 'report.json'),
  stages: (finalReport.stages || []).map(({ name, status }) => ({ name, status })),
  camera: 'simulated fixture', model: 'live API', audio_listened: false }, null, 2));
process.exitCode = finalReport.status === 'passed' ? 0 : 1;
