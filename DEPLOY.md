# Deploy to Railway - Step by Step Guide

## Step 1: Prepare Your Code

Ensure you have these files ready:
- `bot.py` (main bot file)
- `requirements.txt` (dependencies)
- `Procfile` (entry point)
- `railway.json` (config)

## Step 2: Create Railway Account

1. Go to [railway.app](https://railway.app)
2. Sign up with GitHub (recommended)
3. Verify your email

## Step 3: Create New Project

1. Click "New Project"
2. Select "Deploy from GitHub repo"
3. If this is your first time, install Railway GitHub app
4. Select your repository containing the bot files

## Step 4: Configure Environment Variables

1. In your Railway project, go to "Variables" tab
2. Click "New Variable"
3. Add:
   - `TELEGRAM_BOT_TOKEN` = Your bot token from @BotFather
   - (Optional) `SYMBOL` = BTC/USDT
   - (Optional) `TIMEFRAME` = 5m

## Step 5: Deploy

1. Railway will auto-deploy when you push to GitHub
2. Or click "Deploy" button in Railway dashboard
3. Watch the logs for "Bot is running!"

## Step 6: Verify Deployment

1. Open Telegram
2. Find your bot
3. Send /start
4. You should see the welcome message

## Troubleshooting

**Bot not responding?**
- Check logs in Railway (Deployments tab → View Logs)
- Verify TELEGRAM_BOT_TOKEN is set correctly
- Ensure token has no extra spaces

**Import errors?**
- Check requirements.txt is committed
- Try redeploying

**Bot keeps restarting?**
- Check for errors in logs
- Ensure health check endpoint is working
- Verify PORT environment variable is handled

## Updating Bot

Just push to GitHub - Railway auto-deploys:
```bash
git add .
git commit -m "Update bot"
git push origin main
```

## Free Tier Limits

- Railway free tier: $5 credit/month
- This bot uses ~$1-2/month (very lightweight)
- Sleep after inactivity (wakes on webhook or you can use cron job)

## Alternative: Deploy from CLI

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Link project
railway link

# Deploy
railway up
```
