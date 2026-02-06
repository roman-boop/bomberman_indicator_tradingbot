
import pandas as pd
import numpy as np
from scipy.signal import argrelextrema
import datetime

import time, hmac, hashlib, requests, json
# Updated BingxClient with additional methods
class BingxClient:
    BASE_URL = "https://open-api-vst.bingx.com"

    def __init__(self, api_key: str, api_secret: str, symbol: str = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.symbol = self._to_bingx_symbol(symbol) if symbol else None
        self.time_offset = self.get_server_time_offset()

    def _to_bingx_symbol(self, symbol: str) -> str:
        return symbol.replace("USDT", "-USDT")

    def _sign(self, query: str) -> str:
        return hmac.new(self.api_secret.encode("utf-8"),
                        query.encode("utf-8"),
                        hashlib.sha256).hexdigest()

    def parseParam(self, paramsMap: dict) -> str:
        sortedKeys = sorted(paramsMap)
        paramsStr = "&".join(f"{k}={paramsMap[k]}" for k in sortedKeys)
        timestamp = str(int(time.time() * 1000))
        if paramsStr:
            return f"{paramsStr}&timestamp={timestamp}"
        else:
            return f"timestamp={timestamp}"

    APIURL = "https://open-api-vst.bingx.com"

    def send_request(self, method: str, path: str, urlpa: str, payload: dict):
        sign = self._sign(urlpa)
        url = f"{self.APIURL}{path}?{urlpa}&signature={sign}"
        headers = {'X-BX-APIKEY': self.api_key}
        response = requests.request(method, url, headers=headers, data=payload)
        try:
            return response.json()  # ← сразу возвращаем dict
        except Exception as e:
            print("Ошибка при парсинге JSON:", e)
            print("Ответ сервера:", response.text)
            return None

    def _request(self, method: str, path: str, params=None):
        if params is None:
            params = {}
        sorted_keys = sorted(params)
        query = "&".join([f"{k}={params[k]}" for k in sorted_keys])
        signature = self._sign(query)
        url = f"{self.BASE_URL}{path}?{query}&signature={signature}"
        headers = {"X-BX-APIKEY": self.api_key}
        r = requests.request(method, url, headers=headers)
        r.raise_for_status()
        return r.json()

    def _public_request(self, path: str, params=None, timeout: int = 10):
        url = f"{self.BASE_URL}{path}"
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def get_server_time_offset(self):
        url = f"{self.BASE_URL}/openApi/swap/v2/server/time"
        r = requests.get(url)
        r.raise_for_status()
        data = r.json()
        if data.get("code") == 0:
            server_time = int(data["data"]["serverTime"])
            local_time = int(time.time() * 1000)
            return server_time - local_time
        return 0

    def get_mark_price(self, symbol=None):
        path = "/openApi/swap/v2/quote/premiumIndex"
        s = symbol or self.symbol
        params = {'symbol': s}
        try:
            data = self._public_request(path, params)
            if data.get('code') == 0 and 'data' in data:
                if isinstance(data['data'], list) and len(data['data']) > 0:
                    mark_price = data['data'][0].get('markPrice')
                    return float(mark_price) if mark_price is not None else None
                elif isinstance(data['data'], dict):
                    mark_price = data['data'].get('markPrice')
                    return float(mark_price) if mark_price is not None else None
            return None
        except Exception as e:
            return None

    def place_market_order(self, side: str, qty: float, symbol: str = None, stop: float = None, tp: float = None):
        side_param = "BUY" if side == "long" else "SELL"
        s = symbol or self.symbol

        params = {
            "symbol": s,
            "side": side_param,
            "positionSide": "LONG" if side == "long" else "SHORT",
            "type": "MARKET",
            "timestamp": int(time.time()*1000) + self.get_server_time_offset(),
            "quantity": qty,
            "recvWindow": 5000,
            "timeInForce": "GTC",
        }

        # добавляем стоп, если указан
        if stop is not None:
            stopLoss_param = {
                "type": "STOP_MARKET",
                "stopPrice": stop,
                "price": stop,
                "workingType": "MARK_PRICE"
            }
            params["stopLoss"] = json.dumps(stopLoss_param)

        # добавляем тейк, если указан
        if tp is not None:
            takeProfit_param = {
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": tp,
                "price": tp,
                "workingType": "MARK_PRICE"
            }
            params["takeProfit"] = json.dumps(takeProfit_param)

        return self._request("POST", "/openApi/swap/v2/trade/order", params)

    def count_decimal_places(self, number: float) -> int:
        s = str(number).rstrip('0')  
        if '.' in s:
            return len(s.split('.')[1])
        else:
            return 0

    def set_multiple_sl(self, symbol: str, qty: float, entry_price: float, side: str, sl_levels):
        precision = self.count_decimal_places(entry_price)

        if precision >= 3:
            qty_round = 0
        elif precision >= 2:
            qty_round = 1
        elif precision >= 1:
            qty_round = 2
        elif precision == 0:
            qty_round = 3
        qty_sl = round(qty / len(sl_levels), qty_round)
        print(qty_sl)
        for stop in sl_levels:
            params = {
                "symbol": symbol,
                "side": "SELL" if side == "long" else "BUY",
                "positionSide": "LONG" if side == "long" else "SHORT",
                "type": "STOP_MARKET",
                "stopPrice": stop,
                "price": stop,
                "quantity": qty_sl,
                "workingType": "MARK_PRICE",
                "timestamp": int(time.time() * 1000) + self.time_offset,
                "recvWindow": 5000
            }

            try:
                resp = self._request("POST", "/openApi/swap/v2/trade/order", params)
                print(f"[SL2] Установлен стоп: {stop}")
                
            except Exception as e:
                print(f"[SL2 ERROR] {e}")
        return resp

    def set_multiple_tp(self, symbol: str, qty: float, mark_price: float, side: str, tp_levels):
        print(mark_price)
        precision = self.count_decimal_places(mark_price)

        if side == "short":
            tp_side = "BUY"
            pos_side = "SHORT"
        else:
            tp_side = "SELL"
            pos_side = "LONG"

        answer = []
        if precision >= 3:
            qty_round = 0
        elif precision == 2:
            qty_round = 2
        elif precision == 1 :
            qty_round = 3
        elif precision == 0:
            qty_round = 4

        qty_tp = round(qty / len(tp_levels), qty_round)
        print(precision)
        print(qty_tp)
        for tp in tp_levels:
            params = {
                "symbol": symbol,
                "side": tp_side,
                "positionSide": pos_side,
                "type": "TAKE_PROFIT_MARKET",

                "stopPrice": tp,
                "quantity": qty_tp ,
                "timestamp": int(time.time()*1000) + self.time_offset,
                "workingType": "MARK_PRICE"
            }
            try:
                resp = self._request("POST", "/openApi/swap/v2/trade/order", params)
                answer.append(resp)
                print(f"[TP] Установлен тейк-профит {tp}")
            except Exception as e:
                print("[TP ERROR]", e)

        
        return answer

    # Added methods

    def get_klines(self, symbol, interval, limit=1000, start_time=None, end_time=None):
        path = "/openApi/swap/v2/quote/klines"
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': limit
        }
        if start_time:
            params['startTime'] = start_time
        if end_time:
            params['endTime'] = end_time
        data = self._public_request(path, params)
        if data['code'] == 0:
            klines = data['data']
            df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_volume', 'trades', 'taker_buy_base', 'taker_buy_quote', 'ignore'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)
            return df
        else:
            raise Exception(data['msg'])

    def get_positions(self, symbol=None):
        path = "/openApi/swap/v2/user/positions"
        params = {
            'timestamp': int(time.time() * 1000)
        }
        symbol = symbol[:-3] + '-' +symbol[-4:] if '-' not in symbol else symbol
        if symbol:
            params['symbol'] = symbol
        return self._request("GET", path, params)

    def get_balance(self):
        path = "/openApi/swap/v3/user/balance"
        return self._request("GET", path)

    def set_leverage(self, symbol, side, leverage):
        path = "/openApi/swap/v2/user/leverage"
        params = {
            'symbol': symbol,
            'side': side,  # 'LONG', 'SHORT', or 'BOTH' if supported
            'leverage': leverage
        }
        return self._request("POST", path, params)

    def get_open_orders(self, symbol):
        path = "/openApi/swap/v2/trade/allOpenOrders"
        params = {'symbol': symbol}
        return self._request("GET", path, params)

    def cancel_order(self, symbol, order_id):
        path = "/openApi/swap/v2/trade/cancel"
        params = {
            'symbol': symbol,
            'orderId': order_id
        }
        return self._request("POST", path, params)

