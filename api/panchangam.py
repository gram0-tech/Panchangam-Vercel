from http.server import BaseHTTPRequestHandler
import os, time, requests, traceback
from datetime import datetime
from zoneinfo import ZoneInfo
import datetime as dt

# --------------------------
# Retry
# --------------------------
RETRY_STATUS = {429, 500, 502, 503, 504}

def http_with_retry(method, url, *, max_attempts=3, backoff=0.7, **kwargs):
    attempt, last_exc = 0, None
    timeout = kwargs.pop("timeout", 20)
    while attempt < max_attempts:
        try:
            r = requests.request(method, url, timeout=timeout, **kwargs)
            if r.status_code in RETRY_STATUS:
                raise requests.RequestException(f"{r.status_code} {r.reason}: {r.text}")
            return r
        except Exception as e:
            last_exc = e
            attempt += 1
            if attempt >= max_attempts:
                break
            time.sleep(backoff * attempt)
    raise last_exc
 
# --------------------------
# MET OFFICE (via Open‑Meteo UKMO)
# --------------------------
def get_metoffice_sun_times(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "sunrise,sunset",
        "timezone": "Europe/London",
        "models": "ukmo_global"
    }
    r = http_with_retry("GET", url, params=params)
    r.raise_for_status()
    d = r.json().get("daily", {})
    if not d.get("sunrise") or not d.get("sunset"):
        raise Exception(f"Missing sunrise/sunset from UKMO: {d}")
    return d["sunrise"][0], d["sunset"][0]   # e.g. "2026-03-12T06:27"

def parse_london(ts):
    if not ts:
        return None
    if len(ts) == 16:      # YYYY-MM-DDTHH:MM
        ts = ts + ":00"
    dt_obj = datetime.fromisoformat(ts)
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=ZoneInfo("Europe/London"))
    return dt_obj

def to_uk(ts_iso):
    try:
        d = datetime.fromisoformat(ts_iso)
        return d.astimezone(ZoneInfo("Europe/London")).strftime("%I:%M %p").lstrip("0")
    except:
        return "—"

# --------------------------
# Prokerala helpers
# --------------------------
def today_india_iso():
    return f"{dt.date.today().strftime('%Y-%m-%d')}T05:30:00+05:30"

def get_token(cid, sec):
    r = http_with_retry(
        "POST", "https://api.prokerala.com/token",
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
        auth=(cid, sec)
    )
    r.raise_for_status()
    return r.json()["access_token"]

