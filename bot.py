#!/usr/bin/env python3
# bot.py — Stock News Bot (Webhook) with /harga (text) and /stock (image card)

import os
import logging
import requests
from io import BytesIO
from datetime import datetime
from typing import List, Tuple, Optional

from flask import Flask, request
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

import yfinance as yf
from PIL import Image, ImageDraw, ImageFont

# ---------------- config ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
APP_URL = os.getenv("APP_URL")  # must be https://your-app.up.railway.app
PORT = int(os.environ.get("PORT", 8080))

if not BOT_TOKEN or not APP_URL:
    raise RuntimeError("Please set BOT_TOKEN and APP_URL environment variables")

WEBHOOK_PATH = "/" + BOT_TOKEN
WEBHOOK_URL = APP_URL.rstrip("/") + WEBHOOK_PATH

# ---------------- logging ----------------
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ---------------- Flask + Telegram app ----------------
app = Flask(__name__)
application = ApplicationBuilder().token(BOT_TOKEN).build()

# set Telegram command menu (will be called at startup via HTTP set webhook step)
import asyncio

async def set_bot_commands():
    try:
        await application.bot.set_my_commands([
            BotCommand("start", "Mulai bot"),
            BotCommand("help", "Bantuan"),
            BotCommand("harga", "Cek harga saham (multi)"),
            BotCommand("stock", "Kartu saham (gambar)"),
        ])
    except Exception:
        # may fail at import-time on some platforms; fine, it's optional
        pass

# Run the async function to set commands at startup
asyncio.run(set_bot_commands())

# ---------------- utilities: fetch price & TA ----------------
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"

def to_jk(ticker: str) -> str:
    t = ticker.strip().upper()
    if t.endswith(".JK"):
        return t
    if t.isalpha() and len(t) <= 6:
        return t + ".JK"
    return t

def fetch_price_yahoo_json(symbol: str) -> Optional[float]:
    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    try:
        r = requests.get(url, params={"symbols": symbol}, headers={"User-Agent": USER_AGENT}, timeout=8)
        r.raise_for_status()
        data = r.json()
        res = data.get("quoteResponse", {}).get("result", [])
        if res:
            q = res[0]
            for key in ("regularMarketPrice", "ask", "bid", "regularMarketPreviousClose"):
                if q.get(key) is not None:
                    return float(q.get(key))
    except Exception as e:
        log.debug("Yahoo JSON failed for %s: %s", symbol, e)
    return None

def fetch_price(symbol: str) -> Optional[float]:
    sym = to_jk(symbol)
    # 1) try yfinance Ticker.history
    try:
        t = yf.Ticker(sym)
        df = t.history(period="2d", interval="1d", auto_adjust=False, proxy=None)
        if df is not None and not df.empty and "Close" in df.columns:
            return float(df["Close"].dropna().iloc[-1])
    except Exception as e:
        log.debug("yfinance failed for %s: %s", sym, e)

    # 2) try Yahoo JSON
    p = fetch_price_yahoo_json(sym)
    if p is not None:
        return p
    # 3) try without .JK if user supplied full symbol
    if sym.endswith(".JK"):
        p2 = fetch_price_yahoo_json(sym[:-3])
        if p2 is not None:
            return p2
    return None

def fetch_price_and_change(symbol: str) -> Optional[Tuple[float, float]]:
    """
    Return (last_price, percent_change) comparing last vs previous close.
    """
    sym = to_jk(symbol)
    try:
        t = yf.Ticker(sym)
        df = t.history(period="3d", interval="1d", auto_adjust=False)
        if df is not None and len(df.dropna()) >= 2:
            close = df["Close"].dropna()
            last = float(close.iloc[-1])
            prev = float(close.iloc[-2])
            pct = ((last - prev) / prev) * 100 if prev != 0 else 0.0
            return last, pct
        # fallback: use quote endpoint
        p = fetch_price_yahoo_json(sym)
        if p is not None:
            # can't compute pct reliably -> return 0
            return p, 0.0
    except Exception as e:
        log.debug("fetch_price_and_change fail %s: %s", sym, e)
    return None

# ---------------- Image card generator (Pillow) ----------------
def load_font(size: int, bold: bool = False):
    # try DejaVuSans which is commonly available on Linux containers
    font_names = ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
                  "Arial.ttf", "arial.ttf"]
    for name in font_names:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()

