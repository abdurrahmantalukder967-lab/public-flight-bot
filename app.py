import math
import os
import sqlite3
import requests
import time
from datetime import datetime
from threading import Thread
from flask import Flask, request

app = Flask(__name__)

# --- Configuration ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8924222773:AAHtjTaGPTnGMcoYsUkgxPhaFZYtChxIWNE")
CHECK_INTERVAL = 57

# Memory set for tracked planes per user session: {chat_id: set(icao_ids)}
alerted_planes = {}

# --- Database Setup ---
DB_NAME = "flight_bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id TEXT PRIMARY KEY,
            lat REAL,
            lon REAL,
            radius REAL DEFAULT 90.0,
            active INTEGER DEFAULT 1
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def db_execute(query, params=(), fetchall=False, fetchone=False):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(query, params)
    res = None
    if fetchall:
        res = cursor.fetchall()
    elif fetchone:
        res = cursor.fetchone()
    conn.commit()
    conn.close()
    return res

# --- Static Data ---
AIRLINE_NAMES = {
    "AQA": "Air Astra", "BBC": "Biman Bangladesh Airlines", "UBG": "US-Bangla Airlines",
    "VOX": "Air Astra", "IGO": "IndiGo", "AIC": "Air India", "SEJ": "SpiceJet",
    "VTI": "Vistara", "AXB": "Air India Express", "SIA": "Singapore Airlines",
    "GFA": "Gulf Air", "QFA": "Qantas", "UAE": "Emirates", "ETD": "Etihad Airways",
    "FDB": "flydubai", "JAZ": "Jazeera Airways", "KAC": "Kuwait Airways",
    "MSR": "EgyptAir", "SVA": "Saudia", "THY": "Turkish Airlines", "MAS": "Malaysia Airlines",
    "BVT": "Batik Air", "CPA": "Cathay Pacific", "DRK": "Drukair", "AKJ": "Akasa Air",
    "SWM": "SalamAir", "NVQ": "Novoair", "LLR": "Alliance Air", "CSH": "China Southern Airlines",
    "CES": "China Eastern Airlines", "CCA": "Air China", "RNA": "Nepal Airlines",
    "AJK": "Bangladesh Air Force", "ABY": "Air Arabia", "VJC": "VietJet Air"
}

AIRCRAFT_NAMES = {
    "AT76": "ATR 72-600", "AT75": "ATR 72-500", "AT72": "ATR 72", "DH8D": "De Havilland Dash 8-400",
    "A20N": "Airbus A320neo", "A320": "Airbus A320", "A21N": "Airbus A321neo", "A321": "Airbus A321",
    "A319": "Airbus A319", "A332": "Airbus A330-200", "A333": "Airbus A330-300", "A339": "Airbus A330-900neo",
    "A359": "Airbus A350-900", "A388": "Airbus A380-800", "B738": "Boeing 737-800", "B38M": "Boeing 737 MAX 8",
    "B773": "Boeing 777-300ER", "B788": "Boeing 787-8 Dreamliner", "B789": "Boeing 787-9 Dreamliner"
}

BIMAN_NAMES = {
    "S2-AFR": "আকাশপ্রদীপ / Akash Pradip", "S2-AFS": "রাঙা প্রভাত / Ranga Prabhat",
    "S2-AHV": "মেঘদূত / Meghdoot", "S2-AHW": "ময়ূরপঙ্খী / Mayurpankhi",
    "S2-AJS": "আকাশবীণা / Akashbeena", "S2-AJT": "হংসবলাকা / Hangsabalaka",
    "S2-AJU": "গাংচিল / Gangchil", "S2-AJV": "রাজহাঁস / Rajjahagsha",
    "S2-AJX": "সোনার তরী / Sonar Tori", "S2-AJY": "অচিন পাখি / Achin Pakhi",
    "S2-AGR": "ধ্রুবতারা / Dhrubotara", "S2-AKU": "শ্বেতকপোত / Swetkapot",
    "S2-AKV": "নীলাচল / Neelachal", "S2-AKW": "পঙ্খীরাজ / Pankhiraj", "S2-AKF": "আকাশতরি / Akashtori"
}

