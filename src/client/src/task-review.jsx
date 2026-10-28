import { useEffect, useRef, useState } from 'react';
import { api, patch, post, privateImage } from './api';
import { WorkedSolution } from './worked-solution';

function SubmittedPage({ page, onUpdated, onError }) {
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  const [selectedCheck, setSelectedCheck] = useState(null);
  const [review, setReview] = useState(null);
  const [saving, setSaving] = useState(false);
  const openCheck = (check, index) => {
    setSelectedCheck({ check, index });
    setReview({ status: check.status, answer: check.answer || '', note: '' });
  };
  const saveReview = async () => {
    if (!selectedCheck || !review || saving) return;
    setSaving(true);
    try {
      const result = await patch(
        `/v1/submission-pages/${page.id}/checks/${selectedCheck.index}/parent-review`,
        {
          ...review,
          box: selectedCheck.check.box,
          revision: selectedCheck.check.parent_revision || 0,
        },
      );
      setSelectedCheck({ ...selectedCheck, check: result.check });
      await onUpdated();
    } catch (error) {
      onError(error);
    } finally {
      setSaving(false);
    }
  };
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = '';
    privateImage(`/v1/submission-pages/${page.id}/image`, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [page.id]);
  return (
    <figure className="task-submission-page">
      <div className="task-submission-image">
        {url ? (
          <img src={url} alt={`孩子提交的第 ${page.page_index} 页作业`} />
        ) : (
          <span>{error || '加载截图中…'}</span>
        )}
        {url &&
          page.grade_checks?.map(
            (check, index) =>
              check.box && (
                <button
                  key={index}
                  type="button"
                  className={`task-grade-marker ${check.status}`}
                  style={{
                    left: `${(check.box[0] + check.box[2] / 2) * 100}%`,
                    top: `${(check.box[1] + check.box[3] / 2) * 100}%`,
                  }}
                  onClick={() => openCheck(check, index)}
                  aria-label={`查看第 ${index + 1} 题批改`}
                >
                  {check.status === 'looks_correct'
                    ? '✓'
                    : check.status === 'check_again'
                      ? '×'
                      : check.status === 'needs_discussion'
                        ? '议'
                        : '?'}
                </button>
              ),
          )}
      </div>
      <figcaption>第 {page.page_index} 页</figcaption>
      {page.grade_status === 'pending' || page.grade_status === 'processing' ? (
        <small>完整批改中…</small>
      ) : (
        page.grade_summary && <small>{page.grade_summary}</small>
      )}
      {page.grade_checks?.map(
        (check, index) =>
          !check.box && (
            <button key={index} className="text-button" onClick={() => openCheck(check, index)}>
              {check.question || `第 ${index + 1} 题`}：查看分析
            </button>
          ),
      )}
      {selectedCheck && (
        <div className="task-grade-backdrop" onClick={() => setSelectedCheck(null)}>
          <div className="task-grade-dialog" onClick={(event) => event.stopPropagation()}>
            <button className="text-button" onClick={() => setSelectedCheck(null)}>
              关闭对照
            </button>
            <img src={url} alt={`第 ${page.page_index} 页原图`} />
            <div>
              <strong>{selectedCheck.check.question || '当前题目'}</strong>
              <p>
                孩子作答：
                {selectedCheck.check.answer ||
                  (selectedCheck.check.answer_state === 'blank'
                    ? '未作答（按错题计）'
                    : '未可靠识别')}
              </p>
              <p>纸面依据：{selectedCheck.check.evidence || '请对照原图核对'}</p>
              <p>批改理由：{selectedCheck.check.reason || '暂无'}</p>
              {selectedCheck.check.parent_review && (
                <p>
                  家长已校对：{selectedCheck.check.parent_review.note || '请以家长判断为准'}；原 AI
                  判断：{selectedCheck.check.original_review?.status || '见原记录'}
                </p>
              )}
              <WorkedSolution
                check={
                  selectedCheck.check.parent_review
                    ? selectedCheck.check.original_review || selectedCheck.check
                    : selectedCheck.check
                }
                cautionText={
                  selectedCheck.check.parent_review ? '原 AI 解答仅供对照，请以家长校对为准。' : ''
                }
              />
              <div className="task-grade-review">
                <strong>家长校对</strong>
                <label>
                  判断
                  <select
                    value={review?.status || 'unclear'}
                    onChange={(event) =>
                      setReview((old) => ({ ...old, status: event.target.value }))
                    }
                  >
                    <option value="looks_correct">看起来正确</option>
                    <option value="check_again">需要订正</option>
                    <option value="needs_discussion">适合讨论</option>
                    <option value="unclear">无法判断</option>
                  </select>
                </label>
                <label>
                  纸上答案
                  <input
                    value={review?.answer || ''}
                    onChange={(event) =>
                      setReview((old) => ({ ...old, answer: event.target.value }))
                    }
                  />
                </label>
                <label>
                  校对说明
                  <input
                    value={review?.note || ''}
                    maxLength={500}
                    onChange={(event) => setReview((old) => ({ ...old, note: event.target.value }))}
                    placeholder="更改判断或答案时请说明依据"
                  />
                </label>
                <button className="button mini" disabled={saving} onClick={saveReview}>
                  {saving ? '保存中…' : '保存家长校对'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </figure>
  );
}

function evidenceText(item) {
  if (item.type === 'hints') return `辅导：${item.question}；${item.response}`;
  if (item.type === 'observations')
    return `纸面观察：${item.label}${item.evidence ? `；${item.evidence}` : ''}`;
  if (item.type === 'corrections') return `批改：${item.summary}`;
  return `孩子自述：${item.text}`;
}

export function TaskReviewCard({ task, onUpdated, onError }) {
  const [open, setOpen] = useState(false);
  const [attempts, setAttempts] = useState([]);
  const [evidence, setEvidence] = useState([]);
  const [reason, setReason] = useState('');
  const [childFeedback, setChildFeedback] = useState('');
  const [draftBusy, setDraftBusy] = useState(false);
  const draftSource = useRef('');
  const [busy, setBusy] = useState(false);
  const [grading, setGrading] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (!open) return;
    let canceled = false;
    const load = () =>
      api(`/v1/tasks/${task.id}/submissions`)
        .then((result) => {
          if (!canceled) {
            setAttempts(result.submissions || []);
            setEvidence(result.evidence || []);
          }
        })
        .catch((error) => {
          if (!canceled) onError(error);
        });
    load();
    const timer = setInterval(load, 8000);
    return () => {
      canceled = true;
      clearInterval(timer);
    };
  }, [open, task.id, task.latest_submission_id, task.ai_status, task.status, task.version, reload]);
  const latest = attempts[0];
  const review = async (decision) => {
    if (!latest || busy) return;
    if (decision === 'rejected' && (!reason.trim() || !childFeedback.trim())) {
      onError(new Error('请写明退回原因和孩子可见的补做说明'));
      return;
    }
    setBusy(true);
    try {
      await post(`/v1/tasks/${task.id}/reviews`, {
        submission_id: latest.id,
        version: task.version,
        decision,
        reason: decision === 'rejected' ? reason.trim() : '',
        child_feedback: decision === 'rejected' ? childFeedback.trim() : '',
      });
      setReason('');
      setChildFeedback('');
      await onUpdated();
    } catch (error) {
      onError(error);
    } finally {
      setBusy(false);
    }
  };
  const suggestFeedback = async () => {
    if (!latest || !reason.trim() || draftBusy) return;
    const source = reason.trim();
    draftSource.current = source;
    setDraftBusy(true);
    try {
      const result = await post(`/v1/tasks/${task.id}/feedback-draft`, {
        submission_id: latest.id,
        reason: source,
      });
      if (draftSource.current === source) {
        if (result.draft) setChildFeedback(result.draft);
        else onError(new Error('退回原因还不够具体，请自行填写孩子可见的补做说明'));
      }
    } catch (error) {
      onError(error);
    } finally {
      setDraftBusy(false);
    }
  };
  const requestGrade = async (attempt) => {
    setGrading(attempt.id);
    try {
      await post(`/v1/tasks/${task.id}/submissions/${attempt.id}/grade`, {});
      const result = await api(`/v1/tasks/${task.id}/submissions`);
      setAttempts(result.submissions || []);
    } catch (error) {
      onError(error);
    } finally {
      setGrading('');
    }
  };
  return (
    <div className="task-review-card">
      <button className="button mini" onClick={() => setOpen((value) => !value)}>
        {open ? '收起提交依据' : '查看提交与审核'}
      </button>
      {open && (
        <div className="task-review-detail">
          {attempts.length ? (
            attempts.map((attempt) => (
              <section key={attempt.id} className="task-attempt">
                <strong>
                  第 {attempt.attempt} 次提交 ·{' '}
                  {new Date(attempt.submitted_at * 1000).toLocaleString('zh-CN')}
                </strong>
                <p>{attempt.note || '孩子没有补充说明'}</p>
                <p>
                  AI：
                  {attempt.ai_status === 'pending' || attempt.ai_status === 'processing'
                    ? '正在核对清晰度与明显遗漏…'
                    : attempt.ai_summary || '暂无建议；请人工查看。'}
                </p>
                {attempt.ai_details?.length > 0 && (
                  <ul>
                    {attempt.ai_details.map((item) => (
                      <li key={item.index}>
                        第 {item.index} 页：{item.note}
                      </li>
                    ))}
                  </ul>
                )}
                {attempt.pages.length > 0 && (
                  <button
                    className="button secondary mini"
                    disabled={
                      grading === attempt.id ||
                      attempt.pages.every((page) =>
                        ['pending', 'processing', 'ready'].includes(page.grade_status),
                      )
                    }
                    onClick={() => requestGrade(attempt)}
                  >
                    {grading === attempt.id ? '正在发起…' : '按需完整批改这次截图'}
                  </button>
                )}
                <div className="task-submission-pages">
                  {attempt.pages.map((page) => (
                    <SubmittedPage
                      key={page.id}
                      page={page}
                      onUpdated={() => setReload((value) => value + 1)}
                      onError={onError}
                    />
                  ))}
                </div>
                {!attempt.pages.length && <p>本次未附纸面截图；请结合说明或现场作业核对。</p>}
                {attempt.review_reason && <p>家长意见：{attempt.review_reason}</p>}
                {attempt.child_feedback && attempt.child_feedback !== attempt.review_reason && (
                  <p>孩子看到：{attempt.child_feedback}</p>
                )}
              </section>
            ))
          ) : (
            <p>加载提交记录中…</p>
          )}
          {evidence.length > 0 && (
            <details className="task-attempt">
              <summary>查看完整辅导与批改过程（{evidence.length} 条）</summary>
              <ul>
                {evidence.map((item) => (
                  <li key={`${item.type}:${item.id}`}>
                    {new Date(item.created_at * 1000).toLocaleString('zh-CN')} ·{' '}
                    {evidenceText(item)}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {task.status === 'awaiting_parent' && latest?.id === task.latest_submission_id && (
            <div className="task-review-actions">
              <button
                className="button primary mini"
                disabled={busy}
                onClick={() => review('accepted')}
              >
                确认完成
              </button>
              <label>
                退回原因
                <textarea
                  rows={2}
                  maxLength={500}
                  value={reason}
                  onChange={(event) => {
                    draftSource.current = '';
                    setReason(event.target.value);
                    setChildFeedback('');
                  }}
                  placeholder="例如：请补写第 3 题的演算步骤"
                />
              </label>
              <button
                className="button secondary mini"
                disabled={busy || draftBusy || !reason.trim()}
                onClick={suggestFeedback}
              >
                {draftBusy ? '正在拟写…' : '建议孩子可读说法'}
              </button>
              <label>
                孩子可见的补做说明（家长确认后发送）
                <textarea
                  rows={2}
                  maxLength={300}
                  value={childFeedback}
                  onChange={(event) => setChildFeedback(event.target.value)}
                  placeholder="例如：请先补写第 3 题的演算步骤，写好后再提交给我看。"
                />
              </label>
              <button
                className="button secondary mini"
                disabled={busy || !reason.trim() || !childFeedback.trim()}
                onClick={() => review('rejected')}
              >
                退回补做
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
