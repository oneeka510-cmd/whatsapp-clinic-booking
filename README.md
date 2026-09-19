# Astra Dental Clinic: WhatsApp Booking System (MVP)

A fully **deterministic** appointment-booking system for a fictional dental clinic. Patients book,
view, reschedule and cancel appointments entirely through WhatsApp buttons and lists. There is no
LLM and no AI anywhere: every reply is decided by a state machine and a database-backed
availability engine.

Stack: Python · FastAPI · SQLAlchemy · SQLite · Pydantic · Meta WhatsApp Cloud API. Free to run locally.

---

## 1. Purpose

A patient messages the clinic's WhatsApp number and can:

1. **Book** an appointment (service → doctor → date → time → name → confirm)
2. **View** their upcoming appointment(s)
3. **Cancel** an appointment
4. **Reschedule** an appointment
5. See **only genuinely available** slots, with the backend as the single source of truth
6. Receive a **confirmation** with a booking ID (e.g. `AST-1001`)

The same booking engine is also exposed as a REST API (with Swagger docs), so the whole lifecycle can
be exercised without WhatsApp.

## 2. Architecture

```text
Patient ─► WhatsApp ─► Meta Cloud API ──webhook──► FastAPI
                                                     │
        api/whatsapp.py    thin: verify signature, parse, hand off
                                                     │
        conversation_service   deterministic state machine (no business rules)
                                                     │
        booking_service ─► availability_service ─► SQLite
        (create / cancel / reschedule / list)   (the source of truth)
                                                     ▲
        api/appointments.py, availability.py …  REST API (same services)
```

| Layer | Responsibility |
|---|---|
| `app/api/` | HTTP only. The WhatsApp webhook is thin, with no booking logic. |
| `app/services/availability_service.py` | Computes which slots can be booked. **The one place availability rules live.** |
| `app/services/booking_service.py` | Create / cancel / reschedule / list. Re-checks availability before every write. |
| `app/services/conversation_service.py` | Per-phone state machine that turns WhatsApp taps and text into replies. Calls the services above; contains no booking rules. |
| `app/services/replies.py` | All patient-facing message text. |
| `app/services/whatsapp_service.py` | Meta glue: signature check, payload parsing, Cloud API payloads, sending. |
| `app/models/` | SQLAlchemy models. |

### Key design decisions

* **Availability = schedule minus existing appointments.** For a doctor/service/day it takes the doctor's
  schedule blocks, walks them in `SLOT_INTERVAL_MINUTES` steps, keeps starts where the *whole* service
  duration fits inside a block, then drops any interval that **overlaps** a confirmed appointment
  (`existing.start < new.end AND existing.end > new.start`). So a 60-minute Root Canal is never offered at
  10:00 if 10:30-11:00 is taken, and back-to-back appointments are allowed. Schedules live in the
  database (`doctor_schedules`), so nothing is hard-coded in the WhatsApp flow.
* **Booking accepts exactly what availability offers.** `create_appointment` asks
  `is_slot_available`, i.e. "is this start in the list the availability engine would show right now?".
  The slots we display and the slots we accept can never disagree.
* **No double booking, in three layers:**
  1. the patient's **Confirm** tap re-checks availability ("Sorry, this appointment was just booked…");
  2. writes run under a lock so check-then-insert is atomic within a process;
  3. **SQLite triggers** reject any overlapping `CONFIRMED` interval for the same doctor on insert *and*
     update, so even another process or a future bug cannot store one. (Cancelled rows never conflict.)
* **Reschedule never cancels first.** The appointment is updated in place only after the new slot is
  confirmed free; on failure the original booking is untouched.
* **Times are naive clinic-local datetimes** (`CLINIC_TIMEZONE`, default `Asia/Kolkata`). SQLite has no
  time zones and a clinic has one wall clock. Timezone-aware datetimes sent to the REST API are
  converted to clinic time.
* **WhatsApp Flows are not used.** Flows need a Meta-hosted JSON definition plus an encrypted endpoint
  for dynamic data. Interactive **buttons and lists** work with a plain webhook and are enough for the
  whole journey. Dates are chosen with *Today / Tomorrow / Choose another date* (a list of upcoming days
  that actually have availability, or type e.g. `25/09/2026`).

## 3. Requirements

