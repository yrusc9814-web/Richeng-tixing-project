/**
 * Phase57 — LifeSync Hub Schedule Management Frontend
 *
 * Reuses existing task CRUD and sync APIs.
 * Uses PATCH status=cancelled for archive — no default DELETE.
 */

// ── Constants ────────────────────────────────────────────────────────────
const API_BASE = '';
const API_TOKEN = window.__API_TOKEN__ || 'test-token-hermes-local-4.3';
const AUTH_HEADERS = {
  'Authorization': `Bearer ${API_TOKEN}`,
  'Content-Type': 'application/json',
};

const STATUS_SYNC_LABELS = {
  pending: '待同步',
  in_progress: '同步中',
  synced: '已同步',
  failed: '同步失败',
  failed_permanent: '同步永久失败',
  skipped: '已跳过',
  stale: '已过期',
  orphaned: '已孤立',
  disabled: '已禁用',
  deleted: '已删除',
  not_synced: '未同步',
};

const TASK_STATUS_LABELS = {
  pending: '待办',
  completed: '已完成',
  cancelled: '已取消',
};

const SCHEDULE_TYPE_LABELS = {
  plan: '普通计划',
  action: '行动任务',
  risk: '风险事项',
  critical: '关键事项',
};

const NOTIFY_POLICY_LABELS = {
  silent: '不主动提醒',
  calendar_only: '仅日历',
  wechat_normal: '普通微信',
  wechat_important: '重要微信',
  wechat_emergency: '强提醒',
};

let currentTasks = [];
let currentTab = 'all';
let currentPage = 0;
const PAGE_SIZE = 50;
let searchQuery = '';
let currentFormMode = 'view'; // 'view', 'edit', 'create'
let selectedTaskId = null;
let selectedDate = null; // ISO date string (YYYY-MM-DD) or null
let hasRenderedTaskCards = false;

// ── Utility Functions ────────────────────────────────────────────────────

function $(sel, ctx) { return (ctx || document).querySelector(sel); }
function $$(sel, ctx) { return Array.from((ctx || document).querySelectorAll(sel)); }

function formatDatetime(iso) {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch {
    return iso;
  }
}

function escapeHtml(text) {
  if (text == null) return '';
  const d = document.createElement('div');
  d.textContent = String(text);
  return d.innerHTML;
}

