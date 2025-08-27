from flask import Flask, request, jsonify
import swisseph as swe
import datetime

app = Flask(__name__)

@app.route("/natal", methods=["POST"])
def natal():
    try:
        data = request.json
        date_str = data.get("date")  # format: YYYY-MM-DD
        time_str = data.get("time")  # format: HH:MM
        lat = float(data.get("lat"))
        lon = float(data.get("lon"))

        # Parse date/time
        year, month, day = map(int, date_str.split("-"))
        hour, minute = map(int, time_str.split(":"))
        utc = datetime.datetime(year, month, day, hour, minute)

        # Swiss Ephemeris setup
        swe.set_ephe_path("./se")  # path to ephemeris files (add later)
        jd = swe.julday(year, month, day, hour + minute/60.0)

        # Planets to calculate
        planets = [swe.SUN, swe.MOON, swe.MERCURY, swe.VENUS, swe.MARS,
                   swe.JUPITER, swe.SATURN, swe.URANUS, swe.NEPTUNE, swe.PLUTO]

        results = {}
        for pl in planets:
            lon, lat_p, dist = swe.calc_ut(jd, pl)[0:3]
            results[swe.get_planet_name(pl)] = round(lon, 2)

        return jsonify({"ok": True, "planets": results})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