# Bot code
def calculate_indicators(df, ema_period=100, macd_fast=12, macd_slow=26, macd_signal=9, bb_period=20, bb_std=2.0, atr_period=14, vol_period=20):
    df['ema'] = df['close'].ewm(span=ema_period, adjust=False).mean()
    ema_fast = df['close'].ewm(span=macd_fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=macd_slow, adjust=False).mean()
    df['macd'] = ema_fast - ema_slow
    df['signal'] = df['macd'].ewm(span=macd_signal, adjust=False).mean()
    df['bb_mid'] = df['close'].rolling(bb_period).mean()
    df['bb_std'] = df['close'].rolling(bb_period).std()
    df['bb_upper'] = df['bb_mid'] + bb_std * df['bb_std']
    df['bb_lower'] = df['bb_mid'] - bb_std * df['bb_std']
    df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift()), abs(df['low'] - df['close'].shift())))
    df['atr'] = df['tr'].rolling(atr_period).mean()
    df['vol_avg'] = df['volume'].rolling(vol_period).mean()
    df['bb_width'] = ((df['bb_upper'] - df['bb_lower']) / df['bb_mid']) * 100
    return df

def detect_qml_bull(df, i, order=3, recency=20):
    prices = df['low'][:i+1].values
    local_min_idx = argrelextrema(prices, np.less, order=order)[0]
    if len(local_min_idx) < 3:
        return None
    idx3 = local_min_idx[-1]
    idx2 = local_min_idx[-2]
    idx1 = local_min_idx[-3]
    low1 = prices[idx1]
    low2 = prices[idx2]
    low3 = prices[idx3]
    if low2 < low1 and low3 > low2 and (i - idx3 < recency):
        return low2
    return None

