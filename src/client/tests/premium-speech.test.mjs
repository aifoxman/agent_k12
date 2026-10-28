import assert from 'node:assert/strict';
import test from 'node:test';

globalThis.location = { search: '?mode=child', hostname: 'localhost' };
globalThis.localStorage = {
  getItem: (name) => (name === 'child_api' ? 'http://localhost:8000' : 'test-token'),
  setItem: () => {},
};
globalThis.CustomEvent = class {
  constructor(type, options) {
    this.type = type;
    this.detail = options.detail;
  }
};

const { configureSpeech, prepareSpeech, speakAndWait } = await import('../src/api.js');

test('a slower lesson cancels prepared normal-speed audio and sends the chosen speed to cloud speech', async () => {
  const speeds = [];
  globalThis.window = {
    dispatchEvent: () => {},
    PaperVoice: {
      stopPcm: () => {},
      stop: () => {},
      startPcm: () => true,
      pushPcm: () => true,
      finishPcm: (id) => queueMicrotask(() => window.__pcmOutputDone(id, true)),
    },
  };
  globalThis.fetch = async (_url, options) => {
    speeds.push(JSON.parse(options.body).speed);
    return new Response(new Uint8Array([1, 0, 2, 0]));
  };
  configureSpeech({ enabled: true });
  const prepared = prepareSpeech('Read slowly.', undefined, { rate: 1 });
  let cancelled = false;
  const cancel = prepared.cancel;
  prepared.cancel = () => {
    cancelled = true;
    cancel();
  };
  assert.equal(
    await speakAndWait('Read slowly.', [], undefined, prepared, undefined, { rate: 0.85 }),
    true,
  );
  assert.deepEqual(speeds, [1, 0.85]);
  assert.equal(cancelled, true);
  configureSpeech();
});

test('Pad system speech receives the slower rate through the compatible native bridge', async () => {
  const rates = [];
  globalThis.window = {
    dispatchEvent: () => {},
    PaperVoice: {
      stopPcm: () => {},
      stop: () => {},
      speakAtRate: (_text, id, rate) => {
        rates.push(rate);
        queueMicrotask(() => {
          window.__speechOutputStart(id);
          window.__speechOutputDone(id);
        });
      },
    },
  };
  configureSpeech();
  assert.equal(
    await speakAndWait('A short step.', [], undefined, null, undefined, { rate: 0.85 }),
    true,
  );
  assert.deepEqual(rates, [0.85]);
});

test('Pad system speech defaults to normal rate and keeps ink start on the output callback', async () => {
  const rates = [];
  let started = 0;
  globalThis.window = {
    dispatchEvent: () => {},
    PaperVoice: {
      stopPcm: () => {},
      stop: () => {},
      speakAtRate: (_text, id, rate) => {
        rates.push(rate);
        assert.equal(started, 0);
        queueMicrotask(() => {
          window.__speechOutputStart(id);
          window.__speechOutputDone(id);
        });
      },
    },
  };
  configureSpeech();
  assert.equal(await speakAndWait('A normal step.', [], undefined, null, () => started++), true);
  assert.deepEqual(rates, [1]);
  assert.equal(started, 1);
});

test('browser system voice uses normal rate unless the child explicitly asks for slower', async () => {
  const rates = [];
  globalThis.speechSynthesis = {
    cancel() {},
    speak(utterance) {
      rates.push(utterance.rate);
      queueMicrotask(() => {
        utterance.onstart?.();
        utterance.onend?.();
      });
    },
  };
  globalThis.SpeechSynthesisUtterance = class {};
  globalThis.window = { dispatchEvent() {}, speechSynthesis };
  configureSpeech();
  assert.equal(await speakAndWait('Normal browser speech.'), true);
  assert.equal(
    await speakAndWait('Slower browser speech.', [], undefined, null, undefined, { rate: 0.85 }),
    true,
  );
  assert.deepEqual(rates, [1, 0.85]);
  delete globalThis.speechSynthesis;
  delete globalThis.SpeechSynthesisUtterance;
});

