import os
import logging
from flask import Flask, request
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import yfinance as yf
import requests

# Logging biar gampang debug
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
APP_URL = os.getenv("APP_URL")

# Telegram Application
application = ApplicationBuilder().token(BOT_TOKEN).build()

# =========================
# Command Handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Halo! Saya adalah Stock News Bot 📈\n\n"
        "Gunakan /stock <ticker> untuk cek harga saham\n"
        "Gunakan /news <ticker> untuk lihat berita terbaru\n"
        "Gunakan /help untuk panduan"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 Panduan:\n"
        "/start - Mulai bot\n"
        "/help - Panduan penggunaan\n"
        "/stock <ticker> - Lihat harga saham\n"
        "/news <ticker> - Lihat berita saham terbaru"
    )

async def stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: /stock AAPL")
        return
    ticker = context.args[0].upper()
    try:
        data = yf.Ticker(ticker).history(period="1d")
        if data.empty:
            await update.message.reply_text("Ticker tidak ditemukan.")
            return
        price = round(data["Close"].iloc[-1], 2)
        await update.message.reply_text(f"💹 Harga {ticker}: ${price}")
    except Exception as e:
        await update.message.reply_text(f"Terjadi kesalahan: {e}")

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: /news AAPL")
        return
    ticker = context.args[0].upper()
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={ticker}"
        r = requests.get(url, timeout=5)
        data = r.json()
        if "news" not in data:
            await update.message.reply_text("Tidak ada berita ditemukan.")
            return
        messages = []
        for item in data["news"][:5]:
            messages.append(f"📰 {item['title']}\n🔗 {item['link']}")
        await update.message.reply_text("\n\n".join(messages))
    except Exception as e:
        await update.message.reply_text(f"Terjadi kesalahan: {e}")

# Daftarin handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_cmd))
application.add_handler(CommandHandler("stock", stock))
application.add_handler(CommandHandler("news", news))

# Set perintah biar muncul di menu Telegram
async def set_commands():
    await application.bot.set_my_commands([
        BotCommand("start", "Mulai bot"),
        BotCommand("help", "Panduan penggunaan"),
        BotCommand("stock", "Lihat harga saham"),
        BotCommand("news", "Lihat berita saham"),
    ])

# Flask app untuk webhook
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return "ok"

# Jalankan set_commands pas start
@app.before_first_request
def before_first_request():
    application.create_task(set_commands())

if __name__ == "__main__":
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        url_path=BOT_TOKEN,
        webhook_url=f"{APP_URL}/{BOT_TOKEN}"
    )