def detect_qml_bear(df, i, order=3, recency=20):
    prices = df['high'][:i+1].values
    local_max_idx = argrelextrema(prices, np.greater, order=order)[0]
    if len(local_max_idx) < 3:
        return None
    idx3 = local_max_idx[-1]
    idx2 = local_max_idx[-2]
    idx1 = local_max_idx[-3]
    high1 = prices[idx1]
    high2 = prices[idx2]
    high3 = prices[idx3]
    if high2 > high1 and high3 < high2 and (i - idx3 < recency):
        return high2
    return None

# Bot parameters from best grid
SYMBOL = 'LTCUSDT'  # Will be converted to BTC-USDT
INTERVAL = '1h'
EMA_PERIOD = 100
BB_STD = 2.0
ATR_SL_MULT = 1.5
RR = 2
QML_ORDER = 3
QML_RECENCY = 20
USE_VOL_FILTER = False
SQUEEZE_THRESHOLD = 1.0
USE_QML_EXTREME_SL = False
LEVERAGE = 20
RISK_PERCENT = 0.01  # 1% risk per trade
QTY_PRECISION = 3  # For BTC, e.g., 0.001

# API keys (replace with yours)
API_KEY = ''
API_SECRET = ''

client = BingxClient(API_KEY, API_SECRET, SYMBOL)

# Set leverage
try:
    client.set_leverage(client.symbol, 'LONG', LEVERAGE)
    client.set_leverage(client.symbol, 'SHORT', LEVERAGE)
    print("Leverage set")
except Exception as e:
    print(f"Leverage set error: {e}")

position = None  # Current position state

