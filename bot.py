import os
import asyncio
import logging
import requests
import traceback
from flask import Flask, request
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes

# ===================== Logging Setup =====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# Load environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
APP_URL = os.environ.get("APP_URL")
ADMIN_ID = os.environ.get("ADMIN_ID")  # Tambahkan ID admin di Railway variables

# Setup Flask
app = Flask(__name__)

# Setup Telegram bot
application = Application.builder().token(BOT_TOKEN).build()

# ===================== Commands =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("Menjalankan /start untuk user: %s", update.effective_user.id)
    await update.message.reply_text("Halo! Saya bot informasi saham 📈.\nGunakan /help untuk panduan.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("Menjalankan /help")
    await update.message.reply_text(
        "/start - Mulai bot\n"
        "/help - Panduan penggunaan\n"
        "/stock <kode> - Lihat harga saham\n"
        "/news - Lihat berita saham terbaru"
    )

async def stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("Menjalankan /stock args: %s", context.args)
    if not context.args:
        await update.message.reply_text("Gunakan format: /stock <kode>")
        return
    kode = context.args[0].upper()
    await update.message.reply_text(f"Harga saham {kode} saat ini adalah Rp10.000")

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("Menjalankan /news")
    await update.message.reply_text("Berita saham terbaru:\n1. Saham A naik\n2. Saham B turun")

# ===================== Error Handler =====================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Terjadi error: %s", context.error)
    traceback_str = ''.join(traceback.format_exception(None, context.error, context.error.__traceback__))
    logger.error("Traceback:\n%s", traceback_str)

    # Kirim error ke chat admin
    if ADMIN_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_ID),
                text=f"⚠️ <b>Bot Error</b>\n\n<pre>{traceback_str}</pre>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error("Gagal mengirim pesan error ke admin: %s", e)

    # Kirim notifikasi error ke user
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("⚠️ Terjadi error pada bot, sedang diperbaiki.")

# ===================== Add Handlers =====================
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("stock", stock))
application.add_handler(CommandHandler("news", news))
application.add_error_handler(error_handler)

# ===================== Startup Tasks =====================
async def startup_tasks():
    logger.info("Menjalankan startup tasks...")
    await application.bot.set_my_commands([
        BotCommand("start", "Mulai bot"),
        BotCommand("help", "Panduan penggunaan"),
        BotCommand("stock", "Lihat harga saham"),
        BotCommand("news", "Lihat berita saham"),
    ])

    if APP_URL:
        webhook_url = f"{APP_URL}/{BOT_TOKEN}"
        logger.info("Mengatur webhook ke: %s", webhook_url)
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            data={"url": webhook_url}
        )
        logger.info("Set Webhook Status: %s", r.json())
    else:
        logger.warning("APP_URL kosong → Bot berjalan di mode polling.")

# ===================== Flask Routes =====================
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    logger.debug("[WEBHOOK] Update masuk dari Telegram: %s", data)
    update = Update.de_json(data, application.bot)
    application.update_queue.put_nowait(update)
    return "ok"

@app.route("/")
def index():
    logger.info("Halaman index diakses.")
    return "Bot is running."

# ===================== Run =====================
if __name__ == "__main__":
    try:
        asyncio.get_event_loop().run_until_complete(startup_tasks())

        if APP_URL:
            app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
        else:
            application.run_polling()
    except Exception as e:
        logger.exception("Terjadi error saat menjalankan bot: %s", e)
