/**
 * HRone Employee Attendance & Analytics Portal - Frontend Logic
 */

let allEmployees = [];
let activeTab = 'punch';

// --------------------------------------------------------------------------
// 1. App Initialization
// --------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  initClock();
  initTabs();
  initModals();
  initPunchStation();
  initAnalytics();
  initAttendanceLog();
  initLeaderboard();
  initDemoSeeder();

  // Load initial data
  loadEmployees();
  loadAttendanceLogs();
  checkHealth();
});

// Live IST & UTC Clock
function initClock() {
  const clockEl = document.getElementById('live-ist-clock');
  const dateEl = document.getElementById('live-ist-date');

  function update() {
    const now = new Date();
    // Format in IST (UTC+5:30)
    const istOptions = { timeZone: 'Asia/Kolkata', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' };
    const dateOptions = { timeZone: 'Asia/Kolkata', year: 'numeric', month: 'short', day: '2-digit', weekday: 'short' };
    
    if (clockEl) clockEl.textContent = new Intl.DateTimeFormat('en-IN', istOptions).format(now) + ' IST';
    if (dateEl) dateEl.textContent = new Intl.DateTimeFormat('en-IN', dateOptions).format(now);
  }
  update();
  setInterval(update, 1000);
}

// Tab Switching
function initTabs() {
  const tabs = document.querySelectorAll('.nav-tab');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const target = tab.dataset.tab;
      switchTab(target);
    });
  });
}

function switchTab(tabId) {
  activeTab = tabId;
  document.querySelectorAll('.nav-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tabId);
  });
  document.querySelectorAll('.tab-content').forEach(c => {
    c.classList.toggle('active', c.id === `tab-${tabId}`);
  });

  // Tab-specific triggers
  if (tabId === 'analytics') {
    loadDepartmentSummary();
  } else if (tabId === 'attendance') {
    loadAttendanceLogs();
  } else if (tabId === 'leaderboard') {
    loadLeaderboard();
  } else if (tabId === 'employees') {
    renderEmployees();
  }
}

// Health check badge
async function checkHealth() {
  const textEl = document.getElementById('db-status-text');
  const pillEl = document.getElementById('db-status-pill');
  try {
    const res = await fetch('/health');
    if (res.ok) {
      if (textEl) textEl.textContent = 'Atlas Connected (200 OK)';
      if (pillEl) { pillEl.className = 'status-pill connected'; }
    } else {
      if (textEl) textEl.textContent = 'Database Offline (503)';
      if (pillEl) { pillEl.className = 'status-pill badge-red'; }
    }
  } catch (err) {
    if (textEl) textEl.textContent = 'Server Unreachable';
  }
}

