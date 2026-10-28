function DiagramElement({ element }) {
  if (element.kind === 'line' || element.kind === 'arrow') {
    const { x1, y1, x2, y2 } = element;
    const angle = Math.atan2(y2 - y1, x2 - x1);
    const arrow =
      element.kind === 'arrow' ? (
        <path
          d={`M ${x2 - 4 * Math.cos(angle - 0.55)} ${y2 - 4 * Math.sin(angle - 0.55)} L ${x2} ${y2} L ${x2 - 4 * Math.cos(angle + 0.55)} ${y2 - 4 * Math.sin(angle + 0.55)}`}
        />
      ) : null;
    return (
      <g className="worked-diagram-ink">
        <line x1={x1} y1={y1} x2={x2} y2={y2} />
        {arrow}
      </g>
    );
  }
  if (element.kind === 'rect') {
    return (
      <rect
        className="worked-diagram-shape"
        x={element.x}
        y={element.y}
        width={element.w}
        height={element.h}
      />
    );
  }
  if (element.kind === 'circle') {
    return <circle className="worked-diagram-shape" cx={element.x} cy={element.y} r={element.r} />;
  }
  if (element.kind === 'text') {
    return (
      <text className="worked-diagram-text" x={element.x} y={element.y} textAnchor="middle">
        {element.text}
      </text>
    );
  }
  return null;
}

function SolutionDiagram({ diagram }) {
  if (!diagram?.elements?.length) return null;
  return (
    <figure className="worked-diagram">
      <svg viewBox="0 0 100 100" role="img" aria-label={diagram.description}>
        {diagram.elements.map((element, index) => (
          <DiagramElement key={index} element={element} />
        ))}
      </svg>
      <figcaption>
        <strong>{diagram.caption || '解题示意图'}</strong>
        <span>{diagram.description}</span>
        <small>示意图不代表原题原图，也不按比例绘制。</small>
      </figcaption>
    </figure>
  );
}

export function WorkedSolution({ check, compact = false, cautionText = '' }) {
  const steps = Array.isArray(check.solution_steps) ? check.solution_steps : [];
  const structured = Boolean(
    check.reference_answer ||
    check.solution_idea ||
    steps.length ||
    check.comparison ||
    check.diagram,
  );
  if (!structured && !check.explanation) return null;
  return (
    <div className={`worked-solution${compact ? ' compact' : ''}`}>
      {cautionText && <small className="worked-solution-caution">{cautionText}</small>}
      {structured ? (
        <>
          {check.reference_answer && (
            <div className="worked-answer">
              <span>
                {check.status === 'needs_discussion'
                  ? 'AI 参考表达 · 待核对'
                  : 'AI 参考解答 · 待核对'}
              </span>
              <strong>{check.reference_answer}</strong>
            </div>
          )}
          {check.solution_idea && (
            <div className="worked-explanation-block">
              <strong>解题思路</strong>
              <p>{check.solution_idea}</p>
            </div>
          )}
          {steps.length > 0 && (
            <div className="worked-explanation-block">
              <strong>解题步骤</strong>
              <ol>
                {steps.map((step, index) => (
                  <li key={index}>{step}</li>
                ))}
              </ol>
            </div>
          )}
          <SolutionDiagram diagram={check.diagram} />
          {check.comparison && (
            <div className="worked-comparison">
              <strong>与孩子作答对照</strong>
              <p>{check.comparison}</p>
            </div>
          )}
        </>
      ) : (
        <div className="worked-explanation-block">
          <strong>解题分析</strong>
          <p>{check.explanation}</p>
        </div>
      )}
    </div>
  );
}
