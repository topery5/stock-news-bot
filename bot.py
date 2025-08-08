#!/usr/bin/env python3
# bot.py — Stock News Bot (webhook for Railway) — full version (no TA-Lib)

import os
import json
import time
import threading
import logging
from datetime import datetime
from typing import List, Dict, Any

import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import yfinance as yf
import telebot
from flask import Flask, request

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("8433631932:AAGlzeHsbHeIEsgMB7D0wnD6y9zbrI0eKKo")            # required
APP_URL = os.getenv("http://stock-news-bot-production.up.railway.app")                # required, e.g. https://yourapp.up.railway.app
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SEC", "300"))  # seconds, default 300 (5m)
CHAT_FILE = "chat_ids.json"
LAST_SENT_FILE = "last_sent.json"
LOG_FILE = "bot.log"
DEFAULT_TICKERS = os.getenv("DEFAULT_TICKERS",
                            "BBCA,BBRI,BMRI,TLKM,ASII,UNVR,ICBP,ADRO,ANTM,MDKA").split(",")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36")

if not BOT_TOKEN or not APP_URL:
    raise RuntimeError("Please set BOT_TOKEN and APP_URL environment variables")

# ---------------- Logging ----------------
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)])

# ---------------- Bot & Flask ----------------
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")
app = Flask(__name__)

# ---------------- Persistence helpers ----------------
def load_json(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception as e:
        logging.warning("Failed to load %s: %s", path, e)
    return default

def save_json(path: str, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logging.warning("Failed to save %s: %s", path, e)

def load_chat_ids() -> List[int]:
    return load_json(CHAT_FILE, [])

def save_chat_id(chat_id: int):
    ids = load_chat_ids()
    if chat_id not in ids:
        ids.append(chat_id)
        save_json(CHAT_FILE, ids)
        logging.info("Saved chat id: %s", chat_id)

def remove_chat_id(chat_id: int):
    ids = load_chat_ids()
    ids = [i for i in ids if str(i) != str(chat_id) and i != chat_id]
    save_json(CHAT_FILE, ids)
    logging.info("Removed chat id: %s", chat_id)

def load_last_sent() -> set:
    arr = load_json(LAST_SENT_FILE, [])
    return set(arr)

def update_last_sent(links: List[str]):
    s = load_last_sent()
    s.update(links)
    save_json(LAST_SENT_FILE, list(s))

# ---------------- Technical analysis helpers (pure pandas) ----------------
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ma_up = up.ewm(alpha=1/period, min_periods=period).mean()
    ma_down = down.ewm(alpha=1/period, min_periods=period).mean()
    rs = ma_up / ma_down
    return 100 - (100 / (1 + rs))

def macd(series: pd.Series, fast=12, slow=26, signal=9):
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def analyze_ticker(ticker: str) -> Dict[str, Any] | None:
    sym = ticker.upper()
    if not sym.endswith(".JK"):
        sym = sym + ".JK"
    try:
        # disable auto_adjust to avoid warning; threads False for determinism
        df = yf.download(sym, period="180d", interval="1d", progress=False, threads=False, auto_adjust=False)
    except Exception as e:
        logging.warning("yfinance download error for %s: %s", sym, e)
        return None
    if df is None or df.empty or 'Close' not in df.columns:
        return None
    close = df['Close'].dropna()
    vol = df['Volume'].dropna() if 'Volume' in df.columns else pd.Series(np.zeros(len(close)))
    if len(close) < 30:
        return None

    try:
        ema20 = ema(close, 20).iloc[-1]
        ema50 = ema(close, 50).iloc[-1]
        rsi14 = rsi(close, 14).iloc[-1]
        macd_line, signal_line, hist = macd(close)
        macd_val = macd_line.iloc[-1]
        macd_sig = signal_line.iloc[-1]
    except Exception as e:
        logging.warning("TA calc fail for %s: %s", sym, e)
        return None

    latest = float(close.iloc[-1])
    vol_sma20 = vol.rolling(20).mean().iloc[-1] if len(vol) >= 20 else float(vol.mean() if len(vol)>0 else 1)
    vol_ratio = float(vol.iloc[-1]) / vol_sma20 if vol_sma20 and vol_sma20>0 else 1.0
    recent_high = float(close[-60:].max()) if len(close) >= 60 else float(close.max())
    recent_low = float(close[-60:].min()) if len(close) >= 60 else float(close.min())

    reasons = []
    if ema20 > ema50:
        reasons.append("EMA20>EMA50")
    else:
        reasons.append("EMA20<EMA50")
    reasons.append("MACD bullish" if macd_val > macd_sig else "MACD bearish")
    if rsi14 < 30:
        reasons.append("RSI oversold")
    elif rsi14 > 70:
        reasons.append("RSI overbought")

    signal = "HOLD"
    if ema20 > ema50 and macd_val > macd_sig and rsi14 < 70:
        signal = "BUY"
    elif ema20 < ema50 and macd_val < macd_sig and rsi14 > 65:
        signal = "SELL"

    return {
        "symbol": sym,
        "price": latest,
        "ema20": float(ema20),
        "ema50": float(ema50),
        "rsi14": float(rsi14),
        "macd": float(macd_val),
        "macd_signal": float(macd_sig),
        "vol_ratio": float(vol_ratio),
        "recent_high": recent_high,
        "recent_low": recent_low,
        "signal": signal,
        "reasons": reasons
    }

# ---------------- News sources ----------------
def fetch_idx_announcements(limit=5) -> List[Dict]:
    url = "https://www.idx.co.id/umbraco/Surface/ListedCompany/GetCompanyAnnouncement"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, timeout=10, headers=headers)
        r.raise_for_status()
        data = r.json()
        items = data.get("data", [])[:limit]
        out = []
        for it in items:
            title = it.get("title") or it.get("announcementTitle") or ""
            code = it.get("code") or it.get("issuerCode") or ""
            file_path = it.get("filePath") or ""
            link = f"https://www.idx.co.id{file_path}" if file_path else ""
            out.append({"title": title, "code": code, "link": link, "source": "IDX"})
        return out
    except Exception as e:
        logging.warning("fetch_idx_announcements error: %s", e)
        return []

def fetch_cnbc_headlines(limit=5) -> List[Dict]:
    url = "https://www.cnbcindonesia.com/market"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, timeout=10, headers=headers)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select("a.box_link") or soup.select("div.listing a") or soup.select("article a")
        res = []
        count = 0
        for a in items:
            if count >= limit:
                break
            title = a.get_text(strip=True)
            href = a.get("href")
            if not href:
                continue
            link = href if href.startswith("http") else f"https://www.cnbcindonesia.com{href}"
            res.append({"title": title, "code": "", "link": link, "source": "CNBC"})
            count += 1
        return res
    except Exception as e:
        logging.warning("fetch_cnbc_headlines error: %s", e)
        return []

