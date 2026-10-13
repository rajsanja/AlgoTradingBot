/**
 * SMC Algorithmic Trading Terminal - Frontend Logic
 * Live TradingView Lightweight Charts with Overlay Lines & Real-Time PnL Tracking
 */

// Global State
let chart = null;
let candlestickSeries = null;
let currentPriceLines = [];
let activeSymbol = 'BTC/USDT';
let activeTimeframe = '1h';
let dashboardData = null;
let pollTimer = null;
let klineTimer = null;
let positionToClose = null;

// Initialize on DOM Ready
document.addEventListener('DOMContentLoaded', () => {
  initChart();
  bindEvents();
  fetchStatus(true);
  
  // Real-time polling: Status every 2.5s, Klines every 12s
  pollTimer = setInterval(() => fetchStatus(false), 2500);
  klineTimer = setInterval(() => fetchKlines(activeSymbol, activeTimeframe, false), 12000);
  
  // Clock update
  updateClock();
  setInterval(updateClock, 1000);
});

/**
 * 1. Initialize TradingView Lightweight Chart
 */
function initChart() {
  const container = document.getElementById('tv-chart');
  if (!container) return;

  chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: container.clientHeight,
    layout: {
      background: { color: '#0e131f' },
      textColor: '#94a3b8',
      fontSize: 12,
      fontFamily: "'Inter', sans-serif"
    },
    grid: {
      vertLines: { color: 'rgba(255, 255, 255, 0.04)' },
      horzLines: { color: 'rgba(255, 255, 255, 0.04)' }
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: {
        color: 'rgba(255, 255, 255, 0.2)',
        width: 1,
        style: LightweightCharts.LineStyle.Dashed
      },
      horzLine: {
        color: 'rgba(255, 255, 255, 0.2)',
        width: 1,
        style: LightweightCharts.LineStyle.Dashed
      }
    },
    rightPriceScale: {
      borderColor: 'rgba(255, 255, 255, 0.08)',
      autoScale: true
    },
    timeScale: {
      borderColor: 'rgba(255, 255, 255, 0.08)',
      timeVisible: true,
      secondsVisible: false
    }
  });

  // Candlestick Series
  candlestickSeries = chart.addCandlestickSeries({
    upColor: '#00e676',
    downColor: '#ff3366',
    borderVisible: false,
    wickUpColor: '#00e676',
    wickDownColor: '#ff3366'
  });

  // Handle Resize
  window.addEventListener('resize', () => {
    if (chart && container) {
      chart.applyOptions({
        width: container.clientWidth,
        height: container.clientHeight
      });
    }
  });
}

/**
 * 2. Event Listeners Setup
 */
function bindEvents() {
  // Refresh Button
  document.getElementById('btn-refresh').addEventListener('click', () => {
    fetchStatus(true);
  });

  // Timeframe Buttons
  document.querySelectorAll('.tf-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeTimeframe = btn.dataset.tf;
      fetchKlines(activeSymbol, activeTimeframe, true);
    });
  });

  // Modal Cancel Buttons
  document.getElementById('modal-btn-cancel').addEventListener('click', closeModal);
  document.getElementById('modal-btn-cancel-x').addEventListener('click', closeModal);
  
  // Modal Confirm Close Trade
  document.getElementById('modal-btn-confirm').addEventListener('click', executeClosePosition);
}

/**
 * 3. Fetch Account Status, Positions, and Watchlist
 */
async function fetchStatus(isManual = false) {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    if (!data.success) return;

    dashboardData = data;
    renderHeader(data);
    renderStats(data);
    renderPositions(data);
    renderSymbolTabs(data);
    renderWatchlist(data);

    // Initial load or symbol selection
    if (isManual) {
      // If activeSymbol doesn't have an open position but another symbol does, focus on open position!
      const runningPositions = data.positions || [];
      if (runningPositions.length > 0 && !runningPositions.some(p => p.symbol === activeSymbol)) {
        activeSymbol = runningPositions[0].symbol;
      }
      fetchKlines(activeSymbol, activeTimeframe, true);
    }
  } catch (err) {
    console.error('Error fetching dashboard status:', err);
  }
}

/**
 * 4. Render Header Metrics
 */
