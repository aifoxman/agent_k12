import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { createRoot } from 'react-dom/client';
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Camera,
  Check,
  CheckCircle2,
  ClipboardCheck,
  CircleHelp,
  Clock3,
  Eye,
  Headphones,
  Link2,
  Mic,
  MicOff,
  Pause,
  Play,
  Plus,
  Radio,
  RefreshCw,
  RotateCw,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
  Volume2,
  X,
} from 'lucide-react';
import {
  api,
  CONNECTION_ERROR,
  configureSpeech,
  connectRealtime,
  hasAutomaticSpeechPermission,
  key,
  mode,
  patch,
  post,
  privateImage,
  prepareSpeech,
  save,
  saved,
  speak,
  speakAndWait,
  streamApi,
  startRecordedSpeechSession,
  startSpeechSession,
  stopSpeechOutput,
} from './api';
import { measurePaperFrame, recognizePaperText } from './paperPreflight';
import { openCameraSource } from './camera-source';
import { recognizeHintAnchors } from './paper-vision-anchors';
import { logicalTeachingSteps, teachingStepIndex } from './teaching-progress';
import { keepPendingCameraPermission } from './camera-lifecycle';
import { cameraRotation, canRotateCamera, waitForCameraDimensions } from './camera-view';
import {
  isTaskReadCommand,
  parseLocatedQuestionReference,
  parseLearningSupport,
  parseVoiceReply,
  routeUnmatchedVoiceReply,
} from './voice-intents';
import { likelyPromptEcho } from './voice-echo';
import { choicesForPrompt, markedSpeechParts } from './interaction-copy';
import { needsSpokenReply, shouldProbeHintAttempt } from './hint-followup';
import {
  correctionAllowedNow,
  correctionTestBypassEnabled,
  visibleCorrectionAllowed,
} from './session-time';
import { formatStudyTime, parseTimerCommand, useSessionTimer } from './session-timer';
import {
  createStallWatch,
  sampleStallWatch,
  settleAmbiguousPage,
  settleIdleCheckin,
  settleStallProbe,
} from './stall-watch';
import { completedVoiceReply, createReplyPause } from './voice-turn';
import { TaskReviewCard } from './task-review';
import { sameSubject } from './subject-matching';
import { WorkedSolution } from './worked-solution';
import {
  TeachingInk,
  TeachingPaperFrame,
  TeachingScratch,
  TeachingStepControls,
} from './teaching-ink';
import { splitTeachingBeat, teachingSteps } from './teaching-steps';
import { createTeachingPlayback, teachingPause } from './teaching-playback';
import { teachingBeatDuration, teachingPaperPresentation } from './teaching-layout';
import {
  latestTeachingDraft,
  teachingDraftFromTarget,
  teachingDraftInScope,
} from './teaching-notes';
import { mergeTeachingTarget, visibleTeachingDraft } from './teaching-session';
import { createHintContext, finishConfirmationText } from './hint-context';
import {
  READING_QUESTION,
  THINKING_QUESTION,
  asksForCompleteTeaching,
  guidanceInScope,
  guidanceRequest,
  teachingExplanation,
  teachingTier,
} from './teaching-levels';
import { TaskEditor } from './task-editor';
import { pendingTaskReminders, reminderSpeech } from './task-reminders';
import { guardVoiceHoldSelection, setVoiceHoldGestureActive } from './voice-hold-gesture';
import { paperMarkEdge, visiblePaperMarks } from './paper-marks';
import { dashboardSpeechConfiguration, premiumVoiceQuota } from './premium-voice';
import { createPenHintScheduler, penSelectionBounds, penSelectionReady } from './pen-selection';
import './style.css';

function selectSpeechAction(word, spoken) {
  window.dispatchEvent(new CustomEvent('paper-speech-choice', { detail: { word, spoken } }));
}

function clearSpeechCaption() {
  window.dispatchEvent(new CustomEvent('paper-caption-clear'));
}

