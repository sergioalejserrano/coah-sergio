#!/usr/bin/env python3
"""
upload_workouts.py  —  Sube las sesiones de la semana a Garmin Connect
(aparecen en el reloj Fénix y en el Edge 1040).

EXPERIMENTAL: este es el único pedazo que no se puede probar sin tu cuenta.
Por seguridad arranca en modo DRY_RUN (solo imprime, no sube nada).
Corré primero con DRY_RUN=1, revisá la salida, y cuando confirmemos que
el formato es correcto en tu cuenta, lo activamos en el cron semanal.

Uso local/manual:
    DRY_RUN=1 GARMIN_EMAIL=... GARMIN_PASSWORD=... python scripts/upload_workouts.py
"""
import os, sys, json, datetime

DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"
LTHR = 178   # umbral de FC de Sergio

# Sesiones tipo por zona de FC. (warmup / esfuerzo / cooldown)
def hr_step(name, minutes, lo, hi, kind="interval"):
    return {
        "type": "ExecutableStepDTO",
        "stepType": {"stepTypeId": {"warmup": 1, "cooldown": 2, "interval": 3,
                                    "recovery": 4, "rest": 5}.get(kind, 3),
                     "stepTypeKey": kind},
        "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
        "endConditionValue": minutes * 60,
        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "heart.rate.zone"},
        "targetValueOne": lo, "targetValueTwo": hi,
        "description": name,
    }

def workout(name, steps):
    return {
        "workoutName": name,
        "sport": {"sportTypeId": 2, "sportTypeKey": "cycling"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 2, "sportTypeKey": "cycling"},
            "workoutSteps": [dict(s, stepOrder=i + 1) for i, s in enumerate(steps)],
        }],
    }

# Plantillas según el tipo de día del plan
TEMPLATES = {
    "descanso": None,   # sin workout
    "endurance": lambda: workout("Z2 Endurance · Vegabikes Coach", [
        hr_step("Calentar", 10, 110, 130, "warmup"),
        hr_step("Z2 constante 90-100 rpm", 55, 120, 139, "interval"),
        hr_step("Aflojar", 10, 100, 120, "cooldown"),
    ]),
    "calidad": lambda: workout("Umbral 4x8 · Vegabikes Coach", [
        hr_step("Calentar", 15, 110, 135, "warmup"),
        *[s for _ in range(4) for s in (
            hr_step("Bloque umbral Z4", 8, 162, 178, "interval"),
            hr_step("Recuperar", 4, 110, 130, "recovery"))],
        hr_step("Aflojar", 10, 100, 120, "cooldown"),
    ]),
    "largo": lambda: workout("Fondo Z2 · Vegabikes Coach", [
        hr_step("Calentar", 15, 110, 130, "warmup"),
        hr_step("Fondo Z2", 120, 120, 145, "interval"),
        hr_step("Aflojar", 10, 100, 120, "cooldown"),
    ]),
}


def login():
    from garminconnect import Garmin
    tok = os.environ.get("GARMIN_TOKENSTORE_B64", "").strip()
    if tok:
        g = Garmin(); g.login(tok); return g
    g = Garmin(email=os.environ["GARMIN_EMAIL"], password=os.environ["GARMIN_PASSWORD"])
    g.login(); return g


def build_from_inputs():
    """Construye un workout desde variables de entorno (WK_NAME/WK_DOT/WK_DURATION).
    Lo usa el workflow upload_workout.yml disparado desde la app."""
    name = os.environ["WK_NAME"]
    dot  = (os.environ.get("WK_DOT", "z2") or "z2").lower()
    dur  = int(os.environ.get("WK_DURATION", "60") or 60)
    if dot == "rest" or dur == 0:
        return None
    if dot == "quality":
        return workout(name, [
            hr_step("Calentar", 15, 110, 135, "warmup"),
            *[s for _ in range(4) for s in (
                hr_step("Bloque umbral Z4", 8, 162, 178, "interval"),
                hr_step("Recuperar", 4, 110, 130, "recovery"))],
            hr_step("Aflojar", 10, 100, 120, "cooldown"),
        ])
    # endurance / largo / z2 / race -> warmup + Z2 proporcional + cooldown
    warm, cool = 10, 10
    main_min = max(10, dur - warm - cool)
    lo, hi = (120, 145) if (dot == "race" or dur >= 120) else (120, 139)
    return workout(name, [
        hr_step("Calentar", warm, 110, 130, "warmup"),
        hr_step("Z2 90-100 rpm", main_min, lo, hi, "interval"),
        hr_step("Aflojar", cool, 100, 120, "cooldown"),
    ])


def upload_and_schedule(g, wo, date_iso):
    # Crea el workout y lo agenda en la fecha (aparece en reloj y Edge).
    created = g.garth.connectapi("/workout-service/workout", method="POST", json=wo)
    wid = created.get("workoutId")
    g.garth.connectapi(f"/workout-service/schedule/{wid}", method="POST",
                       json={"date": date_iso})
    return wid


def main():
    # Modo de un solo entrenamiento desde la app (workflow upload_workout.yml).
    if os.environ.get("WK_NAME"):
        wo = build_from_inputs()
        if not wo:
            print("Día de descanso: nada que subir."); return
        date_iso = os.environ.get("WK_DATE") or datetime.date.today().isoformat()
        if DRY_RUN:
            print(f"[DRY_RUN] {date_iso}  {wo['workoutName']}")
            print(json.dumps(wo, ensure_ascii=False)[:400], "…")
            return
        g = login()
        wid = upload_and_schedule(g, wo, date_iso)
        print(f"Subido y agendado {date_iso}: {wo['workoutName']} (id {wid})")
        return

    # day_type por fecha lo decide la app; acá agendamos un ejemplo de la semana.
    plan = [("calidad", 2), ("endurance", 3)]   # (tipo, días-desde-hoy) p.ej. Mié, Jue
    today = datetime.date.today()
    g = None if DRY_RUN else login()
    for day_type, offset in plan:
        tpl = TEMPLATES.get(day_type)
        if not tpl:
            continue
        wo = tpl()
        date_iso = (today + datetime.timedelta(days=offset)).isoformat()
        if DRY_RUN:
            print(f"[DRY_RUN] {date_iso}  {wo['workoutName']}  "
                  f"({len(wo['workoutSegments'][0]['workoutSteps'])} pasos)")
            print(json.dumps(wo, ensure_ascii=False)[:400], "…\n")
        else:
            wid = upload_and_schedule(g, wo, date_iso)
            print(f"Subido y agendado {date_iso}: {wo['workoutName']} (id {wid})")
    if DRY_RUN:
        print("\nModo DRY_RUN. Nada se subió. Poné DRY_RUN=0 para subir de verdad.")


if __name__ == "__main__":
    main()