REGISTRATION_PREFIXES = {
    "S2": "Bangladesh 🇧🇩", "VT": "India 🇮🇳", "AP": "Pakistan 🇵🇰", "4R": "Sri Lanka 🇱🇰",
    "8Q": "Maldives 🇲🇻", "A5": "Bhutan 🇧🇹", "9N": "Nepal 🇳🇵", "A6": "United Arab Emirates 🇦🇪",
    "A7": "Qatar 🇶🇦", "HZ": "Saudi Arabia 🇸🇦", "B": "China / Taiwan / Hong Kong 🇨🇳",
    "G": "United Kingdom 🇬🇧", "N": "United States 🇺🇸"
}

def get_country_from_registration(reg):
    if not reg or reg == "Unknown":
        return "Unknown"
    reg_upper = reg.upper()
    for prefix in sorted(REGISTRATION_PREFIXES.keys(), key=len, reverse=True):
        if reg_upper.startswith(prefix):
            return REGISTRATION_PREFIXES[prefix]
    return "Unknown"

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    return 2 * R * math.asin(math.sqrt(a))

def send_telegram(chat_id, message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"Telegram error for {chat_id}:", e, flush=True)

# --- Webhook Routes ---
@app.route("/", methods=["GET"])
def health_check():
    return "Public Bot Online", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)
    if data:
        message = data.get("message", {}) or data.get("edited_message", {})
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "")
        location = message.get("location")

        if chat_id:
            chat_id = str(chat_id)
            
            # Ensure user exists in DB
            user = db_execute("SELECT chat_id FROM users WHERE chat_id=?", (chat_id,), fetchone=True)
            if not user:
                db_execute("INSERT INTO users (chat_id, active) VALUES (?, 1)", (chat_id,))

            # Handle /start
            if text.startswith("/start"):
                db_execute("UPDATE users SET active=1 WHERE chat_id=?", (chat_id,))
                msg = (
                    "👋 <b>Welcome to Public Flight Tracker Bot!</b>\n\n"
                    "📍 আপনার আশেপাশের প্লেন ট্র্যাক করতে আপনার <b>Location</b> অথবা <b>Live Location</b> পাঠান।\n\n"
                    "⚙️ <b>Commands:</b>\n"
                    "• <code>/radius 60</code> - ট্র্যাকিং রেডিয়াস বদলে ৬০ কিমি করা\n"
                    "• <code>/stop</code> - অ্যালার্ট বন্ধ করা"
                )
                send_telegram(chat_id, msg)

            # Handle /stop
            elif text.startswith("/stop"):
                db_execute("UPDATE users SET active=0 WHERE chat_id=?", (chat_id,))
                send_telegram(chat_id, "🛑 আপনার ফ্লাইট অ্যালার্ট বন্ধ করা হয়েছে। চালু করতে আবার <b>Location</b> শেয়ার করুন বা /start চাপুন।")

            # Handle /radius
            elif text.startswith("/radius"):
                try:
                    r_val = float(text.split()[1])
                    if 10.0 <= r_val <= 300.0:
                        db_execute("UPDATE users SET radius=? WHERE chat_id=?", (r_val, chat_id))
                        send_telegram(chat_id, f"✅ আপনার ট্র্যাকিং রেডিয়াস <b>{r_val} km</b> সেট করা হয়েছে।")
                    else:
                        send_telegram(chat_id, "⚠️ রেডিয়াস ১০ কিমি থেকে ৩০০ কিমির মধ্যে দিন।")
                except:
                    send_telegram(chat_id, "❌ সঠিক ফরম্যাট: <code>/radius 50</code>")

            # Handle Location
            elif location:
                lat = location.get("latitude")
                lon = location.get("longitude")
                db_execute("UPDATE users SET lat=?, lon=?, active=1 WHERE chat_id=?", (lat, lon, chat_id))
                send_telegram(chat_id, "📍 <b>Location Updated!</b> আপনার আকাশে প্লেন আসলেই অ্যালার্ট পাবেন।")

    return "OK", 200