while True:
    now = datetime.datetime.utcnow()
    
    # Check for new candle close (every hour, at :00)
    if now.minute == 46:  # Around the hour
        print("Checking for signals...")
        time.sleep(10)  # Wait for data update
        
        try:
            df = client.get_klines(client.symbol, INTERVAL, limit=200)
            df = calculate_indicators(df, ema_period=EMA_PERIOD, bb_std=BB_STD)
            i = len(df) - 1
            
            # Check if already in position
            pos_resp = client.get_positions(client.symbol)
            in_pos = False
            current_pos = None
            if pos_resp['code'] == 0 and 'data' in pos_resp and pos_resp['data']:
                for p in pos_resp['data']:
                    if float(p.get('positionAmt', 0)) != 0:
                        in_pos = True
                        current_pos = p
                        break
            print(pos_resp)
            if not in_pos:
                
                # Filters
                vol_filter_pass = (not USE_VOL_FILTER) or (df['volume'].iloc[i] > df['vol_avg'].iloc[i] * 0.8)
                no_squeeze = df['bb_width'].iloc[i] >= SQUEEZE_THRESHOLD
                if vol_filter_pass and no_squeeze:
                    # Long signal
                    macd_cross_up = (df['macd'].iloc[i] > df['signal'].iloc[i]) and (df['macd'].iloc[i-1] <= df['signal'].iloc[i-1])
                    price_above_ema = df['close'].iloc[i] > df['ema'].iloc[i]
                    qml_low = detect_qml_bull(df, i, order=QML_ORDER, recency=QML_RECENCY)
                    if macd_cross_up and price_above_ema and qml_low is not None:
                        current_price = client.get_mark_price(client.symbol)
                        atr = df['atr'].iloc[i]
                        sl = current_price - ATR_SL_MULT * atr if not USE_QML_EXTREME_SL else qml_low - ATR_SL_MULT * atr
                        risk = current_price - sl
                        tp = current_price + RR * risk
                        
                        # Get balance
                        bal_resp = client.get_balance()
                        balance = float(bal_resp['data'][0]['availableBalance'])  # Assuming structure
                        
                        risk_usdt = RISK_PERCENT * balance
                        qty = risk_usdt / risk
                        qty = round(qty, QTY_PRECISION)
                        
                        if qty > 0:
                            # Place market order without SL/TP
                            order_resp = client.place_market_order('long', qty, client.symbol)
                            if order_resp['code'] == 0:
                                entry = float(order_resp['data']['avgPrice'])  # Assuming key
                                # Recalc SL/TP with actual entry
                                sl = entry - ATR_SL_MULT * atr if not USE_QML_EXTREME_SL else qml_low - ATR_SL_MULT * atr
                                tp = entry + RR * (entry - sl)
                                
                                # Place SL
                                sl_levels = [sl]
                                sl_resp = client.set_multiple_sl(client.symbol, qty, entry, 'long', sl_levels)
                                sl_order_id = sl_resp['data']['orderId']  # Assuming
                                
                                # Place TP
                                tp_levels = [tp]
                                tp_resp = client.set_multiple_tp(client.symbol, qty, entry, 'long', tp_levels)
                                tp_order_id = tp_resp[0]['data']['orderId']  # Assuming list
                                
                                position = {
                                    'type': 'long',
                                    'entry': entry,
                                    'sl': sl,
                                    'tp': tp,
                                    'qty': qty,
                                    'sl_order_id': sl_order_id,
                                    'tp_order_id': tp_order_id,
                                    'reached_1to1': False
                                }
                                print(f"Opened long at {entry}, SL {sl}, TP {tp}")
                    
                    # Short signal
                    macd_cross_down = (df['macd'].iloc[i] < df['signal'].iloc[i]) and (df['macd'].iloc[i-1] >= df['signal'].iloc[i-1])
                    price_below_ema = df['close'].iloc[i] < df['ema'].iloc[i]
                    qml_high = detect_qml_bear(df, i, order=QML_ORDER, recency=QML_RECENCY)
                    if macd_cross_down and price_below_ema and qml_high is not None:
                        current_price = client.get_mark_price(client.symbol)
                        atr = df['atr'].iloc[i]
                        sl = current_price + ATR_SL_MULT * atr if not USE_QML_EXTREME_SL else qml_high + ATR_SL_MULT * atr
                        risk = sl - current_price
                        tp = current_price - RR * risk
                        
                        # Balance and qty same as above
                        bal_resp = client.get_balance()
                        balance = float(bal_resp['data'][0]['availableBalance'])
                        risk_usdt = RISK_PERCENT * balance
                        qty = risk_usdt / risk
                        qty = round(qty, QTY_PRECISION)
                        
                        if qty > 0:
                            order_resp = client.place_market_order('short', qty, client.symbol)
                            if order_resp['code'] == 0:
                                entry = float(order_resp['data']['avgPrice'])
                                sl = entry + ATR_SL_MULT * atr if not USE_QML_EXTREME_SL else qml_high + ATR_SL_MULT * atr
                                tp = entry - RR * (sl - entry)
                                
                                sl_levels = [sl]
                                sl_resp = client.set_multiple_sl(client.symbol, qty, entry, 'short', sl_levels)
                                sl_order_id = sl_resp['data']['orderId']
                                
                                tp_levels = [tp]
                                tp_resp = client.set_multiple_tp(client.symbol, qty, entry, 'short', tp_levels)
                                tp_order_id = tp_resp[0]['data']['orderId']
                                
                                position = {
                                    'type': 'short',
                                    'entry': entry,
                                    'sl': sl,
                                    'tp': tp,
                                    'qty': qty,
                                    'sl_order_id': sl_order_id,
                                    'tp_order_id': tp_order_id,
                                    'reached_1to1': False
                                }
                                print(f"Opened short at {entry}, SL {sl}, TP {tp}")
        except Exception as e:
            print(f"Signal check error: {e}")
    
    # Monitor position (trailing, check close)
    """if position:
        try:
            current_price = client.get_mark_price(client.symbol)
            if position['type'] == 'long':
                risk = position['entry'] - position['sl']
                if not position['reached_1to1'] and current_price >= position['entry'] + risk:
                    position['reached_1to1'] = True
                    print("Reached 1:1 for long")
                if position['reached_1to1']:
                    df = client.get_klines(client.symbol, INTERVAL, limit=100)
                    df = calculate_indicators(df)
                    bb_mid = df['bb_mid'].iloc[-1]
                    new_sl = max(position['sl'], bb_mid)
                    if new_sl > position['sl']:
                        # Cancel old SL
                        client.cancel_order(client.symbol, position['sl_order_id'])
                        # Place new SL
                        sl_levels = [new_sl]
                        sl_resp = client.set_multiple_sl(client.symbol, position['qty'], position['entry'], 'long', sl_levels)
                        position['sl_order_id'] = sl_resp['data']['orderId']
                        position['sl'] = new_sl
                        print(f"Trailed SL to {new_sl}")
            else:  # short
                risk = position['sl'] - position['entry']
                if not position['reached_1to1'] and current_price <= position['entry'] - risk:
                    position['reached_1to1'] = True
                    print("Reached 1:1 for short")
                if position['reached_1to1']:
                    df = client.get_klines(client.symbol, INTERVAL, limit=100)
                    df = calculate_indicators(df)
                    bb_mid = df['bb_mid'].iloc[-1]
                    new_sl = min(position['sl'], bb_mid)
                    if new_sl < position['sl']:
                        client.cancel_order(client.symbol, position['sl_order_id'])
                        sl_levels = [new_sl]
                        sl_resp = client.set_multiple_sl(client.symbol, position['qty'], position['entry'], 'short', sl_levels)
                        position['sl_order_id'] = sl_resp['data']['orderId']
                        position['sl'] = new_sl
                        print(f"Trailed SL to {new_sl}")
            
            # Check if position closed
            pos_resp = client.get_positions(client.symbol)
            still_open = False
            if pos_resp['code'] == 0 and 'data' in pos_resp and pos_resp['data']:
                for p in pos_resp['data']:
                    if p['positionSide'] == position['type'].upper() and float(p.get('positionAmt', 0)) != 0:
                        still_open = True
                        break
            if not still_open:
                position = None
                print("Position closed")
        except Exception as e:
            print(f"Monitor error: {e}")"""
    
    time.sleep(60)  # Check every minute