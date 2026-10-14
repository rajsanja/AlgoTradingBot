import sys
if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)

# Prevent duplicate module execution when imported by child modules like web_dashboard
if __name__ == '__main__':
    sys.modules['trading_bot'] = sys.modules['__main__']

import ccxt
import pandas as pd
import pandas_ta as ta
import time
import os
import hmac
import hashlib
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ==========================================
# CONFIGURATION
# ==========================================
# Note: XAU/USDT is TradFi Gold Perpetual on Binance.
SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XAU/USDT']
ENTRY_TIMEFRAME = '1h'
MACRO_TIMEFRAMES = ['4h', '1d', '1w', '1M']   # Higher Timeframes for Massive Context
RISK_PER_TRADE = 0.01  # 1% risk per trade
MAX_DAILY_LOSS = 0.03  # Stop trading if we lose 3% in a day
MAX_CONCURRENT_TRADES = 2  # Maximum simultaneous open positions allowed across all pairs (Default: 2)
DEFAULT_LEVERAGE = 10  # Fixed institutional leverage (10x: optimal margin efficiency and safety)

# Binance Testnet API Keys (Loaded from .env file)
API_KEY = os.getenv('BINANCE_TESTNET_API_KEY')
API_SECRET = os.getenv('BINANCE_TESTNET_API_SECRET')

# Initialize CCXT Binance instance for Testnet
exchange = ccxt.binance({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',
        'adjustForTimeDifference': True,
        'recvWindow': 60000,
    },
})
# Manually route to Binance Demo/Testnet to avoid CCXT deprecation error
exchange.urls['api'] = exchange.urls['demo']

def sync_server_time():
    """Synchronizes client clock offset with Binance Futures server to prevent code -1021 error."""
    try:
        return exchange.load_time_difference()
    except Exception:
        return None

def sign_tradfi_agreement():
    """
    Automatically signs Binance TradFi-Perps contract agreement (error -4411)
    to enable trading on commodity perpetuals like Gold (XAU/USDT).
    """
    try:
        server_time = exchange.fetch_time()
        query = f"timestamp={server_time}&recvWindow=60000"
        sig = hmac.new(API_SECRET.encode('utf-8'), query.encode('utf-8'), hashlib.sha256).hexdigest()
        
        base_url = "https://demo-fapi.binance.com"
        url = f"{base_url}/fapi/v1/stock/contract?{query}&signature={sig}"
        headers = {'X-MBX-APIKEY': API_KEY}
        
        resp = requests.post(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            print("✅ Binance TradFi Agreement signed (XAU/Gold trading enabled).")
            return True
    except Exception:
        pass
    return False

_exchange_initialized = False

def init_exchange():
    global _exchange_initialized
    if _exchange_initialized:
        return
    _exchange_initialized = True
    
    print("Connecting to Binance Testnet (Paper Trading)...")
    sync_diff = sync_server_time()
    if sync_diff is not None:
        print(f"🕒 Server time synchronized with Binance (Offset: {sync_diff}ms)")
    sign_tradfi_agreement()
    try:
        account_info = exchange.fapiPrivateV2GetAccount()
        usdt_balance = 0.0
        for asset in account_info.get('assets', []):
            if asset['asset'] == 'USDT':
                usdt_balance = float(asset['walletBalance'])
        print(f"✅ API Connected Successfully! Fake USDT Balance: ${usdt_balance:.2f}\n")
    except Exception as e:
        print(f"❌ API Connection Failed! Please check your .env file. Error: {e}\n")

# Run connection initialization exactly once
init_exchange()

def fetch_data(symbol, timeframe, limit=250):
    try:
        raw_symbol = symbol.replace('/', '')
        klines = exchange.fapiPublicGetKlines({
            'symbol': raw_symbol,
            'interval': timeframe,
            'limit': limit
        })
        # klines: [Open time, Open, High, Low, Close, Volume, Close time...]
        ohlcv = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in klines]
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
        return None

def detect_fvg(df):
    """
    Detects Fair Value Gaps (FVG) in the last 3 candles.
    Bullish FVG: Low of candle 3 is higher than High of candle 1
    Bearish FVG: High of candle 3 is lower than Low of candle 1
    """
    if len(df) < 3:
        return None

    c1 = df.iloc[-3]
    c3 = df.iloc[-1]

    if c3['low'] > c1['high']:
        return {'type': 'bullish', 'top': c3['low'], 'bottom': c1['high']}
    elif c3['high'] < c1['low']:
        return {'type': 'bearish', 'top': c1['low'], 'bottom': c3['high']}
    
    return None

def calculate_quantity(entry_price, stop_loss_price, capital, risk_pct, symbol=''):
    risk_amount = capital * risk_pct
    risk_per_coin = abs(entry_price - stop_loss_price)
    
    if risk_per_coin == 0 or entry_price == 0:
        return 0
        
    quantity = risk_amount / risk_per_coin
    
    # Enforce Binance minimum notional filter
    min_notional = 50.0 if 'BTC' in symbol else (20.0 if 'ETH' in symbol else 5.0)
    if (quantity * entry_price) < min_notional:
        quantity = (min_notional * 1.05) / entry_price
        
    return round(quantity, 5)