# --- Flightradar24 API Fetching ---
def get_flight_details(flight_id):
    url = f"https://data-live.flightradar24.com/clickapi/v1/data/a/default/all/json?flight={flight_id}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            res_json = r.json()
            if 'result' in res_json and 'response' in res_json['result']:
                f = res_json['result']['response']['data']['flight']
                origin = f.get('airport', {}).get('origin', {}).get('code', {}).get('iata', '')
                dest = f.get('airport', {}).get('destination', {}).get('code', {}).get('iata', '')
                if origin and dest:
                    return f"{origin} ✈️ {dest}"
    except Exception as e:
        print("Route Fetch Error:", e, flush=True)
    return None

def fetch_fr24_feed():
    url = "https://data-cloud.flightradar24.com/zones/fcgi/feed.json"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        return r.json()
    except Exception as e:
        print("FR24 Feed Fetch Error:", e, flush=True)
        return {}

# --- Multi-user Scanner Loop ---
def scanner_loop():
    print("Public Scanner Loop Running...", flush=True)
    while True:
        try:
            # Fetch all active users with valid location
            active_users = db_execute("SELECT chat_id, lat, lon, radius FROM users WHERE active=1 AND lat IS NOT NULL AND lon IS NOT NULL", fetchall=True)

            if active_users:
                feed_data = fetch_fr24_feed()

                for user in active_users:
                    chat_id, u_lat, u_lon, u_radius = user[0], user[1], user[2], user[3]
                    
                    if chat_id not in alerted_planes:
                        alerted_planes[chat_id] = set()

                    current_in_range = set()

                    for key, val in feed_data.items():
                        if isinstance(val, list) and len(val) > 13:
                            p_lat, p_lon = val[1], val[2]
                            dist = haversine(u_lat, u_lon, p_lat, p_lon)

                            if dist <= u_radius:
                                current_in_range.add(key)

                                if key not in alerted_planes[chat_id]:
                                    callsign = val[16] if (len(val) > 16 and val[16]) else "Unknown"
                                    alt = val[4]
                                    speed_kts = val[5]
                                    speed_kmh = round(speed_kts * 1.852) if isinstance(speed_kts, (int, float)) else 0
                                    reg = val[9] if (len(val) > 9 and val[9]) else "Unknown"
                                    
                                    country = val[17] if (len(val) > 17 and val[17]) else "Unknown"
                                    if country == "Unknown" and reg != "Unknown":
                                        country = get_country_from_registration(reg)

                                    raw_aircraft = val[8] if (len(val) > 8 and val[8]) else ""
                                    aircraft_fullname = AIRCRAFT_NAMES.get(raw_aircraft, raw_aircraft)
                                    if reg and reg.upper() in BIMAN_NAMES:
                                        aircraft_fullname += f" ({BIMAN_NAMES[reg.upper()]})"

                                    orig_code = val[11] if (len(val) > 11 and val[11]) else ""
                                    dest_code = val[12] if (len(val) > 12 and val[12]) else ""
                                    route = f"{orig_code} ✈️ {dest_code}" if (orig_code and dest_code) else (get_flight_details(key) or "N/A")

                                    raw_code = val[18] if (len(val) > 18 and val[18]) else ""
                                    airline_fullname = AIRLINE_NAMES.get(raw_code.upper(), raw_code)

                                    msg = (
                                        f"✈️🟢 <b>New Flight Detected!</b>\n\n"
                                        f"Airline: <b>{airline_fullname}</b>\n"
                                        f"Aircraft: <b>{aircraft_fullname}</b>\n"
                                        f"Country: <b>{country}</b>\n"
                                        f"Route: <b>{route}</b>\n"
                                        f"Callsign: <b>{callsign}</b>\n"
                                        f"Distance: <b>{round(dist, 1)} km</b>\n"
                                        f"Altitude: <b>{alt} ft</b>\n"
                                        f"Speed: <b>{speed_kmh} km/h</b>\n"
                                        f"Time: <b>{datetime.now().strftime('%H:%M:%S')}</b>"
                                    )
                                    send_telegram(chat_id, msg)
                                    alerted_planes[chat_id].add(key)

                    # Clear out-of-range planes from memory
                    alerted_planes[chat_id] = {p for p in alerted_planes[chat_id] if p in current_in_range}

        except Exception as err:
            print("Scanner Loop Error:", err, flush=True)

        time.sleep(CHECK_INTERVAL)

# Start thread
Thread(target=scanner_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
