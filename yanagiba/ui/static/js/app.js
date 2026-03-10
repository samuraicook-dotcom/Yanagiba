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
           'macd_signal', 'bb_period', 'max_correlated_trades'].includes(field)) {
        val = Math.round(val);
      }
      data[field] = val;
    }
  });

  // Boolean fields
  const boolFields = [
    'use_limit_entry', 'use_trailing_stop', 'enable_gaming_sector',
    'use_session_filter', 'use_weekend_filter',
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

// ---- Init ----
loadConfig();
