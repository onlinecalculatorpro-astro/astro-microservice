from flask import Flask, request, jsonify
from flask_cors import CORS
import swisseph as swe           # package name: pyswisseph
import datetime as dt
import os
import math

app = Flask(__name__)
CORS(app)

PLANETS = [
    swe.SUN, swe.MOON, swe.MERCURY, swe.VENUS, swe.MARS,
    swe.JUPITER, swe.SATURN, swe.URANUS, swe.NEPTUNE, swe.PLUTO
]

# Use Moshier ephemeris so we don't need ephemeris files on disk
EPH_FLAGS = swe.FLG_MOSEPH | swe.FLG_SPEED

def parse_tz_offset(tz_raw):
    """
    Accepts:
      None -> 0 minutes
      "+05:30" / "-07:00" -> minutes (int)
      "330" / "-420" -> minutes (stringified int)
      330 / -420 -> minutes (int)
    """
    if tz_raw is None:
        return 0
    if isinstance(tz_raw, (int, float)):
        return int(tz_raw)
    tz_raw = str(tz_raw).strip()
    if tz_raw.lstrip("-").isdigit():       # "330" or "-420"
        return int(tz_raw)
    # "+hh:mm" / "-hh:mm"
    try:
        sign = 1 if tz_raw[0] == "+" else -1
        hh, mm = tz_raw[1:].split(":")
        return sign * (int(hh) * 60 + int(mm))
    except Exception:
        return 0

def to_utc(date_str, time_str, tz_raw):
    """
    Returns (utc_datetime, julian_day_ut)
    - date: "YYYY-MM-DD"
    - time: "HH:MM" (24h)
    - tz:   minutes or "+HH:MM"/"-HH:MM"; positive = east of UTC
    """
    year, month, day = map(int, date_str.split("-"))
    hour, minute = map(int, time_str.split(":"))
    local_dt = dt.datetime(year, month, day, hour, minute)
    offset_min = parse_tz_offset(tz_raw)
    # Local time = UTC + offset  =>  UTC = local - offset
    utc_dt = local_dt - dt.timedelta(minutes=offset_min)
    h_float = utc_dt.hour + utc_dt.minute / 60.0 + utc_dt.second / 3600.0
    jd_ut = swe.julday(utc_dt.year, utc_dt.month, utc_dt.day, h_float)
    return utc_dt, jd_ut

@app.get("/")
def root():
    return jsonify(ok=True, service="astro-microservice")

@app.get("/healthz")
def healthz():
    return jsonify(ok=True)

@app.post("/natal")
def natal():
    try:
        data = request.get_json(silent=True) or {}
        date_str = data.get("date")     # "YYYY-MM-DD"
        time_str = data.get("time")     # "HH:MM"
        lat = data.get("lat")
        lon = data.get("lon")
        tz = data.get("tz")             # optional: "+05:30" / -420 / etc.

        # Basic validation
        if not date_str or not time_str or lat is None or lon is None:
            return jsonify(ok=False, error="Missing required fields: date, time, lat, lon"), 400

        lat = float(lat)
        lon = float(lon)

        # Compute UTC datetime & Julian Day
        utc_dt, jd_ut = to_utc(date_str, time_str, tz)

        # Planets
        planets = {}
        for pl in PLANETS:
            name = swe.get_planet_name(pl)
            resp = swe.calc_ut(jd_ut, pl, EPH_FLAGS)
            # resp -> (longitude, latitude, distance, speed_long, speed_lat, speed_dist)
            lon_ecl = float(resp[0])
            planets[name] = round(lon_ecl % 360.0, 2)

        # Houses / angles (Placidus)
        # swe.houses_ex returns (cusps[1..12], ascmc[1..10]) where:
        # ascmc[0]=Asc, [1]=MC in some builds; with pyswisseph it's index 0=Asc, 1=MC
        cusps, ascmc = swe.houses_ex(jd_ut, EPH_FLAGS, lat, lon, b'P')
        asc = float(ascmc[0]) % 360.0
        mc  = float(ascmc[1]) % 360.0

        return jsonify({
            "ok": True,
            "datetime_utc": utc_dt.replace(tzinfo=dt.timezone.utc).isoformat(),
            "jd_ut": jd_ut,
            "planets": planets,
            "angles": {
                "Asc": round(asc, 2),
                "MC":  round(mc, 2)
            }
        })

    except Exception as e:
        # Surface a concise error
        return jsonify(ok=False, error=str(e)), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
