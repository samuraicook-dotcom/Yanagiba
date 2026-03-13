/* ============================================
   Yanagiba Trading Bot — Dashboard JS
   ============================================ */

// Current config state
let currentConfig = {};

// ---- Navigation ----
document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    // Update active nav
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    item.classList.add('active');

    // Show corresponding panel
    const panelId = 'panel-' + item.dataset.panel;
    document.querySelectorAll('.config-panel').forEach(p => p.classList.remove('active'));
    document.getElementById(panelId).classList.add('active');
  });
});

// ---- Load Config ----
async function loadConfig() {
  try {
    const res = await fetch('/api/config');
    currentConfig = await res.json();
    updateDashboardStats();
  } catch (e) {
    console.error('Failed to load config:', e);
  }
}

// ---- Save Config ----
async function saveConfig() {
  const data = gatherFormData();

  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const result = await res.json();

    if (result.status === 'ok') {
      currentConfig = result.config;
      updateDashboardStats();
      showToast('Configuration saved successfully!', 'success');
      document.getElementById('save-status').textContent = 'Saved just now';
      document.getElementById('save-status').className = 'save-status saved';
    } else {
      showToast('Failed to save configuration', 'error');
    }
  } catch (e) {
    showToast('Error saving: ' + e.message, 'error');
  }
}

// ---- Gather all form data ----
function gatherFormData() {
  const data = {};

  // Numeric fields (range sliders stored as percentages -> convert back)
  const percentFields = {
    'max_risk_per_trade': 100,
    'max_portfolio_risk': 100,
    'daily_loss_limit': 100,
    'scalp_stop_loss': 100,
    'weekend_position_scale': 100,
  };

  const numericFields = [
    'max_risk_per_trade', 'max_portfolio_risk', 'daily_loss_limit', 'max_leverage',
    'min_risk_reward', 'min_confidence', 'max_correlated_trades',
    'max_funding_rate_long', 'max_funding_rate_short',
    'scalp_stop_loss', 'scalp_take_profit_min', 'scalp_take_profit_max', 'scalp_position_size',
    'rsi_long_threshold', 'rsi_short_threshold',
    'ema_fast', 'ema_mid', 'ema_slow', 'rsi_period',
    'macd_fast', 'macd_slow', 'macd_signal',
    'bb_period', 'bb_std',
    'maker_fee', 'taker_fee',
    'trailing_stop_activation', 'trailing_stop_callback',
    'weekend_position_scale',
    'liq_cascade_threshold_usd', 'liq_window_seconds',
    'volume_flow_large_threshold', 'mempool_whale_threshold_btc',
  ];

  numericFields.forEach(field => {
    const el = document.getElementById('cfg-' + field);
    if (el) {
      let val = parseFloat(el.value);
      if (percentFields[field]) {
        val = val / percentFields[field];
      }
      // Integer fields
      if (['ema_fast', 'ema_mid', 'ema_slow', 'rsi_period', 'macd_fast', 'macd_slow',
           'macd_signal', 'bb_period', 'max_correlated_trades', 'liq_window_seconds'].includes(field)) {
        val = Math.round(val);
      }
      data[field] = val;
    }
  });

  // Boolean fields
  const boolFields = [
    'use_limit_entry', 'use_trailing_stop', 'enable_gaming_sector',
    'use_session_filter', 'use_weekend_filter',
    'enable_open_interest', 'enable_liquidation_stream',
    'enable_volume_flow', 'enable_mempool_monitor',
  ];

  boolFields.forEach(field => {
    const el = document.getElementById('cfg-' + field);
    if (el) data[field] = el.checked;
  });

  // Sandbox (two checkboxes sync)
  const sandboxEl = document.getElementById('cfg-sandbox');
  if (sandboxEl) data.sandbox = sandboxEl.checked;

  // Select fields
  const selectFields = ['exchange', 'market_type'];
  selectFields.forEach(field => {
    const el = document.getElementById('cfg-' + field);
    if (el) data[field] = el.value;
  });

  // Text / password fields
  const textFields = ['api_key', 'api_secret'];
  textFields.forEach(field => {
    const el = document.getElementById('cfg-' + field);
    if (el) data[field] = el.value;
  });

  // List fields (from tags)
  if (currentConfig.assets) data.assets = [...currentConfig.assets];
  if (currentConfig.gaming_tokens) data.gaming_tokens = [...currentConfig.gaming_tokens];
  if (currentConfig.timeframes) data.timeframes = [...currentConfig.timeframes];
  if (currentConfig.scalp_timeframes) data.scalp_timeframes = [...currentConfig.scalp_timeframes];

  return data;
}

