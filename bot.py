#!/usr/bin/env python3
"""
Pocket Option Signals Bot with Martingale
Production ready for Railway deployment
"""

import os
import asyncio
import logging
import json
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import pandas as pd
import numpy as np
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import ccxt
from dataclasses import dataclass, asdict
from enum import Enum
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
class MartingaleConfig:
    enabled: bool = True
    max_levels: int = 3
    multiplier: float = 2.2
    initial_stake: float = 10.0
    profit_percent: float = 85.0  # Pocket Option payout %

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

        return analysis

class PocketOptionBot:
    def __init__(self):
        self.config = TradingConfig()
        self.martingale = MartingaleConfig()
        self.strategy = TradingStrategy(self.config)
        self.monitoring = False
        self.monitor_task = None
        self.subscribers = set()
        self.application = None
        self.active_signals = {}  # Track signals for martingale

    def get_main_keyboard(self):
        keyboard = [
            [InlineKeyboardButton("ðŸ“Š GET SIGNAL", callback_data='analyze')],
            [InlineKeyboardButton("â–¶ï¸ Auto Signals", callback_data='start_monitor'),
             InlineKeyboardButton("â¹ï¸ Stop", callback_data='stop_monitor')],
            [InlineKeyboardButton("ðŸ’° Martingale", callback_data='martingale'),
             InlineKeyboardButton("ðŸ“ˆ Strategy", callback_data='strategy')]
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
ðŸ¤– <b>Pocket Option Signals Bot</b>

âš¡ <b>Real-time binary options signals with Martingale</b>

<b>Strategy:</b>
ðŸŸ¢ <b>CALL (UP)</b> when:
â€¢ Price > EMA200 + Lower Keltner touch
â€¢ RSI < 38 + Bullish rejection
â€¢ SAR flips below

ðŸ”´ <b>PUT (DOWN)</b> when:
â€¢ Price < EMA200 + Upper Keltner touch  
â€¢ RSI > 62 + Bearish rejection
â€¢ SAR flips above

ðŸ’° <b>Martingale System:</b>
â€¢ Auto-calculated stake levels
â€¢ Risk management built-in
â€¢ Max 3 recovery levels

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
            await query.edit_message_text("ðŸ” Scanning market...")
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
<b>Price:</b> <code>{analysis['price']:.2f}</code>
<b>Time:</b> {analysis['timestamp']}

<b>Market Conditions:</b>
â€¢ EMA200: <code>{analysis['ema200']:.2f}</code>
â€¢ RSI: <code>{analysis['rsi']:.1f}</code>
â€¢ Upper KC: <code>{analysis['upper_kc']:.2f}</code>
â€¢ Lower KC: <code>{analysis['lower_kc']:.2f}</code>
â€¢ SAR: <code>{analysis['sar']:.2f}</code>
"""

        if analysis["conditions_met"]:
            message += "
<b>âœ… Conditions Met:</b>
"
            for condition in analysis["conditions_met"]:
                message += f"â€¢ {condition}
"
            message += f"
<b>â± Suggested Expiry:</b> 5-15 minutes"

            # Add martingale info if signal detected
            if self.martingale.enabled:
                stakes = self.martingale.calculate_stakes()
                profits = [self.martingale.calculate_profit(s) for s in stakes]
                message += "

<b>ðŸ’° Martingale Plan:</b>
"
                for i, (stake, profit) in enumerate(zip(stakes, profits), 1):
                    message += f"Level {i}: Stake ${stake} â†’ Profit ${profit}
"
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
            message += f"Level {i}: ${stake} | Profit: ${profit} | Total Risk: ${total_risk}
"

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
                "âœ… Already monitoring! You'll get alerts when signals appear.",
                reply_markup=self.get_main_keyboard()
            )
            return

        self.monitoring = True
        await query.edit_message_text(
            "ðŸš€ <b>Auto signals activated!</b>

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

                if analysis["signal"] != SignalType.NONE:
                    signal_key = f"{analysis['signal'].value}_{self.config.symbol}"
                    current_time = datetime.now()

                    if signal_key in last_signal_time:
                        if (current_time - last_signal_time[signal_key]).seconds < 300:
                            await asyncio.sleep(self.config.check_interval)
                            continue

                    last_signal_time[signal_key] = current_time

                    if analysis["signal"] == SignalType.BUY:
                        emoji = "ðŸŸ¢ðŸ“ˆ"
                        title = "<b>CALL SIGNAL ALERT!</b>"
                        action = "BUY / CALL"
                    else:
                        emoji = "ðŸ”´ðŸ“‰"
                        title = "<b>PUT SIGNAL ALERT!</b>"
                        action = "SELL / PUT"

                    stakes = self.martingale.calculate_stakes()
                    profits = [self.martingale.calculate_profit(s) for s in stakes]

                    alert_message = f"""
{emoji} {title}

<b>Asset:</b> {self.config.symbol}
<b>Action:</b> {action}
<b>Price:</b> <code>{analysis['price']:.2f}</code>
<b>Time:</b> {analysis['timestamp']}

<b>Setup:</b>
"""
                    for condition in analysis["conditions_met"]:
                        alert_message += f"âœ… {condition}
"

                    alert_message += f"
<b>â± Expiry:</b> 5-15 minutes
"
                    alert_message += "<b>ðŸŽ¯ Confidence:</b> High (All conditions met)

"
                    alert_message += "<b>ðŸ’° Martingale Plan:</b>
"
                    for i, (stake, profit) in enumerate(zip(stakes, profits), 1):
                        alert_message += f"Level {i}: ${stake} â†’ Profit ${profit}
"

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
ðŸ“ˆ <b>Pocket Option Strategy</b>

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

<b>ðŸ’° Martingale Rules:</b>
â€¢ Level 1: Initial stake
â€¢ Level 2: 2.2x stake (if Level 1 loses)
â€¢ Level 3: 2.2x again (if Level 2 loses)
â€¢ Reset to Level 1 after any win
â€¢ Max 3 levels to limit risk

<b>Risk Management:</b>
â€¢ Trade only when ALL conditions met
â€¢ Expiry: 5-15 minutes (1-3 candles)
â€¢ Risk 1-2% per trade cycle
â€¢ Stop after 3 consecutive losses

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
        elif query.data == 'martingale':
            await self.show_martingale(update, context)
        elif query.data == 'strategy':
            await self.show_strategy(update, context)
        elif query.data == 'mg_show':
            await self.show_martingale(update, context)
        elif query.data == 'back_main':
            await query.edit_message_text(
                "Main menu:",
                reply_markup=self.get_main_keyboard()
            )

# Webhook handler for Railway
async def webhook_handler(request):
    return web.Response(text="Bot is running!", status=200)

async def run_web_server():
    app = web.Application()
    app.router.add_get('/', webhook_handler)
    app.router.add_get('/health', webhook_handler)
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
    bot.strategy = TradingStrategy(config)

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

    # Use webhook for Railway (more reliable than polling)
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