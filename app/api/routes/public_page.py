from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.core.config import settings

router = APIRouter(tags=["Public"])

# ── Self-booking page (/public/book/{clinic_slug}) ───────────────────────────

_BOOKING_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Agendar Cita</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --teal:       #0d9488;
      --teal-d:     #0f766e;
      --teal-l:     #e6f7f6;
      --teal-ll:    #f0fdfa;
      --amber:      #d97706;
      --amber-l:    #fef3c7;
      --gray-50:    #fafaf8;
      --gray-100:   #f4f4f0;
      --gray-200:   #e7e7e2;
      --gray-400:   #a8a89e;
      --gray-600:   #6b6b62;
      --gray-800:   #2d2d27;
      --red:        #dc2626;
      --red-l:      #fef2f2;
      --green:      #16a34a;
      --green-l:    #f0fdf4;
      --white:      #ffffff;
      --radius:     14px;
      --radius-sm:  8px;
      --shadow:     0 4px 24px rgba(13,148,136,.12), 0 1px 4px rgba(0,0,0,.06);
    }
    body {
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      background: var(--gray-50);
      color: var(--gray-800);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }

    /* ── Header ───────────────────────────────────────────────── */
    .page-header {
      background: var(--white);
      border-bottom: 1px solid var(--gray-200);
      padding: 1rem 1.5rem;
      display: flex;
      align-items: center;
      gap: .75rem;
      box-shadow: 0 2px 8px rgba(0,0,0,.05);
    }
    .logo-icon {
      width: 40px; height: 40px;
      background: var(--teal-l);
      border-radius: 10px;
      display: flex; align-items: center; justify-content: center;
      font-size: 1.3rem;
    }
    .logo-text { font-size: 1.1rem; font-weight: 700; color: var(--teal-d); }
    .logo-sub  { font-size: .76rem; color: var(--gray-600); margin-top: .1rem; }

    /* ── Steps progress ───────────────────────────────────────── */
    .steps-bar {
      background: var(--white);
      border-bottom: 1px solid var(--gray-200);
      padding: .9rem 1.5rem;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0;
    }
    .step-item {
      display: flex;
      align-items: center;
      gap: .4rem;
      font-size: .78rem;
      color: var(--gray-400);
      font-weight: 500;
    }
    .step-item.active  { color: var(--teal-d); }
    .step-item.done    { color: var(--green); }
    .step-dot {
      width: 24px; height: 24px;
      border-radius: 50%;
      background: var(--gray-200);
      display: flex; align-items: center; justify-content: center;
      font-size: .72rem;
      font-weight: 700;
      flex-shrink: 0;
    }
    .step-item.active .step-dot { background: var(--teal); color: var(--white); }
    .step-item.done   .step-dot { background: var(--green); color: var(--white); font-size: .8rem; }
    .step-sep { width: 28px; height: 2px; background: var(--gray-200); margin: 0 .2rem; flex-shrink: 0; }
    .step-sep.done { background: var(--green); }

    /* ── Layout ───────────────────────────────────────────────── */
    main {
      flex: 1;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 2rem 1rem 3rem;
    }
    .card {
      background: var(--white);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 2rem;
      width: 100%;
      max-width: 560px;
    }
    .card-title {
      font-size: 1.1rem;
      font-weight: 700;
      color: var(--gray-800);
      margin-bottom: 1.25rem;
    }

    /* ── Spinner / loading ────────────────────────────────────── */
    .spinner-wrap { text-align: center; padding: 3rem 0; }
    .spinner {
      width: 42px; height: 42px;
      border: 3px solid var(--gray-200);
      border-top-color: var(--teal);
      border-radius: 50%;
      animation: spin .8s linear infinite;
      margin: 0 auto 1.25rem;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .spinner-wrap p { color: var(--gray-600); font-size: .94rem; }

    /* ── Error / success banners ──────────────────────────────── */
    .banner {
      padding: .85rem 1rem;
      border-radius: var(--radius-sm);
      font-size: .9rem;
      line-height: 1.5;
      text-align: center;
    }
    .banner-err     { background: var(--red-l);   color: var(--red);   font-weight: 600; }
    .banner-ok      { background: var(--green-l); color: var(--green); font-weight: 600; }
    .banner-info    { background: var(--teal-l);  color: var(--teal-d); }

    /* ── Doctor cards ─────────────────────────────────────────── */
    .doctor-list { display: flex; flex-direction: column; gap: .75rem; }
    .doctor-card {
      border: 2px solid var(--gray-200);
      border-radius: var(--radius-sm);
      padding: 1rem 1.1rem;
      cursor: pointer;
      transition: border-color .15s, background .15s;
      display: flex;
      align-items: flex-start;
      gap: .85rem;
    }
    .doctor-card:hover  { border-color: var(--teal); background: var(--teal-ll); }
    .doctor-card.active { border-color: var(--teal); background: var(--teal-l); }
    .doctor-avatar {
      width: 44px; height: 44px;
      border-radius: 50%;
      background: var(--teal-l);
      display: flex; align-items: center; justify-content: center;
      font-size: 1.4rem;
      flex-shrink: 0;
    }
    .doctor-info { flex: 1; }
    .doctor-name    { font-size: .97rem; font-weight: 700; }
    .doctor-spec    { font-size: .8rem;  color: var(--gray-600); margin-top: .15rem; }
    .doctor-dur     { font-size: .75rem; color: var(--teal-d); margin-top: .25rem; font-weight: 600; }
    .doctor-check {
      width: 22px; height: 22px;
      border-radius: 50%;
      border: 2px solid var(--gray-300);
      display: flex; align-items: center; justify-content: center;
      font-size: .7rem;
      flex-shrink: 0;
      margin-top: .1rem;
      color: transparent;
    }
    .doctor-card.active .doctor-check {
      background: var(--teal);
      border-color: var(--teal);
      color: var(--white);
    }

    /* ── Calendar ─────────────────────────────────────────────── */
    .cal-header {
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 1rem;
    }
    .cal-nav-btn {
      background: var(--gray-100);
      border: none;
      border-radius: var(--radius-sm);
      width: 34px; height: 34px;
      cursor: pointer;
      font-size: 1rem;
      display: flex; align-items: center; justify-content: center;
      transition: background .12s;
    }
    .cal-nav-btn:hover { background: var(--teal-l); }
    .cal-month-lbl { font-size: .95rem; font-weight: 700; text-transform: capitalize; }

    .cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
    .cal-hdr  { text-align: center; font-size: .68rem; color: var(--gray-400); font-weight: 600; padding: .25rem 0; }
    .cal-day  {
      text-align: center; padding: .48rem .2rem;
      border-radius: 6px; font-size: .82rem; line-height: 1;
      color: var(--gray-200); cursor: default;
    }
    .cal-day.past    { color: var(--gray-200); }
    .cal-day.unavail { color: var(--gray-400); }
    .cal-day.avail   {
      background: var(--teal-l); color: var(--teal-d);
      font-weight: 600; cursor: pointer;
    }
    .cal-day.avail:hover  { background: var(--teal); color: var(--white); }
    .cal-day.today        { outline: 2px solid var(--amber); outline-offset: -2px; }
    .cal-day.selected     { background: var(--teal) !important; color: var(--white) !important; font-weight: 700; }

    /* ── Slots ────────────────────────────────────────────────── */
    .slots-section { margin-top: 1rem; padding-top: 1rem; border-top: 1px solid var(--gray-200); }
    .slots-lbl  { font-size: .82rem; font-weight: 600; color: var(--gray-600); margin-bottom: .6rem; }
    .slots-grid { display: flex; flex-wrap: wrap; gap: .45rem; }
    .slot-btn {
      padding: .45rem .9rem;
      border: 2px solid var(--teal-l);
      background: var(--white);
      color: var(--teal-d);
      border-radius: var(--radius-sm);
      font-size: .84rem; font-weight: 600;
      cursor: pointer;
      transition: all .12s;
    }
    .slot-btn:hover    { background: var(--teal-l); border-color: var(--teal); }
    .slot-btn.selected { background: var(--teal); color: var(--white); border-color: var(--teal); }

    /* ── Selected slot preview ────────────────────────────────── */
    .slot-preview {
      margin-top: .85rem;
      padding: .8rem 1rem;
      background: var(--teal-l);
      border: 1px solid var(--teal);
      border-radius: var(--radius-sm);
      font-size: .9rem;
      font-weight: 600;
      color: var(--teal-d);
    }

    /* ── Form ─────────────────────────────────────────────────── */
    .form-fields { display: flex; flex-direction: column; gap: 1rem; }
    .form-group label {
      display: block;
      font-size: .8rem;
      font-weight: 600;
      color: var(--gray-600);
      margin-bottom: .35rem;
      text-transform: uppercase;
      letter-spacing: .03em;
    }
    .form-group input,
    .form-group textarea {
      width: 100%;
      border: 2px solid var(--gray-200);
      border-radius: var(--radius-sm);
      padding: .65rem .85rem;
      font-size: .94rem;
      font-family: inherit;
      outline: none;
      transition: border-color .15s;
      background: var(--white);
    }
    .form-group input:focus,
    .form-group textarea:focus { border-color: var(--teal); }
    .form-group input.error,
    .form-group textarea.error { border-color: var(--red); }
    .field-err { font-size: .78rem; color: var(--red); margin-top: .25rem; }
    .form-group textarea { resize: vertical; min-height: 80px; }

    .booking-summary {
      background: var(--gray-100);
      border-radius: var(--radius-sm);
      padding: .85rem 1rem;
      margin-bottom: 1.25rem;
      font-size: .88rem;
      color: var(--gray-800);
      line-height: 1.7;
    }
    .booking-summary strong { color: var(--teal-d); }

    /* ── Buttons ──────────────────────────────────────────────── */
    .btn {
      display: flex; align-items: center; justify-content: center; gap: .4rem;
      padding: .85rem 1.25rem;
      border-radius: 10px;
      font-size: .95rem; font-weight: 600;
      cursor: pointer; border: none;
      transition: opacity .15s, transform .1s;
      line-height: 1.2;
      width: 100%;
    }
    .btn:hover:not(:disabled) { opacity: .88; }
    .btn:active:not(:disabled) { transform: scale(.97); }
    .btn:disabled { opacity: .5; cursor: not-allowed; }
    .btn-primary { background: var(--teal); color: var(--white); }
    .btn-secondary { background: var(--gray-100); color: var(--gray-600); margin-top: .6rem; }
    .btn-row { display: grid; gap: .6rem; margin-top: 1.5rem; }

    /* ── Success state ────────────────────────────────────────── */
    .success-wrap { text-align: center; padding: 1.5rem 0; }
    .success-icon { font-size: 3.5rem; margin-bottom: .75rem; }
    .success-title { font-size: 1.2rem; font-weight: 700; color: var(--green); margin-bottom: .5rem; }
    .success-text  { color: var(--gray-600); font-size: .92rem; line-height: 1.7; margin-bottom: 1.5rem; }
    .success-detail {
      background: var(--green-l);
      border-radius: var(--radius-sm);
      padding: .9rem 1rem;
      font-size: .88rem;
      color: var(--green);
      font-weight: 600;
      margin-bottom: 1rem;
    }

    .hidden { display: none !important; }
    footer  {
      text-align: center;
      padding: 1.5rem;
      font-size: .76rem;
      color: var(--gray-400);
    }

    @media (max-width: 480px) {
      .card { padding: 1.5rem; }
      .steps-bar { gap: .1rem; }
      .step-item span { display: none; }
      .step-sep { width: 16px; }
    }
  </style>
</head>
<body>

<header class="page-header">
  <div class="logo-icon">&#129487;</div>
  <div>
    <div class="logo-text" id="clinic-name">Cargando&hellip;</div>
    <div class="logo-sub">Agendar cita en l&iacute;nea</div>
  </div>
</header>

<!-- Step progress bar -->
<div class="steps-bar">
  <div class="step-item active" id="step-ind-1">
    <div class="step-dot" id="dot-1">1</div>
    <span>Doctor</span>
  </div>
  <div class="step-sep" id="sep-1"></div>
  <div class="step-item" id="step-ind-2">
    <div class="step-dot" id="dot-2">2</div>
    <span>Fecha y hora</span>
  </div>
  <div class="step-sep" id="sep-2"></div>
  <div class="step-item" id="step-ind-3">
    <div class="step-dot" id="dot-3">3</div>
    <span>Tus datos</span>
  </div>
  <div class="step-sep" id="sep-3"></div>
  <div class="step-item" id="step-ind-4">
    <div class="step-dot" id="dot-4">4</div>
    <span>Listo</span>
  </div>
</div>

<main>
  <div class="card">

    <!-- ── Global loading / error ─────────────────────────── -->
    <div id="global-loading" class="spinner-wrap">
      <div class="spinner"></div>
      <p>Cargando disponibilidad&hellip;</p>
    </div>
    <div id="global-error" class="hidden">
      <p class="banner banner-err" id="global-error-text">Error al cargar la información.</p>
    </div>

    <!-- ── Step 1: Doctor ─────────────────────────────────── -->
    <div id="step-1" class="hidden">
      <div class="card-title">&#129489;&#8205;&#9877;&#65039; Selecciona un doctor</div>
      <div id="doctor-list" class="doctor-list"></div>
      <div class="btn-row">
        <button class="btn btn-primary" id="btn-step1-next" onclick="goStep2()" disabled>
          Continuar &rarr;
        </button>
      </div>
    </div>

    <!-- ── Step 2: Date & time ────────────────────────────── -->
    <div id="step-2" class="hidden">
      <div class="card-title">&#128197; Elige fecha y hora</div>

      <div id="slots-loading" class="spinner-wrap" style="padding:2rem 0;">
        <div class="spinner" style="width:30px;height:30px;border-width:2px;"></div>
        <p style="font-size:.85rem;">Cargando horarios disponibles&hellip;</p>
      </div>

      <div id="cal-wrap" class="hidden">
        <div class="cal-header">
          <button class="cal-nav-btn" id="cal-prev" onclick="calNav(-7)" title="Semana anterior">&#8592;</button>
          <span class="cal-month-lbl" id="cal-week-label"></span>
          <button class="cal-nav-btn" id="cal-next" onclick="calNav(7)" title="Semana siguiente">&#8594;</button>
        </div>
        <div class="cal-grid" id="cal-grid"></div>

        <div id="slots-section" class="slots-section hidden">
          <div class="slots-lbl" id="slots-day-label"></div>
          <div id="slots-grid" class="slots-grid"></div>
          <div id="slot-preview" class="slot-preview hidden"></div>
        </div>

        <div id="no-slots-msg" class="banner banner-info hidden" style="margin-top:.75rem;">
          No hay horarios disponibles en los próximos 7 días.
        </div>
      </div>

      <div class="btn-row">
        <button class="btn btn-primary" id="btn-step2-next" onclick="goStep3()" disabled>
          Continuar &rarr;
        </button>
        <button class="btn btn-secondary" onclick="goStep1()">&#8592; Cambiar doctor</button>
      </div>
    </div>

    <!-- ── Step 3: Patient form ───────────────────────────── -->
    <div id="step-3" class="hidden">
      <div class="card-title">&#128101; Tus datos</div>

      <div class="booking-summary" id="booking-summary"></div>

      <form class="form-fields" id="booking-form" onsubmit="submitBooking(event)">
        <div class="form-group">
          <label for="f-name">Nombre completo *</label>
          <input id="f-name" type="text" placeholder="Ej: María García López" maxlength="200" autocomplete="name" />
          <div class="field-err hidden" id="err-name"></div>
        </div>
        <div class="form-group">
          <label for="f-email">Correo electrónico *</label>
          <div style="display:flex;gap:8px;align-items:flex-start;">
            <input id="f-email" type="email" placeholder="tu@correo.com" maxlength="200" autocomplete="email" style="flex:1;min-width:0;" />
            <button type="button" id="btn-send-code" onclick="sendVerificationCode()" class="btn btn-secondary" style="flex-shrink:0;padding:9px 14px;font-size:.82rem;white-space:nowrap;">Enviar código</button>
          </div>
          <div id="email-verified-badge" class="hidden" style="margin-top:6px;color:#16a34a;font-weight:600;font-size:.85rem;">&#10003; Correo verificado</div>
          <div id="code-section" class="hidden" style="margin-top:10px;">
            <div style="font-size:.82rem;color:var(--gray-600);margin-bottom:6px;">Ingresa el código de 6 dígitos enviado a tu correo (válido 15 min):</div>
            <div style="display:flex;gap:8px;align-items:flex-start;">
              <input id="f-code" type="text" inputmode="numeric" placeholder="000000" maxlength="6" style="width:130px;letter-spacing:4px;font-size:1.1rem;text-align:center;" />
              <button type="button" id="btn-check-code" onclick="checkVerificationCode()" class="btn btn-secondary" style="padding:9px 14px;font-size:.82rem;">Verificar</button>
            </div>
            <div class="field-err hidden" id="err-code"></div>
          </div>
          <div class="field-err hidden" id="err-email"></div>
        </div>
        <div class="form-group">
          <label for="f-phone">Teléfono *</label>
          <input id="f-phone" type="tel" placeholder="Ej: 5512345678" maxlength="30" autocomplete="tel" />
          <div class="field-err hidden" id="err-phone"></div>
        </div>
        <div class="form-group">
          <label for="f-reason">Motivo de la consulta (opcional)</label>
          <textarea id="f-reason" placeholder="Ej: Revisión de rutina, dolor de muela..." maxlength="300"></textarea>
        </div>

        <div id="submit-error" class="banner banner-err hidden"></div>

        <div class="btn-row">
          <button class="btn btn-primary" type="submit" id="btn-submit" disabled>
            &#128197; Confirmar cita
          </button>
          <button class="btn btn-secondary" type="button" onclick="goStep2()">&#8592; Cambiar horario</button>
        </div>
      </form>
    </div>

    <!-- ── Step 4: Success ────────────────────────────────── -->
    <div id="step-4" class="hidden">
      <div class="success-wrap">
        <div class="success-icon">&#9989;</div>
        <div class="success-title">¡Cita agendada!</div>
        <p class="success-text">
          Tu cita ha sido confirmada. Te hemos enviado un correo
          con los detalles y un enlace para gestionar tu cita.
        </p>
        <div class="success-detail" id="success-detail"></div>
        <p style="font-size:.82rem;color:var(--gray-600);">
          Revisa tu correo y guarda el enlace para confirmar, cancelar
          o reagendar si lo necesitas.
        </p>
      </div>
    </div>

  </div>
</main>

<footer id="page-footer">MedSync &mdash; Sistema de Citas</footer>

<script>
const SLUG        = "__CLINIC_SLUG__";
const API_BASE    = window.location.origin + "/api/v1/public/book/" + SLUG;
const VERIFY_BASE = window.location.origin + "/api/v1/public/verify";

// ── State ──────────────────────────────────────────────────────────────────
let clinicInfo   = null;   // ClinicPublicInfo
let selectedDoc  = null;   // DoctorPublicInfo
let allSlots     = [];     // [{starts_at: Date, ends_at: Date}]
let selectedDate = null;   // "YYYY-MM-DD"
let selectedSlot = null;   // {starts_at: Date, ends_at: Date}
let calOffset         = 0;      // days offset from today for calendar week start
let verificationToken = null;  // set by checkVerificationCode(); required for booking

// ── Utilities ──────────────────────────────────────────────────────────────
function $(id) { return document.getElementById(id); }
function show(id) { $(id).classList.remove("hidden"); }
function hide(id) { $(id).classList.add("hidden"); }
function pad2(n) { return String(n).padStart(2, "0"); }

function toLocalDateStr(d) {
  return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
}

function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

function fmtDate(iso) {
  return cap(new Date(iso).toLocaleDateString("es-MX", {
    weekday: "long", day: "numeric", month: "long"
  }));
}

function fmtTime12(dt) {
  return dt.toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit", hour12: true });
}

function fmtSlot(slot) {
  return fmtDate(slot.starts_at) + " • " + fmtTime12(slot.starts_at) + "–" + fmtTime12(slot.ends_at);
}

// ── Step indicators ────────────────────────────────────────────────────────
function setStepActive(n) {
  for (let i = 1; i <= 4; i++) {
    const ind = $("step-ind-" + i);
    const dot = $("dot-" + i);
    ind.classList.remove("active", "done");
    if (i < n) {
      ind.classList.add("done");
      dot.textContent = "✓";
    } else if (i === n) {
      ind.classList.add("active");
      dot.textContent = i;
    } else {
      dot.textContent = i;
    }
  }
  for (let i = 1; i <= 3; i++) {
    const sep = $("sep-" + i);
    sep.classList.toggle("done", i < n);
  }
}

// ── Navigation ─────────────────────────────────────────────────────────────
function showStep(n) {
  [1,2,3,4].forEach(i => hide("step-" + i));
  show("step-" + n);
  setStepActive(n);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function goStep1() {
  selectedDoc  = null;
  selectedSlot = null;
  allSlots     = [];
  calOffset    = 0;
  document.querySelectorAll(".doctor-card").forEach(c => c.classList.remove("active"));
  $("btn-step1-next").disabled = true;
  showStep(1);
}

async function goStep2() {
  if (!selectedDoc) return;
  showStep(2);
  hide("cal-wrap");
  hide("no-slots-msg");
  show("slots-loading");

  selectedSlot = null;
  selectedDate = null;
  calOffset    = 0;
  $("btn-step2-next").disabled = true;

  await loadSlots();
}

function goStep3() {
  if (!selectedSlot) return;
  resetEmailVerification();
  showStep(3);
  renderBookingSummary();
}

function goStep4() { showStep(4); }

// ── Slots loading ──────────────────────────────────────────────────────────
async function loadSlots() {
  const today = new Date();
  const start = new Date(today);
  start.setDate(today.getDate() + calOffset);
  const end = new Date(start);
  end.setDate(start.getDate() + 6);

  const params = new URLSearchParams({
    doctor_id:  selectedDoc.id,
    start_date: toLocalDateStr(start),
    end_date:   toLocalDateStr(end),
  });

  try {
    const res = await fetch(API_BASE + "/slots?" + params);
    if (!res.ok) throw new Error();
    const data = await res.json();
    allSlots = data.slots.map(s => ({
      starts_at: new Date(s.starts_at),
      ends_at:   new Date(s.ends_at),
    }));
  } catch {
    allSlots = [];
  }

  hide("slots-loading");
  hide("slots-section");
  hide("slot-preview");
  $("btn-step2-next").disabled = true;
  selectedDate = null;
  selectedSlot = null;

  if (allSlots.length === 0) {
    show("no-slots-msg");
    show("cal-wrap");
  } else {
    hide("no-slots-msg");
    renderCal();
    show("cal-wrap");
  }
}

// ── Calendar navigation ────────────────────────────────────────────────────
async function calNav(delta) {
  calOffset += delta;
  if (calOffset < 0) calOffset = 0;
  hide("cal-wrap");
  show("slots-loading");
  await loadSlots();
}

// ── Calendar render ────────────────────────────────────────────────────────
function renderCal() {
  const today   = new Date();
  const weekStart = new Date(today);
  weekStart.setDate(today.getDate() + calOffset);
  weekStart.setHours(0, 0, 0, 0);

  const weekEnd = new Date(weekStart);
  weekEnd.setDate(weekStart.getDate() + 6);

  const availSet = new Set(allSlots.map(s => toLocalDateStr(s.starts_at)));
  const todayStr = toLocalDateStr(today);

  // Week label
  const opts = { day: "numeric", month: "short" };
  $("cal-week-label").textContent =
    cap(weekStart.toLocaleDateString("es-MX", opts)) + " – " +
    cap(weekEnd.toLocaleDateString("es-MX", opts));

  // Disable prev if at today
  $("cal-prev").disabled = calOffset <= 0;

  const DAY_HDRS = ["Do","Lu","Ma","Mi","Ju","Vi","Sá"];
  const grid = $("cal-grid");
  grid.innerHTML = "";

  DAY_HDRS.forEach(h => {
    const el = document.createElement("div");
    el.className = "cal-hdr";
    el.textContent = h;
    grid.appendChild(el);
  });

  // Fill blanks before weekStart.getDay()
  const startDow = weekStart.getDay();
  for (let i = 0; i < startDow; i++) {
    const blank = document.createElement("div");
    blank.className = "cal-day";
    grid.appendChild(blank);
  }

  // Fill 7 days
  for (let d = 0; d < 7; d++) {
    const dt = new Date(weekStart);
    dt.setDate(weekStart.getDate() + d);
    const ds  = toLocalDateStr(dt);
    const isPast  = dt < new Date(today.getFullYear(), today.getMonth(), today.getDate());
    const isAvail = availSet.has(ds);
    const isToday = ds === todayStr;
    const isSel   = ds === selectedDate;

    const el = document.createElement("div");
    let cls = "cal-day";
    if (isPast)        cls += " past";
    else if (isAvail)  cls += " avail";
    else               cls += " unavail";
    if (isToday) cls += " today";
    if (isSel)   cls += " selected";
    el.className = cls;
    el.textContent = dt.getDate();

    if (isAvail && !isPast) {
      el.addEventListener("click", () => selectCalDay(ds));
    }
    grid.appendChild(el);
  }
}

function selectCalDay(ds) {
  selectedDate = ds;
  selectedSlot = null;
  $("btn-step2-next").disabled = true;
  hide("slot-preview");
  renderCal();

  const daySlots = allSlots.filter(s => toLocalDateStr(s.starts_at) === ds);
  $("slots-day-label").textContent =
    "Horarios para " + fmtDate(daySlots[0].starts_at + "");

  const grid = $("slots-grid");
  grid.innerHTML = "";
  daySlots.forEach(slot => {
    const btn = document.createElement("button");
    btn.className = "slot-btn";
    btn.textContent = fmtTime12(slot.starts_at);
    btn.type = "button";
    btn.addEventListener("click", () => selectSlot(slot, btn));
    grid.appendChild(btn);
  });

  show("slots-section");
  $("slots-section").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function selectSlot(slot, clickedBtn) {
  selectedSlot = slot;
  document.querySelectorAll(".slot-btn").forEach(b => b.classList.remove("selected"));
  clickedBtn.classList.add("selected");
  $("slot-preview").textContent = "Seleccionado: " + fmtSlot(slot);
  show("slot-preview");
  $("btn-step2-next").disabled = false;
}

// ── Booking summary ────────────────────────────────────────────────────────
function renderBookingSummary() {
  if (!selectedDoc || !selectedSlot) return;
  $("booking-summary").innerHTML =
    "<strong>Doctor:</strong> " + selectedDoc.title + " " + selectedDoc.name + "<br>" +
    "<strong>Fecha:</strong> " + fmtDate(selectedSlot.starts_at) + "<br>" +
    "<strong>Hora:</strong> " + fmtTime12(selectedSlot.starts_at) + "–" + fmtTime12(selectedSlot.ends_at);
}

// ── Form validation & submit ───────────────────────────────────────────────
function clearErrors() {
  ["name","email","phone"].forEach(f => {
    $("f-" + f).classList.remove("error");
    const err = $("err-" + f);
    err.textContent = "";
    err.classList.add("hidden");
  });
  const errCode = $("err-code");
  if (errCode) { errCode.textContent = ""; errCode.classList.add("hidden"); }
  hide("submit-error");
}

function setFieldErr(field, msg) {
  $("f-" + field).classList.add("error");
  const el = $("err-" + field);
  el.textContent = msg;
  el.classList.remove("hidden");
}

async function sendVerificationCode() {
  const emailEl = $("f-email");
  const email   = emailEl.value.trim().toLowerCase();
  if (!email.includes("@") || !email.split("@")[1]?.includes(".")) {
    setFieldErr("email", "Ingresa un correo válido antes de enviar el código.");
    return;
  }
  emailEl.classList.remove("error");
  $("err-email").classList.add("hidden");

  const btn = $("btn-send-code");
  btn.disabled = true;
  btn.textContent = "Enviando…";

  try {
    const res = await fetch(`${VERIFY_BASE}/send-code`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ email }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setFieldErr("email", data.detail || "No se pudo enviar el código. Intenta de nuevo.");
      btn.disabled = false;
      btn.textContent = "Reenviar código";
      return;
    }
    emailEl.disabled = true;
    show("code-section");
    btn.textContent = "Reenviar código";
    btn.disabled = false;
  } catch {
    setFieldErr("email", "Error de conexión. Intenta de nuevo.");
    btn.disabled = false;
    btn.textContent = "Enviar código";
  }
}

async function checkVerificationCode() {
  const email = $("f-email").value.trim().toLowerCase();
  const code  = $("f-code").value.trim();
  const errEl = $("err-code");

  if (!/^\d{6}$/.test(code)) {
    errEl.textContent = "Ingresa el código de 6 dígitos recibido en tu correo.";
    errEl.classList.remove("hidden");
    return;
  }
  errEl.classList.add("hidden");

  const btn = $("btn-check-code");
  btn.disabled = true;
  btn.textContent = "Verificando…";

  try {
    const res = await fetch(`${VERIFY_BASE}/check-code`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ email, code }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.verified) {
      errEl.textContent = data.detail || "Código incorrecto o expirado.";
      errEl.classList.remove("hidden");
      btn.disabled = false;
      btn.textContent = "Verificar";
      return;
    }
    verificationToken = data.token;
    hide("code-section");
    show("email-verified-badge");
    $("btn-send-code").style.display = "none";
    $("btn-submit").disabled = false;
  } catch {
    errEl.textContent = "Error de conexión. Intenta de nuevo.";
    errEl.classList.remove("hidden");
    btn.disabled = false;
    btn.textContent = "Verificar";
  }
}

function resetEmailVerification() {
  verificationToken = null;
  const emailEl = $("f-email");
  if (emailEl) { emailEl.disabled = false; emailEl.value = ""; }
  const codeEl = $("f-code");
  if (codeEl) codeEl.value = "";
  hide("code-section");
  hide("email-verified-badge");
  const sendBtn = $("btn-send-code");
  if (sendBtn) {
    sendBtn.textContent = "Enviar código";
    sendBtn.disabled = false;
    sendBtn.style.display = "";
  }
  const submitBtn = $("btn-submit");
  if (submitBtn) submitBtn.disabled = true;
  const errCode = $("err-code");
  if (errCode) { errCode.textContent = ""; errCode.classList.add("hidden"); }
}

async function submitBooking(evt) {
  evt.preventDefault();
  clearErrors();

  const name   = $("f-name").value.trim();
  const email  = $("f-email").value.trim();
  const phone  = $("f-phone").value.trim();
  const reason = $("f-reason").value.trim() || null;

  let valid = true;
  if (name.length < 2)  { setFieldErr("name",  "Ingresa tu nombre completo."); valid = false; }
  if (!email.includes("@") || !email.split("@")[1]?.includes(".")) {
    setFieldErr("email", "Ingresa un correo válido."); valid = false;
  }
  if (phone.replace(/\\D/g, "").length < 7) {
    setFieldErr("phone", "Ingresa un teléfono válido."); valid = false;
  }
  if (!valid) return;

  if (!verificationToken) {
    setFieldErr("email", "Debes verificar tu correo electrónico antes de confirmar la cita.");
    return;
  }

  const btn = $("btn-submit");
  btn.disabled = true;
  btn.textContent = "Agendando…";

  try {
    const res = await fetch(API_BASE, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        doctor_id:          selectedDoc.id,
        starts_at:          selectedSlot.starts_at.toISOString(),
        ends_at:            selectedSlot.ends_at.toISOString(),
        patient_name:       name,
        patient_email:      email,
        patient_phone:      phone,
        reason,
        verification_token: verificationToken,
      }),
    });
    const data = await res.json();

    if (!res.ok) {
      const errEl = $("submit-error");
      errEl.textContent = data.detail || "No se pudo agendar la cita. Intenta de nuevo.";
      errEl.classList.remove("hidden");
      btn.disabled = false;
      btn.textContent = "Confirmar cita";
      return;
    }

    $("success-detail").innerHTML =
      "&#128197; " + fmtSlot(selectedSlot) + "<br>" +
      "&#128231; Confirmación enviada a <strong>" + email + "</strong>";
    goStep4();
  } catch {
    const errEl = $("submit-error");
    errEl.textContent = "Error de conexión. Por favor, intenta de nuevo.";
    errEl.classList.remove("hidden");
    btn.disabled = false;
    btn.textContent = "Confirmar cita";
  }
}

