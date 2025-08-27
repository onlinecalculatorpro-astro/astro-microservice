from flask import Flask, request, jsonify
import swisseph as swe
import datetime

app = Flask(__name__)

# ---- Config -------------------------------------------------
# If you later upload ephemeris files, set an absolute path here.
# For now we let Swiss Ephemeris use its built-in computation.
# swe.set_ephe_path("/app/se")  # e.g., when you add ephemeris files

FLAGS = swe.FLG_SWIEPH | swe.FLG_SPEED  # Swiss ephemeris, include speeds

SIGNS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"
]

PLANETS = [
    swe.SUN, swe.MOON, swe.MERCURY, swe.VENUS, swe.MARS,
    swe.JUPITER, swe.SATURN, swe.URANUS, swe.NEPTUNE, swe.PLUTO
]

# ---- Helpers ------------------------------------------------
def norm360(x: float) -> float:
    """Normalize degrees to [0, 360)."""
    x = float(x) % 360.0
    if x < 0:
        x += 360.0
    return x

def to_sign(deg: float):
    """Return (sign_name, degree_within_sign) for an ecliptic longitude."""
    d = norm360(deg)
    idx = int(d // 30)
    within = d - idx * 30
    return SIGNS[idx], within

def parse_ymd(date_str: str):
    """YYYY-MM-DD -> (y, m, d). Raise ValueError on bad format."""
    y, m, d = map(int, date_str.split("-"))
    return y, m, d

def parse_hm(time_str: str):
    """HH:MM -> (h, m). Raise ValueError on bad format."""
    h, m = map(int, time_str.split(":"))
    return h, m

# ---- Routes -------------------------------------------------
@app.route("/")
def root():
    return jsonify({
        "service": "astro-microservice",
        "status": "ok",
        "ephemeris_path": "(default)",
        "endpoints": {
            "health": "/healthz",
            "natal": {"method": "POST", "path": "/natal"}
        }
    })

@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})

@app.route("/natal", methods=["POST"])
def natal():
    try:
        if not request.is_json:
            return jsonify({"ok": False, "error": "Content-Type must be application/json"}), 400

        data = request.get_json(silent=True) or {}

        # Required
        date_str = data.get("date")  # "YYYY-MM-DD" (UTC date, unless tz_offset provided)
        time_str = data.get("time")  # "HH:MM" in 24h
        lat = data.get("lat")
        lon = data.get("lon")

        if not date_str or not time_str or lat is None or lon is None:
            return jsonify({
                "ok": False,
                "error": "Missing required fields: 'date', 'time', 'lat', 'lon'"
            }), 400

        # Optional: timezone offset in HOURS (e.g., 5.5 for IST). Default UTC.
        tz_offset = float(data.get("tz_offset", 0))

        # Parse
        year, month, day = parse_ymd(date_str)
        hour, minute = parse_hm(time_str)
        lat = float(lat)
        lon = float(lon)

        # Build UT (UTC) time: local time minus tz_offset
        # (e.g., 14:30 local with tz_offset +5.5 -> 09:00 UT)
        ut_hours = hour + minute / 60.0 - tz_offset

        # Julian day in UT
        jd_ut = swe.julday(year, month, day, ut_hours)

        # ---- Planets (longitudes) -----------------------------------------
        planets_out = {}
        for pl in PLANETS:
            try:
                pos, ret = swe.calc_ut(jd_ut, pl, FLAGS)
                lon_deg = float(pos[0])
                sign_name, deg_in_sign = to_sign(lon_deg)
                planets_out[swe.get_planet_name(pl)] = {
                    "lon": round(norm360(lon_deg), 4),
                    "sign": sign_name,
                    "deg_in_sign": round(deg_in_sign, 4)
                }
            except Exception as e:
                planets_out[str(pl)] = {"error": str(e)}

        # ---- Houses / Angles (Placidus) -----------------------------------
        # Returns (cusps[1..12], ascmc[0..9])  where ascmc[0]=Asc, ascmc[1]=MC
        # Lon is geographic longitude (east positive). Swiss Ephemeris expects east-positive,
        # which matches common GIS. If yours is west-positive, negate it.
        try:
            cusps, ascmc = swe.houses_ex(jd_ut, lat, lon, b'P', FLAGS)
            houses = [None] * 12
            for i in range(12):
                c = float(cusps[i+1])  # cusps array is 1-indexed
                houses[i] = round(norm360(c), 4)

            asc_lon = float(ascmc[0])
            mc_lon = float(ascmc[1])
            asc_sign, asc_deg = to_sign(asc_lon)
            mc_sign, mc_deg = to_sign(mc_lon)

            angles = {
                "ASC": {
                    "lon": round(norm360(asc_lon), 4),
                    "sign": asc_sign,
                    "deg_in_sign": round(asc_deg, 4)
                },
                "MC": {
                    "lon": round(norm360(mc_lon), 4),
                    "sign": mc_sign,
                    "deg_in_sign": round(mc_deg, 4)
                }
            }
        except Exception as e:
            houses = None
            angles = {"error": str(e)}

        # Quick “top 3” summary
        top3 = {
            "sun_sign": planets_out.get("Sun", {}).get("sign"),
            "moon_sign": planets_out.get("Moon", {}).get("sign"),
            "asc_sign": angles.get("ASC", {}).get("sign") if isinstance(angles, dict) else None
        }

        return jsonify({
            "ok": True,
            "meta": {
                "jd_ut": jd_ut,
                "tz_offset_hours": tz_offset
            },
            "planets": planets_out,
            "angles": angles,
            "houses": houses,
            "summary": top3
        })

    except ValueError as ve:
        return jsonify({"ok": False, "error": f"Bad input: {ve}"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# Local run (Render/Heroku use gunicorn via Procfile)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