def detect_order_block_4h(df_4h):
    """
    Detects the most recent 4H Order Block (OB).
    Bullish OB: Last down-candle before strong bullish expansion.
    Bearish OB: Last up-candle before strong bearish expansion.
    """
    for i in range(len(df_4h) - 3, 1, -1):
        c = df_4h.iloc[i]
        next_c = df_4h.iloc[i+1]
        if c['close'] < c['open'] and next_c['close'] > c['high']:
            return {'type': 'bullish', 'top': float(c['high']), 'bottom': float(c['low'])}
        elif c['close'] > c['open'] and next_c['close'] < c['low']:
            return {'type': 'bearish', 'top': float(c['high']), 'bottom': float(c['low'])}
    return None

def detect_4h_fvg(df_4h):
    """Detects the latest 4H Fair Value Gap."""
    for i in range(len(df_4h) - 1, 2, -1):
        c1 = df_4h.iloc[i-2]
        c3 = df_4h.iloc[i]
        if c3['low'] > c1['high']:
            return {'type': 'bullish', 'top': float(c3['low']), 'bottom': float(c1['high'])}
        elif c3['high'] < c1['low']:
            return {'type': 'bearish', 'top': float(c1['high']), 'bottom': float(c3['low'])}
    return None

def get_4h_levels_and_structure(symbol):
    """
    Analyzes the 4H timeframe to extract institutional key levels:
    - 4H Swing Highs & Lows (BSL & SSL Liquidity Pools)
    - 4H Key Support & Key Resistance
    - 4H Order Block zone
    - 4H Fair Value Gap zone
    - 4H Trend & Market Structure Bias
    """
    df_4h = fetch_data(symbol, '4h', limit=150)
    if df_4h is None or len(df_4h) < 25:
        return None
        
    df_4h['ema20'] = df_4h['close'].ewm(span=20, adjust=False).mean()
    df_4h['ema50'] = df_4h['close'].ewm(span=50, adjust=False).mean()
    
    current_price = float(df_4h.iloc[-1]['close'])
    ema20 = float(df_4h.iloc[-1]['ema20'])
    ema50 = float(df_4h.iloc[-1]['ema50'])
    
    # Swing Highs and Swing Lows (Fractal Points)
    swing_highs = []
    swing_lows = []
    for i in range(2, len(df_4h) - 2):
        c = df_4h.iloc[i]
        if c['high'] == df_4h.iloc[i-2:i+3]['high'].max():
            swing_highs.append(float(c['high']))
        if c['low'] == df_4h.iloc[i-2:i+3]['low'].min():
            swing_lows.append(float(c['low']))
            
    # Key levels above and below current price
    resistances = sorted([h for h in swing_highs if h > current_price])
    supports = sorted([l for l in swing_lows if l < current_price], reverse=True)
    
    nearest_resistance = resistances[0] if resistances else current_price * 1.025
    nearest_support = supports[0] if supports else current_price * 0.975
    
    major_swing_high = max(swing_highs[-15:]) if len(swing_highs) >= 3 else current_price * 1.05
    major_swing_low = min(swing_lows[-15:]) if len(swing_lows) >= 3 else current_price * 0.95
    
    order_block_4h = detect_order_block_4h(df_4h)
    fvg_4h = detect_4h_fvg(df_4h)
    
    # 4H Structure Bias
    is_ema_bullish = current_price >= ema20
    is_struct_bullish = len(swing_lows) >= 2 and swing_lows[-1] >= swing_lows[-2]
    
    if is_ema_bullish and is_struct_bullish:
        structure_bias = 'BULLISH'
    elif not is_ema_bullish and (len(swing_highs) >= 2 and swing_highs[-1] <= swing_highs[-2]):
        structure_bias = 'BEARISH'
    else:
        structure_bias = 'BULLISH' if is_ema_bullish else 'BEARISH'
        
    return {
        'current_price': current_price,
        'ema20': ema20,
        'ema50': ema50,
        'nearest_support': nearest_support,
        'nearest_resistance': nearest_resistance,
        'major_swing_high': major_swing_high,
        'major_swing_low': major_swing_low,
        'resistances': resistances,
        'supports': supports,
        'order_block': order_block_4h,
        'fvg': fvg_4h,
        'structure_bias': structure_bias,
    }