* Python **3.10+** (developed and tested on 3.13)
* For the REST API and console demo: nothing else
* For real WhatsApp: a free [Meta developer](https://developers.facebook.com/) account, a phone with
  WhatsApp, and a free tunnel such as [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) (`cloudflared`)

## 4. Python environment setup

From the project root:

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# If activation is blocked: Set-ExecutionPolicy -Scope Process Bypass
# (or skip activation and run tools as .\.venv\Scripts\python.exe -m ...)
```

## 5. Install dependencies

```bash
pip install -r requirements.txt
```

(`tzdata` is included so time zones work on Windows.)

## 6. Create `.env`

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

Open `.env`. To try the REST API and the console chat you don't need to change anything. WhatsApp values
are filled in during step 10. **Never commit `.env`** (it is in `.gitignore`).

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | SQLite location | `sqlite:///./clinic.db` |
| `WHATSAPP_ACCESS_TOKEN` | Token used to send messages | - |
| `WHATSAPP_PHONE_NUMBER_ID` | ID of the WhatsApp number (not the number itself) | - |
| `WHATSAPP_VERIFY_TOKEN` | Any secret string you invent; must match the Meta webhook setup | - |
| `WHATSAPP_APP_SECRET` | Meta app secret; used to verify webhook signatures | - |
| `WHATSAPP_API_VERSION` | Graph API version | `v23.0` |
| `WHATSAPP_ALLOW_UNSIGNED` | **Local testing only.** Accept unsigned webhook POSTs when no app secret is set | `false` |
| `CLINIC_TIMEZONE` | Clinic time zone | `Asia/Kolkata` |
| `DEFAULT_COUNTRY_CODE` | Added to 10-digit numbers typed into the REST API | `91` |
| `SLOT_INTERVAL_MINUTES` | Spacing of bookable start times | `30` |
| `BOOKING_WINDOW_DAYS` | How far ahead patients can book | `60` |
| `SESSION_TIMEOUT_MINUTES` | Idle time after which a chat resets to the main menu | `30` |

If WhatsApp credentials are missing the app still runs: outgoing messages are only **logged** (dry-run).

## 7. Create and seed SQLite

```bash
python -m app.database.seed            # create tables, seed if empty
python -m app.database.seed --reset    # wipe everything and re-seed
```

This creates `clinic.db` with **Astra Dental Clinic**, four services (General Consultation 30 min, Teeth
Cleaning 30 min, Root Canal 60 min, Orthodontic Consultation 30 min), three doctors
(Dr. Ananya Sharma, Dr. Rohan Mehta, Dr. Priya Kapoor, each with their own services) and schedules
Mon-Sat 09:00-13:00 and 14:00-18:00 (Sunday closed). The command prints a summary so you can verify it.

The app also creates and seeds an empty database on startup, so this step is optional but useful.
Tables are created with `create_all`; if you change a model, delete `clinic.db` (or use `--reset`).

To change a doctor's hours, edit the `doctor_schedules` rows (e.g. with DB Browser for SQLite):
`day_of_week` is `0` = Monday … `6` = Sunday; several blocks per day are allowed.

## 8. Start FastAPI

```bash
uvicorn app.main:app --reload --port 8000
```

* Swagger UI: <http://localhost:8000/docs>
* Health check: <http://localhost:8000/health>

## 9. Test through Swagger (no WhatsApp needed)

Open <http://localhost:8000/docs> and walk the lifecycle:

1. `GET /services` and `GET /doctors?service_id=3` (Root Canal → Dr. Sharma and Dr. Kapoor).
2. `GET /availability?doctor_id=1&service_id=1&date=<a weekday>` → e.g. `["09:00", "09:30", …]`.
   A Sunday returns `[]`.
3. `POST /appointments`:
   ```json
   { "patient_name": "Rahul Sharma", "phone": "+919876543210",
     "doctor_id": 1, "service_id": 1, "start_datetime": "2026-09-21T17:30:00" }
   ```
   → `201` with `booking_reference` like `AST-1001`. Repeat it → `409` (slot taken).
   Availability no longer lists `17:30`.
4. `GET /appointments?phone=+919876543210` → the appointment.
5. `POST /appointments/{id}/reschedule` with `{"phone": "+919876543210", "new_start_datetime": "2026-09-22T10:00:00"}`.
6. `POST /appointments/{id}/cancel` with `{"phone": "+919876543210"}` → status `CANCELLED`, and the slot is
   bookable again. The row is kept.

`cancel`/`reschedule` require the booking phone number: an appointment can only be changed by the number it
was booked with (a mismatch returns `404`, never confirming that the booking exists).

### Try the WhatsApp conversation in your terminal

The console chat runs the *same* conversation engine WhatsApp uses:

```bash
python -m scripts.console_chat            # chat as +919876543210
python -m scripts.console_chat +919123456780
```

Type a number to tap a button or list row, or type text (`menu` returns to the main menu).

### Run the automated tests

```bash
pytest
```

Covers: schedule → slots, booked slots disappearing, cancellation freeing slots, 60-minute overlap,
double booking (including a 12-thread race and the database triggers), rescheduling, closed days,
the REST API, the whole conversation flow, and the webhook (signatures, payload parsing, outgoing payload
format, de-duplication).

## 10. Meta WhatsApp developer / test setup

> Meta's dashboard changes often; names below may shift slightly.

1. Sign in at <https://developers.facebook.com/> and **Create App**. Choose a *Business* type app (the use case
   that includes WhatsApp), then **Add product → WhatsApp**.
