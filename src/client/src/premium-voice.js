export const premiumVoiceQuotaMode = (value) => (value === 'provider' ? 'provider' : 'application');

export function premiumVoiceQuota(family = {}) {
  const mode = premiumVoiceQuotaMode(family.premium_voice_quota_mode);
  const provider = mode === 'provider';
  const used = Number.isFinite(family.premium_voice_used_chars)
    ? Math.max(0, family.premium_voice_used_chars)
    : 0;
  const limit = provider
    ? null
    : Number.isFinite(family.premium_voice_trial_chars)
      ? Math.max(0, family.premium_voice_trial_chars)
      : 20000;
  return { mode, provider, used, limit, exhausted: !provider && used >= limit };
}

export function dashboardSpeechConfiguration(dashboard, child = false) {
  const family = dashboard?.family || {};
  const quota = premiumVoiceQuota(family);
  const authorized = Boolean(family.premium_voice_enabled);
  return {
    familyId: family.id,
    authorized,
    quotaMode: quota.mode,
    quotaLimit: quota.limit,
    quotaUsed: quota.used,
    enabled: Boolean(child && dashboard?.tts_available && authorized && !quota.exhausted),
  };
}
