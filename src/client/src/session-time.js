export function dashboardNow(dashboard, monotonicMs = performance.now()) {
  if (!dashboard?.server_now) return Date.now() / 1000;
  return (
    dashboard.server_now +
    Math.max(0, monotonicMs - (dashboard.received_mono_ms ?? monotonicMs)) / 1000
  );
}

export function correctionAllowedNow(dashboard, monotonicMs = performance.now()) {
  const session = dashboard?.session;
  const access = session?.correction_access;
  if (access !== undefined) {
    if (session.status !== 'active' || access?.allowed !== true) return false;
    if (access.mode === 'test_bypass') return true;
    if (access.mode !== 'parent_authorization') return false;
    return (
      typeof access.authorized_until === 'number' &&
      Number.isFinite(access.authorized_until) &&
      access.authorized_until > dashboardNow(dashboard, monotonicMs)
    );
  }
  return (session?.correction_authorized_until || 0) > dashboardNow(dashboard, monotonicMs);
}

export function correctionTestBypassEnabled(dashboard) {
  return dashboard?.session?.correction_access?.mode === 'test_bypass';
}

export function visibleCorrectionAllowed(dashboard, stage, monotonicMs = performance.now()) {
  return (
    stage === 'active' &&
    dashboard?.session?.status === 'active' &&
    correctionAllowedNow(dashboard, monotonicMs)
  );
}
