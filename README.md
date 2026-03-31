# Pocket Option Signals Bot 🤖

Telegram bot for binary options trading signals using technical analysis.

## Features

- **Real-time signals** for CALL/PUT options
- **5 technical indicators** combined for high accuracy
- **Auto-monitoring** mode for instant alerts
- **Railway-ready** deployment
- **Multi-user support** - share with friends

## Strategy

**CALL Signal (Price Up):**
- Price > EMA200 (uptrend)
- Touches Lower Keltner Band (oversold)
- RSI < 38 (oversold)
- Bullish rejection candle
- SAR flips below price

**PUT Signal (Price Down):**
- Price < EMA200 (downtrend)
- Touches Upper Keltner Band (overbought)
- RSI > 62 (overbought)
- Bearish rejection candle
- SAR flips above price

## Quick Start (Local)

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=your_token_here
python bot.py
```

## Deploy to Railway

See [DEPLOY.md](DEPLOY.md) for detailed instructions.

### One-Click Deploy

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template/your-template-url)

## Usage

1. Start bot: `/start`
2. Get instant signal: Click "📊 GET SIGNAL"
3. Enable auto-alerts: Click "▶️ Auto Signals"
4. View strategy: Click "📈 Strategy"

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | - | From @BotFather |
| `SYMBOL` | No | BTC/USDT | Trading pair |
| `TIMEFRAME` | No | 5m | Chart timeframe |

## Disclaimer

⚠️ **Trading Risk Warning**: This bot is for educational purposes only. Binary options trading carries high risk. Never trade with money you cannot afford to lose. Past performance does not guarantee future results.

## License

MIT License - Use at your own risk.
