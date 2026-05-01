#!/usr/bin/env python3
"""
Pocket Option Signals Bot with Trade Management
Entry Points, Stop Loss & Take Profit System
Production ready for Railway deployment
"""

import os
import asyncio
import logging
import json
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from enum import Enum
import pandas as pd
import numpy as np
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import ccxt
from aiohttp import web

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class SignalType(Enum):
    BUY = "CALL"
    SELL = "PUT"
    NONE = "WAIT"

@dataclass
class TradingConfig:
    symbol: str = "BTC/USDT"
    timeframe: str = "5m"
    ema_period: int = 200
    keltner_period: int = 20
    keltner_multiplier: float = 2.0
    rsi_period: int = 14
    rsi_buy_threshold: int = 38
    rsi_sell_threshold: int = 62
    sar_acceleration: float = 0.02
    sar_maximum: float = 0.2
    check_interval: int = 30

@dataclass
class TradeManagement:
    """Trade entry, stop loss and take profit configuration"""
    # Risk:Reward ratios
    rr_ratio_1: float = 1.5  # Conservative
    rr_ratio_2: float = 2.0  # Moderate
    rr_ratio_3: float = 3.0  # Aggressive

    # Stop loss methods
    sl_atr_multiplier: float = 1.5  # SL = Entry Â± (ATR * multiplier)
    sl_fixed_pips: float = 0.0  # 0 = use ATR-based

    # Entry confirmation
    min_confirmation_candles: int = 1
    max_entry_delay_minutes: int = 3

    # Partial exits
    partial_exit_1: float = 0.30  # Close 30% at R:R 1.5
    partial_exit_2: float = 0.30  # Close 30% at R:R 2.0
    trail_remaining: bool = True  # Trail stop on remaining 40%

    # Breakeven
    move_to_breakeven_at: float = 1.0  # Move SL to entry when price hits 1:1

@dataclass
class MartingaleConfig:
    enabled: bool = True
    max_levels: int = 3
    multiplier: float = 2.2
    initial_stake: float = 10.0
    profit_percent: float = 85.0

    def calculate_stakes(self) -> List[float]:
        stakes = [self.initial_stake]
        for i in range(1, self.max_levels):
            stakes.append(round(stakes[-1] * self.multiplier, 2))
        return stakes

    def calculate_profit(self, stake: float) -> float:
        return round(stake * (self.profit_percent / 100), 2)

    def calculate_total_risk(self, level: int) -> float:
        stakes = self.calculate_stakes()
        return round(sum(stakes[:level+1]), 2)

@dataclass
class TradeSignal:
    """Complete trade signal with entry, SL, TP"""
    signal_type: SignalType
    symbol: str
    entry_price: float
    stop_loss: float
    take_profit_1: float  # R:R 1.5
    take_profit_2: float  # R:R 2.0
    take_profit_3: float  # R:R 3.0
    atr_value: float
    risk_reward: float
    position_size: float
    confidence: float
    timestamp: str
    conditions_met: List[str]

    def risk_amount(self) -> float:
        return abs(self.entry_price - self.stop_loss)

    def risk_percent(self) -> float:
        return (self.risk_amount() / self.entry_price) * 100

