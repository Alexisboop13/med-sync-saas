---
name: project-audit-log
description: AuditLog implementation — log_audit() service wired into key endpoints for NOM-024/HIPAA compliance
metadata:
  type: project
---

AuditLog model existed but was never written. Implemented full audit trail.

**Why:** NOM-024 / HIPAA compliance requires recording who did what and when.

**How to apply:** Use `log_audit()` from `app/services/audit.py` in any new endpoint that touches sensitive data. Pass `commit=True` for read-only or error paths where the main flow never commits.

Key design:
- `log_audit()` NEVER raises — errors are logged, never surfaced to callers
- `commit=False` (default) — audit row committed together with the main write
- `commit=True` — for GET endpoints (download_pdf, view) and error paths (login_failed)
- Payload follows CloudEvents 1.0 envelope; PII values never stored, only field names in `masked_fields`

Events wired:
- auth.py: USER_LOGIN, USER_LOGIN_FAILED, USER_LOGOUT, USER_TOKEN_REFRESHED
- users.py: USER_ROLE_CHANGED, USER_DEACTIVATED
- patients.py: PATIENT_CREATED, PATIENT_UPDATED, PATIENT_DELETED
- appointments.py: APPT_CREATED, APPT_CANCELED_STAFF, APPT_COMPLETED, APPT_NO_SHOW
- medical_records.py: RECORD_CREATED, RECORD_VIEWED, RECORD_UPDATED, RECORD_SIGNED, RECORD_PDF_UPLOADED, RECORD_PDF_DOWNLOADED, medical_record.deleted

Added to EventType registry: USER_LOGOUT, USER_TOKEN_REFRESHED