// ── Doctor card selection ──────────────────────────────────────────────────
function buildDoctorCards(doctors) {
  const list = $("doctor-list");
  list.innerHTML = "";
  if (doctors.length === 0) {
    list.innerHTML = "<p class=\\"banner banner-info\\">No hay doctores disponibles en este momento.</p>";
    return;
  }
  doctors.forEach(doc => {
    const card = document.createElement("div");
    card.className = "doctor-card";
    card.dataset.id = doc.id;
    card.innerHTML =
      "<div class=\\"doctor-avatar\\">&#129489;&#8205;&#9877;&#65039;</div>" +
      "<div class=\\"doctor-info\\">" +
        "<div class=\\"doctor-name\\">" + doc.title + " " + doc.name + "</div>" +
        "<div class=\\"doctor-spec\\">" + doc.specialty + "</div>" +
        "<div class=\\"doctor-dur\\">" + doc.duration_minutes + " min por consulta</div>" +
        (doc.bio ? "<div style=\\"font-size:.78rem;color:var(--gray-600);margin-top:.3rem;\\">" + doc.bio + "</div>" : "") +
      "</div>" +
      "<div class=\\"doctor-check\\">&#10003;</div>";
    card.addEventListener("click", () => {
      document.querySelectorAll(".doctor-card").forEach(c => c.classList.remove("active"));
      card.classList.add("active");
      selectedDoc = doc;
      $("btn-step1-next").disabled = false;
    });
    list.appendChild(card);
  });
}

