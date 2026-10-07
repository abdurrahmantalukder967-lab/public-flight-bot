import os
import math
import time
import threading
import requests
from datetime import datetime
from flask import Flask, request

app = Flask(__name__)

# Fetch Telegram Bot Token from Render Environment Variables
BOT_TOKEN = "8924222773:AAHtjTaGPTnGMcoYsUkgxPhaFZYtChxIWNE"

# Dictionary to store registered user locations and notified flights
# Structure: { chat_id: {"lat": float, "lon": float, "notified": set()} }
user_locations = {}

AIRLINE_NAMES = {
    "AWA": "Air Astra", "BBC": "Biman Bangladesh Airlines", "UBG": "US-Bangla Airlines",
    "IGO": "IndiGo", "AIC": "Air India", "SEJ": "SpiceJet", "VTI": "Vistara",
    "AXB": "Air India Express", "SIA": "Singapore Airlines", "GFA": "Gulf Air",
    "QTR": "Qatar Airways", "UAE": "Emirates", "ETD": "Etihad Airways", "FDB": "flydubai",
    "JAZ": "Jazeera Airways", "KAC": "Kuwait Airways", "SV": "Saudia", "THY": "Turkish Airlines",
    "MAS": "Malaysia Airlines", "CPA": "Cathay Pacific", "AKJ": "Akasa Air", "NVQ": "NovoAir"
}

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def send_telegram_message(chat_id, text, reply_markup=None):
    if not BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{"8924222773:AAHtjTaGPTnGMcoYsUkgxPhaFZYtChxIWNE"}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(url, json=payload, timeout=8)
    except Exception as e:
        print(f"Error sending message to {chat_id}:", e)

def fetch_flights(lat, lon, radius_km=90.0):
    lat_min = lat - (radius_km / 111.0)
    lat_max = lat + (radius_km / 111.0)
    lon_min = lon - (radius_km / (111.0 * math.cos(math.radians(lat))))
    lon_max = lon + (radius_km / (111.0 * math.cos(math.radians(lat))))

    # FlightRadar24 Data Feed
    fr24_url = f"https://data-cloud.flightradar24.com/zones/fcgi/feed.json?bounds={lat_max},{lat_min},{lon_min},{lon_max}&faa=1&satellite=1&mlat=1&flarm=1&adsb=1&gnd=1&air=1&vehicles=1&estimated=1&stats=1"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0',
        'Origin': 'https://www.flightradar24.com',
        'Referer': 'https://www.flightradar24.com/'
    }

    planes = []
    try:
        res = requests.get(fr24_url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            for key, val in data.items():
                if not isinstance(val, list) or len(val) < 18:
                    continue
                p_lat, p_lon = val[1], val[2]
                if p_lat is None or p_lon is None:
                    continue

                dist = calculate_distance(lat, lon, p_lat, p_lon)
                if dist <= radius_km:
                    callsign = val[16].strip() if val[16] else "N/A"
                    prefix = callsign[:3].upper() if callsign != "N/A" else ""
                    airline = AIRLINE_NAMES.get(prefix, f"Airline ({prefix})" if prefix else "Commercial Airline")
                    origin = val[11] if val[11] else "N/A"
                    dest = val[12] if val[12] else "N/A"
                    route = f"{origin} → {dest}" if (origin != "N/A" or dest != "N/A") else "N/A → N/A"

                    planes.append({
                        'id': key,
                        'callsign': callsign,
                        'airline': airline,
                        'aircraft': val[8] if val[8] else "Aircraft",
                        'route': route,
                        'alt': val[4] if val[4] else 0,
                        'speed': int(val[5] * 1.852) if val[5] else 0,
                        'dist': round(dist, 1)
                    })
    except Exception as e:
        print("FR24 Fetch Error:", e)

    return sorted(planes, key=lambda x: x['dist'])

# Background tracking thread for registered user locations
def background_tracker():
    while True:
        try:
            for chat_id, user_data in list(user_locations.items()):
                u_lat = user_data["lat"]
                u_lon = user_data["lon"]
                notified = user_data["notified"]

                current_planes = fetch_flights(u_lat, u_lon, radius_km=90.0)
                active_ids = set()

                for p in current_planes:
                    active_ids.add(p['id'])
                    if p['id'] not in notified:
                        now_time = datetime.now().strftime("%I:%M:%S %p")
                        msg = (
                            f"✈️🟢 *New Flight Detected Near You!*\n\n"
                            f"*Airline:* {p['airline']}\n"
                            f"*Aircraft:* {p['aircraft']}\n"
                            f"*Route:* {p['route']}\n"
                            f"*Callsign:* {p['callsign']}\n"
                            f"*Distance:* {p['dist']} km\n"
                            f"*Altitude:* {p['alt']} ft\n"
                            f"*Speed:* {p['speed']} km/h\n"
                            f"*Time:* {now_time}"
                        )
                        send_telegram_message(chat_id, msg)
                        notified.add(p['id'])

                # Remove flights that moved out of range
                for old_id in list(notified):
                    if old_id not in active_ids:
                        notified.remove(old_id)

        except Exception as err:
            print("Background Tracker Loop Error:", err)

        time.sleep(60)

# Start background thread
threading.Thread(target=background_tracker, daemon=True).start()

# Telegram Webhook Receiver Endpoint
@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    update = request.json or {}
    if "message" in update:
        msg = update["message"]
        chat_id = str(msg["chat"]["id"])

        # /start command triggers location request button
        if "text" in msg and msg["text"].startswith("/start"):
            keyboard = {
                "keyboard": [[{
                    "text": "📍 Send My Live Location",
                    "request_location": True
                }]],
                "resize_keyboard": True,
                "one_time_keyboard": True
            }
            welcome_msg = (
                "👋 *Welcome to Public Flight Tracker Bot!*\n\n"
                "Please tap the *'📍 Send My Live Location'* button below to register your coordinates.\n"
                "You will automatically receive notifications for flights passing within 90km of your location."
            )
            send_telegram_message(chat_id, welcome_msg, reply_markup=keyboard)

        # Handle user location submission
        elif "location" in msg:
            lat = msg["location"]["latitude"]
            lon = msg["location"]["longitude"]

            if chat_id not in user_locations:
                user_locations[chat_id] = {"lat": lat, "lon": lon, "notified": set()}
            else:
                user_locations[chat_id]["lat"] = lat
                user_locations[chat_id]["lon"] = lon

            confirm_msg = (
                f"✅ *Location Updated Successfully!*\n\n"
                f"*Latitude:* `{lat}`\n*Longitude:* `{lon}`\n\n"
                f"Live flight tracking is now active for a 90km radius around your location."
            )
            send_telegram_message(chat_id, confirm_msg)

    return "OK", 200

@app.route('/')
def home():
    return "Public Flight Tracker Bot Service is Running!"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