2. Open **WhatsApp → API Setup**. Meta provides a free **test phone number**.
   * Copy the **Phone number ID** → `WHATSAPP_PHONE_NUMBER_ID`.
   * Copy the **Temporary access token** → `WHATSAPP_ACCESS_TOKEN`. *It expires after ~24 hours*, so regenerate it
     when messages stop sending (see Troubleshooting). For a long-lived token create a **System User** in
     Business Settings and generate a token with the `whatsapp_business_messaging` permission.
   * Under **To**, add your own phone as a recipient and verify it with the code Meta sends
     (the test number can only message verified recipients).
3. **App settings → Basic → App secret** (click *Show*) → `WHATSAPP_APP_SECRET`. This must be the secret of
   the *same* app, and is what lets the server reject forged webhook requests.
4. Invent a verify token (any random string) → `WHATSAPP_VERIFY_TOKEN`.
5. Save `.env` and restart uvicorn so it picks the values up.

## 11. Webhook verification

Meta verifies your webhook once with `GET /webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=…&hub.challenge=…`.
The app echoes `hub.challenge` (plain text) if `hub.verify_token` equals `WHATSAPP_VERIFY_TOKEN`, otherwise it
returns `403`. You can rehearse it locally:

```bash
curl "http://localhost:8000/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=12345"
# → 12345
```