function showToast(message, type) {
  type = type || 'info';
  const container = $('#toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s';
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

function showLoading(show) {
  $('#loading-overlay').classList.toggle('show', show);
}

function showModal(show) {
  $('#modal-overlay').classList.toggle('show', show);
}

function getStatusChipHtml(status, type) {
  const labelMap = type === 'sync' ? STATUS_SYNC_LABELS : TASK_STATUS_LABELS;
  const label = labelMap[status] || status || '未知';
  const cls = status ? `status-chip ${status}` : 'status-chip';
  return `<span class="${cls}">${escapeHtml(label)}</span>`;
}

function getSyncTargetLabel(target) {
  const map = {
    apple_calendar: 'Apple日历',
    apple_reminder: 'Apple提醒',
  };
  return map[target] || target || '-';
}

function getScheduleTypeLabel(value) {
  return SCHEDULE_TYPE_LABELS[value] || value || '-';
}

function getNotifyPolicyLabel(value) {
  return NOTIFY_POLICY_LABELS[value] || value || '-';
}

function getSyncStatusHint(status) {
  if (status === 'failed' || status === 'failed_permanent') {
    return '同步失败，需要重新同步；已保留在本地，等待处理';
  }
  if (status === 'stale') {
    return '本地内容已更新，需要重新同步';
  }
  if (status === 'pending' || status === 'in_progress') {
    return '等待同步到目标日历';
  }
  return '';
}

function getFailedDetailHtml(status, syncStatus) {
  if (syncStatus === 'failed' || syncStatus === 'failed_permanent') {
    return '<div class="failed-detail-box">'
      + '<div class="failed-detail-title">⚠️ 同步失败</div>'
      + '<div class="failed-detail-line">需要重新同步</div>'
      + '<div class="failed-detail-line">已保留在本地，等待处理</div>'
      + '<div class="failed-detail-note">系统将在下次同步周期自动重试，当前不影响本地操作</div>'
      + '</div>';
  }
  if (syncStatus === 'stale') {
    return '<div class="failed-detail-box">'
      + '<div class="failed-detail-title">🔄 内容已过期</div>'
      + '<div class="failed-detail-line">本地内容已更新，需要重新同步</div>'
      + '<div class="failed-detail-note">编辑已保存到本地数据库，将安排重新同步到日历</div>'
      + '</div>';
  }
  return '';
}

async function apiFetch(path, options) {
  options = options || {};
  const url = `${API_BASE}${path}`;
  const resp = await fetch(url, {
    ...options,
    headers: { ...AUTH_HEADERS, ...(options.headers || {}) },
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      if (body.detail) detail = body.detail;
    } catch {}
    throw new Error(detail);
  }
  if (resp.status === 204) return null;
  return resp.json();
}

async function fetchTasks(params) {
  const qs = params ? '?' + new URLSearchParams(params).toString() : '';
  return apiFetch(`/api/tasks${qs}`);
}

async function fetchTask(taskId) {
  return apiFetch(`/api/tasks/${taskId}`);
}

async function createTask(data) {
  return apiFetch('/api/tasks', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

async function updateTask(taskId, data) {
  return apiFetch(`/api/tasks/${taskId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

async function completeTask(taskId) {
  return apiFetch(`/api/tasks/${taskId}/complete`, { method: 'POST' });
}

async function fetchSyncStatus(taskId) {
  return apiFetch(`/api/sync/tasks/${taskId}/status`);
}

async function fetchSystemStatus() {
  return apiFetch('/api/system/status');
}

async function fetchSyncEngineStats() {
  return apiFetch('/api/system/sync-engine/stats');
}

async function fetchReminderSummary() {
  return apiFetch('/api/local/reminders/summary');
}

async function fetchWeatherOverview() {
  return apiFetch('/api/local/weather/overview');
}

async function evaluateLifeSyncWeather(taskId) {
  return apiFetch('/api/weather/evaluate', {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId }),
  });
}

async function sendLifeSyncWeChat(taskId) {
  return apiFetch('/api/wechat/send', {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId, trigger_type: 'manual' }),
  });
}

async function syncLifeSyncCalendar(taskId) {
  return apiFetch('/api/calendar/sync', {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId, sync_target: 'apple_calendar' }),
  });
}

function renderLifeSyncTaskSelect() {
  const select = $('#lifesync-task-select');
  if (!select) return;
  const active = currentTasks.filter(t => t.status !== 'cancelled');
  if (active.length === 0) {
    select.innerHTML = '<option value="">暂无可操作任务</option>';
    return;
  }
  const current = select.value;
  select.innerHTML = active.map(t => `<option value="${escapeHtml(t.task_id)}">${escapeHtml(t.title)} · ${formatDatetime(t.start_time || t.due_time)}</option>`).join('');
  if (current && active.some(t => t.task_id === current)) select.value = current;
}

function renderLifeSyncResult(payload) {
  const node = $('#lifesync-result');
  if (!node) return;
  node.textContent = typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2);
}

async function runLifeSyncAction(action) {
  const select = $('#lifesync-task-select');
  const taskId = select && select.value;
  if (!taskId) {
    showToast('请先选择任务', 'warning');
    return;
  }
  showLoading(true);
  try {
    let result;
    if (action === 'weather') {
      result = await evaluateLifeSyncWeather(taskId);
    } else if (action === 'wechat') {
      result = await sendLifeSyncWeChat(taskId);
    } else if (action === 'calendar') {
      result = await syncLifeSyncCalendar(taskId);
    } else {
      throw new Error(`未知动作: ${action}`);
    }
    renderLifeSyncResult(result);
    showToast(result.ok ? '闭环动作已执行' : `闭环动作受阻: ${result.error || result.reason || result.status}`, result.ok ? 'success' : 'warning');
    await refreshTasks();
  } catch (err) {
    renderLifeSyncResult({ ok: false, error: err.message });
    showToast('闭环动作失败: ' + err.message, 'error');
  } finally {
    showLoading(false);
  }
}

// ── Task Filtering/Grouping ──────────────────────────────────────────────

function getTodayRange() {
  const now = new Date();
  const tz = 'Asia/Shanghai';
  const start = new Date(now.toLocaleDateString('en-CA', { timeZone: tz }));
  const end = new Date(start);
  end.setDate(end.getDate() + 1);
  return { start: start.toISOString(), end: end.toISOString() };
}

function isToday(isoStr) {
  if (!isoStr) return false;
  const today = getTodayRange();
  return isoStr >= today.start && isoStr < today.end;
}

function isUpcoming(isoStr) {
  if (!isoStr) return false;
  return isoStr > new Date().toISOString();
}

function isFuture(isoStr) {
  if (!isoStr) return false;
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  return isoStr > tomorrow.toISOString();
}

function filterTasks(tab) {
  let filtered = [...currentTasks];

  // Apply tab filter
  if (tab === 'today') {
    filtered = filtered.filter(t => isToday(t.start_time) || isToday(t.due_time));
    filtered.sort((a, b) => (a.start_time || a.due_time || '').localeCompare(b.start_time || b.due_time || ''));
  } else if (tab === 'upcoming') {
    filtered = filtered.filter(t => (t.start_time && isUpcoming(t.start_time)) || (t.due_time && isUpcoming(t.due_time)));
    filtered.sort((a, b) => (a.start_time || a.due_time || '').localeCompare(b.start_time || b.due_time || ''));
  } else if (tab === 'future') {
    filtered = filtered.filter(t => (t.start_time && isFuture(t.start_time)) || (t.due_time && isFuture(t.due_time)));
    filtered.sort((a, b) => (a.start_time || a.due_time || '').localeCompare(b.start_time || b.due_time || ''));
  } else if (tab === 'archived') {
    filtered = filtered.filter(t => t.status === 'cancelled');
    filtered.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
  } else if (tab === 'sync-error') {
    filtered = filtered.filter(t => ['failed', 'failed_permanent', 'stale'].includes(t.last_sync_status));
  } else if (tab === 'failed') {
    filtered = filtered.filter(t => ['failed', 'failed_permanent'].includes(t.last_sync_status));
  } else if (tab === 'pending-sync') {
    filtered = filtered.filter(t => ['pending', 'in_progress'].includes(t.last_sync_status));
  } else if (tab === 'synced') {
    filtered = filtered.filter(t => t.last_sync_status === 'synced');
  } else if (tab === 'stale') {
    filtered = filtered.filter(t => t.last_sync_status === 'stale');
  } else {
    // 'all' — exclude archived by default
    filtered = filtered.filter(t => t.status !== 'cancelled');
    filtered.sort((a, b) => (a.start_time || a.due_time || '').localeCompare(b.start_time || b.due_time || ''));
  }

  // Apply selected date filter (from calendar mini click)
  if (selectedDate) {
    filtered = filtered.filter(t => {
      const taskDate = (t.start_time || t.due_time || '').substring(0, 10);
      return taskDate === selectedDate;
    });
  }

  // Apply search
  if (searchQuery) {
    const q = searchQuery.toLowerCase();
    filtered = filtered.filter(t =>
      t.title.toLowerCase().includes(q) ||
      (t.description && t.description.toLowerCase().includes(q)) ||
      (t.location && t.location.toLowerCase().includes(q))
    );
  }

  return filtered;
}

function computeStats() {
  const active = currentTasks.filter(t => t.status !== 'cancelled');
  const all = active.length;
  const today = active.filter(t => isToday(t.start_time) || isToday(t.due_time)).length;
  const upcoming = active.filter(t => (t.start_time && isUpcoming(t.start_time)) || (t.due_time && isUpcoming(t.due_time))).length;
  const future = active.filter(t => (t.start_time && isFuture(t.start_time)) || (t.due_time && isFuture(t.due_time))).length;
  return { all, today, upcoming, future };
}

// ── Render Functions ─────────────────────────────────────────────────────

function renderStats() {
  const stats = computeStats();
  $('#stat-all').textContent = stats.all;
  $('#stat-today').textContent = stats.today;
  $('#stat-upcoming').textContent = stats.upcoming;
  $('#stat-future').textContent = stats.future;
}

function renderTable() {
  const filtered = filterTasks(currentTab);
  const tbody = $('#table-body');
  const empty = $('#empty-state');

  if (filtered.length === 0) {
    tbody.innerHTML = '';
    empty.style.display = 'flex';
    return;
  }
  empty.style.display = 'none';

  tbody.innerHTML = filtered.map(task => {
    const syncStatus = task.last_sync_status || 'not_synced';
    const syncTargets = task.sync_enabled ? (task.sync_targets || []).map(getSyncTargetLabel).join(', ') || '-' : '-';

    return `
      <tr class="${task.task_id === selectedTaskId ? 'selected' : ''}" onclick="selectTaskRow('${task.task_id}')">
        <td><input type="checkbox" class="task-select" aria-label="选择日程 ${escapeHtml(task.title)}" onclick="event.stopPropagation()"></td>
        <td class="task-title" title="${escapeHtml(task.title)}">${escapeHtml(task.title)}</td>
        <td class="time-cell">${formatDatetime(task.start_time)}</td>
        <td class="time-cell">${formatDatetime(task.due_time)}</td>
        <td>${getStatusChipHtml(syncStatus, 'sync')}</td>
        <td style="font-size:12px">${escapeHtml(syncTargets)}</td>
        <td class="actions-cell">
          <button class="btn-icon" onclick="event.stopPropagation(); viewTask('${task.task_id}')" title="查看">👁</button>
          <button class="btn-icon" onclick="event.stopPropagation(); editTask('${task.task_id}')" title="编辑">✏️</button>
          <button class="btn-icon" onclick="event.stopPropagation(); archiveTask('${task.task_id}')" title="归档">📦</button>
        </td>
      </tr>
    `;
  }).join('');

  renderPagination(filtered.length);
}

function selectTaskRow(taskId) {
  selectedTaskId = taskId;
  renderTable();
}

function renderPagination(total) {
  const info = $('#pagination-info');
  const start = currentPage * PAGE_SIZE + 1;
  const end = Math.min((currentPage + 1) * PAGE_SIZE, total);
  info.textContent = total > 0 ? `显示 ${start}-${end} / 共 ${total} 条` : '共 0 条';
}

function renderInfoPanel() {
  renderTodayList();
  renderUpcomingList();
  renderSyncSummary();
  renderCalendarMini();
}

function renderTodayList() {
  const todayTasks = currentTasks.filter(t => t.status !== 'cancelled' && (isToday(t.start_time) || isToday(t.due_time)));
  const container = $('#info-today-list');
  if (todayTasks.length === 0) {
    container.innerHTML = '<div style="padding:6px 0;font-size:12px;color:var(--color-text-light)">今日暂无日程</div>';
    return;
  }
  container.innerHTML = todayTasks.slice(0, 5).map(t => {
    const syncStatus = t.last_sync_status || 'not_synced';
    const now = new Date();
    const startTime = t.start_time ? new Date(t.start_time) : null;
    let dotColor = 'green';
    let badgeClass = 'green';
    let badgeText = '进行中';
    if (startTime && startTime > now) {
      const diffMin = (startTime - now) / 60000;
      if (diffMin <= 60) {
        dotColor = 'orange'; badgeClass = 'orange'; badgeText = '即将开始';
      } else {
        dotColor = 'purple'; badgeClass = 'purple'; badgeText = '稍后开始';
      }
    }
    return `
      <div class="info-today-item">
        <span class="today-dot ${dotColor}"></span>
        <span class="info-today-time">${formatDatetime(t.start_time || t.due_time)}</span>
        <span class="info-today-title" title="${escapeHtml(t.title)}">${escapeHtml(t.title)}</span>
        <span class="today-badge ${badgeClass}">${badgeText}</span>
      </div>
    `;
  }).join('');
}

function renderUpcomingList() {
  const upcomingTasks = currentTasks
    .filter(t => t.status !== 'cancelled' && ((t.start_time && isUpcoming(t.start_time)) || (t.due_time && isUpcoming(t.due_time))))
    .sort((a, b) => (a.start_time || a.due_time || '').localeCompare(b.start_time || b.due_time || ''));
  const container = $('#info-upcoming-list');
  if (upcomingTasks.length === 0) {
    container.innerHTML = '<div style="padding:6px 0;font-size:12px;color:var(--color-text-light)">暂无即将开始的日程</div>';
    return;
  }
  container.innerHTML = upcomingTasks.slice(0, 5).map(t => {
    const syncStatus = t.last_sync_status || 'not_synced';
    return `
      <div class="info-upcoming-item">
        <span class="info-today-time">${formatDatetime(t.start_time || t.due_time)}</span>
        <span class="info-today-title" title="${escapeHtml(t.title)}">${escapeHtml(t.title)}</span>
        <span class="info-status-line">${STATUS_SYNC_LABELS[syncStatus] || syncStatus}</span>
      </div>
    `;
  }).join('');
}

function renderSyncSummary() {
  const active = currentTasks.filter(t => t.status !== 'cancelled');
  const synced = active.filter(t => t.last_sync_status === 'synced').length;
  const pending = active.filter(t => t.last_sync_status === 'pending' || t.last_sync_status === 'in_progress').length;
  const stale = active.filter(t => t.last_sync_status === 'stale').length;
  const failed = active.filter(t => t.last_sync_status === 'failed' || t.last_sync_status === 'failed_permanent').length;
  const notSynced = active.filter(t => !t.last_sync_status || t.last_sync_status === 'not_synced').length;
  const lastSyncedAt = active
    .map(t => t.last_synced_at)
    .filter(Boolean)
    .sort()
    .pop();

  $('#sync-summary').innerHTML = `
    <div class="sync-summary-item"><span>最后同步时间</span><span class="sync-count">${lastSyncedAt ? formatDatetime(lastSyncedAt) : '-'}</span></div>
    <div class="sync-summary-item"><span>已同步</span><span class="sync-count" style="color:var(--color-chip-synced)">${synced}</span></div>
    <div class="sync-summary-item"><span>待同步</span><span class="sync-count" style="color:var(--color-chip-pending)">${pending}</span></div>
    <div class="sync-summary-item"><span>已过期</span><span class="sync-count" style="color:var(--color-chip-stale)">${stale}</span></div>
    <div class="sync-summary-item"><span>同步失败</span><span class="sync-count" style="color:var(--color-chip-failed)">${failed}</span></div>
    <div class="sync-summary-item"><span>未同步</span><span class="sync-count" style="color:var(--color-chip-skipped)">${notSynced}</span></div>
  `;
}

function renderReminderSummary() {
  fetchReminderSummary()
    .then(data => {
      $('#reminder-summary').innerHTML = `
        <div class="sync-summary-item"><span>今日待提醒</span><span class="sync-count">${data.today_pending ?? 0}</span></div>
        <div class="sync-summary-item"><span>已发送提醒</span><span class="sync-count">${data.sent ?? 0}</span></div>
        <div class="sync-summary-item"><span>提醒失败</span><span class="sync-count">${data.failed ?? 0}</span></div>
        <div class="sync-summary-item"><span>微信提醒数</span><span class="sync-count">${data.wechat ?? 0}</span></div>
        <div class="sync-summary-item"><span>Apple 提醒数</span><span class="sync-count">${data.apple ?? 0}</span></div>
      `;
    })
    .catch(() => {
      $('#reminder-summary').innerHTML = '<div style="padding:6px 0;color:var(--color-text-light);font-size:12px">暂无提醒数据</div>';
    });
}

function renderWeatherSummary() {
  fetchWeatherOverview()
    .then(data => {
      const latest = data.latest || {};
      $('#weather-summary').innerHTML = `
        <div class="sync-summary-item"><span>活跃预报</span><span class="sync-count">${data.active_forecasts ?? 0}</span></div>
        <div class="sync-summary-item"><span>最新地点</span><span class="sync-count">${escapeHtml(latest.location || '-')}</span></div>
        <div class="sync-summary-item"><span>最新天气</span><span class="sync-count">${escapeHtml(latest.condition || '-')}</span></div>
        <div class="sync-summary-item"><span>降雨概率</span><span class="sync-count">${latest.rain_probability ?? '-'}</span></div>
        <div class="sync-summary-item"><span>AQI</span><span class="sync-count">${latest.aqi ?? '-'}</span></div>
      `;
    })
    .catch(() => {
      $('#weather-summary').innerHTML = '<div style="padding:6px 0;color:var(--color-text-light);font-size:12px">暂无天气数据</div>';
    });
}


function renderCalendarMini() {
  const now = new Date();
  const days = ['日', '一', '二', '三', '四', '五', '六'];
  const year = now.getFullYear();
  const month = now.getMonth();
  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const todayDate = now.getDate();
  const todayStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(todayDate).padStart(2, '0')}`;

  // Build set of dates that have tasks (from start_time or due_time)
  const dateSet = new Set();
  currentTasks.filter(t => t.status !== 'cancelled').forEach(t => {
    if (t.start_time) dateSet.add(t.start_time.substring(0, 10));
    if (t.due_time) dateSet.add(t.due_time.substring(0, 10));
  });

  const container = $('#calendar-mini-body');
  const monthLabel = $('#calendar-mini-month');
  if (monthLabel) monthLabel.textContent = `${year}年${month + 1}月`;

  if (!currentTasks.length) {
    container.innerHTML = '<div class="calendar-mini-placeholder">暂无日程数据</div>';
    return;
  }

  let html = '<table style="width:100%;border-collapse:collapse;font-size:12px;text-align:center">';
  html += '<thead><tr>' + days.map(d => `<th style="padding:4px;color:var(--color-text-light);font-weight:500">${d}</th>`).join('') + '</tr></thead><tbody><tr>';
  for (let i = 0; i < firstDay; i++) {
    html += '<td style="padding:4px;color:var(--color-text-light)"></td>';
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    const isToday = d === todayDate;
    const isSelected = selectedDate === dateStr;
    const hasTask = dateSet.has(dateStr);

    let style = 'padding:4px;cursor:pointer;border-radius:4px;';
    if (isSelected) {
      style += 'background:var(--color-primary);color:#fff;font-weight:600;';
    } else if (isToday) {
      style += 'background:var(--color-chip-pending);color:#fff;font-weight:600;';
    } else if (hasTask) {
      style += 'font-weight:500;';
    }
    const dot = hasTask && !isSelected ? '<span style="display:block;width:4px;height:4px;background:var(--color-primary);border-radius:50%;margin:1px auto 0"></span>' : '';
    html += `<td onclick="selectCalendarDate('${dateStr}')" style="${style}">${d}${dot}</td>`;
    if ((firstDay + d) % 7 === 0 && d < daysInMonth) {
      html += '</tr><tr>';
    }
  }
  html += '</tr></tbody></table>';
  container.innerHTML = html;

  // Show empty-state hint if a date is selected and no tasks match
  if (selectedDate) {
    const tasksOnDate = currentTasks.filter(t => {
      const taskDate = (t.start_time || t.due_time || '').substring(0, 10);
      return taskDate === selectedDate;
    });
    if (tasksOnDate.length === 0) {
      const hint = document.createElement('div');
      hint.className = 'calendar-mini-placeholder';
      hint.textContent = `📅 ${selectedDate} 暂无日程`;
      container.appendChild(hint);
    }
  }
}

// ── Calendar Date Selection ────────────────────────────────────────────

function selectCalendarDate(dateStr) {
  if (selectedDate === dateStr) {
    // Toggle off
    selectedDate = null;
  } else {
    selectedDate = dateStr;
    // Reset tab to 'all' when selecting a date
    currentTab = 'all';
    $$('.tab-item').forEach(el => el.classList.toggle('active', el.dataset.tab === 'all'));
    $$('.nav-item').forEach(el => {
      if (el.dataset.tab) el.classList.toggle('active', el.dataset.tab === 'all');
    });
  }
  renderCalendarMini();
  renderTable();
}

function renderSystemStatus() {
  fetchSystemStatus()
    .then(data => {
      $('#sys-db-status').textContent = data.db_connected ? '正常' : '断开';
      const calEl = $('#sys-calendar-status');
      if (calEl) calEl.textContent = data.db_connected ? '已连接' : '未连接';
    })
    .catch(() => {
      $('#sys-db-status').textContent = '错误';
    });
}

// ── Task Actions ─────────────────────────────────────────────────────────

async function refreshTasks() {
  showLoading(true);
  try {
    const params = { limit: PAGE_SIZE, offset: currentPage * PAGE_SIZE };
    const data = await fetchTasks(params);
    currentTasks = data.tasks || [];
    const stats = computeStats();
    renderStats();
    renderTable();
    renderTaskCards(currentTasks);
    renderInfoPanel();
    renderSystemStatus();
    if (selectedTaskId && $('#task-detail-card')?.style.display !== 'none') {
      const selectedTask = currentTasks.find(t => t.task_id === selectedTaskId);
      if (selectedTask) renderTaskDetail(selectedTask);
    }
  } catch (err) {
    showToast('加载任务失败: ' + err.message, 'error');
  } finally {
    showLoading(false);
  }
}

function viewTask(taskId) {
  currentFormMode = 'view';
  $('#view-detail').style.display = '';
  $('#edit-form').style.display = 'none';
  fetchTask(taskId).then(task => {
    if (!task) return;
    const syncTargets = (task.sync_targets || []).map(getSyncTargetLabel).join(', ');
    const syncStatus = task.last_sync_status || 'not_synced';
    $('#detail-task-id').textContent = task.task_id;
    $('#detail-title').textContent = task.title;
    $('#detail-description').textContent = task.description || '无描述';
    $('#detail-priority').textContent = task.priority;
    $('#detail-status').innerHTML = getStatusChipHtml(task.status);
    $('#detail-time').textContent = `${formatDatetime(task.start_time)} ~ ${formatDatetime(task.due_time)}`;
    $('#detail-timezone').textContent = task.timezone;
    $('#detail-location').textContent = task.location || '-';
    $('#detail-channel').textContent = task.created_channel;
    const syncHint = getSyncStatusHint(syncStatus);
    const failedDetail = getFailedDetailHtml(task.status, syncStatus);
    $('#detail-sync').innerHTML = `<span class="status-chip ${syncStatus}">${STATUS_SYNC_LABELS[syncStatus] || syncStatus}</span>${syncHint ? `<div class="sync-status-hint">${escapeHtml(syncHint)}</div>` : ''}${failedDetail}`;
    $('#detail-sync-target').textContent = task.sync_enabled ? syncTargets : '未启用';
    $('#detail-created').textContent = formatDatetime(task.created_at);
    $('#detail-updated').textContent = formatDatetime(task.updated_at);
    showModal(true);
    $('#modal-title').textContent = '任务详情';
    $('#modal-footer').style.display = 'none';
    $('.modal-body').scrollTop = 0;
  }).catch(err => showToast('获取任务详情失败: ' + err.message, 'error'));
}

function editTask(taskId) {
  currentFormMode = 'edit';
  $('#view-detail').style.display = 'none';
  $('#edit-form').style.display = '';
  fetchTask(taskId).then(task => {
    if (!task) return;
    $('#edit-task-id').value = task.task_id;
    $('#edit-title').value = task.title;
    $('#edit-description').value = task.description || '';
    $('#edit-priority').value = task.priority;
    $('#edit-start-time').value = task.start_time ? task.start_time.replace('T', ' ').substring(0, 16) : '';
    $('#edit-due-time').value = task.due_time ? task.due_time.replace('T', ' ').substring(0, 16) : '';
    $('#edit-timezone').value = task.timezone;
    $('#edit-location').value = task.location || '';
    $('#edit-schedule-type').value = task.schedule_type || 'plan';
    $('#edit-notify-policy').value = task.notify_policy || 'calendar_only';
    $('#edit-weather-sensitive').checked = !!task.weather_sensitive;
    $('#edit-reminder-profile').value = task.reminder_profile || '';
    $('#edit-sync-enabled').checked = task.sync_enabled;
    $('#edit-sync-targets').value = (task.sync_targets || []).join(',');
    $('#modal-title').textContent = '编辑任务';
    $('#modal-footer').style.display = 'flex';
    showModal(true);
    $('.modal-body').scrollTop = 0;
  }).catch(err => showToast('获取任务详情失败: ' + err.message, 'error'));
}

async function saveEdit() {
  const taskId = $('#edit-task-id').value;
  const data = {
    title: $('#edit-title').value.trim(),
    description: $('#edit-description').value.trim() || null,
    priority: $('#edit-priority').value,
    timezone: $('#edit-timezone').value,
    schedule_type: $('#edit-schedule-type').value,
    notify_policy: $('#edit-notify-policy').value,
    weather_sensitive: $('#edit-weather-sensitive').checked,
    reminder_profile: $('#edit-reminder-profile').value.trim() || null,
  };

  const startTime = $('#edit-start-time').value.trim();
  const dueTime = $('#edit-due-time').value.trim();
  if (startTime) {
    data.start_time = startTime.includes('T') ? startTime + ':00+08:00' : startTime.replace(' ', 'T') + ':00+08:00';
  }
  if (dueTime) {
    data.due_time = dueTime.includes('T') ? dueTime + ':00+08:00' : dueTime.replace(' ', 'T') + ':00+08:00';
  }

  const location = $('#edit-location').value.trim();
  if (location) data.location = location;

  data.sync_enabled = $('#edit-sync-enabled').checked;
  const targets = $('#edit-sync-targets').value.trim();
  data.sync_targets = targets ? targets.split(',').map(t => t.trim()).filter(Boolean) : ['apple_calendar'];

  if (!data.title) {
    showToast('标题不能为空', 'error');
    return;
  }

  showLoading(true);
  try {
    await updateTask(taskId, data);
    showToast('任务已更新', 'success');
    showModal(false);
    await refreshTasks();
  } catch (err) {
    showToast('更新失败: ' + err.message, 'error');
  } finally {
    showLoading(false);
  }
}

async function archiveTask(taskId) {
  showConfirm(
    '归档任务',
    '确定要将此任务归档吗？归档后任务状态将变为"已取消"。',
    async () => {
      showLoading(true);
      try {
        await updateTask(taskId, { status: 'cancelled' });
        showToast('任务已归档', 'success');
        await refreshTasks();
      } catch (err) {
        showToast('归档失败: ' + err.message, 'error');
      } finally {
        showLoading(false);
      }
    }
  );
}

// ── Confirm Dialog ───────────────────────────────────────────────────────

function showConfirm(title, message, onConfirm) {
  $('#confirm-title').textContent = title;
  $('#confirm-message').textContent = message;
  $('#confirm-overlay').classList.add('show');
  $('#confirm-yes').onclick = () => {
    $('#confirm-overlay').classList.remove('show');
    onConfirm();
  };
  $('#confirm-no').onclick = () => {
    $('#confirm-overlay').classList.remove('show');
  };
}

// ── Create Task Modal ────────────────────────────────────────────────────

function showCreateModal() {
  currentFormMode = 'create';
  $('#view-detail').style.display = 'none';
  $('#edit-form').style.display = '';
  // Reset form — share fields with edit mode
  $('#edit-task-id').value = '';
  $('#edit-title').value = '';
  $('#edit-description').value = '';
  $('#edit-priority').value = 'P2';
  $('#edit-start-time').value = '';
  $('#edit-due-time').value = '';
  $('#edit-timezone').value = 'Asia/Shanghai';
  $('#edit-location').value = '';
  $('#edit-schedule-type').value = 'plan';
  $('#edit-notify-policy').value = 'calendar_only';
  $('#edit-weather-sensitive').checked = false;
  $('#edit-reminder-profile').value = '';
  $('#edit-sync-enabled').checked = true;
  $('#edit-sync-targets').value = 'apple_calendar';
  $('#modal-title').textContent = '创建日程';
  showModal(true);
  $('#modal-footer').style.display = 'flex';
}

async function saveCreate() {
  const data = {
    title: $('#edit-title').value.trim(),
    description: $('#edit-description').value.trim() || null,
    priority: $('#edit-priority').value,
    timezone: $('#edit-timezone').value,
    created_channel: 'local_ui',
    schedule_type: $('#edit-schedule-type').value,
    notify_policy: $('#edit-notify-policy').value,
    weather_sensitive: $('#edit-weather-sensitive').checked,
    reminder_profile: $('#edit-reminder-profile').value.trim() || null,
  };

  const startTime = $('#edit-start-time').value.trim();
  const dueTime = $('#edit-due-time').value.trim();
  if (startTime) {
    data.start_time = startTime.includes('T') ? startTime + ':00+08:00' : startTime.replace(' ', 'T') + ':00+08:00';
  }
  if (dueTime) {
    data.due_time = dueTime.includes('T') ? dueTime + ':00+08:00' : dueTime.replace(' ', 'T') + ':00+08:00';
  }

  const location = $('#edit-location').value.trim();
  if (location) data.location = location;

  data.sync_enabled = $('#edit-sync-enabled').checked;
  const targets = $('#edit-sync-targets').value.trim();
  data.sync_targets = targets ? targets.split(',').map(t => t.trim()).filter(Boolean) : ['apple_calendar'];

  if (!data.title) {
    showToast('标题不能为空', 'error');
    return;
  }

  showLoading(true);
  try {
    await createTask(data);
    showToast('日程已创建', 'success');
    showModal(false);
    await refreshTasks();
  } catch (err) {
    showToast('创建失败: ' + err.message, 'error');
  } finally {
    showLoading(false);
  }
}

// ── Tab Switching ────────────────────────────────────────────────────────

function switchTab(tab) {
  currentTab = tab;
  selectedDate = null; // Clear calendar date selection on tab switch
  $$('.tab-item').forEach(el => el.classList.toggle('active', el.dataset.tab === tab));
  $$('.nav-item').forEach(el => {
    if (el.dataset.tab) el.classList.toggle('active', el.dataset.tab === tab);
  });
  renderTable();
  renderCalendarMini(); // Re-render to clear selected date highlight
}

// ── Search ───────────────────────────────────────────────────────────────

function handleSearch() {
  searchQuery = $('#search-input').value.trim();
  renderTable();
}

// ── Init ─────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  refreshTasks();

  // Tab click handlers
  $$('.tab-item').forEach(el => {
    el.addEventListener('click', () => switchTab(el.dataset.tab));
  });

  // Nav item click handlers — items with data-tab act as tab switchers
  $$('.nav-item').forEach(el => {
    el.addEventListener('click', () => {
      const tab = el.dataset.tab;
      $$('.nav-item').forEach(e => e.classList.remove('active'));
      el.classList.add('active');
      if (tab) {
        switchTab(tab);
      }
    });
  });

  // Modal close
  $('#modal-overlay').addEventListener('click', (e) => {
    if (e.target === $('#modal-overlay')) showModal(false);
  });

  // Confirm overlay close
  $('#confirm-overlay').addEventListener('click', (e) => {
    if (e.target === $('#confirm-overlay')) {
      $('#confirm-overlay').classList.remove('show');
    }
  });

  // Search debounce
  let searchTimer;
  $('#search-input').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(handleSearch, 300);
  });

  // Enter key in search
  $('#search-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handleSearch();
  });
});