test('cloud failure preserves normal or explicit slower speed in native fallback', async () => {
  const cloudRates = [];
  const systemRates = [];
  globalThis.window = {
    dispatchEvent() {},
    PaperVoice: {
      stopPcm() {},
      stop() {},
      speakAtRate(_text, id, rate) {
        systemRates.push(rate);
        queueMicrotask(() => {
          window.__speechOutputStart(id);
          window.__speechOutputDone(id);
        });
      },
    },
  };
  globalThis.fetch = async (_url, options) => {
    cloudRates.push(JSON.parse(options.body).speed);
    return new Response(JSON.stringify({ error: { message: 'temporary failure' } }), {
      status: 502,
    });
  };
  configureSpeech({ enabled: true });
  assert.equal(await speakAndWait('Normal fallback.'), true);
  assert.equal(
    await speakAndWait('Slower fallback.', [], undefined, null, undefined, { rate: 0.85 }),
    true,
  );
  assert.deepEqual(cloudRates, [1, 0.85]);
  assert.deepEqual(systemRates, [1, 0.85]);
  configureSpeech();
});

test('a prepared OpenAI-shaped PCM stream plays once and waits for native completion', async () => {
  const requests = [];
  const pcm = [];
  let started = 0;
  globalThis.window = {
    dispatchEvent: () => {},
    PaperVoice: {
      stopPcm: () => {},
      stop: () => {},
      startPcm: () => true,
      pushPcm: (_id, encoded) => {
        pcm.push(Buffer.from(encoded, 'base64'));
        return true;
      },
      finishPcm: (id) => queueMicrotask(() => window.__pcmOutputDone(id, true)),
    },
  };
  globalThis.fetch = async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) });
    return new Response(new Uint8Array([1, 0, 2, 0]), {
      headers: { 'Content-Type': 'audio/pcm' },
    });
  };
  configureSpeech({ enabled: true });
  const prepared = prepareSpeech('看看这一步。');
  assert.equal(await speakAndWait('看看这一步。', [], undefined, prepared, () => started++), true);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, 'http://localhost:8000/v1/audio/speech');
  assert.deepEqual(requests[0].body, {
    model: 'k12-tts',
    input: '看看这一步。',
    voice: 'teacher-warm-female',
    response_format: 'pcm',
    speed: 1,
    stream_format: 'audio',
  });
  assert.deepEqual(Buffer.concat(pcm), Buffer.from([1, 0, 2, 0]));
  assert.equal(started, 1);
});

test('a coalesced PCM read is split into safe Android bridge chunks', async () => {
  const pcm = [];
  const source = new Uint8Array(300000);
  globalThis.window = {
    dispatchEvent: () => {},
    PaperVoice: {
      stopPcm: () => {},
      stop: () => {},
      startPcm: () => true,
      pushPcm: (_id, encoded) => {
        const bytes = Buffer.from(encoded, 'base64');
        assert.ok(bytes.length <= 32768);
        pcm.push(bytes);
        return true;
      },
      finishPcm: (id) => queueMicrotask(() => window.__pcmOutputDone(id, true)),
    },
  };
  globalThis.fetch = async () => new Response(source);
  configureSpeech({ enabled: true });
  assert.equal(await speakAndWait('long segment'), true);
  assert.ok(pcm.length > 1);
  assert.deepEqual(Buffer.concat(pcm), Buffer.from(source));
  configureSpeech();
});

test('provider failure before audio falls back to the system voice', async () => {
  const spoken = [];
  globalThis.window = {
    dispatchEvent: () => {},
    PaperVoice: {
      stopPcm: () => {},
      stop: () => {},
      speak: (phrase, id) => {
        spoken.push(phrase);
        queueMicrotask(() => {
          window.__speechOutputStart(id);
          window.__speechOutputDone(id);
        });
      },
    },
  };
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ error: { message: 'unavailable' } }), { status: 502 });
  let started = 0;
  assert.equal(await speakAndWait('继续看题。', [], undefined, null, () => started++), true);
  assert.deepEqual(spoken, ['继续看题。']);
  assert.equal(started, 1);
  configureSpeech();
});

