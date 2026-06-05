import os
import json
import logging
import smtplib
import ssl
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from threading import Thread, Event
from time import sleep

from flask import Flask, render_template, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

# --- Config from .env ---
IG_USERNAME = os.getenv("IG_USERNAME")
IG_PASSWORD = os.getenv("IG_PASSWORD")
TARGET_USERNAME = os.getenv("TARGET_USERNAME")

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER", EMAIL_SENDER)

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

DATA_FILE = Path(__file__).parent / "data.json"
stop_event = Event()

# --- Email ---
def send_email(subject, body):
    if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
        return False
    msg = MIMEMultipart()
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        log.info("Email sent: %s", subject)
        return True
    except Exception as e:
        log.error("Email error: %s", e)
        return False

# --- Data persistence ---
def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"history": [], "last": None}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# --- Instagram fetch ---
def fetch_stats(username):
    try:
        from instaloader import Instaloader, Profile
        L = Instaloader()
        if IG_USERNAME and IG_PASSWORD:
            L.login(IG_USERNAME, IG_PASSWORD)
        profile = Profile.from_username(L.context, username)
        return {
            "followers": profile.followers,
            "following": profile.followees,
            "posts": profile.mediacount,
            "full_name": profile.full_name,
        }
    except Exception as e:
        log.error("Fetch error for %s: %s", username, e)
        return None

# --- Background checker ---
def check_loop():
    log.info("Background checker started")
    while not stop_event.is_set():
        if not TARGET_USERNAME:
            sleep(60)
            continue
        data = load_data()
        stats = fetch_stats(TARGET_USERNAME)
        if not stats:
            sleep(CHECK_INTERVAL * 60)
            continue

        now = datetime.now().isoformat()
        entry = {
            "time": now,
            "followers": stats["followers"],
            "following": stats["following"],
            "posts": stats["posts"],
        }
        data["history"].append(entry)
        # Keep last 1000 entries
        if len(data["history"]) > 1000:
            data["history"] = data["history"][-1000:]

        last = data["last"]
        changes = []
        if last:
            if stats["followers"] != last["followers"]:
                diff = stats["followers"] - last["followers"]
                direction = "arti" if diff > 0 else "eksi"
                changes.append(f"Takipci: {last['followers']} -> {stats['followers']} ({direction} {abs(diff)})")
            if stats["following"] != last["following"]:
                diff = stats["following"] - last["following"]
                direction = "arti" if diff > 0 else "eksi"
                changes.append(f"Takip: {last['following']} -> {stats['following']} ({direction} {abs(diff)})")

        data["last"] = {"followers": stats["followers"], "following": stats["following"], "posts": stats["posts"]}
        save_data(data)

        if changes:
            log.info("Change detected: %s", "; ".join(changes))
            subject = f"[IG Tracker] @{TARGET_USERNAME} - {'; '.join(changes)}"
            body = f"@{TARGET_USERNAME}\nTarih: {now}\n\n" + "\n".join(changes)
            send_email(subject, body)

        sleep(CHECK_INTERVAL * 60)

# --- Web routes ---
@app.route("/")
def index():
    data = load_data()
    return render_template("index.html",
        target=TARGET_USERNAME,
        last=data.get("last"),
        history=data.get("history", [])[-20:],
    )

@app.route("/api/status")
def api_status():
    data = load_data()
    return jsonify({
        "target": TARGET_USERNAME,
        "last": data.get("last"),
        "total_checks": len(data.get("history", [])),
    })

@app.route("/api/history")
def api_history():
    data = load_data()
    return jsonify(data.get("history", []))

# --- Start background thread ---
thread = Thread(target=check_loop, daemon=True)
thread.start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