// ════════════════════════════════════════════════════════════════════════════
// LifeSync UI Redesign — Task Cards + Detail Panel + Animations
// ════════════════════════════════════════════════════════════════════════════

var currentView = 'cards';

// ── View Toggle ────────────────────────────────────────────────────────────
function switchView(view) {
  currentView = view;
  $$('.view-toggle-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.view === view);
  });
  const cardsContainer = $('#task-cards-container');
  const tableSection = $('#table-section');
  if (view === 'cards') {
    if (cardsContainer) cardsContainer.style.display = '';
    if (tableSection) tableSection.style.display = 'none';
  } else {
    if (cardsContainer) cardsContainer.style.display = 'none';
    if (tableSection) tableSection.style.display = '';
  }
  requestAnimationFrame(() => {
    if (cardsContainer) cardsContainer.classList.toggle('is-visible', view === 'cards');
    if (tableSection) tableSection.classList.toggle('is-visible', view !== 'cards');
  });
}

// ── Render Task Cards ──────────────────────────────────────────────────────
function renderTaskCards(tasks) {
  const container = $('#task-cards-container');
  if (!container) return;
  if (!tasks || tasks.length === 0) {
    container.innerHTML = '';
    return;
  }

  const animateClass = hasRenderedTaskCards ? '' : ' task-card--animate-in';
  container.innerHTML = tasks.map(t => {
    const syncStatus = t.last_sync_status || 'not_synced';
    const statusClass = `status-${syncStatus}`;
    const priority = t.priority || 'P2';
    const timeStr = formatDatetime(t.start_time || t.due_time) || '未设置时间';
    const location = t.location ? `<span class="task-card-meta-item">📍 ${escapeHtml(t.location)}</span>` : '';
    const weatherHtml = t.need_weather_check
      ? `<span class="task-card-weather">🌤 天气敏感</span>`
      : '';
    const scheduleLabel = getScheduleTypeLabel(t.schedule_type) || '';
    const syncTargets = (t.sync_targets || []).map(getSyncTargetLabel).join(', ');
    const title = escapeHtml(t.title);

    return `
      <div class="task-card ${statusClass}${animateClass}" role="button" tabindex="0" aria-label="查看任务 ${title}" onclick="selectTaskCard('${t.task_id}')" onkeydown="handleTaskCardKeydown(event, '${t.task_id}')">
        <div class="task-card-header">
          <div class="task-card-title" title="${title}">${title}</div>
          <div class="task-card-priority ${priority}">${priority}</div>
        </div>
        <div class="task-card-meta">
          <span class="task-card-meta-item">⏰ ${timeStr}</span>
          ${location}
          <span class="task-card-meta-item">📋 ${scheduleLabel}</span>
          <span class="task-card-meta-item">🔄 ${syncTargets}</span>
        </div>
        <div class="task-card-status-row">
          ${getStatusChipHtml(syncStatus, 'sync')}
          ${weatherHtml}
        </div>
        <div class="task-card-actions" onclick="event.stopPropagation()">
          <button class="btn btn-sm" aria-label="天气评估" onclick="runLifeSyncActionForTask('${t.task_id}', 'weather', this)">🌦</button>
          <button class="btn btn-sm" aria-label="微信通知" onclick="runLifeSyncActionForTask('${t.task_id}', 'wechat', this)">💬</button>
          <button class="btn btn-sm" aria-label="日历同步" onclick="runLifeSyncActionForTask('${t.task_id}', 'calendar', this)">📅</button>
          <button class="btn btn-sm" aria-label="查看详情" onclick="viewTask('${t.task_id}')">👁</button>
        </div>
      </div>
    `;
  }).join('');
  hasRenderedTaskCards = true;
}

