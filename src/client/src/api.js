import { premiumVoiceQuotaMode } from './premium-voice.js';

export const mode =
  new URLSearchParams(location.search).get('mode') || localStorage.getItem('mode') || 'parent';
export const key = (name) => `${mode}_${name}`;
export const saved = (name) => localStorage.getItem(key(name)) || '';
export const save = (name, value) => localStorage.setItem(key(name), value);

export function baseUrl() {
  return (
    saved('api') ||
    import.meta.env.VITE_API_URL ||
    (location.hostname === 'localhost' ? 'http://localhost:8000' : '')
  ).replace(/\/$/, '');
}

export const CONNECTION_ERROR = '无法连接服务，请检查网络或设备连接后重试。';

function requestError(error) {
  if (error instanceof TypeError) return new Error(CONNECTION_ERROR);
  return error;
}

let activityId = 0;
function activityLabel(path, method) {
  if (path === '/v1/devices/status' || path.includes('/realtime/')) return '';
  if (path.endsWith('/timer/events')) return '';
  if (path === '/v1/setup/check') return '正在检查纸面是否完整、文字是否清晰…';
  if (path.includes('/observations')) return '正在比较纸面变化并生成观察…';
  if (path.includes('/recheck')) return '正在放大这道题并独立复检…';
  if (path.includes('/corrections'))
    return method === 'POST' ? '正在逐题检查本页答案与书写…' : '正在同步批改复核…';
  if (path.includes('/correction-request')) return '正在向家长申请批改权限…';
  if (path.includes('/correction-access')) return '正在更新批改权限…';
  if (path === '/v1/hints' || path === '/v1/hints/stream') return '正在识别题目并准备辅导讲解…';
  if (path === '/v1/voice/transcribe') return '正在转写刚才的语音…';
  if (path === '/v1/sessions' && method === 'POST') return '正在进入作业状态…';
  if (path.includes('/sessions/') && path.endsWith('/finish')) return '正在整理作业反馈…';
  if (path.includes('/sessions/') && path.endsWith('/reflections')) return '正在记录孩子的想法…';
  if (path.startsWith('/v1/sessions/') && method === 'PATCH') return '正在同步作业状态…';
  if (path.startsWith('/v1/tasks/') && method === 'PATCH') return '正在同步任务状态…';
  if (path === '/v1/tasks/import') return '正在发布作业任务…';
  if (path === '/v1/live-sessions' && method === 'POST') return '正在连接实时纸面…';
  if (path.includes('/live-sessions/') && path.endsWith('/finish')) return '正在结束远程查看…';
  if (path === '/v1/families/pair') return '正在配对平板…';
  if (path === '/v1/devices/claim') return '正在生成配对码…';
  if (path === '/v1/data/delete') return '正在删除作业历史…';
  if (path.startsWith('/v1/reports/daily')) return '正在整理今日回顾…';
  if (method === 'POST' && path.includes('/auth/')) return '正在验证家庭账户…';
  if (method !== 'GET' && !path.includes('/realtime/') && path !== '/v1/ice')
    return '正在同步操作…';
  return '';
}

export async function api(path, options = {}) {
  const token = saved('token');
  const headers = {
    ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...options.headers,
  };
  const label = activityLabel(path, options.method || 'GET');
  const id = ++activityId;
  if (label) window.dispatchEvent(new CustomEvent('paper-activity', { detail: { id, label } }));
  try {
    const response = await fetch(`${baseUrl()}${path}`, { ...options, headers }).catch((error) => {
      throw requestError(error);
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok)
      throw new Error(
        typeof data.detail === 'string' ? data.detail : `请求失败 (${response.status})`,
      );
    return data;
  } finally {
    if (label) window.dispatchEvent(new CustomEvent('paper-activity', { detail: { id } }));
  }
}

export async function streamApi(path, form, onEvent, signal) {
  const token = saved('token');
  const id = ++activityId;
  window.dispatchEvent(
    new CustomEvent('paper-activity', {
      detail: { id, label: activityLabel(path, 'POST') },
    }),
  );
  try {
    const response = await fetch(`${baseUrl()}${path}`, {
      method: 'POST',
      body: form,
      signal,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }).catch((error) => {
      throw requestError(error);
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(
        typeof data.detail === 'string' ? data.detail : `请求失败 (${response.status})`,
      );
    }
    if (!response.body) throw new Error('当前设备不支持流式提示');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let result = null;
    const handle = (frame) => {
      const event = frame.match(/^event:\s*(.+)$/m)?.[1];
      const data = frame.match(/^data:\s*(.+)$/m)?.[1];
      if (!event || !data) return;
      let parsed;
      try {
        parsed = JSON.parse(data);
      } catch {
        return;
      }
      if (event === 'error') throw new Error(parsed.detail || '提示生成中断');
      onEvent?.(event, parsed);
      if (event === 'done') result = parsed;
    };
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() || '';
      frames.forEach(handle);
      if (done) break;
    }
    if (buffer.trim()) handle(buffer);
    if (!result) throw new Error('提示流未正常完成');
    return result;
  } finally {
    window.dispatchEvent(new CustomEvent('paper-activity', { detail: { id } }));
  }
}

export const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });
export const patch = (path, body) => api(path, { method: 'PATCH', body: JSON.stringify(body) });