def fetch_investing_rss(limit=5) -> List[Dict]:
    url = "https://www.investing.com/rss/news_301.rss"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, timeout=10, headers=headers)
        r.raise_for_status()
        import xml.etree.ElementTree as ET
        root = ET.fromstring(r.content)
        items = root.findall(".//item")[:limit]
        out = []
        for item in items:
            title = item.find("title").text if item.find("title") is not None else ""
            link = item.find("link").text if item.find("link") is not None else ""
            out.append({"title": title, "code": "", "link": link, "source": "Investing"})
        return out
    except Exception as e:
        logging.warning("fetch_investing_rss error: %s", e)
        return []


def fetch_combined_news(limit=5) -> List[Dict]:
    # prefer IDX + CNBC; fallback to Investing RSS if needed
    news = []
    news.extend(fetch_idx_announcements(limit=limit))
    news.extend(fetch_cnbc_headlines(limit=limit))
    if not news:
        news.extend(fetch_investing_rss(limit=limit))
    # dedupe by link/title
    seen = set()
    dedup = []
    for n in news:
        link = n.get("link") or n.get("title")
        if link and link not in seen:
            seen.add(link)
            dedup.append(n)
    return dedup[:limit]

# ---------------- Message formatting ----------------
def format_ta_message(ta: Dict[str, Any]) -> str:
    if not ta:
        return "❌ Analisis tidak tersedia."
    s = (f"📊 *{ta['symbol']}*\n"
         f"Harga: Rp {ta['price']:.0f}\n"
         f"Signal: *{ta['signal']}*\n"
         f"EMA20: {ta['ema20']:.0f}  EMA50: {ta['ema50']:.0f}\n"
         f"RSI14: {ta['rsi14']:.1f}  MACD: {ta['macd']:.2f}\n"
         f"Volume: {ta['vol_ratio']:.2f}x SMA20\n"
         f"60d High/Low: {ta['recent_high']:.0f}/{ta['recent_low']:.0f}\n"
         f"Alasan: {', '.join(ta['reasons'])}\n")
    return s

def send_news_item(chat_id: int, item: Dict):
    title = item.get("title", "")
    source = item.get("source", "News")
    link = item.get("link", "")
    code = item.get("code") or ""
    msg = f"📰 *{title}*\n_Source: {source}_\n{link}\n\n"
    if code:
        ta = analyze_ticker(code)
        msg += format_ta_message(ta)
    else:
        # try heuristic ticker detection
        tokens = [t for t in title.split() if t.isupper() and 2 <= len(t) <= 6]
        if tokens:
            ta = analyze_ticker(tokens[0])
            msg += format_ta_message(ta)
        else:
            msg += "_No ticker detected for TA._\n"
    try:
        bot.send_message(chat_id, msg)
        time.sleep(0.8)
    except Exception as e:
        logging.warning("Failed to send message to %s: %s", chat_id, e)

