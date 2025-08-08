import os
import time
import requests
import telebot
from bs4 import BeautifulSoup
import pandas as pd
import yfinance as yf

TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

CHAT_IDS_FILE = "chat_ids.txt"

# Simpan chat_id secara otomatis
def save_chat_id(chat_id):
    if not os.path.exists(CHAT_IDS_FILE):
        with open(CHAT_IDS_FILE, "w") as f:
            f.write(str(chat_id) + "\n")
    else:
        with open(CHAT_IDS_FILE, "r") as f:
            ids = f.read().splitlines()
        if str(chat_id) not in ids:
            with open(CHAT_IDS_FILE, "a") as f:
                f.write(str(chat_id) + "\n")

def load_chat_ids():
    if not os.path.exists(CHAT_IDS_FILE):
        return []
    with open(CHAT_IDS_FILE, "r") as f:
        return f.read().splitlines()

# Ambil berita saham (contoh CNBC)
def get_latest_news():
    url = "https://www.cnbcindonesia.com/market"
    resp = requests.get(url)
    soup = BeautifulSoup(resp.text, "html.parser")
    articles = soup.find_all("a", class_="box_link")
    news_list = []
    for art in articles[:3]:
        title = art.get_text(strip=True)
        link = art["href"]
        if not link.startswith("http"):
            link = "https://www.cnbcindonesia.com" + link
        news_list.append({"title": title, "link": link})
    return news_list

# Analisis teknikal sederhana
def technical_analysis(ticker):
    try:
        data = yf.download(ticker+".JK", period="3mo", interval="1d")
        if data.empty:
            return "Data tidak ditemukan"
        close = data["Close"]
        sma5 = close.rolling(5).mean().iloc[-1]
        sma20 = close.rolling(20).mean().iloc[-1]
        last_price = close.iloc[-1]
        if last_price > sma5 > sma20:
            signal = "BUY ✅ (Bullish)"
        elif last_price < sma5 < sma20:
            signal = "SELL ❌ (Bearish)"
        else:
            signal = "WAIT ⏳ (Sideways)"
        return f"Harga terakhir: {last_price:.2f}\nSMA5: {sma5:.2f}\nSMA20: {sma20:.2f}\nSinyal: {signal}"
    except:
        return "Error analisis"

# Kirim pesan ke semua subscriber
def broadcast():
    news = get_latest_news()
    for n in news:
        ticker_guess = n["title"].split()[0]
        ta = technical_analysis(ticker_guess)
        msg = f"📢 *{n['title']}*\n{n['link']}\n\n📊 Analisis:\n{ta}"
        for cid in load_chat_ids():
            bot.send_message(cid, msg, parse_mode="Markdown")

@bot.message_handler(commands=["start"])
def start(message):
    save_chat_id(message.chat.id)
    bot.reply_to(message, "✅ Kamu sudah terdaftar untuk menerima berita saham setiap 5 menit.")

@bot.message_handler(commands=["stop"])
def stop(message):
    if os.path.exists(CHAT_IDS_FILE):
        with open(CHAT_IDS_FILE, "r") as f:
            ids = f.read().splitlines()
        ids = [i for i in ids if i != str(message.chat.id)]
        with open(CHAT_IDS_FILE, "w") as f:
            f.write("\n".join(ids))
    bot.reply_to(message, "❌ Kamu telah berhenti menerima berita.")

if __name__ == "__main__":
    bot.send_message(load_chat_ids()[0] if load_chat_ids() else message.chat.id, "🚀 Bot dimulai!")
    while True:
        try:
            broadcast()
            time.sleep(300)  # 5 menit
        except Exception as e:
            print("Error:", e)
            time.sleep(60)