def calculate_4h_level_sl_tp(levels_4h, entry_price, side, fvg_1h=None):
    """
    Calculates Stop Loss and Take Profit strictly based on 4H key levels.
    - Stop Loss: Placed safely behind 4H Key Support / Order Block / 1H FVG.
    - Take Profit: Targeted right before 4H Key Resistance / Liquidity Pool.
    - Enforces optimal Risk-to-Reward ratio (aiming for 1:1.8 to 1:2.5).
    """
    if side == 'long':
        support_level = levels_4h['nearest_support']
        if levels_4h['order_block'] and levels_4h['order_block']['type'] == 'bullish':
            ob_bottom = levels_4h['order_block']['bottom']
            if ob_bottom < entry_price:
                support_level = min(support_level, ob_bottom)
        if fvg_1h and fvg_1h['type'] == 'bullish':
            if fvg_1h['bottom'] < entry_price:
                support_level = min(support_level, fvg_1h['bottom'])
                
        # Buffer 0.25% below support to protect against stop hunts
        stop_loss = support_level * 0.9975
        
        if stop_loss >= entry_price:
            stop_loss = entry_price * 0.985
        elif (entry_price - stop_loss) / entry_price > 0.035:
            stop_loss = entry_price * 0.98
            
        risk = entry_price - stop_loss
        
        # Target 4H resistance candidate giving at least 1:1.8 R:R
        target_tp = None
        for res in levels_4h['resistances']:
            potential_tp = res * 0.997  # 0.3% before resistance for clean fill
            if (potential_tp - entry_price) / risk >= 1.8:
                target_tp = potential_tp
                break
                
        if target_tp is None:
            major_tp = levels_4h['major_swing_high'] * 0.996
            if (major_tp - entry_price) / risk >= 1.8:
                target_tp = min(major_tp, entry_price + (risk * 2.8))
            else:
                target_tp = entry_price + (risk * 2.0)
                
        take_profit = target_tp
        rr_ratio = round((take_profit - entry_price) / risk, 2)
        return stop_loss, take_profit, rr_ratio, support_level, levels_4h['nearest_resistance']

    elif side == 'short':
        resistance_level = levels_4h['nearest_resistance']
        if levels_4h['order_block'] and levels_4h['order_block']['type'] == 'bearish':
            ob_top = levels_4h['order_block']['top']
            if ob_top > entry_price:
                resistance_level = max(resistance_level, ob_top)
        if fvg_1h and fvg_1h['type'] == 'bearish':
            if fvg_1h['top'] > entry_price:
                resistance_level = max(resistance_level, fvg_1h['top'])
                
        # Buffer 0.25% above resistance to protect against wick hunting
        stop_loss = resistance_level * 1.0025
        
        if stop_loss <= entry_price:
            stop_loss = entry_price * 1.015
        elif (stop_loss - entry_price) / entry_price > 0.035:
            stop_loss = entry_price * 1.02
            
        risk = stop_loss - entry_price
        
        target_tp = None
        for sup in levels_4h['supports']:
            potential_tp = sup * 1.003  # 0.3% above support for clean fill
            if (entry_price - potential_tp) / risk >= 1.8:
                target_tp = potential_tp
                break
                
        if target_tp is None:
            major_tp = levels_4h['major_swing_low'] * 1.004
            if (entry_price - major_tp) / risk >= 1.8:
                target_tp = max(major_tp, entry_price - (risk * 2.8))
            else:
                target_tp = entry_price - (risk * 2.0)
                
        take_profit = target_tp
        rr_ratio = round((entry_price - take_profit) / risk, 2)
        return stop_loss, take_profit, rr_ratio, resistance_level, levels_4h['nearest_support']

def get_quantity_precision(symbol):
    """Returns valid decimal precision for each symbol on Binance Futures."""
    if 'BTC' in symbol:
        return 3
    elif 'ETH' in symbol:
        return 2
    elif 'BNB' in symbol:
        return 2
    elif 'SOL' in symbol:
        return 2
    elif 'XAU' in symbol:
        return 3
    return 2

def execute_demo_trade(symbol, side, qty):
    """
    Executes a demo order directly on Binance Futures Testnet,
    bypassing CCXT's unified wrapper to avoid 'sapi' errors.
    """
    try:
        sync_server_time()
        raw_symbol = symbol.replace('/', '')
        
        qty_prec = get_quantity_precision(symbol)
        quantity_str = f"{qty:.{qty_prec}f}"
        if float(quantity_str) <= 0:
            print(f"⚠️ Calculated quantity ({quantity_str}) is 0 or too small for {symbol}. Order aborted.")
            return None

        # Set fixed institutional leverage (10x)
        try:
            exchange.fapiPrivatePostLeverage({'symbol': raw_symbol, 'leverage': DEFAULT_LEVERAGE})
        except Exception:
            pass

        order = exchange.fapiPrivatePostOrder({
            'symbol': raw_symbol,
            'side': side.upper(),
            'type': 'MARKET',
            'quantity': quantity_str
        })
        print(f"✅ Demo {side.upper()} Order Executed on {symbol}! Order ID: {order.get('orderId')} | Qty: {quantity_str}")
        return order
    except Exception as e:
        err_msg = str(e)
        if '-4411' in err_msg or 'TradFi' in err_msg:
            print(f"⚠️ TradFi Agreement required for {symbol}. Signing agreement and retrying order...")
            sign_tradfi_agreement()
            try:
                order = exchange.fapiPrivatePostOrder({
                    'symbol': raw_symbol,
                    'side': side.upper(),
                    'type': 'MARKET',
                    'quantity': quantity_str
                })
                print(f"✅ Demo {side.upper()} Order Executed on {symbol}! Order ID: {order.get('orderId')} | Qty: {quantity_str}")
                return order
            except Exception as retry_err:
                print(f"❌ Retry failed for {symbol}: {retry_err}")
                return None
        print(f"❌ Failed to place Demo Order for {symbol}: {e}")
        return None

