"""add medical_record_code to patients

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "patients",
        sa.Column(
            "medical_record_code",
            sa.String(4),
            nullable=True,
            comment="4-char unique code per clinic: 2 consonants + 2 digits (no vowels, no 0/1).",
        ),
    )

    op.create_index(
        "idx_patients_code",
        "patients",
        ["clinic_id", "medical_record_code"],
        unique=True,
    )

    op.execute("""
        DO $$
        DECLARE
            consonants text[] := ARRAY['B','C','D','F','G','H','J','K','L','M','N','P','Q','R','S','T','V','W','X','Y','Z'];
            digits     text[] := ARRAY['2','3','4','5','6','7','8','9'];
            r          RECORD;
            candidate  text;
            attempt    int;
            taken      bool;
        BEGIN
            FOR r IN SELECT id, clinic_id FROM patients WHERE medical_record_code IS NULL LOOP
                attempt := 0;
                LOOP
                    candidate := consonants[1 + (floor(random() * 21))::int]
                              || consonants[1 + (floor(random() * 21))::int]
                              || digits[1 + (floor(random() * 8))::int]
                              || digits[1 + (floor(random() * 8))::int];
                    SELECT EXISTS(
                        SELECT 1 FROM patients
                        WHERE clinic_id = r.clinic_id
                          AND medical_record_code = candidate
                    ) INTO taken;
                    EXIT WHEN NOT taken;
                    attempt := attempt + 1;
                    IF attempt >= 50 THEN
                        RAISE EXCEPTION 'Could not assign unique code for patient %', r.id;
                    END IF;
                END LOOP;
                UPDATE patients SET medical_record_code = candidate WHERE id = r.id;
            END LOOP;
        END;
        $$;
    """)

    op.alter_column("patients", "medical_record_code", nullable=False)


def downgrade() -> None:
    op.drop_index("idx_patients_code", table_name="patients")
    op.drop_column("patients", "medical_record_code")
