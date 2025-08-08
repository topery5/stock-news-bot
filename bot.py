from telegram import Update, BotCommand
from telegram.ext import CommandHandler, ContextTypes

# Handler untuk /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Halo! Bot sudah aktif dan siap membantu.")

# Handler untuk /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📜 Daftar perintah:\n"
        "/start - Mulai bot\n"
        "/help - Bantuan\n"
        "/harga - Cek harga saham\n"
        "/news - Cek berita saham\n"
    )

# Daftar command menu Telegram
async def set_commands(application):
    await application.bot.set_my_commands([
        BotCommand("start", "Mulai bot"),
        BotCommand("help", "Lihat bantuan"),
        BotCommand("harga", "Cek harga saham"),
        BotCommand("news", "Cek berita saham"),
    ])

# Pendaftaran handler
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))

# Set command menu saat bot start
application.post_init = set_commands