class TechnicalAnalysis:
    @staticmethod
    def calculate_ema(data: pd.Series, period: int) -> pd.Series:
        return data.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_keltner_channels(df: pd.DataFrame, period: int = 20, multiplier: float = 2.0):
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        atr = TechnicalAnalysis.calculate_atr(df, period)
        ema_tp = typical_price.ewm(span=period, adjust=False).mean()
        upper_band = ema_tp + (multiplier * atr)
        lower_band = ema_tp - (multiplier * atr)
        return upper_band, ema_tp, lower_band

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14):
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        return true_range.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_rsi(prices: pd.Series, period: int = 14):
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def calculate_sar(df: pd.DataFrame, acceleration: float = 0.02, maximum: float = 0.2):
        high = df['high'].values
        low = df['low'].values
        close = df['close'].values

        sar = np.zeros(len(df))
        trend = np.zeros(len(df))
        ep = np.zeros(len(df))
        af = np.zeros(len(df))

        if len(close) > 1:
            trend[0] = 1 if close[0] > close[1] else -1
        else:
            trend[0] = 1
        sar[0] = low[0] if trend[0] == 1 else high[0]
        ep[0] = high[0] if trend[0] == 1 else low[0]
        af[0] = acceleration

        for i in range(1, len(df)):
            sar[i] = sar[i-1] + af[i-1] * (ep[i-1] - sar[i-1])

            if trend[i-1] == 1:
                sar[i] = min(sar[i], low[i-1], low[i-2] if i > 1 else low[i-1])
                if high[i] > ep[i-1]:
                    ep[i] = high[i]
                    af[i] = min(af[i-1] + acceleration, maximum)
                else:
                    ep[i] = ep[i-1]
                    af[i] = af[i-1]

                if low[i] < sar[i]:
                    trend[i] = -1
                    sar[i] = ep[i-1]
                    ep[i] = low[i]
                    af[i] = acceleration
                else:
                    trend[i] = 1
            else:
                sar[i] = max(sar[i], high[i-1], high[i-2] if i > 1 else high[i-1])
                if low[i] < ep[i-1]:
                    ep[i] = low[i]
                    af[i] = min(af[i-1] + acceleration, maximum)
                else:
                    ep[i] = ep[i-1]
                    af[i] = af[i-1]

                if high[i] > sar[i]:
                    trend[i] = 1
                    sar[i] = ep[i-1]
                    ep[i] = high[i]
                    af[i] = acceleration
                else:
                    trend[i] = -1

        return pd.Series(sar, index=df.index), pd.Series(trend, index=df.index)

    @staticmethod
    def is_rejection_candle(df: pd.DataFrame, lookback: int = 1) -> Tuple[bool, str]:
        for i in range(1, lookback + 1):
            if i >= len(df):
                continue

            candle = df.iloc[-i]
            body = abs(candle['close'] - candle['open'])
            upper_shadow = candle['high'] - max(candle['open'], candle['close'])
            lower_shadow = min(candle['open'], candle['close']) - candle['low']

            if lower_shadow > 2 * body and upper_shadow < body:
                return True, "bullish"

            if upper_shadow > 2 * body and lower_shadow < body:
                return True, "bearish"

        return False, "none"

    @staticmethod
    def find_support_resistance(df: pd.DataFrame, lookback: int = 20) -> Tuple[float, float]:
        """Find recent support and resistance levels"""
        recent = df.tail(lookback)
        support = recent['low'].min()
        resistance = recent['high'].max()
        return support, resistance

