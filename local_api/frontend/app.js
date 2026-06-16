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
  failed: '失败',
  failed_permanent: '永久失败',
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

let currentTasks = [];
let currentTab = 'all';
let currentPage = 0;
const PAGE_SIZE = 50;
let searchQuery = '';
let currentFormMode = 'view'; // 'view', 'edit', 'create'

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

// ── API Calls ────────────────────────────────────────────────────────────

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

async function deleteTask(taskId) {
  return apiFetch(`/api/tasks/${taskId}`, { method: 'DELETE' });
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
  } else if (tab === 'upcoming') {
    filtered = filtered.filter(t => (t.start_time && isUpcoming(t.start_time)) || (t.due_time && isUpcoming(t.due_time)));
    filtered.sort((a, b) => (a.start_time || a.due_time || '').localeCompare(b.start_time || b.due_time || ''));
  } else if (tab === 'future') {
    filtered = filtered.filter(t => (t.start_time && isFuture(t.start_time)) || (t.due_time && isFuture(t.due_time)));
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
  const all = currentTasks.length;
  const today = currentTasks.filter(t => isToday(t.start_time) || isToday(t.due_time)).length;
  const upcoming = currentTasks.filter(t => (t.start_time && isUpcoming(t.start_time)) || (t.due_time && isUpcoming(t.due_time))).length;
  const future = currentTasks.filter(t => (t.start_time && isFuture(t.start_time)) || (t.due_time && isFuture(t.due_time))).length;
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
    const syncTargets = (task.sync_targets || []).map(getSyncTargetLabel).join(', ') || '-';
    const syncTargetDisplay = task.sync_enabled ? syncTargets : '-';

    return `
      <tr>
        <td class="task-title" title="${escapeHtml(task.title)}">${escapeHtml(task.title)}</td>
        <td class="time-cell">${formatDatetime(task.start_time)}</td>
        <td class="time-cell">${formatDatetime(task.due_time)}</td>
        <td>${getStatusChipHtml(syncStatus, 'sync')}</td>
        <td style="font-size:12px">${escapeHtml(syncTargetDisplay)}</td>
        <td class="actions-cell">
          <button class="btn-icon" onclick="viewTask('${task.task_id}')" title="查看">👁</button>
          <button class="btn-icon" onclick="editTask('${task.task_id}')" title="编辑">✏️</button>
          <button class="btn-icon" onclick="archiveTask('${task.task_id}')" title="归档">📦</button>
          <button class="btn-icon" onclick="confirmDeleteTask('${task.task_id}')" title="删除" style="color:var(--color-danger)">🗑</button>
        </td>
      </tr>
    `;
  }).join('');

  renderPagination(filtered.length);
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
  const todayTasks = currentTasks.filter(t => isToday(t.start_time) || isToday(t.due_time));
  const container = $('#info-today-list');
  if (todayTasks.length === 0) {
    container.innerHTML = '<div style="padding:6px 0;font-size:12px;color:var(--color-text-light)">今日暂无日程</div>';
    return;
  }
  container.innerHTML = todayTasks.slice(0, 5).map(t => `
    <div class="info-today-item">
      <span class="info-today-time">${formatDatetime(t.start_time || t.due_time)}</span>
      <span class="info-today-title" title="${escapeHtml(t.title)}">${escapeHtml(t.title)}</span>
    </div>
  `).join('');
}

function renderUpcomingList() {
  const upcomingTasks = currentTasks
    .filter(t => (t.start_time && isUpcoming(t.start_time)) || (t.due_time && isUpcoming(t.due_time)))
    .sort((a, b) => (a.start_time || a.due_time || '').localeCompare(b.start_time || b.due_time || ''));
  const container = $('#info-upcoming-list');
  if (upcomingTasks.length === 0) {
    container.innerHTML = '<div style="padding:6px 0;font-size:12px;color:var(--color-text-light)">暂无即将开始的日程</div>';
    return;
  }
  container.innerHTML = upcomingTasks.slice(0, 5).map(t => `
    <div class="info-upcoming-item">
      <span class="info-today-time">${formatDatetime(t.start_time || t.due_time)}</span>
      <span class="info-today-title" title="${escapeHtml(t.title)}">${escapeHtml(t.title)}</span>
    </div>
  `).join('');
}

