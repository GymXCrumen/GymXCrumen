#!/usr/bin/env python3
"""
Pocket Option Signals Bot - Production Ready
Deploy on Railway, Render, or any cloud platform
"""

import os
import asyncio
import logging
import json
from datetime import datetime
from typing import Dict, List, Tuple
import pandas as pd
import numpy as np
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import ccxt
from dataclasses import dataclass
from enum import Enum
from aiohttp import web
import threading

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
    timeframe: str = "5m"  # Pocket Option style - shorter timeframes
    ema_period: int = 200
    keltner_period: int = 20
    keltner_multiplier: float = 2.0
    rsi_period: int = 14
    rsi_buy_threshold: int = 38
    rsi_sell_threshold: int = 62
    sar_acceleration: float = 0.02
    sar_maximum: float = 0.2
    check_interval: int = 30  # Check every 30 seconds for binary options

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

class TradingStrategy:
    def __init__(self, config: TradingConfig):
        self.config = config
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

    async def analyze(self) -> Dict:
        df = await self.fetch_ohlcv()
        if df is None or len(df) < self.config.ema_period + 10:
            return {"signal": SignalType.NONE, "reason": "Insufficient data"}

        df['ema200'] = TechnicalAnalysis.calculate_ema(df['close'], self.config.ema_period)
        df['upper_kc'], df['middle_kc'], df['lower_kc'] = TechnicalAnalysis.calculate_keltner_channels(
            df, self.config.keltner_period, self.config.keltner_multiplier
        )
        df['rsi'] = TechnicalAnalysis.calculate_rsi(df['close'], self.config.rsi_period)
        df['sar'], df['sar_trend'] = TechnicalAnalysis.calculate_sar(
            df, self.config.sar_acceleration, self.config.sar_maximum
        )

        current = df.iloc[-1]
        previous = df.iloc[-2]
        price = current['close']

        rejection, rejection_type = TechnicalAnalysis.is_rejection_candle(df)

        analysis = {
            "price": price,
            "ema200": current['ema200'],
            "upper_kc": current['upper_kc'],
            "lower_kc": current['lower_kc'],
            "rsi": current['rsi'],
            "sar": current['sar'],
            "rejection": rejection,
            "rejection_type": rejection_type,
            "signal": SignalType.NONE,
            "conditions_met": [],
            "timestamp": datetime.now().strftime('%H:%M:%S')
        }

        # BUY Strategy for Pocket Option (CALL)
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

        # SELL Strategy for Pocket Option (PUT)
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

        return analysis

class PocketOptionBot:
    def __init__(self):
        self.config = TradingConfig()
        self.strategy = TradingStrategy(self.config)
        self.monitoring = False
        self.monitor_task = None
        self.subscribers = set()  # Multiple users can subscribe
        self.application = None

    def get_main_keyboard(self):
        keyboard = [
            [InlineKeyboardButton("📊 GET SIGNAL", callback_data='analyze')],
            [InlineKeyboardButton("▶️ Auto Signals", callback_data='start_monitor'),
             InlineKeyboardButton("⏹️ Stop", callback_data='stop_monitor')],
            [InlineKeyboardButton("⚙️ Settings", callback_data='settings'),
             InlineKeyboardButton("📈 Strategy", callback_data='strategy')]
        ]
        return InlineKeyboardMarkup(keyboard)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        self.subscribers.add(chat_id)

        welcome_msg = """
🤖 <b>Pocket Option Signals Bot</b>

⚡ <b>Real-time binary options signals</b>

<b>Strategy:</b>
🟢 <b>CALL (BUY)</b> when:
• Price > EMA200 + Lower Keltner touch
• RSI < 38 + Bullish rejection
• SAR flips below

🔴 <b>PUT (SELL)</b> when:
• Price < EMA200 + Upper Keltner touch  
• RSI > 62 + Bearish rejection
• SAR flips above

⏱ <b>Recommended expiry: 5-15 minutes</b>

Click 📊 GET SIGNAL for instant analysis!
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
            await query.edit_message_text("🔍 Scanning market...")
        except:
            pass

        analysis = await self.strategy.analyze()

        if analysis["signal"] == SignalType.BUY:
            emoji = "🟢"
            signal_text = "<b>📈 CALL SIGNAL</b>"
            direction = "UP"
            color = "#00ff00"
        elif analysis["signal"] == SignalType.SELL:
            emoji = "🔴"
            signal_text = "<b>📉 PUT SIGNAL</b>"
            direction = "DOWN"
            color = "#ff0000"
        else:
            emoji = "⚪"
            signal_text = "<b>⏳ NO SIGNAL</b>"
            direction = "WAIT"

        message = f"""
{emoji} {signal_text}

<b>Asset:</b> {self.config.symbol}
<b>Price:</b> <code>{analysis['price']:.2f}</code>
<b>Time:</b> {analysis['timestamp']}

