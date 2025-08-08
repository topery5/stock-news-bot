#!/usr/bin/env python3
# bot.py — Stock News Bot (webhook) with extra features: /stock, /news, daily summary, price alerts

import os
import json
import time
import threading
import logging
from datetime import datetime, timedelta, time as dtime
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
APP_URL = os.getenv("APP_URL")  # must be https://... (Railway)
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SEC", "300"))  # background news check
DAILY_HOUR_WIB = int(os.getenv("DAILY_HOUR_WIB", "8"))  # hour in WIB for daily summary (0-23)
TIMEZONE_OFFSET = int(os.getenv("TIMEZONE_OFFSET_HOURS", "7"))  # WIB = UTC+7 default
CHAT_FILE = "chat_ids.json"
ALERTS_FILE = "alerts.json"
LAST_SENT_FILE = "last_sent.json"
LOG_FILE = "bot.log"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36")
DEFAULT_TICKERS = os.getenv("DEFAULT_TICKERS", "BBCA,BBRI,BMRI,TLKM,ASII").split(",")

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

def load_alerts() -> Dict[str, List[Dict[str,Any]]]:
    return load_json(ALERTS_FILE, {})

def save_alerts(data: Dict[str, List[Dict[str,Any]]]):
    save_json(ALERTS_FILE, data)

def load_last_sent() -> set:
    return set(load_json(LAST_SENT_FILE, []))

def update_last_sent(links: List[str]):
    s = load_last_sent()
    s.update(links)
    save_json(LAST_SENT_FILE, list(s))

# ---------------- TA helpers (pure pandas) ----------------
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

def analyze_ticker_simple(ticker: str) -> Optional[Dict[str,Any]]:
    t = ticker.upper()
    if not t.endswith(".JK"):
        t = t + ".JK"
    try:
        df = yf.download(t, period="120d", interval="1d", progress=False, threads=False, auto_adjust=False)
    except Exception as e:
        logging.warning("yfinance error for %s: %s", t, e)
        return None
    if df is None or df.empty or 'Close' not in df.columns:
        return None
    close = df['Close'].dropna()
    if len(close) < 20:
        return None
    try:
        ema20 = ema(close, 20).iloc[-1]
        ema50 = ema(close, 50).iloc[-1]
        rsi14 = rsi(close, 14).iloc[-1]
        macd_line, signal_line, _ = macd(close)
        macd_val = macd_line.iloc[-1]
        macd_sig = signal_line.iloc[-1]
    except Exception as e:
        logging.warning("TA calc fail %s: %s", t, e)
        return None
    latest = float(close.iloc[-1])
    return {
        "symbol": t,
        "price": latest,
        "ema20": float(ema20),
        "ema50": float(ema50),
        "rsi14": float(rsi14),
        "macd": float(macd_val),
        "macd_signal": float(macd_sig)
    }

# ---------------- Charts (matplotlib) ----------------
def make_price_chart(ticker: str, period: str = "1mo") -> Optional[BytesIO]:
    sym = ticker.upper()
    if not sym.endswith(".JK"):
        sym = sym + ".JK"
    try:
        df = yf.download(sym, period=period, interval="1d", progress=False, threads=False, auto_adjust=False)
    except Exception as e:
        logging.warning("yfinance chart error %s: %s", sym, e)
        return None
    if df is None or df.empty or 'Close' not in df.columns:
        return None
    plt.close("all")
    fig, ax = plt.subplots(figsize=(6,3.5))
    ax.plot(df.index, df['Close'])
    ax.set_title(f"{ticker} - Close price")
    ax.set_ylabel("Price")
    ax.grid(True)
    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    buf.seek(0)
    return buf

# ---------------- News sources (IDX + CNBC + Investing fallback) ----------------
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
    # filter by sector keyword if provided
    if sector:
        kw = sector.lower()
        news = [n for n in news if kw in (n.get("title","").lower() + " " + n.get("source","").lower())]
    # dedupe
    seen = set()
    dedup = []
    for n in news:
        link = n.get("link") or n.get("title")
        if link and link not in seen:
            seen.add(link)
            dedup.append(n)
    return dedup[:limit]

