#!/usr/bin/env python3
"""
scripts/encrypt_existing_patients.py
─────────────────────────────────────────────────────────────────────────────
Diagnóstica y cifra registros de pacientes que estén en texto plano.

Por qué este script:
  EncryptedString (TypeDecorator) cifra/descifra en el ORM de Python, pero
  si los datos se insertaron ANTES de que el TypeDecorator existiera, o si
  la migración h8c9d0e1f2g3 no se aplicó, la BD tiene texto plano.
  Este script verifica el valor RAW (sin pasar por el ORM) y cifra lo que
  falte.

Uso:
  # Solo diagnóstico — no toca la BD:
  python scripts/encrypt_existing_patients.py --dry-run

  # Cifra todo lo que esté en texto plano:
  python scripts/encrypt_existing_patients.py

  # Muestra más filas en el diagnóstico:
  python scripts/encrypt_existing_patients.py --dry-run --sample 20
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ── Añadir raíz del proyecto al path ─────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlalchemy as sa

from app.core.crypto import (
    DecryptionError,
    _current_key,
    decrypt,
    encrypt,
    make_search_hash,
)
from app.db.session import get_sync_engine

# Campos TEXT cifrados (no incluye allergies_enc que es JSON)
_TEXT_ENC_FIELDS = [
    "full_name_enc",
    "phone_enc",
    "email_enc",
    "date_of_birth_enc",
    "gender_enc",
    "address_enc",
    "blood_type_enc",
    "notes_enc",
    "emergency_contact_name_enc",
    "emergency_contact_phone_enc",
]

# allergies_enc se maneja aparte (es JSON → EncryptedJSON)
_JSON_ENC_FIELDS = ["allergies_enc"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_encrypted(value: str) -> bool:
    """True si el valor ya es un ciphertext válido producido por crypto.encrypt()."""
    try:
        decrypt(value)
        return True
    except Exception:
        return False


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


# ── Diagnóstico ───────────────────────────────────────────────────────────────

def run_diagnostic(conn: sa.engine.Connection, sample: int) -> None:
    print(f"\n{'═'*60}")
    print("  DIAGNÓSTICO — valores RAW en PostgreSQL (sin ORM)")
    print(f"{'═'*60}\n")

    rows = conn.execute(
        sa.text(
            f"SELECT id, full_name_enc, key_version, created_at "
            f"FROM patients ORDER BY created_at LIMIT :n"
        ),
        {"n": sample},
    ).fetchall()

    if not rows:
        print("  (no hay registros en la tabla patients)\n")
        return

    plaintext_count = 0
    encrypted_count = 0

    for row in rows:
        patient_id, raw, kv, created = row
        if raw is None:
            status = "⚠️  NULL"
        elif _is_encrypted(raw):
            status = "✅ CIFRADO"
            encrypted_count += 1
        else:
            status = "❌ TEXTO PLANO"
            plaintext_count += 1
        # Recorta para no mostrar datos sensibles
        preview = (raw[:35] + "…") if raw and len(raw) > 35 else raw
        print(f"  id={str(patient_id)[:8]}…  kv={kv}  {status}")
        print(f"       raw: {preview!r}\n")

    total = len(rows)
    print(f"  Muestra de {total} filas: {encrypted_count} cifradas, {plaintext_count} en texto plano")

    # Total real en la tabla
    total_db = conn.execute(sa.text("SELECT COUNT(*) FROM patients")).scalar()
    pt_db = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM patients "
            "WHERE full_name_enc IS NOT NULL AND key_version = 0"
        )
    ).scalar()
    print(f"  Total en BD: {total_db} pacientes  (key_version=0: {pt_db})\n")


# ── Fix ───────────────────────────────────────────────────────────────────────

def run_fix(conn: sa.engine.Connection) -> None:
    print(f"\n{'═'*60}")
    print("  CIFRANDO registros en texto plano")
    print(f"{'═'*60}\n")

    current_version = _current_key().version

    # Selecciona todos los campos en raw SQL → TypeDecorator NO interviene
    field_list = ", ".join(_TEXT_ENC_FIELDS + _JSON_ENC_FIELDS)
    rows = conn.execute(
        sa.text(f"SELECT id, {field_list} FROM patients")
    ).fetchall()

    updated = 0
    skipped = 0

    for row in rows:
        patient_id = row[0]
        field_values: dict[str, str | None] = dict(
            zip(_TEXT_ENC_FIELDS + _JSON_ENC_FIELDS, row[1:])
        )

        updates: dict[str, object] = {}
        plaintext_cache: dict[str, str] = {}  # campo → plaintext (para hashes)

        # ── Campos TEXT ───────────────────────────────────────────────────────
        for field in _TEXT_ENC_FIELDS:
            value = field_values[field]
            if value is None:
                continue
            if _is_encrypted(value):
                continue  # ya está cifrado, no tocar
            ciphertext, _ = encrypt(value)
            updates[field] = ciphertext
            plaintext_cache[field] = value

        # ── Campo JSON (allergies_enc) ────────────────────────────────────────
        import json as _json
        allergy_raw = field_values.get("allergies_enc")
        if allergy_raw is not None and not _is_encrypted(allergy_raw):
            try:
                parsed = _json.loads(allergy_raw)
            except _json.JSONDecodeError:
                parsed = allergy_raw  # guarda como está si no parsea
            ciphertext, _ = encrypt(_json.dumps(parsed, ensure_ascii=False))
            updates["allergies_enc"] = ciphertext

        if not updates:
            skipped += 1
            continue

        # ── Recalcula search hashes y search_text con los plaintexts ─────────
        if "full_name_enc" in plaintext_cache:
            name_pt = plaintext_cache["full_name_enc"]
            updates["full_name_search_hash"] = make_search_hash(
                name_pt.lower().strip()
            )

        if "phone_enc" in plaintext_cache:
            phone_pt = plaintext_cache["phone_enc"]
            updates["phone_search_hash"] = make_search_hash(_digits(phone_pt))

        # Reconstruye search_text si alguno de sus tres campos fue cifrado ahora
        search_changed = any(
            f in plaintext_cache for f in ("full_name_enc", "email_enc", "phone_enc")
        )
        if search_changed:
            # Usa el plaintext si se acaba de cifrar, o el valor raw si ya estaba OK
            def _pt(field: str) -> str | None:
                if field in plaintext_cache:
                    return plaintext_cache[field]
                raw = field_values[field]
                if raw is None:
                    return None
                try:
                    return decrypt(raw)
                except Exception:
                    return raw  # fallback: ya estaba en plaintext y se salta

            updates["search_text"] = " ".join(
                filter(None, [_pt("full_name_enc"), _pt("email_enc"), _pt("phone_enc")])
            ).lower()

        updates["key_version"] = current_version

        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        conn.execute(
            sa.text(f"UPDATE patients SET {set_clause} WHERE id = :id"),
            {**updates, "id": str(patient_id)},
        )
        updated += 1
        changed_fields = [k for k in updates if k not in ("key_version",)]
        print(f"  ✅ id={str(patient_id)[:8]}…  → cifrado: {changed_fields}")

    conn.commit()
    print(
        f"\n  Resumen: {updated} registros cifrados, "
        f"{skipped} ya estaban cifrados (saltados).\n"
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnóstica y cifra pacientes en texto plano."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo diagnóstico — no modifica la BD.",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=5,
        help="Número de filas a mostrar en el diagnóstico (default: 5).",
    )
    args = parser.parse_args()

    engine = get_sync_engine()

    with engine.connect() as conn:
        run_diagnostic(conn, sample=args.sample)

        if args.dry_run:
            print("  Modo --dry-run: no se realizaron cambios.\n")
            return

        confirm = input("  ¿Proceder con el cifrado? [s/N] ").strip().lower()
        if confirm not in ("s", "si", "sí", "y", "yes"):
            print("  Cancelado.\n")
            return

        run_fix(conn)


if __name__ == "__main__":
    main()