// ---- Range display updater ----
function updateRangeDisplay(input, displayId, suffix) {
  const display = document.getElementById(displayId);
  if (display) {
    display.textContent = parseFloat(input.value).toFixed(1).replace(/\.0$/, '') + suffix;
  }
}

// ---- Dashboard stats update ----
function updateDashboardStats() {
  const c = currentConfig;
  if (!c.max_leverage) return;

  const setTextIfExists = (id, text) => {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  };

  setTextIfExists('stat-leverage', c.max_leverage + 'x');
  setTextIfExists('stat-risk-per-trade', (c.max_risk_per_trade * 100).toFixed(1) + '%');
  setTextIfExists('stat-daily-loss', (c.daily_loss_limit * 100).toFixed(1) + '%');
  setTextIfExists('stat-min-rr', c.min_risk_reward + ':1');
  setTextIfExists('stat-exposure', (c.max_portfolio_risk * 100).toFixed(0) + '%');
  setTextIfExists('stat-confidence', c.min_confidence);

  // Risk profile meter
  const riskScore = calculateRiskScore(c);
  const meter = document.getElementById('risk-meter-fill');
  const badge = document.getElementById('risk-profile-badge');

  if (meter) {
    meter.style.width = riskScore + '%';
    if (riskScore < 33) {
      meter.style.background = 'linear-gradient(90deg, #10b981, #10b981)';
    } else if (riskScore < 66) {
      meter.style.background = 'linear-gradient(90deg, #10b981, #f59e0b)';
    } else {
      meter.style.background = 'linear-gradient(90deg, #f59e0b, #ef4444)';
    }
  }

  if (badge) {
    if (riskScore < 33) {
      badge.textContent = 'CONSERVATIVE';
      badge.className = 'card-badge badge-green';
    } else if (riskScore < 66) {
      badge.textContent = 'MODERATE';
      badge.className = 'card-badge badge-yellow';
    } else {
      badge.textContent = 'AGGRESSIVE';
      badge.className = 'card-badge badge-red';
    }
  }
}

function calculateRiskScore(c) {
  // Score 0-100 based on how aggressive the settings are
  let score = 0;

  // Leverage (1-50 -> 0-30 points)
  score += Math.min(30, (c.max_leverage / 50) * 30);

  // Risk per trade (0.5-10% -> 0-25 points)
  score += Math.min(25, (c.max_risk_per_trade * 100 / 10) * 25);

  // Daily loss limit (1-15% -> 0-20 points)
  score += Math.min(20, (c.daily_loss_limit * 100 / 15) * 20);

  // Exposure (10-100% -> 0-15 points)
  score += Math.min(15, (c.max_portfolio_risk * 100 / 100) * 15);

  // Low confidence threshold -> more risk (1-10 -> 0-10 points)
  score += Math.min(10, ((10 - c.min_confidence) / 9) * 10);

  return Math.min(100, Math.round(score));
}