function handleTaskCardKeydown(event, taskId) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    selectTaskCard(taskId);
  }
}

// ── Select Task Card → Show Detail ─────────────────────────────────────────
async function selectTaskCard(taskId) {
  selectedTaskId = taskId;
  const card = $('#task-detail-card');
  const empty = $('#detail-empty-state');
  const body = $('#task-detail-body');
  if (empty) empty.style.display = 'none';
  if (card) card.style.display = '';
  if (body) body.innerHTML = '<div class="skeleton skeleton-line medium"></div><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line short"></div>';
  try {
    const task = await fetchTask(taskId);
    if (!task) return;
    renderTaskDetail(task);
  } catch (err) {
    showToast('加载详情失败: ' + err.message, 'error');
  }
}

// ── Close Task Detail ──────────────────────────────────────────────────────
function closeTaskDetail() {
  selectedTaskId = null;
  const card = $('#task-detail-card');
  const empty = $('#detail-empty-state');
  if (card) {
    card.classList.add('detail-card--closing');
    setTimeout(() => {
      card.style.display = 'none';
      card.classList.remove('detail-card--closing');
      if (empty) empty.style.display = '';
    }, 150);
  } else if (empty) {
    empty.style.display = '';
  }
}

// ── Render Task Detail Panel ───────────────────────────────────────────────
function renderTaskDetail(task) {
  const card = $('#task-detail-card');
  const empty = $('#detail-empty-state');
  const body = $('#task-detail-body');
  if (!card || !body) return;

  const syncStatus = task.last_sync_status || 'not_synced';
  const syncLabel = STATUS_SYNC_LABELS[syncStatus] || syncStatus;

  body.innerHTML = `
    <div class="detail-section">
      <div class="detail-field">
        <span class="detail-field-label">标题</span>
        <span class="detail-field-value" title="${escapeHtml(task.title)}">${escapeHtml(task.title)}</span>
      </div>
      <div class="detail-field">
        <span class="detail-field-label">优先级</span>
        <span class="detail-field-value">${task.priority || 'P2'}</span>
      </div>
      <div class="detail-field">
        <span class="detail-field-label">开始</span>
        <span class="detail-field-value">${formatDatetime(task.start_time) || '-'}</span>
      </div>
      <div class="detail-field">
        <span class="detail-field-label">结束</span>
        <span class="detail-field-value">${formatDatetime(task.due_time) || '-'}</span>
      </div>
      <div class="detail-field">
        <span class="detail-field-label">地点</span>
        <span class="detail-field-value">${escapeHtml(task.location || '-')}</span>
      </div>
    </div>
    <div class="detail-section">
      <div class="detail-section-title">同步状态</div>
      <div class="detail-field">
        <span class="detail-field-label">状态</span>
        ${getStatusChipHtml(syncStatus, 'sync')}
      </div>
      <div class="detail-field">
        <span class="detail-field-label">目标</span>
        <span class="detail-field-value">${(task.sync_targets || []).map(getSyncTargetLabel).join(', ')}</span>
      </div>
      <div class="detail-field">
        <span class="detail-field-label">外部ID</span>
        <span class="detail-field-value" title="${task.apple_external_id || ''}">${(task.apple_external_id || '-').substring(0, 24)}</span>
      </div>
    </div>
    <div class="detail-section">
      <div class="detail-section-title">闭环操作</div>
      <div class="detail-actions">
        <button class="btn btn-sm" aria-label="天气评估" onclick="runLifeSyncActionForTask('${task.task_id}', 'weather', this)">🌦 天气</button>
        <button class="btn btn-sm" aria-label="微信通知" onclick="runLifeSyncActionForTask('${task.task_id}', 'wechat', this)">💬 微信</button>
        <button class="btn btn-sm" aria-label="日历同步" onclick="runLifeSyncActionForTask('${task.task_id}', 'calendar', this)">📅 日历</button>
        <button class="btn btn-sm" aria-label="刷新详情" onclick="selectTaskCard('${task.task_id}')">🔄 刷新</button>
      </div>
    </div>
  `;

  if (empty) empty.style.display = 'none';
  card.style.display = '';
}