test('one failed segment does not switch later segments to the system voice', async () => {
  let requests = 0;
  const system = [];
  const pcm = [];
  globalThis.window = {
    dispatchEvent: () => {},
    PaperVoice: {
      stopPcm: () => {},
      stop: () => {},
      speak: (phrase, id) => {
        system.push(phrase);
        queueMicrotask(() => {
          window.__speechOutputStart(id);
          window.__speechOutputDone(id);
        });
      },
      startPcm: () => true,
      pushPcm: (_id, encoded) => {
        pcm.push(Buffer.from(encoded, 'base64'));
        return true;
      },
      finishPcm: (id) => queueMicrotask(() => window.__pcmOutputDone(id, true)),
    },
  };
  globalThis.fetch = async () => {
    requests++;
    return requests === 1
      ? new Response(JSON.stringify({ error: { message: 'temporary failure' } }), { status: 502 })
      : new Response(new Uint8Array([1, 0, 2, 0]), {
          headers: { 'Content-Type': 'audio/pcm' },
        });
  };
  configureSpeech({ enabled: true });
  assert.equal(await speakAndWait('first'), true);
  assert.equal(await speakAndWait('second'), true);
  assert.equal(requests, 2);
  assert.deepEqual(system, ['first']);
  assert.deepEqual(Buffer.concat(pcm), Buffer.from([1, 0, 2, 0]));
  configureSpeech();
});

function quotaSpeechFixture() {
  const system = [];
  const pcm = [];
  globalThis.window = {
    dispatchEvent() {},
    PaperVoice: {
      stopPcm() {},
      stop() {},
      speakAtRate(text, id, rate) {
        system.push({ text, rate });
        queueMicrotask(() => {
          window.__speechOutputStart(id);
          window.__speechOutputDone(id);
        });
      },
      startPcm: () => true,
      pushPcm(_id, encoded) {
        pcm.push(Buffer.from(encoded, 'base64'));
        return true;
      },
      finishPcm: (id) => queueMicrotask(() => window.__pcmOutputDone(id, true)),
    },
  };
  return { system, pcm };
}

const quotaFailure = () =>
  new Response(
    JSON.stringify({ error: { code: 'insufficient_quota', message: 'quota exhausted' } }),
    {
      status: 429,
    },
  );
const quotaAudio = () => new Response(new Uint8Array([1, 0, 2, 0]));
const quotaConfiguration = (familyId, extra = {}) => ({
  familyId,
  enabled: true,
  authorized: true,
  quotaLimit: 20000,
  quotaUsed: 19990,
  ...extra,
});

test('quota exhaustion survives repeated dashboard polls and keeps later speech on the system voice', async () => {
  const { system, pcm } = quotaSpeechFixture();
  let requests = 0;
  globalThis.fetch = async () => {
    requests++;
    return quotaFailure();
  };
  configureSpeech(quotaConfiguration('quota-poll'));
  assert.equal(await speakAndWait('The next sentence exceeds the remaining quota.'), true);
  for (const used of [19990, 19995, 20000, 19990]) {
    configureSpeech(quotaConfiguration('quota-poll', { quotaUsed: used }));
    assert.equal(prepareSpeech('No more cloud preparation.'), null);
    assert.equal(
      await speakAndWait('Continue with the system voice.', [], undefined, null, undefined, {
        rate: 0.85,
      }),
      true,
    );
  }
  assert.equal(requests, 1);
  assert.equal(pcm.length, 0);
  assert.deepEqual(
    system.map((entry) => entry.rate),
    [1, 0.85, 0.85, 0.85, 0.85],
  );
  configureSpeech();
});

for (const [name, changed] of [
  ['a real quota increase', { quotaLimit: 21000, quotaUsed: 20000 }],
  ['a real usage refund', { quotaUsed: 19989 }],
]) {
  test(`${name} permits cloud speech again after a family quota block`, async () => {
    const { system, pcm } = quotaSpeechFixture();
    let requests = 0;
    globalThis.fetch = async () => (++requests === 1 ? quotaFailure() : quotaAudio());
    const familyId = `quota-change-${name}`;
    configureSpeech(quotaConfiguration(familyId));
    assert.equal(await speakAndWait('First attempt.'), true);
    configureSpeech(quotaConfiguration(familyId, { quotaUsed: 20000 }));
    assert.equal(prepareSpeech('An ordinary usage update is still blocked.'), null);
    configureSpeech(quotaConfiguration(familyId, changed));
    assert.equal(await speakAndWait('Cloud can now resume.'), true);
    assert.equal(requests, 2);
    assert.equal(system.length, 1);
    assert.equal(pcm.length, 1);
    configureSpeech();
  });
}

