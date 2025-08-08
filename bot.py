import os
import telebot
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
APP_URL = os.environ.get("APP_URL")  # Contoh: "https://stock-news-bot-production.up.railway.app"

if not BOT_TOKEN or not APP_URL:
    raise RuntimeError("Please set BOT_TOKEN and APP_URL environment variables")

bot = telebot.TeleBot(BOT_TOKEN)
server = Flask(__name__)

# ---- Command example ----
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "Hello! Bot is running 🚀")

# ---- Webhook route ----
@server.route(f"/{BOT_TOKEN}", methods=['POST'])
def webhook():
    json_str = request.get_data().decode('UTF-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

# ---- Index route ----
@server.route("/", methods=['GET'])
def index():
    return "Bot is alive ✅", 200

# ---- Main ----
if __name__ == "__main__":
    # Set webhook (once on startup)
    bot.remove_webhook()
    bot.set_webhook(url=f"{APP_URL}/{BOT_TOKEN}")

    # Baca PORT dari Railway atau default ke 8080
    port = int(os.environ.get("PORT", 8080))
    server.run(host="0.0.0.0", port=port)
