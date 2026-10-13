import sys
if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)

import os
import json
import threading
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
import ccxt
from dotenv import load_dotenv

# Import analysis logic from trading_bot
from trading_bot import (
    SYMBOLS,
    MAX_CONCURRENT_TRADES,
    exchange,
    sync_server_time,
    get_4h_levels_and_structure,
    close_demo_trade,
    cancel_algo_orders
)

load_dotenv()

PORT = 5050
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')

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
                
                # Current Price
                try:
                    ticker = exchange.fapiPublicGetTickerPrice({'symbol': raw_sym})
                    current_price = float(ticker['price'])
                except Exception:
                    current_price = entry_price
                
                pnl_pct = ((current_price - entry_price) / entry_price * 100) if side == 'LONG' else ((entry_price - current_price) / entry_price * 100)
                
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
                
                positions.append({
                    'symbol': matching_sym,
                    'raw_symbol': raw_sym,
                    'side': side,
                    'qty': qty,
                    'entry': entry_price,
                    'current_price': current_price,
                    'stop_loss': sl_price,
                    'take_profit': tp_price,
                    'pnl_usd': unrealized_pnl,
                    'pnl_pct': pnl_pct,
                    'leverage': int(pos.get('leverage', 10)),
                    'liquidation_price': float(pos.get('liquidationPrice', 0)),
                    'risk_reward': rr_str,
                    'cloud_protection': cloud_active,
                    'algo_orders': algo_orders_list
                })
        
        # 3. Monitored Watchlist Symbols
        watchlist = []
        for sym in SYMBOLS:
            raw = sym.replace('/', '')
            try:
                ticker = exchange.fapiPublicGetTickerPrice({'symbol': raw})
                price = float(ticker['price'])
            except Exception:
                price = 0.0
            
            is_active = any(p['symbol'] == sym for p in positions)
            watchlist.append({
                'symbol': sym,
                'raw_symbol': raw,
                'price': price,
                'is_active': is_active
            })
        
        equity = usdt_balance + total_pnl_usd
        total_pnl_pct = (total_pnl_usd / usdt_balance * 100) if usdt_balance > 0 else 0.0
        
        return {
            'success': True,
            'balance': usdt_balance,
            'equity': equity,
            'total_pnl_usd': total_pnl_usd,
            'total_pnl_pct': total_pnl_pct,
            'active_count': len(positions),
            'max_trades': MAX_CONCURRENT_TRADES,
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
                
        levels_4h = get_4h_levels_and_structure(formatted_sym)
        
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
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. API: /api/status
        if path == '/api/status':
            data = get_dashboard_data()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode('utf-8'))
            return

        # 2. API: /api/klines
        if path == '/api/klines':
            symbol = query.get('symbol', ['BTCUSDT'])[0]
            tf = query.get('interval', ['1h'])[0]
            data = get_klines_data(symbol, tf)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode('utf-8'))
            return

        # 3. Static Files
        file_path = os.path.join(WEB_DIR, 'index.html' if path in ['/', ''] else path.lstrip('/'))
        if os.path.isfile(file_path):
            content_type = 'text/html'
            if file_path.endswith('.css'):
                content_type = 'text/css'
            elif file_path.endswith('.js'):
                content_type = 'application/javascript'
            elif file_path.endswith('.json'):
                content_type = 'application/json'
            elif file_path.endswith('.png'):
                content_type = 'image/png'
            elif file_path.endswith('.svg'):
                content_type = 'image/svg+xml'

            with open(file_path, 'rb') as f:
                content = f.read()

            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_response(404)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'File Not Found')

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # API: /api/close_position
        if path == '/api/close_position':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            try:
                params = json.loads(body)
                symbol = params.get('symbol', '')
                side = params.get('side', '').lower()
                qty = float(params.get('qty', 0))
                
                # Close trade and cancel cloud SL/TP
                close_demo_trade(symbol, side, qty)
                cancel_algo_orders(symbol)
                
                resp = {'success': True, 'message': f'Closed {side.upper()} position on {symbol}'}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(resp).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode('utf-8'))
            return

        self.send_response(404)
        self.end_headers()

def run_server(port=PORT):
    server = ThreadingHTTPServer(('0.0.0.0', port), DashboardRequestHandler)
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
