from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import datetime as dt

# Swiss Ephemeris (pyswisseph)
import swisseph as swe

app = Flask(__name__)
CORS(app)

# -----------------------------
# Swiss Ephemeris configuration
# -----------------------------
# Allow an override path for .se1/.se2 files if you later mount them.
EPHE_PATH = os.environ.get("EPHE_PATH", "").strip()
if EPHE_PATH:
    swe.set_ephe_path(EPHE_PATH)  # Safe if path exists; else SwissEphem uses built-in files

SIGNS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"
]

PLANETS = [
    swe.SUN, swe.MOON, swe.MERCURY, swe.VENUS, swe.MARS,
    swe.JUPITER, swe.SATURN, swe.URANUS, swe.NEPTUNE, swe.PLUTO,
]

PLANET_NAMES = {p: swe.get_planet_name(p) for p in PLANETS}


def sign_of(longitude_deg: float) -> str:
    """Return zodiac sign name for a 0..360 longitude."""
    i = int((longitude_deg % 360.0) // 30)
    return SIGNS[i]


def parse_input(json):
    """Validate and normalize incoming JSON. Raises ValueError with human messages."""
    if not isinstance(json, dict):
        raise ValueError("Expected JSON object payload.")

    # Required string fields
    for k in ("date", "time"):
        if not json.get(k):
            raise ValueError(f"Missing required field: '{k}'")

    date_str = str(json["date"])  # 'YYYY-MM-DD'
    time_str = str(json["time"])  # 'HH:MM' (24h); seconds optional "HH:MM:SS"

    # Lat/Lon: Swiss Ephem uses east-longitudes positive, west negative.
    # Provide clear error if not numbers.
    try:
        lat = float(json.get("lat"))
        lon = float(json.get("lon"))
    except Exception:
        raise ValueError("Fields 'lat' and 'lon' must be numbers.")

    # Optional seconds
    parts = time_str.split(":")
    if len(parts) not in (2, 3):
        raise ValueError("Time must be HH:MM or HH:MM:SS (24-hour).")
    hour, minute = int(parts[0]), int(parts[1])
    second = int(parts[2]) if len(parts) == 3 else 0

    # Parse date
    try:
        year, month, day = map(int, date_str.split("-"))
    except Exception:
        raise ValueError("Date must be YYYY-MM-DD.")

    # Build a naive UTC datetime (expecting the provided time is already UTC).
    # If you need timezone conversion later, pass a tz offset from the client.
    utc_dt = dt.datetime(year, month, day, hour, minute, second)

    # Convert to Julian Day (UT); Swiss Ephem wants fractional hours
    ut_hours = hour + minute / 60.0 + second / 3600.0
    jd_ut = swe.julday(year, month, day, ut_hours)

    return {
        "jd_ut": jd_ut,
        "lat": lat,
        "lon": lon,
        "dt": utc_dt.isoformat() + "Z",
    }


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "astro-microservice",
        "status": "ok",
        "endpoints": {
            "health": "/healthz",
            "natal": {"path": "/natal", "method": "POST"}
        },
        "ephemeris_path": EPHE_PATH or "(default)"
    }), 200


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"}), 200


@app.route("/natal", methods=["POST"])
def natal():
    """
    Request JSON:
    {
      "date": "1995-06-15",
      "time": "03:15",          # assumed UTC; or pass already converted time
      "lat": 19.0760,           # latitude (+N, -S)
      "lon": 72.8777            # longitude (+E, -W)
    }
    """
    try:
        data = request.get_json(silent=True)
        parsed = parse_input(data)

        jd = parsed["jd_ut"]
        lat = parsed["lat"]
        lon = parsed["lon"]

        # Planetary longitudes (tropical, geocentric, apparent, true node etc. default flags)
        planets = {}
        for body in PLANETS:
            # swe.calc_ut returns (lon, lat, dist, speed_lon, speed_lat, speed_dist)
            lon_deg, lat_deg, _dist = swe.calc_ut(jd, body)[:3]
            name = PLANET_NAMES[body]
            planets[name] = {
                "lon": round(lon_deg % 360.0, 6),
                "sign": sign_of(lon_deg),
            }

        # Houses + angles (Placidus by default). Returns (cusps[1..12], ascmc[ASC, MC, ARMC, Vertex, Equatorial Asc, Co-Asc1, Co-Asc2, Polar Asc])
        cusps, ascmc = swe.houses(jd, lat, lon)
        asc = ascmc[0] % 360.0
        mc = ascmc[1] % 360.0

        angles = {
            "Ascendant": {"lon": round(asc, 6), "sign": sign_of(asc)},
            "MC": {"lon": round(mc, 6), "sign": sign_of(mc)},
        }

        houses = {f"H{i}": round((cusps[i - 1] % 360.0), 6) for i in range(1, 13)}

        return jsonify({
            "ok": True,
            "input": {
                "datetime_utc": parsed["dt"],
                "lat": lat,
                "lon": lon
            },
            "planets": planets,
            "angles": angles,
            "houses": houses
        }), 200

    except ValueError as ve:
        return jsonify({"ok": False, "error": str(ve)}), 400
    except Exception as e:
        # For production you might want to hide raw exception details.
        return jsonify({"ok": False, "error": f"Server error: {str(e)}"}), 500


if __name__ == "__main__":
    # Local dev; in Render we use gunicorn (see Procfile)
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
