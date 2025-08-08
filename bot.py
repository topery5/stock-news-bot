import os
import json
import time
import threading
import requests
import pandas as pd
import talib
import yfinance as yf
from datetime import datetime
from flask import Flask, request
import telebot

# ===== CONFIG =====
TOKEN = os.getenv("BOT_TOKEN")
APP_URL = os.getenv("https://stock-news-bot-production.up.railway.app/")  # misal: https://mybot.up.railway.app
PORT = int(os.getenv("PORT", 5000))

bot = telebot.TeleBot(TOKEN)
CHAT_ID_FILE = "chat_ids.json"

# ===== CHAT ID STORAGE =====
def load_chat_ids():
    if os.path.exists(CHAT_ID_FILE):
        with open(CHAT_ID_FILE, "r") as f:
            return json.load(f)
    return []

def save_chat_id(chat_id):
    ids = load_chat_ids()
    if chat_id not in ids:
        ids.append(chat_id)
        with open(CHAT_ID_FILE, "w") as f:
            json.dump(ids, f)
        print(f"[{datetime.now()}] ✅ Chat ID tersimpan: {chat_id}")

# ===== SIGNAL =====
def get_signal(symbol):
    try:
        df = yf.download(f"{symbol}.JK", period="6mo", interval="1d", progress=False, threads=True)
        if df.empty:
            return "❌ Data tidak tersedia"
        df["SMA20"] = talib.SMA(df["Close"].values, timeperiod=20)
        df["SMA50"] = talib.SMA(df["Close"].values, timeperiod=50)
        last = df.iloc[-1]
        if last["SMA20"] > last["SMA50"]:
            return "📈 BUY (Uptrend)"
        elif last["SMA20"] < last["SMA50"]:
            return "📉 SELL (Downtrend)"
        else:
            return "⚖️ NETRAL"
    except Exception as e:
        print(f"[{datetime.now()}] Error get_signal {symbol}: {e}")
        return "❌ Error analisis"

# ===== FETCH NEWS (IDX) =====
def fetch_news_idx():
    try:
        url = "https://www.idx.co.id/umbraco/Surface/ListedCompany/GetCompanyAnnouncement"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data["data"][:5]:
            title = item["title"]
            code = item["code"]
            link = f"https://www.idx.co.id/{item['filePath']}"
            signal = get_signal(code)
            results.append(f"📰 {title} ({code})\n{signal}\n{link}")
        return results
    except Exception as e:
        print(f"[{datetime.now()}] Error fetch_news_idx: {e}")
        return []

# ===== AUTO SEND BERITA =====
def auto_send():
    while True:
        news_list = fetch_news_idx()
        if news_list:
            for chat_id in load_chat_ids():
                for news in news_list:
                    bot.send_message(chat_id, news)
        else:
            print(f"[{datetime.now()}] ⏳ Tidak ada berita baru")
        time.sleep(300)

# ===== COMMAND START =====
@bot.message_handler(commands=["start"])
def start_cmd(message):
    save_chat_id(message.chat.id)
    bot.reply_to(message, "✅ Chat ID kamu tersimpan. Bot akan kirim update otomatis + rekomendasi saham harian.")

# ===== COMMAND REKOMENDASI =====
@bot.message_handler(commands=["rekomendasi"])
def rekomendasi_cmd(message):
    saham_list = ["BBRI", "BBCA", "TLKM", "ASII", "BMRI"]
    reply = "📊 *Rekomendasi Saham Hari Ini:*\n\n"
    for s in saham_list:
        signal = get_signal(s)
        reply += f"{s}: {signal}\n"
    bot.reply_to(message, reply, parse_mode="Markdown")

# ===== WEBHOOK SETUP =====
app = Flask(__name__)

@app.route(f"/{TOKEN}", methods=["POST"])
def receive_update():
    json_str = request.get_data().decode("UTF-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route("/", methods=["GET"])
def index():
    return "Bot is running!", 200

def set_webhook():
    bot.remove_webhook()
    time.sleep(1)
    bot.set_webhook(url=f"{APP_URL}/{TOKEN}")

# ===== START THREAD AUTO SEND =====
threading.Thread(target=auto_send, daemon=True).start()

if __name__ == "__main__":
    set_webhook()
    app.run(host="0.0.0.0", port=PORT)
