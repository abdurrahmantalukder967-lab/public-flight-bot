import os
import time
import math
import json
import requests
import threading
from flask import Flask, request

app = Flask(__name__)

# Config
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8924222773:AAHtjTaGPTnGMcoYsUkgxPhaFZYtChxIWNE")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
LOCATION_FILE = "user_locations.json"

# File Persistence Functions
def load_locations():
    if os.path.exists(LOCATION_FILE):
        try:
            with open(LOCATION_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_locations(locations):
    try:
        with open(LOCATION_FILE, "w") as f:
            json.dump(locations, f)
    except Exception:
        pass

# Global Memory
user_locations = load_locations()
notified_flights = {}  # {chat_id: set(flight_ids)}

AIRLINE_NAMES = {
    "BBC": "Biman Bangladesh Airlines",
    "UBG": "US-Bangla Airlines",
    "AWA": "Air Astra",
    "IGO": "IndiGo",
    "AIC": "Air India",
    "AKJ": "Akasa Air",
    "SEJ": "SpiceJet",
    "JAI": "Jet Airways",
    "UAE": "Emirates",
    "QTR": "Qatar Airways",
    "ETD": "Etihad Airways",
    "GFA": "Gulf Air",
    "FDX": "FedEx",
    "CES": "China Eastern Airlines",
    "CSN": "China Southern Airlines"
}

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def send_telegram_message(chat_id, text, reply_markup=None):
    url = f"{TELEGRAM_API}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Error sending message: {e}")

def get_airline_name(callsign):
    if not callsign or len(callsign) < 3:
        return "Unknown Airline"
    prefix = callsign[:3].upper()
    return AIRLINE_NAMES.get(prefix, f"Airline ({prefix})")

def fetch_flights():
    url = "https://data-cloud.flightradar24.com/zones/fcgi/feed.json"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"Error fetching FR24 feed: {e}")
    return {}

def background_tracker():
    while True:
        try:
            if user_locations:
                data = fetch_flights()
                current_time_str = time.strftime("%I:%M:%S %p")
                
                for chat_id_str, loc in list(user_locations.items()):
                    user_lat = loc["lat"]
                    user_lon = loc["lon"]
                    
                    if chat_id_str not in notified_flights:
                        notified_flights[chat_id_str] = set()
                    
                    currently_nearby = set()
                    
                    for key, val in data.items():
                        if isinstance(val, list) and len(val) >= 14:
                            flight_id = key
                            lat = val[1]
                            lon = val[2]
                            alt = val[4]
                            spd = val[5]
                            callsign = val[16] if len(val) > 16 and val[16] else "N/A"
                            aircraft = val[8] if len(val) > 8 and val[8] else "Unknown Aircraft"
                            origin = val[11] if len(val) > 11 and val[11] else "???"
                            dest = val[12] if len(val) > 12 and val[12] else "???"
                            
                            dist = haversine(user_lat, user_lon, lat, lon)
                            
                            if dist <= 90.0:
                                currently_nearby.add(flight_id)
                                
                                if flight_id not in notified_flights[chat_id_str]:
                                    airline = get_airline_name(callsign)
                                    msg = (
                                        f"✈️🟢 *New Flight Detected Near You!*\n\n"
                                        f"**Airline:** {airline}\n"
                                        f"**Aircraft:** {aircraft}\n"
                                        f"**Route:** {origin} ➔ {dest}\n"
                                        f"**Callsign:** {callsign}\n"
                                        f"**Distance:** {dist:.1f} km\n"
                                        f"**Altitude:** {alt} ft\n"
                                        f"**Speed:** {spd} kts\n"
                                        f"**Time:** {current_time_str}"
                                    )
                                    send_telegram_message(int(chat_id_str), msg)
                                    notified_flights[chat_id_str].add(flight_id)
                    
                    # 90km-এর বাইরে গেলে মেমরি থেকে রিমুভ
                    notified_flights[chat_id_str] = notified_flights[chat_id_str].intersection(currently_nearby)
                    
        except Exception as e:
            print(f"Error in background tracker loop: {e}")
            
        time.sleep(60)

# Global Thread Reference & Handler
tracker_thread = None

def ensure_tracker_running():
    global tracker_thread
    if tracker_thread is None or not tracker_thread.is_alive():
        tracker_thread = threading.Thread(target=background_tracker, daemon=True)
        tracker_thread.start()

@app.route('/', methods=['GET', 'HEAD'])
def index():
    ensure_tracker_running()
    return "Flight Alert 24 Service is Running!", 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    ensure_tracker_running()
    data = request.get_json(force=True, silent=True)
    
    if not data or "message" not in data:
        return "OK", 200
        
    msg = data["message"]
    chat_id = msg["chat"]["id"]
    chat_id_str = str(chat_id)
    
    # 1. Location Handling
    if "location" in msg:
        lat = msg["location"]["latitude"]
        lon = msg["location"]["longitude"]
        
        user_locations[chat_id_str] = {"lat": lat, "lon": lon}
        save_locations(user_locations)
        
        confirm_text = (
            "✅ *Location Updated Successfully!*\n\n"
            f"**Latitude:** {lat}\n"
            f"**Longitude:** {lon}\n\n"
            "Live flight tracking is now active for a 90km radius around your location."
        )
        send_telegram_message(chat_id, confirm_text)
        
    # 2. Command Handling
    elif "text" in msg:
        text = msg["text"].strip()
        
        if text.startswith("/start"):
            welcome_msg = (
                "👋 *Welcome to Public Flight Tracker Bot!*\n\n"
                "Please tap the *'📍 Send My Live Location'* button below to register your coordinates.\n"
                "You will automatically receive notifications for flights passing within 90km of your location.\n\n"
                "🔒 *Privacy Guarantee:* Your location is processed in real-time solely to calculate flight distances "
                "and is never saved to external databases or shared with anyone."
            )
            keyboard = {
                "keyboard": [[{"text": "📍 Send My Live Location", "request_location": True}]],
                "resize_keyboard": True,
                "one_time_keyboard": False
            }
            send_telegram_message(chat_id, welcome_msg, reply_markup=keyboard)
            
    return "OK", 200

if __name__ == "__main__":
    ensure_tracker_running()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