def broadcast_items(items: List[Dict]):
    ids = load_chat_ids()
    if not ids:
        logging.info("No subscribers.")
        return
    for cid in ids:
        for it in items:
            send_news_item(cid, it)

# ---------------- Commands ----------------
@bot.message_handler(commands=["start"])
def handle_start(msg):
    save_chat_id(msg.chat.id)
    bot.reply_to(msg, ("✅ Terdaftar untuk menerima update.\n"
                       "Perintah: /berita /rekomendasi /stop /help"))
    # send one immediate news item
    try:
        items = fetch_combined_news(limit=1)
        if items:
            send_news_item(msg.chat.id, items[0])
    except Exception as e:
        logging.warning("Immediate news send error: %s", e)

@bot.message_handler(commands=["stop"])
def handle_stop(msg):
    remove_chat_id(msg.chat.id)
    bot.reply_to(msg, "✅ Kamu sudah berhenti menerima notifikasi.")

@bot.message_handler(commands=["help"])
def handle_help(msg):
    bot.reply_to(msg, ("/start - daftar\n"
                       "/stop - berhenti\n"
                       "/berita - minta berita sekarang\n"
                       "/rekomendasi - rekomendasi saham harian\n"
                       "/signal <TICKER> - analisa ticker tertentu"))

@bot.message_handler(commands=["berita"])
def handle_berita(msg):
    bot.reply_to(msg, "🔎 Mencari berita terbaru... tunggu sebentar.")
    items = fetch_combined_news(limit=3)
    for it in items:
        send_news_item(msg.chat.id, it)

@bot.message_handler(commands=["rekomendasi"])
def handle_rekomendasi(msg):
    tickers = DEFAULT_TICKERS
    lines = []
    for t in tickers:
        ta = analyze_ticker(t)
        if ta:
            lines.append(f"{t} → *{ta['signal']}* (Rp {ta['price']:.0f})")
        else:
            lines.append(f"{t} → ❌ data tidak tersedia")
    bot.send_message(msg.chat.id, "*Rekomendasi Saham Harian*\n" + "\n".join(lines))

@bot.message_handler(commands=["signal"])
def handle_signal(msg):
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "Gunakan: /signal <TICKER> (contoh: /signal BBCA)")
        return
    t = parts[1].upper()
    ta = analyze_ticker(t)
    if not ta:
        bot.reply_to(msg, f"❌ Data {t} tidak tersedia")
        return
    bot.reply_to(msg, format_ta_message(ta))

# ---------------- Background loop (auto-send) ----------------
def auto_send_loop():
    logging.info("Background auto-send loop started (interval=%s sec)", CHECK_INTERVAL)
    last_sent = load_last_sent()
    while True:
        try:
            items = fetch_combined_news(limit=5)
            # select items with links not in last_sent
            new_items = [it for it in items if it.get("link") and it.get("link") not in last_sent]
            if new_items:
                logging.info("Found %d new item(s)", len(new_items))
                broadcast_items(new_items)
                update_last_sent([it.get("link") for it in new_items if it.get("link")])
                last_sent.update(it.get("link") for it in new_items if it.get("link"))
            else:
                logging.info("No new items at %s", datetime.utcnow().isoformat())
            # write last check time into log file (optional)
        except Exception as e:
            logging.exception("Error in auto_send_loop: %s", e)
        time.sleep(CHECK_INTERVAL)

# ---------------- Webhook endpoints ----------------
@app.route("/" + BOT_TOKEN, methods=["POST"])
def webhook_receive():
    json_str = request.get_data().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route("/", methods=["GET"])
def index():
    return "Stock news bot is running", 200

def set_webhook():
    webhook_url = APP_URL.rstrip("/") + "/" + BOT_TOKEN
    try:
        bot.remove_webhook()
    except Exception:
        pass
    time.sleep(0.5)
    ok = bot.set_webhook(url=webhook_url)
    logging.info("Set webhook to %s -> %s", webhook_url, ok)
    return ok

# ---------------- Main ----------------
if __name__ == "__main__":
    logging.info("Starting app, setting webhook...")
    set_webhook()
    # start background thread
    t = threading.Thread(target=auto_send_loop, daemon=True)
    t.start()
    # run Flask (Railway/Gunicorn will actually run via web: gunicorn bot:app)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