export async function privateImage(path, signal) {
  const response = await fetch(`${baseUrl()}${path}`, {
    headers: { Authorization: `Bearer ${saved('token')}` },
    cache: 'no-store',
    signal,
  }).catch((error) => {
    throw requestError(error);
  });
  if (!response.ok) throw new Error(`作业截图读取失败 (${response.status})`);
  return response.blob();
}

let activeSpeechSession = null;
let speechOutputId = 0;
let speechWaiter = null;
let activePlayback = null;
let premiumSpeechEnabled = false;
let speechConfiguration = null;
const speechQuotaBlocks = new Map();
const speechQuotaPolicies = new Map();
let speechQuotaPolicyId = 0;

export function configureSpeech({
  enabled = false,
  familyId = '',
  authorized = enabled,
  quotaMode = 'application',
  quotaLimit = null,
  quotaUsed = null,
  resetQuota = false,
} = {}) {
  const familyScope = `${baseUrl()}:${familyId || 'explicit'}`;
  const policyMode = premiumVoiceQuotaMode(quotaMode);
  let policy = speechQuotaPolicies.get(familyScope);
  if (!policy || policy.mode !== policyMode) {
    if (policy) speechQuotaBlocks.delete(policy.scope);
    policy = {
      mode: policyMode,
      scope: `${familyScope}:${policyMode}:${++speechQuotaPolicyId}`,
    };
    speechQuotaPolicies.set(familyScope, policy);
  }
  const configuration = {
    familyScope,
    scope: policy.scope,
    authorized: Boolean(authorized),
    quotaLimit: Number.isFinite(quotaLimit) ? quotaLimit : null,
    quotaUsed: Number.isFinite(quotaUsed) ? quotaUsed : null,
  };
  const blocked = speechQuotaBlocks.get(configuration.scope);
  if (blocked && !configuration.authorized) blocked.sawDisabled = true;
  const increasedQuota =
    blocked &&
    ((configuration.quotaLimit !== null &&
      blocked.quotaLimit !== null &&
      configuration.quotaLimit > blocked.quotaLimit) ||
      (configuration.quotaUsed !== null &&
        blocked.quotaUsed !== null &&
        configuration.quotaUsed < blocked.quotaUsed));
  if (
    resetQuota ||
    (!familyId && enabled) ||
    increasedQuota ||
    (blocked?.sawDisabled && configuration.authorized)
  )
    speechQuotaBlocks.delete(configuration.scope);
  speechConfiguration = configuration;
  premiumSpeechEnabled = Boolean(enabled) && !speechQuotaBlocks.has(configuration.scope);
}

const speechRate = (rate, fallback = 1) =>
  Number.isFinite(rate) ? Math.max(0.5, Math.min(1.2, rate)) : fallback;

export function prepareSpeech(text, signal, options = {}) {
  if (!premiumSpeechEnabled || mode !== 'child' || !text) return null;
  const controller = new AbortController();
  signal?.addEventListener('abort', () => controller.abort(), { once: true });
  const rate = speechRate(options.rate, 1);
  const response = premiumAudioResponse(text, controller.signal, rate);
  response.catch(() => {});
  return { response, rate, cancel: () => controller.abort() };
}