// ── Init ───────────────────────────────────────────────────────────────────
(async function init() {
  try {
    const res = await fetch(API_BASE + "/info");
    if (!res.ok) throw new Error();
    clinicInfo = await res.json();

    $("clinic-name").textContent = clinicInfo.clinic_name;
    $("page-footer").textContent = clinicInfo.clinic_name + " — Sistema de citas en línea";

    hide("global-loading");
    buildDoctorCards(clinicInfo.doctors);
    show("step-1");
    setStepActive(1);
  } catch {
    hide("global-loading");
    $("global-error-text").textContent =
      "No se pudo cargar la información de la clínica. Verifica el enlace e intenta de nuevo.";
    show("global-error");
  }
})();
</script>
</body>
</html>"""


@router.get("/public/book/{clinic_slug}", response_class=HTMLResponse, include_in_schema=False)
async def booking_page(clinic_slug: str):
    """Self-booking HTML page — no authentication required."""
    html = _BOOKING_PAGE.replace("__CLINIC_SLUG__", clinic_slug)
    return HTMLResponse(content=html)

_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Tu Cita — DentalSync</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --teal:        #0d9488;
      --teal-dark:   #0f766e;
      --teal-light:  #e6f7f6;
      --amber:       #d97706;
      --amber-light: #fef3c7;
      --gray-50:     #fafaf8;
      --gray-100:    #f4f4f0;
      --gray-200:    #e7e7e2;
      --gray-400:    #a8a89e;
      --gray-600:    #6b6b62;
      --gray-800:    #2d2d27;
      --red:         #dc2626;
      --red-light:   #fef2f2;
      --green:       #16a34a;
      --green-light: #f0fdf4;
      --white:       #ffffff;
      --radius:      14px;
      --radius-sm:   8px;
      --shadow:      0 4px 24px rgba(13,148,136,.12), 0 1px 4px rgba(0,0,0,.06);
    }

    body {
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      background: var(--gray-50);
      color: var(--gray-800);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }

    /* ── Header ──────────────────────────────────────────────── */
    .page-header {
      background: var(--white);
      border-bottom: 1px solid var(--gray-200);
      padding: 1rem 1.5rem;
      display: flex;
      align-items: center;
      gap: .75rem;
      box-shadow: 0 2px 8px rgba(0,0,0,.05);
    }
    .logo-icon {
      width: 40px; height: 40px;
      background: var(--teal-light);
      border-radius: 10px;
      display: flex; align-items: center; justify-content: center;
      font-size: 1.3rem;
    }
    .logo-text { font-size: 1.1rem; font-weight: 700; color: var(--teal-dark); }
    .logo-sub  { font-size: .76rem; color: var(--gray-600); margin-top: .1rem; }

    /* ── Layout ──────────────────────────────────────────────── */
    main {
      flex: 1;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 2rem 1rem 3rem;
    }

    .card {
      background: var(--white);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 2rem;
      width: 100%;
      max-width: 520px;
    }

    /* ── Loading ─────────────────────────────────────────────── */
    .state-loading { text-align: center; padding: 3.5rem 0; }
    .spinner {
      width: 42px; height: 42px;
      border: 3px solid var(--gray-200);
      border-top-color: var(--teal);
      border-radius: 50%;
      animation: spin .8s linear infinite;
      margin: 0 auto 1.25rem;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .state-loading p { color: var(--gray-600); font-size: .94rem; }

    /* ── Error ───────────────────────────────────────────────── */
    .state-error { text-align: center; padding: 2.5rem 0; }
    .state-error .err-icon { font-size: 3rem; margin-bottom: .75rem; }
    .state-error h2 { font-size: 1.15rem; color: var(--red); margin-bottom: .5rem; }
    .state-error p  { color: var(--gray-600); line-height: 1.6; font-size: .92rem; }

    /* ── Appointment info ────────────────────────────────────── */
    .appt-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 1rem;
      margin-bottom: 1.5rem;
    }
    .appt-name { font-size: 1.1rem; font-weight: 700; }
    .appt-sub  { font-size: .8rem; color: var(--gray-600); margin-top: .2rem; }

    .badge {
      display: inline-block;
      padding: .28rem .8rem;
      border-radius: 999px;
      font-size: .7rem;
      font-weight: 700;
      letter-spacing: .04em;
      text-transform: uppercase;
      white-space: nowrap;
      flex-shrink: 0;
    }
    .badge-teal   { background: var(--teal-light); color: var(--teal-dark); }
    .badge-green  { background: var(--green-light); color: var(--green); }
    .badge-red    { background: var(--red-light); color: var(--red); }
    .badge-gray   { background: var(--gray-100); color: var(--gray-600); }
    .badge-amber  { background: var(--amber-light); color: var(--amber); }

    .details { display: grid; gap: .85rem; margin-bottom: 1.75rem; }
    .detail-row { display: flex; align-items: flex-start; gap: .85rem; }
    .detail-icon {
      width: 36px; height: 36px;
      background: var(--teal-light);
      border-radius: var(--radius-sm);
      display: flex; align-items: center; justify-content: center;
      font-size: .92rem;
      flex-shrink: 0;
    }
    .detail-label { font-size: .72rem; color: var(--gray-600); text-transform: uppercase; letter-spacing: .04em; }
    .detail-value { font-size: .95rem; font-weight: 500; margin-top: .15rem; }

    .divider { height: 1px; background: var(--gray-200); margin: 1.5rem 0; }

    /* ── Action buttons ──────────────────────────────────────── */
    .actions { display: grid; grid-template-columns: 1fr 1fr; gap: .7rem; }
    .actions-3 { display: grid; grid-template-columns: 1fr 1fr; gap: .7rem; }
    .actions-3 .btn-reschedule { grid-column: 1 / -1; }

    .btn {
      display: flex; align-items: center; justify-content: center;
      gap: .4rem;
      padding: .85rem 1rem;
      border-radius: 10px;
      font-size: .92rem;
      font-weight: 600;
      cursor: pointer;
      border: none;
      transition: opacity .15s, transform .1s, background .15s;
      line-height: 1.2;
    }
    .btn:hover:not(:disabled) { opacity: .88; }
    .btn:active:not(:disabled) { transform: scale(.97); }
    .btn:disabled { opacity: .5; cursor: not-allowed; }

    .btn-confirm         { background: var(--green); color: var(--white); }
    .btn-confirm-attend  { background: var(--green); color: var(--white); grid-column: 1 / -1; }
    .btn-cancel     { background: var(--white); color: var(--red); border: 2px solid var(--red); }
    .btn-reschedule { background: var(--teal-light); color: var(--teal-dark); border: 2px solid var(--teal); }
    .btn-back       { background: var(--gray-100); color: var(--gray-600); font-size: .85rem; padding: .6rem .9rem; }
    .btn-confirm-slot { background: var(--teal); color: var(--white); width: 100%; margin-top: .75rem; }

    /* ── Info / result banners ───────────────────────────────── */
    .info-banner, .result-banner {
      border-radius: var(--radius-sm);
      padding: .85rem 1rem;
      font-size: .88rem;
      text-align: center;
      line-height: 1.5;
    }
    .info-banner   { background: var(--teal-light); color: var(--teal-dark); }
    .result-ok     { background: var(--green-light); color: var(--green); font-weight: 600; }
    .result-err    { background: var(--red-light); color: var(--red); font-weight: 600; }

    /* ── Reschedule panel ────────────────────────────────────── */
    .reschedule-panel { margin-top: 1.25rem; }
    .reschedule-header {
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 1rem;
    }
    .reschedule-title {
      font-size: .95rem; font-weight: 700; color: var(--teal-dark);
    }

    /* ── Mini calendar ───────────────────────────────────────── */
    .cal-legend {
      display: flex; gap: 1rem; font-size: .72rem; color: var(--gray-600);
      margin-bottom: .75rem; flex-wrap: wrap;
    }
    .cal-legend-item { display: flex; align-items: center; gap: .3rem; }
    .cal-legend-dot {
      width: 10px; height: 10px; border-radius: 50%;
    }
    .cal-legend-dot.avail { background: var(--teal); }
    .cal-legend-dot.today { background: var(--amber); }

    .cal-months { display: flex; flex-direction: column; gap: 1.25rem; }

    .cal-month-title {
      font-size: .88rem; font-weight: 700; color: var(--gray-800);
      text-align: center; margin-bottom: .6rem; text-transform: capitalize;
    }
    .cal-grid {
      display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px;
    }
    .cal-day-hdr {
      text-align: center; font-size: .68rem; color: var(--gray-400);
      font-weight: 600; padding: .25rem 0;
    }
    .cal-day {
      text-align: center;
      padding: .42rem .2rem;
      border-radius: 6px;
      font-size: .8rem;
      line-height: 1;
      cursor: default;
      color: var(--gray-200);
    }
    .cal-day.other-month { color: var(--gray-200); }
    .cal-day.unavail  { color: var(--gray-400); }
    .cal-day.past     { color: var(--gray-200); }
    .cal-day.avail {
      background: var(--teal-light);
      color: var(--teal-dark);
      font-weight: 600;
      cursor: pointer;
    }
    .cal-day.avail:hover { background: var(--teal); color: var(--white); }
    .cal-day.is-today {
      outline: 2px solid var(--amber);
      outline-offset: -2px;
    }
    .cal-day.selected { background: var(--teal) !important; color: var(--white) !important; font-weight: 700; }

    /* ── Time slots ──────────────────────────────────────────── */
    .slots-section {
      margin-top: 1rem;
      padding-top: 1rem;
      border-top: 1px solid var(--gray-200);
    }
    .slots-label { font-size: .82rem; font-weight: 600; color: var(--gray-600); margin-bottom: .6rem; }
    .slots-grid  { display: flex; flex-wrap: wrap; gap: .45rem; }

    .slot-btn {
      padding: .45rem .9rem;
      border: 2px solid var(--teal-light);
      background: var(--white);
      color: var(--teal-dark);
      border-radius: var(--radius-sm);
      font-size: .84rem;
      font-weight: 600;
      cursor: pointer;
      transition: all .12s;
    }
    .slot-btn:hover { background: var(--teal-light); border-color: var(--teal); }
    .slot-btn.selected { background: var(--teal); color: var(--white); border-color: var(--teal); }

    /* ── Confirm reschedule section ──────────────────────────── */
    .confirm-slot-section {
      margin-top: 1rem;
      padding: .9rem 1rem;
      background: var(--teal-light);
      border-radius: var(--radius-sm);
      border: 1px solid var(--teal);
    }
    .confirm-slot-label { font-size: .78rem; color: var(--teal-dark); font-weight: 600; margin-bottom: .3rem; }
    .confirm-slot-time  { font-size: .95rem; font-weight: 700; color: var(--teal-dark); }

    /* ── Reschedule request modal ───────────────────────────────── */
    .modal-overlay {
      position: fixed; inset: 0;
      background: rgba(0,0,0,.5);
      display: flex; align-items: center; justify-content: center;
      z-index: 100;
      padding: 1rem;
    }
    .modal-box {
      background: var(--white);
      border-radius: var(--radius);
      padding: 1.75rem;
      width: 100%; max-width: 420px;
      box-shadow: 0 8px 32px rgba(0,0,0,.2);
    }
    .modal-title {
      font-size: 1.05rem; font-weight: 700; color: var(--amber);
      margin-bottom: .4rem;
    }
    .modal-sub {
      font-size: .86rem; color: var(--gray-600); margin-bottom: 1rem; line-height: 1.5;
    }
    .modal-textarea {
      width: 100%;
      border: 2px solid var(--gray-200);
      border-radius: var(--radius-sm);
      padding: .7rem .9rem;
      font-size: .9rem;
      font-family: inherit;
      resize: vertical;
      min-height: 90px;
      outline: none;
      transition: border-color .15s;
      box-sizing: border-box;
    }
    .modal-textarea:focus { border-color: var(--teal); }
    .modal-actions { display: grid; grid-template-columns: 1fr 1fr; gap: .6rem; margin-top: 1rem; }
    .btn-amber { background: var(--amber-light); color: var(--amber); border: 2px solid var(--amber); }

    .hidden { display: none !important; }

    footer {
      text-align: center;
      padding: 1.5rem;
      font-size: .76rem;
      color: var(--gray-400);
    }

    @media (max-width: 480px) {
      .card { padding: 1.5rem; }
      .actions, .actions-3 { grid-template-columns: 1fr; }
      .actions-3 .btn-reschedule { grid-column: auto; }
      .appt-header { flex-direction: column; align-items: flex-start; }
      .cal-day { padding: .38rem .15rem; font-size: .76rem; }
      .modal-actions { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>

<header class="page-header">
  <div class="logo-icon">&#129463;</div>
  <div>
    <div class="logo-text">DentalSync</div>
    <div class="logo-sub">Gesti&oacute;n de Citas</div>
  </div>
</header>

<main>
  <div class="card">

    <!-- Loading -->
    <div id="state-loading" class="state-loading">
      <div class="spinner"></div>
      <p>Cargando los detalles de tu cita&hellip;</p>
    </div>

    <!-- Error -->
    <div id="state-error" class="state-error hidden">
      <div class="err-icon">&#9888;&#65039;</div>
      <h2>Enlace no v&aacute;lido</h2>
      <p id="error-text">El enlace puede ser inv&aacute;lido o haber expirado.</p>
    </div>

    <!-- Appointment -->
    <div id="state-appt" class="hidden">
      <div class="appt-header">
        <div>
          <div class="appt-name" id="appt-patient"></div>
          <div class="appt-sub">Detalles de tu cita</div>
        </div>
        <span class="badge" id="appt-status"></span>
      </div>

      <div class="details">
        <div class="detail-row">
          <div class="detail-icon">&#128197;</div>
          <div>
            <div class="detail-label">Fecha</div>
            <div class="detail-value" id="appt-date"></div>
          </div>
        </div>
        <div class="detail-row">
          <div class="detail-icon">&#128336;</div>
          <div>
            <div class="detail-label">Hora</div>
            <div class="detail-value" id="appt-time"></div>
          </div>
        </div>
        <div class="detail-row">
          <div class="detail-icon">&#129489;&#8205;&#9877;&#65039;</div>
          <div>
            <div class="detail-label">Doctor</div>
            <div class="detail-value" id="appt-doctor"></div>
          </div>
        </div>
        <div class="detail-row hidden" id="row-reason">
          <div class="detail-icon">&#128203;</div>
          <div>
            <div class="detail-label">Motivo</div>
            <div class="detail-value" id="appt-reason"></div>
          </div>
        </div>
      </div>

      <div class="divider"></div>

      <!-- Action buttons -->
      <div id="action-buttons" class="actions-3">
        <button class="btn btn-confirm-attend" id="btn-confirm-attend" onclick="confirmAttendance()">
          &#10003; Confirmar cita
        </button>
        <button class="btn btn-confirm" id="btn-confirm" onclick="doAction('confirm')">
          &#10003; Confirmar
        </button>
        <button class="btn btn-cancel" id="btn-cancel" onclick="doAction('cancel')">
          &#10005; Cancelar
        </button>
        <button class="btn btn-reschedule" id="btn-reschedule" onclick="openReschedule()">
          &#128197; Reprogramar cita
        </button>
        <button class="btn btn-amber" id="btn-req-reschedule" onclick="openRescheduleReq()">
          &#128203; Solicitar reagendar
        </button>
      </div>

      <!-- Cancel window warning -->
      <div id="cancel-window-msg" class="info-banner hidden" style="margin-top:.6rem;background:var(--amber-light);color:var(--amber);"></div>

      <!-- Terminal state info -->
      <div id="action-info" class="info-banner hidden"></div>

      <!-- Action result -->
      <div id="action-result" class="result-banner hidden"></div>

      <!-- Reschedule panel -->
      <div id="reschedule-panel" class="reschedule-panel hidden">
        <div class="reschedule-header">
          <span class="reschedule-title">&#128197; Elige una nueva fecha</span>
          <button class="btn btn-back" onclick="closeReschedule()">&#8592; Volver</button>
        </div>

        <!-- Calendar loading -->
        <div id="reschedule-loading" class="state-loading" style="padding:1.5rem 0;">
          <div class="spinner" style="width:30px;height:30px;border-width:2px;"></div>
          <p style="font-size:.85rem;">Cargando disponibilidad&hellip;</p>
        </div>

        <!-- Calendar -->
        <div id="reschedule-calendar" class="hidden">
          <div class="cal-legend">
            <div class="cal-legend-item">
              <div class="cal-legend-dot avail"></div>
              <span>Disponible</span>
            </div>
            <div class="cal-legend-item">
              <div class="cal-legend-dot today"></div>
              <span>Hoy</span>
            </div>
          </div>
          <div id="cal-months" class="cal-months"></div>
        </div>

        <!-- No slots available -->
        <div id="no-slots-msg" class="info-banner hidden" style="margin-top:.75rem;">
          No hay horarios disponibles en los pr&oacute;ximos 3 meses.
        </div>

        <!-- Time slots for selected day -->
        <div id="slots-section" class="slots-section hidden">
          <div class="slots-label" id="slots-day-label"></div>
          <div id="slots-grid" class="slots-grid"></div>

          <!-- Confirm reschedule -->
          <div id="confirm-slot-section" class="confirm-slot-section hidden">
            <div class="confirm-slot-label">Nueva fecha y hora seleccionada:</div>
            <div class="confirm-slot-time" id="confirm-slot-time"></div>
            <button class="btn btn-confirm-slot" id="btn-confirm-reschedule" onclick="doAction('reschedule')">
              &#10003; Confirmar reprogramaci&oacute;n
            </button>
          </div>
        </div>
      </div>

    </div><!-- /state-appt -->

  </div><!-- /card -->
</main>

<!-- Reschedule request modal -->
<div id="modal-reschedule-req" class="modal-overlay hidden" onclick="onModalOverlayClick(event)">
  <div class="modal-box">
    <div class="modal-title">&#128203; Solicitar reagendar</div>
    <p class="modal-sub">Escribe una nota opcional para el asistente, por ejemplo qu&eacute; d&iacute;as u horarios te quedan mejor.</p>
    <textarea id="modal-note" class="modal-textarea" placeholder="Ej: &iquest;Podr&iacute;a ser el viernes por la tarde?" maxlength="500"></textarea>
    <div id="modal-result" class="result-banner hidden" style="margin-top:.75rem;"></div>
    <div class="modal-actions">
      <button class="btn btn-back" onclick="closeRescheduleReq()">Cancelar</button>
      <button class="btn btn-amber" id="btn-send-req" onclick="sendRescheduleReq()">Enviar solicitud</button>
    </div>
  </div>
</div>

<footer>DentalSync &copy; 2025 &mdash; Cl&iacute;nica Dental</footer>

<script>
const TOKEN        = "__TOKEN__";
const CANCEL_HOURS = __CANCEL_HOURS__;
const API_PUB      = window.location.origin + "/api/v1/appointments/public/";

let apptData     = null;  // PublicAppointmentResponse from API
let allSlots     = [];    // [{starts_at: Date, ends_at: Date}]
let selectedDate = null;  // "YYYY-MM-DD"
let selectedSlot = null;  // {starts_at: Date, ends_at: Date}

// ── Utilities ──────────────────────────────────────────────────────────────

function $(id) { return document.getElementById(id); }
function show(id) { $(id).classList.remove("hidden"); }
function hide(id) { $(id).classList.add("hidden"); }

function pad2(n) { return String(n).padStart(2, "0"); }
function toLocalDateStr(d) {
  return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
}

function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

function fmtDate(iso) {
  return cap(new Date(iso).toLocaleDateString("es-MX", {
    weekday: "long", year: "numeric", month: "long", day: "numeric"
  }));
}

function fmtTime(s, e) {
  const o = { hour: "2-digit", minute: "2-digit", hour12: true };
  return new Date(s).toLocaleTimeString("es-MX", o) +
         "–" +
         new Date(e).toLocaleTimeString("es-MX", o);
}

function fmtTimeShort(d) {
  return new Date(d).toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit", hour12: true });
}

function fmtDateShort(iso) {
  return cap(new Date(iso).toLocaleDateString("es-MX", { weekday: "long", day: "numeric", month: "long" }));
}

// ── Status ─────────────────────────────────────────────────────────────────

const STATUS_LABEL = {
  scheduled:           "Pendiente",
  confirmed:           "Confirmada",
  in_progress:         "En progreso",
  completed:           "Completada",
  canceled:            "Cancelada",
  canceled_by_patient: "Cancelada",
  no_show:             "No asistió",
};
const STATUS_BADGE = {
  scheduled:           "badge-teal",
  confirmed:           "badge-green",
  in_progress:         "badge-amber",
  completed:           "badge-green",
  canceled:            "badge-red",
  canceled_by_patient: "badge-red",
  no_show:             "badge-gray",
};
const TERMINAL = new Set(["completed","canceled","canceled_by_patient","no_show","in_progress"]);
const TERMINAL_MSG = {
  completed:           "✅ Esta cita ya fue completada. ¡Gracias por tu visita!",
  canceled:            "Esta cita fue cancelada por la clínica. Contáctanos si deseas reagendar.",
  canceled_by_patient: "Esta cita fue cancelada. Contáctanos si deseas reagendar.",
  no_show:             "Esta cita fue marcada como no asistida.",
  in_progress:         "Esta cita está en progreso en este momento.",
};

function applyStatus(statusVal) {
  const badge = $("appt-status");
  badge.textContent = STATUS_LABEL[statusVal] || statusVal;
  badge.className = "badge " + (STATUS_BADGE[statusVal] || "badge-gray");
}

// ── Render appointment ─────────────────────────────────────────────────────

function render(data) {
  $("appt-patient").textContent = data.patient_name;
  $("appt-date").textContent    = data.formatted_date || fmtDate(data.starts_at);
  $("appt-time").textContent    = data.formatted_time || fmtTime(data.starts_at, data.ends_at);
  $("appt-doctor").textContent  = data.doctor_name;
  applyStatus(data.status);

  if (data.reason) {
    $("appt-reason").textContent = data.reason;
    show("row-reason");
  }

  if (data.patient_confirmed_at) {
    const btn = $("btn-confirm-attend");
    if (btn) {
      btn.innerHTML = "&#10003; Cita confirmada &#10003;";
      btn.disabled = true;
      btn.style.opacity = "1";
    }
  }

  if (TERMINAL.has(data.status)) {
    hide("action-buttons");
    const info = $("action-info");
    info.textContent = TERMINAL_MSG[data.status] || "Esta cita no puede modificarse.";
    show("action-info");
  } else if (data.can_cancel === false) {
    const cancelBtn = $("btn-cancel");
    if (cancelBtn) cancelBtn.style.display = "none";
    const msg = $("cancel-window-msg");
    if (msg) {
      const label = CANCEL_HOURS === 1 ? "1 hora" : CANCEL_HOURS + " horas";
      msg.textContent = "No es posible cancelar con menos de " + label + " de anticipación.";
      show("cancel-window-msg");
    }
  }

  hide("state-loading");
  hide("state-error");
  show("state-appt");
}

// ── Reschedule: load & calendar ────────────────────────────────────────────

function openReschedule() {
  hide("action-buttons");
  hide("action-info");
  hide("action-result");
  show("reschedule-panel");
  show("reschedule-loading");
  hide("reschedule-calendar");
  hide("slots-section");
  hide("no-slots-msg");
  selectedDate = null;
  selectedSlot = null;

  const today = new Date();
  const endD  = new Date(today.getFullYear(), today.getMonth() + 3, today.getDate());
  const params = new URLSearchParams({
    doctor_id:  apptData.doctor_id,
    start_date: toLocalDateStr(today),
    end_date:   toLocalDateStr(endD),
  });

  fetch(window.location.origin + "/api/v1/appointments/public/slots?" + params)
    .then(r => r.ok ? r.json() : Promise.reject(r))
    .then(data => {
      allSlots = data.slots.map(s => ({
        starts_at: new Date(s.starts_at),
        ends_at:   new Date(s.ends_at),
      }));
      hide("reschedule-loading");
      if (allSlots.length === 0) {
        show("no-slots-msg");
      } else {
        renderCalendar();
        show("reschedule-calendar");
      }
    })
    .catch(() => {
      hide("reschedule-loading");
      show("no-slots-msg");
      $("no-slots-msg").textContent = "No se pudo cargar la disponibilidad. Intenta de nuevo.";
    });
}

function closeReschedule() {
  hide("reschedule-panel");
  show("action-buttons");
  selectedDate = null;
  selectedSlot = null;
  allSlots = [];
}

// ── Calendar rendering ─────────────────────────────────────────────────────

function renderCalendar() {
  const availSet = new Set(allSlots.map(s => toLocalDateStr(s.starts_at)));
  const today    = new Date();
  const todayStr = toLocalDateStr(today);

  const container = $("cal-months");
  container.innerHTML = "";

  const DAY_HDRS = ["Do","Lu","Ma","Mi","Ju","Vi","Sá"];
  const MONTH_NAMES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"];

  for (let mi = 0; mi < 3; mi++) {
    const baseDate    = new Date(today.getFullYear(), today.getMonth() + mi, 1);
    const year        = baseDate.getFullYear();
    const month       = baseDate.getMonth();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const firstDow    = new Date(year, month, 1).getDay(); // 0=Sun

    let html = `<div>`;
    html += `<div class="cal-month-title">${MONTH_NAMES[month]} ${year}</div>`;
    html += `<div class="cal-grid">`;
    DAY_HDRS.forEach(h => { html += `<div class="cal-day-hdr">${h}</div>`; });

    for (let i = 0; i < firstDow; i++) {
      html += `<div class="cal-day"></div>`;
    }

    for (let d = 1; d <= daysInMonth; d++) {
      const dateD   = new Date(year, month, d);
      const dateStr = toLocalDateStr(dateD);
      const isToday = dateStr === todayStr;
      const isPast  = dateD < new Date(today.getFullYear(), today.getMonth(), today.getDate());
      const isAvail = availSet.has(dateStr);
      const isSel   = dateStr === selectedDate;

      let cls = "cal-day";
      if (isPast)       cls += " past";
      else if (isAvail) cls += " avail";
      else              cls += " unavail";
      if (isToday) cls += " is-today";
      if (isSel)   cls += " selected";

      const onclick = isAvail && !isPast ? ` onclick="selectDate('${dateStr}')"` : "";
      html += `<div class="${cls}"${onclick}>${d}</div>`;
    }

    html += `</div></div>`;
    container.innerHTML += html;
  }
}

function selectDate(dateStr) {
  selectedDate = dateStr;
  selectedSlot = null;

  renderCalendar(); // re-render to update selected highlight

  const daySlots = allSlots.filter(s => toLocalDateStr(s.starts_at) === dateStr);

  // Day label
  $("slots-day-label").textContent =
    "Horarios disponibles para " + fmtDateShort(daySlots[0].starts_at);

  // Slot buttons
  const grid = $("slots-grid");
  grid.innerHTML = "";
  daySlots.forEach(slot => {
    const btn = document.createElement("button");
    btn.className = "slot-btn";
    btn.textContent = fmtTimeShort(slot.starts_at);
    btn.addEventListener("click", () => selectSlot(slot, btn));
    grid.appendChild(btn);
  });

  hide("confirm-slot-section");
  show("slots-section");

  // Scroll to slots on mobile
  $("slots-section").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function selectSlot(slot, clickedBtn) {
  selectedSlot = slot;
  document.querySelectorAll(".slot-btn").forEach(b => b.classList.remove("selected"));
  clickedBtn.classList.add("selected");

  $("confirm-slot-time").textContent =
    fmtDateShort(slot.starts_at) + " • " + fmtTime(slot.starts_at, slot.ends_at);
  show("confirm-slot-section");
}

// ── Actions ────────────────────────────────────────────────────────────────

function setAllDisabled(v) {
  ["btn-confirm-attend","btn-confirm","btn-cancel","btn-reschedule","btn-req-reschedule","btn-confirm-reschedule"].forEach(id => {
    const el = $(id);
    if (el) el.disabled = v;
  });
}

async function confirmAttendance() {
  const btn = $("btn-confirm-attend");
  btn.disabled = true;
  btn.textContent = "Confirmando…";

  try {
    const res  = await fetch(
      window.location.origin + "/api/v1/appointments/public/confirm/" + TOKEN,
      { method: "POST" }
    );
    const data = await res.json();
    if (res.ok) {
      btn.innerHTML = "&#10003; Cita confirmada &#10003;";
      btn.style.opacity = "1";
    } else {
      btn.textContent = "&#10003; Confirmar cita";
      btn.disabled = false;
      const result = $("action-result");
      result.textContent = data.detail || "No se pudo confirmar. Intenta de nuevo.";
      result.className   = "result-banner result-err";
      result.classList.remove("hidden");
    }
  } catch (e) {
    btn.textContent = "&#10003; Confirmar cita";
    btn.disabled = false;
    const result = $("action-result");
    result.textContent = "Error de conexión. Por favor, intenta de nuevo.";
    result.className   = "result-banner result-err";
    result.classList.remove("hidden");
  }
}

// ── Reschedule request modal ───────────────────────────────────────────────

function openRescheduleReq() {
  $("modal-note").value = "";
  $("modal-result").className = "result-banner hidden";
  $("btn-send-req").disabled = false;
  show("modal-reschedule-req");
}

function closeRescheduleReq() {
  hide("modal-reschedule-req");
}

function onModalOverlayClick(e) {
  if (e.target === $("modal-reschedule-req")) closeRescheduleReq();
}

async function sendRescheduleReq() {
  const note = $("modal-note").value.trim() || null;
  const btn  = $("btn-send-req");
  btn.disabled = true;

  const result = $("modal-result");
  result.className = "result-banner hidden";

  try {
    const res  = await fetch(
      window.location.origin + "/api/v1/appointments/public/reschedule-request/" + TOKEN,
      {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ note }),
      }
    );
    const data = await res.json();
    if (!res.ok) {
      result.textContent = data.detail || "No se pudo enviar la solicitud. Intenta de nuevo.";
      result.className   = "result-banner result-err";
      btn.disabled = false;
    } else {
      result.textContent = "✅ Solicitud enviada. El equipo de la clínica se pondrá en contacto contigo pronto.";
      result.className   = "result-banner result-ok";
      setTimeout(closeRescheduleReq, 2800);
    }
  } catch (e) {
    result.textContent = "Error de conexión. Por favor, intenta de nuevo.";
    result.className   = "result-banner result-err";
    btn.disabled = false;
  }
  result.classList.remove("hidden");
}

async function doAction(action) {
  if (action === "reschedule" && !selectedSlot) return;

  setAllDisabled(true);

  const body = { action };
  if (action === "reschedule") {
    body.new_starts_at = selectedSlot.starts_at.toISOString();
    body.new_ends_at   = selectedSlot.ends_at.toISOString();
  }

  const result = $("action-result");
  result.className = "result-banner hidden";

  try {
    const res  = await fetch(API_PUB + TOKEN, {
      method:  "PUT",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(body),
    });
    const data = await res.json();

    if (!res.ok) {
      result.textContent = data.detail || "No se pudo procesar la acción. Intenta de nuevo.";
      result.className   = "result-banner result-err";
      setAllDisabled(false);
    } else {
      hide("action-buttons");
      hide("reschedule-panel");
      applyStatus(data.status);

      const msgs = {
        confirm:    "✅ ¡Cita confirmada! Te esperamos. ¡Hasta pronto!",
        cancel:     "✅ Tu cita fue cancelada. Contáctanos si deseas reagendar.",
        reschedule: "✅ ¡Cita reprogramada! Te esperamos en tu nueva fecha.",
      };
      result.textContent = msgs[action] || "Acción completada.";
      result.className   = "result-banner result-ok";

      // Update displayed date/time if rescheduled
      if (action === "reschedule") {
        $("appt-date").textContent = data.formatted_date || fmtDate(data.starts_at);
        $("appt-time").textContent = data.formatted_time || fmtTime(data.starts_at, data.ends_at);
      }
    }
  } catch (e) {
    result.textContent = "Error de conexión. Por favor, intenta de nuevo.";
    result.className   = "result-banner result-err";
    setAllDisabled(false);
  }

  result.classList.remove("hidden");
}

// ── Init ───────────────────────────────────────────────────────────────────

function showError(msg) {
  hide("state-loading");
  hide("state-appt");
  $("error-text").textContent = msg;
  show("state-error");
}

(async function init() {
  try {
    const res  = await fetch(API_PUB + TOKEN);
    const data = await res.json();
    if (!res.ok) {
      showError(data.detail || "El enlace es inválido o ha expirado.");
    } else {
      apptData = data;
      render(data);
    }
  } catch (e) {
    showError("Error de conexión. Verifica tu internet e intenta de nuevo.");
  }
})();
</script>
</body>
</html>"""


@router.get("/appointments/public/{token}", response_class=HTMLResponse, include_in_schema=False)
async def appointment_public_page(token: str):
    html = (
        _HTML
        .replace("__TOKEN__", token)
        .replace("__CANCEL_HOURS__", str(settings.PATIENT_CANCEL_HOURS_BEFORE))
    )
    return HTMLResponse(content=html)