<b>Market Conditions:</b>
• EMA200: <code>{analysis['ema200']:.2f}</code>
• RSI: <code>{analysis['rsi']:.1f}</code>
• Upper KC: <code>{analysis['upper_kc']:.2f}</code>
• Lower KC: <code>{analysis['lower_kc']:.2f}</code>
• SAR: <code>{analysis['sar']:.2f}</code>
"""

        if analysis["conditions_met"]:
            message += "
<b>✅ Conditions Met:</b>
"
            for condition in analysis["conditions_met"]:
                message += f"• {condition}
"
            message += f"
<b>⏱ Suggested Expiry:</b> 5-15 minutes"
        else:
            message += "
<i>No entry conditions met. Waiting for setup...</i>"

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

    async def start_monitoring(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        chat_id = update.effective_chat.id
        self.subscribers.add(chat_id)

        if self.monitoring:
            await query.edit_message_text(
                "✅ Already monitoring! You'll get alerts when signals appear.",
                reply_markup=self.get_main_keyboard()
            )
            return

        self.monitoring = True
        await query.edit_message_text(
            "🚀 <b>Auto signals activated!</b>

You'll receive alerts when CALL or PUT signals are detected.",
            reply_markup=self.get_main_keyboard(),
            parse_mode='HTML'
        )

        self.monitor_task = asyncio.create_task(self.monitor_loop(context))

    async def stop_monitoring(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if not self.monitoring:
            await query.edit_message_text(
                "ℹ️ Not monitoring currently.", 
                reply_markup=self.get_main_keyboard()
            )
            return

        self.monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()

        await query.edit_message_text(
            "⏹️ <b>Auto signals stopped.</b>", 
            reply_markup=self.get_main_keyboard(),
            parse_mode='HTML'
        )

    async def monitor_loop(self, context: ContextTypes.DEFAULT_TYPE):
        last_signal_time = {}

        while self.monitoring:
            try:
                analysis = await self.strategy.analyze()

                if analysis["signal"] != SignalType.NONE:
                    signal_key = f"{analysis['signal'].value}_{self.config.symbol}"
                    current_time = datetime.now()

                    # Avoid spam - only alert once per 5 minutes for same signal
                    if signal_key in last_signal_time:
                        if (current_time - last_signal_time[signal_key]).seconds < 300:
                            await asyncio.sleep(self.config.check_interval)
                            continue

                    last_signal_time[signal_key] = current_time

                    if analysis["signal"] == SignalType.BUY:
                        emoji = "🟢📈"
                        title = "<b>CALL SIGNAL ALERT!</b>"
                        action = "BUY / CALL"
                    else:
                        emoji = "🔴📉"
                        title = "<b>PUT SIGNAL ALERT!</b>"
                        action = "SELL / PUT"

                    alert_message = f"""
{emoji} {title}

<b>Asset:</b> {self.config.symbol}
<b>Action:</b> {action}
<b>Price:</b> <code>{analysis['price']:.2f}</code>
<b>Time:</b> {analysis['timestamp']}

<b>Setup:</b>
"""
                    for condition in analysis["conditions_met"]:
                        alert_message += f"✅ {condition}
"

                    alert_message += "
<b>⏱ Expiry:</b> 5-15 minutes
"
                    alert_message += "<b>🎯 Confidence:</b> High (All conditions met)"

                    # Send to all subscribers
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

    async def show_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        settings_text = f"""
⚙️ <b>Bot Configuration</b>

<b>Asset:</b> <code>{self.config.symbol}</code>
<b>Timeframe:</b> <code>{self.config.timeframe}</code>
<b>Check Interval:</b> <code>{self.config.check_interval}s</code>

<b>Indicators:</b>
• EMA: <code>{self.config.ema_period}</code>
• Keltner: <code>{self.config.keltner_period}</code> (x{self.config.keltner_multiplier})
• RSI: <code>{self.config.rsi_period}</code> (Buy <{self.config.rsi_buy_threshold}, Sell >{self.config.rsi_sell_threshold})
• SAR: <code>{self.config.sar_acceleration}/{self.config.sar_maximum}</code>

<i>To change: Set environment variables or edit config</i>
        """
        await query.edit_message_text(
            settings_text, 
            reply_markup=self.get_main_keyboard(),
            parse_mode='HTML'
        )

    async def show_strategy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        strategy_text = """
📈 <b>Pocket Option Strategy</b>

<b>🟢 CALL Entry (Price Going Up):</b>
1. Price above EMA200 (uptrend)
2. Price touches Lower Keltner Band
3. RSI below 38 (oversold)
4. Bullish rejection candle (hammer)
5. SAR flips below price

<b>🔴 PUT Entry (Price Going Down):</b>
1. Price below EMA200 (downtrend)
2. Price touches Upper Keltner Band
3. RSI above 62 (overbought)
4. Bearish rejection candle (shooting star)
5. SAR flips above price

<b>Money Management:</b>
• Trade only when ALL conditions met
• Expiry: 5-15 minutes (1-3 candles)
• Risk 1-2% per trade
• Avoid news releases

<i>All 5 conditions must be true for signal</i>
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
        elif query.data == 'settings':
            await self.show_settings(update, context)
        elif query.data == 'strategy':
            await self.show_strategy(update, context)

# Health check server for Railway
async def health_check(request):
    return web.Response(text="Bot is running!", status=200)

async def run_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Health check server started on port {port}")

async def main():
    # Get token from environment variable (Railway way)
    TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not TOKEN:
        logger.error("No TELEGRAM_BOT_TOKEN found in environment!")
        logger.error("Set it in Railway Variables tab")
        return

    # Optional: Get config from environment
    config = TradingConfig()
    if os.environ.get('SYMBOL'):
        config.symbol = os.environ.get('SYMBOL')
    if os.environ.get('TIMEFRAME'):
        config.timeframe = os.environ.get('TIMEFRAME')

    bot = PocketOptionBot()
    bot.config = config
    bot.strategy = TradingStrategy(config)

    application = Application.builder().token(TOKEN).build()
    bot.application = application

    # Handlers
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CallbackQueryHandler(bot.button_handler))

    # Start web server for health checks (Railway requirement)
    web_task = asyncio.create_task(run_web_server())

    # Start bot
    logger.info("Starting Pocket Option Signals Bot...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    # Keep running
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