def close_demo_trade(symbol, side, qty):
    """
    Closes a demo position on Binance Futures Testnet using reduceOnly.
    side: 'long' -> closes with 'SELL'
    side: 'short' -> closes with 'BUY'
    """
    try:
        sync_server_time()
        raw_symbol = symbol.replace('/', '')
        close_side = 'SELL' if side.lower() == 'long' else 'BUY'
        
        qty_prec = get_quantity_precision(symbol)
        quantity_str = f"{qty:.{qty_prec}f}"
        
        order = exchange.fapiPrivatePostOrder({
            'symbol': raw_symbol,
            'side': close_side,
            'type': 'MARKET',
            'quantity': quantity_str,
            'reduceOnly': 'true'
        })
        print(f"✅ Demo Position Closed on {symbol}! Closed with {close_side} {quantity_str} | Order ID: {order.get('orderId')}")
        cancel_algo_orders(symbol)
        return order
    except Exception as e:
        print(f"❌ Failed to close Demo Position for {symbol}: {e}")
        return None

def format_price(symbol, price):
    """Formats price to valid tick precision for Binance Futures."""
    if 'BTC' in symbol:
        return f"{round(price, 1):.2f}"
    elif 'ETH' in symbol:
        return f"{round(price, 2):.2f}"
    elif 'BNB' in symbol:
        return f"{round(price, 2):.2f}"
    elif 'SOL' in symbol:
        return f"{round(price, 2):.2f}"
    elif 'XAU' in symbol:
        return f"{round(price, 2):.2f}"
    return f"{round(price, 2):.2f}"

def cancel_algo_orders(symbol):
    """Cancels any open conditional/algo orders on Binance for the symbol."""
    try:
        raw_symbol = symbol.replace('/', '')
        exchange.fapiPrivateDeleteAlgoOpenOrders({'symbol': raw_symbol})
    except Exception:
        pass

def place_server_sl_tp(symbol, side, stop_loss, take_profit):
    """
    Places native server-side STOP_MARKET and TAKE_PROFIT_MARKET orders directly
    on Binance Futures Cloud. These orders live on Binance 24/7, so trades will
    automatically close at SL or TP even if the PC is turned off or disconnected.
    """
    try:
        sync_server_time()
        raw_symbol = symbol.replace('/', '')
        close_side = 'SELL' if side.lower() == 'long' or side.upper() == 'BUY' else 'BUY'
        
        # Clean up any existing conditional orders for this symbol first
        cancel_algo_orders(symbol)
        
        sl_price_str = format_price(symbol, stop_loss)
        tp_price_str = format_price(symbol, take_profit)
        
        sl_order = None
        tp_order = None
        
        # 1. Place Native Cloud Stop Loss Order
        try:
            sl_order = exchange.fapiPrivatePostAlgoOrder({
                'algoType': 'CONDITIONAL',
                'symbol': raw_symbol,
                'side': close_side,
                'type': 'STOP_MARKET',
                'triggerPrice': sl_price_str,
                'closePosition': 'true'
            })
        except Exception as e_sl:
            print(f"⚠️ Warning: Could not place Cloud Stop Loss for {symbol}: {e_sl}")

        # 2. Place Native Cloud Take Profit Order
        try:
            tp_order = exchange.fapiPrivatePostAlgoOrder({
                'algoType': 'CONDITIONAL',
                'symbol': raw_symbol,
                'side': close_side,
                'type': 'TAKE_PROFIT_MARKET',
                'triggerPrice': tp_price_str,
                'closePosition': 'true'
            })
        except Exception as e_tp:
            print(f"⚠️ Warning: Could not place Cloud Take Profit for {symbol}: {e_tp}")
        
        print(f"☁️ [BINANCE CLOUD PROTECTION ACTIVE] {symbol} ({side.upper()})")
        if sl_order:
            print(f"   • Stop Loss Order:    ${float(sl_price_str):,.2f} (Algo ID: {sl_order.get('algoId')})")
        if tp_order:
            print(f"   • Take Profit Order:  ${float(tp_price_str):,.2f} (Algo ID: {tp_order.get('algoId')})")
        print(f"   🛡️ Safe to close PC: Binance servers will auto-close at SL or TP 24/7.")
        return sl_order, tp_order
    except Exception as e:
        print(f"⚠️ Failed to place server-side SL/TP for {symbol} on Binance: {e}")
        return None, None

