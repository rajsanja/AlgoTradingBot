import sys
if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)

import os
import json
import time
import threading
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
import ccxt
from dotenv import load_dotenv

# Import analysis logic and state from trading_bot
from trading_bot import (
    SYMBOLS,
    MAX_CONCURRENT_TRADES,
    DEFAULT_LEVERAGE,
    exchange,
    sync_server_time,
    get_4h_levels_and_structure,
    close_demo_trade,
    cancel_algo_orders,
    record_closed_trade,
    load_trade_history,
    get_recent_events,
    is_bot_paused,
    toggle_bot_pause,
    get_next_scan_seconds,
    get_next_scan_target,
    log_bot_event
)

load_dotenv()

PORT = 5050
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')

# Fast in-memory cache for 4H SMC levels (30 seconds TTL)
_4h_levels_cache = {}

def get_cached_4h_levels(symbol, ttl_seconds=30):
    now = time.time()
    if symbol in _4h_levels_cache:
        cached_time, val = _4h_levels_cache[symbol]
        if now - cached_time < ttl_seconds:
            return val
    try:
        val = get_4h_levels_and_structure(symbol)
        _4h_levels_cache[symbol] = (now, val)
        return val
    except Exception as e:
        print(f"Error getting 4H levels for {symbol}: {e}")
        return None