class TradingStrategy:
    def __init__(self, config: TradingConfig, trade_mgmt: TradeManagement):
        self.config = config
        self.trade_mgmt = trade_mgmt
        self.exchange = ccxt.binance({'enableRateLimit': True})

    async def fetch_ohlcv(self, limit: int = 250):
        try:
            ohlcv = self.exchange.fetch_ohlcv(
                self.config.symbol, 
                self.config.timeframe, 
                limit=limit
            )
            df = pd.DataFrame(
                ohlcv, 
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            logger.error(f"Error fetching data: {e}")
            return None

    def calculate_trade_levels(self, df: pd.DataFrame, signal_type: SignalType, 
                               price: float, atr: float) -> TradeSignal:
        """Calculate complete trade levels: entry, SL, TP"""

        support, resistance = TechnicalAnalysis.find_support_resistance(df)

        if signal_type == SignalType.BUY:
            # For BUY: SL below recent support or ATR-based
            if self.trade_mgmt.sl_fixed_pips > 0:
                stop_loss = price - self.trade_mgmt.sl_fixed_pips
            else:
                # Use the lower of: ATR-based or recent support
                atr_sl = price - (atr * self.trade_mgmt.sl_atr_multiplier)
                stop_loss = min(atr_sl, support * 0.998)  # Slight buffer below support

            risk = price - stop_loss

            # Take profits
            tp1 = price + (risk * self.trade_mgmt.rr_ratio_1)
            tp2 = price + (risk * self.trade_mgmt.rr_ratio_2)
            tp3 = price + (risk * self.trade_mgmt.rr_ratio_3)

        else:  # SELL
            if self.trade_mgmt.sl_fixed_pips > 0:
                stop_loss = price + self.trade_mgmt.sl_fixed_pips
            else:
                atr_sl = price + (atr * self.trade_mgmt.sl_atr_multiplier)
                stop_loss = max(atr_sl, resistance * 1.002)  # Slight buffer above resistance

            risk = stop_loss - price

            # Take profits
            tp1 = price - (risk * self.trade_mgmt.rr_ratio_1)
            tp2 = price - (risk * self.trade_mgmt.rr_ratio_2)
            tp3 = price - (risk * self.trade_mgmt.rr_ratio_3)

        # Calculate position size based on 1% risk of $1000 account
        account_balance = 1000.0  # Default, can be configured
        risk_percent = 1.0
        risk_amount = account_balance * (risk_percent / 100)
        position_size = risk_amount / risk

        return TradeSignal(
            signal_type=signal_type,
            symbol=self.config.symbol,
            entry_price=round(price, 2),
            stop_loss=round(stop_loss, 2),
            take_profit_1=round(tp1, 2),
            take_profit_2=round(tp2, 2),
            take_profit_3=round(tp3, 2),
            atr_value=round(atr, 4),
            risk_reward=round(self.trade_mgmt.rr_ratio_2, 1),
            position_size=round(position_size, 4),
            confidence=0.0,
            timestamp=datetime.now().strftime('%H:%M:%S'),
            conditions_met=[]
        )

    async def analyze(self) -> Dict:
        df = await self.fetch_ohlcv()
        if df is None or len(df) < self.config.ema_period + 10:
            return {"signal": SignalType.NONE, "reason": "Insufficient data"}

        # Calculate indicators
        df['ema200'] = TechnicalAnalysis.calculate_ema(df['close'], self.config.ema_period)
        df['upper_kc'], df['middle_kc'], df['lower_kc'] = TechnicalAnalysis.calculate_keltner_channels(
            df, self.config.keltner_period, self.config.keltner_multiplier
        )
        df['rsi'] = TechnicalAnalysis.calculate_rsi(df['close'], self.config.rsi_period)
        df['sar'], df['sar_trend'] = TechnicalAnalysis.calculate_sar(
            df, self.config.sar_acceleration, self.config.sar_maximum
        )
        df['atr'] = TechnicalAnalysis.calculate_atr(df)

        current = df.iloc[-1]
        previous = df.iloc[-2]
        price = current['close']
        atr = current['atr']

        rejection, rejection_type = TechnicalAnalysis.is_rejection_candle(df)

        analysis = {
            "price": price,
            "ema200": current['ema200'],
            "upper_kc": current['upper_kc'],
            "lower_kc": current['lower_kc'],
            "rsi": current['rsi'],
            "sar": current['sar'],
            "atr": atr,
            "rejection": rejection,
            "rejection_type": rejection_type,
            "signal": SignalType.NONE,
            "conditions_met": [],
            "timestamp": datetime.now().strftime('%H:%M:%S'),
            "trade": None
        }

        # BUY Strategy (CALL)
        if (price > current['ema200'] and 
            current['low'] <= current['lower_kc'] and 
            current['rsi'] < self.config.rsi_buy_threshold and 
            rejection and rejection_type == "bullish" and
            previous['sar'] > previous['close'] and current['sar'] < current['close']):

            analysis["signal"] = SignalType.BUY
            analysis["conditions_met"] = [
                f"Price > EMA200 ({price:.2f} > {current['ema200']:.2f})",
                f"Touch Lower Keltner ({current['lower_kc']:.2f})",
                f"RSI Oversold ({current['rsi']:.1f})",
                "Bullish Rejection Candle",
                "SAR Flip Below"
            ]

            # Calculate trade levels
            trade = self.calculate_trade_levels(df, SignalType.BUY, price, atr)
            trade.conditions_met = analysis["conditions_met"]
            trade.confidence = 95.0  # All conditions met
            analysis["trade"] = trade

        # SELL Strategy (PUT)
        elif (price < current['ema200'] and 
              current['high'] >= current['upper_kc'] and 
              current['rsi'] > self.config.rsi_sell_threshold and 
              rejection and rejection_type == "bearish" and
              previous['sar'] < previous['close'] and current['sar'] > current['close']):

            analysis["signal"] = SignalType.SELL
            analysis["conditions_met"] = [
                f"Price < EMA200 ({price:.2f} < {current['ema200']:.2f})",
                f"Touch Upper Keltner ({current['upper_kc']:.2f})",
                f"RSI Overbought ({current['rsi']:.1f})",
                "Bearish Rejection Candle",
                "SAR Flip Above"
            ]

            # Calculate trade levels
            trade = self.calculate_trade_levels(df, SignalType.SELL, price, atr)
            trade.conditions_met = analysis["conditions_met"]
            trade.confidence = 95.0
            analysis["trade"] = trade

        return analysis

class PocketOptionBot:
    def __init__(self):
        self.config = TradingConfig()
        self.trade_mgmt = TradeManagement()
        self.martingale = MartingaleConfig()
        self.strategy = TradingStrategy(self.config, self.trade_mgmt)
        self.monitoring = False
        self.monitor_task = None
        self.subscribers = set()
        self.application = None
        self.active_trades = {}  # Track active trades

    def get_main_keyboard(self):
        keyboard = [
            [InlineKeyboardButton("ðŸ“Š GET SIGNAL", callback_data='analyze')],
            [InlineKeyboardButton("â–¶ï¸ Auto Signals", callback_data='start_monitor'),
             InlineKeyboardButton("â¹ï¸ Stop", callback_data='stop_monitor')],
            [InlineKeyboardButton("ðŸ’° Martingale", callback_data='martingale'),
             InlineKeyboardButton("ðŸ“ˆ Strategy", callback_data='strategy')],
            [InlineKeyboardButton("âš™ï¸ Trade Settings", callback_data='trade_settings')]
        ]
        return InlineKeyboardMarkup(keyboard)

    def get_trade_settings_keyboard(self):
        keyboard = [
            [InlineKeyboardButton("R:R 1.5 (Conservative)", callback_data='rr_15'),
             InlineKeyboardButton("R:R 2.0 (Moderate)", callback_data='rr_20')],
            [InlineKeyboardButton("R:R 3.0 (Aggressive)", callback_data='rr_30')],
            [InlineKeyboardButton("SL ATR x1.5", callback_data='sl_atr_15'),
             InlineKeyboardButton("SL ATR x2.0", callback_data='sl_atr_20')],
            [InlineKeyboardButton("Breakeven ON", callback_data='be_on'),
             InlineKeyboardButton("Breakeven OFF", callback_data='be_off')],
            [InlineKeyboardButton("â¬…ï¸ Back", callback_data='back_main')]
        ]
        return InlineKeyboardMarkup(keyboard)

    def get_martingale_keyboard(self):
        keyboard = [
            [InlineKeyboardButton("ðŸ“Š Show Levels", callback_data='mg_show')],
            [InlineKeyboardButton("âš™ï¸ Settings", callback_data='mg_settings'),
             InlineKeyboardButton("ðŸ”„ Reset", callback_data='mg_reset')],
            [InlineKeyboardButton("â¬…ï¸ Back", callback_data='back_main')]
        ]
        return InlineKeyboardMarkup(keyboard)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        self.subscribers.add(chat_id)

        welcome_msg = """
ðŸ¤– <b>Advanced Trading Bot</b>

âš¡ <b>Signals with Entry, Stop Loss & Take Profit</b>

<b>Features:</b>
â€¢ Exact entry price calculations
â€¢ ATR-based stop loss
â€¢ Multiple take profit targets (1.5x, 2x, 3x)
â€¢ Partial exit strategy
â€¢ Martingale recovery system
â€¢ Breakeven automation

<b>Strategy:</b>
ðŸŸ¢ <b>CALL (UP)</b> when:
â€¢ Price > EMA200 + Lower Keltner touch
â€¢ RSI < 38 + Bullish rejection
â€¢ SAR flips below

ðŸ”´ <b>PUT (DOWN)</b> when:
â€¢ Price < EMA200 + Upper Keltner touch  
â€¢ RSI > 62 + Bearish rejection
â€¢ SAR flips above

â± <b>Recommended expiry: 5-15 minutes</b>
        """
        await update.message.reply_text(
            welcome_msg, 
            reply_markup=self.get_main_keyboard(),
            parse_mode='HTML'
        )

    async def analyze_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer("Analyzing...")

        try:
            await query.edit_message_text("ðŸ” Scanning market for entry points...")
        except:
            pass

        analysis = await self.strategy.analyze()

        if analysis["signal"] == SignalType.BUY:
            emoji = "ðŸŸ¢"
            signal_text = "<b>ðŸ“ˆ CALL SIGNAL</b>"
            direction = "UP"
        elif analysis["signal"] == SignalType.SELL:
            emoji = "ðŸ”´"
            signal_text = "<b>ðŸ“‰ PUT SIGNAL</b>"
            direction = "DOWN"
        else:
            emoji = "âšª"
            signal_text = "<b>â³ NO SIGNAL</b>"
            direction = "WAIT"

        message = f"""
{emoji} {signal_text}

<b>Asset:</b> {self.config.symbol}
<b>Time:</b> {analysis['timestamp']}
"""

        if analysis["trade"]:
            trade = analysis["trade"]

            message += f"""
<b>ðŸ“ TRADE SETUP:</b>

<b>Entry Price:</b> <code>{trade.entry_price}</code>
<b>Stop Loss:</b> <code>{trade.stop_loss}</code> âŒ
<b>Risk:</b> <code>{trade.risk_amount():.2f}</code> ({trade.risk_percent():.2f}%)

<b>ðŸŽ¯ TAKE PROFIT TARGETS:</b>
â€¢ TP1 (1.5x): <code>{trade.take_profit_1}</code> ðŸ¥‰
â€¢ TP2 (2.0x): <code>{trade.take_profit_2}</code> ðŸ¥ˆ
â€¢ TP3 (3.0x): <code>{trade.take_profit_3}</code> ðŸ¥‡

<b>ðŸ“Š Risk:Reward:</b> 1:{trade.risk_reward}
<b>Position Size:</b> <code>{trade.position_size}</code> units
<b>ATR:</b> <code>{trade.atr_value}</code>
"""

            message += f"""
<b>âœ… Entry Conditions:</b>
"""
            for condition in analysis["conditions_met"]:
                message += f"â€¢ {condition}\n"

            message += f"""
<b>ðŸ’° Martingale Plan:</b>
"""
            stakes = self.martingale.calculate_stakes()
            profits = [self.martingale.calculate_profit(s) for s in stakes]
            for i, (stake, profit) in enumerate(zip(stakes, profits), 1):
                message += f"Level {i}: ${stake} â†’ Profit ${profit}\n"

            message += f"""
<b>ðŸ“ Trade Management:</b>
â€¢ Enter at: <code>{trade.entry_price}</code>
â€¢ Set SL: <code>{trade.stop_loss}</code>
â€¢ TP1 @ 1.5x: Close 30% position
â€¢ TP2 @ 2.0x: Close 30% position  
â€¢ TP3 @ 3.0x: Close remaining 40%
â€¢ Move to breakeven when 1:1 reached
"""
        else:
            message += f"""
<b>Market Conditions:</b>
â€¢ Price: <code>{analysis['price']:.2f}</code>
â€¢ EMA200: <code>{analysis['ema200']:.2f}</code>
â€¢ RSI: <code>{analysis['rsi']:.1f}</code>
â€¢ ATR: <code>{analysis['atr']:.4f}</code>
â€¢ Upper KC: <code>{analysis['upper_kc']:.2f}</code>
â€¢ Lower KC: <code>{analysis['lower_kc']:.2f}</code>

<i>No entry setup. Waiting for conditions...</i>
"""

        try:
            await query.edit_message_text(
                message, 
                reply_markup=self.get_main_keyboard(),
                parse_mode='HTML'
            )
        except Exception as e:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=message,
                reply_markup=self.get_main_keyboard(),
                parse_mode='HTML'
            )

    async def show_trade_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        message = f"""
âš™ï¸ <b>Trade Management Settings</b>

<b>Current Configuration:</b>

<b>Risk:Reward Ratios:</b>
â€¢ Conservative: 1:{self.trade_mgmt.rr_ratio_1}
â€¢ Moderate: 1:{self.trade_mgmt.rr_ratio_2}
â€¢ Aggressive: 1:{self.trade_mgmt.rr_ratio_3}

<b>Stop Loss:</b>
â€¢ Method: ATR x {self.trade_mgmt.sl_atr_multiplier}
â€¢ Based on recent volatility

<b>Partial Exits:</b>
â€¢ 30% at R:R {self.trade_mgmt.rr_ratio_1}
â€¢ 30% at R:R {self.trade_mgmt.rr_ratio_2}
â€¢ 40% at R:R {self.trade_mgmt.rr_ratio_3}

<b>Breakeven:</b>
â€¢ {'Enabled' if self.trade_mgmt.move_to_breakeven_at > 0 else 'Disabled'}
â€¢ Move SL to entry when price hits 1:1

<b>How It Works:</b>
1. Signal fires â†’ Exact entry price given
2. Set stop loss immediately (ATR-based)
3. Take partial profits at each target
4. Trail remaining position
5. Move to breakeven when safe
"""

        await query.edit_message_text(
            message,
            reply_markup=self.get_trade_settings_keyboard(),
            parse_mode='HTML'
        )

    async def show_martingale(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        stakes = self.martingale.calculate_stakes()
        profits = [self.martingale.calculate_profit(s) for s in stakes]

        message = f"""
ðŸ’° <b>Martingale Configuration</b>

<b>Current Settings:</b>
â€¢ Initial Stake: ${self.martingale.initial_stake}
â€¢ Multiplier: {self.martingale.multiplier}x
â€¢ Max Levels: {self.martingale.max_levels}
â€¢ Payout: {self.martingale.profit_percent}%

<b>Stake Levels:</b>
"""
        for i, (stake, profit) in enumerate(zip(stakes, profits), 1):
            total_risk = self.martingale.calculate_total_risk(i-1)
            message += f"Level {i}: ${stake} | Profit: ${profit} | Total Risk: ${total_risk}\n"

        message += f"""
<b>How it works:</b>
1. Start with Level 1 (${stakes[0]})
2. If loss, move to Level 2 (${stakes[1]})
3. If loss again, Level 3 (${stakes[2]})
4. If win at any level, return to Level 1

<b>Risk Warning:</b>
Total exposure at Level 3: ${self.martingale.calculate_total_risk(2)}
Only trade what you can afford to lose!
"""

        await query.edit_message_text(
            message,
            reply_markup=self.get_martingale_keyboard(),
            parse_mode='HTML'
        )

    async def start_monitoring(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        chat_id = update.effective_chat.id
        self.subscribers.add(chat_id)

        if self.monitoring:
            await query.edit_message_text(
                "âœ… Already monitoring! You'll get alerts with entry/SL/TP.",
                reply_markup=self.get_main_keyboard()
            )
            return

        self.monitoring = True
        await query.edit_message_text(
            "ðŸš€ <b>Auto signals activated!</b>\n\nYou'll receive complete trade setups with entry, stop loss and take profit levels.",
            reply_markup=self.get_main_keyboard(),
            parse_mode='HTML'
        )

        self.monitor_task = asyncio.create_task(self.monitor_loop(context))

    async def stop_monitoring(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if not self.monitoring:
            await query.edit_message_text(
                "â„¹ï¸ Not monitoring currently.", 
                reply_markup=self.get_main_keyboard()
            )
            return

        self.monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()

        await query.edit_message_text(
            "â¹ï¸ <b>Auto signals stopped.</b>", 
            reply_markup=self.get_main_keyboard(),
            parse_mode='HTML'
        )

    async def monitor_loop(self, context: ContextTypes.DEFAULT_TYPE):
        last_signal_time = {}

        while self.monitoring:
            try:
                analysis = await self.strategy.analyze()

                if analysis["signal"] != SignalType.NONE and analysis["trade"]:
                    signal_key = f"{analysis['signal'].value}_{self.config.symbol}"
                    current_time = datetime.now()

                    if signal_key in last_signal_time:
                        if (current_time - last_signal_time[signal_key]).seconds < 300:
                            await asyncio.sleep(self.config.check_interval)
                            continue

                    last_signal_time[signal_key] = current_time
                    trade = analysis["trade"]

                    if analysis["signal"] == SignalType.BUY:
                        emoji = "ðŸŸ¢ðŸ“ˆ"
                        title = "<b>CALL SIGNAL - TRADE SETUP!</b>"
                        action = "BUY / CALL"
                    else:
                        emoji = "ðŸ”´ðŸ“‰"
                        title = "<b>PUT SIGNAL - TRADE SETUP!</b>"
                        action = "SELL / PUT"

                    stakes = self.martingale.calculate_stakes()
                    profits = [self.martingale.calculate_profit(s) for s in stakes]

                    alert_message = f"""
{emoji} {title}

<b>Asset:</b> {self.config.symbol}
<b>Action:</b> {action}
<b>Time:</b> {analysis['timestamp']}

<b>ðŸ“ ENTRY POINT:</b>
<b>Enter at:</b> <code>{trade.entry_price}</code>

<b>ðŸ›¡ï¸ STOP LOSS:</b>
<b>SL:</b> <code>{trade.stop_loss}</code>
<b>Risk:</b> <code>{trade.risk_amount():.2f}</code> ({trade.risk_percent():.2f}%)

<b>ðŸŽ¯ TAKE PROFIT TARGETS:</b>
â€¢ TP1 (1.5x): <code>{trade.take_profit_1}</code> ðŸ¥‰ Close 30%
â€¢ TP2 (2.0x): <code>{trade.take_profit_2}</code> ðŸ¥ˆ Close 30%
â€¢ TP3 (3.0x): <code>{trade.take_profit_3}</code> ðŸ¥‡ Close 40%

<b>ðŸ“Š Setup:</b>
"""
                    for condition in analysis["conditions_met"]:
                        alert_message += f"âœ… {condition}\n"

                    alert_message += f"""
<b>ðŸ’° Martingale:</b>
"""
                    for i, (stake, profit) in enumerate(zip(stakes, profits), 1):
                        alert_message += f"Level {i}: ${stake} â†’ ${profit}\n"

                    alert_message += f"""
<b>ðŸ“ Trade Plan:</b>
1. Enter at <code>{trade.entry_price}</code>
2. Set SL at <code>{trade.stop_loss}</code>
3. TP1 @ <code>{trade.take_profit_1}</code> â†’ Close 30%
4. TP2 @ <code>{trade.take_profit_2}</code> â†’ Close 30%
5. TP3 @ <code>{trade.take_profit_3}</code> â†’ Close 40%
6. Move SL to breakeven after 1:1 profit

<b>â± Expiry:</b> 5-15 minutes
"""

                    for chat_id in self.subscribers:
                        try:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=alert_message,
                                parse_mode='HTML'
                            )
                        except Exception as e:
                            logger.error(f"Failed to send to {chat_id}: {e}")

                await asyncio.sleep(self.config.check_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor error: {e}")
                await asyncio.sleep(10)

    async def show_strategy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        strategy_text = """
ðŸ“ˆ <b>Complete Trading Strategy</b>

<b>ðŸŸ¢ CALL Entry (Price Up):</b>
1. Price above EMA200 (uptrend)
2. Price touches Lower Keltner Band
3. RSI below 38 (oversold)
4. Bullish rejection candle (hammer)
5. SAR flips below price

<b>ðŸ”´ PUT Entry (Price Down):</b>
1. Price below EMA200 (downtrend)
2. Price touches Upper Keltner Band
3. RSI above 62 (overbought)
4. Bearish rejection candle (shooting star)
5. SAR flips above price

<b>ðŸ“ Trade Management:</b>
<b>Entry:</b> Exact price when all conditions align
<b>Stop Loss:</b> ATR-based or below/above support/resistance
<b>Take Profits:</b>
  â€¢ TP1 at 1.5x R:R - Close 30% position
  â€¢ TP2 at 2.0x R:R - Close 30% position
  â€¢ TP3 at 3.0x R:R - Close remaining 40%

<b>Breakeven:</b>
â€¢ When price hits 1:1, move SL to entry
â€¢ Protect capital while letting winners run

<b>ðŸ’° Martingale Rules:</b>
â€¢ Level 1: Initial stake
â€¢ Level 2: 2.2x stake (if Level 1 loses)
â€¢ Level 3: 2.2x again (if Level 2 loses)
â€¢ Reset to Level 1 after any win
â€¢ Max 3 levels to limit risk

<b>Risk Management:</b>
â€¢ Risk 1-2% per trade cycle
â€¢ Use exact SL levels - no guessing
â€¢ Take partial profits at each target
â€¢ Never risk more than you can afford
"""
        await query.edit_message_text(
            strategy_text, 
            reply_markup=self.get_main_keyboard(),
            parse_mode='HTML'
        )

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query

        if query.data == 'analyze':
            await self.analyze_command(update, context)
        elif query.data == 'start_monitor':
            await self.start_monitoring(update, context)
        elif query.data == 'stop_monitor':
            await self.stop_monitoring(update, context)
        elif query.data == 'martingale':
            await self.show_martingale(update, context)
        elif query.data == 'strategy':
            await self.show_strategy(update, context)
        elif query.data == 'trade_settings':
            await self.show_trade_settings(update, context)
        elif query.data == 'back_main':
            await query.edit_message_text(
                "Main menu:",
                reply_markup=self.get_main_keyboard()
            )
        elif query.data == 'rr_15':
            self.trade_mgmt.rr_ratio_2 = 1.5
            await query.answer("R:R set to 1.5")
            await self.show_trade_settings(update, context)
        elif query.data == 'rr_20':
            self.trade_mgmt.rr_ratio_2 = 2.0
            await query.answer("R:R set to 2.0")
            await self.show_trade_settings(update, context)
        elif query.data == 'rr_30':
            self.trade_mgmt.rr_ratio_2 = 3.0
            await query.answer("R:R set to 3.0")
            await self.show_trade_settings(update, context)
        elif query.data == 'sl_atr_15':
            self.trade_mgmt.sl_atr_multiplier = 1.5
            await query.answer("SL set to ATR x1.5")
            await self.show_trade_settings(update, context)
        elif query.data == 'sl_atr_20':
            self.trade_mgmt.sl_atr_multiplier = 2.0
            await query.answer("SL set to ATR x2.0")
            await self.show_trade_settings(update, context)
        elif query.data == 'be_on':
            self.trade_mgmt.move_to_breakeven_at = 1.0
            await query.answer("Breakeven enabled")
            await self.show_trade_settings(update, context)
        elif query.data == 'be_off':
            self.trade_mgmt.move_to_breakeven_at = 0.0
            await query.answer("Breakeven disabled")
            await self.show_trade_settings(update, context)

# Web server for Railway health checks
async def health_handler(request):
    return web.Response(text="Bot is running!", status=200)

async def run_web_server():
    app = web.Application()
    app.router.add_get('/', health_handler)
    app.router.add_get('/health', health_handler)
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Health check server on port {port}")

async def main():
    TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not TOKEN:
        logger.error("No TELEGRAM_BOT_TOKEN found!")
        logger.error("Add it in Railway Variables tab")
        return

    # Optional env config
    config = TradingConfig()
    if os.environ.get('SYMBOL'):
        config.symbol = os.environ.get('SYMBOL')
    if os.environ.get('TIMEFRAME'):
        config.timeframe = os.environ.get('TIMEFRAME')

    bot = PocketOptionBot()
    bot.config = config
    bot.strategy = TradingStrategy(config, bot.trade_mgmt)

    application = Application.builder().token(TOKEN).build()
    bot.application = application

    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CallbackQueryHandler(bot.button_handler))

    # Start web server
    web_task = asyncio.create_task(run_web_server())

    # Initialize and start bot
    logger.info("Starting bot...")
    await application.initialize()
    await application.start()

    # Use webhook for Railway
    WEBHOOK_URL = os.environ.get('WEBHOOK_URL')
    if WEBHOOK_URL:
        await application.updater.start_webhook(
            listen='0.0.0.0',
            port=int(os.environ.get('PORT', 8080)),
            webhook_url=WEBHOOK_URL
        )
        logger.info(f"Webhook set: {WEBHOOK_URL}")
    else:
        await application.updater.start_polling(drop_pending_updates=True)
        logger.info("Using polling mode")

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

if __name__ == "__main__":
    asyncio.run(main())