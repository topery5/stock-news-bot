#!/usr/bin/env python3
# bot.py — Stock News Bot (news + TA + commands)

import os
import time
import json
import threading
import requests
from datetime import datetime
import pandas as pd
import yfinance as yf
import telebot
from bs4 import BeautifulSoup

# ---------------- CONFIG ----------------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # set this in Railway / env
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SEC", "300"))  # default 300s (5 minutes)
CHAT_FILE = "chat_ids.json"
DEFAULT_TICKERS = os.getenv("DEFAULT_TICKERS",
    "BBCA,BBRI,BMRI,TLKM,ASII,UNVR,ICBP,ADRO,ANTM,MDKA").split(",")
KEYWORDS = [k.strip().lower() for k in os.getenv("KEYWORDS", "backdoor listing,ipo,akuisisi").split(",")]

bot = telebot.TeleBot(TOKEN)

# ------------- Persistence Chat IDs -------------
def load_chat_ids():
    if os.path.exists(CHAT_FILE):
        try:
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

def remove_chat_id(chat_id):
    ids = load_chat_ids()
    if str(chat_id) in [str(x) for x in ids]:
        ids = [x for x in ids if str(x) != str(chat_id)]
        with open(CHAT_FILE, "w") as f:
            json.dump(ids, f)

# ------------- Simple TA helpers (pandas) -------------
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ma_up = up.ewm(alpha=1/period, min_periods=period).mean()
    ma_down = down.ewm(alpha=1/period, min_periods=period).mean()
    rs = ma_up / ma_down
    return 100 - (100 / (1 + rs))

def macd(series, fast=12, slow=26, signal=9):
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

# ------------- Analisis teknikal per ticker -------------
def analyze_ticker(ticker):
    """
    ticker: e.g. 'BBCA' -> uses 'BBCA.JK' on Yahoo Finance
    returns dict or None
    """
    sym = ticker.upper()
    if not sym.endswith(".JK"):
        sym = sym + ".JK"
    try:
        df = yf.download(sym, period="120d", interval="1d", progress=False, threads=False)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    close = df["Close"].dropna()
    vol = df["Volume"].dropna()
    if len(close) < 30:
        return None

    ema20 = ema(close, 20).iloc[-1]
    ema50 = ema(close, 50).iloc[-1]
    rsi14 = rsi(close, 14).iloc[-1]
    macd_line, signal_line, hist = macd(close)
    macd_val = macd_line.iloc[-1]
    signal_val = signal_line.iloc[-1]
    latest_price = close.iloc[-1]

    vol_sma20 = vol.rolling(20).mean().iloc[-1] if len(vol) >= 20 else vol.mean()
    vol_ratio = (vol.iloc[-1] / vol_sma20) if vol_sma20 and vol_sma20 > 0 else 1.0

    # Recent high/low (60 days)
    recent_high = close[-60:].max()
    recent_low = close[-60:].min()

    # Simple rule-based signal
    reasons = []
    signal = "HOLD"
    if ema20 > ema50:
        reasons.append("EMA20>EMA50")
    else:
        reasons.append("EMA20<EMA50")

    if macd_val > signal_val:
        reasons.append("MACD bullish")
    else:
        reasons.append("MACD bearish")

    if rsi14 < 30:
        reasons.append("RSI oversold")
    elif rsi14 > 70:
        reasons.append("RSI overbought")

    if ema20 > ema50 and macd_val > signal_val and rsi14 < 70:
        signal = "BUY"
    elif ema20 < ema50 and macd_val < signal_val and rsi14 > 65:
        signal = "SELL"
    else:
        signal = "HOLD"

    return {
        "symbol": sym,
        "price": float(latest_price),
        "ema20": float(ema20),
        "ema50": float(ema50),
        "rsi14": float(rsi14),
        "macd": float(macd_val),
        "macd_signal": float(signal_val),
        "vol_ratio": float(vol_ratio),
        "recent_high": float(recent_high),
        "recent_low": float(recent_low),
        "signal": signal,
        "reasons": reasons
    }

# ------------- News fetching (multi-source lightweight) -------------
def fetch_idx_announcements(limit=5):
    """Fetch recent company announcements from IDX API (returns list of dict)"""
    try:
        url = "https://www.idx.co.id/umbraco/Surface/ListedCompany/GetCompanyAnnouncement"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])[:limit]
        results = []
        for it in items:
            title = it.get("title") or it.get("announcementTitle") or ""
            code = it.get("code") or it.get("issuerCode") or ""
            file_path = it.get("filePath") or ""
            link = f"https://www.idx.co.id{file_path}" if file_path else ""
            results.append({"title": title, "code": code, "link": link, "source": "IDX"})
        return results
    except Exception:
        return []

