#!/usr/bin/env python3
# bot.py — Stock News Bot (webhook for Railway) — improved: Yahoo JSON price + IDX header + fallbacks

import os
import json
import time
import threading
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from io import BytesIO

import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import yfinance as yf
import telebot
from flask import Flask, request
import matplotlib.pyplot as plt

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
APP_URL = os.getenv("APP_URL")              # required, https://yourapp.up.railway.app
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SEC", "300"))
CHAT_FILE = "chat_ids.json"
LAST_SENT_FILE = "last_sent.json"
ALERTS_FILE = "alerts.json"
LOG_FILE = "bot.log"
DEFAULT_TICKERS = os.getenv("DEFAULT_TICKERS", "BBCA,BBRI,BMRI,TLKM,ASII").split(",")
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

# ---------------- Persistence ----------------
def load_json(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logging.warning("Failed to load %s: %s", path, e)
    return default

def save_json(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
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
    return set(load_json(LAST_SENT_FILE, []))

def update_last_sent(links: List[str]):
    s = load_last_sent()
    s.update(links)
    save_json(LAST_SENT_FILE, list(s))

def load_alerts() -> Dict[str, List[Dict[str, Any]]]:
    return load_json(ALERTS_FILE, {})

def save_alerts(a):
    save_json(ALERTS_FILE, a)

# ---------------- Price fetching (Yahoo JSON primary, yfinance fallback) ----------------
def fetch_price_yahoo_json(symbol: str) -> Optional[float]:
    """
    Use Yahoo quote endpoint returning JSON. symbol should be like 'BBCA.JK' or 'AAPL'.
    Returns latest price or None on failure.
    """
    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    params = {"symbols": symbol}
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        if "quoteResponse" in data and "result" in data["quoteResponse"] and data["quoteResponse"]["result"]:
            q = data["quoteResponse"]["result"][0]
            # try multiple fields to get most reliable last price
            for key in ("regularMarketPrice", "ask", "bid", "regularMarketPreviousClose"):
                if key in q and q[key] is not None:
                    return float(q[key])
        return None
    except Exception as e:
        logging.warning("Yahoo JSON price fetch failed for %s: %s", symbol, e)
        return None

def fetch_price(symbol: str) -> Optional[float]:
    """Try Yahoo JSON first, fallback to yfinance."""
    sym = symbol.upper()
    if not sym.endswith(".JK"):
        sym = sym
    # use .JK if user passed exchange-less IDX ticker
    if len(sym) <= 5 and sym.isalpha():
        try_sym = sym + ".JK"
    else:
        try_sym = sym
    # 1) Yahoo JSON
    p = fetch_price_yahoo_json(try_sym)
    if p is not None:
        return p
    # 2) If failed, fallback to yfinance (best-effort)
    try:
        df = yf.download(try_sym, period="7d", interval="1d", progress=False, threads=False, auto_adjust=False)
        if df is not None and not df.empty and 'Close' in df.columns:
            return float(df['Close'].dropna().iloc[-1])
    except Exception as e:
        logging.warning("yfinance fallback failed for %s: %s", try_sym, e)
    return None

# ---------------- Technical analysis (pandas-based) ----------------
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

def analyze_ticker(ticker: str) -> Optional[Dict[str, Any]]:
    sym = ticker.upper()
    if not sym.endswith(".JK"):
        sym = sym + ".JK"
    # prefer JSON price + simple TA on last 90 days via yfinance if needed
    try:
        df = yf.download(sym, period="120d", interval="1d", progress=False, threads=False, auto_adjust=False)
    except Exception as e:
        logging.warning("yfinance download error for %s: %s", sym, e)
        df = None
    if df is None or df.empty or 'Close' not in df.columns:
        # minimal: use single price fetch
        p = fetch_price(sym)
        if p is None:
            return None
        return {"symbol": sym, "price": p, "ema20": None, "ema50": None, "rsi14": None, "macd": None, "macd_signal": None, "signal": "PRICE_ONLY", "reasons": []}
    close = df['Close'].dropna()
    if len(close) < 20:
        # return price only
        p = float(close.iloc[-1])
        return {"symbol": sym, "price": p, "ema20": None, "ema50": None, "rsi14": None, "macd": None, "macd_signal": None, "signal": "PRICE_ONLY", "reasons": []}
    try:
        ema20 = float(ema(close, 20).iloc[-1])
        ema50 = float(ema(close, 50).iloc[-1])
        rsi14 = float(rsi(close, 14).iloc[-1])
        macd_line = ema(close, 12) - ema(close, 26)
        macd_sig = macd_line.ewm(span=9, adjust=False).mean().iloc[-1]
        macd_val = float(macd_line.iloc[-1])
    except Exception as e:
        logging.warning("TA calc fail for %s: %s", sym, e)
        return None
    latest = float(close.iloc[-1])
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
    return {"symbol": sym, "price": latest, "ema20": ema20, "ema50": ema50, "rsi14": rsi14, "macd": macd_val, "macd_signal": float(macd_sig), "signal": signal, "reasons": reasons}

# ---------------- Charts ----------------
def make_price_chart(ticker: str, period: str = "1mo") -> Optional[BytesIO]:
    sym = ticker.upper()
    if not sym.endswith(".JK"):
        sym = sym + ".JK"
    try:
        # try JSON price series not available -> use yfinance
        df = yf.download(sym, period=period, interval="1d", progress=False, threads=False, auto_adjust=False)
    except Exception as e:
        logging.warning("Chart yfinance error %s: %s", sym, e)
        return None
    if df is None or df.empty or 'Close' not in df.columns:
        return None
    plt.close("all")
    fig, ax = plt.subplots(figsize=(6,3.5))
    ax.plot(df.index, df['Close'])
    ax.set_title(f"{ticker} - Close")
    ax.set_ylabel("Price")
    ax.grid(True)
    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    buf.seek(0)
    return buf

# ---------------- News sources (IDX with UA header + fallbacks) ----------------
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

def fetch_combined_news(limit=5, sector: Optional[str]=None) -> List[Dict]:
    news = []
    news.extend(fetch_idx_announcements(limit=limit))
    news.extend(fetch_cnbc_headlines(limit=limit))
    if not news:
        news.extend(fetch_investing_rss(limit=limit))
    if sector:
        kw = sector.lower()
        news = [n for n in news if kw in (n.get("title","").lower() + " " + n.get("source","").lower())]
    seen = set()
    dedup = []
    for n in news:
        link = n.get("link") or n.get("title")
        if link and link not in seen:
            seen.add(link)
            dedup.append(n)
    return dedup[:limit]

# ---------------- Message format & send ----------------
def format_ta_message(ta: Dict[str, Any]) -> str:
    if not ta:
        return "❌ Analisis tidak tersedia."
    s = (f"📊 *{ta['symbol']}*\n"
         f"Harga: Rp {ta['price']:.0f}\n"
         f"Signal: *{ta.get('signal','-')}*\n"
         f"EMA20: {ta.get('ema20') or 0:.0f}  EMA50: {ta.get('ema50') or 0:.0f}\n"
         f"RSI14: {ta.get('rsi14') or 0:.1f}  MACD: {ta.get('macd') or 0:.2f}\n")
    return s

def send_news_item(chat_id: int, item: Dict):
    title = item.get("title","")
    source = item.get("source","News")
    link = item.get("link","")
    code = item.get("code") or ""
    msg = f"📰 *{title}*\n_Source: {source}_\n{link}\n\n"
    try:
        bot.send_message(chat_id, msg)
    except Exception as e:
        logging.warning("Failed send news text to %s: %s", chat_id, e)
    if code:
        ta = analyze_ticker(code)
        try:
            if ta:
                bot.send_message(chat_id, format_ta_message(ta))
                buf = make_price_chart(code, period="1mo")
                if buf:
                    bot.send_photo(chat_id, buf)
        except Exception as e:
            logging.warning("Failed send TA/chart to %s: %s", chat_id, e)
    time.sleep(0.6)

# ---------------- Commands (same as previous) ----------------
@bot.message_handler(commands=["start"])
def handle_start(msg):
    save_chat_id(msg.chat.id)
    bot.reply_to(msg, "✅ Terdaftar. Gunakan /help untuk perintah.")
    try:
        items = fetch_combined_news(limit=1)
        if items:
            send_news_item(msg.chat.id, items[0])
    except Exception as e:
        logging.warning("Immediate news send error: %s", e)

@bot.message_handler(commands=["help"])
def handle_help(msg):
    bot.reply_to(msg, ("/start /stop /help\n"
                       "/news [sector]\n"
                       "/stock <TICKER>\n"
                       "/signal <TICKER>\n"
                       "/rekomendasi"))

@bot.message_handler(commands=["stock"])
def cmd_stock(m):
    parts = m.text.split()
    if len(parts) < 2:
        bot.reply_to(m, "Gunakan: /stock <TICKER>")
        return
    ticker = parts[1].upper()
    bot.reply_to(m, f"📊 Mengambil data {ticker}...")
    p = fetch_price(ticker)
    if p is None:
        bot.reply_to(m, f"❌ Data {ticker} tidak tersedia")
        return
    ta = analyze_ticker(ticker) or {"symbol": ticker, "price": p, "signal": "PRICE_ONLY"}
    bot.send_message(m.chat.id, format_ta_message(ta))
    buf = make_price_chart(ticker, period="1mo")
    if buf:
        bot.send_photo(m.chat.id, buf)

@bot.message_handler(commands=["signal"])
def cmd_signal(m):
    parts = m.text.split()
    if len(parts) < 2:
        bot.reply_to(m, "Gunakan: /signal <TICKER>")
        return
    t = parts[1].upper()
    ta = analyze_ticker(t)
    if not ta:
        bot.reply_to(m, f"❌ Data {t} tidak tersedia")
        return
    bot.reply_to(m, format_ta_message(ta))

@bot.message_handler(commands=["news"])
def cmd_news(m):
    parts = m.text.split(maxsplit=1)
    sector = parts[1] if len(parts) > 1 else None
    bot.reply_to(m, "🔎 Mencari berita...")
    items = fetch_combined_news(limit=5, sector=sector)
    if not items:
        bot.reply_to(m, "Tidak menemukan berita.")
        return
    for it in items:
        send_news_item(m.chat.id, it)

@bot.message_handler(commands=["rekomendasi"])
def cmd_rekomendasi(m):
    bot.reply_to(m, "🔍 Menganalisis rekomendasi...")
    lines = []
    for t in DEFAULT_TICKERS:
        ta = analyze_ticker(t.strip())
        if ta:
            sig = "BUY" if ta.get('ema20') and ta.get('ema50') and ta['ema20'] > ta['ema50'] else "HOLD"
            lines.append(f"{t}: *{sig}* (Rp {ta['price']:.0f})")
        else:
            lines.append(f"{t}: ❌")
    bot.send_message(m.chat.id, "*Rekomendasi Harian*\n" + "\n".join(lines))

# ---------------- Background news loop (uses fetch_combined_news) ----------------
def news_auto_loop():
    logging.info("News auto loop started (interval=%s sec)", CHECK_INTERVAL)
    last_sent = load_last_sent()
    while True:
        try:
            items = fetch_combined_news(limit=5)
            new_items = [it for it in items if it.get("link") and it.get("link") not in last_sent]
            if new_items:
                logging.info("Found %d new items", len(new_items))
                ids = load_chat_ids()
                for cid in ids:
                    for it in new_items:
                        send_news_item(cid, it)
                update_last_sent([it.get("link") for it in new_items if it.get("link")])
                last_sent.update(it.get("link") for it in new_items if it.get("link"))
            else:
                logging.info("No new items this cycle")
        except Exception as e:
            logging.exception("news_auto_loop error: %s", e)
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
    threading.Thread(target=news_auto_loop, daemon=True).start()
    # run flask (Railway/Gunicorn will normally run this)
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