def get_dashboard_data():
    """Fetches real-time Binance account status, positions, and cloud orders."""
    try:
        sync_server_time()
        
        # 1. Wallet Balance
        account_info = exchange.fapiPrivateV2GetAccount()
        usdt_balance = 0.0
        for asset in account_info.get('assets', []):
            if asset['asset'] == 'USDT':
                usdt_balance = float(asset['walletBalance'])
                break
        
        # 2. Open Positions
        positions_raw = exchange.fapiPrivateV2GetPositionRisk()
        positions = []
        total_pnl_usd = 0.0
        
        for pos in positions_raw:
            amt = float(pos['positionAmt'])
            if amt != 0:
                raw_sym = pos['symbol']
                # Match back to formatted symbol (e.g. BTC/USDT)
                matching_sym = raw_sym
                for s in SYMBOLS:
                    if s.replace('/', '') == raw_sym:
                        matching_sym = s
                        break
                
                entry_price = float(pos['entryPrice'])
                side = 'LONG' if amt > 0 else 'SHORT'
                qty = abs(amt)
                unrealized_pnl = float(pos.get('unRealizedProfit', 0.0))
                total_pnl_usd += unrealized_pnl
                
                mark_price = float(pos.get('markPrice', entry_price))
                leverage = int(pos.get('leverage', 10))
                
                # Current Market Last Price
                try:
                    ticker = exchange.fapiPublicGetTickerPrice({'symbol': raw_sym})
                    current_price = float(ticker['price'])
                except Exception:
                    current_price = mark_price
                
                # Exact Binance ROE % (Return on Equity) with Leverage
                # Formula: (Unrealized PnL / Initial Margin) * 100
                initial_margin = (qty * entry_price) / leverage if leverage > 0 else (qty * entry_price)
                roe_pct = (unrealized_pnl / initial_margin * 100) if initial_margin > 0 else 0.0
                raw_price_pct = ((mark_price - entry_price) / entry_price * 100) if side == 'LONG' else ((entry_price - mark_price) / entry_price * 100)
                
                # Fetch Active Cloud Algo Orders (SL & TP) on Binance
                sl_price = None
                tp_price = None
                cloud_active = False
                algo_orders_list = []
                try:
                    algos = exchange.fapiPrivateGetOpenAlgoOrders({'symbol': raw_sym})
                    if isinstance(algos, list):
                        for algo in algos:
                            o_type = algo.get('orderType')
                            trig = float(algo.get('triggerPrice', 0))
                            algo_id = algo.get('algoId')
                            algo_orders_list.append({'type': o_type, 'price': trig, 'id': algo_id})
                            if o_type == 'STOP_MARKET':
                                sl_price = trig
                            elif o_type == 'TAKE_PROFIT_MARKET':
                                tp_price = trig
                        if sl_price and tp_price:
                            cloud_active = True
                except Exception:
                    pass
                
                # Fallback SL/TP for display if algo query fails
                if not sl_price:
                    sl_price = entry_price * 0.99 if side == 'LONG' else entry_price * 1.01
                if not tp_price:
                    tp_price = entry_price * 1.02 if side == 'LONG' else entry_price * 0.98
                
                risk = abs(entry_price - sl_price)
                reward = abs(tp_price - entry_price)
                rr_str = f"1:{reward/risk:.1f}" if risk > 0 else "1:2.0"
                
                # Attach 4H institutional SMC market structure
                levels_4h = get_cached_4h_levels(matching_sym)
                
                positions.append({
                    'symbol': matching_sym,
                    'raw_symbol': raw_sym,
                    'side': side,
                    'qty': qty,
                    'entry': entry_price,
                    'mark_price': mark_price,
                    'current_price': current_price,
                    'initial_margin': initial_margin,
                    'stop_loss': sl_price,
                    'take_profit': tp_price,
                    'pnl_usd': unrealized_pnl,
                    'pnl_pct': roe_pct,  # Matches official Binance ROE %
                    'raw_price_pct': raw_price_pct,
                    'leverage': leverage,
                    'liquidation_price': float(pos.get('liquidationPrice', 0)),
                    'risk_reward': rr_str,
                    'cloud_protection': cloud_active,
                    'algo_orders': algo_orders_list,
                    'levels_4h': levels_4h
                })
        
        # 3. Monitored Watchlist Symbols (Single batch network query for high performance)
        all_tickers = {}
        try:
            tickers_raw = exchange.fapiPublicGetTickerPrice()
            if isinstance(tickers_raw, list):
                all_tickers = {t['symbol']: float(t['price']) for t in tickers_raw if 'symbol' in t and 'price' in t}
        except Exception:
            pass

        watchlist = []
        for sym in SYMBOLS:
            raw = sym.replace('/', '')
            price = all_tickers.get(raw, 0.0)
            if price == 0.0:
                try:
                    t = exchange.fapiPublicGetTickerPrice({'symbol': raw})
                    price = float(t['price'])
                except Exception:
                    pass
            
            is_active = any(p['symbol'] == sym for p in positions)
            watchlist.append({
                'symbol': sym,
                'raw_symbol': raw,
                'price': price,
                'is_active': is_active
            })
        
        equity = usdt_balance + total_pnl_usd
        total_pnl_pct = (total_pnl_usd / usdt_balance * 100) if usdt_balance > 0 else 0.0
        
        # Calculate closed trade statistics
        history = load_trade_history()
        today_str = datetime.now().strftime('%Y-%m-%d')
        today_trades = [t for t in history if t.get('closed_at', '').startswith(today_str)]
        today_realized_pnl = sum(t.get('pnl_usd', 0.0) for t in today_trades)
        wins = sum(1 for t in history if t.get('pnl_usd', 0) > 0)
        win_rate = round((wins / len(history) * 100), 1) if history else 0.0

        scan_target = get_next_scan_target()
        
        return {
            'success': True,
            'balance': usdt_balance,
            'equity': equity,
            'total_pnl_usd': total_pnl_usd,
            'total_pnl_pct': total_pnl_pct,
            'today_realized_pnl': today_realized_pnl,
            'win_rate': win_rate,
            'total_trades_count': len(history),
            'next_scan_seconds': scan_target['seconds_remaining'],
            'next_scan_target_ts': scan_target['target_timestamp'],
            'next_scan_target_iso': scan_target['target_utc_iso'],
            'next_scan_local_time': scan_target['target_local_str'],
            'next_scan_utc_time': scan_target['target_utc_str'],
            'is_paused': is_bot_paused(),
            'trade_history': history[:25],
            'recent_events': get_recent_events()[:30],
            'active_count': len(positions),
            'max_trades': MAX_CONCURRENT_TRADES,
            'default_leverage': DEFAULT_LEVERAGE,
            'positions': positions,
            'watchlist': watchlist,
            'server_time': datetime.now(timezone.utc).isoformat(),
            'local_time': datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_klines_data(symbol, timeframe='1h', limit=150):
    """Fetches candlestick bars + 4H level overlays for the interactive chart."""
    try:
        raw_symbol = symbol.replace('/', '')
        klines = exchange.fapiPublicGetKlines({
            'symbol': raw_symbol,
            'interval': timeframe,
            'limit': limit
        })
        
        candles = []
        for k in klines:
            candles.append({
                'time': int(k[0] / 1000),
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5])
            })
            
        # Get 4H key levels
        formatted_sym = symbol
        for s in SYMBOLS:
            if s.replace('/', '') == raw_symbol:
                formatted_sym = s
                break
                
        levels_4h = get_cached_4h_levels(formatted_sym)
        
        return {
            'success': True,
            'symbol': formatted_sym,
            'raw_symbol': raw_symbol,
            'timeframe': timeframe,
            'candles': candles,
            'levels_4h': levels_4h
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

class DashboardRequestHandler(BaseHTTPRequestHandler):
    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            self.close_connection = True

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            pass

    def finish(self):
        try:
            super().finish()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            pass

    def log_message(self, format, *args):
        # Completely suppress all routine HTTP access and polling logs so the terminal
        # is 100% clean and reserved exclusively for algorithmic trading bot signals.
        return

    def end_headers(self):
        try:
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            super().end_headers()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            pass

    def safe_send_json(self, data, status=200):
        try:
            payload = json.dumps(data).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            pass

    def safe_send_bytes(self, content, content_type='text/html', status=200):
        try:
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            pass

    def do_OPTIONS(self):
        try:
            self.send_response(200)
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Content-Length', '0')
            self.end_headers()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            pass

    def do_GET(self):
        try:
            self._handle_get()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            pass

    def _handle_get(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 0. Favicon & robots quick response
        if path in ('/favicon.ico', '/robots.txt'):
            self.safe_send_bytes(b'', 'image/x-icon', 204)
            return

        # 1. API: /api/status
        if path == '/api/status':
            data = get_dashboard_data()
            self.safe_send_json(data)
            return

        # 2. API: /api/klines
        if path == '/api/klines':
            symbol = query.get('symbol', ['BTCUSDT'])[0]
            tf = query.get('interval', ['1h'])[0]
            data = get_klines_data(symbol, tf)
            self.safe_send_json(data)
            return

        # 3. API: /api/trade_history
        if path == '/api/trade_history':
            history = load_trade_history()
            self.safe_send_json({'success': True, 'history': history})
            return

        # 4. API: /api/events
        if path == '/api/events':
            events = get_recent_events()
            self.safe_send_json({'success': True, 'events': events})
            return

        # 5. Static Files (Secured against path traversal)
        rel_path = 'index.html' if path in ('/', '') else path.lstrip('/')
        norm_path = os.path.normpath(os.path.join(WEB_DIR, rel_path))
        if not norm_path.startswith(os.path.abspath(WEB_DIR)):
            self.safe_send_bytes(b'Forbidden', 'text/plain', 403)
            return

        if os.path.isfile(norm_path):
            content_type = 'text/html'
            if norm_path.endswith('.css'):
                content_type = 'text/css'
            elif norm_path.endswith('.js'):
                content_type = 'application/javascript'
            elif norm_path.endswith('.json'):
                content_type = 'application/json'
            elif norm_path.endswith('.png'):
                content_type = 'image/png'
            elif norm_path.endswith('.svg'):
                content_type = 'image/svg+xml'

            with open(norm_path, 'rb') as f:
                content = f.read()

            self.safe_send_bytes(content, content_type)
        else:
            self.safe_send_bytes(b'File Not Found', 'text/plain', 404)

    def do_POST(self):
        try:
            self._handle_post()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            pass

    def _handle_post(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # API: /api/toggle_pause
        if path == '/api/toggle_pause':
            paused = toggle_bot_pause()
            self.safe_send_json({'success': True, 'is_paused': paused})
            return

        # API: /api/close_position
        if path == '/api/close_position':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            try:
                params = json.loads(body)
                symbol = params.get('symbol', '')
                side = params.get('side', '').lower()
                qty = float(params.get('qty', 0))
                
                # Fetch entry/ticker before closing to record PnL
                raw_sym = symbol.replace('/', '')
                curr_px = 0.0
                try:
                    ticker = exchange.fapiPublicGetTickerPrice({'symbol': raw_sym})
                    curr_px = float(ticker['price'])
                except Exception:
                    pass

                # Close trade and cancel cloud SL/TP
                close_demo_trade(symbol, side, qty)
                cancel_algo_orders(symbol)
                
                # Record manual close in trade history
                time.sleep(0.3)
                realized_pnl = 0.0
                try:
                    trades = exchange.fapiPrivateGetUserTrades({'symbol': raw_sym, 'limit': 3})
                    closing_trades = [t for t in trades if float(t.get('realizedPnl', 0.0)) != 0]
                    if closing_trades:
                        realized_pnl = sum(float(t.get('realizedPnl', 0.0)) for t in closing_trades[-2:])
                except Exception:
                    pass
                
                pnl_pct = (realized_pnl / ((curr_px * qty) / DEFAULT_LEVERAGE)) * 100 if curr_px > 0 and qty > 0 else 0.0
                record_closed_trade(symbol, side, curr_px, curr_px, qty, realized_pnl, pnl_pct, "Manual Dashboard Close ✋")
                log_bot_event(f"✋ Manual Close: {side.upper()} {symbol} closed via Dashboard | PnL: {'+' if realized_pnl >= 0 else ''}${realized_pnl:,.2f} ({'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%)", "CONTROL")

                self.safe_send_json({'success': True, 'message': f'Closed {side.upper()} position on {symbol}'})
            except Exception as e:
                self.safe_send_json({'success': False, 'error': str(e)}, 500)
            return

        self.safe_send_bytes(b'Not Found', 'text/plain', 404)

class QuietThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc_type, exc_val, _ = sys.exc_info()
        # Silently ignore normal client disconnects / connection aborts (WinError 10053 / 10054 / BrokenPipe)
        if exc_type is not None:
            if issubclass(exc_type, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
                return
            if issubclass(exc_type, OSError) and getattr(exc_val, 'winerror', None) in (10053, 10054, 10038):
                return
        err_msg = str(exc_val)
        if any(code in err_msg for code in ('10053', '10054', '10038', 'Broken pipe')):
            return
        super().handle_error(request, client_address)

def run_server(port=PORT):
    server = QuietThreadingHTTPServer(('0.0.0.0', port), DashboardRequestHandler)
    print(f"\n🌐 [SMC LIVE WEB DASHBOARD] Running at http://localhost:{port}")
    print(f"📊 Open in browser: http://localhost:{port} (Live charts, real-time PnL, SL/TP overlays)")
    server.serve_forever()

def start_dashboard_thread(port=PORT):
    """Starts the dashboard HTTP server in a daemon background thread."""
    thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    thread.start()
    return thread

if __name__ == '__main__':
    run_server()
