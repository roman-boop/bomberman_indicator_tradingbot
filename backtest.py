# --------------------------------------------------------------
# bomberman_backtest_with_shorts.py
# --------------------------------------------------------------
# pip install ccxt pandas numpy matplotlib
# --------------------------------------------------------------

import ccxt
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import warnings
from datetime import datetime
from binance.client import Client
client = Client()
import time
# ------------------------------------------------------------------
# 1. Загрузка данных
# ------------------------------------------------------------------
def fetch_binance_ohlcv(symbol='BTCUSDT', interval='15m', total_bars=50000, client=client):
    limit = 1000
    data = []
    current_end = None
    while len(data) < total_bars:
        bars_to_fetch = min(limit, total_bars - len(data))
        try:
            klines = client.futures_klines(
                symbol=symbol.upper(),
                interval=interval,
                limit=bars_to_fetch,
                endTime=current_end
            )
        except Exception as e:
            print("Ошибка Binance API:", e)
            break
        if not klines:
            break
        data = klines + data  # prepend
        current_end = klines[0][0] - 1
        time.sleep(0.1)

    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data, columns=[
        'timestamp','open','high','low','close','volume',
        'close_time','quote_asset_volume','number_of_trades',
        'taker_buy_base','taker_buy_quote','ignore'
    ])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)
    df = df.drop_duplicates('timestamp').sort_values('timestamp').reset_index(drop=True)
    df = df.rename(columns={'timestamp': 'time'})
    return df[["time", "open", "high", "low", "close", "volume"]]


# ------------------------------------------------------------------
# 2. Индикаторы
# ------------------------------------------------------------------
def add_bop(df: pd.DataFrame, smooth: int = 1) -> pd.DataFrame:
    df = df.copy()
    df['bop'] = (df['close'] - df['open']) / (df['high'] - df['low']).replace(0, np.nan)
    if smooth > 1:
        df['bop'] = df['bop'].rolling(smooth).mean()
    return df


def add_mean_reversion(df: pd.DataFrame, period: int = 20, mult: float = 2.0) -> pd.DataFrame:
    df = df.copy()
    df['sma'] = df['close'].rolling(period).mean()
    df['std'] = df['close'].rolling(period).std()
    df['upper'] = df['sma'] + mult * df['std']
    df['lower'] = df['sma'] - mult * df['std']
    return df