def sync_active_positions(active_trades):
    """
    Syncs in-memory active_trades two-way with live Binance Futures positions.
    1. If Binance has an open position (amt != 0) not yet in active_trades, registers it.
       Ensures server-side SL and TP orders are active on Binance Cloud.
    2. If Binance shows amt == 0 for a symbol previously in active_trades, clears it and cancels any leftover algo orders.
    """
    try:
        positions = exchange.fapiPrivateV2GetPositionRisk()
        live_positions = {pos['symbol']: pos for pos in positions}
        for sym in SYMBOLS:
            sym_raw = sym.replace('/', '')
            pos = live_positions.get(sym_raw)
            if pos:
                amt = float(pos['positionAmt'])
                if amt != 0:
                    entry = float(pos['entryPrice'])
                    side = 'long' if amt > 0 else 'short'
                    qty = abs(amt)
                    if active_trades[sym] is None:
                        active_trades[sym] = {
                            'side': side,
                            'entry': entry,
                            'stop_loss': entry * 0.99 if side == 'long' else entry * 1.01,
                            'take_profit': entry * 1.02 if side == 'long' else entry * 0.98,
                            'qty': qty,
                            'breakeven_triggered': False
                        }
                        print(f"📦 Synced open position from Binance for {sym} ({side.upper()} {qty} @ ${entry:,.2f}). Managing it now.")
                    
                    # Ensure Native Server-Side SL/TP exists on Binance Cloud
                    try:
                        open_algos = exchange.fapiPrivateGetOpenAlgoOrders({'symbol': sym_raw})
                        if not open_algos or len(open_algos) < 2:
                            trade = active_trades[sym]
                            place_server_sl_tp(sym, trade['side'], trade['stop_loss'], trade['take_profit'])
                    except Exception:
                        pass
                else:
                    if active_trades[sym] is not None:
                        print(f"ℹ️ Position {sym} is no longer open on Binance (Triggered SL/TP or closed). Cleared from active trades.")
                        cancel_algo_orders(sym)
                        active_trades[sym] = None
    except Exception as e:
        print(f"⚠️ Could not sync existing positions from Binance: {e}")

def print_portfolio_dashboard(active_trades):
    """
    Prints a clean, formatted live portfolio summary table of ALL open positions on Binance.
    """
    open_trades = [(sym, t) for sym, t in active_trades.items() if t is not None]
    if not open_trades:
        print("\n💼 [PORTFOLIO STATUS] No open positions currently running.")
        return
    
    print("\n" + "=" * 82)
    print("💼 [LIVE PORTFOLIO DASHBOARD - ALL ACTIVE POSITIONS]")
    print("=" * 82)
    print(f"| {'Symbol':<9} | {'Side':<5} | {'Size':<8} | {'Entry':<9} | {'Current':<9} | {'SL':<9} | {'TP':<9} | {'PnL ($)':<9} | {'PnL (%)':<8} |")
    print(f"|{'-'*11}|{'-'*7}|{'-'*10}|{'-'*11}|{'-'*11}|{'-'*11}|{'-'*11}|{'-'*11}|{'-'*10}|")
    
    total_unrealized_pnl = 0.0
    for sym, trade in open_trades:
        try:
            ticker = exchange.fapiPublicGetTickerPrice({'symbol': sym.replace('/', '')})
            current_price = float(ticker['price'])
            if trade['side'] == 'long':
                pnl_usd = (current_price - trade['entry']) * trade['qty']
                pnl_pct = ((current_price - trade['entry']) / trade['entry']) * 100
            else:
                pnl_usd = (trade['entry'] - current_price) * trade['qty']
                pnl_pct = ((trade['entry'] - current_price) / trade['entry']) * 100
            total_unrealized_pnl += pnl_usd
            
            pnl_usd_str = f"{'+' if pnl_usd >= 0 else ''}${pnl_usd:,.2f}"
            pnl_pct_str = f"{'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%"
            side_str = "LONG" if trade['side'] == 'long' else "SHORT"
            print(f"| {sym:<9} | {side_str:<5} | {trade['qty']:<8.4g} | ${trade['entry']:<8,.2f} | ${current_price:<8,.2f} | ${trade['stop_loss']:<8,.2f} | ${trade['take_profit']:<8,.2f} | {pnl_usd_str:<9} | {pnl_pct_str:<8} |")
        except Exception:
            pass
    print("=" * 82)
    total_str = f"{'+' if total_unrealized_pnl >= 0 else ''}${total_unrealized_pnl:,.2f}"
    print(f"💰 Total Unrealized PnL: {total_str} | Active Positions: {len(open_trades)}/{MAX_CONCURRENT_TRADES}")
    print("☁️ Native Cloud SL/TP: ACTIVE on Binance (24/7 Protection - Safe to close PC)")
    print("=" * 82 + "\n")