def fetch_cnbc_headlines(limit=5):
    try:
        url = "https://www.cnbcindonesia.com/market"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("a.box_link")[:limit]
        results = []
        for a in items:
            title = a.get_text(strip=True)
            href = a.get("href")
            link = href if href.startswith("http") else f"https://www.cnbcindonesia.com{href}"
            results.append({"title": title, "code": "", "link": link, "source": "CNBC"})
        return results
    except Exception:
        return []

def fetch_combined_news(limit=5):
    # combine IDX announcements and CNBC headlines (de-duplicated by link)
    news = fetch_idx_announcements(limit=limit) + fetch_cnbc_headlines(limit=limit)
    seen = set()
    dedup = []
    for n in news:
        link = n.get("link") or n.get("title")
        if link not in seen:
            seen.add(link)
            dedup.append(n)
    return dedup[:limit]

# ------------- Message formatting & send -------------
def format_ta_message(ta):
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

def send_news_to(chat_id, limit=3):
    news = fetch_combined_news(limit=limit)
    if not news:
        bot.send_message(chat_id, "Tidak menemukan berita terbaru saat ini.")
        return
    for item in news:
        title = item.get("title")
        source = item.get("source")
        link = item.get("link")
        # try get a ticker code from IDX item if exists
        code = item.get("code") or ""
        msg = f"📰 *{title}*\n_Source: {source}_\n{link}\n\n"
        if code:
            ta = analyze_ticker(code)
            msg += format_ta_message(ta)
        else:
            # try to guess tickers from title (simple heuristic: uppercase tokens)
            tokens = [t for t in title.split() if t.isupper() and len(t) <= 6]
            if tokens:
                ta = analyze_ticker(tokens[0])
                msg += format_ta_message(ta)
            else:
                msg += "_No ticker detected for TA._\n"
        bot.send_message(chat_id, msg, parse_mode="Markdown")

# ------------- Commands -------------
@bot.message_handler(commands=["start"])
def cmd_start(message):
    save_chat_id(message.chat.id)
    bot.reply_to(message, ("✅ Kamu terdaftar. Bot akan mengirim berita & sinyal otomatis.\n"
                           "Perintah:\n"
                           "/berita - minta berita terbaru sekarang\n"
                           "/rekomendasi - rekomendasi saham harian (TA)\n"
                           "/stop - hentikan notifikasi"))
    # send immediate latest news once
    try:
        send_news_to(message.chat.id, limit=1)
    except Exception:
        pass

@bot.message_handler(commands=["stop"])
def cmd_stop(message):
    remove_chat_id(message.chat.id)
    bot.reply_to(message, "❌ Kamu berhenti menerima notifikasi.")

@bot.message_handler(commands=["berita"])
def cmd_berita(message):
    bot.reply_to(message, "🔎 Mencari berita terbaru... Mohon tunggu sebentar.")
    send_news_to(message.chat.id, limit=3)

@bot.message_handler(commands=["rekomendasi"])
def cmd_rekomendasi(message):
    bot.reply_to(message, "🔍 Sedang memproses rekomendasi saham harian... Mohon tunggu.")
    tickers = DEFAULT_TICKERS
    results = []
    for t in tickers:
        ta = analyze_ticker(t)
        if ta:
            results.append(f"{t} → *{ta['signal']}* (Rp {ta['price']:.0f})")
        else:
            results.append(f"{t} → ❌ tidak tersedia")
    reply = "*Rekomendasi Saham Harian*\n" + "\n".join(results)
    bot.send_message(message.chat.id, reply, parse_mode="Markdown")

# ------------- Auto-send loop -------------
def auto_send_loop():
    while True:
        try:
            ids = load_chat_ids()
            if ids:
                news = fetch_combined_news(limit=3)
                for cid in ids:
                    for item in news:
                        # build message same as send_news_to but for each subscriber
                        title = item.get("title")
                        source = item.get("source")
                        link = item.get("link")
                        code = item.get("code") or ""
                        msg = f"📰 *{title}*\n_Source: {source}_\n{link}\n\n"
                        if code:
                            ta = analyze_ticker(code)
                            msg += format_ta_message(ta)
                        else:
                            tokens = [t for t in title.split() if t.isupper() and len(t) <= 6]
                            if tokens:
                                ta = analyze_ticker(tokens[0])
                                msg += format_ta_message(ta)
                            else:
                                msg += "_No ticker detected for TA._\n"
                        try:
                            bot.send_message(cid, msg, parse_mode="Markdown")
                        except Exception:
                            # ignore per-recipient failures (bot might be blocked)
                            pass
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            print("Error in auto_send_loop:", e)
            time.sleep(60)

# ------------- Start background sender & bot polling -------------
if __name__ == "__main__":
    # start background thread
    t = threading.Thread(target=auto_send_loop, daemon=True)
    t.start()

    print("Bot starting polling... (Ctrl+C to stop)")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
