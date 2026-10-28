import { useEffect, useState } from 'react';
import { patch, post } from './api';

function inputDateTime(seconds) {
  if (!seconds) return '';
  const date = new Date(seconds * 1000);
  const two = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())}T${two(date.getHours())}:${two(date.getMinutes())}`;
}

export function TaskEditor({ task, onUpdated, onError }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(task.title);
  const [subject, setSubject] = useState(task.subject || '');
  const [dueAt, setDueAt] = useState(inputDateTime(task.due_at));
  const [cancelReason, setCancelReason] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) return;
    setTitle(task.title);
    setSubject(task.subject || '');
    setDueAt(inputDateTime(task.due_at));
  }, [open, task.title, task.subject, task.due_at]);
  const save = async () => {
    const deadline = Math.floor(new Date(dueAt).getTime() / 1000);
    if (!title.trim() || !Number.isFinite(deadline))
      return onError(new Error('请填写任务和截止时间'));
    setBusy(true);
    try {
      await patch(`/v1/tasks/${task.id}/details`, {
        version: task.version,
        title: title.trim(),
        subject: subject.trim() || null,
        due_at: deadline,
        timezone_offset_minutes: -new Date().getTimezoneOffset(),
      });
      setOpen(false);
      await onUpdated();
    } catch (error) {
      onError(error);
    } finally {
      setBusy(false);
    }
  };
  const cancel = async () => {
    if (!cancelReason.trim()) return onError(new Error('请说明取消原因'));
    setBusy(true);
    try {
      await post(`/v1/tasks/${task.id}/cancel`, {
        version: task.version,
        reason: cancelReason.trim(),
      });
      setOpen(false);
      await onUpdated();
    } catch (error) {
      onError(error);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="task-editor">
      <button className="text-button" onClick={() => setOpen((value) => !value)}>
        {open ? '收起任务设置' : '调整截止时间或取消任务'}
      </button>
      {open && (
        <div className="task-editor-form">
          <label>
            任务内容
            <input value={title} onChange={(event) => setTitle(event.target.value)} />
          </label>
          <label>
            科目
            <input value={subject} onChange={(event) => setSubject(event.target.value)} />
          </label>
          <label>
            截止时间
            <input
              type="datetime-local"
              value={dueAt}
              onChange={(event) => setDueAt(event.target.value)}
            />
          </label>
          <button className="button mini primary" disabled={busy} onClick={save}>
            保存调整
          </button>
          <label>
            取消原因
            <input
              value={cancelReason}
              maxLength={300}
              onChange={(event) => setCancelReason(event.target.value)}
              placeholder="例如：老师撤回这项作业"
            />
          </label>
          <button
            className="button mini secondary"
            disabled={busy || !cancelReason.trim()}
            onClick={cancel}
          >
            确认取消任务
          </button>
        </div>
      )}
    </div>
  );
}
