import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

ADMIN_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "").split(",")
    if value.strip().isdigit()
}

DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    os.path.join(BASE_DIR, "data", "bot.db")
)

SMM_API_URL = os.getenv("SMM_API_URL", "").strip()
SMM_API_KEY = os.getenv("SMM_API_KEY", "").strip()
PIX_PROVIDER = os.getenv("PIX_PROVIDER", "").strip()