function renderHeader(data) {
  const balanceEl = document.getElementById('hdr-balance');
  const pnlEl = document.getElementById('hdr-pnl');
  const pnlChip = document.getElementById('hdr-pnl-chip');

  balanceEl.textContent = `$${data.balance.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  const pnlUsd = data.total_pnl_usd;
  const pnlPct = data.total_pnl_pct;
  const sign = pnlUsd >= 0 ? '+' : '';

  pnlEl.textContent = `${sign}$${pnlUsd.toFixed(2)} (${sign}${pnlPct.toFixed(2)}%)`;

  if (pnlUsd >= 0) {
    pnlChip.classList.remove('negative');
  } else {
    pnlChip.classList.add('negative');
  }
}

/**
 * 5. Render Top Stats Cards
 */
function renderStats(data) {
  document.getElementById('stat-equity').textContent = `$${data.equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  
  const countEl = document.getElementById('stat-positions-count');
  const barEl = document.getElementById('stat-positions-bar');
  
  countEl.textContent = `${data.active_count} / ${data.max_trades} ACTIVE`;
  const pct = (data.active_count / data.max_trades) * 100;
  barEl.style.width = `${Math.min(pct, 100)}%`;
}

/**
 * 6. Render Symbol Switcher Tabs
 */
function renderSymbolTabs(data) {
  const container = document.getElementById('symbol-tabs');
  if (!container) return;

  const symbols = data.watchlist || [];
  container.innerHTML = '';

  symbols.forEach(item => {
    const tab = document.createElement('button');
    tab.className = `sym-tab ${item.symbol === activeSymbol ? 'active' : ''} ${item.is_active ? 'running-trade' : ''}`;
    
    let liveBadge = item.is_active ? `<span class="live-dot" title="Active Trade Running"></span>` : '';
    tab.innerHTML = `${liveBadge} ${item.symbol}`;

    tab.addEventListener('click', () => {
      document.querySelectorAll('.sym-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      activeSymbol = item.symbol;
      fetchKlines(activeSymbol, activeTimeframe, true);
    });

    container.appendChild(tab);
  });
}

/**
 * 7. Fetch Candlesticks & Draw Overlays
 */
async function fetchKlines(symbol, timeframe, showLoading = true) {
  const loadingOverlay = document.getElementById('chart-loading');
  const watermark = document.getElementById('chart-watermark');

  watermark.textContent = `${symbol} ${timeframe.toUpperCase()}`;

  if (showLoading && loadingOverlay) {
    loadingOverlay.classList.add('active');
  }

  try {
    const res = await fetch(`/api/klines?symbol=${encodeURIComponent(symbol)}&interval=${timeframe}`);
    const data = await res.json();
    if (!data.success) return;

    // Render candles
    if (candlestickSeries && data.candles && data.candles.length > 0) {
      candlestickSeries.setData(data.candles);
      if (showLoading) {
        chart.timeScale().fitContent();
      }
    }

    // Render 4H Structure Panel
    renderStructurePanel(data.levels_4h, symbol);

    // Draw Price Overlays (Entry, SL, TP, S/R)
    drawPriceOverlays(symbol, data.levels_4h);
  } catch (err) {
    console.error('Error fetching klines:', err);
  } finally {
    if (loadingOverlay) {
      loadingOverlay.classList.remove('active');
    }
  }
}

/**
 * 8. Draw Overlay Price Lines on Candlestick Chart
 */
function drawPriceOverlays(symbol, levels_4h) {
  // Clear old lines
  currentPriceLines.forEach(line => candlestickSeries.removePriceLine(line));
  currentPriceLines = [];

  // 1. Check if there is an active trade for this symbol
  const runningTrade = (dashboardData?.positions || []).find(p => p.symbol === symbol);

  if (runningTrade) {
    // Entry Price Line (Cyan / Blue)
    const entryLine = candlestickSeries.createPriceLine({
      price: runningTrade.entry,
      color: '#38bdf8',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
      title: `ENTRY (${runningTrade.side})`
    });
    currentPriceLines.push(entryLine);

    // Stop Loss Line (Red)
    const slLine = candlestickSeries.createPriceLine({
      price: runningTrade.stop_loss,
      color: '#ff3366',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: 'STOP LOSS'
    });
    currentPriceLines.push(slLine);

    // Take Profit Line (Green)
    const tpLine = candlestickSeries.createPriceLine({
      price: runningTrade.take_profit,
      color: '#00e676',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: 'TAKE PROFIT'
    });
    currentPriceLines.push(tpLine);
  }

  // 2. 4H Key Support & Resistance Lines
  if (levels_4h) {
    if (levels_4h.nearest_support) {
      const supLine = candlestickSeries.createPriceLine({
        price: levels_4h.nearest_support,
        color: '#eab308',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted,
        axisLabelVisible: true,
        title: '4H SUPPORT'
      });
      currentPriceLines.push(supLine);
    }

    if (levels_4h.nearest_resistance) {
      const resLine = candlestickSeries.createPriceLine({
        price: levels_4h.nearest_resistance,
        color: '#c084fc',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted,
        axisLabelVisible: true,
        title: '4H RESISTANCE'
      });
      currentPriceLines.push(resLine);
    }
  }
}

/**
 * 9. Render 4H Structure Panel
 */
function renderStructurePanel(levels, symbol) {
  document.getElementById('struct-symbol-badge').textContent = symbol;
  const biasBadge = document.getElementById('struct-bias-badge');

  if (!levels) {
    biasBadge.textContent = 'ANALYZING';
    biasBadge.className = 'struct-bias-badge';
    return;
  }

  const bias = levels.structure_bias || 'NEUTRAL';
  biasBadge.textContent = bias;
  biasBadge.className = `struct-bias-badge ${bias.toLowerCase()}`;

  document.getElementById('struct-ema20').textContent = levels.ema20 ? `$${levels.ema20.toLocaleString('en-US', { minimumFractionDigits: 2 })}` : '-';
  document.getElementById('struct-support').textContent = levels.nearest_support ? `$${levels.nearest_support.toLocaleString('en-US', { minimumFractionDigits: 2 })}` : '-';
  document.getElementById('struct-ssl').textContent = levels.major_swing_low ? `$${levels.major_swing_low.toLocaleString('en-US', { minimumFractionDigits: 2 })}` : '-';
  document.getElementById('struct-resistance').textContent = levels.nearest_resistance ? `$${levels.nearest_resistance.toLocaleString('en-US', { minimumFractionDigits: 2 })}` : '-';
  document.getElementById('struct-bsl').textContent = levels.major_swing_high ? `$${levels.major_swing_high.toLocaleString('en-US', { minimumFractionDigits: 2 })}` : '-';

  if (levels.order_block) {
    document.getElementById('struct-ob').textContent = `${levels.order_block.type.toUpperCase()} [$${levels.order_block.bottom.toFixed(1)}-$${levels.order_block.top.toFixed(1)}]`;
  } else {
    document.getElementById('struct-ob').textContent = 'None detected';
  }

  if (levels.fvg) {
    document.getElementById('struct-fvg').textContent = `${levels.fvg.type.toUpperCase()} [$${levels.fvg.bottom.toFixed(1)}-$${levels.fvg.top.toFixed(1)}]`;
  } else {
    document.getElementById('struct-fvg').textContent = 'None detected';
  }
}

/**
 * 10. Render Active Running Positions Cards
 */
function renderPositions(data) {
  const container = document.getElementById('positions-list');
  const counterBadge = document.getElementById('pos-counter-badge');
  if (!container) return;

  const positions = data.positions || [];
  counterBadge.textContent = `${positions.length} OPEN`;

  if (positions.length === 0) {
    container.innerHTML = `
      <div class="empty-positions-state">
        <div class="empty-icon">💤</div>
        <div style="font-weight: 600; margin-bottom: 4px;">No Open Positions</div>
        <div style="font-size: 11px;">Bot is scanning 4H market structure and 1H FVGs quietly at candle close.</div>
      </div>
    `;
    return;
  }

  container.innerHTML = '';

  positions.forEach(pos => {
    const card = document.createElement('div');
    const isLong = pos.side.toUpperCase() === 'LONG';
    card.className = `trade-card ${isLong ? 'side-long' : 'side-short'}`;

    const sign = pos.pnl_usd >= 0 ? '+' : '';
    const pnlClass = pos.pnl_usd >= 0 ? 'text-success' : 'text-danger';

    card.innerHTML = `
      <div class="trade-card-header">
        <div class="trade-sym-title">
          <span>${pos.symbol}</span>
          <span class="side-badge ${isLong ? 'long' : 'short'}">${pos.side}</span>
          <span class="lev-badge">${pos.leverage}x</span>
        </div>
      </div>

      <div class="trade-pnl-banner">
        <div>
          <div class="td-label">UNREALIZED PnL</div>
          <div class="pnl-amount mono ${pnlClass}">${sign}$${pos.pnl_usd.toFixed(2)}</div>
        </div>
        <div class="pnl-percent mono ${pnlClass}">${sign}${pos.pnl_pct.toFixed(2)}%</div>
      </div>

      <div class="trade-details-grid">
        <div class="trade-detail-cell">
          <span class="td-label">LOT SIZE / QTY</span>
          <span class="td-value mono">${pos.qty}</span>
        </div>
        <div class="trade-detail-cell">
          <span class="td-label">CURRENT PRICE</span>
          <span class="td-value mono">$${pos.current_price.toLocaleString('en-US', { minimumFractionDigits: 2 })}</span>
        </div>
        <div class="trade-detail-cell">
          <span class="td-label">ENTRY PRICE</span>
          <span class="td-value mono">$${pos.entry.toLocaleString('en-US', { minimumFractionDigits: 2 })}</span>
        </div>
        <div class="trade-detail-cell">
          <span class="td-label">R:R RATIO</span>
          <span class="td-value mono">${pos.risk_reward}</span>
        </div>
        <div class="trade-detail-cell">
          <span class="td-label">STOP LOSS (SL)</span>
          <span class="td-value mono text-danger">$${pos.stop_loss.toLocaleString('en-US', { minimumFractionDigits: 2 })}</span>
        </div>
        <div class="trade-detail-cell">
          <span class="td-label">TAKE PROFIT (TP)</span>
          <span class="td-value mono text-success">$${pos.take_profit.toLocaleString('en-US', { minimumFractionDigits: 2 })}</span>
        </div>
      </div>

      <div class="cloud-status-row">
        <span>☁️ Cloud Guard:</span>
        <strong class="${pos.cloud_protection ? 'text-success' : 'text-cyan'}">
          ${pos.cloud_protection ? 'Binance 24/7 Orders Active' : 'Protected'}
        </strong>
      </div>

      <button class="btn-close-position" data-sym="${pos.symbol}" data-side="${pos.side}" data-qty="${pos.qty}">
        <span>✕ Close Trade Now</span>
      </button>
    `;

    // Click on card switches the chart to this symbol!
    card.addEventListener('click', (e) => {
      if (e.target.closest('.btn-close-position')) return;
      if (activeSymbol !== pos.symbol) {
        activeSymbol = pos.symbol;
        document.querySelectorAll('.sym-tab').forEach(t => {
          t.classList.toggle('active', t.textContent.includes(pos.symbol));
        });
        fetchKlines(activeSymbol, activeTimeframe, true);
      }
    });

    // Close button opens confirm modal
    const closeBtn = card.querySelector('.btn-close-position');
    closeBtn.addEventListener('click', () => {
      openCloseModal(pos);
    });

    container.appendChild(card);
  });
}

/**
 * 11. Render Monitored Watchlist
 */
function renderWatchlist(data) {
  const container = document.getElementById('watchlist-list');
  if (!container) return;

  const watchlist = data.watchlist || [];
  container.innerHTML = '';

  watchlist.forEach(item => {
    const row = document.createElement('div');
    row.className = 'wl-item';

    const liveBadge = item.is_active ? `<span class="live-dot" title="Position Running"></span>` : '';

    row.innerHTML = `
      <div class="wl-item-left">
        ${liveBadge}
        <span>${item.symbol}</span>
      </div>
      <div class="wl-item-price mono">
        $${item.price.toLocaleString('en-US', { minimumFractionDigits: 2 })}
      </div>
    `;

    row.addEventListener('click', () => {
      if (activeSymbol !== item.symbol) {
        activeSymbol = item.symbol;
        document.querySelectorAll('.sym-tab').forEach(t => {
          t.classList.toggle('active', t.textContent.includes(item.symbol));
        });
        fetchKlines(activeSymbol, activeTimeframe, true);
      }
    });

    container.appendChild(row);
  });
}

/**
 * 12. Modal & Manual Trade Closure
 */
function openCloseModal(pos) {
  positionToClose = pos;
  document.getElementById('modal-pos-name').textContent = `${pos.symbol} (${pos.side} ${pos.qty})`;
  document.getElementById('modal-backdrop').classList.add('active');
}

function closeModal() {
  positionToClose = null;
  document.getElementById('modal-backdrop').classList.remove('active');
}

async function executeClosePosition() {
  if (!positionToClose) return;

  const btnConfirm = document.getElementById('modal-btn-confirm');
  btnConfirm.textContent = 'Closing on Binance...';
  btnConfirm.disabled = true;

  try {
    const res = await fetch('/api/close_position', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol: positionToClose.symbol,
        side: positionToClose.side,
        qty: positionToClose.qty
      })
    });

    const data = await res.json();
    if (data.success) {
      closeModal();
      fetchStatus(true);
    } else {
      alert(`Error closing position: ${data.error}`);
    }
  } catch (err) {
    alert(`Failed to close position: ${err}`);
  } finally {
    btnConfirm.textContent = 'Yes, Close Position Now';
    btnConfirm.disabled = false;
  }
}

/**
 * 13. UTC & Local Clock
 */
function updateClock() {
  const el = document.getElementById('footer-time');
  if (el) {
    const now = new Date();
    el.textContent = `Local: ${now.toLocaleTimeString()} | UTC: ${now.toISOString().slice(11, 19)}`;
  }
}