test('disabling cloud for availability does not reset quota until authorization really changes', async () => {
  const { system, pcm } = quotaSpeechFixture();
  let requests = 0;
  globalThis.fetch = async () => (++requests === 1 ? quotaFailure() : quotaAudio());
  configureSpeech(quotaConfiguration('quota-authorization'));
  assert.equal(await speakAndWait('Blocked sentence.'), true);
  configureSpeech(quotaConfiguration('quota-authorization', { enabled: false }));
  configureSpeech(quotaConfiguration('quota-authorization'));
  assert.equal(await speakAndWait('Availability polling must not retry cloud.'), true);
  assert.equal(requests, 1);
  configureSpeech(quotaConfiguration('quota-authorization', { enabled: false, authorized: false }));
  configureSpeech(quotaConfiguration('quota-authorization'));
  assert.equal(await speakAndWait('A new authorization can retry cloud.'), true);
  assert.equal(requests, 2);
  assert.equal(system.length, 2);
  assert.equal(pcm.length, 1);
  configureSpeech();
});

test('an explicit family quota reset permits a new attempt with otherwise unchanged quota', async () => {
  const { system, pcm } = quotaSpeechFixture();
  let requests = 0;
  globalThis.fetch = async () => (++requests === 1 ? quotaFailure() : quotaAudio());
  configureSpeech(quotaConfiguration('quota-explicit-reset'));
  assert.equal(await speakAndWait('Blocked sentence.'), true);
  configureSpeech(quotaConfiguration('quota-explicit-reset', { resetQuota: true }));
  assert.equal(await speakAndWait('Explicitly reset attempt.'), true);
  assert.equal(requests, 2);
  assert.equal(system.length, 1);
  assert.equal(pcm.length, 1);
  configureSpeech();
});

test('a late quota rejection from family A neither disables family B nor loses the A block', async () => {
  const { system, pcm } = quotaSpeechFixture();
  const requests = [];
  let rejectA;
  globalThis.fetch = async (_url, options) => {
    const text = JSON.parse(options.body).input;
    requests.push(text);
    return text === 'Prepared for A.'
      ? new Promise((resolve) => {
          rejectA = resolve;
        })
      : quotaAudio();
  };
  configureSpeech(quotaConfiguration('quota-family-A'));
  const preparedA = prepareSpeech('Prepared for A.');
  configureSpeech(quotaConfiguration('quota-family-B'));
  assert.equal(await speakAndWait('B before the late A failure.'), true);
  rejectA(quotaFailure());
  await assert.rejects(preparedA.response, /quota exhausted/);
  configureSpeech(quotaConfiguration('quota-family-B'));
  assert.equal(await speakAndWait('B after the late A failure.'), true);
  configureSpeech(quotaConfiguration('quota-family-A'));
  assert.equal(prepareSpeech('A remains blocked.'), null);
  assert.equal(await speakAndWait('A uses system speech.'), true);
  assert.deepEqual(requests, [
    'Prepared for A.',
    'B before the late A failure.',
    'B after the late A failure.',
  ]);
  assert.deepEqual(
    system.map((entry) => entry.text),
    ['A uses system speech.'],
  );
  assert.equal(pcm.length, 2);
  configureSpeech();
});

test('quota blocks are separate for different API servers even with the same family label', async () => {
  const { system, pcm } = quotaSpeechFixture();
  const previousStorage = globalThis.localStorage;
  const urls = [];
  let server = 'http://quota-server-a.invalid';
  globalThis.localStorage = {
    getItem: (name) => (name === 'child_api' ? server : 'test-token'),
    setItem() {},
  };
  globalThis.fetch = async (url) => {
    urls.push(url);
    return url.startsWith('http://quota-server-a.invalid') ? quotaFailure() : quotaAudio();
  };
  try {
    configureSpeech(quotaConfiguration('quota-same-family-label'));
    assert.equal(await speakAndWait('Server A is out of quota.'), true);
    server = 'http://quota-server-b.invalid';
    configureSpeech(quotaConfiguration('quota-same-family-label'));
    assert.equal(await speakAndWait('Server B still has quota.'), true);
    server = 'http://quota-server-a.invalid';
    configureSpeech(quotaConfiguration('quota-same-family-label'));
    assert.equal(await speakAndWait('Server A stays on system voice.'), true);
    assert.deepEqual(urls, [
      'http://quota-server-a.invalid/v1/audio/speech',
      'http://quota-server-b.invalid/v1/audio/speech',
    ]);
    assert.equal(system.length, 2);
    assert.equal(pcm.length, 1);
  } finally {
    globalThis.localStorage = previousStorage;
    configureSpeech();
  }
});

