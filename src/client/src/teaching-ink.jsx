import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  chooseScratchPlacement,
  currentPageMarks,
  markBounds,
  pageMarkPath,
  placePageLabels,
  scratchLayout,
  scratchPath,
  scratchScrollFocus,
  scratchDiagramFocusBeats,
  scratchSnapshots,
  teachingPaperPresentation,
  teachingElementDelay,
  teachingPaperFrame,
  teachingPaperLayout,
  teachingProcessReserve,
  processOperationNotes,
} from './teaching-layout';

const PaperViewport = createContext(null);

function InkStroke({ className, d, style, ...attributes }) {
  const pathRef = useRef(null);
  useLayoutEffect(() => {
    const path = pathRef.current;
    if (!path || !className.includes('fresh')) return;
    const matrix = path.getScreenCTM();
    const scale = matrix ? Math.hypot(matrix.a, matrix.b) : 1;
    // Non-scaling SVG ink needs physical dash lengths, otherwise scaled drafts
    // show alternating gaps even when the writing animation has finished.
    path.style.setProperty('--ink-length', String(path.getTotalLength() * scale + 1));
  }, [className, d, style]);
  return <path ref={pathRef} className={className} d={d} style={style} {...attributes} />;
}

function useElementSize(ref, active = true, initialSize = { width: 1000, height: 750 }) {
  const [size, setSize] = useState(initialSize);
  useEffect(() => {
    const element = ref.current;
    if (!element || !active) return;
    const measure = () => {
      const rect = element.getBoundingClientRect();
      if (rect.width && rect.height)
        setSize((previous) =>
          previous.width === rect.width && previous.height === rect.height
            ? previous
            : { width: rect.width, height: rect.height },
        );
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(element);
    window.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [ref, active]);
  return size;
}

export function TeachingPaperFrame({
  target,
  beats = [],
  beatIndex = null,
  showMarks,
  onToggleMarks,
  children,
}) {
  const viewportRef = useRef(null);
  const toolbarRef = useRef(null);
  const summaryRef = useRef(null);
  const notesListRef = useRef(null);
  const notesBodyRef = useRef(null);
  const size = useElementSize(viewportRef);
  const toolbarSize = useElementSize(toolbarRef, true, { width: 200, height: 44 });
  const [wholePage, setWholePage] = useState(false);
  const [notesOpen, setNotesOpen] = useState(null);
  const [aspect, setAspect] = useState(4 / 3);
  const paperLayout = teachingPaperLayout({ aspect, box: target.box, fullPage: wholePage });
  const stackedQuestion =
    paperLayout === 'wide-question' && window.matchMedia('(orientation: portrait)').matches;
  const marks = currentPageMarks(beats, 2, beatIndex);
  const operations = showMarks
    ? processOperationNotes(
        marks.filter(
          (mark) =>
            mark.kind === 'note' && mark.anchor_id && mark.text && [...mark.text].length <= 60,
        ),
      )
    : [];
  const moveLabels = showMarks
    ? [
        ...new Set(
          marks
            .filter((mark) => mark.kind === 'arrow' && mark.purpose === 'move')
            .map((mark) => mark.label || '搬入示意'),
        ),
      ]
    : [];
  const hasOperations = operations.length > 0 || moveLabels.length > 0;
  const expandedNotes = notesOpen ?? !(wholePage || (size.height < 420 && !stackedQuestion));
  const summarySize = useElementSize(summaryRef, hasOperations, { width: 200, height: 44 });
  const notesSize = useElementSize(notesListRef, hasOperations && expandedNotes, {
    width: 200,
    height: 80,
  });
  const sideProcess = hasOperations && !stackedQuestion && size.height < 420 && size.width >= 540;
  const sideWidth = Math.min(280, Math.floor(size.width * 0.36));
  const notesReserve =
    hasOperations && !sideProcess
      ? teachingProcessReserve({
          height: size.height,
          summaryHeight: summarySize.height,
          contentHeight: stackedQuestion ? Math.min(notesSize.height, 88) : notesSize.height,
          expanded: expandedNotes,
        })
      : 0;
  const frame = teachingPaperFrame({
    ...size,
    width: sideProcess ? size.width - sideWidth - 20 : size.width,
    height: Math.max(1, size.height - notesReserve),
    aspect,
    box: target.box,
    marks,
    fullPage: wholePage,
    toolbarInset: toolbarSize.height + 20,
  });
  const notesRoom = Math.max(
    summarySize.height + 2,
    sideProcess
      ? size.height - frame.viewport.top - 8
      : size.height - frame.viewport.top - frame.viewport.height - 20,
  );
  const operationKey = operations
    .map((mark) => `${mark.beatIndex}:${mark.localIndex}:${mark.text}`)
    .join('|');
  useLayoutEffect(() => {
    const body = notesBodyRef.current;
    if (body && expandedNotes) body.scrollTop = body.scrollHeight;
  }, [operationKey, expandedNotes, notesRoom]);
  return (
    <div
      ref={viewportRef}
      className="hint-paper-guide frozen teaching-paper-frame"
      data-paper-view={wholePage ? 'whole' : 'question'}
      data-paper-layout={paperLayout}
      data-process-layout={sideProcess ? 'side' : 'below'}
    >
      <div
        className="teaching-paper-window"
        style={{
          width: frame.viewport.width,
          height: frame.viewport.height,
          marginTop: frame.viewport.top,
        }}
      >
        <div
          className="teaching-paper-surface"
          style={{
            left: frame.left,
            top: frame.top - frame.viewport.top,
            width: frame.width,
            height: frame.height,
          }}
          onLoad={(event) => {
            const image = event.target;
            if (image.tagName === 'IMG' && image.naturalWidth && image.naturalHeight)
              setAspect(image.naturalWidth / image.naturalHeight);
          }}
        >
          <PaperViewport.Provider value={frame.region}>{children}</PaperViewport.Provider>
        </div>
      </div>
      {hasOperations && (
        <details
          className="teaching-note-tray teaching-paper-process"
          open={expandedNotes}
          onToggle={(event) => {
            if (event.currentTarget.open !== expandedNotes) setNotesOpen(event.currentTarget.open);
          }}
          style={{
            maxHeight: notesRoom,
            width: sideProcess ? sideWidth : undefined,
            marginTop: sideProcess ? frame.viewport.top : undefined,
          }}
        >
          <summary ref={summaryRef}>原题解题过程</summary>
          <div
            ref={notesBodyRef}
            className="teaching-paper-process-body"
            style={{ maxHeight: Math.max(0, notesRoom - summarySize.height - 2) }}
          >
            <div ref={notesListRef}>
              {moveLabels.length > 0 && (
                <p className="teaching-move-key">箭头：{moveLabels.join('、')}</p>
              )}
              {operations.map((mark, index) => (
                <p
                  key={`${mark.beatIndex}-${mark.localIndex}-${mark.text}`}
                  className="teaching-page-note"
                  data-teaching-note-anchor={mark.anchor_id}
                  data-teaching-operation-current={index === operations.length - 1 || undefined}
                >
                  {mark.text}
                </p>
              ))}
            </div>
          </div>
        </details>
      )}
      <div ref={toolbarRef} className="teaching-paper-toolbar">
        <button
          type="button"
          onClick={() => setWholePage((value) => !value)}
          aria-pressed={wholePage}
        >
          {wholePage ? '聚焦当前题' : '查看整页'}
        </button>
        <button type="button" onClick={onToggleMarks} aria-pressed={!showMarks}>
          {showMarks ? '隐藏标记' : '显示标记'}
        </button>
      </div>
    </div>
  );
}

export function TeachingInk({
  target,
  beats = [],
  show = true,
  beatIndex = null,
  playbackVersion,
}) {
  const inkRef = useRef(null);
  const versionRef = useRef(undefined);
  const [revealed, setRevealed] = useState(null);
  const visibleBox = useContext(PaperViewport);
  const pageMarks = currentPageMarks(beats, 2, beatIndex);
  const hasInk = Boolean(target && (target.box || pageMarks.length));
  const { width, height } = useElementSize(inkRef, show && hasInk);
  const selectedBeat = beatIndex ?? beats.length - 1;
  useLayoutEffect(() => {
    if (playbackVersion === undefined || playbackVersion === null) return;
    if (versionRef.current !== playbackVersion) {
      versionRef.current = playbackVersion;
      setRevealed({ version: playbackVersion, beatIndex: selectedBeat });
    } else {
      setRevealed((previous) =>
        previous && previous.beatIndex !== selectedBeat ? null : previous,
      );
    }
  }, [playbackVersion, selectedBeat]);
  if (!target || !show || (!target.box && !pageMarks.length)) return null;
  const fresh = (mark) =>
    (beatIndex === null && mark.beatIndex === beats.length - 1) ||
    (revealed?.version === playbackVersion && revealed?.beatIndex === mark.beatIndex);
  const strokeKey = (mark) =>
    `${mark.beatIndex}-${mark.localIndex}-${fresh(mark) ? (revealed?.version ?? 'live') : 'still'}`;
  const lastCheck = pageMarks.findLastIndex((mark) => mark.kind === 'check');
  const labels = placePageLabels(
    pageMarks
      .filter(
        (mark, index) => mark.kind !== 'note' && (mark.kind !== 'check' || index === lastCheck),
      )
      .map((mark) =>
        mark.kind === 'arrow' && mark.purpose === 'move'
          ? { ...mark, kind: 'label', text: mark.label || '搬入示意', box: markBounds(mark) }
          : mark,
      ),
    width,
    height,
    {
      targetBox: target.box,
      visibleBox,
      protectedBoxes: (target.textAnchors || []).map((anchor) => anchor.box),
    },
  );
  return (
    <div
      ref={inkRef}
      className="teaching-ink"
      aria-label={`图上讲解：${target.label || '当前题目'}；暖色方框勾表示已找到条件`}
      role="img"
    >
      <svg className="teaching-page-ink" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
        {pageMarks.length === 0 && target.box && (
          <InkStroke
            className="teaching-target-stroke fresh"
            d={pageMarkPath({ kind: 'circle', box: target.box }, width, height)}
          />
        )}
        {pageMarks
          .filter((mark) => !['label', 'check', 'note'].includes(mark.kind))
          .map((mark) => (
            <InkStroke
              key={strokeKey(mark)}
              className={`teaching-page-stroke ${mark.purpose === 'move' ? 'move' : ''} ${fresh(mark) ? 'fresh' : mark.emphasized ? 'current' : 'previous'}`}
              d={pageMarkPath(mark, width, height)}
              style={{ animationDelay: `${teachingElementDelay(mark.animationIndex)}ms` }}
            />
          ))}
        {labels
          .filter((label) => label.leader)
          .map(({ leader, mark, index }) => (
            <path
              key={`note-leader-${mark.beatIndex}-${index}`}
              className="teaching-note-leader"
              d={`M ${leader.x1} ${leader.y1} L ${leader.x2} ${leader.y2}`}
            />
          ))}
      </svg>
      {labels
        .filter((label) => !label.docked)
        .map(({ x, y, width: labelWidth, text, mark, index }) => (
          <span
            key={`${strokeKey(mark)}-${index}-${text}`}
            className={`teaching-point-label ${mark.purpose === 'move' ? 'teaching-move-label' : ''} ${mark.kind === 'note' ? 'teaching-page-note' : ''} ${mark.kind === 'check' ? 'condition-check-label' : ''} ${fresh(mark) ? 'fresh' : ''} ${mark.emphasized ? '' : 'previous'}`}
            style={{
              left: x,
              top: y,
              width: mark.kind === 'note' ? labelWidth : undefined,
              animationDelay: `${teachingElementDelay(mark.animationIndex)}ms`,
            }}
          >
            {mark.kind === 'check' && (
              <span className="teaching-condition-box" aria-hidden="true" />
            )}
            {text}
          </span>
        ))}
    </div>
  );
}

export function TeachingStepControls({
  stepIndex = 0,
  stepCount = 1,
  totalKnown = true,
  title = '',
  label = '',
  busy = false,
  waiting = false,
  slow = false,
  hasNext = stepIndex + 1 < stepCount,
  canAdvance = !busy,
  onNext,
  onReplay,
  onSlower,
  onPause,
  onResume,
  onPrevious,
  onThinking,
  onRetry,
}) {
  return (
    <section className="teaching-step-controls" aria-label="分步跟学控制">
      <div className="teaching-step-controls-heading">
        <strong>{title || '分步跟学'}</strong>
        <span>
          {totalKnown
            ? `第 ${stepIndex + 1} / ${Math.max(stepIndex + 1, stepCount)} 步`
            : `第 ${stepIndex + 1} 步`}
        </span>
      </div>
      <p className="teaching-step-controls-status" aria-live="polite">
        {label ||
          (busy
            ? '老师会连贯讲完这一步，需要时可暂停'
            : waiting
              ? '已停下，准备好后继续讲解'
              : '准备好再继续')}
      </p>
      <div className="teaching-step-controls-actions">
        {onPrevious && (
          <button
            className="teaching-step-previous"
            type="button"
            aria-label="上一步"
            title="上一步"
            disabled={stepIndex === 0}
            onClick={onPrevious}
          >
            ←
          </button>
        )}
        {waiting ? (
          <button
            type="button"
            className="teaching-step-resume"
            aria-label="继续讲解"
            disabled={!onResume}
            onClick={onResume}
          >
            继续
          </button>
        ) : (
          busy && (
            <button
              type="button"
              className="teaching-step-pause"
              aria-label="暂停讲解"
              disabled={!onPause}
              onClick={onPause}
            >
              暂停
            </button>
          )
        )}
        <button
          type="button"
          className="teaching-step-next"
          aria-label="下一步"
          title="下一步"
          disabled={!canAdvance || !hasNext || !onNext}
          onClick={onNext}
        >
          →
        </button>
        <button type="button" aria-label="再讲这步" disabled={busy || !onReplay} onClick={onReplay}>
          重讲
        </button>
        <button type="button" aria-pressed={slow} disabled={!onSlower} onClick={onSlower}>
          慢一点
        </button>
        {(onThinking || onRetry) && (
          <button type="button" className="teaching-depth-action" onClick={onRetry || onThinking}>
            {onRetry ? '重试本层' : '解题思考'}
          </button>
        )}
      </div>
    </section>
  );
}

export function TeachingScratch({
  target = null,
  beats = [],
  show = true,
  inline = false,
  playbackBeatIndex = null,
  playbackVersion,
  paperVisible = false,
}) {
  const panelRef = useRef(null);
  const bodyRef = useRef(null);
  const followRef = useRef(true);
  const automaticScrollRef = useRef(null);
  const navigateRef = useRef(false);
  const playbackVersionRef = useRef(undefined);
  const [placement, setPlacement] = useState({ x: null, y: null, docked: false });
  const visibleBeats = teachingPaperPresentation(
    Number.isFinite(playbackBeatIndex) ? beats.slice(0, playbackBeatIndex + 1) : beats,
    { paperVisible },
  );
  const hasScratch = visibleBeats.some((beat) => beat.scratch?.length);
  const { width: panelWidth } = useElementSize(panelRef, show && hasScratch);
  const { height: bodyHeight } = useElementSize(bodyRef, show && hasScratch);
  const width = Math.min(320, Math.max(180, panelWidth - 32));
  const snapshots = scratchSnapshots(visibleBeats, width);
  const hasFigures = snapshots.some((snapshot) =>
    snapshot.content.diagram.some((element) => element.kind !== 'text'),
  );
  const stepIndex = snapshots.length - 1;
  const currentStep = snapshots[stepIndex];
  // Keep every released reason on the sheet. The caption and available height
  // must never replace written work with a disclosure button or remove history.
  const content = currentStep?.content || scratchLayout([], width);
  const freshBeat = !inline && playbackBeatIndex === null ? currentStep?.beatIndex : -1;
  const focusKind = scratchScrollFocus(content, currentStep?.beatIndex);
  const focusBeat = currentStep?.beatIndex;
  const focusDiagramBeats = scratchDiagramFocusBeats(content, focusBeat);
  const proseHeight = content.proseSteps.reduce(
    (sum, step) =>
      sum +
      26 +
      step.elements.reduce(
        (height, element) =>
          height +
          Math.ceil([...element.text].length / Math.max(8, Math.floor((panelWidth - 32) / 16))) *
            26,
        0,
      ),
    0,
  );
  const targetKey = target?.box?.join(':') || '';

  useLayoutEffect(() => {
    const previous = playbackVersionRef.current;
    playbackVersionRef.current = { version: playbackVersion, beatIndex: playbackBeatIndex };
    // Speech releases a new version for every beat. Keep a child's manual
    // reading position through that ordinary progress; explicit review jumps
    // and replaying an already selected beat may return to their written work.
    const requestedReview =
      previous &&
      Number.isFinite(playbackBeatIndex) &&
      (!Number.isFinite(previous.beatIndex) ||
        playbackBeatIndex < previous.beatIndex ||
        playbackBeatIndex > previous.beatIndex + 1 ||
        (playbackBeatIndex === previous.beatIndex && previous.version !== playbackVersion));
    if (requestedReview) {
      followRef.current = true;
      navigateRef.current = 'playback';
    }
  }, [playbackVersion, playbackBeatIndex]);

  useLayoutEffect(() => {
    const body = bodyRef.current;
    if (!body || (!followRef.current && !navigateRef.current)) return;
    const diagram = body.querySelector('.teaching-scratch-diagram');
    const paragraph = body.querySelector(`[data-teaching-beat="${currentStep?.beatIndex}"]`);
    const current = focusKind === 'diagram' ? diagram : paragraph;
    let currentRect = current?.getBoundingClientRect();
    if (current === diagram && currentRect?.height > body.clientHeight - 24) {
      const parts = [...diagram.querySelectorAll('[data-teaching-focus="current"]')].map(
        (element) => element.getBoundingClientRect(),
      );
      if (parts.length) currentRect = { top: Math.min(...parts.map((rect) => rect.top)) - 2 };
    }
    // A newly drawn diagram follows its top; a later explanation follows its
    // own paragraph even when older drawings remain below the prose history.
    const desired = currentRect
      ? currentRect.top -
        body.getBoundingClientRect().top +
        body.scrollTop -
        parseFloat(getComputedStyle(body).paddingTop || '0')
      : body.scrollHeight;
    navigateRef.current = false;
    const top = Math.max(0, Math.min(body.scrollHeight - body.clientHeight, desired));
    automaticScrollRef.current = Math.abs(body.scrollTop - top) > 0.5 ? top : null;
    body.scrollTop = top;
  }, [
    beats.length,
    stepIndex,
    content.height,
    content.viewBox,
    playbackVersion,
    playbackBeatIndex,
    bodyHeight,
  ]);

  useEffect(() => {
    if (!show || !hasScratch || inline) return;
    const panel = panelRef.current;
    const host = panel?.closest('.paper-panel');
    if (!host) return;
    const measure = () => {
      const guide = host.querySelector('.hint-paper-guide');
      const reference = guide || host.querySelector('.camera-media');
      if (!reference) return;
      const hostBox = host.getBoundingClientRect();
      const paperBox = reference.getBoundingClientRect();
      const [x, y, w, h] = target?.box || [0, 0, 1, 1];
      // A saved target belongs to the frozen guide. Never apply its old page
      // coordinates to the current live camera when that guide is absent.
      const targetRect = guide
        ? {
            x: paperBox.left - hostBox.left + x * paperBox.width - 8,
            y: paperBox.top - hostBox.top + y * paperBox.height - 8,
            width: w * paperBox.width + 16,
            height: h * paperBox.height + 16,
          }
        : null;
      const avoid = [
        ...document.querySelectorAll(
          '.speech-caption, .paper-header, .camera-controls, .study-timer-wrap, .hint-paper-return, .teaching-marks-toggle, .learning-rest-offer, .teaching-step-controls',
        ),
      ]
        .filter((element) => element.getClientRects().length)
        .map((element) => {
          const rect = element.getBoundingClientRect();
          return {
            x: rect.left - hostBox.left,
            y: rect.top - hostBox.top,
            width: rect.width,
            height: rect.height,
          };
        });
      const result = chooseScratchPlacement({
        width: hostBox.width,
        height: hostBox.height,
        panelWidth: panel.offsetWidth,
        panelHeight: Math.min(panel.offsetHeight || 360, Math.max(220, hostBox.height * 0.48)),
        target: targetRect,
        avoid,
      });
      setPlacement((previous) =>
        previous.x === result.x && previous.y === result.y && previous.docked === result.docked
          ? previous
          : result,
      );
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(host);
    const camera = host.querySelector('.camera-media');
    if (camera) observer?.observe(camera);
    const caption = document.querySelector('.speech-caption');
    if (caption) observer?.observe(caption);
    const structure =
      typeof MutationObserver === 'undefined' ? null : new MutationObserver(measure);
    structure?.observe(host, { childList: true, subtree: true });
    window.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      structure?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [targetKey, hasScratch, show, beats.length, inline]);

  if (!show || !hasScratch) return null;
  return (
    <section
      ref={panelRef}
      className={`teaching-scratch ${placement.docked ? 'docked' : ''} ${inline ? 'teaching-scratch-inline' : ''}`}
      style={inline || placement.x === null ? undefined : { left: placement.x, top: placement.y }}
      aria-label="老师逐步写成的草稿"
      data-teaching-scratch-focus={focusKind}
      data-teaching-scratch-space={
        (content.diagram.length ? content.height : 0) + proseHeight <= 138 ? 'compact' : 'roomy'
      }
    >
      <div className="teaching-scratch-heading">
        <div>
          <strong>思考草稿</strong>
          {target?.label && (
            <small className="teaching-scratch-context">题目：{target.label}</small>
          )}
        </div>
        <span>{hasFigures ? '局部示意' : '思路记录'}</span>
      </div>
      <div
        className="teaching-scratch-body"
        ref={bodyRef}
        onScroll={(event) => {
          const element = event.currentTarget;
          const automaticTop = automaticScrollRef.current;
          automaticScrollRef.current = null;
          if (automaticTop !== null && Math.abs(element.scrollTop - automaticTop) <= 1) return;
          followRef.current = element.scrollHeight - element.scrollTop - element.clientHeight <= 24;
        }}
      >
        {content.proseSteps.map((step) => (
          <div
            className="teaching-scratch-step"
            key={step.beatIndex}
            data-teaching-beat={step.beatIndex}
            aria-current={step.beatIndex === currentStep?.beatIndex ? 'step' : undefined}
          >
            <small>思路</small>
            {step.elements.map((element) => (
              <p
                key={element.localIndex}
                className={`teaching-scratch-line ${element.beatIndex === freshBeat ? 'fresh' : ''}`}
                style={{ animationDelay: `${teachingElementDelay(element.localIndex)}ms` }}
              >
                {element.text}
              </p>
            ))}
          </div>
        ))}
        {content.diagram.length > 0 && (
          <svg
            className="teaching-scratch-diagram"
            data-text-only={
              content.diagram.every((element) => element.kind === 'text') || undefined
            }
            viewBox={content.viewBox}
            style={{ height: content.height }}
            role="img"
            aria-label="与步骤对应的辅助图；文字和图形位置一致"
          >
            {content.diagram.map((element) => {
              const className = `${element.kind === 'text' ? 'teaching-scratch-label' : 'teaching-scratch-stroke'} ${element.beatIndex === freshBeat ? 'fresh' : ''}`;
              const style = { animationDelay: `${teachingElementDelay(element.localIndex)}ms` };
              return element.kind === 'text' ? (
                <g key={`${element.beatIndex}-${element.localIndex}`}>
                  {element.leader && (
                    <>
                      <path
                        className="teaching-scratch-leader"
                        d={element.leader.points
                          .map((point, index) => `${index ? 'L' : 'M'} ${point.x} ${point.y}`)
                          .join(' ')}
                        data-teaching-diagram-beat={element.beatIndex}
                        data-teaching-focus={
                          focusDiagramBeats.includes(element.beatIndex) ? 'current' : undefined
                        }
                      />
                      <circle
                        className="teaching-scratch-anchor"
                        cx={element.leader.x2}
                        cy={element.leader.y2}
                        r={content.fontSize * 0.12}
                        data-teaching-diagram-beat={element.beatIndex}
                        data-teaching-focus={
                          focusDiagramBeats.includes(element.beatIndex) ? 'current' : undefined
                        }
                      />
                    </>
                  )}
                  <text
                    className={className}
                    x={element.x}
                    y={element.y}
                    textAnchor={element.centered ? 'middle' : undefined}
                    dominantBaseline={element.centered ? 'central' : undefined}
                    fontSize={content.fontSize}
                    data-teaching-diagram-beat={element.beatIndex}
                    data-teaching-focus={
                      focusDiagramBeats.includes(element.beatIndex) ? 'current' : undefined
                    }
                    style={style}
                  >
                    {element.lines.map((line, index) => (
                      <tspan key={index} x={element.x} dy={index ? content.fontSize * 1.3 : 0}>
                        {line}
                      </tspan>
                    ))}
                  </text>
                </g>
              ) : (
                <InkStroke
                  key={`${element.beatIndex}-${element.localIndex}`}
                  className={className}
                  d={scratchPath(element)}
                  style={style}
                  data-teaching-diagram-beat={element.beatIndex}
                  data-teaching-focus={
                    focusDiagramBeats.includes(element.beatIndex) ? 'current' : undefined
                  }
                />
              );
            })}
          </svg>
        )}
        {!followRef.current && (
          <small className="teaching-scratch-reading">可向下滑动查看新步骤</small>
        )}
      </div>
    </section>
  );
}
