// Playwright CLI runtime. Only camera input is simulated; API/model/voice are real.
export async function runGradingUi(page, config) {
  const report = { schema_version: 1, status: 'running', started_at: new Date().toISOString(),
    run_id: config.run_id, synthetic_family: true, frontend: 'real React UI', api: 'isolated live API',
    camera: 'explicit synthetic answer-sheet canvas', camera_live: false, model_mocked: false,
    speech_callbacks_mocked: false, audio_listened_by_agent: false,
    stages: [], screenshots: [], events: [], page_errors: [], safety_blocks: [], limitations: [
      'No physical Pad camera, actual child handwriting, microphone or human listening is validated.',
      'Parent calibration preserves a correct independent verdict and adds a review; it is not an AI verdict reversal.',
    ] };
  const corrections = new Set();
  let holdDashboards = false;
  const pendingDashboards = [];
  const apiOrigin = new URL(config.base_url).origin;
  const scope = '/v1/sessions/' + config.session_id;
  const allowed = (method, path) => {
    if (/data\/delete|auth\/register|families\/pair/.test(path)) return false;
    if (['GET', 'HEAD', 'OPTIONS'].includes(method)) return true;
    if (method === 'POST' && ['/v1/setup/check', '/v1/realtime/ticket', '/v1/audio/speech'].includes(path)) return true;
    if (method === 'PATCH' && path === scope) return true;
    if (['POST', 'PATCH'].includes(method) && [scope + '/correction-request', scope + '/correction-access',
      scope + '/corrections', scope + '/timer/events', scope + '/page-prep', scope + '/pages/scan', scope + '/finish'].includes(path)) return true;
    for (const id of corrections) {
      if (method === 'PATCH' && path === '/v1/corrections/' + id) return true;
      if (new RegExp('^/v1/corrections/' + id + '/checks/\\d+/(recheck|parent-review)$').test(path)) return true;
    }
    return method === 'POST' && /^\/v1\/tasks\/[^/]+\/reminders$/.test(path);
  };
  await page.context().route('**/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== apiOrigin || !allowed(request.method(), url.pathname)) {
      report.safety_blocks.push({ method: request.method(), path: url.pathname, origin: url.origin });
      await route.abort('blockedbyclient');
    } else if (holdDashboards && request.method() === 'GET' && url.pathname === '/v1/families/dashboard') {
      pendingDashboards.push(route);
    } else await route.continue();
  });
  const sanitized = (value) => {
    if (Array.isArray(value)) return value.map(sanitized);
    if (!value || typeof value !== 'object') return value;
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
      /token|secret|password|authorization|cookie|api_key|ticket/i.test(key) ? '[REDACTED]' : sanitized(item)]));
  };
  const checkpoint = () => page.request.post(config.sink_url, { data: sanitized(report) });
  await page.context().addInitScript(({ config }) => {
    for (const role of ['child', 'parent']) {
      localStorage.setItem(role + '_token', config[role + '_token']);
      localStorage.setItem(role + '_api', config.base_url);
    }
    window.__gradingCameraStreams = [];
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      if (constraints.audio && !constraints.video) throw new DOMException('No simulated microphone', 'NotAllowedError');
      const image = new Image();
      image.src = config.camera_data_url;
      await image.decode();
      const canvas = document.createElement('canvas');
      canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
      canvas.getContext('2d').drawImage(image, 0, 0);
      const stream = canvas.captureStream(5);
      window.__gradingCameraStreams.push(stream);
      return stream;
    };
  }, { config });
  const observe = async (target, role) => {
    target.on('pageerror', (error) => report.page_errors.push({ role, message: error.message }));
    target.on('response', async (response) => {
      const url = new URL(response.url());
      if (url.origin !== apiOrigin || !url.pathname.startsWith('/v1/')) return;
      const entry = { at: new Date().toISOString(), role, path: url.pathname,
        method: response.request().method(), status: response.status() };
      try {
        if ((response.headers()['content-type'] || '').includes('json')) {
          entry.data = await response.json();
          if (url.pathname === scope + '/corrections' && entry.data.id) corrections.add(entry.data.id);
        }
      } catch { entry.body_unavailable = true; }
      report.events.push(entry);
    });
  };
  await observe(page, 'child');
  const url = (role) => { const result = new URL(config.frontend_url); result.searchParams.set('mode', role); return result.href; };
  let active = page;
  const shot = async (name, target = active) => {
    const filename = String(report.screenshots.length + 1).padStart(3, '0') + '-' + name + '.png';
    await target.screenshot({ path: config.output + '/' + filename, fullPage: true });
    report.screenshots.push({ name, path: filename, at: new Date().toISOString() });
  };
  const stage = async (name, action) => {
    const entry = { name, status: 'running', started_at: new Date().toISOString() };
    report.stages.push(entry); await checkpoint();
    try {
      entry.checks = await action(); await shot(name); entry.status = 'passed';
    } catch (error) {
      entry.status = 'failed'; entry.error = error.message;
      await shot(name + '-failed').catch(() => {}); throw error;
    } finally { entry.finished_at = new Date().toISOString(); await checkpoint(); }
  };
  const expect = (condition, message) => { if (!condition) throw new Error(message); };
  let parent;
  try {
    await stage('child-active-before-approval', async () => {
      await page.setViewportSize({ width: 1366, height: 960 });
      await page.goto(url('child'));
      await page.locator('.camera-controls button').filter({ hasText: /申请批改|批改作业/ }).waitFor();
      await page.waitForFunction(() => [...document.querySelectorAll('video')].some((video) => video.videoWidth > 0));
      for (let index = 0; index < 4; index++) {
        const notice = page.getByRole('button', { name: '知道了', exact: true });
        if (!(await notice.isVisible())) break;
        await notice.click();
        await page.waitForTimeout(150);
      }
      return { camera: 'synthetic sheet rendered by actual React camera view' };
    });
    await stage('child-request-awaits-parent', async () => {
      await page.locator('.camera-controls button').filter({ hasText: /申请批改|批改作业/ }).click();
      await page.getByRole('dialog', { name: '拍摄作业' }).waitFor();
      await shot('capture-preview');
      await page.getByRole('button', { name: '确认批改这张', exact: true }).waitFor({ timeout: 90000 });
      await page.getByRole('button', { name: '确认批改这张', exact: true }).click();
      await page.getByText('已请家长授权；获批后将自动批改已确认的截图').waitFor();
      expect(!report.events.some((event) => event.path === scope + '/corrections'), 'Grading ran before approval');
      return { no_grading_before_parent_action: true };
    });
    await stage('parent-before-authorization', async () => {
      parent = await page.context().newPage(); active = parent;
      await observe(parent, 'parent');
      await parent.setViewportSize({ width: 1280, height: 1000 });
      await parent.goto(url('parent'));
      await parent.getByRole('button', { name: '允许 10 分钟', exact: true }).waitFor();
      return { explicit_authorize_button_visible: true };
    });
    await stage('parent-authorizes', async () => {
      await parent.getByRole('button', { name: '允许 10 分钟', exact: true }).click();
      await parent.getByText('已允许孩子在 10 分钟内批改作业', { exact: true }).waitFor();
      return { real_parent_button_used: true };
    });
    await stage('child-live-grading-result', async () => {
      active = page; await page.bringToFront();
      await page.locator('.child-correction-card .correction-result').waitFor({ timeout: 180000 });
      expect(corrections.size === 1, 'Expected one real correction result');
      return { response_source: 'live model', result_visible: true };
    });
    await stage('child-expands-written-result', async () => {
      const review = page.locator('.child-correction-card .correction-written-review');
      if (!(await review.getAttribute('open'))) await review.locator('summary').click();
      const rows = page.locator('.child-correction-card .correction-check');
      await rows.first().waitFor();
      expect(await rows.count() === 3, 'Expected all three simulated questions');
      return { question_count: await rows.count(), expanded_text: await review.innerText() };
    });
    if (!config.pause_focus) {
    await stage('parent-opens-result-and-wrong-answer', async () => {
      active = parent; await parent.bringToFront(); await parent.reload();
      await parent.getByRole('button', { name: '每日回顾', exact: true }).click();
      const toggle = parent.locator('.correction-review-toggle').first(); await toggle.waitFor();
      if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
      await parent.locator('.correction-review .correction-paper img').waitFor();
      const markers = parent.locator('.correction-review .paper-mark-ink');
      expect(await markers.count() >= 2, 'Expected located answer markers');
      await markers.nth(1).click();
      await parent.getByRole('button', { name: 'AI 复检本题', exact: true }).waitFor();
      return { selected_question: 'second simulated arithmetic question' };
    });
    await stage('parent-real-ai-recheck', async () => {
      const result = parent.waitForResponse((response) => response.url().endsWith('/checks/1/recheck') && response.status() === 200,
        { timeout: 180000 });
      await parent.getByRole('button', { name: 'AI 复检本题', exact: true }).click();
      const body = await (await result).json();
      expect(body.recheck?.answer === '5' && body.recheck?.status === 'check_again' && /4/.test(body.recheck?.reference_answer || ''),
        'Independent recheck did not match arithmetic truth');
      return { real_model_recheck: true, original_review_preserved: Boolean(body.check?.original_review) };
    });
    await stage('parent-saves-calibration', async () => {
      await parent.getByRole('button', { name: '家长校对／更正这题', exact: true }).click();
      await parent.getByLabel('纸上实际答案', { exact: true }).fill('5');
      await parent.getByLabel('更正说明', { exact: true }).fill('模拟家长已对照题干核对：24除以6等于4，纸面填写5，需要订正。');
      const saved = parent.waitForResponse((response) => response.url().endsWith('/checks/1/parent-review') && response.status() === 200);
      await parent.getByRole('button', { name: '保存家长校对', exact: true }).click();
      const body = await (await saved).json();
      expect(body.check?.parent_review?.revision === 1 && body.check?.original_review && body.check?.ai_review,
        'Saved review did not preserve provenance');
      await parent.getByText('家长校对结果 · 第 1 次', { exact: true }).waitFor();
      return { saved_revision: 1, original_ai_retained: true, verdict_reversed: false };
    });
    }
    await stage('child-pauses-camera', async () => {
      active = page; await page.bringToFront();
      await page.getByRole('button', { name: '关闭批改结果', exact: true }).click();
      // Delay real dashboard requests, without changing their responses, to
      // prove local pause cannot depend on a later authorization refresh.
      holdDashboards = true;
      await page.waitForTimeout(200);
      const dashboardBefore = report.events.filter((event) => event.role === 'child' && event.path === '/v1/families/dashboard').length;
      await page.locator('.camera-controls button').filter({ hasText: '暂停' }).click();
      await page.getByRole('button', { name: '继续作业', exact: true }).waitFor();
      const tracks = await page.evaluate(() => window.__gradingCameraStreams.flatMap((stream) => stream.getTracks().map((track) => track.readyState)));
      expect(tracks.length > 0 && tracks.every((track) => track === 'ended'), 'Pause left a synthetic camera track live');
      const cached = await page.locator('.child-correction-card,.child-correction-history,.paper-mark-ink').count();
      const dashboardAfter = report.events.filter((event) => event.role === 'child' && event.path === '/v1/families/dashboard').length;
      expect(cached === 0, 'Pause still exposes cached correction cards or marks');
      expect(dashboardBefore === dashboardAfter, 'Pause assertion was reached only after a new dashboard response');
      await shot('pause-immediate-before-dashboard');
      holdDashboards = false;
      for (const route of pendingDashboards.splice(0)) await route.continue();
      return { camera_tracks_ended: true, correction_cards_and_marks: cached,
        dashboard_responses_before_assertion: dashboardAfter - dashboardBefore,
        network_condition: 'Dashboard requests briefly held, actual response bodies unchanged' };
    });
    if (config.pause_focus) {
      await stage('reload-paused-hides-cached-results', async () => {
        await page.reload();
        await page.getByRole('button', { name: '继续作业', exact: true }).waitFor();
        expect(await page.locator('.child-correction-card,.child-correction-history,.paper-mark-ink').count() === 0,
          'Reload restored unauthorized cached results');
        return { old_correction_hidden_after_reload: true };
      });
      await stage('resume-requires-real-visual-check', async () => {
        await page.getByRole('button', { name: '继续作业', exact: true }).click();
        const check = page.getByRole('button', { name: '检查纸面', exact: true });
        await check.waitFor();
        const proof = page.waitForResponse((response) => response.url().endsWith('/v1/setup/check') && response.status() === 200,
          { timeout: 120000 });
        await check.click(); await proof;
        await page.locator('.camera-controls button').filter({ hasText: '申请批改' }).waitFor({ timeout: 120000 });
        expect(await page.locator('.child-correction-card,.child-correction-history,.paper-mark-ink').count() === 0,
          'Resume restored old results without renewed authorization');
        return { real_preflight_response: true, renewed_approval_required: true };
      });
      await stage('resumed-grading-waits-for-new-parent-approval', async () => {
        const gradeCount = report.events.filter((event) => event.method === 'POST' && event.path === scope + '/corrections').length;
        await page.locator('.camera-controls button').filter({ hasText: '申请批改' }).click();
        await page.getByRole('button', { name: '确认批改这张', exact: true }).waitFor({ timeout: 90000 });
        await page.getByRole('button', { name: '确认批改这张', exact: true }).click();
        await page.getByText('已请家长授权；获批后将自动批改已确认的截图').waitFor();
        expect(report.events.filter((event) => event.method === 'POST' && event.path === scope + '/corrections').length === gradeCount,
          'Resumed session silently reused the old grading approval');
        expect(await page.locator('.child-correction-card,.child-correction-history,.paper-mark-ink').count() === 0,
          'Awaiting approval exposed old results');
        await shot('renewed-approval-pending');
        await page.getByRole('button', { name: '取消拍摄作业', exact: true }).click();
        return { renewed_request_sent: true, no_second_grading_call: true, old_results_hidden: true };
      });
    }
    await stage('child-confirms-finish', async () => {
      await page.getByRole('button', { name: '结束作业', exact: true }).click();
      await page.getByRole('dialog', { name: '确认结束本次作业' }).waitFor();
      await shot('finish-confirmation');
      const done = page.waitForResponse((response) => response.url().endsWith(scope + '/finish') && response.status() === 200);
      await page.getByRole('button', { name: '确认结束作业', exact: true }).click();
      await done; await page.getByText('本次学习已结束。', { exact: true }).waitFor();
      return { session_finished_through_ui: true };
    });
  } catch (error) { report.workflow_error = error.message; }
  holdDashboards = false;
  for (const route of pendingDashboards.splice(0)) await route.continue().catch(() => {});
  report.status = report.workflow_error || report.page_errors.length || report.safety_blocks.length ? 'failed' : 'passed';
  report.completed_at = new Date().toISOString();
  await checkpoint();
  return sanitized(report);
}