def manage_active_trades(active_trades, daily_loss_ref):
    """
    Monitors all open positions using fast real-time ticker prices.
    Executes breakeven, take profit, and stop loss order closures.
    Also syncs with Binance to detect if a cloud SL/TP already triggered.
    Returns the updated daily_loss amount.
    """
    daily_loss = daily_loss_ref
    
    # Query live position risk to see if any position already closed on Binance
    live_positions_map = {}
    try:
        positions = exchange.fapiPrivateV2GetPositionRisk()
        for p in positions:
            live_positions_map[p['symbol']] = float(p['positionAmt'])
    except Exception:
        pass

    for symbol, trade in list(active_trades.items()):
        if trade is None:
            continue
        try:
            raw_symbol = symbol.replace('/', '')
            
            # 1. Detect if Cloud SL/TP triggered on Binance
            if raw_symbol in live_positions_map and live_positions_map[raw_symbol] == 0:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ☁️ {symbol} Position closed on Binance (Cloud SL/TP executed). Cleaning up orders...")
                cancel_algo_orders(symbol)
                active_trades[symbol] = None
                continue

            ticker = exchange.fapiPublicGetTickerPrice({'symbol': raw_symbol})
            current_price = float(ticker['price'])
            
            pnl_pct = ((current_price - trade['entry']) / trade['entry'] * 100) if trade['side'] == 'long' else ((trade['entry'] - current_price) / trade['entry'] * 100)
            halfway_mark = trade['entry'] + (trade['take_profit'] - trade['entry']) / 2.0
            
            if trade['side'] == 'long':
                if current_price >= halfway_mark and not trade['breakeven_triggered']:
                    trade['stop_loss'] = trade['entry']
                    trade['breakeven_triggered'] = True
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {symbol} 🛡️ RISK FREE! Price reached 50% target. Stop Loss moved to breakeven (${trade['entry']:,.2f}) on Binance Cloud.")
                    place_server_sl_tp(symbol, trade['side'], trade['stop_loss'], trade['take_profit'])
                
                if current_price >= trade['take_profit']:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {symbol} ✅ TAKE PROFIT HIT at ${current_price:,.2f}! Closing trade on Binance...")
                    close_demo_trade(symbol, trade['side'], trade['qty'])
                    cancel_algo_orders(symbol)
                    active_trades[symbol] = None
                elif current_price <= trade['stop_loss']:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {symbol} ❌ STOP LOSS HIT at ${current_price:,.2f}! Closing trade on Binance...")
                    close_demo_trade(symbol, trade['side'], trade['qty'])
                    cancel_algo_orders(symbol)
                    loss = abs(trade['entry'] - trade['stop_loss']) * trade['qty']
                    daily_loss += loss
                    active_trades[symbol] = None
                    
            elif trade['side'] == 'short':
                if current_price <= halfway_mark and not trade['breakeven_triggered']:
                    trade['stop_loss'] = trade['entry']
                    trade['breakeven_triggered'] = True
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {symbol} 🛡️ RISK FREE! Price reached 50% target. Stop Loss moved to breakeven (${trade['entry']:,.2f}) on Binance Cloud.")
                    place_server_sl_tp(symbol, trade['side'], trade['stop_loss'], trade['take_profit'])
                
                if current_price <= trade['take_profit']:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {symbol} ✅ TAKE PROFIT HIT at ${current_price:,.2f}! Closing trade on Binance...")
                    close_demo_trade(symbol, trade['side'], trade['qty'])
                    cancel_algo_orders(symbol)
                    active_trades[symbol] = None
                elif current_price <= trade['stop_loss']:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {symbol} ❌ STOP LOSS HIT at ${current_price:,.2f}! Closing trade on Binance...")
                    close_demo_trade(symbol, trade['side'], trade['qty'])
                    cancel_algo_orders(symbol)
                    loss = abs(trade['stop_loss'] - trade['entry']) * trade['qty']
                    daily_loss += loss
                    active_trades[symbol] = None
        except Exception as e:
            print(f"Error checking position for {symbol}: {e}")
            
    return daily_loss