// ---- Presets ----
function applyPreset(preset) {
  const presets = {
    conservative: {
      max_risk_per_trade: 0.01,
      max_portfolio_risk: 0.30,
      max_leverage: 5,
      daily_loss_limit: 0.03,
      min_risk_reward: 3.0,
      min_confidence: 8.0,
      scalp_stop_loss: 0.003,
      scalp_take_profit_min: 0.015,
      scalp_take_profit_max: 0.03,
      use_session_filter: true,
      use_weekend_filter: true,
      weekend_position_scale: 0.3,
    },
    moderate: {
      max_risk_per_trade: 0.03,
      max_portfolio_risk: 0.80,
      max_leverage: 20,
      daily_loss_limit: 0.06,
      min_risk_reward: 1.8,
      min_confidence: 5.0,
      scalp_stop_loss: 0.004,
      scalp_take_profit_min: 0.012,
      scalp_take_profit_max: 0.025,
      use_session_filter: true,
      use_weekend_filter: true,
      weekend_position_scale: 0.5,
    },
    aggressive: {
      max_risk_per_trade: 0.05,
      max_portfolio_risk: 0.90,
      max_leverage: 30,
      daily_loss_limit: 0.10,
      min_risk_reward: 1.5,
      min_confidence: 4.0,
      scalp_stop_loss: 0.005,
      scalp_take_profit_min: 0.010,
      scalp_take_profit_max: 0.02,
      use_session_filter: true,
      use_weekend_filter: false,
      weekend_position_scale: 0.8,
    },
    degen: {
      max_risk_per_trade: 0.10,
      max_portfolio_risk: 1.0,
      max_leverage: 50,
      daily_loss_limit: 0.15,
      min_risk_reward: 1.0,
      min_confidence: 3.0,
      scalp_stop_loss: 0.008,
      scalp_take_profit_min: 0.008,
      scalp_take_profit_max: 0.015,
      use_session_filter: false,
      use_weekend_filter: false,
      weekend_position_scale: 1.0,
    },
  };

  const p = presets[preset];
  if (!p) return;

  // Update form fields
  setFieldValue('cfg-max_risk_per_trade', p.max_risk_per_trade * 100);
  setFieldValue('cfg-max_portfolio_risk', p.max_portfolio_risk * 100);
  setFieldValue('cfg-max_leverage', p.max_leverage);
  setFieldValue('cfg-daily_loss_limit', p.daily_loss_limit * 100);
  setFieldValue('cfg-min_risk_reward', p.min_risk_reward);
  setFieldValue('cfg-min_confidence', p.min_confidence);
  setFieldValue('cfg-scalp_stop_loss', p.scalp_stop_loss * 100);
  setFieldValue('cfg-scalp_take_profit_min', p.scalp_take_profit_min);
  setFieldValue('cfg-scalp_take_profit_max', p.scalp_take_profit_max);
  setFieldValue('cfg-weekend_position_scale', p.weekend_position_scale * 100);

  setCheckbox('cfg-use_session_filter', p.use_session_filter);
  setCheckbox('cfg-use_weekend_filter', p.use_weekend_filter);

  // Update range displays
  updateRangeDisplay(document.getElementById('cfg-max_risk_per_trade'), 'val-risk-per-trade', '%');
  updateRangeDisplay(document.getElementById('cfg-max_portfolio_risk'), 'val-portfolio-risk', '%');
  updateRangeDisplay(document.getElementById('cfg-max_leverage'), 'val-leverage', 'x');
  updateRangeDisplay(document.getElementById('cfg-daily_loss_limit'), 'val-daily-loss', '%');
  updateRangeDisplay(document.getElementById('cfg-scalp_stop_loss'), 'val-scalp-sl', '%');
  updateRangeDisplay(document.getElementById('cfg-weekend_position_scale'), 'val-weekend-scale', '%');

  // Update preset buttons
  document.querySelectorAll('.preset-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelector(`.preset-btn[onclick="applyPreset('${preset}')"]`).classList.add('active');

  // Update current config for stats
  Object.assign(currentConfig, p);
  updateDashboardStats();

  showToast(`Applied "${preset}" preset — don't forget to save!`, 'success');
}

function setFieldValue(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value;
}

function setCheckbox(id, checked) {
  const el = document.getElementById(id);
  if (el) el.checked = checked;
}

// ---- Tag management ----
function removeTag(field, value) {
  if (!currentConfig[field]) return;
  currentConfig[field] = currentConfig[field].filter(v => v !== value);
  refreshTags(field);
}

function addTag(field, inputId) {
  const input = document.getElementById(inputId);
  const value = input.value.trim().toUpperCase();
  if (!value) return;

  if (!currentConfig[field]) currentConfig[field] = [];
  if (currentConfig[field].includes(value)) {
    showToast('Already in the list', 'error');
    return;
  }

  currentConfig[field].push(value);
  input.value = '';
  refreshTags(field);
}

function refreshTags(field) {
  const containerMap = {
    'assets': 'assets-tags',
    'gaming_tokens': 'gaming-tokens-tags',
    'timeframes': 'timeframes-tags',
    'scalp_timeframes': 'scalp-timeframes-tags',
  };

  const containerId = containerMap[field];
  if (!containerId) return;

  const container = document.getElementById(containerId);
  if (!container) return;

  container.innerHTML = '';
  (currentConfig[field] || []).forEach(val => {
    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.innerHTML = `${val} <span class="remove-tag" onclick="removeTag('${field}', '${val}')">&times;</span>`;
    container.appendChild(tag);
  });
}

// ---- Reset Config ----
async function resetConfig() {
  if (!confirm('Reset ALL settings to defaults? This cannot be undone.')) return;

  try {
    const res = await fetch('/api/config/reset', { method: 'POST' });
    const result = await res.json();
    if (result.status === 'ok') {
      showToast('Configuration reset to defaults', 'success');
      location.reload();
    }
  } catch (e) {
    showToast('Error resetting: ' + e.message, 'error');
  }
}

// ---- Export Config ----
async function exportConfig() {
  try {
    const res = await fetch('/api/config/export');
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'yanagiba-config.json';
    a.click();
    URL.revokeObjectURL(url);
    showToast('Config exported!', 'success');
  } catch (e) {
    showToast('Export failed: ' + e.message, 'error');
  }
}

// ---- Import Config ----
async function importConfig(event) {
  const file = event.target.files[0];
  if (!file) return;

  try {
    const text = await file.text();
    const data = JSON.parse(text);

    const res = await fetch('/api/config/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const result = await res.json();

    if (result.status === 'ok') {
      showToast('Config imported! Reloading...', 'success');
      setTimeout(() => location.reload(), 1000);
    }
  } catch (e) {
    showToast('Import failed: ' + e.message, 'error');
  }
}

// ---- Toast ----
function showToast(message, type) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.className = `toast toast-${type} show`;
  setTimeout(() => { toast.classList.remove('show'); }, 3000);
}

// ---- Sync sandbox toggles ----
['cfg-sandbox', 'cfg-sandbox-exchange'].forEach(id => {
  const el = document.getElementById(id);
  if (el) {
    el.addEventListener('change', () => {
      const other = id === 'cfg-sandbox' ? 'cfg-sandbox-exchange' : 'cfg-sandbox';
      const otherEl = document.getElementById(other);
      if (otherEl) otherEl.checked = el.checked;
    });
  }
});

// ============================================
//  LIVE TRADING DASHBOARD
// ============================================

// ---- Helpers ----
function formatUSD(val) {
  if (val === undefined || val === null) return '--';
  return '$' + Number(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatPnL(val) {
  if (val === undefined || val === null) return '--';
  const prefix = val >= 0 ? '+' : '';
  return prefix + '$' + Math.abs(val).toFixed(2);
}

function pnlClass(val) {
  if (val > 0) return 'pnl-positive';
  if (val < 0) return 'pnl-negative';
  return 'pnl-zero';
}

function dirClass(dir) {
  const d = (dir || '').toLowerCase();
  if (d === 'long' || d === 'buy') return 'direction-long';
  return 'direction-short';
}

function statusChip(status) {
  const s = (status || 'unknown').toLowerCase();
  let cls = '';
  if (s === 'placed') cls = 'status-placed';
  else if (s === 'simulated') cls = 'status-simulated';
  else if (s === 'closed') cls = 'status-closed';
  else if (s === 'rejected') cls = 'status-rejected';
  else cls = 'status-placed';
  return `<span class="status-chip ${cls}">${status}</span>`;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ---- Fetch Live Portfolio ----
async function refreshPortfolio() {
  try {
    const res = await fetch('/api/portfolio');
    const p = await res.json();

    const setText = (id, text) => {
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    };

    setText('live-total-value', formatUSD(p.total_value));
    setText('live-cash', formatUSD(p.cash));
    setText('live-exposure', (p.total_exposure_pct * 100).toFixed(1) + '%');
    setText('live-daily-pnl', formatPnL(p.daily_pnl));

    // Color the PnL box
    const pnlBox = document.getElementById('pnl-box');
    if (pnlBox) {
      pnlBox.className = 'stat-box ' + (p.daily_pnl >= 0 ? 'positive' : 'negative');
    }
  } catch (e) {
    console.error('Portfolio fetch error:', e);
  }
}

// ---- Fetch Open Positions ----
async function refreshPositions() {
  try {
    const res = await fetch('/api/positions');
    const positions = await res.json();

    // Update counts
    const countBadge = document.getElementById('pos-count-badge');
    if (countBadge) countBadge.textContent = positions.length + ' open';
    const countBadge2 = document.getElementById('positions-count-badge');
    if (countBadge2) countBadge2.textContent = positions.length + ' open';
    const openCount = document.getElementById('live-open-count');
    if (openCount) openCount.textContent = positions.length;

    // Build table for overview
    const overviewContainer = document.getElementById('overview-positions-list');
    const fullContainer = document.getElementById('positions-table-container');

    if (positions.length === 0) {
      const emptyHtml = '<div class="empty-state">No open positions</div>';
      if (overviewContainer) overviewContainer.innerHTML = emptyHtml;
      if (fullContainer) fullContainer.innerHTML = emptyHtml;
      return;
    }

    const tableHtml = `
      <table class="positions-table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th>Entry</th>
            <th>SL</th>
            <th>TP</th>
            <th>Margin</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          ${positions.map(p => `
            <tr>
              <td>${escapeHtml(p.symbol || '?')}</td>
              <td class="${dirClass(p.side)}">${(p.side || '?').toUpperCase()}</td>
              <td>${formatUSD(p.entry_price)}</td>
              <td>${formatUSD(p.stop_loss)}</td>
              <td>${p.take_profits && p.take_profits.length > 0 ? formatUSD(p.take_profits[0]) : '--'}</td>
              <td>${formatUSD(p.margin_usd)}</td>
              <td>${statusChip(p.status || 'open')}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>`;

    if (overviewContainer) overviewContainer.innerHTML = tableHtml;
    if (fullContainer) fullContainer.innerHTML = tableHtml;
  } catch (e) {
    console.error('Positions fetch error:', e);
  }
}

// ---- Fetch Performance ----
async function refreshPerformance() {
  try {
    const res = await fetch('/api/performance');
    const perf = await res.json();

    const setText = (id, text) => {
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    };

    // Overview stats
    setText('live-total-trades', perf.total_trades);
    setText('live-win-rate', perf.win_rate + '%');
    setText('live-total-pnl', formatPnL(perf.total_pnl));

    // Color total PnL
    const totalPnlBox = document.getElementById('totalpnl-box');
    if (totalPnlBox) {
      totalPnlBox.className = 'stat-box ' + (perf.total_pnl >= 0 ? 'positive' : 'negative');
    }

    // Performance page
    setText('perf-win-rate', perf.win_rate + '%');
    setText('perf-total-pnl', formatPnL(perf.total_pnl));
    setText('perf-max-dd', perf.max_drawdown + '%');
    setText('perf-total-trades', perf.total_trades);

    const perfPnlBox = document.getElementById('perf-pnl-box');
    if (perfPnlBox) {
      perfPnlBox.className = 'stat-box ' + (perf.total_pnl >= 0 ? 'positive' : 'negative');
    }

    // Strategy breakdown
    const container = document.getElementById('strategy-breakdown');
    const strats = perf.strategy_stats || {};
    const stratNames = Object.keys(strats);

    if (stratNames.length === 0 || !container) {
      if (container) container.innerHTML = '<div class="empty-state">No strategy data yet</div>';
      return;
    }

    // Find max abs PnL for bar scaling
    const maxPnl = Math.max(1, ...stratNames.map(s => Math.abs(strats[s].pnl)));

    container.innerHTML = `
      <table class="strategy-table">
        <thead>
          <tr>
            <th>Strategy</th>
            <th>Trades</th>
            <th>Wins</th>
            <th>Losses</th>
            <th>Win Rate</th>
            <th>P&L</th>
          </tr>
        </thead>
        <tbody>
          ${stratNames.sort((a, b) => strats[b].pnl - strats[a].pnl).map(name => {
            const s = strats[name];
            const wr = s.trades > 0 ? (s.wins / s.trades * 100).toFixed(1) : '0';
            return `
              <tr>
                <td>${escapeHtml(name)}</td>
                <td>${s.trades}</td>
                <td>${s.wins}</td>
                <td>${s.losses}</td>
                <td>${wr}%</td>
                <td class="${pnlClass(s.pnl)}">${formatPnL(s.pnl)}</td>
              </tr>`;
          }).join('')}
        </tbody>
      </table>`;
  } catch (e) {
    console.error('Performance fetch error:', e);
  }
}

// ---- Fetch Recent Trades ----
async function refreshTrades() {
  try {
    const res = await fetch('/api/trades');
    const trades = await res.json();

    // Overview recent (last 5)
    const overviewContainer = document.getElementById('overview-recent-trades');
    const historyContainer = document.getElementById('history-table-container');

    if (trades.length === 0) {
      const emptyHtml = '<div class="empty-state">No trades yet</div>';
      if (overviewContainer) overviewContainer.innerHTML = emptyHtml;
      if (historyContainer) historyContainer.innerHTML = emptyHtml;
      return;
    }

    function buildTradeTable(tradeList) {
      return `
        <table class="trades-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Symbol</th>
              <th>Direction</th>
              <th>Strategy</th>
              <th>Entry</th>
              <th>SL</th>
              <th>Status</th>
              <th>P&L</th>
            </tr>
          </thead>
          <tbody>
            ${tradeList.map(t => {
              const time = t.timestamp ? t.timestamp.substring(0, 16).replace('T', ' ') : '--';
              return `
                <tr>
                  <td>${time}</td>
                  <td>${escapeHtml(t.symbol || '?')}</td>
                  <td class="${dirClass(t.direction)}">${(t.direction || t.close_type || '--').toUpperCase()}</td>
                  <td>${escapeHtml(t.strategy || '--')}</td>
                  <td>${t.entry ? formatUSD(t.entry) : '--'}</td>
                  <td>${t.stop_loss ? formatUSD(t.stop_loss) : '--'}</td>
                  <td>${statusChip(t.status || 'closed')}</td>
                  <td class="${pnlClass(t.pnl)}">${t.pnl !== undefined ? formatPnL(t.pnl) : '--'}</td>
                </tr>`;
            }).join('')}
          </tbody>
        </table>`;
    }

    if (overviewContainer) overviewContainer.innerHTML = buildTradeTable(trades.slice(0, 5));
    if (historyContainer) historyContainer.innerHTML = buildTradeTable(trades);
  } catch (e) {
    console.error('Trades fetch error:', e);
  }
}

// ---- Fetch Bot Status ----
async function refreshBotStatus() {
  try {
    const res = await fetch('/api/bot/status');
    const data = await res.json();
    const dot = document.getElementById('bot-dot');
    const text = document.getElementById('bot-status-text');
    if (dot && text) {
      if (data.running) {
        dot.className = 'status-dot online';
        text.textContent = 'Bot is running';
      } else {
        dot.className = 'status-dot offline';
        text.textContent = 'Bot is stopped';
      }
    }
  } catch (e) {
    console.error('Bot status fetch error:', e);
  }
}

// ---- Fetch Logs ----
async function refreshLogs() {
  try {
    const res = await fetch('/api/logs?lines=100');
    const lines = await res.json();
    const container = document.getElementById('log-container');
    if (!container) return;

    if (lines.length === 0) {
      container.innerHTML = '<div class="empty-state" style="padding: 40px;">No logs available</div>';
      return;
    }

    container.innerHTML = lines.map(line => {
      let cls = 'log-line';
      if (line.includes('ERROR')) cls += ' log-error';
      else if (line.includes('WARNING')) cls += ' log-warning';
      else if (line.includes('APPROVED') || line.includes('POSITION CLOSED') || line.includes('ORDER STATUS'))
        cls += ' log-info-highlight';
      return `<div class="${cls}">${escapeHtml(line)}</div>`;
    }).join('');

    // Auto-scroll to bottom
    container.scrollTop = container.scrollHeight;
  } catch (e) {
    console.error('Logs fetch error:', e);
  }
}

// ---- Refresh All Live Data ----
async function refreshAllLiveData() {
  await Promise.all([
    refreshPortfolio(),
    refreshPositions(),
    refreshPerformance(),
    refreshTrades(),
    refreshBotStatus(),
  ]);
}

// ---- Auto-refresh every 10 seconds ----
let liveRefreshInterval = null;

function startLiveRefresh() {
  refreshAllLiveData();
  liveRefreshInterval = setInterval(refreshAllLiveData, 10000);
}

function stopLiveRefresh() {
  if (liveRefreshInterval) {
    clearInterval(liveRefreshInterval);
    liveRefreshInterval = null;
  }
}

// ============================================
//  CONNECT FOUR MULTIPLAYER GAME
// ============================================

let c4MyPlayer = 0;  // 0=spectator, 1=red, 2=yellow
let c4PlayerId = 'p_' + Math.random().toString(36).substring(2, 10);
let c4LastUpdate = 0;
let c4PollInterval = null;

function c4BuildBoard() {
  const board = document.getElementById('c4-board');
  if (!board) return;
  board.innerHTML = '';
  for (let col = 0; col < 7; col++) {
    const colDiv = document.createElement('div');
    colDiv.className = 'c4-col';
    colDiv.dataset.col = col;
    colDiv.addEventListener('click', () => c4Drop(col));
    for (let row = 0; row < 6; row++) {
      const cell = document.createElement('div');
      cell.className = 'c4-cell';
      cell.id = `c4-${row}-${col}`;
      colDiv.appendChild(cell);
    }
    board.appendChild(colDiv);
  }
}

async function c4Join() {
  try {
    const res = await fetch('/api/game/join', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({player_id: c4PlayerId}),
    });
    const data = await res.json();
    c4MyPlayer = data.player || 0;
    const label = document.getElementById('c4-you-are');
    if (label) {
      if (c4MyPlayer === 1) label.innerHTML = 'You are <span class="c4-dot c4-red" style="display:inline-block;vertical-align:middle;"></span> <b>Red</b>';
      else if (c4MyPlayer === 2) label.innerHTML = 'You are <span class="c4-dot c4-yellow" style="display:inline-block;vertical-align:middle;"></span> <b>Yellow</b>';
      else label.textContent = 'Game full — spectating';
    }
  } catch (e) {
    console.error('Join error:', e);
  }
}

async function c4Poll() {
  try {
    const res = await fetch('/api/game/state');
    const state = await res.json();

    if (state.updated_at === c4LastUpdate) return;
    c4LastUpdate = state.updated_at;

    // Update board cells
    for (let r = 0; r < 6; r++) {
      for (let c = 0; c < 7; c++) {
        const cell = document.getElementById(`c4-${r}-${c}`);
        if (!cell) continue;
        cell.classList.remove('c4-red', 'c4-yellow', 'c4-winning');
        if (state.board[r][c] === 1) cell.classList.add('c4-red');
        else if (state.board[r][c] === 2) cell.classList.add('c4-yellow');
      }
    }

    // Highlight last move
    if (state.last_move) {
      const lastCell = document.getElementById(`c4-${state.last_move.row}-${state.last_move.col}`);
      if (lastCell) lastCell.classList.add('c4-last-move');
    }

    // Update turn indicator
    const turnEl = document.getElementById('c4-turn');
    if (turnEl) {
      if (state.winner === 1) {
        turnEl.innerHTML = '<span class="c4-dot c4-red"></span> Red Wins!';
        turnEl.className = 'c4-turn-indicator c4-win';
      } else if (state.winner === 2) {
        turnEl.innerHTML = '<span class="c4-dot c4-yellow"></span> Yellow Wins!';
        turnEl.className = 'c4-turn-indicator c4-win';
      } else if (state.winner === 3) {
        turnEl.textContent = "It's a Draw!";
        turnEl.className = 'c4-turn-indicator c4-draw';
      } else if (state.players < 2) {
        turnEl.textContent = 'Waiting for opponent...';
        turnEl.className = 'c4-turn-indicator';
      } else if (state.current_player === c4MyPlayer) {
        turnEl.textContent = 'Your turn!';
        turnEl.className = 'c4-turn-indicator c4-your-turn';
      } else {
        turnEl.textContent = "Opponent's turn";
        turnEl.className = 'c4-turn-indicator';
      }
    }
  } catch (e) {
    console.error('Poll error:', e);
  }
}

async function c4Drop(col) {
  if (c4MyPlayer === 0) return;
  try {
    const res = await fetch('/api/game/move', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({player_id: c4PlayerId, col: col}),
    });
    if (res.ok) c4Poll();
  } catch (e) {
    console.error('Move error:', e);
  }
}

async function c4Reset() {
  try {
    await fetch('/api/game/reset', {method: 'POST'});
    c4MyPlayer = 0;
    await c4Join();
    c4Poll();
  } catch (e) {
    console.error('Reset error:', e);
  }
}

function c4Init() {
  c4BuildBoard();
  c4Join();
  c4Poll();
  c4PollInterval = setInterval(c4Poll, 1000);
}

// ---- Init ----
loadConfig();
startLiveRefresh();
c4Init();
