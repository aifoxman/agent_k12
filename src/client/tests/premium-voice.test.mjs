import assert from 'node:assert/strict';
import test from 'node:test';
import { dashboardSpeechConfiguration, premiumVoiceQuota } from '../src/premium-voice.js';

const dashboard = (family = {}, extra = {}) => ({
  family: {
    id: 'test-family',
    premium_voice_enabled: true,
    premium_voice_trial_chars: 20000,
    premium_voice_used_chars: 19996,
    ...family,
  },
  tts_available: true,
  ...extra,
});

test('provider resources enable authorized speech beyond the application trial without inventing a balance', () => {
  const data = dashboard({
    premium_voice_quota_mode: 'provider',
    premium_voice_trial_chars: null,
    premium_voice_used_chars: 50000,
  });
  assert.deepEqual(premiumVoiceQuota(data.family), {
    mode: 'provider',
    provider: true,
    used: 50000,
    limit: null,
    exhausted: false,
  });
  assert.deepEqual(dashboardSpeechConfiguration(data, true), {
    familyId: 'test-family',
    authorized: true,
    quotaMode: 'provider',
    quotaLimit: null,
    quotaUsed: 50000,
    enabled: true,
  });
});

test('provider mode still requires family opt-in, configured cloud service, and child playback', () => {
  const provider = { premium_voice_quota_mode: 'provider', premium_voice_trial_chars: null };
  assert.equal(
    dashboardSpeechConfiguration(dashboard({ ...provider, premium_voice_enabled: false }), true)
      .enabled,
    false,
  );
  assert.equal(
    dashboardSpeechConfiguration(dashboard(provider, { tts_available: false }), true).enabled,
    false,
  );
  assert.equal(dashboardSpeechConfiguration(dashboard(provider), false).enabled, false);
});

test('application mode remains the safe default and honors zero or exhausted trial limits', () => {
  assert.equal(dashboardSpeechConfiguration(dashboard(), true).enabled, true);
  for (const premium_voice_quota_mode of [undefined, 'application', 'unknown']) {
    const data = dashboard({ premium_voice_quota_mode, premium_voice_used_chars: 20000 });
    assert.equal(premiumVoiceQuota(data.family).exhausted, true);
    assert.equal(dashboardSpeechConfiguration(data, true).enabled, false);
  }
  assert.equal(
    dashboardSpeechConfiguration(dashboard({ premium_voice_trial_chars: 0 }), true).enabled,
    false,
  );
  assert.equal(premiumVoiceQuota({}).limit, 20000);
});
