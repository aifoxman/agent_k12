import assert from 'node:assert/strict';
import test from 'node:test';
import {
  correctionAllowedNow,
  correctionTestBypassEnabled,
  visibleCorrectionAllowed,
} from '../src/session-time.js';

test('authorization expiry follows server time despite a wrong device clock', () => {
  const dashboard = {
    server_now: 1000,
    received_mono_ms: 2000,
    session: { correction_authorized_until: 1008 },
  };
  assert.equal(correctionAllowedNow(dashboard, 3000), true);
  assert.equal(correctionAllowedNow(dashboard, 11000), false);
});

test('pausing hides cached correction immediately, before the next dashboard refresh', () => {
  const dashboard = {
    server_now: 1000,
    received_mono_ms: 2000,
    session: { status: 'active', correction_authorized_until: 1200 },
  };
  assert.equal(visibleCorrectionAllowed(dashboard, 'active', 3000), true);
  assert.equal(visibleCorrectionAllowed(dashboard, 'paused', 3000), false);
  assert.equal(
    visibleCorrectionAllowed(
      { ...dashboard, session: { ...dashboard.session, status: 'paused' } },
      'active',
      3000,
    ),
    false,
  );
  assert.equal(visibleCorrectionAllowed(dashboard, 'active', 203000), false);
});

test('server test mode permits correction without inventing a parent authorization expiry', () => {
  const dashboard = {
    server_now: 1000,
    received_mono_ms: 2000,
    session: {
      status: 'active',
      correction_authorized_until: null,
      correction_access: { allowed: true, mode: 'test_bypass', authorized_until: null },
    },
  };
  assert.equal(correctionTestBypassEnabled(dashboard), true);
  assert.equal(correctionAllowedNow(dashboard, 3000), true);
  assert.equal(correctionAllowedNow(dashboard, 900000), true);
  assert.equal(visibleCorrectionAllowed(dashboard, 'active', 3000), true);
  assert.equal(visibleCorrectionAllowed(dashboard, 'paused', 3000), false);
  for (const status of ['paused', 'ended', 'idle']) {
    const paused = { ...dashboard, session: { ...dashboard.session, status } };
    assert.equal(correctionAllowedNow(paused, 3000), false);
    assert.equal(visibleCorrectionAllowed(paused, 'active', 3000), false);
  }
});

test('an explicit server denial takes precedence over a cached parent grant or mode label', () => {
  const dashboard = {
    server_now: 1000,
    received_mono_ms: 2000,
    session: {
      status: 'active',
      correction_authorized_until: 2000,
      correction_access: { allowed: false, mode: 'test_bypass', authorized_until: null },
    },
  };
  assert.equal(correctionAllowedNow(dashboard, 3000), false);
  for (const access of [
    null,
    {},
    { allowed: true, mode: 'unknown' },
    { allowed: 'true', mode: 'test_bypass' },
  ]) {
    assert.equal(
      correctionAllowedNow(
        { ...dashboard, session: { ...dashboard.session, correction_access: access } },
        3000,
      ),
      false,
    );
  }
  assert.equal(correctionTestBypassEnabled({ session: {} }), false);
  assert.equal(correctionAllowedNow(null, 3000), false);
});

test('normal server-authorized correction still expires according to server time', () => {
  const dashboard = {
    server_now: 1000,
    received_mono_ms: 2000,
    session: {
      status: 'active',
      correction_authorized_until: 3000,
      correction_access: { allowed: true, mode: 'parent_authorization', authorized_until: 1008 },
    },
  };
  assert.equal(correctionTestBypassEnabled(dashboard), false);
  assert.equal(correctionAllowedNow(dashboard, 3000), true);
  assert.equal(correctionAllowedNow(dashboard, 11000), false);
  for (const authorized_until of [null, '2000', NaN, Infinity]) {
    assert.equal(
      correctionAllowedNow(
        {
          ...dashboard,
          session: {
            ...dashboard.session,
            correction_access: { ...dashboard.session.correction_access, authorized_until },
          },
        },
        3000,
      ),
      false,
    );
  }
});
