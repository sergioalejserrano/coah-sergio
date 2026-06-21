#!/usr/bin/env python3
"""
fetch_garmin.py  —  Robot de datos para Coach Vegabikes.

Corre en GitHub Actions cada pocas horas. Hace:
  1) Inicia sesión en Garmin Connect (token o usuario+clave desde Secrets).
  2) Baja: readiness, body battery 7 días, sueño, resumen diario, actividades.
  3) (Opcional) Lee el calendario de Google por URL iCal privada.
  4) Arma un JSON con la forma que espera la app.
  5) Lo CIFRA con AES-256-GCM (clave derivada de tu frase con PBKDF2).
  6) Escribe data/garmin.enc  (ilegible sin tu frase) y data/meta.json.

Nada sensible queda en texto plano. La clave de Garmin y la frase viven
solo en GitHub Secrets (cifrados), nunca en el repo ni en la app.
"""
import os, sys, json, base64, datetime, urllib.request
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PBKDF2_ITERATIONS = 200000          # debe coincidir con la app (index.html)
DOW_ES = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"]

READINESS_MSG = {
    "LET_YOUR_BODY_RECOVER": "Dejá que tu cuerpo se recupere",
    "POOR": "Recuperación baja",
    "MODERATE": "Recuperación moderada",
    "GOOD_TO_GO": "Listo para entrenar",
    "READY": "Listo para entrenar",
    "PRIME": "En punto óptimo",
    "HIGH": "Muy bien recuperado",
}


# --------------------------------------------------------------------------
def log(*a):
    print(*a, flush=True)


def get_env(name, required=False):
    v = os.environ.get(name, "").strip()
    if required and not v:
        log(f"ERROR: falta la variable {name}")
        sys.exit(1)
    return v


# --------------------------------------------------------------------------
def login_garmin():
    """Devuelve un cliente Garmin autenticado."""
    from garminconnect import Garmin

    token_b64 = get_env("GARMIN_TOKENSTORE_B64")
    email     = get_env("GARMIN_EMAIL")
    password  = get_env("GARMIN_PASSWORD")

    # Intentar con token primero
    if token_b64:
        log("Intentando login con token…")
        try:
            g = Garmin()
            # El token puede ser base64 de JSON o JSON directo
            import base64
            try:
                decoded = base64.b64decode(token_b64).decode()
            except Exception:
                decoded = token_b64
            g.client.loads(decoded)
            # Verificar que el token sea válido
            if g.client.is_authenticated():
                log("Token válido, login OK.")
                return g
            else:
                log("Token no válido, cayendo a usuario+clave…")
        except Exception as e:
            log(f"Token falló ({e}), cayendo a usuario+clave…")

    # Login con usuario + clave (salta endpoints móviles que pueden estar bloqueados)
    if email and password:
        log("Login Garmin con usuario y clave…")
        g = Garmin(email=email, password=password)
        g.client.skip_strategies = {"mobile+cffi", "mobile+requests"}
        g.login()
        log("Login con usuario+clave OK.")
        return g

    log("ERROR: definí GARMIN_TOKENSTORE_B64 o GARMIN_EMAIL + GARMIN_PASSWORD en los Secrets")
    sys.exit(1)


def first(d, *keys, default=None):
    """Devuelve el primer key presente y no-nulo en el dict d."""
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def safe(fn, label, default=None):
    try:
        return fn()
    except Exception as e:               # noqa: BLE001
        log(f"  aviso: {label} falló ({e})")
        return default


