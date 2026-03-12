from http.server import BaseHTTPRequestHandler
import os, json, re, time
from datetime import datetime
from zoneinfo import ZoneInfo
import datetime as dt
import requests

# ---- Config & helpers -------------------------------------------------------

RETRY_STATUS = {429, 500, 502, 503, 504}

def http_with_retry(method, url, *, max_attempts=3, backoff=0.75, **kwargs):
    """HTTP with simple exponential backoff for transient errors/timeouts."""
    attempt, last_exc = 0, None
    timeout = kwargs.pop("timeout", 25)
    while attempt < max_attempts:
        try:
            r = requests.request(method, url, timeout=timeout, **kwargs)
            if r.status_code in RETRY_STATUS:
                raise requests.RequestException(f"{r.status_code} {r.reason}: {r.text}")
            return r
        except (requests.Timeout, requests.ConnectionError, requests.RequestException) as e:
            last_exc = e
            attempt += 1
            if attempt >= max_attempts:
                break
            time.sleep(backoff * attempt)
    raise last_exc if last_exc else RuntimeError("HTTP error")

def today_india_iso():
    today = dt.date.today().strftime("%Y-%m-%d")
    return f"{today}T05:30:00+05:30"  # Start of day in IST

def to_uk_time_pretty(ts: str) -> str:
    """Convert API timestamp to Europe/London time, HH:MM AM/PM."""
    try:
        dt_obj = datetime.fromisoformat(ts)
        uk = dt_obj.astimezone(ZoneInfo("Europe/London"))
        return uk.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return "—"

def calc_kalam_ranges(sunrise_ts, sunset_ts):
    """Rahukalam, Yamagandam, Gulika from sunrise/sunset (UK local)."""
    sr = datetime.fromisoformat(sunrise_ts).astimezone(ZoneInfo("Europe/London"))
    ss = datetime.fromisoformat(sunset_ts).astimezone(ZoneInfo("Europe/London"))
    seg = (ss - sr).total_seconds() / 8
    wd = sr.weekday()  # Monday=0 … Sunday=6

    rahu_index   = [2, 7, 5, 6, 4, 3, 8][wd]
    yama_index   = [5, 4, 3, 2, 7, 6, 1][wd]
    gulika_index = [7, 6, 5, 4, 3, 2, 1][wd]

    def seg_range(i):
        s = sr + dt.timedelta(seconds=seg * (i - 1))
        e = sr + dt.timedelta(seconds=seg * i)
        return s.strftime("%I:%M %p").lstrip("0"), e.strftime("%I:%M %p").lstrip("0")

    return seg_range(rahu_index), seg_range(yama_index), seg_range(gulika_index)

def calc_abhijit_brahma(sunrise_ts, sunset_ts):
    """Abhijit = 48 min centered on midday; Brahma = 96–48 min before sunrise."""
    sr = datetime.fromisoformat(sunrise_ts).astimezone(ZoneInfo("Europe/London"))
    ss = datetime.fromisoformat(sunset_ts).astimezone(ZoneInfo("Europe/London"))

    midday = sr + (ss - sr)/2
    abhi_s, abhi_e = midday - dt.timedelta(minutes=24), midday + dt.timedelta(minutes=24)
    bra_s,  bra_e  = sr - dt.timedelta(minutes=96), sr - dt.timedelta(minutes=48)

    fmt = lambda d: d.strftime("%I:%M %p").lstrip("0")
    return (fmt(abhi_s), fmt(abhi_e)), (fmt(bra_s), fmt(bra_e))

def _safe(obj, *path):
    """Safe nested get for dict/list with graceful fallback."""
    for p in path:
        if isinstance(obj, list) and isinstance(p, int):
            if len(obj) <= p: return "—"
            obj = obj[p]
        elif isinstance(obj, dict) and isinstance(p, str):
            if p not in obj: return "—"
            obj = obj[p]
        else:
            return "—"
    return obj

# ---- Panchang / Messaging ---------------------------------------------------

def get_token(client_id, client_secret):
    r = http_with_retry(
        "POST", "https://api.prokerala.com/token",
        data={"grant_type":"client_credentials"},
        headers={"Accept":"application/json"},
        auth=(client_id, client_secret)
    )
    r.raise_for_status()
    return r.json()["access_token"]

def fetch_panchang(token, lang, lat, lon, ayanamsa):
    url = "https://api.prokerala.com/v2/astrology/panchang"
    params = {"la": lang, "datetime": today_india_iso(), "coordinates": f"{lat},{lon}", "ayanamsa": ayanamsa}
    r = http_with_retry("GET", url, params=params,
                        headers={"Authorization": f"Bearer {token}", "Accept":"application/json"})
    r.raise_for_status()
    return r.json()

