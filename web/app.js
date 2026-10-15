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
let lastKlinesData = null;
let pollTimer = null;
let klineTimer = null;
let countdownSeconds = 0;
let targetScanTimestamp = 0; // Unix epoch target in seconds
let nextScanLocalStr = '';
let nextScanUtcStr = '';
let isBotPaused = false;

// Initialize on DOM Ready
document.addEventListener('DOMContentLoaded', () => {
  initChart();
  bindEvents();
  fetchStatus(true);
  
  // Real-time polling: Status every 2.5s, Klines every 12s
  pollTimer = setInterval(() => fetchStatus(false), 2500);
  klineTimer = setInterval(() => fetchKlines(activeSymbol, activeTimeframe, false), 12000);
  
  // Clock & Countdown update every 1 second
  updateClock();
  setInterval(updateClock, 1000);
  setInterval(updateCountdown, 1000);
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

  // Emergency Pause/Resume Bot Button
  const pauseBtn = document.getElementById('btn-toggle-pause');
  if (pauseBtn) {
    pauseBtn.addEventListener('click', togglePauseBot);
  }

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
    if (data.next_scan_target_ts) {
      targetScanTimestamp = data.next_scan_target_ts;
    } else if (data.next_scan_seconds !== undefined) {
      targetScanTimestamp = Math.floor(Date.now() / 1000) + data.next_scan_seconds;
    }
    countdownSeconds = data.next_scan_seconds !== undefined ? data.next_scan_seconds : countdownSeconds;
    if (data.next_scan_local_time) nextScanLocalStr = data.next_scan_local_time;
    if (data.next_scan_utc_time) nextScanUtcStr = data.next_scan_utc_time;

    isBotPaused = !!data.is_paused;
    updatePauseButtonUI(isBotPaused);
    updateCountdown();

    renderHeader(data);
    renderStats(data);
    renderPositions(data);
    renderSymbolTabs(data);
    renderWatchlist(data);
    renderStructureCards(data, lastKlinesData);
    renderTradeHistory(data.trade_history || []);
    renderTerminalFeed(data.recent_events || []);

    // Initial load
    if (isManual) {
      const runningPositions = data.positions || [];
      if (!activeSymbol && runningPositions.length > 0) {
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

  // Today's Realized PnL Chip
  const todayRealizedEl = document.getElementById('hdr-today-realized');
  if (todayRealizedEl) {
    const todayPnl = data.today_realized_pnl || 0;
    const todaySign = todayPnl >= 0 ? '+' : '';
    todayRealizedEl.textContent = `${todaySign}$${todayPnl.toFixed(2)}`;
    todayRealizedEl.style.color = todayPnl >= 0 ? 'var(--color-green)' : 'var(--color-red)';
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

  // Win Rate & Realized Card
  const winrateEl = document.getElementById('stat-winrate');
  if (winrateEl) {
    const totalCount = data.total_trades_count || (data.trade_history ? data.trade_history.length : 0);
    winrateEl.textContent = `${(data.win_rate || 0).toFixed(1)}% Win Rate (${totalCount} Trades)`;
  }
  const todayFootnote = document.getElementById('stat-today-footnote');
  if (todayFootnote) {
    const todayPnl = data.today_realized_pnl || 0;
    const sign = todayPnl >= 0 ? '+' : '';
    todayFootnote.textContent = `Today: ${sign}$${todayPnl.toFixed(2)}`;
    todayFootnote.style.color = todayPnl >= 0 ? 'var(--color-green)' : 'var(--color-red)';
  }

  // Bot Status & Scan Timer initialized via updateCountdown()
  updateCountdown();
}

/**
 * Switch Active Chart Symbol
 */
function switchActiveChartSymbol(symbol) {
  if (!symbol) return;
  activeSymbol = symbol;

  // Update tabs active styling
  document.querySelectorAll('.sym-tab').forEach(t => {
    t.classList.toggle('active', t.textContent.includes(symbol));
  });

  // Fetch candlesticks and update chart + 4H structure cards
  fetchKlines(activeSymbol, activeTimeframe, true);
}

/**
 * 6. Render Symbol Switcher Tabs
 */
function renderSymbolTabs(data) {
  const container = document.getElementById('symbol-tabs');
  if (!container) return;

  const symbols = data.watchlist || [];
  const existingTabs = container.querySelectorAll('.sym-tab');

  // If already rendered with matching count, update in-place without rebuilding DOM
  if (existingTabs.length === symbols.length) {
    existingTabs.forEach((tab, idx) => {
      const item = symbols[idx];
      const isActive = (item.symbol === activeSymbol);
      tab.classList.toggle('active', isActive);
      tab.classList.toggle('running-trade', !!item.is_active);
      const dot = tab.querySelector('.live-dot');
      if (item.is_active && !dot) {
        tab.insertAdjacentHTML('afterbegin', `<span class="live-dot" title="Active Trade Running"></span> `);
      } else if (!item.is_active && dot) {
        dot.remove();
      }
    });
    return;
  }

  // First time build
  container.innerHTML = '';
  symbols.forEach(item => {
    const tab = document.createElement('button');
    const isActive = (item.symbol === activeSymbol);
    tab.className = `sym-tab ${isActive ? 'active' : ''} ${item.is_active ? 'running-trade' : ''}`;
    
    let liveBadge = item.is_active ? `<span class="live-dot" title="Active Trade Running"></span> ` : '';
    tab.innerHTML = `${liveBadge}${item.symbol}`;

    tab.addEventListener('click', () => {
      switchActiveChartSymbol(item.symbol);
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

  if (watermark) {
    watermark.textContent = `${symbol} ${timeframe.toUpperCase()}`;
  }

  if (showLoading && loadingOverlay) {
    loadingOverlay.classList.add('active');
  }

  try {
    const res = await fetch(`/api/klines?symbol=${encodeURIComponent(symbol)}&interval=${timeframe}`);
    const data = await res.json();
    if (!data.success) return;

    lastKlinesData = data;

    // Render candles on chart
    if (candlestickSeries && data.candles && data.candles.length > 0) {
      // 1. Remove all old price lines BEFORE changing series data to prevent price scale corruption!
      if (currentPriceLines && currentPriceLines.length > 0) {
        currentPriceLines.forEach(line => {
          try {
            candlestickSeries.removePriceLine(line);
          } catch (e) {}
        });
        currentPriceLines = [];
      }

      // 2. Clear old candles
      candlestickSeries.setData([]);

      // 3. Dynamic price precision based on asset magnitude (e.g. BTC vs XAU vs SOL vs ETH)
      const lastCandle = data.candles[data.candles.length - 1];
      const samplePrice = lastCandle ? lastCandle.close : 100;
      const precision = samplePrice < 1 ? 4 : (samplePrice < 10 ? 3 : 2);
      const minMove = 1 / Math.pow(10, precision);

      candlestickSeries.applyOptions({
        priceFormat: {
          type: 'price',
          precision: precision,
          minMove: minMove,
        },
      });

      // 4. Set new candles
      candlestickSeries.setData(data.candles);

      // 5. Draw overlay lines (Entry, SL, TP, 4H Support, 4H Resistance) for the active symbol
      drawPriceOverlays(symbol, data.levels_4h);

      // 6. Force time scale and right price scale autoscale so candles fill the canvas cleanly
      chart.timeScale().fitContent();
      chart.priceScale('right').applyOptions({
        autoScale: true,
      });
    }

    // Render 4H Structure Cards for all running trades + active symbol
    renderStructureCards(dashboardData, data);
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
  // Clear any existing lines
  if (currentPriceLines && currentPriceLines.length > 0) {
    currentPriceLines.forEach(line => {
      try {
        candlestickSeries.removePriceLine(line);
      } catch (e) {}
    });
    currentPriceLines = [];
  }

  // 1. Check if there is an active running trade for this symbol
  const runningTrade = (dashboardData?.positions || []).find(p => p.symbol === symbol);

  if (runningTrade) {
    // Entry Price Line (Cyan / Blue)
    const entryLine = candlestickSeries.createPriceLine({
      price: runningTrade.entry,
      color: '#38bdf8',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
      title: `ENTRY (${runningTrade.side}) $${runningTrade.entry.toFixed(2)}`
    });
    currentPriceLines.push(entryLine);

    // Stop Loss Line (Red)
    const slLine = candlestickSeries.createPriceLine({
      price: runningTrade.stop_loss,
      color: '#ff3366',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: `SL $${runningTrade.stop_loss.toFixed(2)}`
    });
    currentPriceLines.push(slLine);

    // Take Profit Line (Green)
    const tpLine = candlestickSeries.createPriceLine({
      price: runningTrade.take_profit,
      color: '#00e676',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: `TP $${runningTrade.take_profit.toFixed(2)}`
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
        title: `4H SUP $${levels_4h.nearest_support.toFixed(2)}`
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
        title: `4H RES $${levels_4h.nearest_resistance.toFixed(2)}`
      });
      currentPriceLines.push(resLine);
    }
  }
}

/**
 * 9. Render 4H Structure Cards for All Running Trades + Active Chart Symbol
 */
function renderStructureCards(data, klinesData) {
  const container = document.getElementById('structures-container');
  const countBadge = document.getElementById('struct-count-badge');
  if (!container) return;

  const positions = data?.positions || [];
  
  if (countBadge) {
    countBadge.textContent = `${positions.length} ACTIVE TRADES`;
  }

  // Collect list of cards to render:
  // 1. Every open running position (e.g. BTC/USDT and XAU/USDT)
  // 2. If activeSymbol is NOT among the open positions, append an analysis card for activeSymbol!
  const cardsList = [];

  positions.forEach(pos => {
    // If this position is the currently active chart symbol and klinesData has fresh levels, prefer them or fallback to pos.levels_4h
    const levels = (pos.symbol === activeSymbol && klinesData?.levels_4h) 
      ? klinesData.levels_4h 
      : (pos.levels_4h || null);

    cardsList.push({
      symbol: pos.symbol,
      isRunningTrade: true,
      position: pos,
      levels: levels,
      isActiveChart: (pos.symbol === activeSymbol)
    });
  });

  // If currently viewed chart symbol is not in open positions, add it as a scan card
  const isViewingOpenTrade = positions.some(p => p.symbol === activeSymbol);
  if (!isViewingOpenTrade && activeSymbol) {
    cardsList.push({
      symbol: activeSymbol,
      isRunningTrade: false,
      position: null,
      levels: klinesData?.levels_4h || null,
      isActiveChart: true
    });
  }

  if (cardsList.length === 0) {
    container.innerHTML = `
      <div class="empty-positions-state">
        <div class="empty-icon">📐</div>
        <div style="font-weight: 600; margin-bottom: 4px;">No Active Trades Running</div>
        <div style="font-size: 11px;">Select any symbol from tabs or watchlist above to view 4H market structure.</div>
      </div>
    `;
    return;
  }

  container.innerHTML = '';

  cardsList.forEach(item => {
    const levels = item.levels;
    const isRunning = item.isRunningTrade;
    const pos = item.position;
    const isChartActive = item.isActiveChart;

    const bias = levels?.structure_bias || 'NEUTRAL';
    const biasClass = bias.toLowerCase();

    const card = document.createElement('div');
    card.className = `structure-card ${isChartActive ? 'active-chart-card' : ''}`;

    const formatPrice = (val) => {
      if (val === undefined || val === null || isNaN(val)) return '-';
      const precision = val < 1 ? 4 : (val < 10 ? 3 : 2);
      return `$${val.toLocaleString('en-US', { minimumFractionDigits: precision, maximumFractionDigits: precision })}`;
    };

    let obText = 'None detected';
    if (levels?.order_block) {
      obText = `${levels.order_block.type.toUpperCase()} [${formatPrice(levels.order_block.bottom)} - ${formatPrice(levels.order_block.top)}]`;
    }

    let fvgText = 'None detected';
    if (levels?.fvg) {
      fvgText = `${levels.fvg.type.toUpperCase()} [${formatPrice(levels.fvg.bottom)} - ${formatPrice(levels.fvg.top)}]`;
    }

    let tradeBadgeHtml = '';
    let cloudSLTPHtml = '';

    if (isRunning && pos) {
      const isLong = pos.side.toUpperCase() === 'LONG';
      const sign = pos.pnl_usd >= 0 ? '+' : '';
      const pnlClass = pos.pnl_usd >= 0 ? 'text-success' : 'text-danger';

      tradeBadgeHtml = `
        <span class="struct-trade-badge ${isLong ? 'long' : 'short'}">
          ${isLong ? '🟢' : '🔴'} RUNNING ${pos.side} (${pos.qty}) • <strong class="${pnlClass}">${sign}$${pos.pnl_usd.toFixed(2)} (ROE: ${sign}${pos.pnl_pct.toFixed(2)}%)</strong>
        </span>
      `;

      cloudSLTPHtml = `
        <div class="struct-cloud-box">
          <div class="cloud-box-header">
            <span>☁️ Native Binance Cloud Orders:</span>
            <strong class="text-success">Active 24/7 (R:R ${pos.risk_reward})</strong>
          </div>
          <div class="cloud-box-levels">
            <span>Entry: <strong class="text-cyan mono">${formatPrice(pos.entry)}</strong></span>
            <span>Stop Loss: <strong class="text-danger mono">${formatPrice(pos.stop_loss)}</strong></span>
            <span>Take Profit: <strong class="text-success mono">${formatPrice(pos.take_profit)}</strong></span>
            <span>Liquidation: <strong class="text-muted mono">${formatPrice(pos.liquidation_price)}</strong></span>
          </div>
        </div>
      `;
    } else {
      tradeBadgeHtml = `
        <span class="struct-trade-badge watchlist">
          🔍 WATCHLIST SCAN (NO POSITION)
        </span>
      `;
    }

    card.innerHTML = `
      <div class="structure-card-header">
        <div class="struct-title-left">
          <div class="struct-symbol-tag">
            <span class="symbol-name">${item.symbol}</span>
            ${tradeBadgeHtml}
          </div>
        </div>

        <div class="struct-actions-right">
          <span class="struct-bias-badge ${biasClass}">${bias}</span>
          <button class="btn-chart-switch ${isChartActive ? 'active' : ''}">
            ${isChartActive ? '● Viewing Chart' : '📊 View Chart'}
          </button>
        </div>
      </div>

      <div class="structure-grid">
        <div class="struct-item">
          <span class="struct-label">4H 20 EMA BIAS</span>
          <span class="struct-val mono">${formatPrice(levels?.ema20)}</span>
          <span class="struct-subtext ${levels && levels.current_price >= levels.ema20 ? 'text-success' : 'text-danger'}">
            ${levels ? (levels.current_price >= levels.ema20 ? '▲ Above 20 EMA (Bullish)' : '▼ Below 20 EMA (Bearish)') : '-'}
          </span>
        </div>

        <div class="struct-item">
          <span class="struct-label">4H 50 EMA TREND</span>
          <span class="struct-val mono">${formatPrice(levels?.ema50)}</span>
          <span class="struct-subtext text-muted">Baseline Institutional Trend</span>
        </div>

        <div class="struct-item highlight-support">
          <span class="struct-label">KEY 4H SUPPORT (FLOOR)</span>
          <span class="struct-val mono text-success">${formatPrice(levels?.nearest_support)}</span>
          <span class="struct-subtext text-success">Institutional Demand Zone</span>
        </div>

        <div class="struct-item highlight-resistance">
          <span class="struct-label">KEY 4H RESISTANCE (CEILING)</span>
          <span class="struct-val mono text-danger">${formatPrice(levels?.nearest_resistance)}</span>
          <span class="struct-subtext text-danger">Institutional Supply Zone</span>
        </div>

        <div class="struct-item">
          <span class="struct-label">MAJOR SWING LOW (SSL)</span>
          <span class="struct-val mono">${formatPrice(levels?.major_swing_low)}</span>
          <span class="struct-subtext text-muted">Sell-Side Liquidity Pool</span>
        </div>

        <div class="struct-item">
          <span class="struct-label">MAJOR SWING HIGH (BSL)</span>
          <span class="struct-val mono">${formatPrice(levels?.major_swing_high)}</span>
          <span class="struct-subtext text-muted">Buy-Side Liquidity Pool</span>
        </div>

        <div class="struct-item">
          <span class="struct-label">4H ORDER BLOCK (OB)</span>
          <span class="struct-val mono text-sm">${obText}</span>
          <span class="struct-subtext text-muted">Smart Money Mitigation Zone</span>
        </div>

        <div class="struct-item">
          <span class="struct-label">FAIR VALUE GAP (FVG)</span>
          <span class="struct-val mono text-sm">${fvgText}</span>
          <span class="struct-subtext text-muted">Imbalance Rebalance Target</span>
        </div>
      </div>

      ${cloudSLTPHtml}
    `;

    // Click handler to switch chart
    card.addEventListener('click', (e) => {
      if (activeSymbol !== item.symbol) {
        switchActiveChartSymbol(item.symbol);
      }
    });

    container.appendChild(card);
  });
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
        <div class="pnl-percent mono ${pnlClass}">ROE: ${sign}${pos.pnl_pct.toFixed(2)}%</div>
      </div>

      <div class="trade-details-grid">
        <div class="trade-detail-cell">
          <span class="td-label">LOT SIZE / QTY</span>
          <span class="td-value mono">${pos.qty}</span>
        </div>
        <div class="trade-detail-cell">
          <span class="td-label">MARK PRICE (BINANCE PnL)</span>
          <span class="td-value mono">$${(pos.mark_price || pos.current_price).toLocaleString('en-US', { minimumFractionDigits: 2 })}</span>
        </div>
        <div class="trade-detail-cell">
          <span class="td-label">ENTRY PRICE</span>
          <span class="td-value mono">$${pos.entry.toLocaleString('en-US', { minimumFractionDigits: 2 })}</span>
        </div>
        <div class="trade-detail-cell">
          <span class="td-label">POSITION MARGIN</span>
          <span class="td-value mono">$${(pos.initial_margin || 0).toFixed(2)} USDT</span>
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
        switchActiveChartSymbol(pos.symbol);
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
        switchActiveChartSymbol(item.symbol);
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

/**
 * 14. Countdown Clock Update (Smooth Absolute Millisecond Synchronization)
 */
function updateCountdown() {
  const chipEl = document.getElementById('hdr-countdown');
  const statEl = document.getElementById('stat-scan-timer');
  const botStatusEl = document.getElementById('stat-bot-status');

  if (isBotPaused) {
    if (chipEl) chipEl.textContent = 'PAUSED';
    if (statEl) {
      statEl.textContent = 'PAUSED ⏸️';
      statEl.className = 'stat-value mono text-danger';
    }
    if (botStatusEl) {
      botStatusEl.textContent = 'Trading Paused (No new setups entered)';
      botStatusEl.className = 'stat-footnote text-danger';
    }
    return;
  }

  const nowSec = Math.floor(Date.now() / 1000);
  let remaining = targetScanTimestamp > 0 ? (targetScanTimestamp - nowSec) : countdownSeconds;

  if (remaining <= 0) {
    if (chipEl) chipEl.textContent = 'SCANNING';
    if (statEl) {
      statEl.textContent = '⚡ SCANNING NOW';
      statEl.className = 'stat-value mono text-cyan';
    }
    if (botStatusEl) {
      botStatusEl.textContent = 'Analyzing 1H Candle Close & SMC Patterns...';
      botStatusEl.className = 'stat-footnote text-cyan';
    }
    return;
  }

  // Format as hours:mins:secs or mins:secs
  const hours = Math.floor(remaining / 3600);
  const mins = Math.floor((remaining % 3600) / 60);
  const secs = remaining % 60;

  const paddedMins = String(mins).padStart(2, '0');
  const paddedSecs = String(secs).padStart(2, '0');
  const formattedChip = hours > 0 ? `${hours}:${paddedMins}:${paddedSecs}` : `${paddedMins}:${paddedSecs}`;
  const formattedStat = hours > 0 ? `${hours}h ${mins}m ${secs}s` : `${mins}m ${secs}s`;

  if (chipEl) {
    chipEl.textContent = formattedChip;
    if (nextScanLocalStr) {
      chipEl.title = `Next 1H candle scan: ${nextScanLocalStr} Local (${nextScanUtcStr || 'UTC'})`;
    }
  }

  if (statEl) {
    statEl.textContent = formattedStat;
    statEl.className = 'stat-value mono text-cyan';
  }

  if (botStatusEl) {
    if (nextScanLocalStr) {
      botStatusEl.textContent = `Next 1H Close: ${nextScanLocalStr} (${nextScanUtcStr || 'UTC'})`;
      botStatusEl.className = 'stat-footnote text-success';
    } else {
      botStatusEl.textContent = 'Bot Active & Guarding';
      botStatusEl.className = 'stat-footnote text-success';
    }
  }
}

/**
 * 15. Emergency Pause / Resume Bot Control
 */
async function togglePauseBot() {
  const btn = document.getElementById('btn-toggle-pause');
  if (!btn) return;
  btn.disabled = true;

  try {
    const res = await fetch('/api/toggle_pause', { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      isBotPaused = data.is_paused;
      updatePauseButtonUI(isBotPaused);
      fetchStatus(false);
    }
  } catch (err) {
    console.error('Error toggling bot pause:', err);
  } finally {
    btn.disabled = false;
  }
}

function updatePauseButtonUI(paused) {
  const btn = document.getElementById('btn-toggle-pause');
  const icon = document.getElementById('pause-btn-icon');
  const text = document.getElementById('pause-btn-text');
  if (!btn || !icon || !text) return;

  if (paused) {
    btn.classList.add('paused');
    icon.textContent = '▶️';
    text.textContent = 'RESUME BOT';
    btn.title = 'Bot execution is PAUSED. Click to resume trading.';
  } else {
    btn.classList.remove('paused');
    icon.textContent = '⏸️';
    text.textContent = 'PAUSE BOT';
    btn.title = 'Bot execution is ACTIVE. Click to pause opening new trades.';
  }
}

/**
 * 16. Render Closed Trades History Table
 */
function renderTradeHistory(history) {
  const tbody = document.getElementById('trade-history-tbody');
  const countBadge = document.getElementById('hist-count-badge');
  const winrateChip = document.getElementById('hist-summary-winrate');
  const pnlChip = document.getElementById('hist-summary-pnl');
  if (!tbody) return;

  if (countBadge) countBadge.textContent = `${history.length} TRADES`;

  if (!history || history.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty-table-state">No closed trades recorded yet. Bot will record entries, exits, and PnL automatically.</td></tr>`;
    if (winrateChip) winrateChip.textContent = 'Win Rate: 0.0%';
    if (pnlChip) pnlChip.textContent = 'Total Realized: $0.00';
    return;
  }

  // Summary metrics
  let totalPnl = 0;
  let winCount = 0;
  history.forEach(t => {
    totalPnl += t.pnl_usd || 0;
    if ((t.pnl_usd || 0) > 0) winCount++;
  });
  const winRate = (winCount / history.length) * 100;

  if (winrateChip) winrateChip.textContent = `Win Rate: ${winRate.toFixed(1)}%`;
  if (pnlChip) {
    const pnlSign = totalPnl >= 0 ? '+' : '';
    pnlChip.textContent = `Total Realized: ${pnlSign}$${totalPnl.toFixed(2)}`;
    pnlChip.style.color = totalPnl >= 0 ? 'var(--color-green)' : 'var(--color-red)';
  }

  // Render rows
  tbody.innerHTML = history.map((trade, idx) => {
    const isLong = trade.side.toUpperCase() === 'LONG' || trade.side.toUpperCase() === 'BUY';
    const sideBadge = `<span class="${isLong ? 'badge-side-long' : 'badge-side-short'}">${isLong ? 'LONG' : 'SHORT'}</span>`;
    
    const pnlUsd = trade.pnl_usd || 0;
    const pnlPct = trade.pnl_pct || 0;
    const pnlSign = pnlUsd >= 0 ? '+' : '';
    const pnlColor = pnlUsd >= 0 ? 'var(--color-green)' : 'var(--color-red)';

    const entryStr = trade.entry ? `$${Number(trade.entry).toLocaleString('en-US', { minimumFractionDigits: 2 })}` : '-';
    const exitStr = trade.exit ? `$${Number(trade.exit).toLocaleString('en-US', { minimumFractionDigits: 2 })}` : '-';

    return `
      <tr>
        <td class="text-muted">#${trade.id || (idx + 1)}</td>
        <td><strong>${trade.symbol}</strong></td>
        <td>${sideBadge}</td>
        <td>${entryStr}</td>
        <td>${exitStr}</td>
        <td>${trade.qty}</td>
        <td style="color: ${pnlColor}; font-weight: 700;">${pnlSign}$${pnlUsd.toFixed(2)}</td>
        <td style="color: ${pnlColor}; font-weight: 700;">${pnlSign}${pnlPct.toFixed(2)}%</td>
        <td><span class="badge-reason">${trade.reason || 'Closed'}</span></td>
        <td class="text-muted text-sm">${trade.closed_at || '-'}</td>
      </tr>
    `;
  }).join('');
}

/**
 * 17. Render Live Bot Event Stream Feed
 */
function renderTerminalFeed(events) {
  const container = document.getElementById('terminal-feed');
  const countEl = document.getElementById('terminal-event-count');
  if (!container) return;

  if (countEl) countEl.textContent = `${events ? events.length : 0} events`;

  if (!events || events.length === 0) {
    container.innerHTML = `<div class="terminal-entry text-muted">[System] Live Bot Event Feed online. Monitoring trades & Binance Cloud orders...</div>`;
    return;
  }

  container.innerHTML = events.map(evt => {
    const rawType = (evt.type || 'INFO').toUpperCase();
    const typeClass = `type-${rawType.toLowerCase()}`;
    const tagClass = `tag-${rawType.toLowerCase()}`;
    return `<div class="terminal-entry ${typeClass}"><span class="ts">[${evt.timestamp}]</span><span class="terminal-tag ${tagClass}">${rawType}</span><span class="terminal-msg">${escapeHtml(evt.message)}</span></div>`;
  }).join('');
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