# --------------------------------------------------------------------------
def build_payload(g):
    today = datetime.date.today()
    iso = today.isoformat()

    # ----- Training Readiness -----
    rd_raw = safe(lambda: g.get_training_readiness(iso), "readiness", []) or []
    rd = rd_raw[0] if isinstance(rd_raw, list) and rd_raw else (rd_raw or {})
    fb = rd.get("feedbackShort", "")
    today_blk = {
        "readiness": rd.get("score", 0),
        "readinessLevel": rd.get("level", ""),
        "readinessMsg": READINESS_MSG.get(fb, READINESS_MSG.get(rd.get("level", ""), "—")),
        "recoveryMin": rd.get("recoveryTime", 0),
        "hrvWeekly": rd.get("hrvWeeklyAverage", 0),
        "sleepScore": rd.get("sleepScore", 0),
        "factors": {
            "sueno": rd.get("sleepScoreFactorPercent", 0),
            "recuperacion": rd.get("recoveryTimeFactorPercent", 0),
            "carga": rd.get("acwrFactorPercent", 0),
            "estres": rd.get("stressHistoryFactorPercent", 0),
            "hrv": rd.get("hrvFactorPercent", 0),
        },
    }

    # ----- Resumen diario (BB actual, FC reposo) -----
    stats = safe(lambda: g.get_stats(iso), "resumen diario", {}) or {}
    today_blk["bodyBattery"] = first(stats, "bodyBatteryMostRecentValue",
                                     "bodyBatteryAtWakeTime", default=0)
    today_blk["restingHr"] = first(stats, "restingHeartRate", default=0)

    # ----- Sueño -----
    sl_raw = safe(lambda: g.get_sleep_data(iso), "sueño", {}) or {}
    dto = sl_raw.get("dailySleepDTO", sl_raw) if isinstance(sl_raw, dict) else {}
    secs = lambda k: int(dto.get(k, 0) or 0)
    sleep_blk = {
        "deepMin": round(secs("deepSleepSeconds") / 60),
        "lightMin": round(secs("lightSleepSeconds") / 60),
        "remMin": round(secs("remSleepSeconds") / 60),
        "awake": round(secs("awakeSleepSeconds") / 60),
    }
    total_sleep_s = secs("sleepTimeSeconds")
    today_blk["sleepHrs"] = round(total_sleep_s / 3600, 1) if total_sleep_s else 0
    if not today_blk["sleepScore"]:
        sc = dto.get("sleepScores", {})
        today_blk["sleepScore"] = (sc.get("overall", {}) or {}).get("value", 0) if isinstance(sc, dict) else 0

    # ----- Body Battery 7 días (pico diario) -----
    bb7 = []
    start = (today - datetime.timedelta(days=6)).isoformat()
    bb_raw = safe(lambda: g.get_body_battery(start, iso), "body battery", []) or []
    for day in bb_raw[-7:]:
        vals = day.get("bodyBatteryValuesArray", []) or []
        peak = max((v[1] for v in vals if isinstance(v, list) and len(v) > 1), default=0)
        try:
            wd = datetime.date.fromisoformat(day.get("date")).weekday()
            dlabel = DOW_ES[(wd + 1) % 7]
        except Exception:
            dlabel = ""
        bb7.append({"d": dlabel, "v": peak})

    # ----- Última actividad -----
    acts = safe(lambda: g.get_activities(0, 1), "actividades", []) or []
    last = {}
    if acts:
        a = acts[0]
        dist_m = first(a, "distance", default=0) or 0
        dur_s = first(a, "duration", "elapsedDuration", default=0) or 0
        last = {
            "name": first(a, "activityName", default="Actividad"),
            "date": (first(a, "startTimeLocal", default="") or "")[:10],
            "km": round(dist_m / 1000, 1),
            "min": round(dur_s / 60),
            "hrAvg": round(first(a, "averageHR", default=0) or 0),
            "hrMax": round(first(a, "maxHR", default=0) or 0),
            "elevM": round(first(a, "elevationGain", default=0) or 0),
            "watts": round(first(a, "avgPower", "averagePower", default=0) or 0),
            "cad": round(first(a, "averageBikingCadenceInRevPerMinute",
                               "averageRunningCadenceInStepsPerMinute", default=0) or 0),
        }

    # ----- Calendario (iCal privado, opcional) -----
    calendar = fetch_calendar(get_env("CALENDAR_ICAL_URL"))

    return {
        "fetchedAt": datetime.datetime.now().astimezone().isoformat(timespec="minutes"),
        "source": "live",
        "today": today_blk,
        "sleep": sleep_blk,
        "bb7": bb7,
        "lastActivity": last,
        "calendar": calendar,
    }


# --------------------------------------------------------------------------
def fetch_calendar(url):
    """Lee un iCal privado y devuelve días ocupados de los próximos 14 días."""
    out = {"busyPM": [], "busyAll": [], "events": []}
    if not url:
        return out
    try:
        from icalendar import Calendar
        with urllib.request.urlopen(url, timeout=30) as r:
            cal = Calendar.from_ical(r.read())
        today = datetime.date.today()
        horizon = today + datetime.timedelta(days=21)
        for comp in cal.walk("VEVENT"):
            start = comp.get("dtstart").dt
            summary = str(comp.get("summary", ""))
            if isinstance(start, datetime.datetime):
                d = start.date()
                if today <= d <= horizon:
                    out["events"].append({"date": d.isoformat(), "summary": summary,
                                          "hour": start.hour})
                    if start.hour >= 17:
                        out["busyPM"].append(d.isoformat())
            elif isinstance(start, datetime.date):     # evento de día completo
                if today <= start <= horizon:
                    out["busyAll"].append(start.isoformat())
                    out["events"].append({"date": start.isoformat(),
                                          "summary": summary, "allDay": True})
    except Exception as e:                              # noqa: BLE001
        log(f"  aviso: calendario falló ({e})")
    # quitar duplicados
    out["busyPM"] = sorted(set(out["busyPM"]))
    out["busyAll"] = sorted(set(out["busyAll"]))
    return out


# --------------------------------------------------------------------------
def encrypt(plaintext, passphrase):
    salt = os.urandom(16)
    iv = os.urandom(12)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=PBKDF2_ITERATIONS)
    key = kdf.derive(passphrase.encode())
    ct = AESGCM(key).encrypt(iv, plaintext.encode(), None)
    return base64.b64encode(salt + iv + ct).decode()


def main():
    passphrase = get_env("DATA_PASSPHRASE", required=True)
    g = login_garmin()
    log("Sesión Garmin OK. Bajando datos…")
    payload = build_payload(g)
    log(f"Readiness {payload['today'].get('readiness')} · "
        f"BB {payload['today'].get('bodyBattery')} · "
        f"actividad {payload['lastActivity'].get('name','—')}")

    os.makedirs("data", exist_ok=True)
    blob = encrypt(json.dumps(payload, ensure_ascii=False), passphrase)
    with open("data/garmin.enc", "w") as f:
        f.write(blob)
    # meta NO sensible (para mostrar "última actualización" sin descifrar)
    with open("data/meta.json", "w") as f:
        json.dump({"fetchedAt": payload["fetchedAt"]}, f)
    log(f"Escrito data/garmin.enc ({len(blob)} bytes cifrados). Listo.")


if __name__ == "__main__":
    main()