test('legacy anonymous explicit enable can intentionally reset its own quota block', async () => {
  const { system, pcm } = quotaSpeechFixture();
  let requests = 0;
  globalThis.fetch = async () => (++requests === 1 ? quotaFailure() : quotaAudio());
  configureSpeech({ enabled: true });
  assert.equal(await speakAndWait('Anonymous blocked sentence.'), true);
  assert.equal(prepareSpeech('Still blocked before explicit enable.'), null);
  configureSpeech({ enabled: true });
  assert.equal(await speakAndWait('Explicit anonymous enable retries.'), true);
  assert.equal(requests, 2);
  assert.equal(system.length, 1);
  assert.equal(pcm.length, 1);
  configureSpeech();
});

test('switching to provider resources clears a trial block while unchanged provider polls keep a new block', async () => {
  const { system, pcm } = quotaSpeechFixture();
  let requests = 0;
  globalThis.fetch = async () => {
    requests++;
    return requests === 2 ? quotaAudio() : quotaFailure();
  };
  const family = 'quota-provider-switch';
  configureSpeech(quotaConfiguration(family));
  assert.equal(await speakAndWait('Application trial exhausted.'), true);
  configureSpeech(
    quotaConfiguration(family, { quotaMode: 'provider', quotaLimit: null, quotaUsed: 19996 }),
  );
  assert.equal(await speakAndWait('Use the configured provider resources.'), true);
  assert.equal(await speakAndWait('Provider resources now reject this request.'), true);
  for (const quotaUsed of [19996, 20000, 25000]) {
    configureSpeech(
      quotaConfiguration(family, { quotaMode: 'provider', quotaLimit: null, quotaUsed }),
    );
    assert.equal(prepareSpeech('Repeated polls must not retry exhausted resources.'), null);
    assert.equal(await speakAndWait('Keep using system speech.'), true);
  }
  assert.equal(requests, 3);
  assert.equal(pcm.length, 1);
  assert.equal(system.length, 5);
  configureSpeech();
});

for (const returnToApplication of [false, true]) {
  const policyName = returnToApplication ? 'renewed application' : 'provider';
  test(`a delayed old trial rejection cannot block the ${policyName} policy`, async () => {
    const { system, pcm } = quotaSpeechFixture();
    const requests = [];
    let rejectOld;
    globalThis.fetch = async (_url, options) => {
      const text = JSON.parse(options.body).input;
      requests.push(text);
      return text === 'Prepared under the old trial.'
        ? new Promise((resolve) => {
            rejectOld = resolve;
          })
        : quotaAudio();
    };
    const family = `quota-late-policy-${policyName}`;
    configureSpeech(quotaConfiguration(family));
    const prepared = prepareSpeech('Prepared under the old trial.');
    const provider = { quotaMode: 'provider', quotaLimit: null };
    configureSpeech(quotaConfiguration(family, provider));
    assert.equal(await speakAndWait('Provider policy works.'), true);
    if (returnToApplication) configureSpeech(quotaConfiguration(family));
    rejectOld(quotaFailure());
    await assert.rejects(prepared.response, /quota exhausted/);
    configureSpeech(quotaConfiguration(family, returnToApplication ? {} : provider));
    assert.equal(await speakAndWait('The current policy still works.'), true);
    assert.equal(system.length, 0);
    assert.equal(pcm.length, 2);
    assert.deepEqual(requests, [
      'Prepared under the old trial.',
      'Provider policy works.',
      'The current policy still works.',
    ]);
    configureSpeech();
  });
}