# ---------------- Messaging helpers ----------------
def format_ta_short(ta: Dict[str,Any]) -> str:
    return (f"*{ta['symbol']}*\nHarga: Rp {ta['price']:.0f}\n"
            f"EMA20: {ta['ema20']:.0f} EMA50: {ta['ema50']:.0f}\n"
            f"RSI14: {ta['rsi14']:.1f}  MACD: {ta['macd']:.2f}\n")

def send_news_item(chat_id: int, item: Dict):
    title = item.get("title","")
    src = item.get("source","News")
    link = item.get("link","")
    code = item.get("code") or ""
    msg = f"📰 *{title}*\n_Source: {src}_\n{link}\n\n"
    bot.send_message(chat_id, msg)
    # if code, send quick TA and chart
    if code:
        ta = analyze_ticker_simple(code)
        if ta:
            bot.send_message(chat_id, format_ta_short(ta))
            buf = make_price_chart(code, period="1mo")
            if buf:
                bot.send_photo(chat_id, buf)
    time.sleep(0.6)

# ---------------- Command handlers ----------------
@bot.message_handler(commands=["start"])
def cmd_start(m):
    save_chat_id(m.chat.id)
    bot.reply_to(m, "✅ Terdaftar. Gunakan /help untuk perintah. Saya akan kirim berita & daily summary otomatis.")

@bot.message_handler(commands=["stop"])
def cmd_stop(m):
    remove_chat_id(m.chat.id)
    bot.reply_to(m, "✅ Berhenti menerima notifikasi.")

@bot.message_handler(commands=["help"])
def cmd_help(m):
    bot.reply_to(m, ("/start /stop /help\n"
                     "/news [sector] - berita terbaru (option: sector keyword)\n"
                     "/stock <TICKER> - price + chart\n"
                     "/signal <TICKER> - TA summary\n"
                     "/rekomendasi - rekomendasi default tickers\n"
                     "/alert <TICKER> <PRICE> - set price alert\n"
                     "/alerts - list your alerts\n"
                     "/unalert <TICKER> - remove alert"))

@bot.message_handler(commands=["stock"])
def cmd_stock(m):
    parts = m.text.split()
    if len(parts) < 2:
        bot.reply_to(m, "Gunakan: /stock <TICKER> (contoh /stock BBCA)")
        return
    ticker = parts[1].upper()
    bot.reply_to(m, f"📊 Mengambil data {ticker}...")
    ta = analyze_ticker_simple(ticker)
    if not ta:
        bot.reply_to(m, f"❌ Data {ticker} tidak tersedia")
        return
    bot.send_message(m.chat.id, format_ta_short(ta))
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
    ta = analyze_ticker_simple(t)
    if not ta:
        bot.reply_to(m, f"❌ Data {t} tidak tersedia")
        return
    bot.reply_to(m, format_ta_short(ta))

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
        ta = analyze_ticker_simple(t.strip())
        if ta:
            # simple rule
            sig = "BUY" if ta['ema20'] > ta['ema50'] and ta['rsi14'] < 70 else "HOLD"
            lines.append(f"{t}: *{sig}* (Rp {ta['price']:.0f})")
        else:
            lines.append(f"{t}: ❌")
    bot.send_message(m.chat.id, "*Rekomendasi Harian*\n" + "\n".join(lines))

# ---------------- Alerts ----------------
@bot.message_handler(commands=["alert"])
def cmd_alert(m):
    parts = m.text.split()
    if len(parts) < 3:
        bot.reply_to(m, "Gunakan: /alert <TICKER> <PRICE>")
        return
    ticker = parts[1].upper()
    try:
        price = float(parts[2])
    except:
        bot.reply_to(m, "Format price salah.")
        return
    alerts = load_alerts()
    uid = str(m.chat.id)
    alerts.setdefault(uid, [])
    alerts[uid].append({"ticker": ticker, "price": price})
    save_alerts(alerts)
    bot.reply_to(m, f"✅ Alert terpasang: {ticker} @ Rp {price:.2f}")

@bot.message_handler(commands=["alerts"])
def cmd_alerts(m):
    alerts = load_alerts()
    uid = str(m.chat.id)
    lst = alerts.get(uid, [])
    if not lst:
        bot.reply_to(m, "Tidak ada alert aktif.")
        return
    lines = [f"{a['ticker']} @ Rp {a['price']:.2f}" for a in lst]
    bot.reply_to(m, "Alerts:\n" + "\n".join(lines))

