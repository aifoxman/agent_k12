function localDay(epochSeconds) {
  const date = new Date(epochSeconds * 1000);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

export function pendingTaskReminders(tasks, states, nowSeconds) {
  const stateByKey = new Map(states.map((state) => [`${state.task_id}:${state.event_key}`, state]));
  const day = localDay(nowSeconds);
  const startOfDay = new Date(`${day}T00:00:00`).getTime() / 1000;
  const open = tasks.filter((task) => ['todo', 'doing', 'needs_revision'].includes(task.status));
  const candidates = [];
  for (const task of open) {
    const taskStates = states.filter((state) => state.task_id === task.id);
    if (taskStates.some((state) => (state.snoozed_until || 0) > nowSeconds)) continue;
    const heardToday = taskStates.some((state) => state.spoken_at >= startOfDay);
    const events = [];
    if (task.status === 'needs_revision')
      events.push({
        key: `returned:${task.latest_submission_id || task.version}`,
        priority: 0,
        type: 'returned',
      });
    if (task.status === 'todo') events.push({ key: `new:${task.id}`, priority: 1, type: 'new' });
    if (
      task.due_at &&
      nowSeconds >= task.due_at &&
      (!task.first_submitted_at || task.first_submitted_at > task.due_at)
    )
      events.push({ key: `overdue:${day}`, priority: 2, type: 'overdue' });
    else if (task.due_at && task.due_at - nowSeconds <= 3600)
      events.push({ key: `due:${task.due_at}`, priority: 2, type: 'due' });
    if (!heardToday) events.push({ key: `daily:${day}`, priority: 3, type: 'daily' });
    for (const event of events) {
      const state = stateByKey.get(`${task.id}:${event.key}`);
      if (state?.spoken_at || (state?.snoozed_until || 0) > nowSeconds) continue;
      candidates.push({ ...event, task });
    }
  }
  return candidates.sort(
    (a, b) => a.priority - b.priority || (a.task.due_at || Infinity) - (b.task.due_at || Infinity),
  );
}

export function reminderSpeech(candidate, count = 1) {
  const { task, type } = candidate;
  if (type === 'returned')
    return count > 1
      ? `有${count}项作业被退回，请查看要补做的内容。`
      : `${task.title}需要继续完成。${task.rejection_reason || '请查看家长的意见。'}你可以说现在开始或稍后提醒。`;
  if (type === 'new')
    return count > 1
      ? `家长发布了${count}项新作业，请查看任务清单。`
      : `家长发布了${task.title}。你可以说现在开始或稍后提醒。`;
  if (type === 'due') return `${task.title}临近截止时间。你可以说现在开始或稍后提醒。`;
  if (type === 'overdue') return `${task.title}仍可补交，请安排完成。`;
  return count > 1
    ? `今天还有${count}项作业待完成，你想先做哪项？`
    : `今天还有${task.title}待完成。你可以说现在开始或稍后提醒。`;
}
