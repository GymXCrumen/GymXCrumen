# ðŸ”§ Railway Bot Not Responding - FIX GUIDE

## Problem
Bot deploys successfully but doesn't respond in Telegram.

## Common Causes & Solutions

### 1. âŒ Missing TELEGRAM_BOT_TOKEN

**Check:**
1. Go to Railway dashboard â†’ Your project â†’ Variables tab
2. Look for `TELEGRAM_BOT_TOKEN`
3. If missing, add it!

**How to add:**
```
Key: TELEGRAM_BOT_TOKEN
Value: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz
```

**Get token:**
1. Open Telegram â†’ Search @BotFather
2. Type /mybots
3. Select your bot â†’ API Token

### 2. âŒ Using Polling Instead of Webhook

Railway free tier sleeps after inactivity. Polling doesn't work well.

**Solution - Use Webhook:**

Step 1: Get your Railway domain
- Go to Railway â†’ Your project â†’ Settings
- Find "Domain" (looks like: your-app.up.railway.app)

Step 2: Add WEBHOOK_URL variable
```
Key: WEBHOOK_URL
Value: https://your-app.up.railway.app
```

Step 3: Redeploy
- Railway auto-redeploys when you change variables

### 3. âŒ Bot Not Started in Telegram

**Check:**
1. Open Telegram
2. Find your bot
3. Send `/start`
4. If no response, check Railway logs

### 4. âŒ Check Railway Logs

**How to check:**
1. Railway dashboard â†’ Your project
2. Click "Deploy" tab
3. Click latest deployment
4. Click "View Logs"
5. Look for errors (red text)

**Common errors:**
- `No TELEGRAM_BOT_TOKEN` â†’ Add token
- `Conflict: terminated by other getUpdates` â†’ Another instance running
- `Connection timeout` â†’ Network issue, redeploy

### 5. âŒ Multiple Bot Instances

If you started bot locally AND on Railway, they conflict.

**Fix:**
1. Stop local bot (Ctrl+C)
2. In Railway, click "Redeploy" button
3. Wait 1 minute
4. Try `/start` in Telegram again

## âœ… Quick Diagnostic Steps

1. **Verify Variables:**
   ```
   Railway â†’ Project â†’ Variables
   Should see: TELEGRAM_BOT_TOKEN = ...
   ```

2. **Check Logs:**
   ```
   Railway â†’ Project â†’ Deploy â†’ View Logs
   Look for: "Bot started successfully" or errors
   ```

3. **Test Webhook:**
   ```
   Open browser: https://your-app.up.railway.app/health
   Should see: "Bot is running!"
   ```

4. **Force Restart:**
   ```
   Railway â†’ Project â†’ Settings â†’ Deploy
   Click "Redeploy" button
   ```

## ðŸš€ Recommended Setup

### Option A: Webhook (Best for Railway)

1. Add variables:
   ```
   TELEGRAM_BOT_TOKEN=your_token
   WEBHOOK_URL=https://your-app.up.railway.app
   ```

2. The bot will use webhook mode (always on)

### Option B: Polling (Simpler but sleeps)

1. Add only:
   ```
   TELEGRAM_BOT_TOKEN=your_token
   ```

2. Bot uses polling (may sleep on free tier)

3. To keep alive, use UptimeRobot:
   - Go to uptimerobot.com
   - Add monitor: https://your-app.up.railway.app/health
   - Set interval: 5 minutes

## ðŸ”„ Redeploy After Changes

Every time you change variables:
1. Railway auto-redeploys (wait 30 seconds)
2. Or manually: Deployments â†’ Redeploy

## ðŸ“ž Still Not Working?

1. Check Railway logs for specific errors
2. Verify bot token is correct (no extra spaces)
3. Try creating new bot with @BotFather
4. Check if Telegram is blocked in your region