function startSystemSpeech(text, id, waitForEnd, finish, onStart, options = {}) {
  if (window.PaperVoice) {
    window.__speechOutputStart = (startedId) => {
      if (startedId === id) onStart?.();
    };
    window.__speechOutputDone = (doneId) => {
      if (speechWaiter?.id === doneId) speechWaiter.finish(true);
    };
    if (window.PaperVoice.speakAtRate)
      window.PaperVoice.speakAtRate(text, id, speechRate(options.rate));
    else window.PaperVoice.speak(text, id);
    return;
  }
  if (!('speechSynthesis' in window)) {
    finish(false);
    return;
  }
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'zh-CN';
  utterance.rate = speechRate(options.rate);
  utterance.onstart = () => onStart?.();
  if (waitForEnd) {
    utterance.onend = () => finish(true);
    utterance.onerror = () => finish(false);
  }
  speechSynthesis.speak(utterance);
}

function encodeBase64(bytes) {
  let value = '';
  for (let index = 0; index < bytes.length; index += 8192)
    value += String.fromCharCode(...bytes.subarray(index, index + 8192));
  return btoa(value);
}

async function premiumAudioResponse(text, signal, rate = 1) {
  const configuration = speechConfiguration;
  const response = await fetch(`${baseUrl()}/v1/audio/speech`, {
    method: 'POST',
    signal,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${saved('token')}`,
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify({
      model: 'k12-tts',
      input: text,
      voice: 'teacher-warm-female',
      response_format: 'pcm',
      speed: rate,
      stream_format: 'audio',
    }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    if (error.error?.code === 'insufficient_quota' || error.error?.code === 'voice_not_enabled') {
      if (
        configuration &&
        speechQuotaPolicies.get(configuration.familyScope)?.scope === configuration.scope
      ) {
        speechQuotaBlocks.set(configuration.scope, { ...configuration, sawDisabled: false });
        // Old family or quota-policy responses cannot disable the current policy.
        if (speechConfiguration?.scope === configuration.scope) premiumSpeechEnabled = false;
      } else if (!configuration) premiumSpeechEnabled = false;
    }
    throw new Error(error.error?.message || `语音合成失败 (${response.status})`);
  }
  if (!response.body) throw new Error('语音合成未返回音频流');
  return response;
}

async function playPremiumSpeech(text, id, playback, prepared) {
  if (prepared && prepared.rate !== playback.rate) {
    prepared.cancel();
    prepared = null;
  }
  if (prepared)
    playback.controller.signal.addEventListener('abort', prepared.cancel, { once: true });
  const response = prepared
    ? await prepared.response
    : await premiumAudioResponse(text, playback.controller.signal, playback.rate);
  const reader = response.body.getReader();
  const native = Boolean(window.PaperVoice?.startPcm && window.PaperVoice?.pushPcm);
  let audioContext;
  let nativeCompletion;
  let nativeTimeout;
  let nativeCallback;
  let nextAt = 0;
  let carry = new Uint8Array();
  let streamEnded = false;
  try {
    if (native) {
      nativeCompletion = new Promise((resolve) => {
        nativeCallback = (doneId, success) => {
          if (doneId !== id) return;
          clearTimeout(nativeTimeout);
          resolve(Boolean(success));
        };
        window.__pcmOutputDone = nativeCallback;
      });
      if (!window.PaperVoice.startPcm(id)) throw new Error('音频播放器无法启动');
    } else {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) throw new Error('当前设备不支持流式音频');
      audioContext = new AudioContextClass({ sampleRate: 24000 });
      await audioContext.resume();
      if (audioContext.state !== 'running') throw new Error('音频播放器无法启动');
    }
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        streamEnded = true;
        break;
      }
      if (playback.controller.signal.aborted) throw new Error('已取消播报');
      if (!value?.length) continue;
      const bytes = new Uint8Array(carry.length + value.length);
      bytes.set(carry);
      bytes.set(value, carry.length);
      const evenLength = bytes.length - (bytes.length % 2);
      carry = bytes.subarray(evenLength);
      if (!evenLength) continue;
      const chunk = bytes.subarray(0, evenLength);
      if (native) {
        // A fetch read may coalesce several provider frames. The Android bridge
        // accepts at most 256 KiB per call, so keep each push well below it.
        for (let offset = 0; offset < chunk.length; offset += 32768)
          if (!window.PaperVoice.pushPcm(id, encodeBase64(chunk.subarray(offset, offset + 32768))))
            throw new Error('音频播放器已停止');
      } else {
        const samples = new Float32Array(chunk.length / 2);
        const view = new DataView(chunk.buffer, chunk.byteOffset, chunk.byteLength);
        for (let index = 0; index < samples.length; index++)
          samples[index] = view.getInt16(index * 2, true) / 32768;
        const buffer = audioContext.createBuffer(1, samples.length, 24000);
        buffer.copyToChannel(samples, 0);
        const source = audioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(audioContext.destination);
        nextAt = Math.max(nextAt, audioContext.currentTime + 0.04);
        source.start(nextAt);
        nextAt += buffer.duration;
      }
      if (!playback.started) playback.onStart?.();
      playback.started = true;
    }
    if (carry.length) throw new Error('音频流长度不完整');
    if (!playback.started) throw new Error('语音合成没有产生音频');
    if (native) {
      window.PaperVoice.finishPcm(id);
      nativeTimeout = setTimeout(
        () => nativeCallback(id, false),
        Math.min(120000, 8000 + text.length * 450),
      );
      if (!(await nativeCompletion)) throw new Error('音频播放未完成');
    } else {
      await new Promise((resolve) =>
        setTimeout(resolve, Math.max(0, (nextAt - audioContext.currentTime) * 1000 + 30)),
      );
    }
    return true;
  } finally {
    clearTimeout(nativeTimeout);
    if (native && window.__pcmOutputDone === nativeCallback) window.__pcmOutputDone = null;
    if (!streamEnded) reader.cancel().catch(() => {});
    if (audioContext) audioContext.close().catch(() => {});
  }
}

function playSpeech(
  text,
  waitForEnd,
  choices = [],
  captionText = text,
  prepared = null,
  onStart,
  options = {},
) {
  stopSpeechOutput();
  if (!text) {
    prepared?.cancel();
    return Promise.resolve(false);
  }
  window.dispatchEvent(
    new CustomEvent('paper-speech', { detail: { text: captionText, spoken: text, choices } }),
  );
  const id = String(++speechOutputId);
  let finish = () => {};
  const completed = waitForEnd
    ? new Promise((resolve) => {
        const timer = setTimeout(() => finish(false), Math.min(120000, 8000 + text.length * 450));
        finish = (played) => {
          clearTimeout(timer);
          if (speechWaiter?.id === id) speechWaiter = null;
          resolve(played);
        };
        speechWaiter = { id, finish };
      })
    : Promise.resolve(false);
  if (premiumSpeechEnabled && mode === 'child') {
    const playback = {
      id,
      controller: new AbortController(),
      started: false,
      onStart,
      rate: speechRate(options.rate, 1),
    };
    activePlayback = playback;
    void playPremiumSpeech(text, id, playback, prepared)
      .then(() => {
        if (activePlayback?.id === id) {
          activePlayback = null;
          finish(true);
        }
      })
      .catch((error) => {
        if (activePlayback?.id !== id || playback.controller.signal.aborted) return;
        activePlayback = null;
        console.warn('Cloud speech failed for this segment', error);
        window.PaperVoice?.stopPcm?.();
        startSystemSpeech(text, id, waitForEnd, finish, onStart, options);
      });
    return completed;
  }
  prepared?.cancel();
  startSystemSpeech(text, id, waitForEnd, finish, onStart, options);
  return completed;
}

export function speak(text, choices = [], captionText = text) {
  playSpeech(text, false, choices, captionText);
}

export function speakAndWait(
  text,
  choices = [],
  captionText = text,
  prepared = null,
  onStart,
  options = {},
) {
  return playSpeech(text, true, choices, captionText, prepared, onStart, options);
}

export function stopSpeechOutput() {
  if (activePlayback) {
    activePlayback.controller.abort();
    activePlayback = null;
  }
  if (speechWaiter) speechWaiter.finish(false);
  window.PaperVoice?.stopPcm?.();
  if (window.PaperVoice) window.PaperVoice.stop?.();
  else if ('speechSynthesis' in window) speechSynthesis.cancel();
}

export async function hasAutomaticSpeechPermission() {
  if (window.PaperVoice) {
    try {
      return Boolean(window.PaperVoice.hasMicrophonePermission?.());
    } catch {
      return false;
    }
  }
  if (!navigator.permissions?.query) return false;
  try {
    const permission = await navigator.permissions.query({ name: 'microphone' });
    return permission.state === 'granted';
  } catch {
    return false;
  }
}

export function startSpeechSession({
  onText,
  onError,
  onState = () => {},
  onPartial = () => {},
  onFinish = () => {},
  holdToTalk = false,
  collectUntilPause = false,
}) {
  activeSpeechSession?.cancel();
  let enabled = true;
  let canceled = false;
  let completed = false;
  let stopRequested = false;
  let recognition = null;
  let timer = null;
  let recognitionEnded = false;
  let finalIndex = 0;
  let partialCandidate = '';
  const parts = [];
  const continuous = holdToTalk || collectUntilPause;
  const native = Boolean(window.PaperVoice);
  const joined = () => parts.join(' ').trim();
  const addPart = (value) => {
    const part = String(value || '').trim();
    if (part && part !== parts.at(-1)) parts.push(part);
    partialCandidate = '';
    onPartial(joined());
  };
  const rememberPartial = (value) => {
    const partial = String(value || '').trim();
    const complete = joined();
    partialCandidate =
      complete && partial.startsWith(complete) ? partial.slice(complete.length).trim() : partial;
    onPartial([complete, partialCandidate].filter(Boolean).join(' ').trim());
  };
  const recoverSpeech = (value, error) => {
    if (value || error || !holdToTalk) return value;
    // The Android recognizer can return NO_MATCH after showing a useful interim result.
    // Finalizing commands must use a final transcript, not an unstable partial one.
    const candidate =
      partialCandidate.length >= 2 && !/(确认|授权|删除|结束|提交|同意)/.test(partialCandidate)
        ? partialCandidate
        : '';
    return [joined(), candidate].filter(Boolean).join(' ').trim();
  };
  const clear = () => {
    clearTimeout(timer);
    if (activeSpeechSession === session) activeSpeechSession = null;
  };
  const finish = (value, error, reason = '') => {
    if (canceled || completed) return;
    completed = true;
    enabled = false;
    if (native) window.PaperVoice.cancelListening();
    clear();
    onState('idle');
    const recovered = recoverSpeech(value, error);
    if (collectUntilPause && error) {
      // A recognizer failure can arrive after an earlier valid segment.
      // Expose that segment for a retry, but never dispatch it as the full reply.
      onError(error);
      onFinish({ text: '', partial: recovered, error, reason });
      return;
    }
    if (recovered) onText(recovered);
    else if (error || holdToTalk) onError(error || '没有听清，请按住说话重试');
    onFinish({ text: recovered, error, reason });
  };
  const run = () => {
    if (!enabled || canceled || stopRequested) return;
    onState('starting');
    if (native) {
      window.__nativeSpeechResult = (value, error, reason) => {
        if (canceled || completed) return;
        finish(value || joined(), error, reason);
      };
      window.__nativeSpeechPartial = (value) => {
        if (enabled && continuous) rememberPartial(value);
      };
      window.__nativeSpeechState = (state) => {
        if (enabled) onState(state);
      };
      if (holdToTalk && window.PaperVoice.listenHold) window.PaperVoice.listenHold();
      else if (collectUntilPause && window.PaperVoice.listenReply) window.PaperVoice.listenReply();
      else window.PaperVoice.listen();
      return;
    }
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      finish('', '当前设备不支持语音识别，请使用屏幕按钮');
      return;
    }
    recognition = new Recognition();
    recognition.lang = 'zh-CN';
    recognition.continuous = false;
    recognition.interimResults = continuous;
    finalIndex = 0;
    recognitionEnded = false;
    let finished = false;
    let error = '';
    recognition.onstart = () => onState('listening');
    recognition.onspeechstart = () => onState('speaking');
    recognition.onspeechend = () => onState('processing');
    recognition.onresult = (event) => {
      if (!continuous) {
        finished = true;
        finish(event.results[0][0].transcript, '');
        return;
      }
      let partial = '';
      for (let index = finalIndex; index < event.results.length; index++) {
        const result = event.results[index];
        if (result.isFinal) {
          addPart(result[0].transcript);
          finalIndex = index + 1;
        } else partial = result[0].transcript;
      }
      rememberPartial(partial);
      if (collectUntilPause && joined()) {
        finished = true;
        finish(joined(), '');
      }
    };
    recognition.onerror = (event) => {
      error = event.error === 'no-speech' ? '' : '语音识别暂不可用，请重试或使用屏幕按钮';
    };
    recognition.onend = () => {
      if (finished || canceled || completed) return;
      recognitionEnded = true;
      if (holdToTalk && !stopRequested && !error) onState('ended');
      else finish(joined(), error);
    };
    recognition.start();
  };
  const session = {
    stop() {
      if (canceled || !enabled) return;
      stopRequested = true;
      enabled = false;
      clearTimeout(timer);
      onState('processing');
      if (native) window.PaperVoice.stopListening();
      else if (recognition && !recognitionEnded) recognition.stop();
      else {
        finish(joined(), '');
        return;
      }
      timer = setTimeout(() => {
        finish(joined(), '', 'timeout');
      }, 5000);
    },
    cancel() {
      if (canceled) return;
      canceled = true;
      completed = true;
      enabled = false;
      if (native) window.PaperVoice.cancelListening();
      else recognition?.abort();
      clear();
      onState('idle');
    },
  };
  activeSpeechSession = session;
  run();
  return session;
}

export function startRecordedSpeechSession({
  onText,
  onError,
  onState = () => {},
  onFinish = () => {},
  maxMs = 15000,
}) {
  activeSpeechSession?.cancel();
  let canceled = false;
  let completed = false;
  let stopRequested = false;
  let recorder = null;
  let stream = null;
  let timer = null;
  const request = new AbortController();
  const chunks = [];
  const stopTracks = () => stream?.getTracks().forEach((track) => track.stop());
  const release = () => {
    clearTimeout(timer);
    stopTracks();
    if (activeSpeechSession === session) activeSpeechSession = null;
  };
  const finish = (text = '', error = '') => {
    if (canceled || completed) return;
    completed = true;
    release();
    onState('idle');
    if (text) onText(text);
    else if (error) onError(error);
    onFinish({ text, error });
  };
  const upload = async () => {
    if (canceled || completed) return;
    stopTracks();
    onState('processing');
    const blob = new Blob(chunks, { type: recorder.mimeType });
    if (!blob.size) {
      finish('', '没有录到语音，请重试');
      return;
    }
    if (blob.size > 900_000) {
      finish('', '语音过长，请分段再说');
      return;
    }
    const body = new FormData();
    body.append('audio', blob, blob.type.includes('mp4') ? 'voice.mp4' : 'voice.webm');
    try {
      const result = await api('/v1/voice/transcribe', {
        method: 'POST',
        body,
        signal: request.signal,
      });
      const text = String(result.text || '').trim();
      finish(text, text ? '' : '没有听清，请再说一遍');
    } catch (error) {
      finish('', error.message || '语音转写暂不可用');
    }
  };
  const session = {
    stop() {
      if (canceled || completed || stopRequested) return;
      stopRequested = true;
      clearTimeout(timer);
      if (recorder?.state === 'recording') recorder.stop();
    },
    cancel() {
      if (canceled || completed) return;
      canceled = true;
      clearTimeout(timer);
      request.abort();
      if (recorder?.state === 'recording') recorder.stop();
      release();
      onState('idle');
    },
  };
  activeSpeechSession = session;
  onState('starting');
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    finish('', '当前设备不支持语音录制');
    return session;
  }
  navigator.mediaDevices
    .getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
    .then((audioStream) => {
      stream = audioStream;
      if (canceled) {
        release();
        return;
      }
      const mimeType = ['audio/webm;codecs=opus', 'audio/mp4'].find((type) =>
        MediaRecorder.isTypeSupported(type),
      );
      if (!mimeType) {
        finish('', '当前设备不支持短语音录制');
        return;
      }
      recorder = new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 24_000 });
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      recorder.onstop = upload;
      recorder.onerror = () => finish('', '语音录制失败，请重试');
      recorder.start();
      onState('listening');
      timer = setTimeout(() => session.stop(), maxMs);
      if (stopRequested) recorder.stop();
    })
    .catch(() => finish('', '麦克风暂不可用，请检查权限后重试'));
  return session;
}

export async function ticket() {
  const data = await api('/v1/realtime/ticket', { method: 'POST' });
  return data.ticket;
}

export async function connectRealtime(onMessage) {
  const t = await ticket();
  const url = baseUrl().replace(/^http/, 'ws') + '/v1/realtime';
  const socket = new WebSocket(url, ['k12', t]);
  socket.onmessage = (event) => {
    try {
      onMessage(JSON.parse(event.data));
    } catch {
      /* ignore */
    }
  };
  return socket;
}