def add_donchian(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    df = df.copy()
    df['donchian_high'] = df['high'].rolling(period).max()
    df['donchian_low']  = df['low'].rolling(period).min()
    return df


# ------------------------------------------------------------------
# 3. Синхронизация (walk-forward)
# ------------------------------------------------------------------
def sync_timeframes(df30, df15, df5):
    # Исправление: join по 'time', а не по numeric index
    df30 = df30.set_index('time')
    df15 = df15.set_index('time')
    df5 = df5.set_index('time')
    df = df30.join(df15[['upper', 'lower']], how='left')
    df = df.join(df5[['donchian_high', 'donchian_low']], how='left')
    cols = ['upper', 'lower', 'donchian_high', 'donchian_low']
    df[cols] = df[cols].ffill()  # Только прошлые данные
    df = df.reset_index()  # Вернуть 'time' как колонку
    return df


# ------------------------------------------------------------------
# 4. Walk-forward бэктест (LONG + SHORT)
# ------------------------------------------------------------------
def backtest_one(df: pd.DataFrame,
                 tol: float,
                 direction: str = "both",
                 initial_capital: float = 10_000,
                 leverage: int = 20,
                 risk_per_trade: float = 0.01) -> dict:

    capital = initial_capital
    position = 0  # 1 = long, -1 = short, 0 = flat
    entry_price = 0.0
    size = 0.0
    equity = [capital]
    trades = []  # (index, description)

    for i in range(50, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]  # Исправлена опечатка
        price = row['close']

        # --- ВХОД ---
        if position == 0:
            # LONG
            if direction in ["long", "both"]:
                long_cond = (
                    row['bop'] > 0 and
                    price <= row['lower'] * (1 + tol) and
                    price > prev['donchian_high']
                )
                if long_cond:
                    size = (capital * risk_per_trade * leverage) / price
                    entry_price = price
                    position = 1
                    trades.append((i, f"LONG at {price:.5f}"))

            # SHORT
            if direction in ["short", "both"]:
                short_cond = (
                    row['bop'] < 0 and
                    price >= row['upper'] * (1 - tol) and
                    price < prev['donchian_low']
                )
                if short_cond:
                    size = (capital * risk_per_trade * leverage) / price
                    entry_price = price
                    position = -1
                    trades.append((i, f"SHORT at {price:.5f}"))

        # --- ВЫХОД ---
        commission_rate = 0.001  # 0.1% = 0.001 (taker fee на Binance Futures)

        if position == 1:  # LONG
            if price >= row['upper']:
                gross_pnl = size * (price - entry_price) * leverage
                # Комиссия: при входе и при выходе
                entry_commission = size * entry_price * commission_rate
                exit_commission = size * price * commission_rate
                net_pnl = gross_pnl - entry_commission - exit_commission
                capital += net_pnl
                position = 0
                trades.append((i, f"EXIT LONG at {price:.5f} | PnL: {net_pnl:+.2f} (comm: {entry_commission + exit_commission:.2f})"))

            elif price <= entry_price * 0.99:
                gross_pnl = size * (price - entry_price) * leverage
                entry_commission = size * entry_price * commission_rate
                exit_commission = size * price * commission_rate
                net_pnl = gross_pnl - entry_commission - exit_commission
                capital += net_pnl
                position = 0
                trades.append((i, f"STOP LONG at {price:.5f} | PnL: {net_pnl:+.2f} (comm: {entry_commission + exit_commission:.2f})"))

        if position == -1:  # SHORT
            if price <= row['lower']:
                gross_pnl = size * (entry_price - price) * leverage
                entry_commission = size * entry_price * commission_rate
                exit_commission = size * price * commission_rate
                net_pnl = gross_pnl - entry_commission - exit_commission
                capital += net_pnl
                position = 0
                trades.append((i, f"EXIT SHORT at {price:.5f} | PnL: {net_pnl:+.2f} (comm: {entry_commission + exit_commission:.2f})"))

            elif price >= entry_price * 1.01:
                gross_pnl = size * (entry_price - price) * leverage
                entry_commission = size * entry_price * commission_rate
                exit_commission = size * price * commission_rate
                net_pnl = gross_pnl - entry_commission - exit_commission
                capital += net_pnl
                position = 0
                trades.append((i, f"STOP SHORT at {price:.5f} | PnL: {net_pnl:+.2f} (comm: {entry_commission + exit_commission:.2f})"))

        equity.append(capital)

    total_ret = (capital - initial_capital) / initial_capital
    days = (df['time'].iloc[-1] - df['time'].iloc[0]).days if len(df) > 0 else 1
    annualized = total_ret * (365 / days) if days > 0 else total_ret 

    return {
        'final_capital': capital,
        'total_return': total_ret,
        'annualized': annualized,
        'equity': equity,
        'trades': trades
    }


# ------------------------------------------------------------------
# 5. Грид-поиск (с long/short/both)
# ------------------------------------------------------------------
def grid_search(df30, df15, df5):
    param_grid = {
        'mr_period': [14, 30, 45],
        'mr_mult'  : [1.5, 2.5],
        'dc_period': [20, 14, 38],
        'bop_smooth': [1, 2],
        'tol'      : [0.015, 0.002, 0.01],
        'direction': ['both']
    }

    results = []
    total = np.prod([len(v) for v in param_grid.values()])
    print(f"Грид-поиск: {total} комбинаций...")

    cnt = 0
    for mr_p in param_grid['mr_period']:
        for mr_m in param_grid['mr_mult']:
            for dc_p in param_grid['dc_period']:
                for bop_s in param_grid['bop_smooth']:
                    for tol in param_grid['tol']:
                        for dir_ in param_grid['direction']:
                            cnt += 1
                            print(f"[{cnt}/{total}] MR={mr_p}*{mr_m}, DC={dc_p}, BOP_s={bop_s}, tol={tol*100:.1f}%, {dir_}")

                            df_mr = add_mean_reversion(df15.copy(), mr_p, mr_m)
                            df_dc = add_donchian(df5.copy(), dc_p)
                            df_bop = add_bop(df30.copy(), bop_s)

                            df_test = sync_timeframes(df_bop, df_mr, df_dc)
                            res = backtest_one(df_test, tol, direction=dir_)

                            results.append({
                                'mr_period': mr_p,
                                'mr_mult': mr_m,
                                'dc_period': dc_p,
                                'bop_smooth': bop_s,
                                'tolerance': tol,
                                'direction': dir_,
                                'final_capital': res['final_capital'],
                                'total_return': res['total_return'],
                                'annualized': res['annualized']
                            })

    return pd.DataFrame(results)


# ------------------------------------------------------------------
# 6. Визуализация лучшего
# ------------------------------------------------------------------
def plot_best(df30, df15, df5, best_row):
    df_mr = add_mean_reversion(df15.copy(),
                               int(best_row['mr_period']),
                               best_row['mr_mult'])
    df_dc = add_donchian(df5.copy(),
                         int(best_row['dc_period']))
    df_bop = add_bop(df30.copy(),
                     int(best_row['bop_smooth']))

    df = sync_timeframes(df_bop, df_mr, df_dc)

    # Пересчёт с лучшими параметрами
    res = backtest_one(df,
                       best_row['tolerance'],
                       direction=best_row['direction'])

    # Сигналы
    df['signal'] = 0
    for idx, description in res['trades']:
        if 'LONG at' in description:
            df.loc[idx, 'signal'] = 1
        elif 'SHORT at' in description:
            df.loc[idx, 'signal'] = -1

    plt.figure(figsize=(16, 10))
    ax1 = plt.subplot(2, 1, 1)  # Исправлено на 2 subplot
    ax1.plot(df['time'], df['close'], label='BTC/USDT', color='steelblue')
    ax1.plot(df['time'], df['lower'], '--', color='green', label='Lower')
    ax1.plot(df['time'], df['upper'], '--', color='red', label='Upper')
    ax1.scatter(df['time'][df['signal'] == 1], df['close'][df['signal'] == 1],
                marker='^', color='lime', s=100, label='LONG')
    ax1.scatter(df['time'][df['signal'] == -1], df['close'][df['signal'] == -1],
                marker='v', color='red', s=100, label='SHORT')
    ax1.set_title(f'Bomberman | {best_row["direction"].upper()} | '
                  f'Годовых: {best_row["annualized"]*100:.1f}%')
    ax1.legend()
    ax1.grid(True, alpha=0.3)


    ax3 = plt.subplot(2, 1, 2)
    ax3.plot(res['equity'], color='gold')
    ax3.set_title(f'Капитал: ${res["final_capital"]:,.0f}')
    ax3.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()


# ------------------------------------------------------------------
# 7. MAIN
# ------------------------------------------------------------------
if __name__ == "__main__":
    print("Загрузка данных...")
    df_30m = fetch_binance_ohlcv('BTCUSDT', '30m', 10000)
    df_15m = fetch_binance_ohlcv('BTCUSDT', '15m', 20000)
    df_5m  = fetch_binance_ohlcv('BTCUSDT', '5m',  60000)
    print(f"  1h: {len(df_30m)} | 30m: {len(df_15m)} | 15m: {len(df_5m)}")  # Переименовал для ясности

    results_df = grid_search(df_30m, df_15m, df_5m)

    top5 = results_df.sort_values('annualized', ascending=False).head(5)
    print("\n" + "="*80)
    print("ТОП-5 ПАРАМЕТРОВ (с LONG/SHORT/BOTH)")
    print("="*80)
    print(top5[['direction', 'mr_period', 'mr_mult', 'dc_period',
                'bop_smooth', 'tolerance', 'annualized', 'final_capital']])

    best = top5.iloc[0]
    print(f"\nВизуализация лучшего: {best['direction']} | "
          f"Годовых: {best['annualized']*100:.1f}%")
    plot_best(df_30m, df_15m, df_5m, best)