function App() {
  const [apiAddress, setApiAddress] = useState(
    saved('api') ||
      import.meta.env.VITE_API_URL ||
      (location.hostname === 'localhost' ? 'http://localhost:8000' : ''),
  );
  const [connectionReady, setConnectionReady] = useState(
    Boolean(saved('api') || import.meta.env.VITE_API_URL || location.hostname === 'localhost'),
  );
  const [token, setToken] = useState(saved('token'));
  const [dashboard, setDashboard] = useState(null);
  const dashboardFamilyRef = useRef(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [activity, setActivity] = useState('');
  const [spokenText, setSpokenText] = useState('');
  const [spokenAudioText, setSpokenAudioText] = useState('');
  const [spokenChoices, setSpokenChoices] = useState([]);
  const [captionExpanded, setCaptionExpanded] = useState(false);
  const [captionOverflow, setCaptionOverflow] = useState(false);
  const captionRef = useRef(null);
  const captionBodyRef = useRef(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [transferClaim, setTransferClaim] = useState(null);
  const [transferBusy, setTransferBusy] = useState(false);
  const [aiPreferences, setAiPreferences] = useState(null);
  const [aiPreferencesSaving, setAiPreferencesSaving] = useState(false);
  useEffect(() => {
    if (!settingsOpen || mode !== 'parent' || !token) return;
    api('/v1/families/ai-preferences')
      .then(setAiPreferences)
      .catch(() => setAiPreferences(null));
  }, [settingsOpen, token]);
  const saveAiPreferences = async () => {
    if (!aiPreferences || aiPreferencesSaving) return;
    setAiPreferencesSaving(true);
    try {
      const savedPreferences = await patch('/v1/families/ai-preferences', {
        solver_model: aiPreferences.solver_model,
        grading_model: aiPreferences.grading_model,
        locator_model: aiPreferences.locator_model,
        tutor_model: aiPreferences.tutor_model,
        precompute_enabled: aiPreferences.precompute_enabled,
      });
      setAiPreferences(savedPreferences);
      setNotice('辅导模型设置已保存');
    } catch (error) {
      setError(error.message);
    } finally {
      setAiPreferencesSaving(false);
    }
  };
  useEffect(() => {
    const pending = new Map();
    let timer;
    const onActivity = (event) => {
      const { id, label } = event.detail;
      if (label) pending.set(id, label);
      else pending.delete(id);
      clearTimeout(timer);
      if (pending.size) timer = setTimeout(() => setActivity([...pending.values()].at(-1)), 350);
      else setActivity('');
    };
    const onSpeech = (event) => {
      setSpokenText(event.detail.text);
      setSpokenAudioText(event.detail.spoken || event.detail.text);
      setSpokenChoices(event.detail.choices || []);
      setCaptionExpanded(false);
      // Identical replay keeps its valid overflow measurement. Clearing it
      // here would hide the toggle without changing the measurement inputs.
    };
    const onSpeechProgress = (event) => {
      setSpokenText(event.detail.text);
      setSpokenAudioText(event.detail.text);
      setSpokenChoices([]);
    };
    const onChoiceUsed = () => setSpokenChoices([]);
    const onCaptionClear = () => {
      setSpokenText('');
      setSpokenAudioText('');
      setSpokenChoices([]);
      setCaptionExpanded(false);
    };
    const onCaptionDisplay = (event) => setCaptionExpanded(Boolean(event.detail?.expanded));
    window.addEventListener('paper-activity', onActivity);
    window.addEventListener('paper-speech', onSpeech);
    window.addEventListener('paper-speech-progress', onSpeechProgress);
    window.addEventListener('paper-speech-choice-used', onChoiceUsed);
    window.addEventListener('paper-caption-display', onCaptionDisplay);
    window.addEventListener('paper-caption-clear', onCaptionClear);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('paper-activity', onActivity);
      window.removeEventListener('paper-speech', onSpeech);
      window.removeEventListener('paper-speech-progress', onSpeechProgress);
      window.removeEventListener('paper-speech-choice-used', onChoiceUsed);
      window.removeEventListener('paper-caption-display', onCaptionDisplay);
      window.removeEventListener('paper-caption-clear', onCaptionClear);
    };
  }, []);
  useEffect(() => {
    if (!spokenText || !captionRef.current || !captionBodyRef.current) return;
    const caption = captionRef.current;
    const body = captionBodyRef.current;
    const measure = () => {
      const app = caption.closest('.app');
      if (app?.querySelector('.child-main.immersive-active.teaching-active')) {
        const style = getComputedStyle(body);
        const lineHeight =
          Number.parseFloat(style.lineHeight) || Number.parseFloat(style.fontSize) * 1.5;
        const previewHeight = Math.ceil(2 * lineHeight);
        // The floating caption follows enlarged text without reserving a whole
        // grid row. Its small safe strip keeps the last written line readable.
        app.style.setProperty('--teaching-caption-preview-height', `${previewHeight}px`);
        app.style.setProperty('--teaching-caption-height', `${Math.max(44, previewHeight) + 16}px`);
      }
      const previewHeight =
        Number.parseFloat(getComputedStyle(caption).getPropertyValue('--caption-preview-height')) ||
        105;
      setCaptionOverflow(body.scrollHeight > previewHeight + 2);
    };
    const frame = requestAnimationFrame(measure);
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null;
    observer?.observe(caption);
    observer?.observe(body);
    window.addEventListener('resize', measure);
    return () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [spokenText, captionExpanded]);
  const refresh = async () => {
    if (saved('token')) {
      try {
        const result = await api(
          `/v1/families/dashboard?offset_minutes=${-new Date().getTimezoneOffset()}`,
        );
        if (
          mode === 'child' &&
          dashboardFamilyRef.current &&
          dashboardFamilyRef.current !== result.family.id
        ) {
          setTransferClaim(null);
          setSettingsOpen(false);
        }
        dashboardFamilyRef.current = result.family.id;
        configureSpeech(dashboardSpeechConfiguration(result, mode === 'child'));
        setDashboard({ ...result, received_mono_ms: performance.now() });
        setError((previous) => (previous === CONNECTION_ERROR ? '' : previous));
      } catch (error) {
        if (error.message !== 'Authentication required') throw error;
        if (mode === 'child') {
          try {
            const status = await post('/v1/devices/status', {
              device_id: saved('device_id'),
              device_secret: saved('device_secret'),
            });
            if (status.token) {
              save('token', status.token);
              setToken(status.token);
              setError('');
              return;
            }
          } catch {
            // An old device pairing can be renewed from the pairing screen.
          }
        }
        localStorage.removeItem(key('token'));
        configureSpeech();
        setToken('');
        setDashboard(null);
        setError('');
        if (mode === 'parent') setNotice('登录已过期，请重新登录');
      }
    }
  };
  useEffect(() => {
    if (!token) return;
    refresh().catch((e) => setError(e.message));
    const id = setInterval(() => refresh().catch(() => {}), 8000);
    return () => clearInterval(id);
  }, [token]);
  const showError = (e) => {
    setError(e.message || String(e));
    setTimeout(() => setError(''), 7000);
  };
  const showNotice = (message) => {
    setNotice(message);
    setTimeout(() => setNotice(''), 6000);
  };
  const createTransferClaim = async () => {
    if (transferBusy) return;
    const deviceId = saved('device_id');
    const deviceSecret = saved('device_secret');
    if (!deviceId || !deviceSecret) {
      showError(new Error('这台平板缺少原配对凭据，请重新配对'));
      return;
    }
    setTransferBusy(true);
    try {
      const claim = await post('/v1/devices/transfer-claim', {
        device_id: deviceId,
        device_secret: deviceSecret,
      });
      setTransferClaim(claim);
    } catch (error) {
      showError(error);
    } finally {
      setTransferBusy(false);
    }
  };
  useEffect(() => {
    if (!transferClaim) return;
    const id = setInterval(async () => {
      if (Date.now() / 1000 >= transferClaim.expires_at) {
        setTransferClaim(null);
        return;
      }
      try {
        const status = await post('/v1/devices/status', {
          device_id: saved('device_id'),
          device_secret: saved('device_secret'),
        });
        if (status.family_id && status.family_id !== transferClaim.current_family_id) {
          setTransferClaim(null);
          setSettingsOpen(false);
          setDashboard(null);
          await refresh();
        }
      } catch {
        // Keep the code visible while the device reconnects.
      }
    }, 3000);
    return () => clearInterval(id);
  }, [transferClaim]);
  const login = (value) => {
    save('token', value);
    setToken(value);
  };
  const logout = () => {
    configureSpeech();
    localStorage.removeItem(key('token'));
    setToken('');
    setDashboard(null);
  };
  const saveApi = () => {
    try {
      const address = new URL(apiAddress.trim());
      if (
        address.protocol !== 'https:' &&
        address.hostname !== 'localhost' &&
        address.hostname !== '127.0.0.1'
      )
        throw new Error('请填写 HTTPS 地址');
      save('api', address.origin);
      setApiAddress(address.origin);
      setConnectionReady(true);
      setSettingsOpen(false);
      showNotice('连接地址已保存');
      if (token) refresh().catch(showError);
    } catch (e) {
      showError(e);
    }
  };
  const extraCaptionChoices = spokenChoices.filter((word) => !spokenText.includes(word));
  const captionChoices = captionOverflow && !captionExpanded ? spokenChoices : extraCaptionChoices;
  return (
    <div className={`app ${mode}`}>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">纸</span>
          <span>
            纸间 <small>HOMEWORK COMPANION</small>
          </span>
        </div>
        <div className="top-actions">
          <span className="mode-tag">{mode === 'child' ? '孩子端 · 平板' : '家长端 · 手机'}</span>
          <button className="icon-button" title="设置" onClick={() => setSettingsOpen(true)}>
            <Settings2 size={19} />
          </button>
        </div>
      </header>
      {error && (
        <div className="toast error" role="alert">
          {error}
          {error === CONNECTION_ERROR && token && (
            <button type="button" onClick={() => refresh().catch(showError)}>
              重试连接
            </button>
          )}
          <button onClick={() => setError('')}>
            <X size={16} />
          </button>
        </div>
      )}
      {notice && (
        <div className="toast" role="status">
          {notice}
        </div>
      )}
      {activity && (
        <div className="activity-banner" role="status">
          <span className="activity-spinner" /> {activity}
        </div>
      )}
      {spokenText && (
        <div
          ref={captionRef}
          className={`speech-caption ${activity ? 'with-activity' : ''} ${captionExpanded ? 'expanded' : ''} ${captionOverflow ? 'has-overflow' : ''}`}
          role="status"
        >
          <div className="speech-caption-header">
            <span className="speech-caption-label">播报文字</span>
            <div className="speech-caption-actions">
              <button
                type="button"
                title="再次播报"
                aria-label="再次播报这段文字"
                onClick={() => speak(spokenAudioText, spokenChoices, spokenText)}
              >
                <Volume2 size={17} />
              </button>
              <button
                type="button"
                title="唤起语音互动"
                aria-label="唤起语音互动，听取一次回应"
                onClick={() =>
                  window.dispatchEvent(
                    new CustomEvent('paper-speech-interact', {
                      detail: { spoken: spokenAudioText, handled: false },
                    }),
                  )
                }
              >
                <Mic size={17} />
              </button>
              <button
                type="button"
                title="关闭播报文字"
                aria-label="关闭播报文字"
                onClick={() => {
                  setSpokenText('');
                  setSpokenAudioText('');
                  setSpokenChoices([]);
                }}
              >
                <X size={17} />
              </button>
            </div>
          </div>
          <div className="speech-caption-content" id="speech-caption-content">
            <p ref={captionBodyRef}>
              {markedSpeechParts(spokenText, spokenChoices).map((part, index) =>
                part.highlighted && !(captionOverflow && !captionExpanded) ? (
                  <button
                    key={index}
                    type="button"
                    className="speech-inline-action"
                    onClick={() => selectSpeechAction(part.text, spokenAudioText)}
                    aria-label={`点击执行：${part.text}`}
                  >
                    {part.text}
                  </button>
                ) : (
                  part.text
                ),
              )}
            </p>
            {captionChoices.length > 0 && (
              <div className="speech-answer-words" aria-label="可以说的回答">
                <span>也可说或点</span>
                {captionChoices.map((word) => (
                  <button
                    key={word}
                    type="button"
                    onClick={() => selectSpeechAction(word, spokenAudioText)}
                  >
                    {word}
                  </button>
                ))}
              </div>
            )}
          </div>
          {captionOverflow && (
            <button
              type="button"
              className="speech-caption-toggle"
              aria-controls="speech-caption-content"
              aria-expanded={captionExpanded}
              onClick={() => setCaptionExpanded((value) => !value)}
            >
              {captionExpanded ? '收起' : '展开全文'}
              <span aria-hidden="true">{captionExpanded ? '⌃' : '⌄'}</span>
            </button>
          )}
        </div>
      )}
      {settingsOpen && (
        <div className="modal-shade">
          <div className="modal">
            <div className="modal-head">
              <h2>连接设置</h2>
              <button className="icon-button" onClick={() => setSettingsOpen(false)}>
                <X />
              </button>
            </div>
            <p>填入服务端 HTTPS 地址。平板和手机需连接同一服务。</p>
            <label>
              API 地址
              <input
                value={apiAddress}
                onChange={(e) => setApiAddress(e.target.value)}
                placeholder="https://api.example.com"
              />
            </label>
            {mode === 'parent' && aiPreferences && (
              <div className="model-preferences">
                <h3>按任务选模型</h3>
                <p className="small muted">
                  整页解题准备提示依据；批改与单题复检独立选模型。6.1 Sol 无可用通道时自动使用
                  Astra。
                </p>
                {[
                  ['solver_model', '整页解题'],
                  ['grading_model', '批改与单题复检'],
                  ['locator_model', '题目定位'],
                  ['tutor_model', '预解后的互动提示'],
                ].map(([field, label]) => (
                  <label key={field}>
                    {label}
                    <select
                      value={aiPreferences[field]}
                      onChange={(event) =>
                        setAiPreferences({ ...aiPreferences, [field]: event.target.value })
                      }
                    >
                      {aiPreferences.available_models.map((modelName) => (
                        <option key={modelName} value={modelName}>
                          {modelName}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
                <label className="model-precompute-toggle">
                  <input
                    type="checkbox"
                    checked={aiPreferences.precompute_enabled}
                    onChange={(event) =>
                      setAiPreferences({
                        ...aiPreferences,
                        precompute_enabled: event.target.checked,
                      })
                    }
                  />
                  检查纸面后异步准备整页题目
                </label>
                <button
                  className="button dark"
                  disabled={aiPreferencesSaving}
                  onClick={saveAiPreferences}
                >
                  保存辅导设置
                </button>
              </div>
            )}
            {mode === 'child' && token && (
              <div className="model-preferences">
                <h3>更换家长账号</h3>
                <p className="small muted">
                  在这台平板上生成换绑码，再由新家长账号输入。旧作业记录留在原家庭；
                  输入配对码前，当前连接不会改变。
                </p>
                {transferClaim && <p className="pair-code">{transferClaim.pairing_code}</p>}
                <button
                  className="button dark"
                  disabled={transferBusy}
                  onClick={createTransferClaim}
                >
                  {transferClaim ? '重新生成换绑码' : '生成换绑码'}
                </button>
              </div>
            )}
            <div className="modal-actions">
              <button className="button primary" onClick={saveApi}>
                保存地址
              </button>
              {token && (
                <button className="button ghost" onClick={logout}>
                  退出登录
                </button>
              )}
            </div>
            <p className="small muted">
              测试环境可使用 localhost。Android 安装包需要 HTTPS 服务地址。
            </p>
          </div>
        </div>
      )}
      {!connectionReady ? (
        <main className="pair-page">
          <div className="pair-art">
            <Link2 size={70} strokeWidth={1.2} />
          </div>
          <div className="section-kicker">CONNECT TO YOUR FAMILY SPACE</div>
          <h1>先连接家庭服务</h1>
          <p>在平板和家长手机上填写同一个 HTTPS 服务地址。</p>
          <div className="connect-form">
            <input
              value={apiAddress}
              onChange={(e) => setApiAddress(e.target.value)}
              placeholder="https://api.example.com"
            />
            <button className="button primary" onClick={saveApi}>
              连接服务 <ArrowRight size={17} />
            </button>
          </div>
        </main>
      ) : !token ? (
        mode === 'parent' ? (
          <ParentAuth onLogin={login} onError={showError} />
        ) : (
          <ChildPair onLogin={login} onError={showError} />
        )
      ) : !dashboard ? (
        <main className="loading">
          <RefreshCw className="spin" />
          <p>正在同步家庭作业…</p>
        </main>
      ) : mode === 'parent' ? (
        <ParentHome
          dashboard={dashboard}
          refresh={refresh}
          onError={showError}
          onNotice={showNotice}
          spokenText={spokenText}
        />
      ) : (
        <ChildHome
          key={dashboard.family.id}
          dashboard={dashboard}
          refresh={refresh}
          onError={showError}
          onNotice={showNotice}
          spokenText={spokenText}
        />
      )}
      <footer className="footer">
        为孩子留一点独立思考的空间 <span>·</span> 作业记录最多保留 60 天
      </footer>
    </div>
  );
}

function handleCaptionDisplayCommand(text) {
  const spoken = String(text || '').trim();
  const expanded = /^(展开|显示|查看)(播报文字|字幕|全文)$/.test(spoken)
    ? true
    : /^(收起|折叠)(播报文字|字幕|全文)$/.test(spoken)
      ? false
      : null;
  if (expanded === null) return false;
  window.dispatchEvent(new CustomEvent('paper-caption-display', { detail: { expanded } }));
  return true;
}

function voiceErrorWithTranscriptionHint(error, dashboard, parent = false) {
  const message = error?.message || String(error);
  if (
    !dashboard.asr_available ||
    dashboard.family.audio_transcription_allowed ||
    !/语音识别.*(不可用|失败)|没有可用的语音识别服务/.test(message)
  )
    return error;
  return new Error(
    `${message}。${parent ? '可在本页“短语音转写”中点“允许”' : '请家长在手机端“短语音转写”中点“允许”'}`,
  );
}

function VoiceControls({
  onText,
  onError,
  remoteTranscription = false,
  disabled = false,
  disabledLabel = '通话中，语音指令已暂停',
  compact = false,
}) {
  const [voiceMode, setVoiceMode] = useState('off');
  const [voiceState, setVoiceState] = useState('idle');
  const [heard, setHeard] = useState('');
  const [partial, setPartial] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const [slideToCancel, setSlideToCancel] = useState(false);
  const [pressing, setPressing] = useState(false);
  const sessionRef = useRef(null);
  const tapTimerRef = useRef(null);
  const startRef = useRef(null);
  const modeRef = useRef('off');
  const pointerRef = useRef(null);
  const voiceButtonRef = useRef(null);
  const finishRef = useRef(null);
  const cancelRef = useRef(false);
  const startedAtRef = useRef(0);
  const handlersRef = useRef({ onText, onError });
  handlersRef.current = { onText, onError };
  const stopAll = () => {
    clearTimeout(tapTimerRef.current);
    sessionRef.current?.cancel();
    sessionRef.current = null;
    if (pointerRef.current) setVoiceHoldGestureActive(false);
    pointerRef.current = null;
    setPressing(false);
    cancelRef.current = false;
    modeRef.current = 'off';
    setVoiceMode('off');
    setVoiceState('idle');
    setPartial('');
    setSlideToCancel(false);
    window.dispatchEvent(new Event('paper-manual-voice-end'));
  };
  useEffect(() => {
    const removeGuards = guardVoiceHoldSelection(voiceButtonRef.current);
    return () => {
      removeGuards();
      if (pointerRef.current) setVoiceHoldGestureActive(false);
    };
  }, []);
  useEffect(() => {
    if (voiceMode !== 'hold' || !pressing) return;
    const interval = setInterval(() => {
      setElapsed(Math.floor((performance.now() - startedAtRef.current) / 1000));
    }, 250);
    return () => clearInterval(interval);
  }, [voiceMode, pressing]);
  useEffect(() => {
    const finishOutside = (event) => {
      if (pointerRef.current?.id === event.pointerId) finishRef.current?.(event, cancelRef.current);
    };
    window.addEventListener('pointerup', finishOutside);
    window.addEventListener('pointercancel', finishOutside);
    return () => {
      window.removeEventListener('pointerup', finishOutside);
      window.removeEventListener('pointercancel', finishOutside);
    };
  }, []);
  useEffect(() => {
    const background = () => stopAll();
    const interact = (event) => {
      queueMicrotask(() => {
        if (!event.detail?.handled) startRef.current?.('tap');
      });
    };
    window.addEventListener('appbackground', background);
    window.addEventListener('paper-speech-interact', interact);
    return () => {
      window.removeEventListener('appbackground', background);
      window.removeEventListener('paper-speech-interact', interact);
      sessionRef.current?.cancel();
      clearTimeout(tapTimerRef.current);
      window.dispatchEvent(new Event('paper-manual-voice-end'));
    };
  }, []);
  useEffect(() => {
    if (disabled) stopAll();
  }, [disabled]);
  useEffect(() => {
    if (sessionRef.current) stopAll();
  }, [remoteTranscription]);
  const start = (requestedMode = 'hold') => {
    if (disabled) return;
    window.dispatchEvent(new Event('paper-manual-voice-start'));
    clearTimeout(tapTimerRef.current);
    sessionRef.current?.cancel();
    modeRef.current = requestedMode;
    startedAtRef.current = performance.now();
    setElapsed(0);
    setHeard('');
    setPartial('');
    setVoiceMode(requestedMode);
    setVoiceState('starting');
    sessionRef.current = (remoteTranscription ? startRecordedSpeechSession : startSpeechSession)({
      holdToTalk: requestedMode === 'hold',
      maxMs: requestedMode === 'hold' ? 30000 : 8000,
      onText: (text) => {
        setHeard(text);
        handlersRef.current.onText(text);
      },
      onPartial: setPartial,
      onError: (error) => handlersRef.current.onError(error),
      onState: (state) => {
        setVoiceState(state);
        if (state === 'idle' && modeRef.current !== 'off') {
          clearTimeout(tapTimerRef.current);
          modeRef.current = 'off';
          setVoiceMode('off');
          window.dispatchEvent(new Event('paper-manual-voice-end'));
        }
      },
    });
    if (requestedMode === 'tap')
      tapTimerRef.current = setTimeout(() => sessionRef.current?.stop(), 8000);
  };
  startRef.current = start;
  const release = () => {
    if (modeRef.current === 'hold') sessionRef.current?.stop();
  };
  const finishPointer = (event, canceled = false) => {
    if (pointerRef.current?.id !== event.pointerId) return;
    setVoiceHoldGestureActive(false);
    pointerRef.current = null;
    setPressing(false);
    cancelRef.current = false;
    setSlideToCancel(false);
    if (canceled) stopAll();
    else release();
  };
  finishRef.current = finishPointer;
  const holding = voiceMode === 'hold' && pressing;
  const microphoneOpen = holding && !['ended', 'segment', 'processing'].includes(voiceState);
  const endedLabel = partial ? '系统收音已结束，松开发送' : '系统收音已结束，松手重试';
  const status = disabled
    ? disabledLabel
    : voiceMode === 'off'
      ? '麦克风未开启'
      : voiceState === 'permission'
        ? '请在系统弹窗中允许麦克风'
        : holding && voiceState === 'ended'
          ? endedLabel
          : holding && (voiceState === 'segment' || voiceState === 'processing')
            ? '正在识别刚才的话，松手后发送'
            : holding
              ? '正在收音，松开发送'
              : voiceState === 'processing'
                ? '正在识别刚才的话…'
                : voiceState === 'speaking'
                  ? '正在听你说话'
                  : voiceState === 'starting'
                    ? '正在开启麦克风…'
                    : voiceMode === 'tap'
                      ? '正在听你说话，最多 8 秒'
                      : '按住说话中，松开发送';
  return (
    <div className={`voice-controls ${compact ? 'compact' : ''} ${holding ? 'is-active' : ''}`}>
      <div className="voice-status" role="status">
        <span className={`voice-status-dot ${microphoneOpen ? 'on' : ''}`} />
        <strong>{status}</strong>
      </div>
      {heard && <small className="voice-heard">刚听到：{heard}</small>}
      {holding && (
        <div
          className={`voice-hold-feedback ${slideToCancel ? 'cancel' : ''} ${voiceState === 'ended' ? 'ended' : ''}`}
          role="status"
        >
          <span className="voice-hold-symbol">
            {slideToCancel ? (
              <X size={21} />
            ) : voiceState === 'ended' ? (
              <MicOff size={21} />
            ) : (
              <Mic size={21} />
            )}
          </span>
          <div className="voice-hold-copy">
            <strong>
              {slideToCancel
                ? '松手取消'
                : voiceState === 'ended'
                  ? endedLabel
                  : microphoneOpen
                    ? '正在收音'
                    : '正在识别'}
            </strong>
            <span>
              {slideToCancel
                ? '移回按钮可继续'
                : voiceState === 'ended'
                  ? '本次麦克风已关闭'
                  : '松开发送 · 上滑取消'}
            </span>
            {partial && <small>{partial}</small>}
          </div>
          <time>
            {String(Math.floor(elapsed / 60)).padStart(2, '0')}:
            {String(elapsed % 60).padStart(2, '0')}
          </time>
        </div>
      )}
      <div className="voice-actions">
        <button
          ref={voiceButtonRef}
          type="button"
          className={`voice-hold ${holding ? 'pressed' : ''} ${slideToCancel ? 'cancel' : ''}`}
          disabled={disabled}
          onPointerDown={(event) => {
            if (pointerRef.current || event.button !== 0) return;
            event.preventDefault();
            event.currentTarget.setPointerCapture(event.pointerId);
            setVoiceHoldGestureActive(true);
            pointerRef.current = { id: event.pointerId, startY: event.clientY };
            setPressing(true);
            cancelRef.current = false;
            setSlideToCancel(false);
            start('hold');
          }}
          onPointerMove={(event) => {
            if (pointerRef.current?.id !== event.pointerId) return;
            const canceled = event.clientY < pointerRef.current.startY - 80;
            if (canceled !== cancelRef.current) {
              cancelRef.current = canceled;
              setSlideToCancel(canceled);
            }
          }}
          onPointerUp={(event) => finishPointer(event, cancelRef.current)}
          onPointerCancel={(event) => finishPointer(event, cancelRef.current)}
          onContextMenu={(event) => event.preventDefault()}
          onKeyDown={(event) => {
            if ((event.key === ' ' || event.key === 'Enter') && !event.repeat) {
              event.preventDefault();
              setPressing(true);
              start('hold');
            }
          }}
          onKeyUp={(event) => {
            if (event.key === ' ' || event.key === 'Enter') {
              setPressing(false);
              release();
            }
          }}
        >
          <Mic size={18} />{' '}
          {holding
            ? slideToCancel
              ? '松手取消'
              : voiceState === 'ended' && !partial
                ? '松手重试'
                : '松开发送'
            : voiceState === 'processing'
              ? '正在识别…'
              : '按住说话'}
        </button>
      </div>
      {!compact && (
        <small className="voice-note">
          按住说话，松开发送；上滑取消。首次使用会请求系统麦克风权限。
        </small>
      )}
    </div>
  );
}

function useTimedVoiceReply(
  onReply,
  enabled = true,
  remoteTranscription = false,
  fallbackTip = '',
) {
  const [status, setStatus] = useState(null);
  const sequenceRef = useRef(0);
  const sessionRef = useRef(null);
  const clockRef = useRef(null);
  const deadlineRef = useRef(null);
  const pauseRef = useRef(null);
  const hideRef = useRef(null);
  const manualRef = useRef(false);
  const manualReplyRef = useRef(null);
  const pendingRef = useRef(null);
  const activePromptRef = useRef(null);
  const tappablePromptRef = useRef(null);
  const askRef = useRef(null);
  const lastPromptEndRef = useRef(0);
  const enabledRef = useRef(enabled);
  const onReplyRef = useRef(onReply);
  enabledRef.current = enabled;
  onReplyRef.current = onReply;

  const cancel = ({ keepChoices = false, keepManual = false } = {}) => {
    sequenceRef.current++;
    pendingRef.current = null;
    activePromptRef.current?.onSpeechEnd?.();
    activePromptRef.current = null;
    tappablePromptRef.current = null;
    if (!keepManual) manualReplyRef.current = null;
    sessionRef.current?.cancel();
    sessionRef.current = null;
    stopSpeechOutput();
    clearInterval(clockRef.current);
    clearTimeout(deadlineRef.current);
    clearTimeout(pauseRef.current);
    clearTimeout(hideRef.current);
    setStatus(null);
    if (!keepChoices) window.dispatchEvent(new Event('paper-speech-choice-used'));
  };
  useEffect(() => {
    if (!enabled) cancel();
  }, [enabled]);
  useEffect(() => {
    if (sessionRef.current) cancel();
  }, [remoteTranscription]);
  useEffect(() => {
    const manualStart = () => {
      const prompt = tappablePromptRef.current;
      manualReplyRef.current = prompt;
      manualRef.current = true;
      cancel({ keepChoices: true, keepManual: true });
    };
    const manualEnd = () => {
      manualRef.current = false;
    };
    const background = () => {
      const prompt = activePromptRef.current || pendingRef.current;
      cancel();
      if (prompt) pendingRef.current = prompt;
    };
    const foreground = () => {
      if (document.visibilityState === 'hidden') return;
      const prompt = pendingRef.current;
      if (!prompt) return;
      pendingRef.current = null;
      if (Date.now() - prompt.at < 120000) {
        const sequence = sequenceRef.current;
        setTimeout(() => {
          if (sequence === sequenceRef.current)
            askRef.current?.(prompt.context, prompt.spoken, false, prompt.captionSuffix);
        }, 200);
      }
    };
    const choose = (event) => {
      const prompt = tappablePromptRef.current;
      const word = event.detail?.word;
      if (
        !prompt ||
        !enabledRef.current ||
        (Date.now() - prompt.at > 120000 && prompt.context !== 'hint_followup') ||
        !prompt.choices.includes(word) ||
        (event.detail?.spoken && event.detail.spoken !== prompt.spoken)
      )
        return;
      cancel();
      stopSpeechOutput();
      onReplyRef.current(prompt.context, word);
    };
    const interact = (event) => {
      const prompt = tappablePromptRef.current;
      if (
        !prompt ||
        !enabledRef.current ||
        (Date.now() - prompt.at > 120000 && prompt.context !== 'hint_followup') ||
        event.detail?.spoken !== prompt.spoken
      )
        return;
      event.detail.handled = true;
      askRef.current?.(prompt.context, prompt.spoken, true, prompt.captionSuffix);
    };
    const visibility = () => {
      if (document.visibilityState === 'hidden') background();
      else foreground();
    };
    window.addEventListener('appbackground', background);
    window.addEventListener('appforeground', foreground);
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('paper-manual-voice-start', manualStart);
    window.addEventListener('paper-manual-voice-end', manualEnd);
    window.addEventListener('paper-speech-choice', choose);
    window.addEventListener('paper-speech-interact', interact);
    return () => {
      window.removeEventListener('appbackground', background);
      window.removeEventListener('appforeground', foreground);
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('paper-manual-voice-start', manualStart);
      window.removeEventListener('paper-manual-voice-end', manualEnd);
      window.removeEventListener('paper-speech-choice', choose);
      window.removeEventListener('paper-speech-interact', interact);
      sequenceRef.current++;
      sessionRef.current?.cancel();
      clearInterval(clockRef.current);
      clearTimeout(deadlineRef.current);
      clearTimeout(pauseRef.current);
      clearTimeout(hideRef.current);
    };
  }, []);

  const ask = async (
    context,
    spoken,
    listenOnly = false,
    captionSuffix = '',
    onSpeechEnd,
    alreadySpoken = false,
  ) => {
    if (listenOnly && activePromptRef.current) lastPromptEndRef.current = Date.now();
    cancel({ keepChoices: listenOnly });
    const sequence = sequenceRef.current;
    const prompt = { context, spoken, captionSuffix, onSpeechEnd, at: Date.now() };
    const caption = spoken + captionSuffix;
    const choices = choicesForPrompt(context, caption);
    if (document.visibilityState === 'hidden') {
      onSpeechEnd?.();
      pendingRef.current = prompt;
      return;
    }
    if (!enabledRef.current || manualRef.current) {
      onSpeechEnd?.();
      return;
    }
    activePromptRef.current = prompt;
    tappablePromptRef.current = { ...prompt, choices };
    if (alreadySpoken)
      window.dispatchEvent(
        new CustomEvent('paper-speech', {
          detail: { text: caption, spoken, choices },
        }),
      );
    if (!listenOnly) {
      const mayListen = await hasAutomaticSpeechPermission();
      if (sequence !== sequenceRef.current) return;
      if (!mayListen) {
        activePromptRef.current = null;
        setStatus({ phase: 'manual', label: '需要回应时，请按住说话', choices });
        hideRef.current = setTimeout(() => setStatus(null), 5000);
        if (!alreadySpoken) await speakAndWait(spoken, choices, caption);
        onSpeechEnd?.();
        return;
      }
      // This system recognizer still transcribes the Pad's own speaker after
      // Android echo cancellation. Keep its microphone closed until TTS ends.
      setStatus({
        phase: 'speaking',
        label: choices.length
          ? '正在播报，播完自动听你回应；也可点操作词'
          : '正在播报，播完可以直接回应',
        choices,
      });
      if (!alreadySpoken) await speakAndWait(spoken, choices, caption);
      onSpeechEnd?.();
      lastPromptEndRef.current = Date.now();
      if (sequence !== sequenceRef.current || !enabledRef.current || manualRef.current) return;
      await new Promise((resolve) => setTimeout(resolve, 350));
      if (sequence === sequenceRef.current) askRef.current?.(context, spoken, true, captionSuffix);
      return;
    }
    const replySeconds = context === 'help_offer' ? 20 : 15;
    const pauseMs = ['hint_followup', 'hint_attempt_reply', 'hint_target'].includes(context)
      ? 5000
      : 4000;
    let deadline = Date.now() + 12000;
    let openedAt = 0;
    const replyPause = createReplyPause(pauseMs);
    let listeningStarted = false;
    const expire = () => {
      if (sequence !== sequenceRef.current) return;
      clearTimeout(pauseRef.current);
      sessionRef.current?.stop();
      setStatus({ phase: 'processing', label: '正在理解回应', choices });
    };
    const extendDeadline = () => {
      if (!listeningStarted) return;
      deadline = Math.min(openedAt + 60000, Date.now() + replySeconds * 1000);
      clearTimeout(deadlineRef.current);
      deadlineRef.current = setTimeout(expire, Math.max(0, deadline - Date.now()));
    };
    const schedulePause = () => {
      clearTimeout(pauseRef.current);
      const pauseDeadline = replyPause.ensureDeadline();
      if (!pauseDeadline) return;
      pauseRef.current = setTimeout(
        () => {
          if (sequence === sequenceRef.current) sessionRef.current?.stop();
        },
        Math.max(0, pauseDeadline - Date.now()),
      );
    };
    const beginListening = () => {
      if (!listeningStarted) {
        listeningStarted = true;
        openedAt = Date.now();
        deadline = openedAt + replySeconds * 1000;
        clearTimeout(deadlineRef.current);
        deadlineRef.current = setTimeout(expire, replySeconds * 1000);
      }
      setStatus({
        phase: 'listening',
        label: replyPause.transcript
          ? '已记下这段话，短暂停顿后可继续说'
          : remoteTranscription
            ? '正在录音，结束后转写；可以停顿后继续说'
            : '正在听你回应，系统可能在较长停顿后结束收音',
        heard: replyPause.transcript,
        seconds: Math.max(0, Math.ceil((deadline - Date.now()) / 1000)),
        pauseSeconds: replyPause.deadline
          ? Math.max(0, Math.ceil((replyPause.deadline - Date.now()) / 1000))
          : null,
        choices,
      });
    };
    setStatus({
      phase: 'starting',
      label: '播报已结束，正在打开麦克风',
      choices,
    });
    clockRef.current = setInterval(() => {
      setStatus((previous) =>
        previous?.phase === 'listening' && previous.seconds != null
          ? {
              ...previous,
              seconds: Math.max(0, Math.ceil((deadline - Date.now()) / 1000)),
              pauseSeconds: replyPause.deadline
                ? Math.max(0, Math.ceil((replyPause.deadline - Date.now()) / 1000))
                : null,
            }
          : previous,
      );
    }, 250);
    const session = (remoteTranscription ? startRecordedSpeechSession : startSpeechSession)({
      collectUntilPause: true,
      maxMs: replySeconds * 1000,
      onText: () => {},
      onError: () => {},
      onPartial: (text) => {
        if (sequence !== sequenceRef.current || !replyPause.noteTranscript(text)) return;
        extendDeadline();
        schedulePause();
        beginListening();
      },
      onState: (phase) => {
        if (phase === 'listening' && sequence === sequenceRef.current) beginListening();
        if (phase === 'speaking' && sequence === sequenceRef.current) {
          // A recognizer's speech-start event can be background noise. Only
          // new transcript text extends the pause after a confirmed phrase.
          if (!replyPause.transcript) extendDeadline();
          beginListening();
        }
        if (phase === 'segment' && sequence === sequenceRef.current) {
          schedulePause();
          beginListening();
        }
        if (phase === 'processing' && sequence === sequenceRef.current)
          setStatus({ phase: 'processing', label: '正在理解回应', choices });
      },
      onFinish: ({ text, partial, error, reason }) => {
        if (sequence !== sequenceRef.current) return;
        clearTimeout(pauseRef.current);
        replyPause.clear();
        activePromptRef.current = null;
        sessionRef.current = null;
        clearInterval(clockRef.current);
        clearTimeout(deadlineRef.current);
        // The native bridge returns confirmed earlier segments alongside a
        // later recognizer error. Treat those words as the completed reply.
        const replyText = completedVoiceReply({ text, partial, error });
        const interrupted = Boolean(error && !replyText);
        if (
          replyText &&
          !interrupted &&
          likelyPromptEcho(
            replyText,
            spoken,
            reason === 'tts_overlap' || Date.now() - lastPromptEndRef.current < 2500,
          )
        ) {
          setStatus({
            phase: 'manual',
            label: choices.length
              ? '刚才听到的是设备播报，请按住说话回答或点击选项'
              : '刚才听到的是设备播报，请按住说话回答',
            choices,
          });
          return;
        }
        const transientError = Boolean(error && /暂不可用|连接失败|网络|服务中断/.test(error));
        setStatus({
          phase: interrupted ? 'error' : replyText ? 'heard' : 'manual',
          choices,
          label: interrupted
            ? transientError
              ? `语音暂时断开。点“重试收音”继续回答，或按住说话。${fallbackTip}`
              : `${error}。可点“重试收音”继续回答，或使用屏幕按钮。`
            : replyText
              ? `听到：${replyText}`
              : '没有听到回应，可按住说话',
        });
        hideRef.current = setTimeout(() => setStatus(null), interrupted ? 30000 : 5000);
        if (replyText && !interrupted) {
          stopSpeechOutput();
          tappablePromptRef.current = null;
          window.dispatchEvent(new Event('paper-speech-choice-used'));
          onReplyRef.current(context, replyText);
        }
      },
    });
    if (sequence !== sequenceRef.current) return;
    sessionRef.current = session;
    if (!listeningStarted) deadlineRef.current = setTimeout(expire, 12000);
  };
  askRef.current = ask;
  const retry = () => {
    const prompt = tappablePromptRef.current;
    if (prompt && enabledRef.current)
      askRef.current?.(prompt.context, prompt.spoken, true, prompt.captionSuffix);
  };
  const consumeManualReply = (text) => {
    const prompt = manualReplyRef.current;
    if (!prompt) return false;
    manualReplyRef.current = null;
    const reply = text.trim();
    if (
      Date.now() - prompt.at > 120000 ||
      /^(暂停|结束作业|确认结束作业|批改|开始批改)$/.test(reply)
    )
      return false;
    if (!reply) return false;
    onReplyRef.current(prompt.context, text);
    return true;
  };
  return { ask, cancel, retry, consumeManualReply, status };
}

function VoiceReplyStatus({ status, onCancel, onRetry, captionVisible = false }) {
  if (!status) return null;
  return (
    <div className={`voice-reply-status ${status.phase}`} role="status">
      <span className={`voice-status-dot ${status.phase === 'listening' ? 'on' : ''}`} />
      <div className="voice-reply-copy">
        <strong>{status.label}</strong>
        {status.heard && <small className="voice-reply-heard">听到：{status.heard}</small>}
        {status.choices?.length > 0 && (
          <span className="voice-reply-words">
            {captionVisible ? (
              '可说，也可点播报文字中的高亮词'
            ) : (
              <>
                {status.phase === 'speaking' ? '现在可点，播报后可说' : '可说或点'}{' '}
                {status.choices.map((word) => (
                  <button
                    key={word}
                    type="button"
                    onClick={() => selectSpeechAction(word, status.spoken)}
                  >
                    {word}
                  </button>
                ))}
              </>
            )}
          </span>
        )}
        {(status.phase === 'error' || status.phase === 'manual') && (
          <button type="button" className="voice-reply-retry" onClick={onRetry}>
            <Mic size={14} /> 重试收音
          </button>
        )}
      </div>
      {status.phase === 'listening' && status.seconds != null && (
        <span className="voice-reply-count" aria-hidden="true">
          {status.pauseSeconds != null ? `${status.pauseSeconds} 秒后提交` : `${status.seconds} 秒`}
        </span>
      )}
      <button type="button" onClick={onCancel} aria-label="取消本次语音等待">
        <X size={15} />
      </button>
    </div>
  );
}

function ParentAuth({ onLogin, onError }) {
  const [register, setRegister] = useState(false);
  const [form, setForm] = useState({
    email: '',
    password: '',
    child_name: '',
    age_band: 'primary',
    grade: '',
    textbook: '',
  });
  const set = (name, value) => setForm((f) => ({ ...f, [name]: value }));
  const submit = async (e) => {
    e.preventDefault();
    try {
      const data = await post(register ? '/v1/auth/register' : '/v1/auth/login', form);
      onLogin(data.token);
    } catch (error) {
      onError(error);
    }
  };
  return (
    <main className="auth-layout">
      <section className="auth-story">
        <div className="eyebrow">FOR EVERY SMALL STEP</div>
        <h1>
          作业有人陪，
          <br />
          <em>思考留给孩子。</em>
        </h1>
        <p>看见进度、按需远程陪伴，再一起确认今天的完成情况。</p>
        <div className="story-lines">
          <div>
            <span>01</span> 粘贴群作业
          </div>
          <div>
            <span>02</span> 看见纸面进度
          </div>
          <div>
            <span>03</span> 语音与圈画指导
          </div>
        </div>
      </section>
      <section className="auth-card">
        <div className="section-kicker">家长入口</div>
        <h2>{register ? '创建家庭空间' : '欢迎回来'}</h2>
        <p className="muted">
          {register ? '先创建家庭，再配对孩子的平板。' : '登录后查看今天的任务和陪伴记录。'}
        </p>
        <form onSubmit={submit}>
          <label>
            邮箱
            <input
              type="email"
              required
              value={form.email}
              onChange={(e) => set('email', e.target.value)}
            />
          </label>
          <label>
            密码
            <input
              type="password"
              required
              minLength={12}
              value={form.password}
              onChange={(e) => set('password', e.target.value)}
              placeholder="至少 12 位"
            />
          </label>
          {register && (
            <>
              <label>
                孩子称呼
                <input
                  required
                  value={form.child_name}
                  onChange={(e) => set('child_name', e.target.value)}
                />
              </label>
              <label>
                年龄段
                <select value={form.age_band} onChange={(e) => set('age_band', e.target.value)}>
                  <option value="kindergarten">幼儿园</option>
                  <option value="primary">小学</option>
                  <option value="junior">初中</option>
                </select>
              </label>
              <div className="two-fields">
                <label>
                  年级
                  <input
                    value={form.grade}
                    onChange={(e) => set('grade', e.target.value)}
                    placeholder="如 三年级"
                  />
                </label>
                <label>
                  教材
                  <input
                    value={form.textbook}
                    onChange={(e) => set('textbook', e.target.value)}
                    placeholder="可选"
                  />
                </label>
              </div>
            </>
          )}
          <button className="button primary wide" type="submit">
            {register ? '创建并进入' : '登录'} <ArrowRight size={17} />
          </button>
        </form>
        <button className="text-button" onClick={() => setRegister(!register)}>
          {register ? '已有账号？去登录' : '第一次使用？创建家庭空间'}
        </button>
      </section>
    </main>
  );
}

function ChildPair({ onLogin, onError }) {
  const [claim, setClaim] = useState(null);
  const [busy, setBusy] = useState(false);
  const create = async () => {
    setBusy(true);
    try {
      const data = await post('/v1/devices/claim', {
        device_id: saved('device_id') || null,
        device_secret: saved('device_secret') || null,
      });
      save('device_id', data.device_id);
      save('device_secret', data.device_secret);
      if (data.token) onLogin(data.token);
      else setClaim(data);
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    create();
  }, []);
  useEffect(() => {
    if (!claim) return;
    const id = setInterval(async () => {
      try {
        const data = await post('/v1/devices/status', {
          device_id: claim.device_id,
          device_secret: claim.device_secret,
        });
        if (data.token) onLogin(data.token);
      } catch {
        /* retry */
      }
    }, 3000);
    return () => clearInterval(id);
  }, [claim]);
  return (
    <main className="pair-page">
      <div className="pair-art">
        <BookOpen size={74} strokeWidth={1.1} />
        <span className="orbit one" />
        <span className="orbit two" />
      </div>
      <div className="section-kicker">第一次使用</div>
      <h1>先让家长和这台平板认识</h1>
      <p>请家长在手机端打开「配对平板」，输入下面的 8 位数字。</p>
      <div className="pair-code">{claim?.pairing_code || '········'}</div>
      <div className="pair-note">
        <ShieldCheck size={17} /> 配对码 10 分钟有效；配对后自动进入。
      </div>
      <button className="button secondary" disabled={busy} onClick={create}>
        <RefreshCw size={16} /> 重新生成配对码
      </button>
    </main>
  );
}

function ParentHome({ dashboard, refresh, onError, onNotice, spokenText }) {
  const voiceQuota = premiumVoiceQuota(dashboard.family);
  const cloudVoiceTitle = voiceQuota.provider ? '云端语音' : '真人感语音试用';
  const remoteTranscription = Boolean(
    dashboard.asr_available && dashboard.family.audio_transcription_allowed,
  );
  const [tab, setTab] = useState('today');
  const [paste, setPaste] = useState('');
  const [subject, setSubject] = useState('');
  const [dueDate, setDueDate] = useState('');
  const [draftTasks, setDraftTasks] = useState([]);
  const [publishRequestId, setPublishRequestId] = useState('');
  const [pairCode, setPairCode] = useState('');
  const [report, setReport] = useState(null);
  const reportRequestRef = useRef(null);
  const reviewCorrections = report?.daily_records?.corrections?.items || [];
  const reviewObservations = report?.daily_records?.observations?.items || [];
  const reviewReflections = report?.daily_records?.reflections?.items || [];
  const [parentGoalMinutes, setParentGoalMinutes] = useState(
    dashboard.timer?.goal_seconds ? String(Math.round(dashboard.timer.goal_seconds / 60)) : '',
  );
  const [liveId, setLiveId] = useState(dashboard.live?.id || '');
  const [helpSettingBusy, setHelpSettingBusy] = useState(false);
  const [audioSettingBusy, setAudioSettingBusy] = useState(false);
  const [voiceSettingBusy, setVoiceSettingBusy] = useState(false);
  const pendingVoiceConfirm = useRef({ id: '', until: 0 });
  const pendingCorrectionConfirm = useRef(0);
  const seenCorrectionRequest = useRef('');
  const seenTaskSubmissions = useRef(new Set());
  const autoReply = useTimedVoiceReply(
    (context, text) => handleParentShortReply(context, text),
    !liveId,
    remoteTranscription,
    dashboard.asr_available && !remoteTranscription ? '也可在本页开启短语音转写。' : '',
  );
  useEffect(() => setLiveId(dashboard.live?.id || ''), [dashboard.live?.id]);
  useEffect(() => {
    setParentGoalMinutes(
      dashboard.timer?.goal_seconds ? String(Math.round(dashboard.timer.goal_seconds / 60)) : '',
    );
  }, [dashboard.timer?.goal_seconds]);
  useEffect(() => {
    const request = dashboard.session?.correction_requested_at;
    if (
      request &&
      request !== seenCorrectionRequest.current &&
      dashboard.session?.status === 'active' &&
      !correctionTestBypassEnabled(dashboard) &&
      !correctionAllowedNow(dashboard)
    ) {
      seenCorrectionRequest.current = request;
      autoReply.ask(
        'parent_correction_request',
        '孩子申请批改作业。请先确认孩子已经独立思考并写下答案。若准备授权，请说允许批改。',
      );
    }
    if (!request) seenCorrectionRequest.current = '';
  }, [
    dashboard.session?.correction_requested_at,
    dashboard.session?.status,
    dashboard.session?.correction_access?.mode,
  ]);
  useEffect(() => {
    const pending = dashboard.tasks.filter(
      (task) => task.status === 'awaiting_parent' && task.latest_submission_id,
    );
    const fresh = pending.filter(
      (task) => !seenTaskSubmissions.current.has(task.latest_submission_id),
    );
    if (fresh.length && !liveId) {
      fresh.forEach((task) => seenTaskSubmissions.current.add(task.latest_submission_id));
      autoReply.ask(
        'parent_task_review',
        `孩子提交了${fresh.length}项作业，等待你确认。请查看任务清单中的截图和建议。`,
      );
    }
  }, [dashboard.tasks, liveId]);
  useEffect(() => {
    if (tab !== 'review') return;
    const controller = new AbortController();
    reportRequestRef.current = controller;
    api(`/v1/reports/daily?offset_minutes=${-new Date().getTimezoneOffset()}`, {
      signal: controller.signal,
    })
      .then((result) => {
        if (!controller.signal.aborted) setReport(result);
      })
      .catch((error) => {
        if (!controller.signal.aborted) onError(error);
      });
    return () => controller.abort();
  }, [tab, dashboard]);
  const importTasks = async () => {
    try {
      const dueAt = Math.floor(new Date(dueDate).getTime() / 1000);
      if (!Number.isFinite(dueAt) || !dueAt) throw new Error('请先设置整批作业的截止日期和时间');
      await post('/v1/tasks/batch', {
        due_at: dueAt,
        timezone_offset_minutes: -new Date().getTimezoneOffset(),
        request_id: publishRequestId,
        items: draftTasks.map((task) => ({
          title: task.title.trim(),
          subject: task.subject.trim() || null,
          due_at: task.dueAt ? Math.floor(new Date(task.dueAt).getTime() / 1000) : null,
        })),
      });
      setPaste('');
      setDueDate('');
      setDraftTasks([]);
      setPublishRequestId('');
      onNotice('作业已发布到孩子端');
      refresh();
      setTab('today');
    } catch (e) {
      onError(e);
    }
  };
  const prepareDraftTasks = () => {
    const lines = paste
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    setDraftTasks(lines.map((title) => ({ title, subject, dueAt: '' })));
    setPublishRequestId(crypto.randomUUID());
  };
  const confirm = async (task) => {
    try {
      if (task.latest_submission_id) {
        await post(`/v1/tasks/${task.id}/reviews`, {
          submission_id: task.latest_submission_id,
          version: task.version,
          decision: 'accepted',
          reason: '',
        });
      } else {
        await patch(`/v1/tasks/${task.id}`, { status: 'confirmed', version: task.version });
      }
      await refresh();
      onNotice('已确认完成');
    } catch (e) {
      onError(e);
    }
  };
  const confirmAll = async () => {
    const pending = dashboard.tasks.filter(
      (task) => task.status === 'awaiting_parent' && task.latest_submission_id,
    );
    if (!pending.length) return;
    try {
      await post('/v1/tasks/reviews/batch', {
        items: pending.map((task) => ({
          task_id: task.id,
          submission_id: task.latest_submission_id,
          version: task.version,
        })),
      });
      await refresh();
      onNotice(`已确认 ${pending.length} 项任务`);
    } catch (error) {
      onError(error);
    }
  };
  const reviewCorrection = async (item, reviewStatus) => {
    try {
      await patch(`/v1/corrections/${item.id}`, { review_status: reviewStatus });
      await refresh();
      onNotice(reviewStatus === 'accepted' ? '已确认这次批改' : '已标记批改有误');
    } catch (e) {
      onError(e);
    }
  };
  const setCorrectionAccess = async (allowed) => {
    if (!dashboard.session?.id) return;
    if (correctionTestBypassEnabled(dashboard)) {
      onNotice('测试模式由服务端配置：当前无需家长人工授权批改。');
      return;
    }
    try {
      await patch(`/v1/sessions/${dashboard.session.id}/correction-access`, { allowed });
      await refresh();
      onNotice(allowed ? '已允许孩子在 10 分钟内批改作业' : '已关闭批改权限');
    } catch (e) {
      onError(e);
    }
  };
  const setParentTimerGoal = async (minutes) => {
    if (!dashboard.session?.id) return;
    if (minutes !== null && (!Number.isInteger(minutes) || minutes < 5 || minutes > 180)) {
      onNotice('参考目标需为 5 到 180 分钟');
      return;
    }
    try {
      await patch(`/v1/sessions/${dashboard.session.id}/timer`, {
        goal_seconds: minutes === null ? null : minutes * 60,
      });
      await refresh();
      onNotice(minutes === null ? '已取消参考目标' : `参考目标已调整为 ${minutes} 分钟`);
    } catch (e) {
      onError(e);
    }
  };
  const setParentHelpMode = async (mode) => {
    if (helpSettingBusy) return;
    setHelpSettingBusy(true);
    try {
      await patch('/v1/families/help-preferences', { mode });
      await refresh();
      onNotice(
        mode === 'off'
          ? '已关闭自动卡题提醒；孩子仍可主动求助'
          : `自动卡题提醒已设为${mode === 'gentle' ? '少提醒' : '标准'}`,
      );
    } catch (e) {
      onError(e);
    } finally {
      setHelpSettingBusy(false);
    }
  };
  const setAudioTranscription = async (allowed) => {
    if (audioSettingBusy) return;
    setAudioSettingBusy(true);
    try {
      await patch('/v1/families/audio-transcription', { allowed });
      await refresh();
      onNotice(allowed ? '已允许短语音转写；可随时关闭' : '已关闭短语音转写');
    } catch (error) {
      onError(error);
    } finally {
      setAudioSettingBusy(false);
    }
  };
  const setPremiumVoice = async (enabled) => {
    if (voiceSettingBusy) return;
    setVoiceSettingBusy(true);
    try {
      await patch('/v1/families/voice-preferences', { enabled });
      await refresh();
      onNotice(enabled ? `已开启${cloudVoiceTitle}` : '已切换回设备系统声音');
    } catch (error) {
      onError(error);
    } finally {
      setVoiceSettingBusy(false);
    }
  };
  const pair = async () => {
    try {
      await post('/v1/families/pair', { code: pairCode });
      setPairCode('');
      await refresh();
      setTab('today');
      onNotice('平板配对成功');
    } catch (e) {
      onError(e);
    }
  };
  const startLive = async () => {
    try {
      const data = await api('/v1/live-sessions', { method: 'POST' });
      setLiveId(data.id);
    } catch (e) {
      onError(e);
    }
  };
  const stopLive = async () => {
    try {
      await api(`/v1/live-sessions/${liveId}/finish`, { method: 'POST' });
      setLiveId('');
      refresh();
    } catch (e) {
      onError(e);
    }
  };
  const remoteEnd = () => {
    setLiveId('');
    refresh();
  };
  const handleVoice = (text) => {
    if (handleCaptionDisplayCommand(text)) return;
    if (/^(关闭|停止)(自动|主动|卡题)提醒$/.test(text.trim())) {
      setParentHelpMode('off');
      return;
    }
    if (/^(开启|打开)(自动|主动|卡题)提醒$/.test(text.trim())) {
      setParentHelpMode('standard');
      return;
    }
    if (/^(设置|改为)?少提醒$/.test(text.trim())) {
      setParentHelpMode('gentle');
      return;
    }
    const timerCommand = parseTimerCommand(text);
    if (timerCommand && dashboard.session) {
      if (timerCommand.type === 'status')
        speak(
          `孩子已学习约${Math.floor((dashboard.timer?.learning_seconds || 0) / 60)}分钟，批改等待约${Math.floor((dashboard.timer?.grading_wait_seconds || 0) / 60)}分钟。`,
        );
      else if (timerCommand.type === 'goal') setParentTimerGoal(timerCommand.minutes);
      else onNotice('科目切换由孩子端确认');
      return;
    }
    if (autoReply.consumeManualReply(text)) return;
    if (/结束直播|关闭直播|结束查看/.test(text) && liveId) stopLive();
    else if (/打开直播|看纸面|查看纸面/.test(text)) startLive();
    else if (/关闭批改|撤销批改/.test(text)) setCorrectionAccess(false);
    else if (/^确认允许批改$/.test(text) && pendingCorrectionConfirm.current > Date.now()) {
      pendingCorrectionConfirm.current = 0;
      setCorrectionAccess(true);
    } else if (/允许批改|同意批改/.test(text))
      handleParentShortReply('parent_correction_request', text);
    else if (/读任务|朗读作业|播报任务/.test(text))
      speak(
        dashboard.tasks.length ? dashboard.tasks.map((t) => t.title).join('。') : '今天还没有任务',
      );
    else if (/读思路|孩子的想法/.test(text))
      speak(
        (dashboard.reflections || []).length
          ? dashboard.reflections.map((r) => r.text).join('。')
          : '孩子还没有记录想法',
      );
    else if (/读回顾|播报回顾/.test(text)) {
      setTab('review');
      speak(
        `今天做了约${Math.round((report?.duration_seconds || 0) / 60)}分钟作业，求助${report?.hint_count || 0}次。`,
      );
    } else if (/确认完成/.test(text)) {
      const pending = dashboard.tasks.filter((t) => t.status === 'awaiting_parent');
      if (pending.length === 1) {
        if (
          pendingVoiceConfirm.current.id === pending[0].id &&
          pendingVoiceConfirm.current.until > Date.now()
        ) {
          pendingVoiceConfirm.current = { id: '', until: 0 };
          confirm(pending[0]);
        } else {
          pendingVoiceConfirm.current = { id: pending[0].id, until: Date.now() + 15000 };
          speak(`请核对：${pending[0].title}。如确实完成，请再说一次“确认完成”。`);
        }
      } else speak(`有${pending.length}项等待确认，请在任务清单中选择。`);
    } else if (/发布作业|录入作业/.test(text)) {
      setTab('import');
      speak('请说出一项作业，我会写入输入框供你核对。');
    } else if (tab === 'import') {
      setPaste((previous) => `${previous}${previous ? '\n' : ''}${text}`);
      speak('已记录，请核对文字后发布。');
    } else onNotice(`听到：${text}。请尝试说读任务、发布作业或打开直播。`);
  };
  const handleParentShortReply = (context, text) => {
    if (handleCaptionDisplayCommand(text)) return;
    if (context === 'parent_task_review') {
      if (/查看|打开|待审|任务/.test(text)) setTab('today');
      return;
    }
    const intent = parseVoiceReply(context, text);
    if (intent?.type === 'decline') return;
    if (
      correctionTestBypassEnabled(dashboard) &&
      ['review_correction_access', 'grant_correction_access'].includes(intent?.type)
    ) {
      pendingCorrectionConfirm.current = 0;
      speak('测试模式已开启，孩子开始作业后可直接批改，无需家长人工授权。');
      return;
    }
    if (intent?.type === 'review_correction_access') {
      pendingCorrectionConfirm.current = Date.now() + 30000;
      autoReply.ask(
        'parent_correction_confirm',
        '请再次确认：开放当前作业十分钟批改权限。若同意，请说确认允许批改。',
      );
    } else if (
      intent?.type === 'grant_correction_access' &&
      pendingCorrectionConfirm.current > Date.now()
    ) {
      pendingCorrectionConfirm.current = 0;
      setCorrectionAccess(true);
    } else speak(routeUnmatchedVoiceReply(context, text)?.message);
  };
  return (
    <div className="parent-shell">
      <VoiceReplyStatus
        status={autoReply.status}
        onCancel={autoReply.cancel}
        onRetry={autoReply.retry}
        captionVisible={Boolean(spokenText)}
      />
      <aside className="side-nav">
        <div className="sidebar-title">家庭空间</div>
        <div className="child-badge">
          <span>{dashboard.family.child_name.slice(0, 1)}</span>
          <div>
            <strong>{dashboard.family.child_name}的作业</strong>
            <small>
              {dashboard.family.grade || '学习记录'} ·{' '}
              {dashboard.family.age_band === 'junior'
                ? '初中'
                : dashboard.family.age_band === 'primary'
                  ? '小学'
                  : '幼儿园'}
            </small>
          </div>
        </div>
        <nav>
          <button className={tab === 'today' ? 'active' : ''} onClick={() => setTab('today')}>
            <BookOpen /> 今日作业
          </button>
          <button className={tab === 'import' ? 'active' : ''} onClick={() => setTab('import')}>
            <Plus /> 发布作业
          </button>
          <button className={tab === 'review' ? 'active' : ''} onClick={() => setTab('review')}>
            <Clock3 /> 每日回顾
          </button>
          <button className={tab === 'pair' ? 'active' : ''} onClick={() => setTab('pair')}>
            <Link2 /> 配对平板
          </button>
        </nav>
        <div className="sidebar-bottom">
          <ShieldCheck size={19} /> 纸面画面仅按需直播
          <br />
          图像不长期保存
        </div>
      </aside>
      <main className="parent-main">
        {tab === 'today' && (
          <>
            <div className="page-heading">
              <div>
                <div className="section-kicker">TODAY'S HOMEWORK</div>
                <h1>今天，一起往前走。</h1>
                <p>任务由家长确认完成；纸面观察仅供参考。</p>
              </div>
              <button className="button primary" onClick={() => setTab('import')}>
                <Plus size={17} /> 发布作业
              </button>
            </div>
            {dashboard.session?.status === 'active' &&
              dashboard.session.correction_requested_at &&
              !correctionTestBypassEnabled(dashboard) &&
              !correctionAllowedNow(dashboard) && (
                <div className="correction-request-banner">
                  <span>孩子申请批改作业。请先确认已独立思考并写下答案。</span>
                  <button className="button primary" onClick={() => setCorrectionAccess(true)}>
                    允许 10 分钟
                  </button>
                </div>
              )}
            <div className="overview-grid">
              <div className="overview-card sage">
                <div className="card-icon">
                  <BookOpen />
                </div>
                <small>今日任务</small>
                <strong>{dashboard.tasks.length}</strong>
                <p>{dashboard.tasks.filter((t) => t.status === 'confirmed').length} 项已确认</p>
              </div>
              <div className="overview-card peach">
                <div className="card-icon">
                  <Sparkles />
                </div>
                <small>等待确认</small>
                <strong>
                  {dashboard.tasks.filter((t) => t.status === 'awaiting_parent').length}
                </strong>
                <p>孩子提交最终作业后会出现在这里</p>
              </div>
              <div className="overview-card cream">
                <div className="card-icon">
                  <Radio />
                </div>
                <small>作业状态</small>
                <strong className="state-word">
                  {dashboard.session?.status === 'active'
                    ? '进行中'
                    : dashboard.session?.status === 'paused'
                      ? '已暂停'
                      : '未开始'}
                </strong>
                <p>
                  {dashboard.session?.status === 'active' ? '可以按需查看纸面' : '等待孩子主动开始'}
                </p>
              </div>
            </div>
            {dashboard.session && (
              <section className="panel parent-timer-panel">
                <div className="parent-timer-main">
                  <Clock3 size={20} />
                  <div>
                    <small>本次学习</small>
                    <strong>{formatStudyTime(dashboard.timer?.learning_seconds || 0)}</strong>
                  </div>
                  <span>
                    独立 {formatStudyTime(dashboard.timer?.focus_seconds || 0)} · 辅导{' '}
                    {formatStudyTime(dashboard.timer?.assisted_seconds || 0)} · 批改等待{' '}
                    {formatStudyTime(dashboard.timer?.grading_wait_seconds || 0)} · 暂停{' '}
                    {formatStudyTime(dashboard.timer?.paused_seconds || 0)}
                  </span>
                </div>
                <div className="parent-timer-goal">
                  <label htmlFor="parent-timer-goal">参考目标（分钟）</label>
                  <input
                    id="parent-timer-goal"
                    type="number"
                    min="5"
                    max="180"
                    value={parentGoalMinutes}
                    onChange={(event) => setParentGoalMinutes(event.target.value)}
                    placeholder="不设目标"
                  />
                  <button
                    className="button mini"
                    onClick={() => setParentTimerGoal(Number(parentGoalMinutes))}
                  >
                    保存
                  </button>
                  <button className="button mini" onClick={() => setParentTimerGoal(null)}>
                    取消目标
                  </button>
                </div>
              </section>
            )}
            <section className="panel help-preference-panel">
              <div>
                <strong>自动卡题提醒</strong>
                <p>只控制系统主动询问；孩子随时可以主动求助。</p>
              </div>
              <div className="help-mode-options" role="group" aria-label="自动卡题提醒强度">
                {[
                  ['off', '关闭'],
                  ['gentle', '少提醒'],
                  ['standard', '标准'],
                ].map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    className={dashboard.family.proactive_help_mode === value ? 'selected' : ''}
                    aria-pressed={dashboard.family.proactive_help_mode === value}
                    disabled={helpSettingBusy}
                    onClick={() => setParentHelpMode(value)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </section>
            <section className="panel help-preference-panel" aria-label="短语音转写授权">
              <div>
                <strong>短语音转写</strong>
                <p>
                  仅在按住说话或回答互动时录音，发送到
                  {dashboard.asr_available ? ` ${dashboard.asr_provider} ` : '配置的转写服务'}
                  转成文字；原始音频不写入作业记录。
                  {dashboard.asr_available
                    ? ''
                    : ' 服务端尚未配置转写服务，当前仍使用系统语音识别。'}
                </p>
              </div>
              <div className="help-mode-options" role="group" aria-label="短语音转写授权">
                <button
                  type="button"
                  className={!dashboard.family.audio_transcription_allowed ? 'selected' : ''}
                  aria-pressed={!dashboard.family.audio_transcription_allowed}
                  disabled={audioSettingBusy}
                  onClick={() => setAudioTranscription(false)}
                >
                  关闭
                </button>
                <button
                  type="button"
                  className={dashboard.family.audio_transcription_allowed ? 'selected' : ''}
                  aria-pressed={Boolean(dashboard.family.audio_transcription_allowed)}
                  disabled={audioSettingBusy || !dashboard.asr_available}
                  onClick={() => setAudioTranscription(true)}
                >
                  允许
                </button>
              </div>
            </section>
            <section className="panel help-preference-panel" aria-label={cloudVoiceTitle}>
              <div>
                <strong>{cloudVoiceTitle}</strong>
                <p>
                  开启后，AI 老师的播报文字会发往{dashboard.tts_provider || '配置的云端语音服务'}
                  合成老师声音；不发送纸面照片或孩子录音。
                  {voiceQuota.provider
                    ? '使用已配置的云服务账户资源，额度与费用以服务商账户为准；云端不可用时自动使用设备系统声音。'
                    : '应用试用额度用完后自动使用设备系统声音；云服务费用由账户设置决定。'}
                  {!dashboard.tts_available && ' 当前服务端尚未配置语音合成服务。'}
                </p>
                <small>
                  {voiceQuota.provider ? (
                    <>
                      累计应用点数 {voiceQuota.used.toLocaleString('zh-CN')} · 汉字按 2
                      点计算（非云服务计费点数或剩余额度）
                    </>
                  ) : (
                    <>
                      剩余 {Math.max(0, voiceQuota.limit - voiceQuota.used).toLocaleString('zh-CN')}{' '}
                      试用额度 · 汉字按 2 点计算（与云服务实际计费不同）
                    </>
                  )}
                </small>
              </div>
              <div className="help-mode-options" role="group" aria-label={`${cloudVoiceTitle}开关`}>
                <button
                  type="button"
                  className={!dashboard.family.premium_voice_enabled ? 'selected' : ''}
                  aria-pressed={!dashboard.family.premium_voice_enabled}
                  disabled={voiceSettingBusy}
                  onClick={() => setPremiumVoice(false)}
                >
                  系统声音
                </button>
                <button
                  type="button"
                  className={dashboard.family.premium_voice_enabled ? 'selected' : ''}
                  aria-pressed={Boolean(dashboard.family.premium_voice_enabled)}
                  disabled={voiceSettingBusy || !dashboard.tts_available || voiceQuota.exhausted}
                  onClick={() => setPremiumVoice(true)}
                >
                  {voiceQuota.provider ? '云端语音' : '真人感试用'}
                </button>
              </div>
            </section>
            <section className="panel model-usage-panel" aria-label="大模型 Token 用量">
              <div className="model-usage-heading">
                <strong>大模型 Token 用量</strong>
                <small>按本机日期统计 · 每次请求成功后更新</small>
              </div>
              <div className="model-usage-values">
                {[
                  ['今日', dashboard.ai_usage?.today],
                  ['本月', dashboard.ai_usage?.month],
                  ['累计', dashboard.ai_usage?.all_time],
                ].map(([label, usage]) => (
                  <div key={label}>
                    <small>{label}</small>
                    <strong>{(usage?.total_tokens || 0).toLocaleString('zh-CN')}</strong>
                    <span>Token · {usage?.calls || 0} 次调用</span>
                  </div>
                ))}
              </div>
              {(dashboard.ai_usage?.all_time?.unreported_calls || 0) > 0 && (
                <p>
                  有 {dashboard.ai_usage.all_time.unreported_calls} 次调用未返回 Token
                  用量，未计入数字。
                </p>
              )}
            </section>
            <div className="content-grid">
              <section className="panel">
                <div className="panel-title">
                  <h2>任务清单</h2>
                  <span>{dashboard.tasks.length} 项</span>
                </div>
                {(dashboard.expiring_tasks?.length > 0 ||
                  dashboard.archived_unconfirmed_count > 0) && (
                  <p className="task-retention-note">
                    {dashboard.expiring_tasks?.length > 0 &&
                      `${dashboard.expiring_tasks.length} 项未确认任务将在 7 天内到期；请尽快核对。`}
                    {dashboard.archived_unconfirmed_count > 0 &&
                      ` 已有 ${dashboard.archived_unconfirmed_count} 项任务超过 60 天，内容已删除并记为未确认。`}
                  </p>
                )}
                {dashboard.tasks.filter(
                  (task) => task.status === 'awaiting_parent' && task.latest_submission_id,
                ).length > 1 && (
                  <button className="button secondary mini" onClick={confirmAll}>
                    批量确认待审任务
                  </button>
                )}
                {dashboard.tasks.length ? (
                  dashboard.tasks.map((task) => (
                    <div className="task-row-wrap" key={task.id}>
                      <div className="task-row">
                        <span className={`task-circle ${task.status}`}>
                          <Check size={15} />
                        </span>
                        <div>
                          <strong>{task.title}</strong>
                          <small>
                            {task.subject || '家庭作业'}
                            {task.due_at
                              ? ` · 截止 ${new Date(task.due_at * 1000).toLocaleString('zh-CN')}`
                              : task.due_date
                                ? ` · 截止 ${task.due_date}`
                                : ''}{' '}
                            ·{' '}
                            {task.status === 'confirmed'
                              ? '家长已确认'
                              : task.status === 'canceled'
                                ? `已取消${task.cancel_reason ? `：${task.cancel_reason}` : ''}`
                                : task.status === 'awaiting_parent'
                                  ? '等待你确认'
                                  : task.status === 'needs_revision'
                                    ? '已退回，等待孩子补做'
                                    : task.status === 'doing'
                                      ? '孩子正在做'
                                      : '待完成'}
                          </small>
                          {task.rejection_reason && (
                            <small>退回原因：{task.rejection_reason}</small>
                          )}
                          {task.first_submitted_at && (
                            <small>
                              首次提交：
                              {new Date(task.first_submitted_at * 1000).toLocaleString('zh-CN')}
                              {task.due_at
                                ? task.first_submitted_at <= task.due_at
                                  ? ' · 按时提交'
                                  : ' · 逾期提交'
                                : ''}
                            </small>
                          )}
                          {task.ai_summary && <small>AI 建议：{task.ai_summary}</small>}
                        </div>
                        {task.status === 'awaiting_parent' && (
                          <button className="button mini" onClick={() => confirm(task)}>
                            确认
                          </button>
                        )}
                      </div>
                      {task.latest_submission_id && (
                        <TaskReviewCard task={task} onUpdated={refresh} onError={onError} />
                      )}
                      {!['confirmed', 'canceled'].includes(task.status) && (
                        <TaskEditor task={task} onUpdated={refresh} onError={onError} />
                      )}
                    </div>
                  ))
                ) : (
                  <Empty
                    icon={<BookOpen />}
                    title="还没有作业"
                    text="粘贴群作业文字，逐行生成任务。"
                  />
                )}
              </section>
              <section className="panel companion-panel">
                <div className="panel-title">
                  <h2>远程陪伴</h2>
                  <span className={dashboard.session?.status === 'active' ? 'live-dot' : ''}>
                    {dashboard.session?.status === 'active' ? '可连接' : '待孩子开始'}
                  </span>
                </div>
                <p>
                  作业进行时可直接查看实时纸面，画圈标注并与孩子语音通话。孩子端会持续显示查看状态。
                </p>
                <button
                  className="button dark wide"
                  disabled={dashboard.session?.status !== 'active'}
                  onClick={startLive}
                >
                  <Eye size={18} /> 打开实时纸面
                </button>
                {dashboard.session && (
                  <div className="correction-access-card">
                    <h3>孩子端批改授权</h3>
                    <p>
                      {correctionTestBypassEnabled(dashboard)
                        ? '测试模式：孩子开始作业后可直接批改，无需家长人工授权。关闭服务端测试配置后恢复授权流程。'
                        : correctionAllowedNow(dashboard)
                          ? '已开放 10 分钟批改窗口，孩子可以检查已写答案。'
                          : dashboard.session.correction_requested_at
                            ? '孩子申请批改作业，请先确认已独立思考并写下答案。'
                            : '孩子点击批改入口后可申请；你也可以主动开启。'}
                    </p>
                    {!correctionTestBypassEnabled(dashboard) && (
                      <button
                        className="button primary wide"
                        disabled={dashboard.session.status !== 'active'}
                        onClick={() =>
                          setCorrectionAccess(correctionAllowedNow(dashboard) ? false : true)
                        }
                      >
                        {correctionAllowedNow(dashboard) ? '关闭批改权限' : '允许批改 10 分钟'}
                      </button>
                    )}
                  </div>
                )}
                <div className="mini-divider" />
                <h3>最近观察</h3>
                {dashboard.observations.slice(0, 3).map((o) => (
                  <div className="observation-line" key={o.id}>
                    <span className="dot" />
                    <div>
                      {o.label}
                      <small>
                        {o.review_status === 'unreviewed'
                          ? '待复核'
                          : o.review_status === 'accepted'
                            ? '已确认'
                            : '已纠正'}
                      </small>
                    </div>
                  </div>
                ))}
                {!dashboard.observations.length && (
                  <p className="muted">开始作业后，这里会出现有依据的纸面观察。</p>
                )}
              </section>
            </div>
          </>
        )}
        {tab === 'import' && (
          <div className="narrow-page">
            <div className="section-kicker">PASTE & PUBLISH</div>
            <h1>把群作业，变成清晰的一天。</h1>
            <p className="lead">每行一项。发布前请核对文字与科目，孩子端会同步显示。</p>
            <div className="panel form-panel">
              <label>
                科目（可选）
                <input
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  placeholder="例如：语文"
                />
              </label>
              <label>
                整批默认截止时间
                <input
                  type="datetime-local"
                  value={dueDate}
                  onChange={(e) => setDueDate(e.target.value)}
                />
              </label>
              <label>
                粘贴群消息
                <textarea
                  rows={10}
                  value={paste}
                  onChange={(e) => {
                    setPaste(e.target.value);
                    setDraftTasks([]);
                  }}
                  placeholder={
                    '语文：朗读课文第 3 页\n数学：练习册第 5 页 1—4 题\n英语：听读 10 分钟'
                  }
                />
              </label>
              {draftTasks.length > 0 && (
                <div className="import-preview">
                  <strong>发布前逐项核对 · {draftTasks.length} 项</strong>
                  {draftTasks.map((task, index) => (
                    <div className="publish-draft" key={index}>
                      <label>
                        第 {index + 1} 项
                        <input
                          value={task.title}
                          onChange={(e) =>
                            setDraftTasks((items) =>
                              items.map((item, i) =>
                                i === index ? { ...item, title: e.target.value } : item,
                              ),
                            )
                          }
                        />
                      </label>
                      <label>
                        科目
                        <input
                          value={task.subject}
                          placeholder={subject || '可选'}
                          onChange={(e) =>
                            setDraftTasks((items) =>
                              items.map((item, i) =>
                                i === index ? { ...item, subject: e.target.value } : item,
                              ),
                            )
                          }
                        />
                      </label>
                      <label>
                        单项截止时间（可选）
                        <input
                          type="datetime-local"
                          value={task.dueAt}
                          onChange={(e) =>
                            setDraftTasks((items) =>
                              items.map((item, i) =>
                                i === index ? { ...item, dueAt: e.target.value } : item,
                              ),
                            )
                          }
                        />
                      </label>
                    </div>
                  ))}
                </div>
              )}
              <p className="small muted">
                先整理再核对。每项截止时间需在未来 60 天内；最多 50 项。
              </p>
              {!draftTasks.length && (
                <button
                  className="button secondary"
                  disabled={!paste.trim()}
                  onClick={prepareDraftTasks}
                >
                  整理待发布任务
                </button>
              )}
              <button
                className="button primary"
                disabled={
                  !draftTasks.length || !dueDate || draftTasks.some((task) => !task.title.trim())
                }
                onClick={importTasks}
              >
                <Send size={17} /> 发布到孩子端
              </button>
            </div>
          </div>
        )}
        {tab === 'pair' && (
          <div className="narrow-page">
            <div className="section-kicker">CONNECT DEVICE</div>
            <h1>连接孩子的平板。</h1>
            <p className="lead">
              在孩子端查看 8 位配对码；已绑定其他家庭的平板可从设置中生成换绑码。
            </p>
            <div className="panel form-panel">
              <label>
                平板配对码
                <input
                  className="big-input"
                  inputMode="numeric"
                  maxLength={8}
                  value={pairCode}
                  onChange={(e) => setPairCode(e.target.value.replace(/\D/g, ''))}
                  placeholder="00000000"
                />
              </label>
              <button className="button primary" disabled={pairCode.length !== 8} onClick={pair}>
                <Link2 size={17} /> 完成配对
              </button>
              <p className="small muted">只允许已登录的家长完成配对。配对码 10 分钟有效。</p>
            </div>
          </div>
        )}
        {tab === 'review' && (
          <div className="narrow-page">
            <div className="section-kicker">DAILY REVIEW</div>
            <h1>今天的每一步，都看得见。</h1>
            <p className="lead">
              {report?.period?.date || '今天'} ·
              纸面分析是候选观察，完成状态以孩子提交和家长确认为准。
            </p>
            <div className="review-stats">
              <div>
                <strong>{Math.round((report?.duration_seconds || 0) / 60)}</strong>
                <span>分钟学习</span>
              </div>
              <div>
                <strong>{report?.hint_count || 0}</strong>
                <span>次求助</span>
              </div>
              <div>
                <strong>{report?.correction_count || 0}</strong>
                <span>份今日纸面批改</span>
              </div>
              <div>
                <strong>{report?.unreviewed_count || 0}</strong>
                <span>条累计待核对观察</span>
              </div>
            </div>
            <p className="small muted">
              今日提交 {report?.task_activity?.submitted || 0} 项 · 家长确认{' '}
              {report?.task_activity?.confirmed || 0} 项 · 退回补做{' '}
              {report?.task_activity?.returned || 0} 项 · 当前待家长确认{' '}
              {report?.pending_review_count || 0} 项。 同一任务在每项今日指标中只计一次。
            </p>
            <div className="panel timer-review-panel">
              <h2>时间回顾</h2>
              <p>
                独立作业 {formatStudyTime(report?.focus_seconds || 0)} · 辅导{' '}
                {formatStudyTime(report?.assisted_seconds || 0)} · 批改等待{' '}
                {formatStudyTime(report?.grading_wait_seconds || 0)} · 暂停{' '}
                {formatStudyTime(report?.paused_seconds || 0)}
              </p>
              {!!report?.by_subject?.length && (
                <div className="timer-subject-list">
                  {report.by_subject.map((item) => (
                    <span key={item.subject || 'unknown'}>
                      {item.subject || '待归类'} {formatStudyTime(item.seconds)}
                    </span>
                  ))}
                </div>
              )}
              {!!report?.legacy_duration_seconds && (
                <small>
                  旧记录约 {Math.round(report.legacy_duration_seconds / 60)}{' '}
                  分钟，包含暂停时间，未计入上方学习时长。
                </small>
              )}
            </div>
            <div className="panel correction-review-panel">
              <div className="panel-title">
                <h2>今日纸面批改</h2>
                <span>家长可复核</span>
              </div>
              {reviewCorrections.length ? (
                reviewCorrections.map((item, index) => (
                  <CorrectionRecord
                    key={item.id}
                    item={item}
                    initiallyOpen={index === 0}
                    onReview={reviewCorrection}
                    onUpdated={refresh}
                  />
                ))
              ) : (
                <p className="muted">今天还没有纸面批改记录；提交页的审核结果可在任务卡查看。</p>
              )}
              {report?.daily_records?.corrections?.truncated && (
                <small>显示最近 100 份，今日共 {report.daily_records.corrections.total} 份。</small>
              )}
            </div>
            <div className="panel">
              <div className="panel-title">
                <h2>今日纸面观察</h2>
                <span>可纠错</span>
              </div>
              {reviewObservations.length ? (
                reviewObservations.map((o) => (
                  <div className="review-row" key={o.id}>
                    <div>
                      <strong>{o.label}</strong>
                      <p>{o.evidence || '无明确可见依据'}</p>
                      {o.progress && <p>进度：{o.progress}</p>}
                      {o.writing && <p>书写：{o.writing}</p>}
                      {o.visible_steps && <p>可见步骤：{o.visible_steps}</p>}
                      <small>
                        置信度 {Math.round((o.confidence || 0) * 100)}% ·{' '}
                        {o.review_status === 'unreviewed'
                          ? '待复核'
                          : o.review_status === 'accepted'
                            ? '已确认'
                            : '已纠正'}
                      </small>
                    </div>
                    <div className="review-actions">
                      <button
                        title="确认观察"
                        onClick={async () => {
                          try {
                            await patch(`/v1/observations/${o.id}`, { review_status: 'accepted' });
                            refresh();
                          } catch (e) {
                            onError(e);
                          }
                        }}
                      >
                        <Check size={17} />
                      </button>
                      <button
                        title="纠正观察"
                        onClick={async () => {
                          try {
                            await patch(`/v1/observations/${o.id}`, { review_status: 'rejected' });
                            refresh();
                          } catch (e) {
                            onError(e);
                          }
                        }}
                      >
                        <X size={17} />
                      </button>
                    </div>
                  </div>
                ))
              ) : (
                <Empty
                  icon={<Sparkles />}
                  title="暂无观察"
                  text="孩子开始作业后，这里记录可复核的变化。"
                />
              )}
              {report?.daily_records?.observations?.truncated && (
                <small>
                  显示最近 100 条，今日共 {report.daily_records.observations.total} 条。
                </small>
              )}
            </div>
            <div className="panel thought-panel">
              <h2>孩子今天说出的想法</h2>
              {reviewReflections.length ? (
                reviewReflections.map((r) => (
                  <div className="thought-row" key={r.id}>
                    {r.text}
                    <small>孩子口述或输入 · 未经 AI 推断</small>
                  </div>
                ))
              ) : (
                <p className="muted">孩子还没有记录自己的思路。</p>
              )}
              {report?.daily_records?.reflections?.truncated && (
                <small>显示最近 100 条，今日共 {report.daily_records.reflections.total} 条。</small>
              )}
            </div>
            <div className="panel history-panel">
              <h2>隐私与数据</h2>
              <p>
                任务、观察、提示及逐页批改截图默认 60
                天后删除。自动观察裁剪图不保存；直播音视频不录制。
              </p>
              <button
                className="button ghost danger"
                onClick={async () => {
                  if (!confirm('确定提前删除全部作业历史吗？')) return;
                  try {
                    reportRequestRef.current?.abort();
                    setReport(null);
                    await api('/v1/data/delete', { method: 'POST' });
                    refresh();
                    setReport(null);
                    onNotice('历史已删除');
                  } catch (e) {
                    onError(e);
                  }
                }}
              >
                <Trash2 size={16} /> 立即删除历史
              </button>
            </div>
          </div>
        )}
      </main>
      <div className="parent-voice-dock">
        <VoiceControls
          onText={handleVoice}
          onError={(error) => onError(voiceErrorWithTranscriptionHint(error, dashboard, true))}
          remoteTranscription={remoteTranscription}
          disabled={Boolean(liveId)}
          disabledLabel="实时查看中，语音指令已暂停"
          compact
        />
      </div>
      {liveId && (
        <ParentLive id={liveId} onClose={stopLive} onRemoteEnd={remoteEnd} onError={onError} />
      )}
    </div>
  );
}

function Empty({ icon, title, text }) {
  return (
    <div className="empty">
      {icon}
      <strong>{title}</strong>
      <p>{text}</p>
    </div>
  );
}

function SelectionInk({ strokes }) {
  return (
    <svg
      className="selection-ink"
      viewBox="0 0 1000 1000"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      {strokes.map((stroke, index) => (
        <polyline
          key={index}
          points={stroke.map(([x, y]) => `${x * 1000},${y * 1000}`).join(' ')}
        />
      ))}
    </svg>
  );
}

function SelectionBox({
  selection,
  strokes,
  resetOnNextStroke,
  onChange,
  onStrokeEnd,
  onHint,
  onCorrect,
  disabled,
}) {
  const gesture = useRef(null);
  const point = (event) => {
    const box = event.currentTarget.getBoundingClientRect();
    return [
      Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)),
      Math.max(0, Math.min(1, (event.clientY - box.top) / box.height)),
    ];
  };
  const down = (event) => {
    if (gesture.current) return;
    if (event.target.closest('button')) return;
    const origin = point(event);
    gesture.current = {
      pointerId: event.pointerId,
      strokes: [...(resetOnNextStroke ? [] : strokes), [origin]],
    };
    onChange(penSelectionBounds(gesture.current.strokes), gesture.current.strokes);
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const move = (event) => {
    const current = gesture.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const end = point(event);
    const latest = current.strokes.at(-1);
    const previous = latest.at(-1);
    if (Math.hypot(end[0] - previous[0], end[1] - previous[1]) < 0.002) return;
    latest.push(end);
    onChange(penSelectionBounds(current.strokes), [...current.strokes]);
  };
  const up = (event) => {
    if (gesture.current?.pointerId !== event.pointerId) return;
    move(event);
    const strokes = gesture.current.strokes;
    if (strokes.at(-1).length < 2) {
      strokes.pop();
      onChange(penSelectionBounds(strokes), [...strokes]);
    }
    gesture.current = null;
    onStrokeEnd(strokes);
  };
  const cancel = (event) => {
    if (gesture.current?.pointerId !== event.pointerId) return;
    const strokes = gesture.current.strokes.slice(0, -1);
    gesture.current = null;
    onChange(penSelectionBounds(strokes), strokes);
  };
  return (
    <div
      className="selection-layer"
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={cancel}
    >
      <SelectionInk strokes={strokes} />
      {selection && penSelectionReady(strokes) && (
        <div
          className="selection-actions"
          style={{
            left: `${Math.min(0.5, Math.max(0.01, selection[0])) * 100}%`,
            top: `${(selection[1] + selection[3] > 0.87 ? Math.max(0.01, selection[1] - 0.095) : selection[1] + selection[3] + 0.012) * 100}%`,
          }}
        >
          <button type="button" disabled={disabled} onClick={onHint}>
            提示
          </button>
          <button type="button" disabled={disabled} onClick={onCorrect}>
            批改
          </button>
          <button type="button" aria-label="清除圈画" onClick={() => onChange(null, [])}>
            ×
          </button>
        </div>
      )}
    </div>
  );
}

function ParentLive({ id, onClose, onRemoteEnd, onError }) {
  const videoRef = useRef(null),
    overlayRef = useRef(null),
    socketRef = useRef(null),
    peerRef = useRef(null),
    audioRef = useRef(null),
    micRef = useRef(null),
    drawingRef = useRef([]);
  const [status, setStatus] = useState('连接中…'),
    [talk, setTalk] = useState(false),
    [talkBusy, setTalkBusy] = useState(false),
    [dots, setDots] = useState([]),
    [videoAspect, setVideoAspect] = useState('16 / 9');
  useEffect(() => {
    const hide = () => onClose();
    window.addEventListener('appbackground', hide);
    return () => window.removeEventListener('appbackground', hide);
  }, [id]);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const ice = await api('/v1/ice');
        const socket = await connectRealtime(async (msg) => {
          if (msg.type === 'peer_ready' && msg.role === 'child') requestOffer();
          if (msg.type === 'offer') {
            await peerRef.current?.setRemoteDescription(msg.sdp);
            await peerRef.current?.setLocalDescription(await peerRef.current.createAnswer());
            socket.send(JSON.stringify({ type: 'answer', sdp: peerRef.current.localDescription }));
          }
          if (msg.type === 'answer') await peerRef.current?.setRemoteDescription(msg.sdp);
          if (msg.type === 'ice') {
            try {
              await peerRef.current?.addIceCandidate(msg.candidate);
            } catch {
              /* peer reset */
            }
          }
          if (msg.type === 'live_ended') {
            setStatus('直播已结束');
            onRemoteEnd();
          }
        });
        if (cancelled) {
          socket.close();
          return;
        }
        socketRef.current = socket;
        const peer = new RTCPeerConnection({ iceServers: ice.iceServers });
        peerRef.current = peer;
        peer.ontrack = (event) => {
          if (event.track.kind === 'video') {
            videoRef.current.srcObject = event.streams[0];
            videoRef.current.onloadedmetadata = () => {
              const video = videoRef.current;
              if (video?.videoWidth && video?.videoHeight)
                setVideoAspect(`${video.videoWidth} / ${video.videoHeight}`);
            };
          } else audioRef.current.srcObject = event.streams[0];
          setStatus('正在查看纸面');
        };
        peer.onicecandidate = (event) => {
          if (event.candidate && socket.readyState === 1)
            socket.send(JSON.stringify({ type: 'ice', candidate: event.candidate }));
        };
        peer.onconnectionstatechange = () => {
          if (peer.connectionState === 'failed') setStatus('连接失败，请检查 TURN 配置与网络');
        };
        socket.onopen = () => requestOffer();
        function requestOffer() {
          if (socket.readyState === 1)
            socket.send(JSON.stringify({ type: 'offer', request: true }));
        }
      } catch (e) {
        onError(e);
        setStatus('无法连接');
      }
    })();
    return () => {
      cancelled = true;
      socketRef.current?.close();
      peerRef.current?.close();
      micRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, [id]);
  const begin = (e) => {
    const box = overlayRef.current.getBoundingClientRect();
    drawingRef.current = [[(e.clientX - box.left) / box.width, (e.clientY - box.top) / box.height]];
    overlayRef.current.setPointerCapture(e.pointerId);
    setDots([...drawingRef.current]);
  };
  const move = (e) => {
    if (!drawingRef.current.length) return;
    const box = overlayRef.current.getBoundingClientRect();
    drawingRef.current.push([
      Math.max(0, Math.min(1, (e.clientX - box.left) / box.width)),
      Math.max(0, Math.min(1, (e.clientY - box.top) / box.height)),
    ]);
    setDots([...drawingRef.current]);
  };
  const end = () => {
    if (drawingRef.current.length > 1 && socketRef.current?.readyState === 1)
      socketRef.current.send(
        JSON.stringify({ type: 'annotation', points: drawingRef.current.slice(0, 300) }),
      );
    drawingRef.current = [];
  };
  const toggleTalk = async () => {
    if (talkBusy) return;
    setTalkBusy(true);
    try {
      if (!peerRef.current) throw new Error('请等待纸面连接后再通话');
      if (!talk) {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true },
        });
        stream.getAudioTracks().forEach((track) => peerRef.current.addTrack(track, stream));
        micRef.current = stream;
        setTalk(true);
        socketRef.current.send(JSON.stringify({ type: 'voice_on' }));
        const offer = await peerRef.current.createOffer();
        await peerRef.current.setLocalDescription(offer);
        socketRef.current.send(
          JSON.stringify({ type: 'offer', sdp: peerRef.current.localDescription }),
        );
      } else {
        peerRef.current
          .getSenders()
          .filter((s) => s.track?.kind === 'audio')
          .forEach((s) => {
            s.track.stop();
            peerRef.current.removeTrack(s);
          });
        micRef.current = null;
        setTalk(false);
        socketRef.current.send(JSON.stringify({ type: 'voice_off' }));
        const offer = await peerRef.current.createOffer();
        await peerRef.current.setLocalDescription(offer);
        socketRef.current.send(
          JSON.stringify({ type: 'offer', sdp: peerRef.current.localDescription }),
        );
      }
    } catch (e) {
      onError(e);
    } finally {
      setTalkBusy(false);
    }
  };
  return (
    <div className="live-overlay">
      <div className="live-window">
        <div className="live-head">
          <div>
            <span className="live-dot" /> <strong>实时纸面</strong>
            <small>{talk ? '语音通话中 · 麦克风开启' : status}</small>
          </div>
          <button className="icon-button" onClick={onClose}>
            <X />
          </button>
        </div>
        <div className="live-stage" style={{ aspectRatio: videoAspect }}>
          <video ref={videoRef} autoPlay playsInline muted={false} />
          <svg
            ref={overlayRef}
            className="draw-surface"
            onPointerDown={begin}
            onPointerMove={move}
            onPointerUp={end}
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
          >
            <polyline
              points={dots.map(([x, y]) => `${x * 100},${y * 100}`).join(' ')}
              fill="none"
              stroke="#ff6b4d"
              strokeWidth="0.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span className="stage-tip">用手指在画面上画圈，孩子会同步看到标注</span>
        </div>
        <audio ref={audioRef} autoPlay />
        <div className="live-tools">
          <button
            className={`button ${talk ? 'recording' : 'secondary'}`}
            disabled={talkBusy}
            onClick={toggleTalk}
          >
            <Mic size={17} />
            {talkBusy ? '正在切换语音…' : talk ? '结束语音通话' : '开启语音通话'}
          </button>
          <button
            className="button ghost"
            onClick={() => {
              setDots([]);
              socketRef.current?.send(JSON.stringify({ type: 'clear_annotation' }));
            }}
          >
            清除圈画
          </button>
          <button className="button dark" onClick={onClose}>
            结束查看
          </button>
        </div>
      </div>
    </div>
  );
}

const correctionLabels = {
  looks_correct: '看起来正确',
  check_again: '需要订正',
  needs_discussion: '适合讨论',
  unclear: '暂无法判断',
};

function correctionLabel(check) {
  return check.status === 'check_again' && check.answer_state === 'blank'
    ? '未作答（错题）'
    : correctionLabels[check.status] || '暂无法判断';
}

function PaperMark({ marker, selected = false, onClick }) {
  const symbol =
    marker.status === 'looks_correct' ? (
      <svg viewBox="0 0 40 40" aria-hidden="true">
        <path className="paper-mark-halo" d="M5 21.5 14.5 30 35 7.5" />
        <path d="M5 21.5 14.5 30 35 7.5" />
      </svg>
    ) : marker.status === 'check_again' ? (
      <svg viewBox="0 0 40 40" aria-hidden="true">
        <path className="paper-mark-halo" d="M9 8 31 31 M31 9 8 31" />
        <path d="M9 8 31 31 M31 9 8 31" />
      </svg>
    ) : (
      <span className="paper-mark-word" aria-hidden="true">
        {marker.status === 'needs_discussion' ? '议' : '?'}
      </span>
    );
  return (
    <button
      type="button"
      className={`paper-mark ${marker.status} ${paperMarkEdge(marker.box)} ${marker.reviewed ? 'parent-reviewed' : ''} ${marker.stepIndex != null ? 'paper-step-mark' : ''} ${selected ? 'selected' : ''}`}
      style={{
        left: `${marker.box[0] * 100}%`,
        top: `${marker.box[1] * 100}%`,
        width: `${marker.box[2] * 100}%`,
        height: `${marker.box[3] * 100}%`,
      }}
      aria-label={`${marker.label}，${correctionLabel(marker)}${marker.reviewed ? '，家长已校对' : ''}，查看分析`}
      aria-pressed={selected}
      onClick={onClick}
    >
      <span className="paper-mark-ink">{symbol}</span>
    </button>
  );
}

function CorrectionRecord({ item, initiallyOpen, onReview, onUpdated }) {
  const [open, setOpen] = useState(initiallyOpen);
  const [snapshotUrl, setSnapshotUrl] = useState('');
  const [snapshotError, setSnapshotError] = useState('');
  useEffect(() => {
    if (!open || !item.image_available) return;
    const controller = new AbortController();
    let url = '';
    privateImage(`/v1/corrections/${item.id}/image`, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        url = URL.createObjectURL(blob);
        setSnapshotUrl(url);
        setSnapshotError('');
      })
      .catch((error) => {
        if (!controller.signal.aborted) setSnapshotError(error.message);
      });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
      setSnapshotUrl('');
    };
  }, [open, item.id, item.image_available]);
  return (
    <div className="correction-review">
      <button
        className="correction-review-toggle"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span>
          <strong>
            {item.subject_label || item.subject || item.task_title || '未关联任务'}
            {item.page_label ? ` · ${item.page_label}` : ''}
          </strong>
          <small>
            {new Date(item.created_at * 1000).toLocaleString('zh-CN')} · {item.checks.length} 项 ·{' '}
            {item.batch_id ? `本批第 ${item.page_index}/${item.batch_total} 页 · ` : ''}
            {item.review_status === 'accepted'
              ? '已确认'
              : item.review_status === 'rejected'
                ? '有误'
                : '待复核'}
          </small>
        </span>
        <span>{open ? '收起' : '查看截图与结果'}</span>
      </button>
      {open && (
        <>
          {item.image_available && !snapshotUrl && !snapshotError && (
            <p className="muted">正在加载本次作业截图…</p>
          )}
          {snapshotError && <p className="muted">{snapshotError}</p>}
          {!item.image_available && <p className="muted">这条旧记录没有保存批改截图。</p>}
          <CorrectionResult item={item} snapshotUrl={snapshotUrl} onUpdated={onUpdated} />
          {onReview && (
            <div className="review-actions correction-review-actions">
              <button
                title="确认批改"
                aria-label="确认批改"
                onClick={() => onReview(item, 'accepted')}
              >
                <Check size={17} />
              </button>
              <button
                title="批改有误"
                aria-label="批改有误"
                onClick={() => onReview(item, 'rejected')}
              >
                <X size={17} />
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function CorrectionInspector({
  item,
  snapshotUrl,
  markers,
  activeMarker,
  onSelect,
  onClose,
  recheck,
  recheckBusy,
  recheckError,
  onRecheck,
  onParentReview,
}) {
  const [imageAspect, setImageAspect] = useState(1);
  const [showMarks, setShowMarks] = useState(true);
  const closeRef = useRef(null);
  const detailRef = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const check = item.checks[activeMarker.checkIndex];
  const original = check.parent_review
    ? check.ai_review || check.original_review || check
    : check.original_review || check;
  const firstAi = check.original_review || original;
  const step =
    recheck || check.parent_review || activeMarker.stepIndex == null
      ? null
      : original.steps?.[activeMarker.stepIndex];
  const [editingReview, setEditingReview] = useState(false);
  const [positionMode, setPositionMode] = useState(false);
  const [reviewDraft, setReviewDraft] = useState({
    status: check.status,
    answer: check.answer || '',
    note: '',
    box: check.box,
  });
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewError, setReviewError] = useState('');
  useEffect(() => {
    setEditingReview(false);
    setPositionMode(false);
    setReviewDraft({ status: check.status, answer: check.answer || '', note: '', box: check.box });
    setReviewError('');
  }, [activeMarker.checkIndex, check.parent_revision]);
  const saveParentReview = async () => {
    if (reviewBusy) return;
    if (
      (reviewDraft.status !== check.status || reviewDraft.answer.trim() !== (check.answer || '')) &&
      reviewDraft.note.trim().length < 2
    ) {
      setReviewError('修改判断或答案时，请写一句更正说明。');
      return;
    }
    setReviewBusy(true);
    setReviewError('');
    try {
      await onParentReview(activeMarker.checkIndex, {
        ...reviewDraft,
        answer: reviewDraft.answer.trim(),
        note: reviewDraft.note.trim(),
        revision: check.parent_revision || 0,
      });
      setEditingReview(false);
      setPositionMode(false);
    } catch (error) {
      setReviewError(error.message || '保存失败，请稍后重试。');
    } finally {
      setReviewBusy(false);
    }
  };
  const locateParentMark = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const width = reviewDraft.box?.[2] || 0.08;
    const height = reviewDraft.box?.[3] || 0.06;
    const x = Math.max(
      0,
      Math.min(1 - width, (event.clientX - rect.left) / rect.width - width / 2),
    );
    const y = Math.max(
      0,
      Math.min(1 - height, (event.clientY - rect.top) / rect.height - height / 2),
    );
    setReviewDraft((draft) => ({
      ...draft,
      box: [x, y, width, height].map((value) => Math.round(value * 10000) / 10000),
    }));
    setPositionMode(false);
  };
  useEffect(() => {
    detailRef.current?.scrollTo({ top: 0 });
  }, [activeMarker.id]);
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    closeRef.current?.focus({ preventScroll: true });
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onCloseRef.current();
      if (event.key !== 'Tab') return;
      const buttons = [
        ...(closeRef.current
          ?.closest('[role="dialog"]')
          ?.querySelectorAll('button:not([disabled])') || []),
      ];
      if (!buttons.length) return;
      if (event.shiftKey && document.activeElement === buttons[0]) {
        event.preventDefault();
        buttons.at(-1).focus();
      } else if (!event.shiftKey && document.activeElement === buttons.at(-1)) {
        event.preventDefault();
        buttons[0].focus();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', onKeyDown);
    };
  }, []);
  return (
    <div className="correction-inspector-backdrop" onClick={onClose}>
      <div
        className="correction-inspector"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${item.id}-inspector-title`}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="inspector-image-pane">
          <div className="inspector-paper" style={{ aspectRatio: imageAspect }}>
            <img
              src={snapshotUrl}
              alt="本次批改的原始作业截图"
              onLoad={(event) =>
                setImageAspect(event.currentTarget.naturalWidth / event.currentTarget.naturalHeight)
              }
            />
            {showMarks && activeMarker.box && (
              <div
                className="paper-mark-focus"
                style={{
                  left: `${activeMarker.box[0] * 100}%`,
                  top: `${activeMarker.box[1] * 100}%`,
                  width: `${activeMarker.box[2] * 100}%`,
                  height: `${activeMarker.box[3] * 100}%`,
                }}
                aria-hidden="true"
              />
            )}
            {showMarks &&
              markers.map((marker) => (
                <PaperMark
                  key={marker.id}
                  marker={marker}
                  selected={marker.id === activeMarker.id}
                  onClick={() => onSelect(marker.id)}
                />
              ))}
            {editingReview && reviewDraft.box && (
              <div
                className="parent-position-preview"
                style={{
                  left: `${reviewDraft.box[0] * 100}%`,
                  top: `${reviewDraft.box[1] * 100}%`,
                  width: `${reviewDraft.box[2] * 100}%`,
                  height: `${reviewDraft.box[3] * 100}%`,
                }}
                aria-hidden="true"
              />
            )}
            {positionMode && (
              <button
                type="button"
                className="parent-position-capture"
                onClick={locateParentMark}
                aria-label="点击作业截图，设置这道题的标记位置"
              >
                <span>点纸面作答位置，移动这道题的标记</span>
              </button>
            )}
          </div>
          <div className="inspector-image-caption">
            <span>{showMarks ? '点击标记切换题目' : '正在查看无标记原图'}</span>
            <button type="button" aria-pressed={showMarks} onClick={() => setShowMarks(!showMarks)}>
              {showMarks ? '隐藏标记' : '显示标记'}
            </button>
          </div>
        </div>
        <div className="inspector-detail">
          <header className="inspector-head">
            <div>
              <span className="inspector-eyebrow">纸面批改 · 对照复检</span>
              <h3 id={`${item.id}-inspector-title`}>{activeMarker.label}</h3>
              <span
                className={`correction-status ${step?.status || check.parent_review?.status || recheck?.status || check.status}`}
              >
                {correctionLabel({
                  status:
                    step?.status || check.parent_review?.status || recheck?.status || check.status,
                  answer_state: step ? undefined : (recheck || check).answer_state,
                })}
              </span>
              {check.parent_review && <small className="parent-reviewed-tag">家长已校对</small>}
            </div>
            <button
              ref={closeRef}
              type="button"
              className="inspector-close"
              onClick={onClose}
              aria-label="关闭解答浮层，返回截图"
            >
              <X size={19} />
            </button>
          </header>
          <div ref={detailRef} className="inspector-detail-body">
            {check.parent_review && (
              <section className="parent-review-result">
                <span>家长校对结果 · 第 {check.parent_review.revision} 次</span>
                <p>
                  当前判断：
                  <strong>{correctionLabels[check.parent_review.status] || '暂无法判断'}</strong>
                </p>
                {check.parent_review.answer && (
                  <p>家长核对的纸面答案：{check.parent_review.answer}</p>
                )}
                {check.parent_review.note && <p>更正说明：{check.parent_review.note}</p>}
                <small>家长校对优先显示；下方 AI 分析和复检记录供对照。</small>
              </section>
            )}
            {mode === 'parent' && (
              <section className="parent-review-editor">
                <button
                  type="button"
                  className="parent-review-toggle"
                  aria-expanded={editingReview}
                  onClick={() => {
                    setEditingReview(!editingReview);
                    setPositionMode(false);
                    setReviewDraft({
                      status: check.status,
                      answer: check.answer || '',
                      note: '',
                      box: check.box,
                    });
                    setReviewError('');
                  }}
                >
                  <ShieldCheck size={16} />
                  {editingReview
                    ? '收起家长校对'
                    : check.parent_review
                      ? '修改家长校对'
                      : '家长校对／更正这题'}
                </button>
                {editingReview && (
                  <div className="parent-review-fields">
                    <div className="parent-review-statuses" role="group" aria-label="家长判断">
                      {Object.entries(correctionLabels).map(([status, label]) => (
                        <button
                          key={status}
                          type="button"
                          className={reviewDraft.status === status ? 'selected' : ''}
                          aria-pressed={reviewDraft.status === status}
                          onClick={() => setReviewDraft((draft) => ({ ...draft, status }))}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <label>
                      纸上实际答案
                      <input
                        value={reviewDraft.answer}
                        maxLength={200}
                        onChange={(event) =>
                          setReviewDraft((draft) => ({ ...draft, answer: event.target.value }))
                        }
                      />
                    </label>
                    <label>
                      更正说明
                      <textarea
                        value={reviewDraft.note}
                        maxLength={500}
                        rows={2}
                        placeholder="例如：原判漏看了题干条件"
                        onChange={(event) =>
                          setReviewDraft((draft) => ({ ...draft, note: event.target.value }))
                        }
                      />
                    </label>
                    <div className="parent-review-position-actions">
                      <button type="button" onClick={() => setPositionMode(!positionMode)}>
                        {positionMode ? '取消定位' : '在原图上定位标记'}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setReviewDraft((draft) => ({ ...draft, box: null }));
                          setPositionMode(false);
                        }}
                      >
                        清除错误位置
                      </button>
                    </div>
                    {positionMode && <small>请点左侧截图中这道题的作答位置。</small>}
                    {reviewError && (
                      <p className="inspector-recheck-error" role="alert">
                        {reviewError}
                      </p>
                    )}
                    <button
                      type="button"
                      className="parent-review-save"
                      disabled={reviewBusy}
                      onClick={saveParentReview}
                    >
                      {reviewBusy ? '正在保存家长校对…' : '保存家长校对'}
                    </button>
                  </div>
                )}
              </section>
            )}
            <section className="inspector-recheck-actions">
              <button
                type="button"
                disabled={recheckBusy || (recheck?.attempt || check.recheck_count || 0) >= 6}
                onClick={() => onRecheck(activeMarker.checkIndex)}
              >
                <RefreshCw size={15} />
                {recheckBusy
                  ? '正在聚焦复检…'
                  : (recheck?.attempt || check.recheck_count || 0) >= 6
                    ? '已达复检次数上限'
                    : 'AI 复检本题'}
              </button>
              <small>
                {check.parent_review
                  ? 'AI 复检只提供新建议，不会覆盖家长校对。'
                  : '复检只针对这道题，原判会保留供对照。'}
              </small>
              {recheckError && (
                <p className="inspector-recheck-error" role="alert">
                  {recheckError}
                </p>
              )}
            </section>
            {recheck && (
              <section className="inspector-recheck-result" aria-live="polite">
                <span>单题复检 · 第 {recheck.attempt} 次</span>
                <div className="inspector-status-compare">
                  <span className={`correction-status ${firstAi.status}`}>
                    原判：{correctionLabel(firstAi)}
                  </span>
                  <ArrowRight size={15} />
                  <span className={`correction-status ${recheck.status}`}>
                    复检：{correctionLabel(recheck)}
                  </span>
                </div>
                {recheck.disagrees && <p>两次结论不同，请家长结合原题核对后再决定。</p>}
                {recheck.answer_state === 'blank' ? (
                  <p>复检确认：孩子未作答，按错题计。</p>
                ) : recheck.answer ? (
                  <p>复检读到的答案：{recheck.answer}</p>
                ) : null}
                {recheck.evidence && <p>纸面依据：{recheck.evidence}</p>}
                {recheck.reason && <p>复检判断：{recheck.reason}</p>}
                <WorkedSolution check={recheck} compact />
              </section>
            )}
            {(original.answer || original.answer_state === 'blank') && (
              <section>
                <span>{recheck || check.parent_review ? 'AI 读到的答案' : '孩子写下的答案'}</span>
                <p className="inspector-answer">{original.answer || '未作答（按错题计）'}</p>
              </section>
            )}
            {step && (
              <section className="inspector-step">
                <span>本处书写步骤 · {step.label || '步骤分析'}</span>
                {step.evidence && <p>纸面依据：{step.evidence}</p>}
                {step.explanation && <p>{step.explanation}</p>}
              </section>
            )}
            <WorkedSolution
              check={original}
              cautionText={
                check.parent_review
                  ? '原 AI 解答仅供对照，请以家长校对为准。'
                  : recheck?.disagrees
                    ? '原 AI 解答与复检意见不同，请对照原题核实。'
                    : recheck?.status === 'unclear'
                      ? '复检尚不可靠，原 AI 解答仅供对照。'
                      : ''
              }
            />
            {original.evidence && (
              <section>
                <span>{recheck || check.parent_review ? 'AI 纸面依据' : '原题与纸面依据'}</span>
                <p>{original.evidence}</p>
              </section>
            )}
            {original.reason && (
              <section>
                <span>{recheck || check.parent_review ? 'AI 判断理由' : '批改判断'}</span>
                <p>{original.reason}</p>
              </section>
            )}
            {!step &&
              (original.steps || []).map((part, index) => (
                <section className="inspector-step" key={`${item.id}-detail-step-${index}`}>
                  <span>
                    {recheck || check.parent_review ? 'AI 步骤 · ' : ''}
                    {part.label || `书写步骤 ${index + 1}`}
                  </span>
                  {part.evidence && <p>纸面依据：{part.evidence}</p>}
                  {part.explanation && <p>{part.explanation}</p>}
                </section>
              ))}
            {original.next_step && (
              <section>
                <span>{recheck || check.parent_review ? 'AI 建议' : '下一步'}</span>
                <p>{original.next_step}</p>
              </section>
            )}
          </div>
          <p className="inspector-note">标记位置与分析由 AI 估计，请对照截图和原题复核。</p>
        </div>
      </div>
    </div>
  );
}

function CorrectionResult({ item, snapshotUrl = '', onUpdated }) {
  const [activeMarkerId, setActiveMarkerId] = useState('');
  const [showMarks, setShowMarks] = useState(true);
  const [writtenReviewOpen, setWrittenReviewOpen] = useState(false);
  const [localRechecks, setLocalRechecks] = useState({});
  const [localParentChecks, setLocalParentChecks] = useState({});
  const [recheckErrors, setRecheckErrors] = useState({});
  const [recheckBusyIndex, setRecheckBusyIndex] = useState(null);
  const markerTriggerRef = useRef(null);
  const checkFor = (index) => {
    const local = localParentChecks[index];
    const saved = item.checks[index];
    return (local?.parent_revision || 0) > (saved?.parent_revision || 0) ? local : saved;
  };
  const effectiveItem = { ...item, checks: item.checks.map((_, index) => checkFor(index)) };
  const recheckFor = (index) => {
    const local = localRechecks[index];
    const saved = checkFor(index)?.recheck;
    return (local?.attempt || 0) >= (saved?.attempt || 0) ? local || saved : saved;
  };
  const saveParentReview = async (index, update) => {
    const result = await patch(`/v1/corrections/${item.id}/checks/${index}/parent-review`, update);
    setLocalParentChecks((checks) => ({ ...checks, [index]: result.check }));
    onUpdated?.();
    return result;
  };
  const requestRecheck = async (index) => {
    if (recheckBusyIndex !== null || !snapshotUrl) return;
    setRecheckBusyIndex(index);
    setRecheckErrors((errors) => ({ ...errors, [index]: '' }));
    try {
      const result = await post(`/v1/corrections/${item.id}/checks/${index}/recheck`, {});
      setLocalRechecks((items) => ({ ...items, [index]: result.recheck }));
      if (mode === 'child')
        speak(`这道题复检完成。${correctionLabel(result.recheck)}。请对照屏幕和原题。`);
      onUpdated?.();
    } catch (error) {
      setRecheckErrors((errors) => ({
        ...errors,
        [index]: error.message || '复检失败，请稍后重试',
      }));
    } finally {
      setRecheckBusyIndex(null);
    }
  };
  const allMarkers = effectiveItem.checks.flatMap((check, index) => [
    {
      id: `${item.id}-question-${index}`,
      box: check.box,
      status: check.parent_review?.status || recheckFor(index)?.status || check.status,
      answer_state: check.parent_review ? undefined : (recheckFor(index) || check).answer_state,
      reviewed: Boolean(check.parent_review),
      label: check.question || `第 ${index + 1} 题`,
      checkIndex: index,
      stepIndex: null,
    },
    ...(!recheckFor(index) && !check.parent_review ? check.steps || [] : []).map(
      (step, stepIndex) => ({
        id: `${item.id}-step-${index}-${stepIndex}`,
        box: step.box,
        status: step.status,
        label: `${check.question || `第 ${index + 1} 题`}，${step.label || `步骤 ${stepIndex + 1}`}`,
        checkIndex: index,
        stepIndex,
      }),
    ),
  ]);
  const markers = visiblePaperMarks(allMarkers);
  const summary =
    markers.length === 0
      ? item.summary.replace('请先查看叉号位置的订正分析。', '请查看下方逐题文字分析。')
      : item.summary;
  const activeMarker = allMarkers.find((marker) => marker.id === activeMarkerId);
  const closeInspector = () => {
    setActiveMarkerId('');
    requestAnimationFrame(() => {
      if (markerTriggerRef.current?.isConnected)
        markerTriggerRef.current.focus({ preventScroll: true });
    });
  };
  return (
    <div className="correction-result">
      <div className="correction-result-heading">
        <ClipboardCheck size={19} />
        <strong>
          本页批改 ·{' '}
          {item.review_status === 'accepted'
            ? '家长已确认'
            : item.review_status === 'rejected'
              ? '家长指出有误'
              : '等待家长复核'}
        </strong>
      </div>
      <p className="correction-summary">{summary}</p>
      {snapshotUrl && (
        <div className="correction-paper-wrap">
          {markers.length > 0 && (
            <div className="correction-paper-toolbar">
              <button
                type="button"
                aria-pressed={showMarks}
                onClick={() => {
                  setShowMarks(!showMarks);
                  setActiveMarkerId('');
                }}
              >
                {showMarks ? '隐藏标记，查看原图' : '显示批改标记'}
              </button>
            </div>
          )}
          <div className="correction-paper">
            <img
              src={snapshotUrl}
              alt={showMarks ? '本页作业截图，叠加批改标记' : '本页作业原始截图'}
            />
            {showMarks &&
              markers.map((marker) => (
                <PaperMark
                  key={marker.id}
                  marker={marker}
                  onClick={(event) => {
                    markerTriggerRef.current = event.currentTarget;
                    setActiveMarkerId(marker.id);
                  }}
                />
              ))}
          </div>
          {markers.length > 0 && (
            <small>点纸面标记查看作答与讲解；AI 标记位置请对照原题核对。</small>
          )}
          {!markers.length && <small>这次未能可靠定位答案区域，下方仍可查看逐题分析。</small>}
        </div>
      )}
      {item.checks.length ? (
        <details
          className="correction-written-review"
          open={writtenReviewOpen || !snapshotUrl || !markers.length}
          onToggle={(event) => {
            if (snapshotUrl && markers.length) setWrittenReviewOpen(event.currentTarget.open);
          }}
        >
          <summary>逐题文字分析 · {item.checks.length} 项</summary>
          {effectiveItem.checks.map((check, index) => {
            const recheck = recheckFor(index);
            const current = check.parent_review
              ? check
              : recheck
                ? {
                    ...check,
                    ...recheck,
                    steps: [],
                    next_step: '',
                    reference_answer: recheck.reference_answer || '',
                    solution_idea: recheck.solution_idea || '',
                    solution_steps: recheck.solution_steps || [],
                    comparison: recheck.comparison || '',
                    diagram: recheck.diagram || null,
                  }
                : check;
            return (
              <div
                className="correction-check"
                id={`${item.id}-question-${index}`}
                key={`${item.id}-${index}`}
              >
                <div className="correction-check-heading">
                  <strong>{check.question || `第 ${index + 1} 项`}</strong>
                  <span className={`correction-status ${current.status}`}>
                    {correctionLabel(current)}
                  </span>
                  {check.parent_review && <small className="parent-reviewed-tag">家长已校对</small>}
                </div>
                {current.answer_state === 'blank' ? (
                  <p>孩子作答：未作答（按错题计）</p>
                ) : current.answer ? (
                  <p>看到的答案：{current.answer}</p>
                ) : null}
                {current.evidence && <p>判断依据：{current.evidence}</p>}
                {current.reason && <p>{current.reason}</p>}
                {current.next_step && (
                  <p className="correction-next">下一步：{current.next_step}</p>
                )}
                <WorkedSolution
                  check={current}
                  compact
                  cautionText={check.parent_review ? 'AI 解答仅供对照，请以家长校对为准。' : ''}
                />
                {check.parent_review ? (
                  <p className="correction-recheck-summary">
                    家长校对：{check.parent_review.note || '已核对这道题'}
                  </p>
                ) : recheck ? (
                  <p className="correction-recheck-summary">
                    当前状态来自第 {recheck.attempt} 次单题复检
                    {recheck.disagrees ? ' · 与原判不同，待家长核对' : ' · 结论与原判相同'}
                  </p>
                ) : null}
                {snapshotUrl && (
                  <div className="correction-check-actions">
                    <button
                      type="button"
                      className="correction-recheck-inline"
                      disabled={
                        recheckBusyIndex !== null ||
                        (recheckFor(index)?.attempt || check.recheck_count || 0) >= 6
                      }
                      onClick={() => requestRecheck(index)}
                    >
                      <RefreshCw size={14} />
                      {recheckBusyIndex === index
                        ? '正在聚焦复检…'
                        : (recheckFor(index)?.attempt || check.recheck_count || 0) >= 6
                          ? '已达复检次数上限'
                          : 'AI 复检这题'}
                    </button>
                    {mode === 'parent' && (
                      <button
                        type="button"
                        className="correction-parent-review-inline"
                        onClick={(event) => {
                          markerTriggerRef.current = event.currentTarget;
                          setActiveMarkerId(`${item.id}-question-${index}`);
                        }}
                      >
                        <ShieldCheck size={14} /> 家长校对
                      </button>
                    )}
                  </div>
                )}
                {recheckErrors[index] && (
                  <p className="inspector-recheck-error" role="alert">
                    {recheckErrors[index]}
                  </p>
                )}
                {(current.steps || []).map((step, stepIndex) => (
                  <div
                    className="correction-step"
                    id={`${item.id}-step-${index}-${stepIndex}`}
                    key={`${item.id}-${index}-${stepIndex}`}
                  >
                    <strong>{step.label || `步骤 ${stepIndex + 1}`}</strong>
                    <span className={`correction-status ${step.status}`}>
                      {correctionLabels[step.status] || '暂无法判断'}
                    </span>
                    {step.evidence && <p>纸面依据：{step.evidence}</p>}
                    {step.explanation && <p>步骤分析：{step.explanation}</p>}
                  </div>
                ))}
              </div>
            );
          })}
        </details>
      ) : (
        <p className="muted">暂时没有足够清晰的已答题目。调整纸面后可以重试。</p>
      )}
      <small>AI 根据当前纸面给出建议；看不清的内容需重拍，完成状态由孩子和家长确认。</small>
      {activeMarker &&
        snapshotUrl &&
        createPortal(
          <CorrectionInspector
            item={effectiveItem}
            snapshotUrl={snapshotUrl}
            markers={markers}
            activeMarker={activeMarker}
            onSelect={setActiveMarkerId}
            onClose={closeInspector}
            recheck={recheckFor(activeMarker.checkIndex)}
            recheckBusy={recheckBusyIndex !== null}
            recheckError={recheckErrors[activeMarker.checkIndex]}
            onRecheck={requestRecheck}
            onParentReview={saveParentReview}
          />,
          document.body,
        )}
    </div>
  );
}

function isBlackCameraFrame(video) {
  if (!video?.videoWidth || !video?.videoHeight) return false;
  const canvas = document.createElement('canvas');
  canvas.width = 16;
  canvas.height = 12;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) return false;
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
  let visible = 0;
  for (let i = 0; i < pixels.length; i += 4) {
    if (Math.max(pixels[i], pixels[i + 1], pixels[i + 2]) > 14) visible++;
  }
  return visible <= 2;
}

function ChildHome({ dashboard, refresh, onError, onNotice, spokenText }) {
  const remoteTranscription = Boolean(
    dashboard.asr_available && dashboard.family.audio_transcription_allowed,
  );
  const [stage, setStage] = useState(
    dashboard.session?.status === 'active'
      ? 'active'
      : dashboard.session?.status === 'paused'
        ? 'paused'
        : 'home',
  );
  useEffect(() => {
    if (stage === 'active' && document.activeElement?.matches?.('input, textarea'))
      document.activeElement.blur();
  }, [stage]);
  const [sessionId, setSessionId] = useState(dashboard.session?.id || '');
  const [resuming, setResuming] = useState(false);
  const [stream, setStream] = useState(null),
    [cameraStarting, setCameraStarting] = useState(false),
    [ready, setReady] = useState(false),
    [setupTicket, setSetupTicket] = useState(''),
    [checking, setChecking] = useState(false),
    [analysis, setAnalysis] = useState('');
  const [cameraPreference, setCameraPreference] = useState(() => saved('camera_source') || 'auto');
  const [cameraAngle, setCameraAngle] = useState(() => cameraRotation(saved('camera_rotation')));
  const [cameraRotating, setCameraRotating] = useState(false);
  const cameraRotatingRef = useRef(false);
  const cameraViewRevision = useRef(0);
  const expectedCameraDimensions = useRef(null);
  const [cameraKind, setCameraKind] = useState('');
  const [cameraPreviewImage, setCameraPreviewImage] = useState('');
  const cameraSourceRef = useRef(null);
  const cameraOpenRef = useRef(null);
  const cameraStageRef = useRef(stage);
  const previewReturnStage = useRef('home');
  cameraStageRef.current = stage;
  useEffect(
    () => () => {
      cameraOpenRef.current?.abort();
      cameraSourceRef.current?.stop();
    },
    [],
  );
  useEffect(
    () => () => {
      if (cameraPreviewImage) URL.revokeObjectURL(cameraPreviewImage);
    },
    [cameraPreviewImage],
  );
  const [setupIssue, setSetupIssue] = useState(null);
  const setupIssueRef = useRef(null);
  const setupCheckSequence = useRef(0);
  const startingSessionRef = useRef(false);
  const [sessionStartError, setSessionStartError] = useState(false);
  const clearSetupIssue = () => {
    const issue = setupIssueRef.current;
    setupIssueRef.current = null;
    setSetupIssue(null);
    if (issue?.imageUrl) URL.revokeObjectURL(issue.imageUrl);
  };
  useEffect(
    () => () => {
      setupCheckSequence.current++;
      if (setupIssueRef.current?.imageUrl) URL.revokeObjectURL(setupIssueRef.current.imageUrl);
    },
    [],
  );
  const [hint, setHint] = useState(''),
    [question, setQuestion] = useState(''),
    [selectedTask, setSelectedTask] = useState(
      () =>
        (dashboard.hints || []).find(
          (item) =>
            item.session_id === dashboard.session?.id &&
            dashboard.tasks.some((task) => task.id === item.task_id),
        )?.task_id || '',
    ),
    [selection, setSelection] = useState(null);
  const [selectionStrokes, setSelectionStrokes] = useState([]);
  const [selectionConsumed, setSelectionConsumed] = useState(false);
  const requestHintRef = useRef(null);
  const markHintRequestRef = useRef(null);
  const [hintTarget, setHintTarget] = useState(null);
  const [teachingPaperSize, setTeachingPaperSize] = useState(null);
  const [teachingDraft, setTeachingDraft] = useState(() =>
    latestTeachingDraft(dashboard.hints, sessionId, selectedTask),
  );
  const [teachingMode, setTeachingMode] = useState('idle');
  const [reviewTeachingDraft, setReviewTeachingDraft] = useState(null);
  const [teachingPlayback, setTeachingPlayback] = useState(null);
  const teachingPlaybackRef = useRef(null);
  useEffect(() => {
    const interrupt = () => teachingPlaybackRef.current?.interrupt();
    window.addEventListener('paper-manual-voice-start', interrupt);
    return () => window.removeEventListener('paper-manual-voice-start', interrupt);
  }, []);
  const [reviewBeat, setReviewBeat] = useState(null);
  const [silentReview, setSilentReview] = useState(false);
  const hintScopeRef = useRef(null);
  hintScopeRef.current = { stage, sessionId, taskId: selectedTask };
  const [showTeachingMarks, setShowTeachingMarks] = useState(true);
  const teachingPaperVisibleRef = useRef(null);
  const [finishConfirmOpen, setFinishConfirmOpen] = useState(false);
  const [restOffer, setRestOffer] = useState(false);
  const hintContextRef = useRef(null);
  if (!hintContextRef.current) hintContextRef.current = createHintContext();
  const teachingRunRef = useRef(0);
  const lastTeachingSceneRef = useRef(null);
  const penHintSchedulerRef = useRef(null);
  if (!penHintSchedulerRef.current) {
    penHintSchedulerRef.current = createPenHintScheduler((strokes, revision) => {
      requestHintRef.current?.(READING_QUESTION, '', '', strokes, revision);
    });
  }
  useEffect(
    () => () => {
      penHintSchedulerRef.current?.cancel();
      markHintRequestRef.current?.abort();
      hintContextRef.current.invalidate();
    },
    [],
  );
  useEffect(() => {
    if (stage !== 'active') {
      // stopCamera already stopped old audio; preserve the new pause/setup/finish message.
      resetTeachingContext(false);
    }
  }, [stage]);
  const [hintBusyTarget, setHintBusyTarget] = useState('');
  useEffect(() => {
    if (!hintTarget?.imageUrl) return;
    const imageUrl = hintTarget.imageUrl;
    return () => URL.revokeObjectURL(imageUrl);
  }, [hintTarget?.imageUrl]);
  useEffect(() => {
    if (teachingMode !== 'lesson') return;
    const draft = teachingDraftFromTarget(hintTarget, sessionId, selectedTask);
    if (draft) setTeachingDraft(draft);
  }, [hintTarget, sessionId, selectedTask, teachingMode]);
  const [questionInputOpen, setQuestionInputOpen] = useState(false);
  const [tasksExpanded, setTasksExpanded] = useState(false);
  useEffect(() => {
    if (stage !== 'active' || !analysis) return;
    const timeout = setTimeout(() => setAnalysis(''), 8000);
    return () => clearTimeout(timeout);
  }, [stage, analysis]);
  const [hintLevel, setHintLevel] = useState(0);
  const [questionGuidance, setQuestionGuidance] = useState(null);
  const questionGuidanceRef = useRef(null);
  const saveQuestionGuidance = (binding) => {
    questionGuidanceRef.current = binding;
    setQuestionGuidance(binding);
  };
  const [hintBusy, setHintBusy] = useState(false);
  const [hintPreparationLabel, setHintPreparationLabel] = useState('');
  const [attemptBusy, setAttemptBusy] = useState(false);
  const lastHintSnapshot = useRef(null);
  const checkHintAttemptRef = useRef(null);
  const [correction, setCorrection] = useState(null);
  const [correctionOpen, setCorrectionOpen] = useState(false);
  const [correctionSnapshot, setCorrectionSnapshot] = useState('');
  const correctionRef = useRef(null);
  const correctionSnapshotRef = useRef('');
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchPurpose, setBatchPurpose] = useState('correction');
  const batchPageLimit = batchPurpose === 'submission' ? 12 : 6;
  const [submissionTaskId, setSubmissionTaskId] = useState('');
  const [manualTaskId, setManualTaskId] = useState('');
  const [manualPhotoIssue, setManualPhotoIssue] = useState('');
  const submissionRequestId = useRef('');
  const pendingSubmissionTaskId = useRef('');
  const [batchPages, setBatchPages] = useState([]);
  const batchPagesRef = useRef([]);
  const batchPreviewRef = useRef(null);
  const [batchCapturing, setBatchCapturing] = useState(false);
  const [batchSubmitting, setBatchSubmitting] = useState(false);
  const [batchAwaitingApproval, setBatchAwaitingApproval] = useState(false);
  const [batchConfirmOpen, setBatchConfirmOpen] = useState(false);
  const [batchProgress, setBatchProgress] = useState('');
  const batchCanceledRef = useRef(false);
  const batchRunRef = useRef(0);
  const batchCaptureInFlightRef = useRef(-1);
  const selectedCaptureRef = useRef(false);
  const [selectedCaptureBusy, setSelectedCaptureBusy] = useState(false);
  const submitWhenAuthorizedRef = useRef(false);
  const batchWatchRef = useRef({ frame: null, moving: true, stable: 0, lastScan: 0 });
  const [cameraAspect, setCameraAspect] = useState('16 / 9');
  const [cameraPortrait, setCameraPortrait] = useState(false);
  const [summary, setSummary] = useState(null);
  const [finishSyncPending, setFinishSyncPending] = useState(false);
  const [reminderEvent, setReminderEvent] = useState(null);
  const lastReminderAnnouncement = useRef('');
  const reminderTaskRef = useRef(null);
  const [timerGoal, setTimerGoal] = useState(
    dashboard.timer?.goal_seconds ?? dashboard.timer_suggestion?.goal_seconds ?? null,
  );
  const [timerSubject, setTimerSubject] = useState(dashboard.timer?.current_subject || null);
  const [timerDetailsOpen, setTimerDetailsOpen] = useState(false);
  const goalTouched = useRef(false);
  const goalNotice = useRef('');
  const subjectCandidate = useRef({ label: '', count: 0 });
  const pendingSubject = useRef('');
  const promptedSubjectKey = useRef('');
  const [live, setLive] = useState(Boolean(dashboard.live)),
    [voiceCall, setVoiceCall] = useState(false),
    [annotation, setAnnotation] = useState([]);
  const [helpSettingBusy, setHelpSettingBusy] = useState(false);
  const parentHelpMode = dashboard.family.proactive_help_mode || 'standard';
  const childHelpEnabled = dashboard.session?.proactive_help_enabled !== 0;
  const proactive = parentHelpMode !== 'off' && childHelpEnabled;
  const videoRef = useRef(null),
    socketRef = useRef(null),
    peerRef = useRef(null),
    currentStream = useRef(null),
    callStream = useRef(null),
    parentVoiceRequested = useRef(false),
    loopRef = useRef(null),
    lastFrame = useRef(null),
    stallWatchRef = useRef(createStallWatch()),
    stallReferenceRef = useRef(null),
    pendingHelpOffer = useRef(null),
    helpOfferExpiryRef = useRef(null),
    offeredQuestionsRef = useRef(new Set()),
    setupWatchRef = useRef({ frame: null, moving: false, stable: 0, lastCheck: 0 }),
    cameraHealthRef = useRef({ darkSamples: 0, restarts: 0, recovering: false }),
    previousCrop = useRef(null),
    busyAnalysis = useRef(false),
    pendingFinish = useRef(0),
    pendingBatchConfirm = useRef(0),
    lastHintKey = useRef(''),
    confirmedBefore = useRef(
      new Set(dashboard.tasks.filter((t) => t.status === 'confirmed').map((t) => t.id)),
    );
  const correctionAllowed = visibleCorrectionAllowed(dashboard, stage);
  const correctionTestBypass = correctionTestBypassEnabled(dashboard);
  const autoReply = useTimedVoiceReply(
    (context, text) => handleShortReply(context, text),
    !voiceCall && !questionInputOpen,
    remoteTranscription,
    dashboard.asr_available && !remoteTranscription ? '请家长在手机端开启短语音转写。' : '',
  );
  useEffect(() => {
    const pending = pendingTaskReminders(
      dashboard.tasks,
      dashboard.task_reminders || [],
      Math.floor(Date.now() / 1000),
    );
    const next = pending[0] || null;
    setReminderEvent(
      (previous) =>
        next ||
        (previous &&
        dashboard.tasks.some(
          (task) =>
            task.id === previous.task.id &&
            ['todo', 'doing', 'needs_revision'].includes(task.status),
        )
          ? previous
          : null),
    );
    if (
      !next ||
      stage === 'active' ||
      stage === 'paused' ||
      voiceCall ||
      document.visibilityState === 'hidden' ||
      autoReply.status
    )
      return;
    const grouped = pending.filter((item) => item.type === next.type);
    const signature = grouped.map((item) => `${item.task.id}:${item.key}`).join('|');
    if (signature === lastReminderAnnouncement.current) return;
    lastReminderAnnouncement.current = signature;
    reminderTaskRef.current = next;
    Promise.all(
      grouped.map((item) =>
        post(`/v1/tasks/${item.task.id}/reminders`, {
          event_key: item.key,
          action: 'heard',
        }),
      ),
    )
      .then(refresh)
      .catch(onError);
    autoReply.ask('task_reminder', reminderSpeech(next, grouped.length));
  }, [dashboard.tasks, dashboard.task_reminders, stage, voiceCall, autoReply.status]);
  const snoozeTaskReminder = async () => {
    const item = reminderEvent || reminderTaskRef.current;
    if (!item) return;
    autoReply.cancel();
    try {
      await post(`/v1/tasks/${item.task.id}/reminders`, { event_key: item.key, action: 'snooze' });
      lastReminderAnnouncement.current = '';
      setReminderEvent(null);
      await refresh();
      onNotice('30 分钟后再提醒');
    } catch (error) {
      onError(error);
    }
  };
  const dismissTaskReminder = async () => {
    const item = reminderEvent || reminderTaskRef.current;
    autoReply.cancel();
    setReminderEvent(null);
    if (!item) return;
    try {
      await post(`/v1/tasks/${item.task.id}/reminders`, { event_key: item.key, action: 'heard' });
      await refresh();
    } catch (error) {
      onError(error);
    }
  };
  useEffect(() => {
    stallWatchRef.current = createStallWatch();
    stallReferenceRef.current = null;
    if (!proactive && pendingHelpOffer.current) {
      pendingHelpOffer.current = null;
      clearTimeout(helpOfferExpiryRef.current);
      autoReply.cancel();
      setHintTarget((current) => (current?.kind === 'offer' ? null : current));
    }
  }, [proactive, sessionId]);
  const clearHelpOffer = (cancelPrompt = false) => {
    if (!pendingHelpOffer.current) return;
    pendingHelpOffer.current = null;
    clearTimeout(helpOfferExpiryRef.current);
    if (cancelPrompt) autoReply.cancel();
    setHintTarget((current) => (current?.kind === 'offer' ? null : current));
  };
  const timer = useSessionTimer({
    sessionId,
    active: stage === 'active' && Boolean(stream),
    kind: batchOpen
      ? 'grading_wait'
      : voiceCall || hintBusy || correctionOpen
        ? 'assisted'
        : 'focus',
    subject: timerSubject,
    dashboard,
  });
  useEffect(() => {
    if (dashboard.timer && stage !== 'done') setTimerGoal(dashboard.timer.goal_seconds);
  }, [dashboard.timer?.goal_seconds, stage]);
  useEffect(() => {
    if (!goalTouched.current && (stage === 'home' || stage === 'setup'))
      setTimerGoal(dashboard.timer_suggestion?.goal_seconds ?? null);
  }, [dashboard.timer_suggestion?.goal_seconds, stage]);
  useEffect(() => {
    if (stage !== 'active' || !timerGoal || timer.learning_seconds < timerGoal) return;
    const noticeKey = key(`timer_goal_notice_${sessionId}`);
    if (goalNotice.current === sessionId || localStorage.getItem(noticeKey)) return;
    goalNotice.current = sessionId;
    localStorage.setItem(noticeKey, JSON.stringify({ saved_at: Date.now() }));
    onNotice('已达到今天的参考目标，可以继续完成手上的题目，也可以稍后休息。');
    if (!autoReply.status) speak('已经达到今天的参考时间。可以继续完成手上的题目，不用着急。');
  }, [stage, sessionId, timerGoal, timer.learning_seconds]);
  const wasCorrectionAllowed = useRef(correctionAllowed);
  const lastHintPrompt = useRef('');
  const lastHintSpokenReference = useRef(false);
  const resetTeachingContext = (stopOutput = true) => {
    hintContextRef.current.invalidate();
    markHintRequestRef.current?.abort();
    markHintRequestRef.current = null;
    penHintSchedulerRef.current?.cancel();
    teachingRunRef.current++;
    teachingPlaybackRef.current?.cancel();
    teachingPlaybackRef.current = null;
    setTeachingPlayback(null);
    lastHintSnapshot.current = null;
    lastHintPrompt.current = '';
    lastHintSpokenReference.current = false;
    lastHintKey.current = '';
    lastTeachingSceneRef.current = null;
    saveQuestionGuidance(null);
    pendingHelpOffer.current = null;
    clearTimeout(helpOfferExpiryRef.current);
    if (stopOutput) {
      autoReply.cancel();
      stopSpeechOutput();
      clearSpeechCaption();
    }
    setHint('');
    setHintLevel(0);
    setHintTarget(null);
    setTeachingMode('idle');
    setReviewTeachingDraft(null);
    setReviewBeat(null);
    setHintBusy(false);
    setHintBusyTarget('');
    setAttemptBusy(false);
    setSelection(null);
    setSelectionStrokes([]);
    setShowTeachingMarks(true);
    setRestOffer(false);
  };
  const replaceCorrectionSnapshot = (url) => {
    if (correctionSnapshotRef.current) URL.revokeObjectURL(correctionSnapshotRef.current);
    correctionSnapshotRef.current = url;
    setCorrectionSnapshot(url);
  };
  useEffect(
    () => () => {
      if (correctionSnapshotRef.current) URL.revokeObjectURL(correctionSnapshotRef.current);
      batchPagesRef.current.forEach((page) => URL.revokeObjectURL(page.previewUrl));
    },
    [],
  );
  useEffect(() => {
    if (batchOpen && batchPreviewRef.current && stream) batchPreviewRef.current.srcObject = stream;
  }, [batchOpen, stream]);
  const replaceBatchPages = (pages) => {
    batchPagesRef.current = pages;
    setBatchPages(pages);
  };
  const clearBatchPages = () => {
    batchCanceledRef.current = true;
    batchRunRef.current++;
    submitWhenAuthorizedRef.current = false;
    batchWatchRef.current = { frame: null, moving: true, stable: 0, lastScan: 0 };
    batchPagesRef.current.forEach((page) => URL.revokeObjectURL(page.previewUrl));
    replaceBatchPages([]);
    setBatchOpen(false);
    setBatchPurpose('correction');
    setSubmissionTaskId('');
    setBatchConfirmOpen(false);
    setBatchAwaitingApproval(false);
    setBatchProgress('');
    currentStream.current
      ?.getVideoTracks()[0]
      ?.applyConstraints({
        width: { ideal: 1280 },
        height: { ideal: 960 },
        aspectRatio: { ideal: 4 / 3 },
      })
      .catch(() => {});
  };
  useEffect(() => {
    if (stage === 'paused' || (stage === 'active' && !correctionAllowed)) {
      replaceCorrectionSnapshot('');
    }
  }, [stage, correctionAllowed]);
  useEffect(() => {
    if (stage === 'active' && correctionAllowed && !wasCorrectionAllowed.current) {
      const accessMessage = correctionTestBypass ? '测试模式已允许直接批改。' : '家长已允许批改。';
      if (submitWhenAuthorizedRef.current) speak(`${accessMessage}正在批改刚才拍好的作业。`);
      else if (batchOpen && batchPagesRef.current.length)
        autoReply.ask('batch', `${accessMessage}作业页已拍好，全部拍完后请说拍完了。`);
      else autoReply.ask('correction_ready', `${accessMessage}要拍下当前作业，请说开始批改。`);
    }
    wasCorrectionAllowed.current = correctionAllowed;
  }, [correctionAllowed, correctionTestBypass, stage]);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const measure = () => {
      if (!video.videoWidth || !video.videoHeight) return;
      setCameraAspect(`${video.videoWidth} / ${video.videoHeight}`);
      setCameraPortrait(video.videoHeight > video.videoWidth);
    };
    video.addEventListener('loadedmetadata', measure);
    video.addEventListener('resize', measure);
    video.srcObject = stream;
    measure();
    return () => {
      video.removeEventListener('loadedmetadata', measure);
      video.removeEventListener('resize', measure);
    };
  }, [stream, stage]);
  useEffect(() => {
    setLive(Boolean(dashboard.live));
  }, [dashboard.live?.id]);
  useEffect(() => {
    resetTeachingContext();
    // Restore the saved board only when its session/task changes. A dashboard
    // refresh must not resurrect old paper annotations or restart speech.
    setTeachingDraft(latestTeachingDraft(dashboard.hints, sessionId, selectedTask));
    if (!sessionId) return;
    const latest = (dashboard.hints || []).find(
      (item) => item.session_id === sessionId && (item.task_id || '') === selectedTask,
    );
    if (!latest) return;
    lastHintPrompt.current = latest.question;
    lastHintSpokenReference.current = Boolean(parseLocatedQuestionReference(latest.question));
    lastHintKey.current = `${selectedTask}:${parseLocatedQuestionReference(latest.question) || latest.question}:[]`;
    setHintLevel(teachingTier(latest.level));
  }, [selectedTask, sessionId]);
  useEffect(() => {
    for (const task of dashboard.tasks)
      if (task.status === 'confirmed' && !confirmedBefore.current.has(task.id)) {
        confirmedBefore.current.add(task.id);
        speak(`${task.title}，家长已经确认完成。`);
      }
  }, [dashboard.tasks]);
  useEffect(() => {
    if (stage === 'active' && !stream)
      beginCamera().catch((e) => {
        if (e.name === 'AbortError') return;
        onError(e);
        setStage('paused');
        patch(`/v1/sessions/${sessionId}`, { status: 'paused' }).catch(() => {});
      });
  }, []);
  useEffect(() => {
    if (stage !== 'active') return;
    let cancelled = false;
    (async () => {
      try {
        const ice = await api('/v1/ice');
        const socket = await connectRealtime(async (msg) => {
          if (msg.type === 'live_started') {
            setLive(true);
            speak('家长正在查看纸面');
            onNotice('家长已打开实时纸面');
          }
          if (msg.type === 'live_ended') {
            setLive(false);
            setVoiceCall(false);
            parentVoiceRequested.current = false;
            callStream.current?.getTracks().forEach((t) => t.stop());
            callStream.current = null;
            setAnnotation([]);
            peerRef.current?.close();
            peerRef.current = null;
            speak('家长已结束查看');
          }
          if (msg.type === 'history_deleted') {
            timer.clear();
            stopCamera();
            setStage('home');
            setSessionId('');
            setLive(false);
            speak('家长已清除作业记录，本次会话结束。');
            refresh();
          }
          if (
            msg.type === 'help_preferences_changed' ||
            msg.type === 'audio_transcription_changed' ||
            msg.type === 'voice_preferences_changed'
          )
            refresh();
          if (msg.type === 'ai_preferences_changed') {
            refresh();
            capture(false, true, null, 1800)
              .then((pageImage) => {
                const prepForm = new FormData();
                prepForm.append('image', pageImage, 'paper.jpg');
                return api(`/v1/sessions/${sessionId}/page-prep`, {
                  method: 'POST',
                  body: prepForm,
                });
              })
              .catch(() => {});
          }
          if (msg.type === 'annotation') {
            setAnnotation(msg.points || []);
            speak('家长画了一个圈');
          }
          if (msg.type === 'clear_annotation') setAnnotation([]);
          if (msg.type === 'voice_on') parentVoiceRequested.current = true;
          if (msg.type === 'offer' && msg.request) {
            if (!currentStream.current) return;
            peerRef.current?.close();
            const peer = new RTCPeerConnection({ iceServers: ice.iceServers });
            peerRef.current = peer;
            currentStream.current
              .getVideoTracks()
              .forEach((track) => peer.addTrack(track, currentStream.current));
            peer.onicecandidate = (e) => {
              if (e.candidate && socket.readyState === 1)
                socket.send(JSON.stringify({ type: 'ice', candidate: e.candidate }));
            };
            peer.ontrack = (e) => {
              if (e.track.kind === 'audio') {
                const audio = document.querySelector('#parent-audio');
                if (audio) audio.srcObject = e.streams[0];
              }
            };
            const offer = await peer.createOffer();
            await peer.setLocalDescription(offer);
            socket.send(JSON.stringify({ type: 'offer', sdp: peer.localDescription }));
            setLive(true);
          }
          if (msg.type === 'offer' && msg.sdp) {
            await peerRef.current?.setRemoteDescription(msg.sdp);
            if (parentVoiceRequested.current && !callStream.current) {
              try {
                const mic = await navigator.mediaDevices.getUserMedia({
                  audio: { echoCancellation: true },
                });
                callStream.current = mic;
                mic.getAudioTracks().forEach((track) => peerRef.current?.addTrack(track, mic));
                setVoiceCall(true);
                speak('家长已开启语音通话');
              } catch {
                onNotice('麦克风不可用，仍可听家长说话');
              }
            }
            const answer = await peerRef.current?.createAnswer();
            await peerRef.current?.setLocalDescription(answer);
            socket.send(JSON.stringify({ type: 'answer', sdp: peerRef.current?.localDescription }));
          }
          if (msg.type === 'voice_off') {
            parentVoiceRequested.current = false;
            callStream.current?.getTracks().forEach((t) => t.stop());
            callStream.current = null;
            peerRef.current
              ?.getSenders()
              .filter((sender) => sender.track?.kind === 'audio')
              .forEach((sender) => peerRef.current?.removeTrack(sender));
            setVoiceCall(false);
          }
          if (msg.type === 'answer') await peerRef.current?.setRemoteDescription(msg.sdp);
          if (msg.type === 'ice') {
            try {
              await peerRef.current?.addIceCandidate(msg.candidate);
            } catch {
              /* peer reset */
            }
          }
        });
        if (cancelled) socket.close();
        else {
          socketRef.current = socket;
          socket.onopen = () => {
            if (dashboard.live) {
              setLive(true);
              speak('家长正在查看纸面');
            }
          };
        }
      } catch (e) {
        onError(e);
      }
    })();
    return () => {
      cancelled = true;
      socketRef.current?.close();
      peerRef.current?.close();
      peerRef.current = null;
      callStream.current?.getTracks().forEach((t) => t.stop());
      callStream.current = null;
    };
  }, [stage]);
  const beginCamera = async () => {
    cameraViewRevision.current++;
    expectedCameraDimensions.current = null;
    cameraOpenRef.current?.abort();
    cameraSourceRef.current?.stop();
    currentStream.current?.getTracks().forEach((track) => track.stop());
    cameraSourceRef.current = null;
    currentStream.current = null;
    setStream(null);
    setCameraPreviewImage('');
    const controller = new AbortController();
    cameraOpenRef.current = controller;
    setCameraStarting(true);
    try {
      const source = await openCameraSource({
        preference: cameraPreference,
        rotation: cameraAngle,
        signal: controller.signal,
        onUnavailable(error) {
          if (cameraOpenRef.current !== controller) return;
          const wasActive = cameraStageRef.current === 'active';
          const returnStage =
            cameraStageRef.current === 'preview'
              ? previewReturnStage.current
              : sessionId
                ? 'paused'
                : 'home';
          stopCamera();
          clearBatchPages();
          setReady(false);
          setSetupTicket('');
          setStage(returnStage);
          setLive(false);
          if (wasActive) {
            timer.stop().catch(() => {});
            patch(`/v1/sessions/${sessionId}`, { status: 'paused' }).then(refresh).catch(onError);
          }
          onError(error);
        },
      });
      if (controller.signal.aborted || cameraOpenRef.current !== controller) {
        source.stop();
        throw new DOMException('相机已关闭', 'AbortError');
      }
      cameraSourceRef.current = source;
      currentStream.current = source.stream;
      const camera = source.stream.getVideoTracks()[0]?.getSettings();
      if (camera?.width && camera?.height) setCameraAspect(`${camera.width} / ${camera.height}`);
      setCameraKind(source.kind);
      const track = source.stream.getVideoTracks()[0];
      for (const sender of peerRef.current?.getSenders() || []) {
        if (sender.track?.kind === 'video') await sender.replaceTrack(track);
      }
      if (controller.signal.aborted || cameraOpenRef.current !== controller) {
        source.stop();
        throw new DOMException('相机已关闭', 'AbortError');
      }
      setStream(source.stream);
      return source.stream;
    } catch (error) {
      if (cameraOpenRef.current === controller) {
        cameraSourceRef.current?.stop();
        cameraSourceRef.current = null;
        currentStream.current = null;
        setCameraKind('');
      }
      throw error;
    } finally {
      if (cameraOpenRef.current === controller) setCameraStarting(false);
    }
  };
  const stopCamera = () => {
    cameraViewRevision.current++;
    expectedCameraDimensions.current = null;
    cameraOpenRef.current?.abort();
    cameraOpenRef.current = null;
    cameraSourceRef.current?.stop();
    cameraSourceRef.current = null;
    setCameraStarting(false);
    setCameraKind('');
    setCameraPreviewImage('');
    resetTeachingContext();
    cameraHealthRef.current = { darkSamples: 0, restarts: 0, recovering: false };
    setupCheckSequence.current++;
    clearSetupIssue();
    autoReply.cancel();
    pendingHelpOffer.current = null;
    clearTimeout(helpOfferExpiryRef.current);
    stallWatchRef.current = createStallWatch();
    stallReferenceRef.current = null;
    setHintTarget(null);
    setupWatchRef.current = { frame: null, moving: false, stable: 0, lastCheck: 0 };
    currentStream.current?.getTracks().forEach((t) => t.stop());
    currentStream.current = null;
    callStream.current?.getTracks().forEach((t) => t.stop());
    callStream.current = null;
    setVoiceCall(false);
    setStream(null);
    previousCrop.current = null;
    lastFrame.current = null;
    clearInterval(loopRef.current);
    peerRef.current?.close();
    peerRef.current = null;
  };
  useEffect(() => {
    if (!stream || !['preview', 'setup', 'active'].includes(stage) || hintTarget?.imageUrl) return;
    const interval = setInterval(async () => {
      if (document.visibilityState !== 'visible' || !videoRef.current?.videoWidth) return;
      const health = cameraHealthRef.current;
      if (!isBlackCameraFrame(videoRef.current)) {
        health.darkSamples = 0;
        health.restarts = 0;
        return;
      }
      health.darkSamples++;
      if (health.darkSamples < 3 || health.recovering || health.restarts >= 2) return;
      health.darkSamples = 0;
      health.restarts++;
      health.recovering = true;
      try {
        currentStream.current?.getTracks().forEach((track) => track.stop());
        currentStream.current = null;
        setStream(null);
        const media = await beginCamera();
        if (videoRef.current) {
          videoRef.current.srcObject = media;
          await videoRef.current.play().catch(() => {});
        }
      } catch {
        onNotice('相机画面全黑，请检查镜头；可以暂停后继续作业以重启相机');
      } finally {
        health.recovering = false;
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [stream, stage, hintTarget?.imageUrl]);
  const capture = async (crop = false, useSelection = true, region = null, maxWidth = 1200) => {
    const viewRevision = cameraViewRevision.current;
    const source = cameraSourceRef.current;
    const video = videoRef.current;
    if (cameraRotatingRef.current || expectedCameraDimensions.current)
      throw new Error('正在调整相机方向，请稍后拍摄');
    if (!source || video?.srcObject !== source.stream) throw new Error('相机已关闭，请重新打开');
    source.assertFresh();
    if (!video?.videoWidth) throw new Error('相机画面尚未准备好');
    if (isBlackCameraFrame(video)) throw new Error('相机画面全黑，请检查镜头并等待相机恢复');
    const [x, y, w, h] =
      region ||
      (crop
        ? useSelection && selection && selection[2] > 0.05 && selection[3] > 0.05
          ? selection
          : [0.14, 0.14, 0.72, 0.72]
        : [0, 0, 1, 1]);
    const sourceWidth = video.videoWidth * w;
    const sourceHeight = video.videoHeight * h;
    const canvas = document.createElement('canvas');
    const scale = Math.min(1, maxWidth / sourceWidth);
    canvas.width = Math.max(1, Math.round(sourceWidth * scale));
    canvas.height = Math.max(1, Math.round(sourceHeight * scale));
    const ctx = canvas.getContext('2d');
    ctx.drawImage(
      video,
      video.videoWidth * x,
      video.videoHeight * y,
      sourceWidth,
      sourceHeight,
      0,
      0,
      canvas.width,
      canvas.height,
    );
    const encode = (quality) =>
      new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
    let blob = await encode(0.8);
    if (blob?.size > 1_900_000) blob = await encode(0.62);
    if (!blob || blob.size > 1_900_000) throw new Error('截图过大，请调整纸面后重试');
    if (cameraSourceRef.current !== source || cameraViewRevision.current !== viewRevision)
      throw new Error('相机画面已调整，请重新拍摄');
    source.assertFresh();
    return blob;
  };
  const rotateCamera = async () => {
    if (
      cameraStarting ||
      cameraRotatingRef.current ||
      startingSessionRef.current ||
      !canRotateCamera({
        stage: cameraStageRef.current,
        preference: cameraPreference,
        kind: cameraKind,
        hasBridge: Boolean(window.PaperCamera),
      })
    )
      return;
    const angle = (cameraAngle + 90) % 360;
    const source = cameraSourceRef.current;
    if (!source) {
      setCameraAngle(angle);
      save('camera_rotation', String(angle));
      return;
    }
    cameraRotatingRef.current = true;
    setCameraRotating(true);
    cameraViewRevision.current++;
    setupCheckSequence.current++;
    setReady(false);
    setSetupTicket('');
    setChecking(false);
    setSessionStartError(false);
    clearSetupIssue();
    autoReply.cancel();
    setCameraPreviewImage('');
    setSelection(null);
    setSelectionStrokes([]);
    previousCrop.current = null;
    lastFrame.current = null;
    setupWatchRef.current = { frame: null, moving: false, stable: 0, lastCheck: Date.now() };
    setAnalysis('');
    try {
      const dimensions = source.setRotation(angle);
      expectedCameraDimensions.current = dimensions;
      setCameraAngle(dimensions.rotation);
      save('camera_rotation', String(dimensions.rotation));
      await waitForCameraDimensions(videoRef.current, dimensions.width, dimensions.height, {
        signal: cameraOpenRef.current?.signal,
      });
      if (cameraSourceRef.current !== source) return;
      expectedCameraDimensions.current = null;
      setCameraAspect(`${dimensions.width} / ${dimensions.height}`);
      setCameraPortrait(dimensions.height > dimensions.width);
      if (cameraStageRef.current === 'setup') setAnalysis('画面方向已调整，请重新检查纸面。');
    } catch (error) {
      if (error.name !== 'AbortError') onError(error);
    } finally {
      cameraRotatingRef.current = false;
      setCameraRotating(false);
    }
  };
  const previewCamera = async () => {
    previewReturnStage.current = stage;
    setStage('preview');
    try {
      await beginCamera();
    } catch (error) {
      if (error.name === 'AbortError') return;
      setStage(previewReturnStage.current);
      onError(error);
    }
  };
  const previewCameraPhoto = async () => {
    try {
      const blob = await capture(false, false, null, 2560);
      setCameraPreviewImage(URL.createObjectURL(blob));
    } catch (error) {
      onError(error);
    }
  };
  const setup = async () => {
    if (stage === 'home' || stage === 'done') {
      goalTouched.current = false;
      goalNotice.current = '';
      setTimerGoal(dashboard.timer_suggestion?.goal_seconds ?? null);
      setTimerSubject(null);
    }
    setCameraStarting(true);
    setStage('setup');
    setReady(false);
    try {
      await beginCamera();
      autoReply.ask('setup_camera', '请把作业本放进取景框，确保文字清晰。摆好后说检查纸面。');
    } catch (e) {
      if (e.name === 'AbortError') return;
      onError(e);
      setStage('home');
    } finally {
      setCameraStarting(false);
    }
  };
  const acceptDetectedSubject = (label, confidence, immediate = false) => {
    const candidate = String(label || '').trim();
    if (!candidate || confidence < 0.8) return;
    const chosenTask = dashboard.tasks.find((task) => task.id === selectedTask);
    if (chosenTask?.subject && chosenTask.subject !== candidate) {
      const candidateKey = `${selectedTask}:${candidate}`;
      if (promptedSubjectKey.current === candidateKey) return;
      promptedSubjectKey.current = candidateKey;
      pendingSubject.current = candidate;
      autoReply.ask(
        'timer_subject',
        `纸面看起来是${candidate}，要把计时切换到这个科目吗？可以说切换或保持原科目。`,
      );
      return;
    }
    if (!timerSubject || timerSubject === candidate || immediate) {
      setTimerSubject(candidate);
      subjectCandidate.current = { label: '', count: 0 };
      return;
    }
    const count =
      subjectCandidate.current.label === candidate ? subjectCandidate.current.count + 1 : 1;
    subjectCandidate.current = { label: candidate, count };
    if (count >= 2) {
      setTimerSubject(candidate);
      subjectCandidate.current = { label: '', count: 0 };
      onNotice(`已按${candidate}记录接下来的学习时间`);
    }
  };
  const check = async () => {
    const checkSequence = ++setupCheckSequence.current;
    const startedAt = performance.now();
    let capturedAt = startedAt;
    let requestedAt = startedAt;
    let respondedAt = null;
    let awaitingRemote = true;
    setSessionStartError(false);
    clearSetupIssue();
    setupWatchRef.current.lastCheck = Date.now();
    setChecking(true);
    try {
      const form = new FormData();
      const image = await capture();
      if (checkSequence !== setupCheckSequence.current || cameraRotatingRef.current) return;
      capturedAt = performance.now();
      const quality = measurePaperFrame(videoRef.current);
      if (quality.issue) {
        const reason =
          quality.issue === 'dark'
            ? '镜头画面太暗，请检查光线或移开遮挡。'
            : '镜头画面几乎全白，请调整纸面和光线。';
        setAnalysis(reason);
        autoReply.ask('setup_camera', `${reason}调整后说检查纸面，画面变化稳定后也会自动复查。`);
        return;
      }
      if (quality.maybeBlurred) setAnalysis('正在核对纸面；请保持镜头和纸张稳定。');
      recognizePaperText(image).then((local) => {
        if (local)
          console.info('paper-check local-ocr', {
            durationMs: local.durationMs,
            lineCount: local.boxes?.length || 0,
            available: !local.error,
          });
        if (
          awaitingRemote &&
          checkSequence === setupCheckSequence.current &&
          stage === 'setup' &&
          local?.boxes?.length
        )
          setAnalysis('已定位纸面文字，正在确认题目内容是否可读…');
      });
      form.append('image', image, 'paper.jpg');
      requestedAt = performance.now();
      const result = await api('/v1/setup/check', { method: 'POST', body: form });
      respondedAt = performance.now();
      awaitingRemote = false;
      if (checkSequence !== setupCheckSequence.current || stage !== 'setup') return;
      setReady(result.ready);
      setSetupTicket(result.setup_ticket || '');
      if (result.ready) acceptDetectedSubject(result.subject, result.subject_confidence, !resuming);
      const reason = (result.reason || '请重新摆放作业页').replace(/^未准备好[：:]\s*/, '');
      setAnalysis(result.ready ? `纸面内容可读，可以开始。${reason}` : `请调整纸面：${reason}`);
      if (result.ready) {
        autoReply.cancel();
        await start(result.setup_ticket, image);
      } else {
        if (result.issue_box) {
          const issue = { imageUrl: URL.createObjectURL(image), box: result.issue_box };
          setupIssueRef.current = issue;
          setSetupIssue(issue);
        }
        autoReply.ask(
          'setup_camera',
          `请调整纸面：${reason}。画面稳定后会自动复查，也可以说检查纸面。`,
          false,
          '',
          () => {
            if (checkSequence === setupCheckSequence.current) clearSetupIssue();
          },
        );
      }
    } catch (e) {
      if (checkSequence === setupCheckSequence.current) onError(e);
    } finally {
      awaitingRemote = false;
      const finishedAt = respondedAt || performance.now();
      console.info('paper-check timing', {
        captureMs: Math.round(capturedAt - startedAt),
        preflightMs: Math.round(requestedAt - capturedAt),
        requestMs: Math.round(finishedAt - requestedAt),
        totalMs: Math.round(finishedAt - startedAt),
      });
      if (checkSequence === setupCheckSequence.current) setChecking(false);
    }
  };
  const start = async (ticket = setupTicket, checkedImage = null) => {
    if (!ticket || startingSessionRef.current) return;
    const startedAt = performance.now();
    startingSessionRef.current = true;
    try {
      setupCheckSequence.current++;
      clearSetupIssue();
      const result = resuming
        ? await patch(`/v1/sessions/${sessionId}`, { status: 'active', setup_ticket: ticket })
        : await post('/v1/sessions', { setup_ticket: ticket, timer_goal_seconds: timerGoal });
      const sessionRequestMs = Math.round(performance.now() - startedAt);
      stallWatchRef.current = createStallWatch();
      stallReferenceRef.current = null;
      if (!resuming) offeredQuestionsRef.current = new Set();
      setSessionId(result.id);
      if (!resuming) {
        setCorrection(null);
        setCorrectionOpen(false);
        replaceCorrectionSnapshot('');
        clearBatchPages();
      }
      previousCrop.current = await capture(true, false).catch(() => null);
      setAnalysis('');
      setQuestionInputOpen(false);
      setStage('active');
      console.info('paper-check entered', {
        sessionRequestMs,
        enterMs: Math.round(performance.now() - startedAt),
      });
      setSessionStartError(false);
      setResuming(false);
      if (checkedImage) {
        const prepForm = new FormData();
        prepForm.append('image', checkedImage, 'paper.jpg');
        api(`/v1/sessions/${result.id}/page-prep`, { method: 'POST', body: prepForm }).catch(
          () => {},
        );
      }
      await refresh();
      if (pendingSubmissionTaskId.current) {
        const taskId = pendingSubmissionTaskId.current;
        pendingSubmissionTaskId.current = '';
        setSelectedTask(taskId);
        setBatchPurpose('submission');
        setSubmissionTaskId(taskId);
        submissionRequestId.current = crypto.randomUUID();
        openBatch();
      } else speak(resuming ? '继续作业' : '开始作业啦。需要帮忙时可以按住说话。');
    } catch (e) {
      setSessionStartError(true);
      onError(e);
    } finally {
      startingSessionRef.current = false;
      setChecking(false);
    }
  };
  const setSessionHelpEnabled = async (enabled) => {
    if (!sessionId || helpSettingBusy) return;
    if (enabled && parentHelpMode === 'off') {
      onNotice('家长已关闭自动提醒，你仍可随时主动求助');
      return;
    }
    setHelpSettingBusy(true);
    try {
      await patch(`/v1/sessions/${sessionId}/help-preferences`, { enabled });
      await refresh();
      onNotice(
        enabled ? '本次作业已开启自动提醒；重新开始观察' : '本次作业已关闭自动提醒；仍可主动求助',
      );
    } catch (e) {
      onError(e);
    } finally {
      setHelpSettingBusy(false);
    }
  };
  useEffect(() => {
    if (stage !== 'setup' || !stream || checking || ready) return;
    const timer = setInterval(() => {
      const video = videoRef.current;
      if (!video?.videoWidth) return;
      const canvas = document.createElement('canvas');
      canvas.width = 32;
      canvas.height = 24;
      const context = canvas.getContext('2d', { willReadFrequently: true });
      context.drawImage(video, 0, 0, 32, 24);
      const frame = context.getImageData(0, 0, 32, 24).data;
      const watch = setupWatchRef.current;
      if (watch.frame) {
        let changed = 0;
        for (let i = 0; i < frame.length; i += 4)
          if (Math.abs(frame[i] - watch.frame[i]) > 25) changed++;
        // The worksheet may occupy only a small part of the camera frame.
        // Uncovering one row must still trigger a fresh paper check.
        if (changed / (32 * 24) > 0.02) {
          watch.moving = true;
          watch.stable = 0;
        } else if (watch.moving && ++watch.stable >= 3 && Date.now() - watch.lastCheck > 15000) {
          watch.moving = false;
          watch.lastCheck = Date.now();
          onNotice('纸面位置已稳定，正在自动复查');
          check();
        }
      }
      watch.frame = new Uint8ClampedArray(frame);
    }, 1000);
    return () => clearInterval(timer);
  }, [stage, stream, checking, ready]);
  useEffect(() => {
    if (stage !== 'active' || !sessionId || !stream) return;
    let pendingChange = false,
      stableSamples = 0,
      lastUpload = 0;
    loopRef.current = setInterval(async () => {
      if (
        busyAnalysis.current ||
        batchOpen ||
        batchSubmitting ||
        hintBusy ||
        voiceCall ||
        !videoRef.current?.videoWidth
      )
        return;
      const canvas = document.createElement('canvas');
      canvas.width = 64;
      canvas.height = 48;
      const context = canvas.getContext('2d', { willReadFrequently: true });
      context.drawImage(videoRef.current, 0, 0, 64, 48);
      const frame = context.getImageData(0, 0, 64, 48).data;
      if (!lastFrame.current && !previousCrop.current) {
        previousCrop.current = await capture(true, false).catch(() => null);
      }
      if (lastFrame.current) {
        let changedPixels = 0;
        for (let i = 0; i < frame.length; i += 4)
          if (Math.abs(frame[i] - lastFrame.current[i]) > 20) changedPixels++;
        const fraction = changedPixels / (64 * 48);
        if (fraction > 0.006) {
          pendingChange = true;
          stableSamples = 0;
        } else {
          if (pendingChange && ++stableSamples >= 2 && Date.now() - lastUpload > 30000) {
            pendingChange = false;
            stableSamples = 0;
            lastUpload = Date.now();
            busyAnalysis.current = true;
            try {
              const snapshot = lastHintSnapshot.current;
              const shouldCheckAttempt = shouldProbeHintAttempt(
                snapshot,
                Date.now(),
                Boolean(autoReply.status || correctionOpen),
              );
              const attemptResult = shouldCheckAttempt
                ? await checkHintAttemptRef.current?.({ silent: true })
                : null;
              if (!attemptResult) {
                const form = new FormData();
                const crop = await capture(true, false);
                const pageImage = await capture(false, true, null, 1800);
                form.append('image', crop, 'change.jpg');
                if (previousCrop.current)
                  form.append('previous_image', previousCrop.current, 'before.jpg');
                form.append('kind', 'paper_change');
                if (selectedTask) form.append('task_id', selectedTask);
                const prepForm = new FormData();
                prepForm.append('image', pageImage, 'paper.jpg');
                // Preparation must start while the slower observation is still running.
                api(`/v1/sessions/${sessionId}/page-prep`, {
                  method: 'POST',
                  body: prepForm,
                }).catch(() => {});
                const result = await api(`/v1/sessions/${sessionId}/observations`, {
                  method: 'POST',
                  body: form,
                });
                previousCrop.current = crop;
                setAnalysis(result.label);
                acceptDetectedSubject(result.subject, result.subject_confidence);
              } else {
                previousCrop.current = await capture(true, false).catch(() => previousCrop.current);
              }
            } catch {
              setAnalysis('分析暂不可用，仍可继续作业');
            } finally {
              busyAnalysis.current = false;
            }
          }
        }
      }
      lastFrame.current = new Uint8ClampedArray(frame);
    }, 5000);
    return () => clearInterval(loopRef.current);
  }, [
    stage,
    sessionId,
    stream,
    selectedTask,
    timerSubject,
    batchOpen,
    batchSubmitting,
    hintBusy,
    voiceCall,
    autoReply.status,
    correctionOpen,
  ]);
  const teachingDraftVisible = hintTarget?.kind === 'teaching';
  useEffect(() => {
    if (stage !== 'active' || !sessionId || !stream || !proactive) return;
    let cancelled = false;
    let captureBusy = false;
    const canvas = document.createElement('canvas');
    canvas.width = 96;
    canvas.height = 72;
    const interval = setInterval(async () => {
      const video = videoRef.current;
      if (
        captureBusy ||
        !video?.videoWidth ||
        document.visibilityState === 'hidden' ||
        batchOpen ||
        batchSubmitting ||
        hintBusy ||
        voiceCall ||
        correctionOpen ||
        autoReply.status ||
        busyAnalysis.current
      )
        return;
      const context = canvas.getContext('2d', { willReadFrequently: true });
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const frame = context.getImageData(0, 0, canvas.width, canvas.height).data;
      const watch = stallWatchRef.current;
      const priorVersion = watch.version;
      const result = sampleStallWatch(
        watch,
        frame,
        canvas.width,
        canvas.height,
        Date.now(),
        parentHelpMode,
      );
      if (
        result.pageChanged ||
        result.progress ||
        (pendingHelpOffer.current?.id && watch.version !== priorVersion)
      ) {
        if (pendingHelpOffer.current) clearHelpOffer(true);
        if (result.pageChanged) stallReferenceRef.current = null;
      }
      if (result.pageChanged) {
        resetTeachingContext();
        return;
      }
      if (pendingHelpOffer.current) return;
      if (result.referenceRefresh || !stallReferenceRef.current) {
        captureBusy = true;
        try {
          const reference = await capture(false, false, null, 1600);
          if (!cancelled) {
            stallReferenceRef.current = reference;
            if (result.referenceRefresh) {
              const prepForm = new FormData();
              prepForm.append('image', reference, 'paper.jpg');
              api(`/v1/sessions/${sessionId}/page-prep`, {
                method: 'POST',
                body: prepForm,
              }).catch(() => {});
            }
          }
        } catch {
          // A missing frame is transient; the next stable sample retries.
        } finally {
          captureBusy = false;
        }
        return;
      }
      if (result.ambiguousPageReady) {
        captureBusy = true;
        let current = null;
        try {
          current = await capture(false, false, null, 1600);
          const form = new FormData();
          form.append('image', current, 'current.jpg');
          form.append('reference_image', stallReferenceRef.current, 'reference.jpg');
          const checkin = await api(`/v1/sessions/${sessionId}/idle-checkin`, {
            method: 'POST',
            body: form,
          });
          if (cancelled || stallWatchRef.current !== watch) return;
          const verdict = settleAmbiguousPage(watch, result.version, checkin, Date.now());
          if (verdict !== 'stale') stallReferenceRef.current = current;
          if (verdict === 'page_changed') resetTeachingContext();
        } catch {
          if (!cancelled && stallWatchRef.current === watch && current) {
            if (settleAmbiguousPage(watch, result.version, null, Date.now()) !== 'stale')
              stallReferenceRef.current = current;
          }
        } finally {
          captureBusy = false;
        }
        return;
      }
      if (teachingDraftVisible) return;
      if (result.checkinReady) {
        captureBusy = true;
        try {
          const current = await capture(false, false, null, 1600);
          const form = new FormData();
          form.append('image', current, 'current.jpg');
          form.append('reference_image', stallReferenceRef.current, 'reference.jpg');
          captureBusy = false;
          const checkin = await api(`/v1/sessions/${sessionId}/idle-checkin`, {
            method: 'POST',
            body: form,
          });
          if (cancelled || stallWatchRef.current !== watch) return;
          const verdict = settleIdleCheckin(watch, result.version, checkin, Date.now());
          if (verdict === 'rebaseline') stallReferenceRef.current = current;
          if (checkin.page_changed && verdict !== 'stale') resetTeachingContext();
          if (verdict !== 'offer' || pendingHelpOffer.current) return;
          pendingHelpOffer.current = {
            id: checkin.candidate_id || '',
            label: checkin.target_label || '',
            box: checkin.target_box || null,
          };
          if (checkin.target_box) {
            setHintTarget((visible) =>
              visible?.kind === 'teaching'
                ? visible
                : {
                    kind: 'offer',
                    box: checkin.target_box,
                    label: checkin.target_label,
                    imageUrl: URL.createObjectURL(current),
                    beats: [],
                  },
            );
          }
          autoReply.ask(
            'help_offer',
            checkin.target_box
              ? `我看到这页暂时没有新的书写。先看我圈出的${checkin.target_label}，要不要一起想想？`
              : '我看到这页暂时没有新的书写。需要我陪你想想当前题吗？可以说要提示，也可以说不用。',
          );
          clearTimeout(helpOfferExpiryRef.current);
          helpOfferExpiryRef.current = setTimeout(() => clearHelpOffer(true), 120_000);
        } catch {
          if (!cancelled && stallWatchRef.current === watch) {
            watch.probePending = false;
            watch.deferUntil = Date.now() + 60_000;
          }
        } finally {
          captureBusy = false;
        }
        return;
      }
      if (!result.ready) return;
      captureBusy = true;
      try {
        const form = new FormData();
        const currentFrame = await capture(false, false, null, 1600);
        form.append('image', currentFrame, 'current.jpg');
        form.append('reference_image', stallReferenceRef.current, 'reference.jpg');
        form.append('activity_box', JSON.stringify(result.activityBox));
        captureBusy = false;
        const candidate = await api(`/v1/sessions/${sessionId}/help-candidates`, {
          method: 'POST',
          body: form,
        });
        if (cancelled || stallWatchRef.current !== watch) return;
        const verdict = settleStallProbe(
          watch,
          result.version,
          candidate,
          parentHelpMode,
          Date.now(),
        );
        if (verdict !== 'offer' || pendingHelpOffer.current) return;
        const centerX = candidate.box[0] + candidate.box[2] / 2;
        const centerY = candidate.box[1] + candidate.box[3] / 2;
        const questionKey = `${watch.pageVersion}:${candidate.label}:${Math.round(centerX * 6)}:${Math.round(centerY * 6)}`;
        if (offeredQuestionsRef.current.has(questionKey)) return;
        offeredQuestionsRef.current.add(questionKey);
        pendingHelpOffer.current = {
          id: candidate.id,
          label: candidate.label,
          box: candidate.box,
          questionKey,
        };
        setHintTarget((visible) =>
          visible?.kind === 'teaching'
            ? visible
            : {
                kind: 'offer',
                box: candidate.box,
                label: candidate.label,
                imageUrl: URL.createObjectURL(currentFrame),
                beats: [],
              },
        );
        autoReply.ask('help_offer', `我看到你在看${candidate.label}，要不要一起找第一步思路？`);
        clearTimeout(helpOfferExpiryRef.current);
        helpOfferExpiryRef.current = setTimeout(() => clearHelpOffer(true), 120_000);
      } catch {
        if (!cancelled && stallWatchRef.current === watch) {
          watch.probePending = false;
          watch.deferUntil = Date.now() + 60_000;
        }
      } finally {
        captureBusy = false;
      }
    }, 2000);
    return () => {
      cancelled = true;
      clearInterval(interval);
      const watch = stallWatchRef.current;
      if (watch.probePending) {
        watch.probePending = false;
        watch.deferUntil = Date.now() + 60_000;
      }
    };
  }, [
    stage,
    sessionId,
    stream,
    proactive,
    parentHelpMode,
    batchOpen,
    batchSubmitting,
    hintBusy,
    voiceCall,
    correctionOpen,
    autoReply.status,
    teachingDraftVisible,
  ]);
  const canPresentTeachingPaper = (imageUrl) => {
    const process = document.querySelector('.teaching-paper-process');
    return Boolean(
      imageUrl && teachingPaperVisibleRef.current === imageUrl && (!process || process.open),
    );
  };
  const presentTeachingPrefix = (beats, imageUrl) => {
    const paperVisible = canPresentTeachingPaper(imageUrl);
    return {
      step: teachingPaperPresentation(beats, { paperVisible }).at(-1),
      paperVisible,
    };
  };
  const newTeachingPlayback = (isCurrent, reveal, rate = 1, presentStep = (step) => ({ step })) => {
    let utteranceGeneration = 0;
    return createTeachingPlayback({
      rate,
      onState: (state) => {
        if (isCurrent()) setTeachingPlayback(state);
      },
      play: async (step, { index, repeat, rate, signal }) => {
        if (!isCurrent() || signal.aborted) return false;
        const presentation = presentStep(step, index);
        const presented = presentation.step || step;
        const generation = ++utteranceGeneration;
        const current = () => isCurrent() && !signal.aborted && generation === utteranceGeneration;
        let started = false;
        let resolveInk;
        const ink = new Promise((resolve) => (resolveInk = resolve));
        const startInk = () => {
          if (started || !current()) return;
          started = true;
          reveal(step, index, repeat, presentation.paperVisible);
          resolveInk();
        };
        const abort = () => {
          if (isCurrent()) stopSpeechOutput();
          resolveInk();
        };
        signal.addEventListener('abort', abort, { once: true });
        const prepared = prepareSpeech(presented.speech, signal, { rate });
        try {
          const spoken = speakAndWait(presented.speech, [], presented.speech, prepared, startInk, {
            rate,
          });
          const duration = window.matchMedia('(prefers-reduced-motion: reduce)').matches
            ? 0
            : teachingBeatDuration(presented);
          const drawing = ink.then(() => teachingPause(duration, signal));
          const played = await spoken;
          if (played && current()) startInk();
          else if (!started) resolveInk();
          await drawing;
          return played;
        } finally {
          signal.removeEventListener('abort', abort);
          prepared?.cancel();
        }
      },
    });
  };
  const teachingPlaybackCommand = (command) => {
    autoReply.cancel();
    if (command === 'next' && historyNavigation) {
      const steps = logicalTeachingSteps(draftTarget?.beats || hintTarget?.beats || []);
      const current = teachingStepIndex(steps, reviewBeat);
      if (current + 1 < steps.length) return reviewTeachingStep(current + 1);
      if (teachingMode === 'review' && !teachingPlaybackRef.current) return;
      setReviewBeat(null);
      setSilentReview(false);
    }
    if (['replay', 'slower'].includes(command) && historyNavigation) {
      if (teachingMode === 'review' || !teachingPlaybackRef.current)
        return replayTeaching(progressIndex, command === 'slower');
      if (command === 'slower') teachingPlaybackRef.current.command('slower');
      teachingPlaybackRef.current.review(reviewBeat);
      return;
    }
    if (command === 'next' && teachingPlayback?.phase === 'complete') exitTeaching();
    else teachingPlaybackRef.current?.command(command);
  };
  const reviewTeachingStep = (index) => {
    const beats = draftTarget?.beats || hintTarget?.beats || [];
    const groups = logicalTeachingSteps(beats);
    const group = groups[Math.max(0, Math.min(groups.length - 1, index))];
    if (!group) return;
    teachingPlaybackRef.current?.interrupt();
    autoReply.cancel();
    stopSpeechOutput();
    setReviewBeat(group.end);
    setSilentReview(true);
    const paperVisible = canPresentTeachingPaper(hintTarget?.imageUrl);
    setHintTarget((target) => target && { ...target, paperPresentationVisible: paperVisible });
    const text = teachingPaperPresentation(beats.slice(0, group.end + 1), { paperVisible })
      .slice(group.start, group.end + 1)
      .map((beat) => beat.speech || '')
      .join('');
    window.dispatchEvent(
      new CustomEvent('paper-speech', { detail: { text, spoken: text, choices: [] } }),
    );
  };
  const handleTeachingPlaybackVoice = (text) => {
    if (stage !== 'active' || teachingMode === 'idle' || !teachingPlaybackRef.current) return false;
    const words = text.trim().replace(/[。！!，,？?]/g, '');
    if (/^(下一步|继续|继续讲|接着讲|往下讲|继续下一步)$/.test(words)) {
      if (teachingPlaybackRef.current.waiting() || teachingPlayback?.phase === 'reading')
        teachingPlaybackCommand('next');
      return true;
    }
    if (/^(等一下|停一下|先停一下|等等|先别讲)$/.test(words)) {
      teachingPlaybackRef.current.interrupt();
      return true;
    }
    if (/^(慢一点|再慢一点|说慢一点|太快了|讲得太快了)$/.test(words)) {
      teachingPlaybackCommand('slower');
      return true;
    }
    if (/^(再讲这步|再讲一遍|再说一遍|重讲这一步|这步没跟上)$/.test(words)) {
      teachingPlaybackCommand('replay');
      return true;
    }
    return false;
  };
  const requestHint = async (
    asked,
    studentThought = '',
    candidateId = '',
    markedStrokes = null,
    markedRevision = null,
    spokenReference = false,
    advance = false,
  ) => {
    const completeCandidate = Boolean(
      candidateId && asksForCompleteTeaching(typeof asked === 'string' ? asked : question),
    );
    if (completeCandidate) {
      const candidateLabel =
        pendingHelpOffer.current?.id === candidateId ? pendingHelpOffer.current.label : '';
      asked = `${candidateLabel ? `${candidateLabel}。` : ''}${typeof asked === 'string' ? asked : question}`;
      candidateId = '';
    }
    if (handleTeachingPlaybackVoice(typeof asked === 'string' ? asked : question)) return;
    if (respondLearningSupport(parseLearningSupport(typeof asked === 'string' ? asked : question)))
      return;
    const scheduler = penHintSchedulerRef.current;
    if (markedRevision !== null && questionInputOpen) return;
    if (markedRevision !== null && scheduler.revision() !== markedRevision) return;
    if (hintBusy && teachingPlaybackRef.current?.waiting()) exitTeaching();
    else if (hintBusy || attemptBusy) {
      if (markedStrokes && markedRevision === scheduler.revision()) scheduler.finish(markedStrokes);
      return;
    }
    if (markedRevision === null) scheduler.cancel();
    const prompt = (typeof asked === 'string' ? asked : question).trim();
    const activeStrokes = markedStrokes || selectionStrokes;
    const activeMark = Boolean(
      !candidateId &&
      !completeCandidate &&
      (markedStrokes || selection) &&
      penSelectionReady(activeStrokes),
    );
    const target =
      prompt || (lastHintSnapshot.current && lastHintPrompt.current) || READING_QUESTION;
    const reference = candidateId ? '' : parseLocatedQuestionReference(target);
    const plan = guidanceRequest({
      binding: questionGuidanceRef.current,
      sessionId,
      taskId: selectedTask,
      frameRevision: cameraViewRevision.current,
      prompt: target,
      reference,
      newSelection: activeMark,
      candidateId,
      advance,
      studentThought,
    });
    if (plan.replay) return replayTeaching();
    const frozen = plan.binding;
    teachingPlaybackRef.current?.cancel();
    const request = hintContextRef.current.begin();
    const markController = request.controller;
    markHintRequestRef.current = markController;
    let streamedSentences = 0;
    const streamedBeats = [];
    const pendingVisual = [];
    const playbackRun = ++teachingRunRef.current;
    const frameRevision = cameraViewRevision.current;
    const isCurrent = () =>
      request.current() &&
      teachingRunRef.current === playbackRun &&
      hintScopeRef.current?.stage === 'active' &&
      hintScopeRef.current.sessionId === sessionId &&
      hintScopeRef.current.taskId === selectedTask &&
      cameraViewRevision.current === frameRevision;
    const inputDraft = questionInputOpen ? question : null;
    let playback;
    try {
      autoReply.cancel();
      stopSpeechOutput();
      clearSpeechCaption();
      setRestOffer(false);
      setShowTeachingMarks(true);
      setTeachingMode('lesson');
      setHintPreparationLabel('正在核对题目并准备讲解…');
      setQuestionInputOpen(false);
      setReviewBeat(null);
      setSilentReview(false);
      clearHelpOffer(false);
      const uncertainSpokenNumber = Boolean(
        reference &&
        (spokenReference || (target === lastHintPrompt.current && lastHintSpokenReference.current)),
      );
      const manualSelection = activeMark && !reference;
      const inkStrokes = manualSelection
        ? activeStrokes.map((stroke) => stroke.map((point) => [...point]))
        : [];
      const selectionRevision = scheduler.revision();
      if (reference) {
        setSelection(null);
        setSelectionStrokes([]);
      }
      setHintBusy(true);
      setHintBusyTarget(reference || '');
      setHintTarget(null);
      if (!frozen) {
        lastTeachingSceneRef.current = null;
        saveQuestionGuidance(null);
      }
      const key = `${selectedTask}:${reference || target}:${JSON.stringify(
        inkStrokes.map((stroke) =>
          stroke.map(([x, y]) => [Math.round(x * 100) / 100, Math.round(y * 100) / 100]),
        ),
      )}`;
      const level = plan.level;
      const form = new FormData();
      form.append('session_id', sessionId);
      form.append('question', target);
      form.append('level', String(level));
      if (studentThought.trim()) form.append('student_thought', studentThought.trim());
      if ((frozen || lastHintKey.current === key) && hint) form.append('previous_hint', hint);
      if (frozen) form.append('previous_hint_id', frozen.hintId);
      if (reference) form.append('target', reference);
      if (uncertainSpokenNumber) form.append('spoken_reference', 'true');
      if (candidateId) form.append('candidate_id', candidateId);
      if (manualSelection) form.append('selected_strokes', JSON.stringify(inkStrokes));
      if (selectedTask) form.append('task_id', selectedTask);
      const hintFrame = frozen?.image || (await capture(false, true, null, 1800));
      if (!isCurrent() || (manualSelection && scheduler.revision() !== selectionRevision)) return;
      const localAnchors = frozen
        ? frozen.localAnchors
        : await recognizeHintAnchors(hintFrame, {
            signal: markController.signal,
            isCurrent,
          });
      if (!isCurrent() || (manualSelection && scheduler.revision() !== selectionRevision)) return;
      form.append('image', hintFrame, 'question.jpg');
      if (localAnchors) form.append('local_text_anchors', JSON.stringify(localAnchors));
      let hintImageUrl = null;
      const imageUrl = () => {
        if (!hintImageUrl) hintImageUrl = URL.createObjectURL(hintFrame);
        return hintImageUrl;
      };
      const shownSteps = new Set();
      const visibleSteps = [];
      const updateTarget = (update = {}) =>
        setHintTarget((current) =>
          mergeTeachingTarget(current, {
            ...update,
            imageUrl: imageUrl(),
            sceneKey: `live:${playbackRun}`,
            beats: [...visibleSteps],
          }),
        );
      updateTarget({
        box: frozen?.box,
        label: frozen?.label || reference,
        textAnchors: localAnchors?.anchors || [],
      });
      playback = newTeachingPlayback(
        isCurrent,
        (step, index, _repeat, paperVisible) => {
          const alreadyShown = shownSteps.has(index);
          shownSteps.add(index);
          if (!alreadyShown) visibleSteps.push(step);
          setReviewBeat(alreadyShown ? index : null);
          setSilentReview(false);
          setHintTarget((current) => ({
            ...mergeTeachingTarget(current, {
              imageUrl: imageUrl(),
              sceneKey: `live:${playbackRun}`,
              beats: [...visibleSteps],
            }),
            playbackVersion: (current?.playbackVersion || 0) + 1,
            paperPresentationVisible: paperVisible,
          }));
        },
        1,
        (step, index) => presentTeachingPrefix([...visibleSteps.slice(0, index), step], imageUrl()),
      );
      teachingPlaybackRef.current = playback;
      const result = await streamApi(
        '/v1/hints/stream',
        form,
        (event, data) => {
          if (!isCurrent()) return;
          if (event === 'status' && data.stage === 'checking_solution') {
            setHintPreparationLabel('正在核对解题过程…');
            return;
          }
          if (event === 'target' && data.box) {
            updateTarget({
              box: data.box,
              label: data.label || reference,
              cue: null,
            });
          }
          if (event === 'visual') {
            pendingVisual.push(data);
            return;
          }
          if (event === 'beat' && data.speech) {
            setHintPreparationLabel('');
            streamedBeats.push(data);
            streamedSentences++;
            playback.enqueue(
              splitTeachingBeat(data, { sourceBeatIndex: streamedBeats.length - 1 }),
            );
            return;
          }
          if (event !== 'sentence' || !data.text) return;
          setHintPreparationLabel('');
          streamedSentences++;
          playback.enqueue(
            splitTeachingBeat(
              {
                speech: data.text,
                page: pendingVisual.flatMap((visual) => visual.page || []),
                scratch: pendingVisual.flatMap((visual) => visual.scratch || []),
              },
              { sourceBeatIndex: streamedSentences - 1 },
            ),
          );
          pendingVisual.length = 0;
        },
        markController.signal,
      );
      if (!isCurrent() || (manualSelection && scheduler.revision() !== selectionRevision)) return;
      if (result.located === false) {
        playback.cancel();
        if (manualSelection) setSelectionConsumed(true);
        setHint('');
        setHintTarget(null);
        setTeachingMode('idle');
        lastHintSnapshot.current = null;
        onNotice(result.response);
        autoReply.ask('hint_target', result.response);
        return;
      }
      lastHintKey.current = key;
      if (manualSelection) {
        setSelectionConsumed(true);
        setSelection(null);
        setSelectionStrokes([]);
      }
      lastHintPrompt.current = target;
      lastHintSpokenReference.current = uncertainSpokenNumber;
      lastHintSnapshot.current =
        result.answer_assessment === 'correct'
          ? null
          : {
              id: result.id,
              sessionId,
              taskId: selectedTask,
              image: hintFrame,
              region: null,
              box: result.target_box || null,
              createdAt: Date.now(),
              lastProbeAt: 0,
              probeCount: 0,
              observed: false,
            };
      const completedTier = teachingTier(result.teaching_tier ?? result.level ?? level);
      const explanation = teachingExplanation(result);
      setHintLevel(completedTier);
      setHint(result.response);
      updateTarget({
        box: result.target_box,
        label: result.target_label || reference,
        cue: result.visual_cue,
      });
      const replayBeats = streamedBeats.length ? streamedBeats : result.visual_beats || [];
      saveQuestionGuidance(
        result.target_box && replayBeats.length && (result.continuation_hint_id || result.id)
          ? {
              image: hintFrame,
              box: result.target_box,
              label: result.target_label || frozen?.label || reference,
              reference:
                reference ||
                frozen?.reference ||
                parseLocatedQuestionReference(result.target_label),
              localAnchors,
              hintId: result.continuation_hint_id || result.id,
              level: completedTier,
              explanationStatus: explanation.status,
              continuationAvailable:
                completedTier === 1 && result.continuation_available !== false && explanation.ready,
              deliveryComplete: result.delivery_complete !== false,
              sessionId,
              taskId: selectedTask,
              frameRevision,
            }
          : null,
      );
      lastTeachingSceneRef.current =
        replayBeats.length && result.target_box
          ? {
              image: hintFrame,
              box: result.target_box,
              label: result.target_label || reference,
              beats: [...replayBeats],
              textAnchors: localAnchors?.anchors || [],
            }
          : null;
      setQuestion('');
      setQuestionInputOpen(false);
      if (!streamedSentences) {
        playback.enqueue(
          teachingSteps(
            replayBeats.length ? replayBeats : [{ speech: result.response, page: [], scratch: [] }],
          ),
        );
      }
      playback.finish();
      await playback.done;
      if (!isCurrent()) return;
      // Both guidance depths end without opening another compulsory reply turn.
      // The child can return to writing, ask a specific question or choose depth two.
      if (isCurrent()) refresh();
    } catch (e) {
      playback?.cancel();
      if (isCurrent()) {
        hintContextRef.current.invalidate();
        teachingRunRef.current++;
        if (streamedSentences) stopSpeechOutput();
        setHintBusy(false);
        setHintBusyTarget('');
        setHintTarget(null);
        setTeachingMode('idle');
        if (!frozen) lastTeachingSceneRef.current = null;
        if (e.name !== 'AbortError' && inputDraft !== null) {
          setQuestion((current) => current || inputDraft);
          setQuestionInputOpen(true);
        }
        if (e.name !== 'AbortError') onError(e);
      }
    } finally {
      if (markHintRequestRef.current === markController) {
        markHintRequestRef.current = null;
        setHintBusy(false);
        setHintBusyTarget('');
      }
    }
  };
  requestHintRef.current = requestHint;
  const exitTeaching = () => {
    hintContextRef.current.invalidate();
    markHintRequestRef.current?.abort();
    markHintRequestRef.current = null;
    penHintSchedulerRef.current?.cancel();
    teachingRunRef.current++;
    teachingPlaybackRef.current?.cancel();
    teachingPlaybackRef.current = null;
    setTeachingPlayback(null);
    autoReply.cancel();
    stopSpeechOutput();
    clearSpeechCaption();
    setTeachingMode('idle');
    setReviewTeachingDraft(null);
    setReviewBeat(null);
    setSilentReview(false);
    setHintBusy(false);
    setHintBusyTarget('');
    setAttemptBusy(false);
    setHintTarget(null);
    setShowTeachingMarks(true);
  };
  const openTeachingReview = () => {
    const savedDraft = teachingDraftInScope(teachingDraft, sessionId, selectedTask);
    if (!savedDraft) return;
    exitTeaching();
    setTeachingMode('review');
    setReviewTeachingDraft(savedDraft);
    setReviewBeat(logicalTeachingSteps(savedDraft.beats)[0]?.end ?? null);
    setSilentReview(true);
    const scene = lastTeachingSceneRef.current;
    if (stage === 'active' && scene?.image) {
      setHintTarget({
        kind: 'teaching',
        sceneKey: `review:${savedDraft.sceneKey}`,
        box: scene.box,
        label: scene.label,
        imageUrl: URL.createObjectURL(scene.image),
        beats: scene.beats,
        textAnchors: scene.textAnchors || [],
      });
    }
  };
  const replayTeaching = async (onlyStep = null, slow = false) => {
    const scene =
      lastTeachingSceneRef.current || teachingDraftInScope(teachingDraft, sessionId, selectedTask);
    if (!scene?.beats?.length || hintBusy) {
      onNotice('当前题目没有可重看的分步讲解');
      return;
    }
    const run = ++teachingRunRef.current;
    hintContextRef.current.invalidate();
    autoReply.cancel();
    stopSpeechOutput();
    setShowTeachingMarks(true);
    setTeachingMode('review');
    const fullSteps = teachingSteps(scene.beats);
    const groups = logicalTeachingSteps(fullSteps);
    const chosen = Number.isInteger(onlyStep) ? groups[onlyStep] : null;
    const first = chosen?.start ?? 0;
    const last = chosen?.end ?? fullSteps.length - 1;
    setReviewBeat(first - 1);
    setSilentReview(false);
    const replayUrl = scene.image ? URL.createObjectURL(scene.image) : null;
    if (replayUrl) {
      setHintTarget({
        kind: 'teaching',
        sceneKey: `replay:${run}`,
        box: scene.box,
        label: scene.label,
        imageUrl: replayUrl,
        beats: fullSteps,
        textAnchors: scene.textAnchors || [],
      });
    } else {
      // A restored board has no original photo. Replay its notes without placing
      // old annotations on today's live camera frame.
      setHintTarget(null);
    }
    teachingPlaybackRef.current?.cancel();
    setReviewTeachingDraft({ ...scene, sessionId, taskId: selectedTask || '', beats: fullSteps });
    const controller = newTeachingPlayback(
      () => teachingRunRef.current === run,
      (step, index, _repeat, paperVisible) => {
        setReviewBeat(first + index);
        setSilentReview(false);
        setHintTarget((current) =>
          replayUrl && current?.imageUrl === replayUrl
            ? {
                ...current,
                playbackVersion: (current.playbackVersion || 0) + 1,
                paperPresentationVisible: paperVisible,
                beats: fullSteps,
              }
            : current,
        );
        setReviewTeachingDraft({
          sessionId,
          taskId: selectedTask || '',
          sceneKey: `replay:${run}`,
          box: scene.box || null,
          label: scene.label,
          beats: fullSteps,
        });
      },
      slow ? 0.85 : 1,
      (_step, index) => presentTeachingPrefix(fullSteps.slice(0, first + index + 1), replayUrl),
    );
    teachingPlaybackRef.current = controller;
    setHintBusy(true);
    try {
      controller.enqueue(fullSteps.slice(first, last + 1));
      controller.finish();
      await controller.done;
    } finally {
      if (teachingRunRef.current === run) setHintBusy(false);
    }
  };

  const checkHintAttempt = async ({ silent = false } = {}) => {
    const snapshot = lastHintSnapshot.current;
    if (
      stage !== 'active' ||
      !snapshot ||
      snapshot.sessionId !== sessionId ||
      snapshot.taskId !== selectedTask ||
      attemptBusy ||
      hintBusy
    ) {
      if (!silent) onNotice('请先听这道题的读题与思路，再核对新写的步骤');
      return null;
    }
    snapshot.lastProbeAt = Date.now();
    snapshot.probeCount += 1;
    const run = teachingRunRef.current;
    const isCurrent = () => teachingRunRef.current === run && lastHintSnapshot.current === snapshot;
    if (!silent) {
      setAttemptBusy(true);
      autoReply.cancel();
      setTeachingMode('lesson');
      setReviewBeat(null);
    }
    try {
      const current = await capture(false, false, snapshot.region, snapshot.region ? 1400 : 1800);
      if (!isCurrent()) return null;
      const form = new FormData();
      form.append('session_id', sessionId);
      form.append('before_image', snapshot.image, 'hint-before.jpg');
      form.append('image', current, 'hint-current.jpg');
      if (silent) form.append('background', 'true');
      if (snapshot.box) form.append('target_box', JSON.stringify(snapshot.box));
      const result = await api(`/v1/hints/${snapshot.id}/attempt`, { method: 'POST', body: form });
      if (!isCurrent()) return null;
      if (result.status === 'new_work') snapshot.observed = true;
      if (result.status === 'changed_page') {
        resetTeachingContext();
        if (!silent) speak(result.response);
        return result;
      }
      if (!silent) {
        if (result.status === 'new_work' && snapshot.box) {
          setHintTarget({
            kind: 'attempt',
            box: snapshot.box,
            label: '刚写的步骤',
            imageUrl: URL.createObjectURL(current),
            beats: result.change_box
              ? [
                  {
                    speech: result.response,
                    page: [{ kind: 'circle', box: result.change_box }],
                    scratch: [],
                  },
                ]
              : [],
          });
        }
        setAnalysis(result.response);
        if (result.status === 'new_work' && needsSpokenReply(result.response))
          autoReply.ask('hint_attempt_reply', result.response);
        else speak(result.response);
      }
      if (!silent || result.status === 'new_work') refresh();
      return result;
    } catch (e) {
      if (!silent && isCurrent()) onError(e);
      return null;
    } finally {
      if (!silent && isCurrent()) setAttemptBusy(false);
    }
  };
  checkHintAttemptRef.current = checkHintAttempt;
  const cropFrozenPage = async (frame, box) => {
    const sourceUrl = URL.createObjectURL(frame);
    try {
      const picture = new Image();
      picture.src = sourceUrl;
      await picture.decode();
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(picture.naturalWidth * box[2]);
      canvas.height = Math.round(picture.naturalHeight * box[3]);
      if (!canvas.width || !canvas.height) throw new Error('未找到可拍摄的完整作业页');
      const context = canvas.getContext('2d');
      context.drawImage(
        picture,
        picture.naturalWidth * box[0],
        picture.naturalHeight * box[1],
        picture.naturalWidth * box[2],
        picture.naturalHeight * box[3],
        0,
        0,
        canvas.width,
        canvas.height,
      );
      const thumbnail = document.createElement('canvas');
      thumbnail.width = 16;
      thumbnail.height = 16;
      const small = thumbnail.getContext('2d', { willReadFrequently: true });
      small.drawImage(canvas, 0, 0, 16, 16);
      const pixels = small.getImageData(0, 0, 16, 16).data;
      const luminance = [];
      for (let i = 0; i < pixels.length; i += 4)
        luminance.push((pixels[i] * 3 + pixels[i + 1] * 6 + pixels[i + 2]) / 10);
      const average = luminance.reduce((sum, value) => sum + value, 0) / luminance.length;
      const signature = luminance.map((value) => value < average);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.85));
      if (!blob) throw new Error('作业截图失败，请保持纸面稳定后重试');
      return { blob, signature };
    } finally {
      URL.revokeObjectURL(sourceUrl);
    }
  };
  const samePage = (a, b) => {
    if (a.subject && b.subject && a.subject !== b.subject) return false;
    if (a.printedPage && b.printedPage && a.printedPage !== b.printedPage) return false;
    if (!a.signature || !b.signature) return false;
    let different = 0;
    for (let i = 0; i < a.signature.length; i++) if (a.signature[i] !== b.signature[i]) different++;
    return different <= (a.printedPage && b.printedPage ? 45 : 18);
  };
  const requestCorrection = () => {
    if (stage !== 'active' || batchOpen) return;
    setBatchPurpose('correction');
    setSubmissionTaskId('');
    openBatch();
  };
  const requestTaskSubmission = (task) => {
    if (
      task.status === 'awaiting_parent' ||
      task.status === 'confirmed' ||
      task.status === 'canceled'
    )
      return;
    if (reminderEvent) void dismissTaskReminder();
    if (stage !== 'active') {
      pendingSubmissionTaskId.current = task.id;
      setSelectedTask(task.id);
      if (stage === 'home' || stage === 'done') setup();
      else if (stage === 'paused') resume();
      else onNotice('纸面检查通过并进入作业状态后，会自动拍摄最终作业页');
      return;
    }
    setSelectedTask(task.id);
    setBatchPurpose('submission');
    setSubmissionTaskId(task.id);
    submissionRequestId.current = crypto.randomUUID();
    openBatch();
  };
  const submitWithoutPhoto = async (task) => {
    if (!manualPhotoIssue.trim()) return onError(new Error('请说明为什么无法拍摄最终纸面'));
    try {
      const form = new FormData();
      form.append('version', String(task.version));
      form.append('request_id', crypto.randomUUID());
      form.append('photo_issue', manualPhotoIssue.trim());
      if (sessionId) form.append('session_id', sessionId);
      await api(`/v1/tasks/${task.id}/submissions`, { method: 'POST', body: form });
      setManualTaskId('');
      setManualPhotoIssue('');
      await refresh();
      speak('已注明未附截图，交给家长人工核对。');
    } catch (error) {
      onError(error);
    }
  };
  const startReminderTask = () => {
    const task = reminderEvent?.task || reminderTaskRef.current?.task;
    if (!task) return;
    dismissTaskReminder();
    setSelectedTask(task.id);
    if (stage === 'paused') resume();
    else if (stage === 'home' || stage === 'done') setup();
    else onNotice(`已选中${task.title}，需要时可直接求助或提交。`);
  };
  const requestSelectedCorrection = async () => {
    if (
      stage !== 'active' ||
      batchOpen ||
      batchSubmitting ||
      selectedCaptureRef.current ||
      !selection ||
      !penSelectionReady(selectionStrokes)
    )
      return;
    selectedCaptureRef.current = true;
    setBatchPurpose('correction');
    setSubmissionTaskId('');
    setSelectedCaptureBusy(true);
    try {
      const selectedBox = [...selection];
      const selectedStrokes = selectionStrokes.map((stroke) => stroke.map((point) => [...point]));
      const blob = await capture(false, true, null, 2560);
      batchCanceledRef.current = false;
      batchRunRef.current++;
      const task = dashboard.tasks.find((item) => item.id === selectedTask);
      replaceBatchPages([
        {
          id: crypto.randomUUID(),
          blob,
          previewUrl: URL.createObjectURL(blob),
          selectedBox,
          selectedStrokes,
          taskId: selectedTask,
          subject: task?.subject || '',
          label: '圈画题目',
        },
      ]);
      setBatchConfirmOpen(true);
      setBatchProgress('已冻结当前截图；请核对圈画位置后确认批改。');
      setBatchOpen(true);
    } catch (error) {
      onError(error);
    } finally {
      selectedCaptureRef.current = false;
      setSelectedCaptureBusy(false);
    }
  };
  const openBatch = () => {
    batchCanceledRef.current = false;
    batchRunRef.current++;
    batchWatchRef.current = { frame: null, moving: true, stable: 0, lastScan: 0 };
    setBatchOpen(true);
    setBatchProgress('请将作业页放稳，系统会自动识别并拍照');
    speak('请把作业放稳。我会自动拍清这一页；如果有下一页，直接翻页。');
    currentStream.current
      ?.getVideoTracks()[0]
      ?.applyConstraints({
        width: { ideal: 1600 },
        height: { ideal: 1200 },
        aspectRatio: { ideal: 4 / 3 },
        resizeMode: { exact: 'none' },
      })
      .catch(() => {});
  };
  const addBatchPage = async () => {
    const run = batchRunRef.current;
    if (batchCaptureInFlightRef.current === run || batchSubmitting || batchAwaitingApproval) return;
    if (batchPurpose !== 'submission' && batchPagesRef.current.length >= batchPageLimit) {
      onNotice(`一次最多收集 ${batchPageLimit} 页，请先提交这一批`);
      return;
    }
    batchCaptureInFlightRef.current = run;
    setBatchCapturing(true);
    setBatchConfirmOpen(false);
    pendingBatchConfirm.current = 0;
    batchWatchRef.current.lastScan = Date.now();
    setBatchProgress('正在识别纸面和页面边界…');
    const stagedPages = [];
    try {
      const frame = await capture(false, true, null, 2560);
      const form = new FormData();
      form.append('image', frame, 'paper.jpg');
      const scan = await api(`/v1/sessions/${sessionId}/pages/scan`, {
        method: 'POST',
        body: form,
      });
      if (run !== batchRunRef.current || batchCanceledRef.current) return;
      if (!scan.pages?.length) {
        const guidance = scan.reason || '作业页没有拍清，请调整后再试';
        setBatchProgress(guidance);
        autoReply.ask('batch', `${guidance}。摆好后我会自动再试。`);
        return;
      }
      const collected = [...batchPagesRef.current];
      let added = 0;
      let updated = 0;
      let subjectSkipped = 0;
      let subjectMismatchMessage = '';
      const replacedOld = [];
      for (const detected of scan.pages) {
        const cropped = await cropFrozenPage(frame, detected.box);
        const targetTask = dashboard.tasks.find((task) => task.id === submissionTaskId);
        if (
          batchPurpose === 'submission' &&
          targetTask?.subject &&
          detected.subject &&
          !sameSubject(targetTask.subject, detected.subject)
        ) {
          subjectSkipped++;
          subjectMismatchMessage = `识别到${detected.subject}，当前准备提交${targetTask.subject}；请换到对应作业页。`;
          continue;
        }
        const matchingTask =
          batchPurpose === 'submission'
            ? targetTask
            : dashboard.tasks.find((task) => sameSubject(task.subject, detected.subject));
        const page = {
          id: crypto.randomUUID(),
          blob: cropped.blob,
          previewUrl: URL.createObjectURL(cropped.blob),
          signature: cropped.signature,
          printedPage: detected.printed_page,
          taskId: matchingTask?.id || '',
          subject: detected.subject || '',
          label: detected.printed_page || `第 ${collected.length + 1} 张`,
        };
        const matchingIndex = collected.findIndex((existing) => samePage(existing, page));
        if (matchingIndex >= 0) {
          if (batchPurpose === 'submission') {
            replacedOld.push(collected[matchingIndex].previewUrl);
            collected[matchingIndex] = page;
            stagedPages.push(page);
            updated++;
          } else URL.revokeObjectURL(page.previewUrl);
          continue;
        }
        if (collected.length >= batchPageLimit) {
          URL.revokeObjectURL(page.previewUrl);
          continue;
        }
        stagedPages.push(page);
        collected.push(page);
        added++;
      }
      if (run !== batchRunRef.current || batchCanceledRef.current) {
        stagedPages.forEach((page) => URL.revokeObjectURL(page.previewUrl));
        return;
      }
      if (!added && !updated) {
        setBatchProgress(
          subjectMismatchMessage ||
            (collected.length >= batchPageLimit
              ? `已收集 ${batchPageLimit} 页，请先提交这一批`
              : collected.length
                ? '这页已经拍过；请翻页，或说“拍完了”'
                : '没有收录到可提交的页面，请调整后重新识别'),
        );
        return;
      }
      replacedOld.forEach((url) => URL.revokeObjectURL(url));
      setBatchConfirmOpen(false);
      pendingBatchConfirm.current = 0;
      replaceBatchPages(collected);
      stagedPages.length = 0;
      setBatchProgress(
        `${updated ? '已更新最终截图；' : ''}已拍下 ${collected.length} 页；${subjectSkipped ? `另有 ${subjectSkipped} 页科目不同，未加入；` : ''}换页后会继续拍`,
      );
      autoReply.ask(
        'batch',
        batchPurpose === 'submission'
          ? `已拍下${collected.length}页。还有这一项的其他页就直接翻页，拍完了请说拍完了。`
          : collected.length === 1
            ? '已拍下这一页。如果只批改这张，请说确认批改；还有作业直接翻页。'
            : `已拍下${added}页，共${collected.length}页。还有作业就直接翻页，拍完了请说拍完了。`,
      );
    } catch (e) {
      stagedPages.forEach((page) => URL.revokeObjectURL(page.previewUrl));
      if (run === batchRunRef.current) {
        onError(e);
        setBatchProgress('页面识别失败，请保持纸面稳定后重试');
      }
    } finally {
      if (batchCaptureInFlightRef.current === run) {
        batchCaptureInFlightRef.current = -1;
        setBatchCapturing(false);
      }
    }
  };
  useEffect(() => {
    if (
      !batchOpen ||
      stage !== 'active' ||
      !stream ||
      batchSubmitting ||
      batchConfirmOpen ||
      batchAwaitingApproval
    )
      return;
    const timer = setInterval(() => {
      const video = batchPreviewRef.current;
      if (!video?.videoWidth) return;
      const canvas = document.createElement('canvas');
      canvas.width = 32;
      canvas.height = 24;
      const context = canvas.getContext('2d', { willReadFrequently: true });
      context.drawImage(video, 0, 0, 32, 24);
      const frame = context.getImageData(0, 0, 32, 24).data;
      const watch = batchWatchRef.current;
      if (watch.frame) {
        let changed = 0;
        for (let i = 0; i < frame.length; i += 4)
          if (Math.abs(frame[i] - watch.frame[i]) > 25) changed++;
        if (changed / (32 * 24) > 0.12) {
          watch.moving = true;
          watch.stable = 0;
        } else if (
          watch.moving &&
          ++watch.stable >= 3 &&
          !batchCapturing &&
          Date.now() - watch.lastScan > 3000
        ) {
          watch.moving = false;
          addBatchPage();
        }
      }
      watch.frame = new Uint8ClampedArray(frame);
    }, 1000);
    return () => clearInterval(timer);
  }, [
    batchOpen,
    stage,
    stream,
    batchCapturing,
    batchSubmitting,
    batchConfirmOpen,
    batchAwaitingApproval,
  ]);
  const removeBatchPage = (id) => {
    const removed = batchPagesRef.current.find((page) => page.id === id);
    if (removed) URL.revokeObjectURL(removed.previewUrl);
    replaceBatchPages(batchPagesRef.current.filter((page) => page.id !== id));
  };
  const submitBatch = async () => {
    const pages = [...batchPagesRef.current];
    if (!pages.length || batchSubmitting) return;
    setBatchConfirmOpen(false);
    if (batchPurpose === 'submission') {
      const task = dashboard.tasks.find((item) => item.id === submissionTaskId);
      if (!task) return onError(new Error('提交任务已变化，请刷新后重试'));
      setBatchSubmitting(true);
      setBatchProgress(`正在提交 ${pages.length} 页给家长…`);
      try {
        const form = new FormData();
        form.append('version', String(task.version));
        form.append('request_id', submissionRequestId.current);
        form.append('session_id', sessionId);
        for (const [index, page] of pages.entries())
          form.append('images', page.blob, `page-${index + 1}.jpg`);
        await api(`/v1/tasks/${task.id}/submissions`, { method: 'POST', body: form });
        clearBatchPages();
        await refresh();
        onNotice(`${task.title}已提交，等待家长确认`);
        speak(`${task.title}已经提交给家长。你可以休息，家长确认后任务才算完成。`);
      } catch (error) {
        onError(error);
        setBatchProgress('提交失败，截图仍保留在这里；请检查网络后重试。');
      } finally {
        setBatchSubmitting(false);
      }
      return;
    }
    if (
      stage !== 'active' ||
      dashboard.session?.status !== 'active' ||
      dashboard.session?.id !== sessionId
    ) {
      onNotice('请先继续当前作业，再批改这些截图');
      return;
    }
    if (!correctionAllowed) {
      if (batchAwaitingApproval) return;
      try {
        submitWhenAuthorizedRef.current = true;
        setBatchAwaitingApproval(true);
        const access = await api(`/v1/sessions/${sessionId}/correction-request`, {
          method: 'POST',
        });
        await refresh();
        if (access.allowed === true) {
          setBatchAwaitingApproval(false);
          setBatchProgress('批改权限已就绪，正在准备检查已确认的截图');
        } else {
          setBatchProgress('已请家长授权；获批后将自动批改已确认的截图');
          speak('已请家长授权。获批后，我会检查刚才拍好的作业。');
        }
      } catch (e) {
        submitWhenAuthorizedRef.current = false;
        setBatchAwaitingApproval(false);
        onError(e);
      }
      return;
    }
    submitWhenAuthorizedRef.current = false;
    setBatchAwaitingApproval(false);
    batchCanceledRef.current = false;
    setBatchSubmitting(true);
    const batchId = pages.length > 1 ? crypto.randomUUID() : '';
    const failed = [];
    let completed = 0;
    for (let index = 0; index < pages.length; index++) {
      if (batchCanceledRef.current) break;
      const page = pages[index];
      setBatchProgress(
        `正在批改第 ${index + 1}/${pages.length} 页：${page.subject || '作业'} ${page.label}`,
      );
      try {
        const form = new FormData();
        form.append('image', page.blob, `page-${index + 1}.jpg`);
        if (page.taskId) form.append('task_id', page.taskId);
        if (page.subject) form.append('subject_label', page.subject);
        form.append('page_label', page.label);
        if (page.selectedStrokes?.length)
          form.append('selected_strokes', JSON.stringify(page.selectedStrokes));
        else if (page.selectedBox) form.append('selected_box', JSON.stringify(page.selectedBox));
        if (batchId) {
          form.append('batch_id', batchId);
          form.append('page_index', String(index + 1));
          form.append('batch_total', String(pages.length));
        }
        const result = await api(`/v1/sessions/${sessionId}/corrections`, {
          method: 'POST',
          body: form,
        });
        completed++;
        setCorrection(result);
        replaceCorrectionSnapshot(URL.createObjectURL(page.blob));
      } catch (e) {
        failed.push(page);
        if (/approval|授权|expired/i.test(e.message)) {
          failed.push(...pages.slice(index + 1));
          break;
        }
      }
    }
    if (!batchCanceledRef.current) {
      pages
        .filter((page) => !failed.includes(page))
        .forEach((page) => URL.revokeObjectURL(page.previewUrl));
      replaceBatchPages(failed);
      await refresh().catch(onError);
      if (failed.length) {
        setBatchProgress(
          `已批改 ${completed} 页，还有 ${failed.length} 页未成功；可保留页面重试。`,
        );
        onNotice(
          correctionTestBypass
            ? '部分页面未批改成功，请检查作业截图与网络后重试'
            : '部分页面未批改成功，请检查授权与网络后重试',
        );
      } else {
        setBatchOpen(false);
        setBatchProgress('');
        setCorrectionOpen(true);
        currentStream.current
          ?.getVideoTracks()[0]
          ?.applyConstraints({
            width: { ideal: 1280 },
            height: { ideal: 960 },
            aspectRatio: { ideal: 4 / 3 },
          })
          .catch(() => {});
        onNotice(`${completed} 页已批改，家长端可逐页查看同一张截图`);
        speak(`${completed}页已经全部检查完。每页的批改与解题分析都已打开，可以查看截图上的标记。`);
      }
    }
    setBatchSubmitting(false);
  };
  useEffect(() => {
    if (stage === 'active' && correctionAllowed && submitWhenAuthorizedRef.current && batchOpen)
      submitBatch();
  }, [correctionAllowed, stage, batchOpen]);
  const requestBatchSubmission = () => {
    const pages = batchPagesRef.current;
    if (batchCapturing) {
      speak('正在识别刚才放好的作业页，请稍等一会儿。');
      return;
    }
    if (!pages.length) {
      speak('还没有拍到清晰作业页。请把纸面放稳，系统会自动拍照。');
      return;
    }
    pendingBatchConfirm.current = Date.now() + 30000;
    setBatchConfirmOpen(true);
    autoReply.ask(
      'batch_confirm',
      `共拍下${pages.length}页，请看一下缩略图。都拍好了就说${batchPurpose === 'submission' ? '确认提交' : '确认批改'}；有问题说重新拍摄。`,
    );
  };
  const recordThought = async (content) => {
    const text = (typeof content === 'string' ? content : question).trim();
    if (!text) {
      onNotice('先说出或输入你的想法');
      return;
    }
    try {
      await post(`/v1/sessions/${sessionId}/reflections`, { text, task_id: selectedTask || null });
      setQuestion('');
      setQuestionInputOpen(false);
      onNotice('你的想法已记录，家长可以看到');
      speak('记下来了，继续试试吧。');
      refresh();
    } catch (e) {
      onError(e);
    }
  };
  const updateTimerGoal = async (seconds) => {
    const previous = timerGoal;
    goalTouched.current = true;
    setTimerGoal(seconds);
    if (!sessionId || stage === 'done') return;
    try {
      await patch(`/v1/sessions/${sessionId}/timer`, { goal_seconds: seconds });
      await refresh();
    } catch (e) {
      setTimerGoal(previous);
      onError(e);
    }
  };
  const handleTimerCommand = (command) => {
    if (command.type === 'status') {
      const minutes = Math.floor(timer.learning_seconds / 60);
      const seconds = timer.learning_seconds % 60;
      const remaining = timerGoal ? Math.max(0, timerGoal - timer.learning_seconds) : null;
      speak(
        `已经学习${minutes}分${seconds}秒。${remaining === null ? '今天没有设置参考目标。' : remaining > 0 ? `距离参考目标还有约${Math.ceil(remaining / 60)}分钟。` : '已经达到参考目标，可以继续完成手上的题目。'}`,
      );
    } else if (command.type === 'goal') {
      updateTimerGoal(command.minutes === null ? null : command.minutes * 60);
      speak(
        command.minutes === null
          ? '已取消时间目标，仍会记录学习时间。'
          : `参考目标改为${command.minutes}分钟。`,
      );
    } else if (command.type === 'subject') {
      setTimerSubject(command.subject);
      promptedSubjectKey.current = '';
      setSelectedTask(dashboard.tasks.find((task) => task.subject === command.subject)?.id || '');
      subjectCandidate.current = { label: '', count: 0 };
      speak(`接下来按${command.subject}记录学习时间。`);
    }
  };
  const pause = async () => {
    const pendingTimer = timer.stop();
    clearBatchPages();
    stopCamera();
    setQuestionInputOpen(false);
    setStage('paused');
    setLive(false);
    speak('已暂停，相机已关闭');
    try {
      await pendingTimer;
      await patch(`/v1/sessions/${sessionId}`, { status: 'paused' });
      refresh();
    } catch (e) {
      onError(e);
    }
  };
  useEffect(() => {
    if (!['active', 'setup', 'preview'].includes(stage)) return;
    const hide = () => {
      if (stage === 'active') pause();
      else {
        stopCamera();
        setReady(false);
        setSetupTicket('');
        setStage(stage === 'preview' ? previewReturnStage.current : resuming ? 'paused' : 'home');
      }
    };
    const visibility = () => {
      if (document.visibilityState !== 'hidden') return;
      let nativeStatus = null;
      try {
        if (cameraPreference !== 'builtin') nativeStatus = window.PaperCamera?.status?.();
      } catch {
        // An unavailable bridge never exempts normal background cleanup.
      }
      if (
        keepPendingCameraPermission({
          stage,
          opening: Boolean(cameraOpenRef.current && !cameraOpenRef.current.signal.aborted),
          hasStream: Boolean(cameraSourceRef.current || currentStream.current),
          nativeStatus,
        })
      )
        return;
      hide();
    };
    window.addEventListener('appbackground', hide);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      window.removeEventListener('appbackground', hide);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [stage, sessionId, cameraPreference]);
  const resume = async () => {
    try {
      setResuming(true);
      setReady(false);
      setSetupTicket('');
      setAnalysis('恢复作业前，请重新检查纸面');
      setStage('setup');
      await beginCamera();
      autoReply.ask('setup_camera', '请重新检查纸面。摆好后说检查纸面。');
    } catch (e) {
      if (e.name === 'AbortError') return;
      onError(e);
      setStage('paused');
      setResuming(false);
    }
  };
  const finish = async () => {
    setFinishConfirmOpen(false);
    pendingFinish.current = 0;
    setSummary(null);
    setFinishSyncPending(true);
    const pendingTimer = timer.stop();
    clearBatchPages();
    stopCamera();
    setStage('paused');
    setLive(false);
    try {
      await pendingTimer;
      const result = await api(`/v1/sessions/${sessionId}/finish`, { method: 'POST' });
      setSummary(result);
      setFinishSyncPending(false);
      setStage('done');
      refresh();
      speak('本次学习已结束。尚未提交的任务仍在清单中；完成后再拍摄提交给家长。');
    } catch (e) {
      try {
        const fresh = await api('/v1/families/dashboard');
        if (!fresh.session || fresh.session.id !== sessionId) {
          const result = await api(`/v1/sessions/${sessionId}/finish`, { method: 'POST' });
          setSummary(result);
          setFinishSyncPending(false);
          setStage('done');
          refresh();
          return;
        }
        setStage('paused');
        await patch(`/v1/sessions/${sessionId}`, { status: 'paused' });
        setFinishSyncPending(false);
      } catch {
        // Preserve camera-off state while connectivity is unavailable.
        setStage('paused');
      }
      onError(new Error(`结束状态尚未确认，请重试同步：${e.message}`));
    }
  };
  const requestFinish = () => {
    exitTeaching();
    setFinishConfirmOpen(true);
    pendingFinish.current = Date.now() + 30000;
    const pending = dashboard.tasks.filter((task) =>
      ['todo', 'doing', 'needs_revision'].includes(task.status),
    ).length;
    if (stage === 'active')
      autoReply.ask(
        'finish_confirm',
        finishConfirmationText(pending, batchPagesRef.current.length),
      );
  };
  const respondLearningSupport = (intent) => {
    if (!intent || stage !== 'active') return false;
    exitTeaching();
    setFinishConfirmOpen(false);
    pendingFinish.current = 0;
    if (intent.type === 'offer_rest') {
      clearHelpOffer(false);
      setRestOffer(true);
      speak(intent.message);
    } else {
      setRestOffer(false);
      autoReply.ask('hint_difficulty', intent.message);
    }
    return true;
  };
  const setTask = async (task, status) => {
    try {
      await patch(`/v1/tasks/${task.id}`, { status, version: task.version });
      await refresh();
      onNotice(status === 'doing' ? '已开始该任务' : '任务状态已更新');
    } catch (e) {
      onError(e);
    }
  };
  const handleVoice = (text) => {
    if (handleCaptionDisplayCommand(text)) return;
    if (handleTeachingPlaybackVoice(text)) return;
    if (respondLearningSupport(parseLearningSupport(text))) return;
    if (stage === 'active' && /^(重看图示|再看一遍图示|再画一遍)$/.test(text.trim())) {
      replayTeaching();
      return;
    }
    if (stage === 'active' && /^(返回实时画面|收起图示|看实时画面)$/.test(text.trim())) {
      exitTeaching();
      return;
    }
    const timerCommand = parseTimerCommand(text);
    const questionReference = parseLocatedQuestionReference(text);
    if (stage === 'active' && /^(关闭|停止)(自动|主动|卡题)提醒$/.test(text.trim())) {
      setSessionHelpEnabled(false);
      return;
    }
    if (stage === 'active' && /^(开启|打开)(自动|主动|卡题)提醒$/.test(text.trim())) {
      setSessionHelpEnabled(true);
      return;
    }
    if (stage === 'active' && pendingHelpOffer.current && parseVoiceReply('help_offer', text)) {
      handleShortReply('help_offer', text);
      return;
    }
    if (timerCommand && ['setup', 'active', 'paused'].includes(stage)) {
      handleTimerCommand(timerCommand);
      return;
    }
    if (stage === 'active' && asksForCompleteTeaching(text)) {
      requestHint(
        text,
        '',
        pendingHelpOffer.current?.id || '',
        null,
        null,
        true,
        /^解题思考[。！!]?$/u.test(text.trim()),
      );
      return;
    }
    if (autoReply.consumeManualReply(text)) return;
    if (stage === 'active' && lastHintSnapshot.current) {
      const hintReply = parseVoiceReply('hint_followup', text);
      if (hintReply?.type === 'check_attempt') {
        checkHintAttempt();
        return;
      }
      if (hintReply?.type === 'next_hint') {
        requestHint(lastHintPrompt.current, '', '', null, null, false, true);
        return;
      }
      if (hintReply?.type === 'try_independently') {
        exitTeaching();
        speak('好，你试试看。');
        return;
      }
    }
    if (stage === 'home' && /开始作业|打开作业/.test(text)) setup();
    else if (stage === 'setup' && ready && /开始作业|进入作业|继续作业/.test(text)) start();
    else if (stage === 'setup' && parseVoiceReply('setup_camera', text)?.type === 'check_paper')
      check();
    else if (/暂停/.test(text) && stage === 'active') pause();
    else if (/继续/.test(text) && stage === 'paused') resume();
    else if (
      /^确认结束作业$/.test(text) &&
      ['active', 'paused'].includes(stage) &&
      pendingFinish.current > Date.now()
    ) {
      pendingFinish.current = 0;
      finish();
    } else if (/结束|完成作业/.test(text) && stage === 'active') {
      requestFinish();
    } else if (
      batchOpen &&
      pendingBatchConfirm.current > Date.now() &&
      parseVoiceReply('batch_confirm', text)
    ) {
      handleShortReply('batch_confirm', text);
    } else if (batchOpen && parseVoiceReply('batch', text)) {
      handleShortReply('batch', text);
    } else if (batchOpen) {
      speak('我会自动拍照。还有作业就翻页，全部拍完请说拍完了。');
    } else if (
      /提交作业|交作业|提交任务/.test(text) &&
      ['home', 'done', 'setup', 'active', 'paused'].includes(stage)
    ) {
      const openTasks = dashboard.tasks.filter((task) =>
        ['todo', 'doing', 'needs_revision'].includes(task.status),
      );
      const matching = openTasks.filter(
        (task) => text.includes(task.title) || (task.subject && text.includes(task.subject)),
      );
      const task =
        matching.length === 1
          ? matching[0]
          : openTasks.find((item) => item.id === selectedTask) ||
            (openTasks.length === 1 ? openTasks[0] : null);
      if (task) requestTaskSubmission(task);
      else
        autoReply.ask(
          'submit_target',
          `有${openTasks.length}项待提交。请说要提交的科目或任务名称。`,
        );
    } else if (/批改|检查答案|检查这页|检查作业/.test(text) && stage === 'active') {
      requestCorrection();
    } else if (
      /^(继续提示|再给一步|下一步)$/.test(text) &&
      stage === 'active' &&
      lastHintPrompt.current
    ) {
      requestHint(lastHintPrompt.current, '', '', null, null, false, true);
    } else if (/读第|讲第|解释第/.test(text) && correction) {
      handleShortReply('correction_result', text);
    } else if (isTaskReadCommand(text)) speak(dashboard.tasks.map((t) => t.title).join('。'));
    else if (/记录思路|我的想法/.test(text) && stage === 'active') {
      const thought = text.replace(/^.*?(记录思路|我的想法)[:：]?/, '').trim();
      if (thought) recordThought(thought);
      else speak('请按住说话，说出“我的想法”后面的内容。');
    } else if (questionReference && stage === 'active') {
      setQuestion(text);
      requestHint(text, '', '', null, null, true);
    } else if (
      stage === 'active' &&
      lastHintSnapshot.current &&
      lastHintPrompt.current &&
      !/^(你好|谢谢|好的|不用|取消)$/.test(text.trim())
    ) {
      requestHint(lastHintPrompt.current, text);
    } else if (stage === 'active' && !/^(你好|谢谢|好的|不用|取消)$/.test(text.trim())) {
      setQuestion(text);
      requestHint(text);
    } else onNotice(`听到：${text}。可点击「文字提问」发送。`);
  };
  const currentCorrection = correction
    ? (dashboard.corrections || []).find((item) => item.id === correction.id) || correction
    : (dashboard.corrections || []).find((item) => item.session_id === sessionId);
  const handleShortReply = (context, text) => {
    if (handleCaptionDisplayCommand(text)) return;
    if (handleTeachingPlaybackVoice(text)) return;
    if (respondLearningSupport(parseLearningSupport(text))) return;
    if (stage === 'active' && asksForCompleteTeaching(text)) {
      requestHint(
        text,
        '',
        context === 'help_offer' ? pendingHelpOffer.current?.id || '' : '',
        null,
        null,
        true,
        /^解题思考[。！!]?$/u.test(text.trim()),
      );
      return;
    }
    if (context === 'task_reminder') {
      if (/稍后|等会|晚点/.test(text)) snoozeTaskReminder();
      else if (/读原因|为什么|家长说了什么/.test(text))
        speak(reminderTaskRef.current?.task?.rejection_reason || '请查看任务卡中的家长意见。');
      else if (/开始|现在做|继续做/.test(text)) startReminderTask();
      return;
    }
    if (context === 'submit_target') {
      const candidates = dashboard.tasks.filter(
        (task) =>
          ['todo', 'doing', 'needs_revision'].includes(task.status) &&
          (text.includes(task.title) || (task.subject && text.includes(task.subject))),
      );
      if (candidates.length === 1) requestTaskSubmission(candidates[0]);
      else speak('还不能确定是哪项任务。请点任务卡上的提交作业，或再说清科目和内容。');
      return;
    }
    if (context === 'help_offer' && !pendingHelpOffer.current) return;
    if (context === 'timer_subject' && pendingSubject.current) {
      if (/^(切换|是|对|好的|切换科目)$/.test(text.trim())) {
        setTimerSubject(pendingSubject.current);
        setSelectedTask(
          dashboard.tasks.find((task) => task.subject === pendingSubject.current)?.id || '',
        );
        speak(`已切换到${pendingSubject.current}计时。`);
      }
      pendingSubject.current = '';
      return;
    }
    const timerCommand = parseTimerCommand(text);
    if (timerCommand && ['setup', 'active', 'paused'].includes(stage)) {
      handleTimerCommand(timerCommand);
      return;
    }
    const intent = parseVoiceReply(context, text);
    if (!intent) {
      const fallback = routeUnmatchedVoiceReply(context, text, {
        active: stage === 'active',
        lastHintPrompt: lastHintPrompt.current,
        candidateId: pendingHelpOffer.current?.id || '',
      });
      if (fallback?.type === 'hint')
        requestHint(
          fallback.question,
          fallback.studentThought || '',
          fallback.candidateId || '',
          null,
          null,
          fallback.spokenReference || false,
        );
      else if (fallback?.message) speak(fallback.message);
      return;
    }
    if (intent.type === 'decline') {
      if (context === 'help_offer') clearHelpOffer(false);
      if (context === 'finish_confirm') {
        pendingFinish.current = 0;
        setFinishConfirmOpen(false);
      }
      onNotice('已取消这次语音提议');
      return;
    }
    if (intent.type === 'try_independently' && stage === 'active') {
      exitTeaching();
      speak('好，你试试看。');
      return;
    }
    if (intent.type === 'check_attempt' && stage === 'active') {
      checkHintAttempt();
      return;
    }
    if (intent.type === 'check_paper' && stage === 'setup') check();
    else if (intent.type === 'ask_target' && stage === 'active') {
      if (context === 'help_offer' && pendingHelpOffer.current) {
        const offer = pendingHelpOffer.current;
        if (offer.id) requestHint(`请针对${offer.label}读题并说明实质解题思路`, '', offer.id);
        else {
          clearHelpOffer(false);
          autoReply.ask('hint_target', '你在看哪一题？直接说题号，我会帮你读题和理清思路。');
        }
      } else requestHint(READING_QUESTION);
    } else if (intent.type === 'hint' && stage === 'active') {
      setQuestion(intent.question);
      requestHint(intent.question, '', '', null, null, true);
    } else if (intent.type === 'next_hint' && stage === 'active' && lastHintPrompt.current)
      requestHint(lastHintPrompt.current, '', '', null, null, false, true);
    else if (intent.type === 'open_correction' && stage === 'active') requestCorrection();
    else if (intent.type === 'retry_page' && batchOpen) addBatchPage();
    else if (intent.type === 'remove_last_page' && batchOpen) {
      const page = batchPagesRef.current.at(-1);
      if (page) {
        removeBatchPage(page.id);
        autoReply.ask('batch', `已删除上一张。请把作业放稳，我会自动重拍。`);
        batchWatchRef.current.moving = true;
        batchWatchRef.current.stable = 0;
      }
    } else if (intent.type === 'review_batch' && batchOpen) requestBatchSubmission();
    else if (
      intent.type === 'submit_batch' &&
      batchOpen &&
      (pendingBatchConfirm.current > Date.now() || batchPagesRef.current.length === 1)
    ) {
      pendingBatchConfirm.current = 0;
      submitBatch();
    } else if (intent.type === 'restart_collection' && batchOpen) {
      clearBatchPages();
      openBatch();
    } else if (intent.type === 'finish_homework' && pendingFinish.current > Date.now()) {
      pendingFinish.current = 0;
      finish();
    } else if (intent.type === 'read_summary' && currentCorrection)
      speak(currentCorrection.summary);
    else if (intent.type === 'read_check' && currentCorrection) {
      const checks = currentCorrection.checks || [];
      const position = checks.findIndex((item) =>
        new RegExp(`第\\s*${intent.number}\\s*题`).test(item.question || ''),
      );
      const index = position >= 0 ? position : intent.number - 1;
      const check = checks[index];
      if (!check) {
        speak(`没有找到第${intent.number}题的批改分析。`);
        return;
      }
      setCorrectionOpen(true);
      setTimeout(() =>
        document
          .getElementById(`${currentCorrection.id}-question-${index}`)
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' }),
      );
      speak(
        `${check.question || `第${intent.number}题`}。${check.reason || check.explanation || check.next_step || '请对照纸面标记核对。'}`,
      );
    }
  };
  const teachingVisible = stage === 'active' && Boolean(hintTarget?.imageUrl);
  const guidanceAvailable =
    stage === 'active' &&
    guidanceInScope(questionGuidance, {
      sessionId,
      taskId: selectedTask,
      frameRevision: cameraViewRevision.current,
    });
  const thinkingAvailable =
    guidanceAvailable && questionGuidance.continuationAvailable && !hintBusy && !attemptBusy;
  const savedDraft = teachingDraftInScope(teachingDraft, sessionId, selectedTask);
  const draftTarget = visibleTeachingDraft({
    mode: teachingMode,
    stage,
    target: hintTarget,
    savedDraft: teachingDraft,
    reviewDraft: reviewTeachingDraft,
    sessionId,
    taskId: selectedTask,
  });
  const guidanceExplanation = teachingExplanation(
    guidanceAvailable
      ? {
          explanation_status: questionGuidance.explanationStatus,
          delivery_complete: questionGuidance.deliveryComplete,
        }
      : { visual_beats: teachingMode === 'review' ? draftTarget?.beats : [] },
  );
  const guidanceIncomplete =
    (guidanceAvailable || teachingMode === 'review') && !guidanceExplanation.ready;
  const progressSteps = logicalTeachingSteps(draftTarget?.beats || hintTarget?.beats || []);
  const progressIndex = teachingStepIndex(progressSteps, reviewBeat);
  const historyNavigation =
    silentReview ||
    !teachingPlayback ||
    ((teachingMode === 'review' || reviewBeat !== null) && teachingPlayback.phase === 'complete');
  const showingHistory = historyNavigation;
  const displayStep =
    reviewBeat !== null || !teachingPlayback ? progressIndex : teachingPlayback.stepIndex;
  const displayStepCount = Math.max(progressSteps.length, teachingPlayback?.stepCount || 0, 1);
  const teachingTargetLow =
    teachingVisible && hintTarget.box && hintTarget.box[1] + hintTarget.box[3] / 2 > 0.62;
  const teachingPaperPortrait =
    teachingVisible &&
    teachingPaperSize?.url === hintTarget.imageUrl &&
    teachingPaperSize.height > teachingPaperSize.width;
  const teachingPaperVisible = Boolean(
    teachingVisible &&
    showTeachingMarks &&
    teachingPaperSize?.url === hintTarget.imageUrl &&
    teachingPaperSize.width > 0 &&
    teachingPaperSize.height > 0,
  );
  teachingPaperVisibleRef.current = teachingPaperVisible ? hintTarget.imageUrl : null;
  const rotationAvailable = canRotateCamera({
    stage,
    preference: cameraPreference,
    kind: cameraKind,
    hasBridge: Boolean(window.PaperCamera),
  });
  return (
    <main
      data-teaching-mode={teachingMode}
      data-teaching-tier={hintLevel || undefined}
      data-camera-source={cameraKind}
      data-camera-stage={stage}
      data-camera-orientation={stream ? (cameraPortrait ? 'portrait' : 'landscape') : undefined}
      data-camera-rotation={cameraKind === 'uvc' ? cameraAngle : 0}
      data-teaching-phase={teachingPlayback?.phase || 'idle'}
      data-teaching-step={teachingMode === 'idle' ? '' : displayStep}
      data-teaching-step-count={teachingMode === 'idle' ? '' : displayStepCount}
      data-teaching-started={Boolean(hintTarget?.beats?.length)}
      data-teaching-beat-count={hintTarget?.beats?.length || 0}
      className={[
        'child-main',
        stage === 'active' && 'immersive-active',
        teachingVisible && 'teaching-active',
        teachingTargetLow && 'teaching-target-low',
        teachingPaperPortrait && 'teaching-paper-portrait',
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <VoiceReplyStatus
        status={autoReply.status}
        onCancel={autoReply.cancel}
        onRetry={autoReply.retry}
        captionVisible={Boolean(spokenText)}
      />
      {finishConfirmOpen && (
        <div className="modal-shade" role="presentation">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label="确认结束本次作业"
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                setFinishConfirmOpen(false);
                pendingFinish.current = 0;
                autoReply.cancel();
              }
            }}
          >
            <h2>结束本次作业？</h2>
            <p>
              {finishConfirmationText(
                dashboard.tasks.filter((task) =>
                  ['todo', 'doing', 'needs_revision'].includes(task.status),
                ).length,
                batchPagesRef.current.length,
              )}
            </p>
            <div className="modal-actions">
              <button
                type="button"
                className="button secondary"
                autoFocus
                onClick={() => {
                  setFinishConfirmOpen(false);
                  pendingFinish.current = 0;
                  autoReply.cancel();
                }}
              >
                取消，继续作业
              </button>
              <button type="button" className="button primary" onClick={finish}>
                确认结束作业
              </button>
            </div>
          </section>
        </div>
      )}
      {restOffer && stage === 'active' && (
        <div className="learning-rest-offer" role="status">
          <span>可以先歇一会儿，按自己的节奏来。</span>
          <button className="button secondary" onClick={pause}>
            <Pause size={16} /> 暂停休息
          </button>
          <button className="button ghost" onClick={() => setRestOffer(false)}>
            我继续试试
          </button>
        </div>
      )}
      {finishSyncPending && stage === 'paused' && (
        <div className="panel" role="status">
          <p>相机已关闭，正在确认结束状态。连接恢复后可重试同步。</p>
          <button className="button secondary" onClick={finish}>
            重试同步结束状态
          </button>
        </div>
      )}
      {reminderEvent && teachingMode === 'idle' && (
        <div className="task-reminder-banner" role="status">
          <div>
            <strong>
              {reminderEvent.type === 'returned'
                ? '家长请你补做'
                : reminderEvent.type === 'due'
                  ? '快到截止时间'
                  : reminderEvent.type === 'overdue'
                    ? '作业仍可补交'
                    : '有作业待完成'}
            </strong>
            <p>
              {reminderEvent.task.title}
              {reminderEvent.task.rejection_reason
                ? `：${reminderEvent.task.rejection_reason}`
                : ''}
            </p>
          </div>
          <div className="task-reminder-actions">
            <button className="button mini primary" onClick={startReminderTask}>
              现在开始
            </button>
            <button className="button mini secondary" onClick={snoozeTaskReminder}>
              稍后提醒
            </button>
            <button className="text-button" onClick={dismissTaskReminder}>
              知道了
            </button>
          </div>
        </div>
      )}
      <div className="child-hero">
        <div>
          <div className="section-kicker">A LITTLE PROGRESS, EVERY DAY</div>
          <h1>
            {stage === 'done' ? '本次学习已结束。' : `${dashboard.family.child_name}，准备好了吗？`}
          </h1>
          <p>
            {stage === 'active'
              ? '看着纸面，一题一题来。需要时，先一起读题、理清思路。'
              : stage === 'preview'
                ? '先调整相机角度，也可以拍一张照片检查清晰度。'
                : stage === 'paused'
                  ? '作业已暂停，相机已关闭。'
                  : '把作业本放好，我们一起开始今天的任务。'}
          </p>
        </div>
        <div className="child-sun">✳</div>
      </div>
      {stage === 'done' && (
        <div className="done-note">
          <CheckCircle2 />
          <div>
            <strong>本次作业回顾</strong>
            <p>
              学习 {formatStudyTime(summary?.duration_seconds || 0)} · 求助{' '}
              {summary?.review?.hint_count || 0} 次 · 本次提交{' '}
              {summary?.review?.submitted_task_count || 0} 项 · 待家长确认{' '}
              {summary?.review?.task_counts?.awaiting_parent || 0} 项 · 待补做{' '}
              {summary?.review?.task_counts?.needs_revision || 0} 项 · 家长已确认{' '}
              {summary?.review?.task_counts?.confirmed || 0} 项。
            </p>
            <p>{summary?.review?.record?.text || '本次还没有保存想法。'}</p>
            <p>{summary?.review?.next_step?.text || '可以用自己的话说说这次采用的方法。'}</p>
            <p>作业提交后由家长确认；退回的任务可按意见补做。</p>
          </div>
        </div>
      )}
      <div className="child-grid">
        <section className="paper-panel">
          <div className="paper-header">
            <div>
              <span className="section-kicker">PAPER VIEW</span>
              <h2>
                {stage === 'preview'
                  ? '相机预览'
                  : stage === 'setup'
                    ? '摆好作业本'
                    : stage === 'active'
                      ? teachingMode === 'lesson'
                        ? '老师辅导中'
                        : teachingMode === 'review'
                          ? '回看辅导'
                          : '正在陪伴'
                      : stage === 'paused'
                        ? '休息一下'
                        : '准备作业'}
              </h2>
            </div>
            <div className="status-pills">
              <span className={stream ? 'status on' : 'status'}>
                <Camera size={14} />
                {cameraStarting
                  ? '相机启动中'
                  : stream
                    ? cameraKind === 'uvc'
                      ? 'USB 外接相机'
                      : '平板后置相机'
                    : '相机关闭'}
              </span>
              {live && (
                <span className="status watching">
                  <Eye size={14} />
                  家长查看中
                </span>
              )}
              {voiceCall && (
                <span className="status watching">
                  <Mic size={14} />
                  通话中 · 麦克风开启
                </span>
              )}
            </div>
          </div>
          {teachingMode !== 'idle' && ['active', 'paused'].includes(stage) && (
            <button
              type="button"
              className="hint-paper-return teaching-exit"
              onClick={exitTeaching}
            >
              <Eye size={15} /> {teachingMode === 'review' ? '结束回看' : '退出辅导，继续作业'}
            </button>
          )}
          {teachingMode === 'review' && !teachingPlayback && (
            <button className="button secondary teaching-review-play" onClick={replayTeaching}>
              <Volume2 size={16} /> 分步重讲
            </button>
          )}
          {teachingMode !== 'idle' &&
            ['active', 'paused'].includes(stage) &&
            (teachingPlayback || progressSteps.length > 0) && (
              <TeachingStepControls
                stepIndex={displayStep}
                stepCount={displayStepCount}
                totalKnown={
                  !guidanceIncomplete &&
                  (teachingMode === 'review' || Boolean(teachingPlayback?.receivedComplete))
                }
                title={
                  guidanceIncomplete
                    ? teachingMode === 'review' && !guidanceAvailable
                      ? '回看核对说明'
                      : guidanceExplanation.title
                    : showingHistory
                      ? progressSteps[progressIndex]?.title || '回看讲解'
                      : teachingPlayback.phase === 'preparing'
                        ? hintPreparationLabel || '正在准备接下来的讲解…'
                        : teachingPlayback.title
                }
                label={
                  showingHistory
                    ? '静默回看这一步；可重讲或继续。'
                    : teachingPlayback.interrupted
                      ? '刚才的播报已暂停，可以重讲这步；准备好再继续。'
                      : teachingPlayback.phase === 'complete'
                        ? guidanceIncomplete
                          ? guidanceExplanation.message
                          : hintLevel === 1
                            ? '可以回去写；需要完整讲解时，再选解题思考。'
                            : '这一轮讲完了，可以自己试一试；不懂的地方可直接提问。'
                        : teachingPlayback.phase === 'reading'
                          ? '看清图上这一步，老师随后继续讲解。'
                          : teachingPlayback.phase === 'preparing'
                            ? '正在准备这一小步。'
                            : ''
                }
                busy={
                  !showingHistory &&
                  ['playing', 'reading', 'preparing'].includes(teachingPlayback?.phase)
                }
                waiting={Boolean(
                  teachingPlayback && teachingPlayback.phase === 'waiting' && !showingHistory,
                )}
                slow={teachingPlayback?.slow}
                hasNext={
                  showingHistory
                    ? progressIndex + 1 < progressSteps.length || Boolean(teachingPlayback?.hasNext)
                    : teachingPlayback?.hasNext
                }
                canAdvance={
                  showingHistory || ['waiting', 'reading'].includes(teachingPlayback?.phase)
                }
                onPrevious={() => reviewTeachingStep(displayStep - 1)}
                onNext={() => teachingPlaybackCommand('next')}
                onReplay={() => teachingPlaybackCommand('replay')}
                onSlower={() => teachingPlaybackCommand('slower')}
                onPause={() => teachingPlaybackCommand('pause')}
                onResume={() => teachingPlaybackCommand('next')}
                onThinking={
                  thinkingAvailable
                    ? () => requestHint(THINKING_QUESTION, '', '', null, null, false, true)
                    : undefined
                }
                onRetry={
                  guidanceAvailable && guidanceIncomplete && guidanceExplanation.retry && !hintBusy
                    ? () => requestHint(lastHintPrompt.current)
                    : undefined
                }
              />
            )}
          <div
            className={`camera-stage ${!stream ? 'blank' : ''} ${stage === 'paused' && draftTarget ? 'has-preserved-draft' : ''}`}
          >
            <div className="camera-media" style={{ '--camera-aspect': cameraAspect }}>
              <video ref={videoRef} data-camera-source={cameraKind} autoPlay playsInline muted />
              {stage === 'preview' && cameraPreviewImage && (
                <div className="camera-test-snapshot">
                  <img src={cameraPreviewImage} alt="刚刚拍摄的相机照片，仅在本机预览" />
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => setCameraPreviewImage('')}
                  >
                    返回实时取景
                  </button>
                </div>
              )}
              {stage === 'setup' && setupIssue && (
                <div
                  className="setup-issue-snapshot"
                  role="img"
                  aria-label="纸面检查截图中看不清的位置"
                >
                  <img src={setupIssue.imageUrl} alt="本次纸面检查截图" />
                  <div
                    className={`setup-issue-box${setupIssue.box[1] < 0.1 ? ' near-top' : ''}${setupIssue.box[0] > 0.72 ? ' near-right' : ''}`}
                    style={{
                      left: `${setupIssue.box[0] * 100}%`,
                      top: `${setupIssue.box[1] * 100}%`,
                      width: `${setupIssue.box[2] * 100}%`,
                      height: `${setupIssue.box[3] * 100}%`,
                    }}
                  >
                    <span>这里看不清</span>
                  </div>
                </div>
              )}
              <div className="frame-corners">
                <i />
                <i />
                <i />
                <i />
              </div>
              {stage === 'active' && hintTarget && (
                <TeachingPaperFrame
                  key={hintTarget.sceneKey}
                  target={hintTarget}
                  beats={hintTarget.beats || []}
                  beatIndex={reviewBeat}
                  showMarks={showTeachingMarks}
                  onToggleMarks={() => setShowTeachingMarks((shown) => !shown)}
                >
                  {hintTarget.imageUrl && (
                    <img
                      src={hintTarget.imageUrl}
                      alt="本次辅导定位的纸面截图"
                      onLoad={(event) => {
                        const image = event.currentTarget;
                        setTeachingPaperSize({
                          url: hintTarget.imageUrl,
                          width: image.naturalWidth,
                          height: image.naturalHeight,
                        });
                      }}
                    />
                  )}
                  <TeachingInk
                    target={hintTarget}
                    beats={hintTarget.beats || []}
                    show={showTeachingMarks}
                    beatIndex={reviewBeat}
                    playbackVersion={hintTarget.playbackVersion}
                  />
                  {showTeachingMarks && hintTarget.cue && !hintTarget.beats?.length && (
                    <div
                      className={`hint-visual-cue ${hintTarget.cue.kind}`}
                      style={{
                        left: `${hintTarget.cue.box[0] * 100}%`,
                        top: `${hintTarget.cue.box[1] * 100}%`,
                        width: `${hintTarget.cue.box[2] * 100}%`,
                        height: `${hintTarget.cue.box[3] * 100}%`,
                      }}
                      role="img"
                      aria-label={`本轮辅导标注：${hintTarget.cue.label || '请看这里'}`}
                    />
                  )}
                </TeachingPaperFrame>
              )}
              {stage === 'active' && !teachingVisible && (
                <SelectionBox
                  selection={selection}
                  strokes={selectionStrokes}
                  resetOnNextStroke={selectionConsumed}
                  onChange={(box, strokes) => {
                    if (teachingMode !== 'idle') exitTeaching();
                    saveQuestionGuidance(null);
                    penHintSchedulerRef.current.change();
                    markHintRequestRef.current?.abort();
                    setSelection(box);
                    setSelectionStrokes(strokes);
                    setSelectionConsumed(false);
                    setHintTarget(null);
                  }}
                  onStrokeEnd={(strokes) => {
                    if (!questionInputOpen && !batchOpen && !correctionOpen && !voiceCall)
                      penHintSchedulerRef.current.finish(strokes);
                  }}
                  onHint={() => {
                    if (hintBusy && markHintRequestRef.current) return;
                    penHintSchedulerRef.current.cancel();
                    requestHint(READING_QUESTION);
                  }}
                  onCorrect={() => {
                    penHintSchedulerRef.current.cancel();
                    markHintRequestRef.current?.abort();
                    requestSelectedCorrection();
                  }}
                  disabled={hintBusy || batchSubmitting || selectedCaptureBusy}
                />
              )}
              {annotation.length > 1 && (
                <svg
                  className="annotation-overlay"
                  viewBox="0 0 100 100"
                  preserveAspectRatio="none"
                >
                  <polyline
                    points={annotation.map(([x, y]) => `${x * 100},${y * 100}`).join(' ')}
                    fill="none"
                    stroke="#ff6b4d"
                    strokeWidth="1.1"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              )}
            </div>
            {!stream && (
              <div className="camera-placeholder">
                <BookOpen size={62} strokeWidth={1.3} />
                <strong>{cameraStarting ? '正在打开相机…' : '纸面还没有出现'}</strong>
                <span>
                  {cameraStarting ? '请稍候，准备好后检查纸面' : '点击「开始作业」打开相机'}
                </span>
              </div>
            )}
            {(checking || hintBusy || attemptBusy) && !(hintBusy && teachingVisible) && (
              <div className="camera-busy" role="status">
                <span className="activity-spinner" />
                {checking
                  ? '正在确认纸面是否清晰…'
                  : attemptBusy
                    ? '正在对比提示前后的纸面，核对新步骤…'
                    : hintBusyTarget
                      ? `正在纸面查找${hintBusyTarget}并准备讲解…`
                      : '正在核对题目并准备讲解…'}
              </div>
            )}
            {stage === 'setup' && (
              <div className="camera-note">作业本完整入镜 · 文字清晰 · 检查通过后自动开始</div>
            )}
            {(stage === 'active' || stage === 'paused') && (
              <div className="study-timer-wrap">
                <button
                  className="study-timer-chip"
                  onClick={() => setTimerDetailsOpen((open) => !open)}
                  aria-expanded={timerDetailsOpen}
                  aria-label="查看学习计时详情"
                >
                  <Clock3 size={16} />
                  <span>
                    <strong>{formatStudyTime(timer.learning_seconds)}</strong>
                    <small>已学习</small>
                  </span>
                  {timerGoal && (
                    <span className="study-timer-target">/ {Math.round(timerGoal / 60)} 分</span>
                  )}
                  {timer.pending && <i title="等待网络恢复后同步">•</i>}
                </button>
                {timerDetailsOpen && (
                  <div className="study-timer-details">
                    <strong>{timerSubject || '待归类科目'} · 学习计时</strong>
                    <p>
                      独立作业 {formatStudyTime(timer.focus_seconds)} · 辅导{' '}
                      {formatStudyTime(timer.assisted_seconds)}
                    </p>
                    <p>
                      批改等待 {formatStudyTime(timer.grading_wait_seconds)} · 暂停{' '}
                      {formatStudyTime(timer.paused_seconds)}，均不计入学习时间
                    </p>
                    {timer.by_subject?.map((item) => (
                      <p key={item.subject || 'unknown'}>
                        {item.subject || '待归类'} {formatStudyTime(item.seconds)}
                      </p>
                    ))}
                    {timer.pending && <p>网络暂不可用，计时已保存在本机，恢复后同步。</p>}
                    <div>
                      <button
                        onClick={() => updateTimerGoal(Math.max(300, (timerGoal || 600) - 300))}
                      >
                        目标－5分
                      </button>
                      <button
                        onClick={() => updateTimerGoal(Math.min(10800, (timerGoal || 0) + 300))}
                      >
                        目标＋5分
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
          {draftTarget && (
            <TeachingScratch
              key={`${sessionId}:${selectedTask}:${draftTarget.sceneKey}`}
              target={draftTarget}
              beats={draftTarget.beats || []}
              inline={stage !== 'active'}
              playbackVersion={hintTarget?.playbackVersion}
              playbackBeatIndex={reviewBeat}
              paperVisible={teachingPaperVisible && hintTarget?.paperPresentationVisible !== false}
            />
          )}
          <div className="camera-controls">
            {['home', 'paused', 'done'].includes(stage) && window.PaperCamera && (
              <label className="camera-source-select">
                <span>拍摄相机</span>
                <select
                  aria-label="拍摄相机"
                  value={cameraPreference}
                  onChange={(event) => {
                    setCameraPreference(event.target.value);
                    save('camera_source', event.target.value);
                  }}
                >
                  <option value="auto">自动 · 优先外接</option>
                  <option value="uvc">USB 外接相机</option>
                  <option value="builtin">平板后置相机</option>
                </select>
              </label>
            )}
            {rotationAvailable && (
              <div className="camera-rotation-control">
                <button
                  type="button"
                  className="button secondary"
                  aria-label="旋转画面"
                  disabled={cameraStarting || cameraRotating}
                  onClick={rotateCamera}
                >
                  <RotateCw size={18} /> {cameraRotating ? '正在旋转…' : '旋转画面'}
                </button>
                <div>
                  <span className="camera-rotation-direction" role="status">
                    {stream ? `${cameraPortrait ? '竖向单页' : '横向画面'} · ` : 'USB 画面 · '}
                    {cameraAngle === 0 ? '原方向' : `已旋转 ${cameraAngle}°`}
                  </span>
                  <small>单页竖拍时，让文字朝上、整页入镜。</small>
                </div>
              </div>
            )}
            {['home', 'paused', 'done'].includes(stage) && (
              <button type="button" className="button secondary" onClick={previewCamera}>
                <Camera size={17} /> 预览相机
              </button>
            )}
            {stage === 'preview' ? (
              <>
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => {
                    stopCamera();
                    setStage(previewReturnStage.current);
                  }}
                >
                  <ArrowLeft size={17} /> 关闭预览
                </button>
                <button
                  type="button"
                  className="button dark"
                  disabled={cameraStarting || cameraRotating || !stream}
                  onClick={previewCameraPhoto}
                >
                  <Camera size={17} /> 拍照预览
                </button>
                <span className="camera-preview-note">照片仅在本机预览</span>
              </>
            ) : stage === 'home' || stage === 'done' ? (
              <button className="button primary large" onClick={setup}>
                <Camera size={20} /> 开始作业
              </button>
            ) : stage === 'setup' ? (
              <>
                <button
                  className="button secondary"
                  onClick={() => {
                    stopCamera();
                    setStage(resuming ? 'paused' : 'home');
                    setResuming(false);
                  }}
                >
                  <ArrowLeft size={17} /> 返回
                </button>
                <button
                  className="button dark"
                  disabled={checking || cameraStarting || cameraRotating}
                  onClick={check}
                >
                  <Eye size={17} />
                  {checking ? '正在检查…' : '检查纸面'}
                </button>
                {sessionStartError && ready && (
                  <button className="button primary" onClick={() => start()}>
                    <Play size={17} /> 重试进入作业
                  </button>
                )}
              </>
            ) : stage === 'active' ? (
              <>
                <button className="button secondary" onClick={pause}>
                  <Pause size={17} /> 暂停
                </button>
                <button
                  className="button dark"
                  disabled={hintBusy}
                  onClick={() =>
                    guidanceAvailable &&
                    (questionGuidance.level === 2 || guidanceExplanation.status === 'needs_parent')
                      ? replayTeaching()
                      : requestHint(READING_QUESTION)
                  }
                >
                  <CircleHelp size={17} />{' '}
                  {hintBusy
                    ? '老师正在准备…'
                    : guidanceAvailable && guidanceExplanation.status === 'needs_parent'
                      ? '重听核对说明'
                      : guidanceAvailable && questionGuidance.level === 2
                        ? '重听本题'
                        : '读题与思路'}
                </button>
                {teachingMode === 'idle' && thinkingAvailable && (
                  <button
                    className="button secondary"
                    onClick={() => requestHint(THINKING_QUESTION, '', '', null, null, false, true)}
                  >
                    解题思考
                  </button>
                )}
                {savedDraft && teachingMode === 'idle' && (
                  <button
                    className="button secondary"
                    disabled={hintBusy}
                    onClick={openTeachingReview}
                  >
                    <BookOpen size={17} /> 回看上次辅导
                  </button>
                )}
                <button
                  className="button primary"
                  disabled={batchSubmitting || (correctionTestBypass && !correctionAllowed)}
                  onClick={() => requestCorrection()}
                >
                  <ClipboardCheck size={17} />{' '}
                  {batchSubmitting
                    ? '正在批改…'
                    : batchOpen
                      ? '正在拍作业'
                      : !correctionAllowed
                        ? correctionTestBypass
                          ? '批改暂不可用'
                          : dashboard.session?.correction_requested_at
                            ? '等待家长授权'
                            : '申请批改'
                        : '批改作业'}
                </button>
                {currentCorrection && correctionAllowed && (
                  <button className="button secondary" onClick={() => setCorrectionOpen(true)}>
                    <ClipboardCheck size={17} /> 查看批改
                  </button>
                )}
                <button className="button ghost" onClick={requestFinish}>
                  <Square size={16} /> 结束作业
                </button>
              </>
            ) : (
              <>
                <button className="button primary" disabled={finishSyncPending} onClick={resume}>
                  <Play size={17} /> 继续作业
                </button>
                <button className="button ghost" onClick={requestFinish}>
                  <Square size={16} /> 结束作业
                </button>
                {savedDraft && teachingMode === 'idle' && (
                  <button className="button secondary" onClick={openTeachingReview}>
                    <BookOpen size={17} /> 回看上次辅导
                  </button>
                )}
              </>
            )}
          </div>
          {stage === 'setup' && (
            <details className="study-goal-setup">
              <summary>
                <Clock3 size={14} />
                {timerGoal ? `参考时间 ${Math.round(timerGoal / 60)} 分钟` : '不设时间目标'}
                <span>· 可选，点此调整</span>
              </summary>
              <div>
                <button onClick={() => updateTimerGoal(Math.max(300, (timerGoal || 600) - 300))}>
                  －5 分
                </button>
                <button onClick={() => updateTimerGoal(Math.min(10800, (timerGoal || 0) + 300))}>
                  ＋5 分
                </button>
                <button
                  onClick={() =>
                    updateTimerGoal(
                      timerGoal ? null : dashboard.timer_suggestion?.goal_seconds || 1200,
                    )
                  }
                >
                  {timerGoal ? '不设目标' : '使用建议'}
                </button>
              </div>
            </details>
          )}
          {['active', 'paused'].includes(stage) && correctionTestBypass && (
            <div
              className="correction-access-note correction-test-mode"
              title="测试模式无需家长授权；仅当前正在进行的作业可批改。"
            >
              <ClipboardCheck size={15} />
              <span className="correction-test-mode-short">
                {correctionAllowed ? '测试模式 · 直接批改' : '测试模式 · 需继续作业'}
              </span>
              <span className="correction-test-mode-detail">
                {correctionAllowed
                  ? '测试模式：可直接批改，无需家长授权。请先写下答案，再拍照检查。'
                  : '测试模式已开启；需在当前设备开始或继续作业后批改，无需家长授权。'}
              </span>
            </div>
          )}
          {stage === 'active' && !correctionTestBypass && !correctionAllowed && (
            <div className="correction-access-note">
              <ClipboardCheck size={15} />
              {dashboard.session?.correction_requested_at
                ? '已申请家长授权。家长批准后可批改作业。'
                : '批改需要家长授权。先自己写下答案，再点击“批改作业”申请。'}
            </div>
          )}
          {analysis && !teachingVisible && !spokenText.includes(analysis) && (
            <div className="analysis-note">
              <Sparkles size={17} />
              <span>{analysis}</span>
              <button onClick={() => speak(analysis)} title="播报">
                <Volume2 size={17} />
              </button>
            </div>
          )}
          {stage === 'active' &&
            hint &&
            !teachingVisible &&
            !correctionOpen &&
            !batchOpen &&
            (!spokenText.includes(hint) || lastTeachingSceneRef.current?.beats?.length) &&
            (lastTeachingSceneRef.current?.beats?.length ? (
              <button className="hint-recap" onClick={openTeachingReview}>
                <RefreshCw size={16} /> 回看辅导图文
              </button>
            ) : (
              <div className="hint-card">
                <div className="hint-card-header">
                  <span className="section-kicker">陪你想一步</span>
                  <button
                    className="hint-replay"
                    onClick={() => speak(hint)}
                    aria-label="再听一遍提示"
                    title="再听一遍"
                  >
                    <Volume2 size={16} />
                  </button>
                </div>
                <p>{hint}</p>
              </div>
            ))}
          {currentCorrection &&
            (stage === 'done' || correctionAllowed) &&
            (stage !== 'active' || correctionOpen) && (
              <div ref={correctionRef} className="child-correction-card">
                {stage === 'active' && (
                  <button
                    className="correction-drawer-close"
                    onClick={() => setCorrectionOpen(false)}
                    aria-label="关闭批改结果"
                  >
                    <X size={18} /> 返回作业画面
                  </button>
                )}
                {correctionSnapshot ? (
                  <CorrectionResult
                    item={currentCorrection}
                    snapshotUrl={correctionSnapshot}
                    onUpdated={refresh}
                  />
                ) : (
                  <CorrectionRecord item={currentCorrection} initiallyOpen onUpdated={refresh} />
                )}
                <button
                  className="correction-replay"
                  onClick={() => speak(currentCorrection.summary)}
                >
                  <Volume2 size={16} /> 再听一次
                </button>
              </div>
            )}
          {(stage === 'done' || correctionAllowed) &&
            (dashboard.corrections || []).filter(
              (item) => item.session_id === sessionId && item.id !== currentCorrection?.id,
            ).length > 0 && (
              <div className="child-correction-history">
                <h3>其他已批改页面</h3>
                {(dashboard.corrections || [])
                  .filter(
                    (item) => item.session_id === sessionId && item.id !== currentCorrection?.id,
                  )
                  .map((item) => (
                    <CorrectionRecord
                      key={item.id}
                      item={item}
                      initiallyOpen={false}
                      onUpdated={refresh}
                    />
                  ))}
              </div>
            )}
        </section>
        <aside className="child-side">
          {(dashboard.tasks.length > 0 || (stage !== 'active' && stage !== 'paused')) && (
            <section className={`panel task-panel ${tasksExpanded ? 'expanded' : ''}`}>
              <div className="panel-title">
                {stage === 'active' || stage === 'paused' ? (
                  <button
                    type="button"
                    className="task-panel-toggle"
                    onClick={() => setTasksExpanded((open) => !open)}
                    aria-expanded={tasksExpanded}
                  >
                    今天要做的事 {tasksExpanded ? '收起' : '展开'}
                  </button>
                ) : (
                  <h2>今天要做的事</h2>
                )}
                <span>
                  {dashboard.tasks.filter((t) => t.status === 'confirmed').length}/
                  {dashboard.tasks.length}
                </span>
              </div>
              {(stage !== 'active' && stage !== 'paused') || tasksExpanded ? (
                dashboard.tasks.length ? (
                  dashboard.tasks.map((task) => (
                    <div
                      className={`child-task ${selectedTask === task.id ? 'selected' : ''}`}
                      key={task.id}
                      onClick={() => {
                        setSelectedTask(task.id);
                        promptedSubjectKey.current = '';
                        if (task.subject) setTimerSubject(task.subject);
                        if (task.status === 'todo') setTask(task, 'doing');
                      }}
                    >
                      <span className={`task-circle ${task.status}`}>
                        <Check size={17} />
                      </span>
                      <div>
                        <strong>{task.title}</strong>
                        <small>
                          {task.status === 'confirmed'
                            ? '家长已确认'
                            : task.status === 'canceled'
                              ? `家长已取消${task.cancel_reason ? `：${task.cancel_reason}` : ''}`
                              : task.status === 'awaiting_parent'
                                ? '已提交 · 等待家长确认'
                                : task.status === 'needs_revision'
                                  ? '家长退回 · 需要补做'
                                  : task.status === 'doing'
                                    ? '正在做'
                                    : '待开始'}
                          {task.due_at
                            ? ` · 截止 ${new Date(task.due_at * 1000).toLocaleString('zh-CN')}`
                            : task.due_date
                              ? ` · 截止 ${task.due_date}`
                              : ''}
                        </small>
                        {task.rejection_reason && (
                          <small className="task-rejection">请补做：{task.rejection_reason}</small>
                        )}
                      </div>
                      {['todo', 'doing', 'needs_revision'].includes(task.status) && (
                        <button
                          className="button mini"
                          onClick={(event) => {
                            event.stopPropagation();
                            requestTaskSubmission(task);
                          }}
                        >
                          提交作业
                        </button>
                      )}
                      {['todo', 'doing', 'needs_revision'].includes(task.status) && (
                        <button
                          className="text-button"
                          onClick={(event) => {
                            event.stopPropagation();
                            setManualTaskId(task.id);
                          }}
                        >
                          无法拍摄？
                        </button>
                      )}
                      {manualTaskId === task.id && (
                        <div className="manual-submit" onClick={(event) => event.stopPropagation()}>
                          <label>
                            说明原因
                            <input
                              value={manualPhotoIssue}
                              maxLength={200}
                              onChange={(event) => setManualPhotoIssue(event.target.value)}
                              placeholder="例如：相机无法启动，需要家长现场核对"
                            />
                          </label>
                          <button className="button mini" onClick={() => submitWithoutPhoto(task)}>
                            提交给家长人工核对
                          </button>
                          <button className="text-button" onClick={() => setManualTaskId('')}>
                            取消
                          </button>
                        </div>
                      )}
                    </div>
                  ))
                ) : (
                  <Empty
                    icon={<BookOpen />}
                    title="今天还没有任务"
                    text="让家长在手机端粘贴群作业文字。"
                  />
                )
              ) : null}
            </section>
          )}
          <section className="voice-card">
            <div className="voice-icon">
              <Headphones size={23} />
            </div>
            <h3>按住说话</h3>
            <p>
              按住下方按钮，说「批改作业」「第 7 题求提示」「我的想法……」或「暂停」。
              进入家长语音通话后，双方会持续传音，结束通话即关闭麦克风。
            </p>
            <VoiceControls
              onText={handleVoice}
              onError={(error) => onError(voiceErrorWithTranscriptionHint(error, dashboard))}
              remoteTranscription={remoteTranscription}
              disabled={voiceCall || batchOpen}
              disabledLabel={
                voiceCall ? '通话中，语音指令已暂停' : '拍摄作业时，请用画面内的语音按钮'
              }
              compact={stage === 'active'}
            />
          </section>
          {stage === 'active' && (
            <div className={`question-bar ${questionInputOpen ? 'open' : 'collapsed'}`}>
              {!questionInputOpen ? (
                <button
                  type="button"
                  className="question-toggle"
                  onClick={() => {
                    penHintSchedulerRef.current.cancel();
                    autoReply.cancel();
                    teachingPlaybackRef.current?.interrupt();
                    setQuestionInputOpen(true);
                  }}
                  aria-label="打开文字提问"
                >
                  <Send size={16} /> 文字提问
                </button>
              ) : (
                <>
                  <input
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    aria-label="我的问题"
                    placeholder="输入问题，或按住说话"
                  />
                  <button className="icon-button" onClick={requestHint} title="发送问题">
                    <Send />
                  </button>
                  <button
                    type="button"
                    className="icon-button"
                    onClick={() => setQuestionInputOpen(false)}
                    aria-label="收起文字提问"
                  >
                    <X size={18} />
                  </button>
                </>
              )}
            </div>
          )}
          {stage === 'active' && (
            <div className="thought-actions">
              <button className="button secondary" onClick={() => recordThought()}>
                记录想法
              </button>
            </div>
          )}
          <label className="toggle-line">
            <span>
              <strong>本次自动卡题提醒</strong>
              <small>
                {parentHelpMode === 'off'
                  ? '家长已关闭；仍可主动求助'
                  : '纸面久无新书写时先问是否需要帮助；仍可主动求助'}
              </small>
            </span>
            <input
              type="checkbox"
              checked={proactive}
              disabled={parentHelpMode === 'off' || helpSettingBusy || stage !== 'active'}
              onChange={(e) => setSessionHelpEnabled(e.target.checked)}
            />
          </label>
          <audio id="parent-audio" autoPlay />
        </aside>
      </div>
      {batchOpen && stage === 'active' && (
        <div className="batch-backdrop" role="presentation">
          <div className="batch-dialog" role="dialog" aria-modal="true" aria-label="拍摄作业">
            <div className="batch-heading">
              <div>
                <strong>{batchPurpose === 'submission' ? '提交作业给家长' : '拍摄作业批改'}</strong>
                <p>
                  {batchPages[0]?.selectedBox
                    ? '已冻结整张纸面，仅批改圈画题目。请核对圈画位置与截图。'
                    : batchPurpose === 'submission'
                      ? '放稳后自动拍照；摊开两页会分别保存。翻页继续，拍完核对页数后提交。'
                      : '放稳后自动拍照；摊开两页会分别保存。翻页继续，拍完即可批改。'}
                </p>
              </div>
              <button
                className="icon-button"
                disabled={batchSubmitting}
                onClick={clearBatchPages}
                aria-label="取消拍摄作业"
              >
                <X size={20} />
              </button>
            </div>
            <div className="batch-layout">
              <div className="batch-capture">
                {batchPages[0]?.selectedBox ? (
                  <div
                    className="selected-correction-preview"
                    style={{ aspectRatio: cameraAspect }}
                  >
                    <img src={batchPages[0].previewUrl} alt="本次批改的冻结截图" />
                    <SelectionInk strokes={batchPages[0].selectedStrokes || []} />
                  </div>
                ) : (
                  <video ref={batchPreviewRef} autoPlay playsInline muted />
                )}
                <div className="batch-actions">
                  <button
                    className="button secondary"
                    disabled={
                      batchCapturing ||
                      batchSubmitting ||
                      batchAwaitingApproval ||
                      (batchPurpose !== 'submission' && batchPages.length >= batchPageLimit)
                    }
                    onClick={() =>
                      batchPages[0]?.selectedBox ? clearBatchPages() : addBatchPage()
                    }
                  >
                    <RefreshCw size={17} />{' '}
                    {batchPages[0]?.selectedBox
                      ? '重新圈画'
                      : batchCapturing
                        ? '正在识别…'
                        : '重新识别'}
                  </button>
                  <VoiceControls
                    onText={handleVoice}
                    onError={(error) => onError(voiceErrorWithTranscriptionHint(error, dashboard))}
                    remoteTranscription={remoteTranscription}
                    disabled={batchSubmitting || voiceCall}
                    disabledLabel={voiceCall ? '通话中，语音指令已暂停' : '正在处理，请稍候'}
                    compact
                  />
                </div>
              </div>
              <div className="batch-queue">
                <h3>
                  {batchPages[0]?.selectedBox
                    ? '待批改题目 · 1 张截图'
                    : `${batchPurpose === 'submission' ? '待提交' : '待批改'}页面 · ${batchPages.length}/${batchPageLimit}`}
                </h3>
                {batchPages.length ? (
                  batchPages.map((page, index) => (
                    <div className="batch-page" key={page.id}>
                      <img src={page.previewUrl} alt={`${page.subject} ${page.label}截图预览`} />
                      <div>
                        <strong>
                          {index + 1}. {page.subject || '科目待识别'} · {page.label}
                        </strong>
                        <small>
                          {dashboard.tasks.find((task) => task.id === page.taskId)?.title ||
                            '未关联任务'}
                        </small>
                      </div>
                      <button
                        type="button"
                        disabled={batchSubmitting}
                        onClick={() => removeBatchPage(page.id)}
                        aria-label={`移除${page.subject}${page.label}`}
                      >
                        <X size={17} />
                      </button>
                    </div>
                  ))
                ) : (
                  <p className="muted">
                    请把作业放进画面并保持稳定。系统会自动识别；截图在提交前只留在本机内存。
                  </p>
                )}
                {batchProgress && (
                  <p className="batch-progress" role="status">
                    {batchProgress}
                  </p>
                )}
                <button
                  className="button primary wide"
                  disabled={
                    !batchPages.length || batchCapturing || batchSubmitting || batchAwaitingApproval
                  }
                  onClick={batchPages.length === 1 ? submitBatch : requestBatchSubmission}
                >
                  <ClipboardCheck size={18} />{' '}
                  {batchSubmitting
                    ? batchPurpose === 'submission'
                      ? '正在提交…'
                      : '正在逐页批改…'
                    : batchPages.length === 1
                      ? batchPurpose === 'submission'
                        ? '确认提交这张'
                        : '确认批改这张'
                      : `拍完了，${batchPurpose === 'submission' ? '提交给家长' : '开始批改'}（${batchPages.length} 页）`}
                </button>
                {batchConfirmOpen && !batchPages[0]?.selectedBox && (
                  <div className="batch-confirm" role="group" aria-label="确认已拍页面">
                    <strong>
                      已拍 {batchPages.length} 页，确认
                      {batchPurpose === 'submission' ? '提交给家长' : '批改'}这些截图？
                    </strong>
                    <div>
                      <button
                        className="button secondary"
                        onClick={() => {
                          setBatchConfirmOpen(false);
                          pendingBatchConfirm.current = 0;
                          batchWatchRef.current.moving = true;
                          batchWatchRef.current.stable = 0;
                        }}
                      >
                        继续拍照
                      </button>
                      <button className="button primary" onClick={submitBatch}>
                        {batchPurpose === 'submission' ? '确认提交' : '确认批改'}
                      </button>
                    </div>
                  </div>
                )}
                <small>
                  {batchPurpose === 'submission'
                    ? '提交后相机可关闭；任务需等家长确认才算完成。'
                    : correctionTestBypass
                      ? '测试模式无需家长授权；确认后直接批改，每页截图与对应结果一起呈现。'
                      : '提交前会再核对页数。家长授权后，每页截图与自己的批改结果一起呈现。'}
                </small>
              </div>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

createRoot(document.getElementById('root')).render(<App />);