@bot.message_handler(commands=["unalert"])
def cmd_unalert(m):
    parts = m.text.split()
    if len(parts) < 2:
        bot.reply_to(m, "Gunakan: /unalert <TICKER>")
        return
    ticker = parts[1].upper()
    alerts = load_alerts()
    uid = str(m.chat.id)
    if uid not in alerts:
        bot.reply_to(m, "Tidak ada alert untuk user ini.")
        return
    before = len(alerts[uid])
    alerts[uid] = [a for a in alerts[uid] if a['ticker'] != ticker]
    save_alerts(alerts)
    bot.reply_to(m, f"✅ Alerts updated ({before} -> {len(alerts[uid])})")

# ---------------- Background jobs ----------------
def check_alerts_loop():
    logging.info("Alert-check loop started")
    while True:
        try:
            alerts = load_alerts()
            if not alerts:
                time.sleep(10)
                continue
            # fetch prices for unique tickers
            tickers = set()
            for uid, lst in alerts.items():
                for a in lst:
                    tickers.add(a['ticker'])
            prices = {}
            for tk in tickers:
                ta = analyze_ticker_simple(tk)
                if ta:
                    prices[tk] = ta['price']
            # check
            for uid, lst in alerts.items():
                for a in lst:
                    tk = a['ticker']
                    target = a['price']
                    cur = prices.get(tk)
                    if cur is None:
                        continue
                    # trigger when price >= target
                    if cur >= target:
                        try:
                            bot.send_message(int(uid), f"🔔 Alert: {tk} telah mencapai Rp {cur:.0f} (target {target:.0f})")
                        except Exception as e:
                            logging.warning("Failed send alert to %s: %s", uid, e)
                        # remove that alert
                        alerts[uid] = [x for x in alerts[uid] if not (x['ticker']==tk and x['price']==target)]
            save_alerts(alerts)
        except Exception as e:
            logging.exception("check_alerts_loop error: %s", e)
        time.sleep(60)

def daily_summary_loop():
    logging.info("Daily summary loop started")
    # compute utc scheduled hour from DAILY_HOUR_WIB and TIMEZONE_OFFSET
    target_utc_hour = (DAILY_HOUR_WIB - TIMEZONE_OFFSET) % 24
    while True:
        now = datetime.utcnow()
        if now.hour == target_utc_hour and now.minute == 0:
            try:
                logging.info("Running daily summary...")
                ids = load_chat_ids()
                if ids:
                    # prepare summary for DEFAULT_TICKERS
                    lines = []
                    for t in DEFAULT_TICKERS:
                        ta = analyze_ticker_simple(t.strip())
                        if ta:
                            lines.append(f"{t}: Rp {ta['price']:.0f} ({ta['ema20']:.0f}/{ta['ema50']:.0f})")
                        else:
                            lines.append(f"{t}: -")
                    text = "*Daily Summary*\n" + "\n".join(lines)
                    for cid in ids:
                        try:
                            bot.send_message(cid, text)
                        except Exception as e:
                            logging.warning("Failed send daily summary to %s: %s", cid, e)
                else:
                    logging.info("No subscribers for daily summary")
            except Exception as e:
                logging.exception("daily_summary error: %s", e)
            # sleep 61 seconds to avoid re-running in same minute
            time.sleep(61)
        time.sleep(20)

def news_auto_loop():
    logging.info("News auto loop started (interval %s sec)", CHECK_INTERVAL)
    last_sent = load_last_sent()
    while True:
        try:
            items = fetch_combined_news(limit=5)
            new_items = [it for it in items if it.get("link") and it.get("link") not in last_sent]
            if new_items:
                logging.info("Found %d new news items", len(new_items))
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

# ---------------- Start background threads and app ----------------
if __name__ == "__main__":
    logging.info("Starting app, setting webhook...")
    set_webhook()
    # start background loops
    threading.Thread(target=news_auto_loop, daemon=True).start()
    threading.Thread(target=check_alerts_loop, daemon=True).start()
    threading.Thread(target=daily_summary_loop, daemon=True).start()
    # run flask (Gunicorn will normally run this in production)
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