// --------------------------------------------------------------------------
// 2. Employees API & Management
// --------------------------------------------------------------------------
async function loadEmployees() {
  try {
    const res = await fetch('/employees?page=1&page_size=100');
    if (!res.ok) throw new Error('Failed to load employees');
    const data = await res.json();
    allEmployees = data.items || [];

    updateEmployeeDropdowns();
    renderEmployees();
    updateMiniStats();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function updateEmployeeDropdowns() {
  const select = document.getElementById('punch-emp-select');
  if (!select) return;

  const currentVal = select.value;
  select.innerHTML = '<option value="" disabled selected>-- Select an employee --</option>';

  const deptFilter = document.getElementById('emp-dept-filter');
  const departments = new Set();

  allEmployees.forEach(emp => {
    const opt = document.createElement('option');
    opt.value = emp.emp_code;
    opt.textContent = `${emp.emp_code} • ${emp.name} (${emp.department})`;
    select.appendChild(opt);
    if (emp.department) departments.add(emp.department);
  });

  if (currentVal) select.value = currentVal;

  if (deptFilter) {
    deptFilter.innerHTML = '<option value="">All Departments</option>';
    departments.forEach(dept => {
      const opt = document.createElement('option');
      opt.value = dept;
      opt.textContent = dept;
      deptFilter.appendChild(opt);
    });
  }

  // Update trend dept select as well
  const trendDeptSelect = document.getElementById('trend-dept-select');
  if (trendDeptSelect && departments.size > 0) {
    trendDeptSelect.innerHTML = '';
    departments.forEach(dept => {
      const opt = document.createElement('option');
      opt.value = dept;
      opt.textContent = dept;
      trendDeptSelect.appendChild(opt);
    });
  }
}

function renderEmployees() {
  const grid = document.getElementById('employee-cards-grid');
  if (!grid) return;

  const search = (document.getElementById('emp-search-input')?.value || '').toLowerCase();
  const deptFilter = document.getElementById('emp-dept-filter')?.value || '';

  const filtered = allEmployees.filter(emp => {
    const matchSearch = emp.name.toLowerCase().includes(search) ||
                        emp.emp_code.toLowerCase().includes(search) ||
                        emp.email.toLowerCase().includes(search);
    const matchDept = !deptFilter || emp.department === deptFilter;
    return matchSearch && matchDept;
  });

  if (filtered.length === 0) {
    grid.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">👥</div>
        <h3>No Employees Found</h3>
        <p>${allEmployees.length === 0 ? 'No employees have been registered yet. Seed sample data or create one!' : 'No employees matched your search.'}</p>
        ${allEmployees.length === 0 ? '<button class="btn btn-primary" onclick="openAddEmpModal()">+ Register Employee</button>' : ''}
      </div>
    `;
    return;
  }

  grid.innerHTML = filtered.map(emp => {
    const initials = emp.name.split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
    return `
      <div class="emp-card">
        <div>
          <div class="emp-card-header">
            <div class="emp-avatar">${initials}</div>
            <div>
              <div class="emp-info-title">${escapeHtml(emp.name)}</div>
              <div class="emp-info-sub">${escapeHtml(emp.email)}</div>
            </div>
          </div>
          <div class="emp-meta-list">
            <div class="emp-meta-item">
              <span>Code</span>
              <strong>${escapeHtml(emp.emp_code)}</strong>
            </div>
            <div class="emp-meta-item">
              <span>Department</span>
              <span class="badge badge-purple">${escapeHtml(emp.department)}</span>
            </div>
            <div class="emp-meta-item">
              <span>Shift Window</span>
              <strong>${emp.shift_start} - ${emp.shift_end}</strong>
            </div>
            <div class="emp-meta-item">
              <span>Joined</span>
              <span>${emp.joined_on}</span>
            </div>
          </div>
        </div>
        <div class="emp-card-actions">
          <button class="btn btn-secondary btn-sm" style="flex:1" onclick="quickSelectEmp('${emp.emp_code}')">⚡ Quick Punch</button>
        </div>
      </div>
    `;
  }).join('');
}

function quickSelectEmp(code) {
  switchTab('punch');
  const sel = document.getElementById('punch-emp-select');
  if (sel) {
    sel.value = code;
    sel.dispatchEvent(new Event('change'));
  }
}

// --------------------------------------------------------------------------
// 3. Punch Station
// --------------------------------------------------------------------------
function initPunchStation() {
  const sel = document.getElementById('punch-emp-select');
  const meta = document.getElementById('punch-emp-meta');
  const customToggle = document.getElementById('custom-time-toggle');
  const customInputs = document.getElementById('custom-time-inputs');

  sel?.addEventListener('change', () => {
    const emp = allEmployees.find(e => e.emp_code === sel.value);
    if (emp && meta) {
      meta.innerHTML = `Shift: <strong>${emp.shift_start} to ${emp.shift_end}</strong> (Joined: ${emp.joined_on})`;
    }
  });

  customToggle?.addEventListener('change', () => {
    customInputs?.classList.toggle('hidden', !customToggle.checked);
  });

  document.getElementById('do-punch-in-btn')?.addEventListener('click', handlePunchIn);
  document.getElementById('do-punch-out-btn')?.addEventListener('click', handlePunchOut);
}

async function handlePunchIn() {
  const empCode = document.getElementById('punch-emp-select')?.value;
  if (!empCode) {
    showToast('Please select an employee first', 'error');
    return;
  }

  const status = document.querySelector('input[name="punch_status"]:checked')?.value || 'PRESENT';
  const customTime = getCustomEpochMs();

  const body = {
    emp_code: empCode,
    status: status
  };
  if (customTime) body.punched_at = customTime;

  try {
    const res = await fetch('/attendance/punch-in', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });

    const data = await res.json();
    if (!res.ok) {
      const msg = data.detail || (data.detail?.[0]?.msg) || 'Punch In Failed';
      throw new Error(msg);
    }

    displayPunchResult('Punch In Successful', data, 'success');
    showToast(`Punch In recorded for ${empCode} (${status})`, 'success');
    loadAttendanceLogs();
    updateMiniStats();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function handlePunchOut() {
  const empCode = document.getElementById('punch-emp-select')?.value;
  if (!empCode) {
    showToast('Please select an employee first', 'error');
    return;
  }

  const customTime = getCustomEpochMs();
  const body = { emp_code: empCode };
  if (customTime) body.punched_at = customTime;

  try {
    const res = await fetch('/attendance/punch-out', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });

    const data = await res.json();
    if (!res.ok) {
      const msg = data.detail || (data.detail?.[0]?.msg) || 'Punch Out Failed';
      throw new Error(msg);
    }

    displayPunchResult('Punch Out Successful', data, 'success');
    showToast(`Punch Out recorded! Hours: ${data.work_hours || 0}h`, 'success');
    loadAttendanceLogs();
    updateMiniStats();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function getCustomEpochMs() {
  const toggle = document.getElementById('custom-time-toggle');
  if (toggle && toggle.checked) {
    const val = document.getElementById('custom-time-val')?.value;
    if (val) {
      return new Date(val).getTime();
    }
  }
  return null;
}

function displayPunchResult(title, data, type) {
  const box = document.getElementById('punch-result');
  const titleEl = document.getElementById('result-title');
  const grid = document.getElementById('result-grid');
  if (!box || !titleEl || !grid) return;

  titleEl.textContent = title;
  box.classList.remove('hidden');

  grid.innerHTML = `
    <div class="result-stat">
      <div class="result-stat-label">Employee</div>
      <div class="result-stat-val">${data.emp_code}</div>
    </div>
    <div class="result-stat">
      <div class="result-stat-label">Shift Date</div>
      <div class="result-stat-val">${data.date}</div>
    </div>
    <div class="result-stat">
      <div class="result-stat-label">Status</div>
      <div class="result-stat-val">${data.status}</div>
    </div>
    <div class="result-stat">
      <div class="result-stat-label">Late Arrival</div>
      <div class="result-stat-val" style="color: ${data.late_minutes > 0 ? '#F87171' : '#34D399'}">${data.late_minutes || 0} mins</div>
    </div>
    <div class="result-stat">
      <div class="result-stat-label">Work Hours</div>
      <div class="result-stat-val" style="color: #60A5FA">${data.work_hours !== null ? data.work_hours + ' hrs' : 'In Progress'}</div>
    </div>
    <div class="result-stat">
      <div class="result-stat-label">Overtime</div>
      <div class="result-stat-val" style="color: #C084FC">${data.overtime_minutes || 0} mins</div>
    </div>
  `;
}

// --------------------------------------------------------------------------
// 4. Attendance Logbook & Regularization
// --------------------------------------------------------------------------
let allLogs = [];

function initAttendanceLog() {
  document.getElementById('log-filter-btn')?.addEventListener('click', loadAttendanceLogs);
  document.getElementById('log-reset-btn')?.addEventListener('click', () => {
    document.getElementById('log-filter-emp').value = '';
    document.getElementById('log-filter-status').value = '';
    document.getElementById('log-filter-from').value = '';
    document.getElementById('log-filter-to').value = '';
    loadAttendanceLogs();
  });
}

async function loadAttendanceLogs() {
  const emp = document.getElementById('log-filter-emp')?.value.trim();
  const status = document.getElementById('log-filter-status')?.value;
  const from = document.getElementById('log-filter-from')?.value;
  const to = document.getElementById('log-filter-to')?.value;

  const params = new URLSearchParams({ page: '1', page_size: '50' });
  if (emp) params.append('emp_code', emp);
  if (status) params.append('status', status);
  if (from) params.append('from_date', from);
  if (to) params.append('to_date', to);

  try {
    const res = await fetch(`/attendance?${params.toString()}`);
    if (!res.ok) throw new Error('Failed to load attendance logs');
    const data = await res.json();
    allLogs = data.items || [];
    renderAttendanceTable(allLogs);
    updateMiniStats();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function renderAttendanceTable(logs) {
  const tbody = document.getElementById('attendance-tbody');
  if (!tbody) return;

  if (logs.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" class="text-center py-4 text-muted">No attendance logs matching query.</td></tr>`;
    return;
  }

  tbody.innerHTML = logs.map(r => {
    const statusBadge = getStatusBadge(r.status);
    const punchInStr = r.punch_in ? formatEpochTime(r.punch_in) : '--:--';
    const punchOutStr = r.punch_out ? formatEpochTime(r.punch_out) : (r.status === 'PRESENT' || r.status === 'WFH' ? '<span class="badge badge-amber">Open</span>' : '--:--');
    const lateBadge = r.late_minutes > 0 ? `<span class="badge badge-red">+${r.late_minutes}m</span>` : '<span class="badge badge-green">On-Time</span>';
    const otBadge = r.overtime_minutes > 0 ? `<span class="badge badge-purple">+${r.overtime_minutes}m</span>` : '--';
    const flags = [];
    if (r.half_day) flags.push('<span class="badge badge-amber">Half-Day</span>');
    if (r.history && r.history.length > 0) flags.push(`<span class="badge badge-purple" title="Edited ${r.history.length} time(s)">Edited (${r.history.length})</span>`);

    return `
      <tr>
        <td><strong>${r.date}</strong></td>
        <td><code>${escapeHtml(r.emp_code)}</code></td>
        <td>${statusBadge}</td>
        <td>${punchInStr}</td>
        <td>${punchOutStr}</td>
        <td>${r.work_hours !== null && r.work_hours !== undefined ? `<strong>${r.work_hours}h</strong>` : '--'}</td>
        <td>${lateBadge}</td>
        <td>${otBadge}</td>
        <td>${flags.join(' ') || '--'}</td>
        <td>
          <button class="btn btn-secondary btn-sm" onclick="openRegularizeModal('${r.emp_code}', '${r.date}', '${r.status}')">
            Regularize
          </button>
        </td>
      </tr>
    `;
  }).join('');
}

function formatEpochTime(ms) {
  if (!ms) return '--:--';
  const d = new Date(ms);
  return d.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit' });
}

function getStatusBadge(status) {
  switch (status) {
    case 'PRESENT': return '<span class="badge badge-present">PRESENT</span>';
    case 'WFH': return '<span class="badge badge-wfh">WFH</span>';
    case 'ON_DUTY': return '<span class="badge badge-onduty">ON_DUTY</span>';
    case 'ABSENT': return '<span class="badge badge-absent">ABSENT</span>';
    case 'LEAVE': return '<span class="badge badge-leave">LEAVE</span>';
    default: return `<span class="badge">${escapeHtml(status)}</span>`;
  }
}

// --------------------------------------------------------------------------
// 5. Analytics Dashboard & Charts
// --------------------------------------------------------------------------
function initAnalytics() {
  const monthInput = document.getElementById('analytics-month');
  if (monthInput) {
    const now = new Date();
    const curMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    monthInput.value = curMonth;
  }

  document.getElementById('refresh-analytics-btn')?.addEventListener('click', loadDepartmentSummary);
  document.getElementById('load-trend-btn')?.addEventListener('click', loadTrend);

  // Default trend range: past 30 days
  const fromEl = document.getElementById('trend-from-date');
  const toEl = document.getElementById('trend-to-date');
  if (fromEl && toEl) {
    const today = new Date();
    const past = new Date();
    past.setDate(today.getDate() - 28);
    toEl.value = today.toISOString().split('T')[0];
    fromEl.value = past.toISOString().split('T')[0];
  }
}

async function loadDepartmentSummary() {
  const month = document.getElementById('analytics-month')?.value;
  if (!month) return;

  try {
    const res = await fetch(`/analytics/departments/summary?month=${month}`);
    if (!res.ok) throw new Error('Failed to load department summary');
    const data = await res.json();
    const items = data.items || (Array.isArray(data) ? data : []);
    renderDepartmentSummary(items);
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function renderDepartmentSummary(items) {
  const tbody = document.getElementById('dept-summary-tbody');
  if (!tbody) return;

  if (!items || items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center py-4 text-muted">No records for this month. Seed demo data or log attendance!</td></tr>`;
    return;
  }

  let totalHeadcount = 0;
  let totalHours = 0;
  let totalLate = 0;
  let weightedRateSum = 0;

  tbody.innerHTML = items.map(d => {
    totalHeadcount += d.headcount || 0;
    totalLate += d.late_count || 0;
    const rate = d.attendance_rate !== undefined ? d.attendance_rate : 0;
    const ratePct = (rate * 100).toFixed(1);
    weightedRateSum += (d.headcount || 1) * rate;
    totalHours += (d.avg_work_hours || 0) * (d.present_days || 0);

    return `
      <tr>
        <td><strong>${escapeHtml(d.department)}</strong></td>
        <td>${d.headcount}</td>
        <td>${d.present_days}</td>
        <td>${d.avg_work_hours !== null && d.avg_work_hours !== undefined ? d.avg_work_hours + ' hrs' : '--'}</td>
        <td>${d.late_count > 0 ? `<span class="badge badge-red">${d.late_count}</span>` : '<span class="badge badge-green">0</span>'}</td>
        <td>
          <div class="rate-cell">
            <div class="progress-track">
              <div class="progress-fill" style="width: ${Math.min(100, ratePct)}%"></div>
            </div>
            <strong>${ratePct}%</strong>
          </div>
        </td>
        <td>
          <button class="btn btn-secondary btn-sm" onclick="selectTrendDept('${escapeHtml(d.department)}')">Trend ↗</button>
        </td>
      </tr>
    `;
  }).join('');

  // Update KPI cards
  const kpiHeadcount = document.getElementById('kpi-total-headcount');
  const kpiRate = document.getElementById('kpi-avg-rate');
  const kpiLate = document.getElementById('kpi-late-incidents');
  const kpiHours = document.getElementById('kpi-total-workhours');

  if (kpiHeadcount) kpiHeadcount.textContent = totalHeadcount;
  if (kpiLate) kpiLate.textContent = totalLate;
  if (kpiHours) kpiHours.textContent = Math.round(totalHours) + ' hrs';
  if (kpiRate) {
    const avgRate = totalHeadcount > 0 ? ((weightedRateSum / totalHeadcount) * 100).toFixed(1) : '0.0';
    kpiRate.textContent = avgRate + '%';
  }
}

function selectTrendDept(dept) {
  const sel = document.getElementById('trend-dept-select');
  if (sel) sel.value = dept;
  loadTrend();
  document.getElementById('trend-chart-container')?.scrollIntoView({ behavior: 'smooth' });
}

// Department Trend Visualizer (SVG line + bar graph)
async function loadTrend() {
  const dept = document.getElementById('trend-dept-select')?.value;
  const from = document.getElementById('trend-from-date')?.value;
  const to = document.getElementById('trend-to-date')?.value;

  if (!dept || !from || !to) {
    showToast('Please select department and date range', 'error');
    return;
  }

  try {
    const res = await fetch(`/analytics/departments/${encodeURIComponent(dept)}/trend?from=${from}&to=${to}`);
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Trend query failed');
    }
    const data = await res.json();
    const items = data.items || (Array.isArray(data) ? data : []);
    renderTrendSVG(items, dept);
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function renderTrendSVG(data, dept) {
  const empty = document.getElementById('chart-empty');
  const svg = document.getElementById('trend-svg');
  if (!svg) return;

  if (!data || data.length === 0) {
    if (empty) empty.textContent = `No attendance trend records for ${dept} in this range.`;
    svg.classList.add('hidden');
    return;
  }

  if (empty) empty.classList.add('hidden');
  svg.classList.remove('hidden');

  const width = 800;
  const height = 300;
  const padLeft = 50;
  const padRight = 30;
  const padTop = 30;
  const padBottom = 40;

  const chartW = width - padLeft - padRight;
  const chartH = height - padTop - padBottom;

  const n = data.length;
  const step = n > 1 ? chartW / (n - 1) : chartW;

  // Points for 7-day moving average
  let pathD = '';
  const bars = [];
  const points = [];

  data.forEach((d, i) => {
    const x = padLeft + (i * step);
    const rate = d.attendance_rate !== undefined ? d.attendance_rate : 0;
    const mAvg = d.moving_avg_7d !== undefined ? d.moving_avg_7d : rate;

    // Y values: 0 at padTop + chartH, 1.0 at padTop
    const yBar = padTop + chartH - (rate * chartH);
    const barH = rate * chartH;
    const yLine = padTop + chartH - (mAvg * chartH);

    bars.push(`<rect x="${x - (chartW / (n * 2.5))}" y="${yBar}" width="${Math.max(4, chartW / (n * 1.5))}" height="${barH}" fill="rgba(99, 102, 241, 0.35)" rx="3"><title>${d.date}: ${(rate * 100).toFixed(1)}%</title></rect>`);

    if (i === 0) {
      pathD += `M ${x} ${yLine}`;
    } else {
      pathD += ` L ${x} ${yLine}`;
    }
    points.push(`<circle cx="${x}" cy="${yLine}" r="3.5" fill="#10B981"><title>${d.date} Moving Avg: ${(mAvg * 100).toFixed(1)}%</title></circle>`);
  });

  svg.innerHTML = `
    <!-- Grid lines -->
    <line x1="${padLeft}" y1="${padTop}" x2="${width - padRight}" y2="${padTop}" stroke="rgba(255,255,255,0.08)" />
    <text x="${padLeft - 8}" y="${padTop + 4}" fill="#6B7280" font-size="10" text-anchor="end">100%</text>

    <line x1="${padLeft}" y1="${padTop + chartH / 2}" x2="${width - padRight}" y2="${padTop + chartH / 2}" stroke="rgba(255,255,255,0.08)" />
    <text x="${padLeft - 8}" y="${padTop + chartH / 2 + 4}" fill="#6B7280" font-size="10" text-anchor="end">50%</text>

    <line x1="${padLeft}" y1="${padTop + chartH}" x2="${width - padRight}" y2="${padTop + chartH}" stroke="rgba(255,255,255,0.15)" />
    <text x="${padLeft - 8}" y="${padTop + chartH + 4}" fill="#6B7280" font-size="10" text-anchor="end">0%</text>

    <!-- Bars for daily rate -->
    ${bars.join('')}

    <!-- Moving avg line -->
    <path d="${pathD}" fill="none" stroke="#10B981" stroke-width="2.5" stroke-linecap="round" />
    ${points.join('')}

    <!-- Legend -->
    <rect x="${width - 240}" y="10" width="12" height="12" fill="rgba(99, 102, 241, 0.4)" rx="2" />
    <text x="${width - 222}" y="20" fill="#9CA3AF" font-size="11">Daily Attendance</text>
    <line x1="${width - 120}" y1="16" x2="${width - 98}" y2="16" stroke="#10B981" stroke-width="2.5" />
    <text x="${width - 92}" y="20" fill="#9CA3AF" font-size="11">7d Moving Avg</text>
  `;
}

// --------------------------------------------------------------------------
// 6. Leaderboard
// --------------------------------------------------------------------------
function initLeaderboard() {
  const lbMonth = document.getElementById('leaderboard-month');
  if (lbMonth) {
    const now = new Date();
    lbMonth.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  }
  document.getElementById('refresh-leaderboard-btn')?.addEventListener('click', loadLeaderboard);
}

async function loadLeaderboard() {
  const month = document.getElementById('leaderboard-month')?.value;
  if (!month) return;

  try {
    const res = await fetch(`/analytics/leaderboard/late?month=${month}&limit=10`);
    if (!res.ok) throw new Error('Failed to load leaderboard');
    const data = await res.json();
    const items = data.items || (Array.isArray(data) ? data : []);
    renderLeaderboard(items);
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function renderLeaderboard(items) {
  const container = document.getElementById('leaderboard-container');
  if (!container) return;

  if (!items || items.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">🏆</div>
        <h3>No Late Arrivals Recorded</h3>
        <p>All employees arrived within their shift grace window for this month!</p>
      </div>
    `;
    return;
  }

  container.innerHTML = items.map(item => {
    const rankClass = item.rank === 1 ? 'rank-1' : item.rank === 2 ? 'rank-2' : item.rank === 3 ? 'rank-3' : '';
    const medal = item.rank === 1 ? '🥇' : item.rank === 2 ? '🥈' : item.rank === 3 ? '🥉' : `#${item.rank}`;

    return `
      <div class="leaderboard-row">
        <div class="leaderboard-emp">
          <div class="leaderboard-rank ${rankClass}">${medal}</div>
          <div>
            <div style="font-weight: 700; color: #fff;">${escapeHtml(item.name || item.emp_code)}</div>
            <div style="font-size: 0.75rem; color: #9CA3AF;"><code>${escapeHtml(item.emp_code)}</code> • ${escapeHtml(item.department || 'Staff')}</div>
          </div>
        </div>
        <div class="leaderboard-stats">
          <div>
            <div class="lb-stat-val">${item.late_count}</div>
            <div class="lb-stat-label">Late Days</div>
          </div>
          <div>
            <div class="lb-stat-val" style="color: #FBBF24;">${item.total_late_minutes || 0}m</div>
            <div class="lb-stat-label">Total Minutes</div>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

// --------------------------------------------------------------------------
// 7. Modals: Add Employee & Regularize
// --------------------------------------------------------------------------
function initModals() {
  // Add Emp
  document.getElementById('open-add-emp-btn')?.addEventListener('click', openAddEmpModal);
  document.getElementById('emp-add-btn-2')?.addEventListener('click', openAddEmpModal);
  document.getElementById('empty-add-emp-btn')?.addEventListener('click', openAddEmpModal);
  document.getElementById('close-add-emp-modal')?.addEventListener('click', closeAddEmpModal);
  document.getElementById('cancel-add-emp-btn')?.addEventListener('click', closeAddEmpModal);
  document.getElementById('add-emp-form')?.addEventListener('submit', handleAddEmpSubmit);

  // Default joined date to today
  const joinedInput = document.getElementById('new-emp-joined');
  if (joinedInput) joinedInput.value = new Date().toISOString().split('T')[0];

  // Regularize
  document.getElementById('close-regularize-modal')?.addEventListener('click', closeRegularizeModal);
  document.getElementById('cancel-regularize-btn')?.addEventListener('click', closeRegularizeModal);
  document.getElementById('regularize-form')?.addEventListener('submit', handleRegularizeSubmit);
}

function openAddEmpModal() {
  document.getElementById('add-emp-modal')?.classList.remove('hidden');
}

function closeAddEmpModal() {
  document.getElementById('add-emp-modal')?.classList.add('hidden');
}

async function handleAddEmpSubmit(e) {
  e.preventDefault();
  const body = {
    emp_code: document.getElementById('new-emp-code').value.trim(),
    name: document.getElementById('new-emp-name').value.trim(),
    email: document.getElementById('new-emp-email').value.trim(),
    department: document.getElementById('new-emp-dept').value.trim(),
    shift_start: document.getElementById('new-emp-shift-start').value.trim(),
    shift_end: document.getElementById('new-emp-shift-end').value.trim(),
    joined_on: document.getElementById('new-emp-joined').value
  };

  try {
    const res = await fetch('/employees', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || (data.detail?.[0]?.msg) || 'Failed to create employee');
    }

    showToast(`Employee ${data.name} (${data.emp_code}) created successfully!`, 'success');
    closeAddEmpModal();
    document.getElementById('add-emp-form')?.reset();
    loadEmployees();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

let curRegEmp = null;
let curRegDate = null;

function openRegularizeModal(empCode, dateStr, currentStatus) {
  curRegEmp = empCode;
  curRegDate = dateStr;

  document.getElementById('reg-emp-code').textContent = empCode;
  document.getElementById('reg-date').textContent = dateStr;
  document.getElementById('reg-status').value = currentStatus || '';
  document.getElementById('regularize-modal')?.classList.remove('hidden');
}

function closeRegularizeModal() {
  document.getElementById('regularize-modal')?.classList.add('hidden');
}

async function handleRegularizeSubmit(e) {
  e.preventDefault();
  if (!curRegEmp || !curRegDate) return;

  const status = document.getElementById('reg-status').value;
  const inVal = document.getElementById('reg-punch-in').value;
  const outVal = document.getElementById('reg-punch-out').value;
  const reason = document.getElementById('reg-reason').value.trim();
  const regBy = document.getElementById('reg-by').value.trim();

  const body = {
    reason: reason,
    regularized_by: regBy
  };
  if (status) body.status = status;
  if (inVal) body.punch_in = new Date(inVal).getTime();
  if (outVal) body.punch_out = new Date(outVal).getTime();

  try {
    const res = await fetch(`/attendance/${encodeURIComponent(curRegEmp)}/${encodeURIComponent(curRegDate)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || (data.detail?.[0]?.msg) || 'Regularization failed');
    }

    showToast(`Record for ${curRegEmp} regularized!`, 'success');
    closeRegularizeModal();
    loadAttendanceLogs();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// --------------------------------------------------------------------------
// 8. Demo Data Seeder
// --------------------------------------------------------------------------
function initDemoSeeder() {
  document.getElementById('seed-demo-btn')?.addEventListener('click', seedSampleData);
}

async function seedSampleData() {
  const btn = document.getElementById('seed-demo-btn');
  if (btn) btn.disabled = true;

  showToast('Seeding 5 sample employees and attendance logs...', 'info');

  const sampleEmployees = [
    { emp_code: 'EMP1001', name: 'Aarav Sharma', email: 'aarav.sharma@example.com', department: 'Engineering', shift_start: '09:30', shift_end: '18:30', joined_on: '2024-01-15' },
    { emp_code: 'EMP1002', name: 'Diya Patel', email: 'diya.patel@example.com', department: 'Engineering', shift_start: '10:00', shift_end: '19:00', joined_on: '2024-02-01' },
    { emp_code: 'EMP1003', name: 'Rohan Verma', email: 'rohan.verma@example.com', department: 'Product', shift_start: '09:00', shift_end: '18:00', joined_on: '2024-03-10' },
    { emp_code: 'EMP1004', name: 'Priya Nair', email: 'priya.nair@example.com', department: 'HR', shift_start: '09:30', shift_end: '18:30', joined_on: '2023-11-20' },
    { emp_code: 'EMP1005', name: 'Vikram Singh', email: 'vikram.singh@example.com', department: 'Sales', shift_start: '09:30', shift_end: '18:30', joined_on: '2024-04-05' }
  ];

  // 1. Create employees
  for (const emp of sampleEmployees) {
    try {
      await fetch('/employees', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(emp)
      });
    } catch (e) {}
  }

  // 2. Create sample attendance punches for today and recent days
  const today = new Date();
  const dateStr = today.toISOString().split('T')[0];

  // EMP1001: Present on time
  try {
    const tIn = new Date(); tIn.setHours(9, 25, 0, 0);
    const tOut = new Date(); tOut.setHours(18, 35, 0, 0);
    await fetch('/attendance/punch-in', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ emp_code: 'EMP1001', status: 'PRESENT', punched_at: tIn.getTime() })
    });
    await fetch('/attendance/punch-out', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ emp_code: 'EMP1001', punched_at: tOut.getTime() })
    });
  } catch (e) {}

  // EMP1002: WFH
  try {
    const tIn = new Date(); tIn.setHours(9, 50, 0, 0);
    await fetch('/attendance/punch-in', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ emp_code: 'EMP1002', status: 'WFH', punched_at: tIn.getTime() })
    });
  } catch (e) {}

  // EMP1003: Late arrival (10:15 vs 09:00 shift start -> 75 mins late)
  try {
    const tIn = new Date(); tIn.setHours(10, 15, 0, 0);
    await fetch('/attendance/punch-in', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ emp_code: 'EMP1003', status: 'PRESENT', punched_at: tIn.getTime() })
    });
  } catch (e) {}

  // EMP1004: On Duty with Overtime (punched out at 19:45 -> overtime >= 30m)
  try {
    const tIn = new Date(); tIn.setHours(9, 30, 0, 0);
    const tOut = new Date(); tOut.setHours(19, 45, 0, 0);
    await fetch('/attendance/punch-in', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ emp_code: 'EMP1004', status: 'ON_DUTY', punched_at: tIn.getTime() })
    });
    await fetch('/attendance/punch-out', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ emp_code: 'EMP1004', punched_at: tOut.getTime() })
    });
  } catch (e) {}

  await loadEmployees();
  await loadAttendanceLogs();
  await loadDepartmentSummary();
  await loadLeaderboard();

  showToast('Demo data successfully populated! Analytics & charts are live.', 'success');
  if (btn) btn.disabled = false;
}

// --------------------------------------------------------------------------
// 9. Utilities & Helpers
// --------------------------------------------------------------------------
function updateMiniStats() {
  const empEl = document.getElementById('mini-emp-count');
  const punchEl = document.getElementById('mini-punch-count');
  if (empEl) empEl.textContent = allEmployees.length;
  if (punchEl) punchEl.textContent = allLogs.length;
}

function showToast(msg, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = msg;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    toast.style.transition = 'all 0.25s ease';
    setTimeout(() => toast.remove(), 250);
  }, 4000);
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/[&<>"']/g, m => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[m]));
}
