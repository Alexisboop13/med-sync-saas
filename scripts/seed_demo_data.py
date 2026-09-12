"""
scripts/seed_demo_data.py
──────────────────────────────────────────────────────────────────────────────
Crea datos de demo (4 doctores, 10 pacientes, 15 citas) contra la API real de
Med-Sync — local o producción — usando los mismos endpoints que usa el
frontend. Nunca toca la base de datos directamente.

Diseñado para correrse en fases separadas, cada una con confirmación
explícita, igual que las migraciones de Alembic:

    python scripts/seed_demo_data.py --api-base <URL> check
    python scripts/seed_demo_data.py --api-base <URL> doctors     --confirm
    python scripts/seed_demo_data.py --api-base <URL> patients    --confirm
    python scripts/seed_demo_data.py --api-base <URL> appointments --confirm

Seguridad:
  - NUNCA modifica al usuario owner. Solo se usa su sesión (login) para
    autenticar las llamadas; no se llama ningún endpoint de escritura sobre
    /users para el owner.
  - La contraseña del owner se pide de forma interactiva (getpass), nunca se
    pasa como argumento ni se guarda en ningún archivo.
  - Antes de crear un doctor o paciente, busca si ya existe algo con nombre
    similar y, si lo encuentra, lo reutiliza en vez de duplicar.
  - Cada fase de escritura requiere el flag --confirm Y una confirmación
    escrita ("SI") en la terminal.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import random
import secrets
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

import httpx

DOCTORS = [
    {
        "full_name": "Ana García López",
        "specialty": "Medicina General",
        "working_hours": {
            "mon": [{"start": "09:00", "end": "13:00"}, {"start": "15:00", "end": "18:00"}],
            "tue": [{"start": "09:00", "end": "13:00"}, {"start": "15:00", "end": "18:00"}],
            "wed": [{"start": "09:00", "end": "13:00"}],
            "thu": [{"start": "09:00", "end": "13:00"}, {"start": "15:00", "end": "18:00"}],
            "fri": [{"start": "09:00", "end": "14:00"}],
        },
    },
    {
        "full_name": "Bruno Martínez Ruiz",
        "specialty": "Odontología",
        "working_hours": {
            "mon": [{"start": "10:00", "end": "14:00"}, {"start": "16:00", "end": "19:00"}],
            "tue": [{"start": "10:00", "end": "14:00"}],
            "wed": [{"start": "10:00", "end": "14:00"}, {"start": "16:00", "end": "19:00"}],
            "thu": [{"start": "10:00", "end": "14:00"}],
            "fri": [{"start": "10:00", "end": "14:00"}, {"start": "16:00", "end": "19:00"}],
        },
    },
    {
        "full_name": "Carla Fernández Soto",
        "specialty": "Pediatría",
        "working_hours": {
            "mon": [{"start": "08:00", "end": "12:00"}],
            "tue": [{"start": "08:00", "end": "12:00"}, {"start": "14:00", "end": "17:00"}],
            "wed": [{"start": "08:00", "end": "12:00"}],
            "thu": [{"start": "08:00", "end": "12:00"}, {"start": "14:00", "end": "17:00"}],
            "fri": [{"start": "08:00", "end": "12:00"}],
        },
    },
    {
        "full_name": "Diego Hernández Vega",
        "specialty": "Dermatología",
        "working_hours": {
            "mon": [{"start": "11:00", "end": "15:00"}],
            "tue": [{"start": "11:00", "end": "15:00"}, {"start": "17:00", "end": "20:00"}],
            "wed": [{"start": "11:00", "end": "15:00"}],
            "thu": [{"start": "11:00", "end": "15:00"}, {"start": "17:00", "end": "20:00"}],
            "fri": [{"start": "11:00", "end": "16:00"}],
        },
    },
]

PATIENTS = [
    dict(full_name="Sofía Ramírez Torres", phone="+52 55 1234 5678", email="sofia.ramirez@example.com",
         date_of_birth="2015-03-12", gender="female", address="Av. Insurgentes Sur 1234, CDMX",
         blood_type="O+", allergies={"medications": [], "foods": ["cacahuate"], "environmental": []}),
    dict(full_name="Mateo González Pérez", phone="+52 55 2345 6789", email="mateo.gonzalez@example.com",
         date_of_birth="2018-07-22", gender="male", address="Calle Reforma 456, CDMX",
         blood_type="A+", allergies=None),
    dict(full_name="Valentina Cruz Morales", phone="+52 55 3456 7890", email="valentina.cruz@example.com",
         date_of_birth="1990-11-05", gender="female", address="Av. Chapultepec 789, CDMX",
         blood_type="B+", allergies={"medications": ["penicilina"], "foods": [], "environmental": []}),
    dict(full_name="Santiago Rojas Mendoza", phone="+52 55 4567 8901", email="santiago.rojas@example.com",
         date_of_birth="1985-02-18", gender="male", address="Calle Durango 321, CDMX",
         blood_type="O-", allergies=None),
    dict(full_name="Camila Vargas Herrera", phone="+52 55 5678 9012", email="camila.vargas@example.com",
         date_of_birth="1978-09-30", gender="female", address="Av. Álvaro Obregón 654, CDMX",
         blood_type="AB+", allergies={"medications": [], "foods": [], "environmental": ["polen"]}),
    dict(full_name="Emiliano Castro Jiménez", phone="+52 55 6789 0123", email="emiliano.castro@example.com",
         date_of_birth="2010-05-14", gender="male", address="Calle Sonora 987, CDMX",
         blood_type="A-", allergies=None),
    dict(full_name="Regina Ortiz Delgado", phone="+52 55 7890 1234", email="regina.ortiz@example.com",
         date_of_birth="1965-12-01", gender="female", address="Av. Universidad 147, CDMX",
         blood_type="O+", allergies={"medications": ["ibuprofeno"], "foods": [], "environmental": []}),
    dict(full_name="Joaquín Silva Reyes", phone="+52 55 8901 2345", email="joaquin.silva@example.com",
         date_of_birth="1995-04-27", gender="male", address="Calle Michoacán 258, CDMX",
         blood_type="B-", allergies=None),
    dict(full_name="Isabella Navarro Campos", phone="+52 55 9012 3456", email="isabella.navarro@example.com",
         date_of_birth="1952-08-09", gender="female", address="Av. Coyoacán 369, CDMX",
         blood_type="A+", allergies={"medications": [], "foods": ["mariscos"], "environmental": []}),
    dict(full_name="Leonardo Aguilar Paredes", phone="+52 55 0123 4567", email="leonardo.aguilar@example.com",
         date_of_birth="2020-01-15", gender="male", address="Calle Yucatán 741, CDMX",
         blood_type="O+", allergies=None),
]

REASONS = ["Revisión general", "Limpieza dental", "Consulta de urgencia", "Seguimiento",
           "Chequeo anual", "Control de rutina", "Dolor persistente", "Primera consulta"]

DAY_ABBR = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


# ── Helpers ────────────────────────────────────────────────────────────────

def normalize(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.lower().strip()


def decode_jwt_payload(token: str) -> dict:
    payload_b64 = token.split(".")[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def confirm_or_exit(phase: str) -> None:
    print(f"\nEstás a punto de ESCRIBIR datos en: {phase}")
    answer = input('Escribe "SI" para confirmar y continuar: ').strip()
    if answer != "SI":
        print("Cancelado. No se realizó ningún cambio.")
        sys.exit(1)


class Client:
    def __init__(self, api_base: str, owner_email: str):
        self.api_base = api_base.rstrip("/")
        self.owner_email = owner_email
        self.http = httpx.Client(timeout=30.0)
        self.token = None
        self.clinic_id = None

    def login(self) -> None:
        password = getpass.getpass(f"Password para {self.owner_email}: ")
        resp = self.http.post(
            f"{self.api_base}/auth/login",
            json={"email": self.owner_email, "password": password},
        )
        if resp.status_code != 200:
            print(f"Login falló ({resp.status_code}): {resp.text}")
            sys.exit(1)
        self.token = resp.json()["access_token"]
        self.clinic_id = decode_jwt_payload(self.token)["clinic_id"]
        print(f"Login OK. clinic_id={self.clinic_id}")

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def get(self, path: str, **kwargs):
        return self.http.get(f"{self.api_base}{path}", headers=self.headers, **kwargs)

    def post(self, path: str, json_body: dict):
        return self.http.post(f"{self.api_base}{path}", headers=self.headers, json=json_body)

    def patch(self, path: str, json_body: dict):
        return self.http.patch(f"{self.api_base}{path}", headers=self.headers, json=json_body)


# ── Duplicate detection ─────────────────────────────────────────────────────

def existing_doctors(client: Client) -> list[dict]:
    resp = client.get("/doctors")
    resp.raise_for_status()
    return resp.json()


def find_doctor_match(existing: list[dict], target_name: str) -> dict | None:
    target = normalize(target_name)
    for doc in existing:
        if normalize(doc.get("full_name") or "") == target:
            return doc
    return None


def find_patient_matches(client: Client, target_name: str) -> list[dict]:
    # search_text is a lowercase concat of name/email/phone; search by first name token.
    first_token = target_name.split()[0]
    resp = client.get("/patients/search", params={"q": first_token, "limit": 20})
    if resp.status_code != 200:
        return []
    candidates = resp.json()
    target = normalize(target_name)
    return [p for p in candidates if normalize(p.get("full_name_enc") or p.get("full_name") or "") == target]


# ── Phase: check ─────────────────────────────────────────────────────────────

def phase_check(client: Client) -> None:
    print("\n=== Doctores ===")
    existing = existing_doctors(client)
    for d in DOCTORS:
        match = find_doctor_match(existing, d["full_name"])
        if match:
            print(f"  YA EXISTE: {d['full_name']} -> doctor_id={match['id']} (se reutilizará, no se crea de nuevo)")
        else:
            print(f"  A CREAR:   {d['full_name']}")

    print("\n=== Pacientes ===")
    for p in PATIENTS:
        matches = find_patient_matches(client, p["full_name"])
        if matches:
            codes = ", ".join(m.get("medical_record_code", "?") for m in matches)
            print(f"  YA EXISTE: {p['full_name']} -> código(s) {codes} (se reutilizará, no se crea de nuevo)")
        else:
            print(f"  A CREAR:   {p['full_name']}")

    print("\nEsto fue solo lectura. Nada se creó ni se modificó.")


# ── Phase: doctors ───────────────────────────────────────────────────────────

def phase_doctors(client: Client) -> None:
    existing = existing_doctors(client)
    created_creds = []

    for d in DOCTORS:
        match = find_doctor_match(existing, d["full_name"])
        if match:
            print(f"Omitido (ya existe): {d['full_name']} -> {match['id']}")
            continue

        email_local = normalize(d["full_name"]).replace(" ", ".")
        email = f"{email_local}@clinicalocal.demo"
        password = secrets.token_urlsafe(12)

        reg = client.post(
            "/auth/register",
            {
                "email": email,
                "password": password,
                "full_name": d["full_name"],
                "role": "doctor",
                "clinic_id": client.clinic_id,
            },
        )
        if reg.status_code >= 400:
            print(f"  ERROR creando usuario para {d['full_name']}: {reg.status_code} {reg.text}")
            continue

        users_resp = client.get("/users")
        users_resp.raise_for_status()
        user = next(u for u in users_resp.json() if u["email"] == email)

        doc_resp = client.post(
            "/doctors",
            {
                "user_id": user["id"],
                "specialty": d["specialty"],
                "working_hours": d["working_hours"],
                "appointment_duration_minutes": 30,
                "is_accepting_patients": True,
            },
        )
        if doc_resp.status_code >= 400:
            print(f"  ERROR creando doctor {d['full_name']}: {doc_resp.status_code} {doc_resp.text}")
            continue

        doctor = doc_resp.json()
        print(f"Creado: {d['full_name']} -> doctor_id={doctor['id']} (email={email})")
        created_creds.append((d["full_name"], email, password))

    if created_creds:
        print("\nCredenciales generadas (guárdalas si quieres que los doctores puedan iniciar sesión):")
        for name, email, pw in created_creds:
            print(f"  {name}: {email} / {pw}")


# ── Phase: patients ──────────────────────────────────────────────────────────

def phase_patients(client: Client) -> None:
    for p in PATIENTS:
        matches = find_patient_matches(client, p["full_name"])
        if matches:
            print(f"Omitido (ya existe): {p['full_name']} -> código {matches[0].get('medical_record_code')}")
            continue

        resp = client.post("/patients", p)
        if resp.status_code >= 400:
            print(f"  ERROR creando paciente {p['full_name']}: {resp.status_code} {resp.text}")
            continue
        patient = resp.json()
        print(f"Creado: {p['full_name']} -> código={patient['medical_record_code']}")


# ── Phase: appointments ──────────────────────────────────────────────────────

def phase_appointments(client: Client, count: int = 15) -> None:
    existing = existing_doctors(client)
    doctor_records = []
    for d in DOCTORS:
        match = find_doctor_match(existing, d["full_name"])
        if match is None:
            print(f"AVISO: doctor '{d['full_name']}' no existe todavía. Corre la fase 'doctors' primero.")
            return
        doctor_records.append(match)

    patient_records = []
    for p in PATIENTS:
        matches = find_patient_matches(client, p["full_name"])
        if not matches:
            print(f"AVISO: paciente '{p['full_name']}' no existe todavía. Corre la fase 'patients' primero.")
            return
        m = dict(matches[0])
        m["full_name"] = p["full_name"]
        patient_records.append(m)

    now = datetime.now(timezone.utc)
    today = now.date()
    monday_this_week = today - timedelta(days=today.weekday())
    candidate_days = [
        monday_this_week + timedelta(days=i)
        for i in range(0, 10)
        if (monday_this_week + timedelta(days=i)).weekday() < 5
    ]

    random.seed()
    doctor_cycle = list(range(len(doctor_records))) * 4
    random.shuffle(doctor_cycle)
    day_pool = candidate_days[:]
    random.shuffle(day_pool)

    used_slots = set()
    created = 0
    attempts = 0
    idx = 0

    while created < count and attempts < 300:
        attempts += 1
        doctor = doctor_records[doctor_cycle[idx % len(doctor_cycle)]]
        day = day_pool[idx % len(day_pool)]
        idx += 1

        abbr = DAY_ABBR[day.weekday()]
        blocks = (doctor.get("working_hours") or {}).get(abbr, [])
        if not blocks:
            continue
        block = random.choice(blocks)
        bsh, bsm = map(int, block["start"].split(":"))
        beh, bem = map(int, block["end"].split(":"))
        block_start_min = bsh * 60 + bsm
        block_end_min = beh * 60 + bem
        duration = 30
        latest_start = block_end_min - duration
        if latest_start < block_start_min:
            continue
        slot_start_min = random.randrange(block_start_min, latest_start + 1, 30)

        key = (doctor["id"], day.isoformat(), slot_start_min)
        if key in used_slots:
            continue
        used_slots.add(key)

        starts_at = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(minutes=slot_start_min)
        ends_at = starts_at + timedelta(minutes=duration)
        patient = random.choice(patient_records)
        reason = random.choice(REASONS)

        resp = client.post(
            "/appointments",
            {
                "doctor_id": doctor["id"],
                "patient_id": patient["id"],
                "starts_at": starts_at.isoformat(),
                "ends_at": ends_at.isoformat(),
                "reason": reason,
            },
        )
        if resp.status_code == 409:
            continue
        if resp.status_code >= 400:
            print(f"  ERROR creando cita: {resp.status_code} {resp.text}")
            continue

        appt = resp.json()
        created += 1

        status_label = "scheduled"
        if starts_at < now:
            client.patch(f"/appointments/{appt['id']}/status", {"status": "completed"})
            status_label = "completed"

        print(f"  Cita {created}/{count}: {patient['full_name']} con Dr. {doctor.get('full_name') or doctor['id']} "
              f"el {starts_at.isoformat()} ({reason}) [{status_label}]")

    print(f"\nCitas creadas: {created}/{count}")


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo data via the real Med-Sync API.")
    parser.add_argument("phase", choices=["check", "doctors", "patients", "appointments"])
    parser.add_argument("--api-base", required=True, help="e.g. https://your-prod-domain.com/api/v1")
    parser.add_argument("--owner-email", default="owner@test.com")
    parser.add_argument("--confirm", action="store_true",
                        help="Required for write phases (doctors/patients/appointments).")
    parser.add_argument("--appointments-count", type=int, default=15)
    args = parser.parse_args()

    if args.phase != "check" and not args.confirm:
        print(f"La fase '{args.phase}' escribe datos. Vuelve a correr con --confirm para habilitarla.")
        sys.exit(1)

    client = Client(args.api_base, args.owner_email)
    client.login()

    if args.phase == "check":
        phase_check(client)
        return

    confirm_or_exit(f"{args.api_base} (fase: {args.phase})")

    if args.phase == "doctors":
        phase_doctors(client)
    elif args.phase == "patients":
        phase_patients(client)
    elif args.phase == "appointments":
        phase_appointments(client, count=args.appointments_count)


if __name__ == "__main__":
    main()