def get_panchang(token, lang, lat, lon, ayan):
    r = http_with_retry(
        "GET",
        "https://api.prokerala.com/v2/astrology/panchang",
        params={
            "la": lang,
            "datetime": today_india_iso(),
            "coordinates": f"{lat},{lon}",
            "ayanamsa": ayan
        },
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    r.raise_for_status()
    return r.json()

# --------------------------
# WhatsApp
# --------------------------
def send_whatsapp(body, to, token):
    r = http_with_retry(
        "POST",
        "https://gate.whapi.cloud/messages/text",
        json={"to": to, "body": body},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    if r.status_code not in (200, 201):
        raise Exception(f"WhatsApp send error: {r.text}")

# --------------------------
# Safe JSON getter
# --------------------------
def _safe(obj, *path):
    for p in path:
        if isinstance(obj, dict) and isinstance(p, str):
            obj = obj.get(p, "—")
        elif isinstance(obj, list) and isinstance(p, int):
            obj = obj[p] if len(obj) > p else "—"
        else:
            return "—"
    return obj

# --------------------------
# Rahu/Yama/Gulika, Abhijit/Brahma
# --------------------------
def calc_kalams(sr_iso, ss_iso):
    sr = datetime.fromisoformat(sr_iso)
    ss = datetime.fromisoformat(ss_iso)
    seg = (ss - sr).total_seconds() / 8
    wd = sr.weekday()
    rahu_i   = [2,7,5,6,4,3,8][wd]
    yama_i   = [5,4,3,2,7,6,1][wd]
    guli_i   = [7,6,5,4,3,2,1][wd]

    def seg_range(i):
        s = sr + dt.timedelta(seconds=seg*(i-1))
        e = sr + dt.timedelta(seconds=seg*i)
        return (s.strftime("%I:%M %p").lstrip("0"), e.strftime("%I:%M %p").lstrip("0"))

    return seg_range(rahu_i), seg_range(yama_i), seg_range(guli_i)

def calc_abhi_brahma(sr_iso, ss_iso):
    sr = datetime.fromisoformat(sr_iso)
    ss = datetime.fromisoformat(ss_iso)
    midday = sr + (ss - sr)/2
    abhi_s = midday - dt.timedelta(minutes=24)
    abhi_e = midday + dt.timedelta(minutes=24)
    bra_s  = sr - dt.timedelta(minutes=96)
    bra_e  = sr - dt.timedelta(minutes=48)
    fmt = lambda x: x.strftime("%I:%M %p").lstrip("0")
    return (fmt(abhi_s), fmt(abhi_e)), (fmt(bra_s), fmt(bra_e))

# --------------------------
# Message Builder
# --------------------------
def build_message(te, ta, lat, lon):
    # Panchang
    tithiTE = _safe(te,"data","tithi",0,"name")
    nakTE   = _safe(te,"data","nakshatra",0,"name")
    yogaTE  = _safe(te,"data","yoga",0,"name")
    karTE   = _safe(te,"data","karana",0,"name")

    tithiTA = _safe(ta,"data","tithi",0,"name")
    nakTA   = _safe(ta,"data","nakshatra",0,"name")
    yogaTA  = _safe(ta,"data","yoga",0,"name")
    karTA   = _safe(ta,"data","karana",0,"name")

    weekdayTE = _safe(te,"data","vaara")
    weekdayTA = _safe(ta,"data","vaara")
    weekdayEN = dt.date.today().strftime("%A")

    # Sunrise/sunset from UKMO with fallback
    try:
        sr_str, ss_str = get_metoffice_sun_times(float(lat), float(lon))
        sr_dt = parse_london(sr_str)
        ss_dt = parse_london(ss_str)
        sr_iso = sr_dt.isoformat()
        ss_iso = ss_dt.isoformat()
    except Exception:
        sr_iso = _safe(te,"data","sunrise")
        ss_iso = _safe(te,"data","sunset")

    sunrise = to_uk(sr_iso)
    sunset  = to_uk(ss_iso)

    # Rahu/Yama/Gulika
    try:
        rk, yg, gk = calc_kalams(sr_iso, ss_iso)
        rahu  = f"{rk[0]}–{rk[1]}"
        yama  = f"{yg[0]}–{yg[1]}"
        guli  = f"{gk[0]}–{gk[1]}"
    except:
        rahu = yama = guli = "—"

    # Abhijit/Brahma
    try:
        (abhi_s, abhi_e), (bra_s, bra_e) = calc_abhi_brahma(sr_iso, ss_iso)
    except:
        abhi_s = abhi_e = bra_s = bra_e = "—"

    return (
        f"📿 *Telugu Panchangam – Chelmsford* ({weekdayTE}, {weekdayEN})\n"
        f"🗓 తిథి: {tithiTE} | ✨ నక్షత్రం: {nakTE} | 🧘 యోగం: {yogaTE} | 🔱 కరణం: {karTE}\n"
        f"🌅 సూర్యోదయం: {sunrise} | 🌇 సూర్యాస్తమయం: {sunset}\n"
        f"☀ అభిజిత్: {abhi_s}–{abhi_e} | 🌄 బ్రహ్మ: {bra_s}–{bra_e}\n"
        f"☀ రాహుకాలం: {rahu} | 🌘 యమగండం: {yama} | 🕉️ గులిక: {guli}\n\n"
        f"📿 *Tamil Panchangam – Chelmsford* ({weekdayTA}, {weekdayEN})\n"
        f"🗓 திதி: {tithiTA} | ✨ நட்சத்திரம்: {nakTA} | 🧘 யோகம்: {yogaTA} | 🔱 கரணம்: {karTA}\n"
        f"🌅 சூரியோதயம்: {sunrise} | 🌇 சூரியாஸ்தமனம்: {sunset}\n"
        f"☀ அபிஜித்: {abhi_s}–{abhi_e} | 🌄 பிரம்ம: {bra_s}–{bra_e}\n"
        f"☀ ராகு: {rahu} | 🌘 எமகண்டம்: {yama} | 🕉️ குளிகை: {guli}\n"
    )

# --------------------------
# Handler
# --------------------------
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        cron_secret = os.getenv("CRON_SECRET","")
        auth = self.headers.get("authorization","")
        if cron_secret and auth != f"Bearer {cron_secret}":
            self.send_response(401); self.end_headers(); self.wfile.write(b"Unauthorized"); return

        cid  = os.getenv("PROKERALA_CLIENT_ID")
        csec = os.getenv("PROKERALA_CLIENT_SECRET")
        wtok = os.getenv("WHAPI_TOKEN")
        to   = os.getenv("WHATSAPP_TO")
        lat  = os.getenv("LAT","51.7350")
        lon  = os.getenv("LON","-0.4696")
        ay   = os.getenv("AYANAMSA","1")

        if not cid or not csec or not wtok or not to:
            self.send_response(500); self.end_headers(); self.wfile.write(b"Missing environment"); return

        try:
            token = get_token(cid, csec)
            te = get_panchang(token,"te",lat,lon,ay)
            ta = get_panchang(token,"ta",lat,lon,ay)

            msg = build_message(te, ta, lat, lon)
            send_whatsapp(msg, to, wtok)

            self.send_response(200); self.end_headers(); self.wfile.write(b"Sent")

        except Exception:
            tb = traceback.format_exc().encode()
            self.send_response(500)
            self.send_header("Content-Type","text/plain")
            self.send_header("Content-Length",str(len(tb)))
            self.end_headers()
            self.wfile.write(tb)

