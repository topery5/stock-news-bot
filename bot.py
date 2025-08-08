import telebot
import os
import json
import time
import requests
import pandas as pd
import talib
import yfinance as yf
from datetime import datetime
from threading import Thread

# ================== CONFIG ==================
TOKEN = os.getenv("BOT_TOKEN")  # Ambil dari Render ENV
CHAT_FILE = "chat_ids.json"
CHECK_INTERVAL = 300  # 5 menit
LOG_FILE = "bot.log"

bot = telebot.TeleBot(TOKEN)
last_sent_links = set()

# ================== UTIL LOGGING ==================
def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

# ================== CHAT ID STORAGE ==================
def load_chat_ids():
    try:
        if os.path.exists(CHAT_FILE):
            with open(CHAT_FILE, "r") as f:
                return json.load(f)
    except:
        return []
    return []

def save_chat_id(chat_id):
    ids = load_chat_ids()
    if chat_id not in ids:
        ids.append(chat_id)
        with open(CHAT_FILE, "w") as f:
            json.dump(ids, f)
        log(f"✅ Chat ID tersimpan: {chat_id}")

# ================== ANALISIS TEKNIKAL ==================
def get_signal(symbol):
    try:
        df = yf.download(f"{symbol}.JK", period="6mo", interval="1d", progress=False, threads=True)
        if df.empty:
            return "❌ Data tidak tersedia"

        df['EMA20'] = talib.EMA(df['Close'], timeperiod=20)
        df['EMA50'] = talib.EMA(df['Close'], timeperiod=50)
        df['RSI'] = talib.RSI(df['Close'], timeperiod=14)
        macd, signal_line, hist = talib.MACD(df['Close'], fastperiod=12, slowperiod=26, signalperiod=9)

        last = df.iloc[-1]

        trend = "📈 BUY" if last['EMA20'] > last['EMA50'] else "📉 SELL"
        if 30 < last['RSI'] < 70:
            rsi_status = "⚖️ NETRAL"
        elif last['RSI'] <= 30:
            rsi_status = "🟢 OVERSOLD"
        else:
            rsi_status = "🔴 OVERBOUGHT"

        macd_status = "📈 BULLISH" if macd.iloc[-1] > signal_line.iloc[-1] else "📉 BEARISH"

        return f"{trend} | {rsi_status} | {macd_status}"
    except Exception as e:
        log(f"Error get_signal {symbol}: {e}")
        return "❌ Data tidak tersedia"

# ================== FETCH BERITA IDX ==================
def fetch_news_idx(limit=5):
    try:
        url = "https://www.idx.co.id/umbraco/Surface/ListedCompany/GetCompanyAnnouncement"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data["data"][:limit]:
            title = item["title"]
            code = item["code"]
            link = f"https://www.idx.co.id/{item['filePath']}"
            results.append({"title": title, "code": code, "link": link})
        return results
    except Exception as e:
        log(f"Error fetch_news_idx: {e}")
        return []

# ================== FETCH BERITA CNBC ==================
def fetch_news_cnbc(limit=5):
    try:
        url = "https://api.cnbcindonesia.com/market"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        articles = data.get("data", [])
        results = []
        for item in articles[:limit]:
            title = item["title"]
            link = item["url"]
            code = extract_ticker(title)
            results.append({"title": title, "code": code, "link": link})
        return results
    except Exception as e:
        log(f"Error fetch_news_cnbc: {e}")
        return []

# ================== EKSTRAK TICKER DARI TEKS ==================
def extract_ticker(text):
    words = text.split()
    for w in words:
        if w.isupper() and w.isalpha() and 3 <= len(w) <= 4:
            return w
    return None

# ================== GABUNGKAN BERITA ==================
def fetch_combined_news(limit=5):
    idx_news = fetch_news_idx(limit)
    cnbc_news = fetch_news_cnbc(limit)
    return idx_news + cnbc_news

# ================== KIRIM BERITA KE USER ==================
def send_news_to_all(news_list):
    for chat_id in load_chat_ids():
        for item in news_list:
            code = item["code"]
            signal = get_signal(code) if code else "📌 Tidak ada kode saham"
            msg = f"📰 {item['title']} ({code if code else '-'})\n{signal}\n🔗 {item['link']}"
            try:
                bot.send_message(chat_id, msg)
                time.sleep(1)  # rate limit
            except Exception as e:
                log(f"Error kirim ke {chat_id}: {e}")

# ================== LOOP OTOMATIS ==================
def auto_send_loop():
    global last_sent_links
    while True:
        try:
            news_list = fetch_combined_news(limit=5)
            new_items = [n for n in news_list if n["link"] not in last_sent_links]

            if new_items:
                send_news_to_all(new_items)
                last_sent_links.update(n["link"] for n in new_items)
                log(f"📢 Kirim {len(new_items)} berita baru")
            else:
                log("⏳ Tidak ada berita baru")

        except Exception as e:
            log(f"Error auto_send_loop: {e}")

        time.sleep(CHECK_INTERVAL)

# ================== COMMAND TELEGRAM ==================
@bot.message_handler(commands=["start"])
def start(message):
    save_chat_id(message.chat.id)
    bot.reply_to(message, "✅ Chat ID tersimpan. Kamu akan menerima update berita & rekomendasi saham otomatis.")

@bot.message_handler(commands=["berita"])
def berita_manual(message):
    news_list = fetch_combined_news(limit=5)
    send_news_to_all(news_list)

@bot.message_handler(commands=["rekomendasi"])
def rekomendasi_manual(message):
    kode_saham = ["BBRI", "BBCA", "TLKM", "ASII", "BMRI"]
    rekom = []
    for kode in kode_saham:
        rekom.append(f"{kode}: {get_signal(kode)}")
    bot.reply_to(message, "\n".join(rekom))

# ================== MAIN ==================
if __name__ == "__main__":
    if load_chat_ids():
        bot.send_message(load_chat_ids()[0], "🚀 Bot dimulai!")
    else:
        log("Belum ada chat ID tersimpan. Kirim /start di Telegram.")

    Thread(target=auto_send_loop, daemon=True).start()
    bot.polling(non_stop=True)
