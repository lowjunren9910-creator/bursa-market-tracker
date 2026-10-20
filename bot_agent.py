import os
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import telebot
from bs4 import BeautifulSoup
from google import genai
from datetime import datetime

# Environment Variables
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)
ai_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

# Background HTTP server to satisfy Render free-tier health checks
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write("Bursa Stock Agent Bot is Active & Running 24/7 (Free Tier)!".encode('utf-8'))

    def log_message(self, format, *args):
        return

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

# Fast mapping dictionary for common tickers
FAST_NAME_MAP = {
    "MAYBANK": "1155",
    "PBBANK": "1023",
    "PUBLICBANK": "1023",
    "TENAGA": "5347",
    "TNB": "5347",
    "CIMB": "1295",
    "YTL": "4677",
    "YTLPOWR": "6742",
    "YTLPOWER": "6742",
    "GAMUDA": "5296",
    "IHH": "5225",
    "INARI": "0166",
    "GENTING": "3182",
    "GENM": "4715",
    "SIME": "4197",
    "PETGAS": "6033",
    "PCHEM": "5183",
    "PETCHEM": "5183",
    "MISC": "3816",
    "IOICORP": "1961",
    "KLK": "2445",
    "MAXIS": "6012",
    "CELCOMDIGI": "6947",
    "CDB": "6947",
    "RHBBANK": "1066",
    "HLBANK": "5819",
    "SUNWAY": "5211",
    "MRDIY": "5296",
    "NOTION": "0083",
    "FARMFRESH": "5306",
    "PADINI": "7052",
    "PPB": "4065",
    "NATGATE": "0270",
    "EONCR": "5281",
    "IGBREIT": "5227"
}

def resolve_code(user_input):
    raw = user_input.strip().upper().replace(" ", "").replace(".KL", "")
    if raw in FAST_NAME_MAP:
        return FAST_NAME_MAP[raw]
    return raw

def fetch_bursa_stock_realtime