Every later `POST /webhooks/whatsapp` must carry a valid `X-Hub-Signature-256` header (HMAC-SHA256 of the raw
body with your app secret). Invalid or missing signatures get `403`, and if `WHATSAPP_APP_SECRET` is empty POSTs
are refused (unless you explicitly set `WHATSAPP_ALLOW_UNSIGNED=true` for local experiments, and never on a
reachable server, because the sender's phone number is what decides who may manage an appointment).

## 12. Expose localhost with a free tunnel

Meta must reach your machine over public HTTPS. With Cloudflare's quick tunnel (no account required):

```bash
# install once: Windows  winget install --id Cloudflare.cloudflared
#               macOS    brew install cloudflared
cloudflared tunnel --url http://localhost:8000
```

It prints an address like `https://random-words.trycloudflare.com`. Keep this terminal open. The address
**changes each time** you restart the tunnel, so you must update the webhook URL in Meta whenever it does.
(Any other tunnel, such as ngrok or localtunnel, works the same way.)

## 13. Connect the Meta webhook to FastAPI

1. In the Meta dashboard go to **WhatsApp → Configuration → Webhook → Edit**.
2. **Callback URL:** `https://<your-tunnel-host>/webhooks/whatsapp`
   **Verify token:** the same value as `WHATSAPP_VERIFY_TOKEN`.
3. Click **Verify and save**. Success means the handshake in step 11 worked (check uvicorn's log if not).
4. Under **Webhook fields**, **Subscribe** to **`messages`**.

## 14. Test the complete WhatsApp flow

1. From the phone you added as a recipient, send **`hi`** to the test number.
2. You should get the welcome message with **Book Appointment / View Appointment / Manage Appointment**.
3. **Book Appointment** → pick a service → doctor (or *Any Available Doctor*) → *Today / Tomorrow / Choose
   another date* → a time from the list → type the patient's name → **Confirm**.
4. You receive **✅ Appointment Confirmed** with a booking ID (`AST-1001` …).
5. Verify it landed in the database: `GET /appointments?phone=<your number>` in Swagger, or inspect `clinic.db`.
6. Tap **View Appointment** → your booking is shown. Tap **Manage Appointment** → **Reschedule** (choose a new
   date/time and confirm) or **Cancel Appointment** → **Yes, Cancel**. The slot becomes available immediately
   (check `GET /availability`).
7. Try the double-booking protection: start a booking, stop at the confirmation screen, book the same slot from
   Swagger, then tap **Confirm**. You'll get *"Sorry, this appointment was just booked"* and a fresh list.

Send **`menu`** at any time to start over. Chats idle for `SESSION_TIMEOUT_MINUTES` reset automatically.

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| Meta says "callback URL or verify token couldn't be validated" | Wrong path (must end in `/webhooks/whatsapp`), token mismatch, tunnel not running, or the tunnel URL changed. |
| uvicorn logs `Rejected webhook with a missing or invalid signature` | `WHATSAPP_APP_SECRET` is missing, or belongs to a different app. |
| You send `hi` and get nothing; log shows `[dry-run]` | `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` not set (or `.env` not reloaded). |
| Log shows `WhatsApp send … rejected (401)` | The temporary access token expired. Generate a new one. |
| Log shows a rejection mentioning the recipient (e.g. error 131030) | Your phone isn't in the test number's recipient list. |
| Nothing arrives at all | The `messages` webhook field isn't subscribed, or the tunnel is down. |

## Project structure

```text
app/
  main.py                    FastAPI app, startup (create + seed DB), error mapping
  config.py                  settings from environment variables
  api/                       services, doctors, availability, appointments, whatsapp (webhook)
  models/                    clinic, doctor (+ doctor_services), service, schedule, patient,
                             appointment (+ overlap triggers), conversation (session + processed messages)
  schemas/                   Pydantic models: API, WhatsApp webhook payloads, channel-neutral messages
  services/
    availability_service.py  slot engine
    booking_service.py       create / cancel / reschedule / list
    catalog_service.py       clinic, service and doctor lookups
    conversation_service.py  WhatsApp state machine
    replies.py               patient-facing message templates
    whatsapp_service.py      Meta glue (signatures, parsing, payloads, sending)
    errors.py                domain errors → HTTP status / friendly messages
  database/                  engine + sessions, seed script
  utils/                     datetime and phone helpers
scripts/console_chat.py      terminal chat with the bot
tests/                       pytest suite
```

## Conversation design (for developers)

* States: `MAIN_MENU`, `BOOK_SELECT_SERVICE`, `BOOK_SELECT_DOCTOR`, `BOOK_SELECT_DATE`, `BOOK_SELECT_SLOT`,
  `BOOK_ENTER_NAME`, `BOOK_CONFIRM`, `MANAGE_SELECT_APPOINTMENT`, `MANAGE_SELECT_ACTION`, `CANCEL_CONFIRM`,
  `RESCHEDULE_SELECT_DATE`, `RESCHEDULE_SELECT_SLOT`, `RESCHEDULE_CONFIRM`. State and collected choices
  (`context_json`) are stored per phone number in `conversation_sessions`.
* Buttons and list rows carry ids like `svc:3` or `slot:2026-09-21T17:30`. Old WhatsApp messages keep their
  buttons forever, so a tap that doesn't fit the current state is ignored and the current question is asked again.
  Actions that carry their own target (`manage:<id>`, `cancel:<id>`, `reschedule:<id>`) are re-validated against the
  database and the sender's phone number every time.
* WhatsApp limits (3 buttons, 10 list rows) shape the UX: long time lists are paged ("More times ▶").
* Meta re-delivers webhooks it thinks failed; message ids are recorded in `processed_messages` so a message is
  handled once.

## Security notes

* Webhook requests are authenticated with Meta's HMAC signature; the verify token is compared in constant time.
* Credentials come from environment variables; the token is never logged or returned. Phone numbers in logs are masked.
* Phone numbers are normalized to E.164 and appointments can only be viewed/changed by their owner's number.
* Inputs (names, dates, ids, webhook payloads) are validated; unknown webhook shapes are ignored.
* **The REST API is an unauthenticated demo interface.** Put authentication in front of it (or disable those routers)
  before exposing the service beyond your machine. Only `/webhooks/whatsapp` is meant to be public.

## Known limitations / next steps

* Single clinic; interactive lists show at most 10 services/doctors.
* Doctors are chosen per appointment; there are no breaks/holidays table yet (schedule blocks cover lunch).
* No reminders or template messages: the bot only replies within WhatsApp's 24-hour customer-service window.
* Could evolve to a WhatsApp Flow for date/time picking, Alembic migrations, Postgres, and a staff dashboard.
* Verified locally with signed simulated webhooks and payload-format tests; run the manual checklist in step 14
  against your own Meta test number to confirm the last mile.
