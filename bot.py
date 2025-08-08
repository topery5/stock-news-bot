import telebot
import os
import json
import time
import requests
from datetime import datetime
import pandas as pd
import talib

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

CHAT_ID_FILE = "chat_ids.json"

# ====== Utility Chat ID ======
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

# ====== Command Start ======
@bot.message_handler(commands=["start"])
def start(message):
    save_chat_id(message.chat.id)
    bot.reply_to(message, "✅ Chat ID kamu tersimpan. Bot akan mengirim update otomatis setiap ada berita atau sinyal.")

# ====== Analisis Teknikal ======
def get_signal(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v7/finance/download/{symbol}.JK?period1=1700000000&period2=9999999999&interval=1d&events=history"
        df = pd.read_csv(url)
        df['SMA20'] = talib.SMA(df['Close'], timeperiod=20)
        df['SMA50'] = talib.SMA(df['Close'], timeperiod=50)
        last = df.iloc[-1]

        if last['SMA20'] > last['SMA50']:
            return "📈 BUY (Uptrend)"
        elif last['SMA20'] < last['SMA50']:
            return "📉 SELL (Downtrend)"
        else:
            return "⚖️ NETRAL"
    except:
        return "❌ Data tidak tersedia"

# ====== Cek Berita ======
def fetch_news():
    try:
        url = "https://www.idx.co.id/umbraco/Surface/ListedCompany/GetCompanyAnnouncement"
        resp = requests.get(url)
        if resp.status_code == 200:
            data = resp.json()
            results = []
            for item in data['data'][:5]:  # ambil 5 berita terbaru
                title = item['title']
                code = item['code']
                link = f"https://www.idx.co.id/{item['filePath']}"
                signal = get_signal(code)
                results.append(f"📰 {title} ({code})\n{signal}\n{link}")
            return results
        return []
    except Exception as e:
        print(f"Error fetch_news: {e}")
        return []

# ====== Kirim Otomatis ======
def auto_send():
    while True:
        news_list = fetch_news()
        if news_list:
            for chat_id in load_chat_ids():
                for news in news_list:
                    bot.send_message(chat_id, news)
        time.sleep(300)  # cek tiap 5 menit

# ====== Startup ======
chat_ids = load_chat_ids()
if chat_ids:
    bot.send_message(chat_ids[0], "🚀 Bot dimulai!")
else:
    print("Belum ada chat ID tersimpan. Kirim /start ke bot di Telegram.")

import threading
threading.Thread(target=auto_send, daemon=True).start()

bot.polling(non_stop=True)
