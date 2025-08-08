import os
import asyncio
from flask import Flask, request
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes

# Load environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
APP_URL = os.environ.get("APP_URL")

# Setup Flask
app = Flask(__name__)

# Setup Telegram bot
application = Application.builder().token(BOT_TOKEN).build()

# Commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Halo! Saya bot informasi saham 📈.\nGunakan /help untuk panduan.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - Mulai bot\n"
        "/help - Panduan penggunaan\n"
        "/stock <kode> - Lihat harga saham\n"
        "/news - Lihat berita saham terbaru"
    )

async def stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Gunakan format: /stock <kode>")
        return
    kode = context.args[0].upper()
    # Dummy data
    await update.message.reply_text(f"Harga saham {kode} saat ini adalah Rp10.000")

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Berita saham terbaru:\n1. Saham A naik\n2. Saham B turun")

# Add handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("stock", stock))
application.add_handler(CommandHandler("news", news))

# Set bot commands at startup
async def set_commands():
    await application.bot.set_my_commands([
        BotCommand("start", "Mulai bot"),
        BotCommand("help", "Panduan penggunaan"),
        BotCommand("stock", "Lihat harga saham"),
        BotCommand("news", "Lihat berita saham"),
    ])

# Eksekusi set_commands sekali saat start
asyncio.get_event_loop().run_until_complete(set_commands())

# Flask route untuk webhook
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return "ok"

@app.route("/")
def index():
    return "Bot is running."

# Jalankan polling jika local
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