// ── Run LifeSync Action for specific task ──────────────────────────────────
async function runLifeSyncActionForTask(taskId, action, buttonEl) {
  const endpoints = {
    weather: '/api/weather/evaluate',
    wechat: '/api/wechat/send',
    calendar: '/api/calendar/sync',
  };
  const bodies = {
    weather: { task_id: taskId },
    wechat: { task_id: taskId, trigger_type: 'manual' },
    calendar: { task_id: taskId, sync_target: 'apple_calendar' },
  };
  const labels = {
    weather: '天气评估',
    wechat: '微信通知',
    calendar: '日历同步',
  };
  const originalText = buttonEl ? buttonEl.textContent : '';
  if (buttonEl) {
    buttonEl.disabled = true;
    buttonEl.classList.add('btn-loading');
    buttonEl.textContent = '处理中…';
  }

  try {
    const result = await apiFetch(endpoints[action], {
      method: 'POST',
      body: JSON.stringify(bodies[action]),
    });

    if (result.ok || result.status === 'synced' || result.status === 'evaluated' || result.status === 'sent') {
      showToast(`${labels[action]} 成功`, 'success');
    } else if (result.status === 'blocked' || result.status === 'skipped') {
      showToast(`${labels[action]}: ${result.error || result.status}`, 'warning');
    } else {
      showToast(`${labels[action]}: ${result.error || '未知状态'}`, 'error');
    }

    // Refresh task data
    await refreshTasks();
    // Re-select the task to update detail panel
    if (selectedTaskId) {
      setTimeout(() => selectTaskCard(selectedTaskId), 300);
    }
  } catch (err) {
    showToast(`${labels[action]} 失败: ${err.message}`, 'error');
  } finally {
    if (buttonEl) {
      buttonEl.disabled = false;
      buttonEl.classList.remove('btn-loading');
      buttonEl.textContent = originalText;
    }
  }
}

// ── Initialize view on page load ───────────────────────────────────────────
// Call directly since script loads at end of body (DOM is ready)
switchView('table');

