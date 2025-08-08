import os
import logging
import requests
import yfinance as yf
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
APP_URL = os.getenv("APP_URL")

app = Flask(__name__)

# === Perbaikan fetch_idx_announcements (403 fix) ===
def fetch_idx_announcements():
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/115.0.0.0 Safari/537.36"
        }
        url = "https://www.idx.co.id/umbraco/Surface/ListedCompany/GetCompanyAnnouncement"
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logging.warning(f"fetch_idx_announcements error: {e}")
        return []

# === Perbaikan get_stock_price (401 fix + fallback) ===
def get_stock_price(symbol):
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if "regularMarketPrice" in info:
            return info["regularMarketPrice"]
        raise Exception("Yahoo Finance data not available")
    except Exception as e:
        logging.error(f"Yahoo Finance failed for {symbol}, trying fallback. Reason: {e}")
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d"
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            return data["chart"]["result"][0]["meta"]["regularMarketPrice"]
        except Exception as e2:
            logging.error(f"Fallback price fetch failed for {symbol}: {e2}")
            return None

# === Command untuk ambil harga saham ===
async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: /price BBCA.JK")
        return
    symbol = context.args[0].upper()
    price = get_stock_price(symbol)
    if price:
        await update.message.reply_text(f"Harga {symbol} sekarang: {price}")
    else:
        await update.message.reply_text(f"Gagal ambil harga {symbol}")

# === Command untuk ambil pengumuman IDX ===
async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    announcements = fetch_idx_announcements()
    if announcements:
        messages = []
        for a in announcements[:5]:
            title = a.get("Title", "-")
            date = a.get("Date", "-")
            link = a.get("Url", "#")
            messages.append(f"{date} - {title}\n{link}")
        await update.message.reply_text("\n\n".join(messages))
    else:
        await update.message.reply_text("Tidak ada pengumuman terbaru.")

# === Telegram Bot setup ===
application = ApplicationBuilder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("price", price))
application.add_handler(CommandHandler("news", news))

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return "ok"

@app.route("/")
def index():
    return "Bot is running"

if __name__ == "__main__":
    application.run_polling()
