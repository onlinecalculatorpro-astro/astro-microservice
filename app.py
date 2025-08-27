from __future__ import annotations
import os
import math
from typing import Tuple, Dict, Any
from flask import Flask, request, jsonify
import swisseph as swe

app = Flask(__name__)

# --- Ephemeris path (optional) ------------------------------------------------
# If you upload Swiss Ephemeris .se1/.se2 etc. files, point to that folder via
# env SWEPHEM_PATH. If not present, pyswisseph will use its internal data.
EPH_PATH = os.getenv("SWEPHEM_PATH", "")
try:
    if EPH_PATH:
        swe.set_ephe_path(EPH_PATH)
except Exception:
    # Don't fail the app if path can't be set
    pass

# --- Helpers ------------------------------------------------------------------
SIGNS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"
]

def norm360(x: float) -> float:
    """Normalize any angle to [0, 360)."""
    return x % 360.0

def to_sign(lon: float) -> Tuple[str, float]:
    """Return (sign_name, degrees_within_sign) for an ecliptic longitude."""
    lon = norm360(lon)
    idx = int(lon // 30)
    deg_in_sign = lon - idx * 30
    return SIGNS[idx], deg_in_sign

def parse_inputs(payload: Dict[str, Any]) -> Tuple[int, int, int, float, float, float]:
    """
    Extract required inputs and return (Y, M, D, hour_utc, lat, lon).
    Assumes date/time are LOCAL and converts to UTC using tz_offset (hours).
    """
    missing = [k for k in ("date", "time", "lat", "lon") if k not in payload]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}")

    # Date/time
    try:
        y, m, d = [int(part) for part in str(payload["date"]).split("-")]
    except Exception:
        raise ValueError("Invalid 'date' format. Use YYYY-MM-DD.")

    try:
        hh, mm = [int(part) for part in str(payload["time"]).split(":")]
    except Exception:
        raise ValueError("Invalid 'time' format. Use HH:MM (24h).")

    # Coordinates
    try:
        lat = float(payload["lat"])
        lon = float(payload["lon"])
    except Exception:
        raise ValueError("'lat' and 'lon' must be numbers.")

    # Timezone offset (local minus UTC). Default 0, India is +5.5, etc.
    tz = float(payload.get("tz_offset", 0.0))

    # Convert local time to UTC hours for Julian Day in UT:
    # local = UTC + tz  =>  UTC = local - tz
    hour_local = hh + (mm / 60.0)
    hour_utc = hour_local - tz

    return y, m, d, hour_utc, lat, lon, tz

# --- Routes -------------------------------------------------------------------
@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "service": "astro-microservice",
        "status": "ok",
        "ephemeris_path": EPH_PATH or "(default)",
        "endpoints": {
            "health": "/healthz",
            "natal": {"method": "POST", "path": "/natal"}
        }
    })

@app.route("/healthz", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/natal", methods=["POST"])
def natal():
    try:
        data = request.get_json(force=True, silent=False) or {}
    except Exception:
        return jsonify({"ok": False, "error": "Invalid JSON body."}), 400

    # Parse inputs
    try:
        y, m, d, hour_utc, lat, lon, tz = parse_inputs(data)
    except ValueError as ve:
        return jsonify({"ok": False, "error": str(ve)}), 400

    # Julian Day (UT)
    try:
        jd_ut = swe.julday(y, m, d, hour_utc)  # UT because we passed UTC hour
    except Exception as e:
        return jsonify({"ok": False, "error": f"julday failed: {e}"}), 500

    # --- Planets --------------------------------------------------------------
    planet_ids = [
        swe.SUN, swe.MOON, swe.MERCURY, swe.VENUS, swe.MARS,
        swe.JUPITER, swe.SATURN, swe.URANUS, swe.NEPTUNE, swe.PLUTO
    ]
    planets = {}
    for pid in planet_ids:
        try:
            res, _ = swe.calc_ut(jd_ut, pid)  # returns (lon, lat, dist, ...)
            lon_ecl = norm360(res[0])
            sign, deg_in_sign = to_sign(lon_ecl)
            name = swe.get_planet_name(pid)
            planets[name] = {
                "lon": round(lon_ecl, 4),
                "sign": sign,
                "deg_in_sign": round(deg_in_sign, 4)
            }
        except Exception as e:
            # Keep the rest even if one planet fails
            name = swe.get_planet_name(pid)
            planets[name] = {"error": str(e)}

        # --- Houses / Angles (Placidus) -----------------------------------------
    houses = None
    angles = {}

    def _normalize_cusps(raw):
        """Return a list of 12 cusps in degrees [C1..C12], regardless of shape."""
        if not raw:
            return None
        # Convert tuple -> list
        cs = list(raw)
        # Case A: 13 long with dummy at 0 (classic pyswisseph)
        if len(cs) >= 13:
            vals = cs[1:13]
        # Case B: exactly 12 long (0..11)
        elif len(cs) == 12:
            vals = cs[:]
        else:
            # Unexpected shape – bail safely
            return None
        return [round(norm360(v), 4) for v in vals]

    def _angles_from_ascmc(raw):
        """Extract ASC/MC safely from ascmc array."""
        if not raw:
            return {"error": "ascmc unavailable"}
        arr = list(raw)
        # Most builds: ASC at idx 0, MC at idx 1; but guard by length
        asc_lon = norm360(arr[0]) if len(arr) > 0 else None
        mc_lon  = norm360(arr[1]) if len(arr) > 1 else None
        out = {}
        if asc_lon is not None:
            s, d = to_sign(asc_lon)
            out["ASC"] = {"lon": asc_lon, "sign": s, "deg_in_sign": round(d, 4)}
        if mc_lon is not None:
            s, d = to_sign(mc_lon)
            out["MC"] = {"lon": mc_lon, "sign": s, "deg_in_sign": round(d, 4)}
        if not out:
            out["error"] = "ASC/MC not present in ascmc"
        return out

    try:
        # Prefer houses_ex for better compatibility; fall back to houses
        cusps = ascmc = None
        try:
            # flags: use Swiss ephemeris; you can OR more flags if needed
            cusps, ascmc = swe.houses_ex(jd_ut, swe.FLG_SWIEPH, lat, lon, b'P')
        except Exception:
            cusps, ascmc = swe.houses(jd_ut, lat, lon, b'P')

        houses = _normalize_cusps(cusps)
        angles = _angles_from_ascmc(ascmc)

        if houses is None:
            raise RuntimeError("House cusps array had unexpected length/shape.")

    except Exception as e:
        houses = None
        angles = {"error": str(e)}

    # --- Summary --------------------------------------------------------------
    sun_sign = planets.get("Sun", {}).get("sign")
    moon_sign = planets.get("Moon", {}).get("sign")
    asc_sign = None
    try:
        if "ASC" in angles and "sign" in angles["ASC"]:
            asc_sign = angles["ASC"]["sign"]
    except Exception:
        asc_sign = None

    return jsonify({
        "ok": True,
        "meta": {
            "jd_ut": jd_ut,
            "tz_offset_hours": tz
        },
        "planets": planets,
        "houses": houses,
        "angles": angles,
        "summary": {
            "sun_sign": sun_sign,
            "moon_sign": moon_sign,
            "asc_sign": asc_sign
        }
    })

# Local run (Render uses gunicorn with Procfile)
if __name__ == "__main__":
    # Useful for local debugging; on Render this is ignored (gunicorn used)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
