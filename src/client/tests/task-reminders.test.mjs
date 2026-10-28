import assert from 'node:assert/strict';
import test from 'node:test';
import { pendingTaskReminders, reminderSpeech } from '../src/task-reminders.js';

const now = Math.floor(new Date(2026, 9, 12, 18, 0).getTime() / 1000);

test('new and returned tasks take priority, while pending review stays quiet', () => {
  const tasks = [
    { id: 'new', title: '语文', status: 'todo', due_at: now + 7200 },
    { id: 'returned', title: '数学', status: 'needs_revision', latest_submission_id: 'attempt2', rejection_reason: '补写步骤' },
    { id: 'waiting', title: '英语', status: 'awaiting_parent' },
  ];
  const events = pendingTaskReminders(tasks, [], now);
  assert.equal(events[0].type, 'returned');
  assert.equal(events[1].type, 'new');
  assert.equal(events.some((event) => event.task.id === 'waiting'), false);
  assert.match(reminderSpeech(events[0]), /补写步骤/);
});

test('snooze suppresses all reminders until its deadline', () => {
  const tasks = [{ id: 'one', title: '数学', status: 'todo', due_at: now + 3000 }];
  const states = [{ task_id: 'one', event_key: 'new:one', snoozed_until: now + 1800, spoken_at: null }];
  assert.deepEqual(pendingTaskReminders(tasks, states, now), []);
  assert.equal(pendingTaskReminders(tasks, states, now + 1801)[0].type, 'new');
});

test('parent review delay does not make an on-time first submission overdue', () => {
  const task = { id: 'one', title: '试卷', status: 'needs_revision', latest_submission_id: 'a1',
    due_at: now - 300, first_submitted_at: now - 3600 };
  const events = pendingTaskReminders([task], [], now);
  assert.equal(events.some((event) => event.type === 'overdue'), false);
  assert.equal(events[0].type, 'returned');
});
