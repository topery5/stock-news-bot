import telebot
import os
import json
import time
import threading
import requests
import pandas as pd
import yfinance as yf
import talib
from datetime import datetime

# ====== Setup Bot ======
TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

CHAT_ID_FILE = "chat_ids.json"
SAHAM_LIST = ["BBRI", "BBCA", "TLKM", "ASII", "BMRI"]

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
        print(f"[{datetime.now()}] ✅ Chat ID tersimpan: {chat_id}")

# ====== Analisis Teknikal ======
def get_signal(symbol):
    try:
        df = yf.download(f"{symbol}.JK", period="6mo", interval="1d", progress=False, threads=True)
        df = df.dropna()
        close_prices = df['Close'].values  # Pastikan numpy array
        sma20 = talib.SMA(close_prices, timeperiod=20)
        sma50 = talib.SMA(close_prices, timeperiod=50)

        if sma20[-1] > sma50[-1]:
            return "📈 BUY (Uptrend)"
        elif sma20[-1] < sma50[-1]:
            return "📉 SELL (Downtrend)"
        else:
            return "⚖️ NETRAL"
    except Exception as e:
        print(f"[{datetime.now()}] Error get_signal {symbol}: {e}")
        return "❌ Data tidak tersedia"

# ====== Rekomendasi Harian ======
@bot.message_handler(commands=["rekomendasi"])
def rekomendasi(message):
    bot.reply_to(message, "⏳ Mengambil rekomendasi saham harian...")
    hasil = []
    for kode in SAHAM_LIST:
        sinyal = get_signal(kode)
        hasil.append(f"{kode}: {sinyal}")
    bot.send_message(message.chat.id, "\n".join(hasil))

# ====== Command Start ======
@bot.message_handler(commands=["start"])
def start(message):
    save_chat_id(message.chat.id)
    bot.reply_to(message, "✅ Chat ID kamu tersimpan.\nGunakan /rekomendasi untuk melihat rekomendasi saham harian.")

# ====== Fetch Berita (Backup Source) ======
def fetch_news_investing():
    try:
        url = "https://www.investing.com/rss/news_301.rss"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            from xml.etree import ElementTree as ET
            root = ET.fromstring(resp.content)
            items = root.findall(".//item")
            berita = []
            for item in items[:5]:
                title = item.find("title").text
                link = item.find("link").text
                berita.append(f"📰 {title}\n🔗 {link}")
            return berita
    except Exception as e:
        print(f"[{datetime.now()}] Error fetch_news_investing: {e}")
    return []

# ====== Auto Send Berita ======
def auto_send():
    while True:
        news_list = fetch_news_investing()
        if news_list:
            for chat_id in load_chat_ids():
                for news in news_list:
                    bot.send_message(chat_id, news)
        else:
            print(f"[{datetime.now()}] ⏳ Tidak ada berita baru")
        time.sleep(300)  # 5 menit

# ====== Startup ======
chat_ids = load_chat_ids()
if chat_ids:
    bot.send_message(chat_ids[0], "🚀 Bot dimulai!")
else:
    print(f"[{datetime.now()}] Belum ada chat ID tersimpan. Kirim /start di Telegram.")

threading.Thread(target=auto_send, daemon=True).start()

bot.polling(non_stop=True)