def bot_loop():
    print(f"Starting SMC Trading Bot. Monitoring: {', '.join(SYMBOLS)}")
    
    # Launch Live Web Dashboard in a background daemon thread
    try:
        from web_dashboard import start_dashboard_thread
        start_dashboard_thread(port=5050)
        print("🌐 [LIVE WEB DASHBOARD] Running at http://localhost:5050 (Interactive Charts, Real-Time PnL & Cloud Orders)\n")
    except Exception:
        pass

    daily_loss = 0.0
    current_day = pd.Timestamp.now().day
    
    # Store trade details PER SYMBOL to manage multiple trades simultaneously
    active_trades = {symbol: None for symbol in SYMBOLS}
    sync_active_positions(active_trades)

    while True:
        try:
            # Keep clock synchronized with Binance server
            sync_server_time()
            
            # Sync local state with live Binance positions (detects fills, manual closes, etc.)
            sync_active_positions(active_trades)
            
            # Display real-time unified portfolio dashboard of all open positions
            print_portfolio_dashboard(active_trades)
            
            # Always use live real-time balance for risk calculations!
            try:
                account_info = exchange.fapiPrivateV2GetAccount()
                current_capital = 0.0
                for asset in account_info['assets']:
                    if asset['asset'] == 'USDT':
                        current_capital = float(asset['walletBalance'])
            except Exception:
                current_capital = 1000.0 # Fallback if API glitch

            if pd.Timestamp.now().day != current_day:
                daily_loss = 0.0
                current_day = pd.Timestamp.now().day

            if daily_loss >= MAX_DAILY_LOSS * current_capital:
                print(f"[{pd.Timestamp.now()}] Max daily loss (${daily_loss:,.2f}) reached. Shutting down until tomorrow.")
                time.sleep(3600)
                continue

            # Check open positions first
            daily_loss = manage_active_trades(active_trades, daily_loss)

            # ---------------------------------------------------------
            # 1. Market Analysis for All Symbols (Executed ONCE per Candle Close)
            # ---------------------------------------------------------
            for symbol in SYMBOLS:
                trade = active_trades[symbol]
                if trade is not None:
                    # Print active trade status instead of re-analyzing entry
                    raw_symbol = symbol.replace('/', '')
                    try:
                        ticker = exchange.fapiPublicGetTickerPrice({'symbol': raw_symbol})
                        current_price = float(ticker['price'])
                        pnl_pct = ((current_price - trade['entry']) / trade['entry'] * 100) if trade['side'] == 'long' else ((trade['entry'] - current_price) / trade['entry'] * 100)
                        print(f"\n[{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}] 📊 {symbol} Active Trade Running:")
                        print(f"Side: {trade['side'].upper()} | Entry: ${trade['entry']:,.2f} | Current: ${current_price:,.2f} | PnL: {pnl_pct:+.2f}%")
                        print(f"SL: ${trade['stop_loss']:,.2f} | TP: ${trade['take_profit']:,.2f} | Breakeven Set: {trade['breakeven_triggered']}")
                    except Exception:
                        pass
                    continue

                # ---------------------------------------------------------
                # 1. 4H Key Levels & Market Structure Analysis
                # ---------------------------------------------------------
                levels_4h = get_4h_levels_and_structure(symbol)
                if levels_4h is None:
                    print(f"⚠️ Could not calculate 4H levels for {symbol}. Skipping...\n")
                    continue

                # ---------------------------------------------------------
                # 2. 1H Execution Timeframe Analysis
                # ---------------------------------------------------------
                df = fetch_data(symbol, ENTRY_TIMEFRAME, limit=250)
                if df is None:
                    continue
                
                df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
                current_close = float(df.iloc[-1]['close'])
                ema_entry = float(df.iloc[-1]['ema50'])
                
                if pd.isna(ema_entry):
                    print(f"⚠️ Not enough data on {ENTRY_TIMEFRAME} for EMA calculation. Skipping...\n")
                    continue

                ltf_uptrend = current_close > ema_entry
                fvg = detect_fvg(df)

                # ---------------------------------------------------------
                # 3. 4H Levels Markdown Output
                # ---------------------------------------------------------
                print(f"\n[{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔍 Analyzing {symbol}...")
                print(f"======================================================================")
                print(f"📊 [4H KEY LEVELS & MARKET STRUCTURE] {symbol}")
                print(f"======================================================================")
                print(f"| Metric                     | Level / Status                       |")
                print(f"|----------------------------|--------------------------------------|")
                print(f"| 4H Structure Bias          | {levels_4h['structure_bias']:<36} |")
                print(f"| 4H Price vs 20 EMA         | ${levels_4h['current_price']:<9,.2f} vs ${levels_4h['ema20']:<9,.2f}           |")
                print(f"| 4H Key Support (Floor)     | ${levels_4h['nearest_support']:<35,.2f} |")
                print(f"| 4H Major Swing Low (SSL)   | ${levels_4h['major_swing_low']:<21,.2f} (Invalidation) |")
                print(f"| 4H Key Resistance (Ceiling)| ${levels_4h['nearest_resistance']:<35,.2f} |")
                print(f"| 4H Major Swing High (BSL)  | ${levels_4h['major_swing_high']:<21,.2f} (Liquidity)     |")
                if levels_4h['order_block']:
                    ob_str = f"{levels_4h['order_block']['type'].upper()} OB [${levels_4h['order_block']['bottom']:,.2f}-${levels_4h['order_block']['top']:,.2f}]"
                    print(f"| 4H Order Block             | {ob_str:<36} |")
                if levels_4h['fvg']:
                    fvg_str = f"{levels_4h['fvg']['type'].upper()} FVG [${levels_4h['fvg']['bottom']:,.2f}-${levels_4h['fvg']['top']:,.2f}]"
                    print(f"| 4H Fair Value Gap          | {fvg_str:<36} |")
                print(f"======================================================================")
                print(f"--- [1H EXECUTION & TRIGGER CONFIRMATION] ({symbol}) ---")
                print(f"1H Price: ${current_close:,.2f} | 1H 50 EMA: ${ema_entry:,.2f} ({'BULLISH' if ltf_uptrend else 'BEARISH'})")
                if fvg:
                    print(f"1H FVG Status: {fvg['type'].upper()} FVG detected between ${fvg['bottom']:.2f} and ${fvg['top']:.2f}")
                else:
                    print(f"1H FVG Status: None detected.")
                print("-" * 70)
                
                # ---------------------------------------------------------
                # 4. Confluence Entry & 4H-Level SL/TP Execution
                # ---------------------------------------------------------
                if fvg and active_trades[symbol] is None:
                    # Enforce global max concurrent trades limit across all symbols
                    current_open_trades = sum(1 for t in active_trades.values() if t is not None)
                    if current_open_trades >= MAX_CONCURRENT_TRADES:
                        active_names = [s for s, t in active_trades.items() if t is not None]
                        print(f"⏸️ Setup detected on {symbol}, but MAX_CONCURRENT_TRADES limit ({current_open_trades}/{MAX_CONCURRENT_TRADES}) reached ({', '.join(active_names)} active). Skipping new entry.\n")
                        continue

                    if levels_4h['structure_bias'] == 'BULLISH' and ltf_uptrend and fvg['type'] == 'bullish':
                        print(f"🚀 [SUPER CONFLUENCE] Mega Bullish 4H/1H Setup on {symbol}!")
                        
                        entry_price = current_close
                        stop_loss, take_profit, dynamic_rr, anchor_sup, anchor_res = calculate_4h_level_sl_tp(levels_4h, entry_price, 'long', fvg)
                        qty = calculate_quantity(entry_price, stop_loss, current_capital, RISK_PER_TRADE, symbol)
                        
                        print(f"🟢 [4H LEVEL-BASED TRADE PLAN]")
                        print(f"   • Entry Price: ${entry_price:,.2f}")
                        print(f"   • Stop Loss:   ${stop_loss:,.2f} (Protected below 4H Support ${anchor_sup:,.2f})")
                        print(f"   • Take Profit: ${take_profit:,.2f} (Targeted before 4H Resistance ${anchor_res:,.2f})")
                        print(f"   • R:R Ratio:   1:{dynamic_rr} | Risking 1% of ${current_capital:,.2f}")
                        print(f"   • Order Qty:   {qty}")
                        order = execute_demo_trade(symbol, 'BUY', qty)
                        
                        if order is not None:
                            active_trades[symbol] = {
                                'side': 'long',
                                'entry': entry_price,
                                'stop_loss': stop_loss,
                                'take_profit': take_profit,
                                'qty': qty,
                                'breakeven_triggered': False
                            }
                            # Place Native Binance Server-Side Cloud SL and TP Orders
                            place_server_sl_tp(symbol, 'long', stop_loss, take_profit)
                        else:
                            print(f"⚠️ Trade setup identified but order execution failed on Binance for {symbol}.")
                        
                    elif levels_4h['structure_bias'] == 'BEARISH' and not ltf_uptrend and fvg['type'] == 'bearish':
                        print(f"📉 [SUPER CONFLUENCE] Mega Bearish 4H/1H Setup on {symbol}!")
                        
                        entry_price = current_close
                        stop_loss, take_profit, dynamic_rr, anchor_res, anchor_sup = calculate_4h_level_sl_tp(levels_4h, entry_price, 'short', fvg)
                        qty = calculate_quantity(entry_price, stop_loss, current_capital, RISK_PER_TRADE, symbol)
                        
                        print(f"🔴 [4H LEVEL-BASED TRADE PLAN]")
                        print(f"   • Entry Price: ${entry_price:,.2f}")
                        print(f"   • Stop Loss:   ${stop_loss:,.2f} (Protected above 4H Resistance ${anchor_res:,.2f})")
                        print(f"   • Take Profit: ${take_profit:,.2f} (Targeted before 4H Support ${anchor_sup:,.2f})")
                        print(f"   • R:R Ratio:   1:{dynamic_rr} | Risking 1% of ${current_capital:,.2f}")
                        print(f"   • Quantity:    {qty}")
                        order = execute_demo_trade(symbol, 'SELL', qty)
                        
                        if order is not None:
                            active_trades[symbol] = {
                                'side': 'short',
                                'entry': entry_price,
                                'stop_loss': stop_loss,
                                'take_profit': take_profit,
                                'qty': qty,
                                'breakeven_triggered': False
                            }
                            # Place Native Binance Server-Side Cloud SL and TP Orders
                            place_server_sl_tp(symbol, 'short', stop_loss, take_profit)
                        else:
                            print(f"⚠️ Trade setup identified but order execution failed on Binance for {symbol}.")

            # ---------------------------------------------------------
            # 2. Smart Sleep Logic (Sleeps until the next 1-Hour candle closes)
            # ---------------------------------------------------------
            now_utc = datetime.now(timezone.utc)
            next_hour_utc = (now_utc + timedelta(hours=1)).replace(minute=0, second=5, microsecond=0)
            total_sleep = (next_hour_utc - now_utc).total_seconds()
            wake_time_local = datetime.now() + timedelta(seconds=total_sleep)
            
            has_active_trades = any(trade is not None for trade in active_trades.values())
            
            if not has_active_trades:
                print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 💤 No open trades. Sleeping for {total_sleep/60:.2f} minutes until {wake_time_local.strftime('%I:%M:%S %p')} Local Time (Exact Candle Close)...")
                time.sleep(total_sleep)
            else:
                active_syms = [s for s, t in active_trades.items() if t is not None]
                print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 💤 Active positions running: {', '.join(active_syms)} ({len(active_syms)}/{MAX_CONCURRENT_TRADES}). Monitoring positions quietly until next candle close at {wake_time_local.strftime('%I:%M:%S %p')} ({total_sleep/60:.2f} mins)...")
                
                # During the hour, monitor open trades every 30s without spamming full market analysis!
                monitor_ticks = 0
                while True:
                    now_utc = datetime.now(timezone.utc)
                    remaining = (next_hour_utc - now_utc).total_seconds()
                    if remaining <= 0:
                        break
                    time.sleep(min(30.0, remaining))
                    daily_loss = manage_active_trades(active_trades, daily_loss)
                    monitor_ticks += 1
                    # Quietly monitors positions every 30s without cluttering the console output
                    
                    # If all trades were closed, wait out the remaining candle time
                    if not any(t is not None for t in active_trades.values()):
                        remaining_mins = max(0.0, (next_hour_utc - datetime.now(timezone.utc)).total_seconds() / 60)
                        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 💤 All trades closed. Waiting {remaining_mins:.2f} mins for next candle close at {wake_time_local.strftime('%I:%M:%S %p')}...")
                        time.sleep(max(0.0, (next_hour_utc - datetime.now(timezone.utc)).total_seconds()))
                        break
            
        except Exception as e:
            print(f"Error in main loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    bot_loop()