def build_message_compact(te, ta):
    # Core fields
    tithiTE = _safe(te, "data", "tithi", 0, "name")
    nakTE   = _safe(te, "data", "nakshatra", 0, "name")
    yogaTE  = _safe(te, "data", "yoga", 0, "name")
    karTE   = _safe(te, "data", "karana", 0, "name")
    tithiTA = _safe(ta, "data", "tithi", 0, "name")
    nakTA   = _safe(ta, "data", "nakshatra", 0, "name")
    yogaTA  = _safe(ta, "data", "yoga", 0, "name")
    karTA   = _safe(ta, "data", "karana", 0, "name")

    weekdayTE = _safe(te, "data", "vaara")
    weekdayTA = _safe(ta, "data", "vaara")
    weekdayEN = dt.date.today().strftime("%A")

    sunrise_raw = _safe(te, "data", "sunrise")
    sunset_raw  = _safe(te, "data", "sunset")
    moonrise_raw = _safe(te, "data", "moonrise")
    moonset_raw  = _safe(te, "data", "moonset")

    sunrise = to_uk_time_pretty(sunrise_raw) if sunrise_raw != "—" else "—"
    sunset  = to_uk_time_pretty(sunset_raw)  if sunset_raw  != "—" else "—"
    moonrise = to_uk_time_pretty(moonrise_raw) if moonrise_raw != "—" else "—"
    moonset  = to_uk_time_pretty(moonset_raw)  if moonset_raw  != "—" else "—"

    next_te_name = _safe(te, "data", "tithi", 1, "name")
    next_te_start = to_uk_time_pretty(_safe(te, "data", "tithi", 1, "start")) if next_te_name != "—" else "—"
    next_ta_name = _safe(ta, "data", "tithi", 1, "name")
    next_ta_start = to_uk_time_pretty(_safe(ta, "data", "tithi", 1, "start")) if next_ta_name != "—" else "—"

    try:
        rk, yg, gk = calc_kalam_ranges(sunrise_raw, sunset_raw)
        rahu, yama, guli = f"{rk[0]}–{rk[1]}", f"{yg[0]}–{yg[1]}", f"{gk[0]}–{gk[1]}"
    except Exception:
        rahu = yama = guli = "—"

    try:
        (abhi_s, abhi_e), (bra_s, bra_e) = calc_abhijit_brahma(sunrise_raw, sunset_raw)
    except Exception:
        abhi_s = abhi_e = bra_s = bra_e = "—"

    te_block = (
        f"📿 తెలుగు పంచాంగం – Chelmsford  •  {weekdayTE} ({weekdayEN})\n"
        f"🗓 తిథి: {tithiTE}  |  ✨ నక్షత్రం: {nakTE}\n"
        f"🧘 యోగం: {yogaTE}  |  🔱 కరణం: {karTE}\n"
        f"🌅 {sunrise}  |  🌇 {sunset}\n"
        f"🌙 {moonrise}  |  🌘 {moonset}\n"
        f"☀ అభిజిత్: {abhi_s}–{abhi_e}  |  🌄 బ్రహ్మ: {bra_s}–{bra_e}\n"
        f"➡ రేపు: {next_te_name} @ {next_te_start}\n"
        f"☀ రాహుకాలం {rahu}\n"
        f"🌘 యమగండం {yama}\n"
        f"🕉️ గులిక {guli}"
    )
    ta_block = (
        f"📿 தமிழ் பஞ்சாங்கம் – Chelmsford  •  {weekdayTA} ({weekdayEN})\n"
        f"🗓 திதி: {tithiTA}  |  ✨ நட்சத்திரம்: {nakTA}\n"
        f"🧘 யோகம்: {yogaTA}  |  🔱 கரணம்: {karTA}\n"
        f"🌅 {sunrise}  |  🌇 {sunset}\n"
        f"🌙 {moonrise}  |  🌘 {moonset}\n"
        f"☀ அபிஜித்: {abhi_s}–{abhi_e}  |  🌄 பிரம்ம: {bra_s}–{bra_e}\n"
        f"➡ நாளை: {next_ta_name} @ {next_ta_start}\n"
        f"☀ ராகு {rahu}\n"
        f"🌘 எமகண்டம் {yama}\n"
        f"🕉️ குளிகை {guli}"
    )
    return f"{te_block}\n\n— — — — —\n\n{ta_block}"

def send_whatsapp(token, to, body):
    url = "https://gate.whapi.cloud/messages/text"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"to": to, "body": body}
    r = http_with_retry("POST", url, json=payload, headers=headers)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"WHAPI error {r.status_code}: {r.text}")

# ---- Vercel HTTP handler ----------------------------------------------------

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Secure the cron invocation: Vercel sends Authorization: Bearer <CRON_SECRET>
        # if you define CRON_SECRET as a project env var. (Recommended by Vercel docs.)
        cron_secret = os.getenv("CRON_SECRET", "")
        auth_hdr = self.headers.get("authorization", "")
        if cron_secret:
            if auth_hdr != f"Bearer {cron_secret}":
                self.send_response(401); self.end_headers(); self.wfile.write(b"Unauthorized"); return

        # Required environment variables
        cid  = os.getenv("PROKERALA_CLIENT_ID")
        csec = os.getenv("PROKERALA_CLIENT_SECRET")
        whapi = os.getenv("WHAPI_TOKEN")
        to   = os.getenv("WHATSAPP_TO")
        lat  = os.getenv("LAT", "51.7350")
        lon  = os.getenv("LON", "-0.4696")
        ay   = os.getenv("AYANAMSA", "1")

        errs=[]
        if not cid:  errs.append("Missing PROKERALA_CLIENT_ID")
        if not csec: errs.append("Missing PROKERALA_CLIENT_SECRET")
        if not whapi: errs.append("Missing WHAPI_TOKEN")
        if not to or not re.fullmatch(r"[0-9-]{9,31}", to): errs.append("WHATSAPP_TO must be digits only")
        if errs:
            self.send_response(500); self.end_headers()
            self.wfile.write(("; ".join(errs)).encode()); return

        try:
            token = get_token(cid, csec)
            te = fetch_panchang(token, "te", lat, lon, ay)
            ta = fetch_panchang(token, "ta", lat, lon, ay)
            msg = build_message_compact(te, ta)
            send_whatsapp(whapi, to, msg)

            self.send_response(200); self.end_headers()
            self.wfile.write(b"Sent")
        except Exception as e:
            self.send_response(500); self.end_headers()
            self.wfile.write(f"Error: {e}".encode())