def create_stock_card(symbol: str, name: str, price: float, change_pct: float, last_update: str) -> BytesIO:
    width, height = 700, 380
    # create gradient background (blue -> purple)
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    top_color = (12, 92, 196)   # deep blue
    bottom_color = (116, 50, 169)  # purple
    for y in range(height):
        ratio = y / (height - 1)
        r = int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio)
        g = int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio)
        b = int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # paddings
    pad_x = 28
    # fonts
    font_logo = load_font(18, bold=True)
    font_symbol = load_font(36, bold=True)
    font_price = load_font(56, bold=True)
    font_change = load_font(28, bold=True)
    font_small = load_font(18, bold=False)

    # header logo
    draw.text((pad_x, 16), "MarketBot", font=font_logo, fill=(255, 255, 255, 255))

    # symbol + name
    draw.text((pad_x, 60), f"{symbol}", font=font_symbol, fill=(255, 255, 255))
    # name smaller to the right if provided
    if name:
        try:
            draw.text((pad_x + 260, 74), f"{name}", font=font_small, fill=(230, 230, 230))
        except Exception:
            pass

    # price
    price_text = f"Rp {price:,.0f}"
    draw.text((pad_x, 140), price_text, font=font_price, fill=(255, 255, 255))

    # change + percent with colored background pill
    change_text = f"{change_pct:+.2f}%"
    if change_pct > 0:
        change_color = (0, 200, 83)   # green
        arrow = "▲"
    elif change_pct < 0:
        change_color = (255, 82, 82)  # red
        arrow = "▼"
    else:
        change_color = (200, 200, 200)
        arrow = "➖"

    # draw small pill rectangle behind change_text
    w, h = draw.textsize(change_text, font=font_change)
    rect_x = pad_x
    rect_y = 220
    rect_pad_x = 14
    rect_pad_y = 8
    draw.rounded_rectangle([rect_x - rect_pad_x, rect_y - rect_pad_y,
                            rect_x + w + rect_pad_x, rect_y + h + rect_pad_y],
                           radius=10, fill=(255, 255, 255, 90))
    # write arrow + change
    draw.text((rect_x + 6, rect_y), f"{arrow} {change_text}", font=font_change, fill=change_color)

    # footer timestamp
    draw.text((pad_x, height - 34), f"Updated: {last_update}", font=font_small, fill=(230, 230, 230))

    # return BytesIO
    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.name = "stock.png"
    bio.seek(0)
    return bio

# ---------------- Handlers ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Halo! Bot saham aktif.\nKetik /help untuk perintah.")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - mulai\n"
        "/help - bantuan\n"
        "/harga <TICKER1> [TICKER2 ...] - cek harga (contoh: /harga BBCA.JK TLKM.JK)\n"
        "/stock <TICKER> - kartu saham bergaya (contoh: /stock BBCA.JK)\n"
    )

async def cmd_harga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Gunakan: /harga <TICKER1> [TICKER2 ...]\nContoh: /harga BBCA.JK TLKM.JK")
        return
    tickers = context.args
    results = []
    for tk in tickers:
        info = fetch_price_and_change(tk)
        if info:
            last, pct = info
            emoji = "🟢📈" if pct > 0 else ("🔴📉" if pct < 0 else "⚪")
            results.append((tk.upper(), f"Rp {last:,.0f}", f"{pct:+.2f}%", emoji))
        else:
            results.append((tk.upper(), "N/A", "", "❌"))
    # format monospace table
    max_t = max(len(r[0]) for r in results)
    max_p = max(len(r[1]) for r in results)
    max_pct = max(len(r[2]) for r in results)
    lines = []
    for t, p, pct, em in results:
        lines.append(f"{t:<{max_t}} | {p:>{max_p}} | {pct:>{max_pct}} {em}")
    text = "```\n" + "\n".join(lines) + "\n```"
    await update.message.reply_text(text, parse_mode="MarkdownV2")

async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Gunakan: /stock <TICKER>\nContoh: /stock BBCA.JK")
        return
    tk = context.args[0].upper()
    # fetch price & pct
    info = fetch_price_and_change(tk)
    if not info:
        await update.message.reply_text(f"❌ Gagal ambil data untuk {tk}")
        return
    price, pct = info
    # try to get long name (company) via yfinance
    name = ""
    try:
        t = yf.Ticker(to_jk(tk))
        info_dict = t.info if hasattr(t, "info") else {}
        name = info_dict.get("longName") or info_dict.get("shortName") or ""
    except Exception:
        name = ""
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    bio = create_stock_card(tk, name, price, pct, ts)
    caption = f"{tk} • Rp {price:,.0f} • {pct:+.2f}%"
    # send photo
    try:
        await update.message.reply_photo(photo=bio, caption=caption)
    except Exception as e:
        log.exception("Failed to send photo: %s", e)
        await update.message.reply_text("❌ Gagal mengirim kartu saham.")

# register handlers
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(CommandHandler("help", cmd_help))
application.add_handler(CommandHandler("harga", cmd_harga))
application.add_handler(CommandHandler("stock", cmd_stock))

# ---------------- Webhook endpoint (Flask) ----------------
@flask_app.route(WEBHOOK_PATH, methods=["POST"])
def telegram_webhook():
    # Telegram delivers JSON update; push into PTB queue
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return "OK", 200

@flask_app.route("/", methods=["GET"])
def index():
    return "Stock News Bot (webhook) is running", 200

# set webhook via Telegram HTTP API synchronously
def ensure_webhook_set():
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
        r = requests.post(url, data={"url": WEBHOOK_URL}, timeout=10)
        if r.ok:
            log.info("Webhook set -> %s", WEBHOOK_URL)
        else:
            log.warning("Failed setWebhook: %s %s", r.status_code, r.text)
    except Exception as e:
        log.exception("ensure_webhook_set error: %s", e)

# ---------------- start app ----------------
if __name__ == "__main__":
    log.info("Setting webhook (HTTP)...")
    ensure_webhook_set()
    # run Flask (Gunicorn will use WSGI in production)
    flask_app.run(host="0.0.0.0", port=PORT)