function renderSyncSummary() {
  const synced = currentTasks.filter(t => t.last_sync_status === 'synced').length;
  const pending = currentTasks.filter(t => t.last_sync_status === 'pending' || t.last_sync_status === 'in_progress').length;
  const stale = currentTasks.filter(t => t.last_sync_status === 'stale').length;
  const failed = currentTasks.filter(t => t.last_sync_status === 'failed' || t.last_sync_status === 'failed_permanent').length;
  const notSynced = currentTasks.filter(t => !t.last_sync_status || t.last_sync_status === 'not_synced').length;

  $('#sync-summary').innerHTML = `
    <div class="sync-summary-item"><span>已同步</span><span class="sync-count" style="color:var(--color-chip-synced)">${synced}</span></div>
    <div class="sync-summary-item"><span>待同步</span><span class="sync-count" style="color:var(--color-chip-pending)">${pending}</span></div>
    <div class="sync-summary-item"><span>已过期</span><span class="sync-count" style="color:var(--color-chip-stale)">${stale}</span></div>
    <div class="sync-summary-item"><span>失败</span><span class="sync-count" style="color:var(--color-chip-failed)">${failed}</span></div>
    <div class="sync-summary-item"><span>未同步</span><span class="sync-count" style="color:var(--color-chip-skipped)">${notSynced}</span></div>
  `;
}

function renderCalendarMini() {
  const now = new Date();
  const days = ['日', '一', '二', '三', '四', '五', '六'];
  const firstDay = new Date(now.getFullYear(), now.getMonth(), 1).getDay();
  const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  const todayDate = now.getDate();

  let html = '<table style="width:100%;border-collapse:collapse;font-size:12px;text-align:center">';
  html += '<thead><tr>' + days.map(d => `<th style="padding:4px;color:var(--color-text-light);font-weight:500">${d}</th>`).join('') + '</tr></thead><tbody><tr>';
  for (let i = 0; i < firstDay; i++) {
    html += '<td style="padding:4px;color:var(--color-text-light)"></td>';
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const cls = d === todayDate ? ' style="padding:4px;background:var(--color-primary);color:#fff;border-radius:4px;font-weight:600"' : ' style="padding:4px"';
    html += `<td${cls}>${d}</td>`;
    if ((firstDay + d) % 7 === 0 && d < daysInMonth) {
      html += '</tr><tr>';
    }
  }
  html += '</tr></tbody></table>';
  $('#calendar-mini-body').innerHTML = html;
}

function renderSystemStatus() {
  fetchSystemStatus()
    .then(data => {
      $('#sys-db-status').textContent = data.db_connected ? '已连接' : '断开';
      $('#sys-task-count').textContent = data.task_count;
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
    renderInfoPanel();
    renderSystemStatus();
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
    $('#detail-sync').innerHTML = `<span class="status-chip ${syncStatus}">${STATUS_SYNC_LABELS[syncStatus] || syncStatus}</span>`;
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

function confirmDeleteTask(taskId) {
  showConfirm(
    '删除任务',
    '确定要永久删除此任务吗？此操作不可撤销。',
    async () => {
      showLoading(true);
      try {
        await deleteTask(taskId);
        showToast('任务已删除', 'success');
        await refreshTasks();
      } catch (err) {
        showToast('删除失败: ' + err.message, 'error');
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
  $('#edit-sync-enabled').checked = false;
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
  $$('.tab-item').forEach(el => el.classList.toggle('active', el.dataset.tab === tab));
  renderTable();
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

  // Nav item click handlers
  $$('.nav-item').forEach(el => {
    el.addEventListener('click', () => {
      $$('.nav-item').forEach(e => e.classList.remove('active'));
      el.classList.add('active');
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
