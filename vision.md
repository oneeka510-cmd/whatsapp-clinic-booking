# WhatsApp Clinic Booking System: MVP

## 1. Objective

Build a **fully deterministic WhatsApp appointment booking system** for a fictional clinic.

The system must allow a patient to interact through WhatsApp using **buttons, lists, and/or WhatsApp Flows**, without using an LLM or any AI.

The user should be able to:

1. Book an appointment
2. View an existing appointment
3. Cancel an appointment
4. Reschedule an appointment
5. See only genuinely available appointment slots
6. Receive a confirmation after successfully booking

This is a **demo/MVP**, so the implementation should remain simple, modular, and free to run locally.

---

# 2. Important Constraints

### DO NOT USE

* OpenAI
* LLMs
* LangChain
* LangGraph
* RAG
* Paid APIs unless absolutely unavoidable
* Google Calendar for V1
* Payment gateways
* Complex microservice architecture

### USE

* Python
* FastAPI
* SQLAlchemy
* SQLite
* Meta WhatsApp Business Platform / Cloud API
* WhatsApp interactive messages and/or WhatsApp Flows
* Pydantic
* Environment variables
* A free local tunnel such as Cloudflare Tunnel if required for webhook testing

The application must be deterministic.

The backend, not WhatsApp, must be the source of truth for appointment availability.

---

# 3. High-Level Architecture

```text
Patient
   │
   ▼
WhatsApp
   │
   ▼
Meta WhatsApp Business Platform
   │
   │ webhook
   ▼
FastAPI Backend
   │
   ├── Services
   ├── Doctors
   ├── Availability
   ├── Booking
   ├── Cancellation
   └── Rescheduling
   │
   ▼
SQLite Database
```

WhatsApp acts as the user interface.

FastAPI contains all business logic.

SQLite stores all persistent demo data.

---

# 4. Demo Clinic

Use the fictional clinic:

**Astra Dental Clinic**

Seed the database with sample data.

## Doctors

### Dr. Ananya Sharma

Specialization:

General Dentist

Services:

* General Consultation
* Teeth Cleaning
* Root Canal

---

### Dr. Rohan Mehta

Specialization:

Orthodontist

Services:

* General Consultation
* Orthodontic Consultation

---

### Dr. Priya Kapoor

Specialization:

Dental Surgeon

Services:

* General Consultation
* Root Canal

---

# 5. Services

Seed these services:

| ID | Service                  | Duration |
| -- | ------------------------ | -------- |
| 1  | General Consultation     | 30 min   |
| 2  | Teeth Cleaning           | 30 min   |
| 3  | Root Canal               | 60 min   |
| 4  | Orthodontic Consultation | 30 min   |

The availability engine must take service duration into account.

---

# 6. Clinic Schedule

For the MVP, use predictable working hours.

Example:

```text
Monday - Saturday

09:00 - 13:00
14:00 - 18:00

Sunday
Closed
```

Allow schedules to be configured per doctor in the database.

Do NOT hardcode availability directly into the WhatsApp workflow.

---

# 7. WhatsApp User Experience

## Initial Message

When the patient starts the conversation:

```text
👋 Welcome to Astra Dental Clinic

How can we help you today?

[ Book Appointment ]
[ View Appointment ]
[ Manage Appointment ]
```

---

# 8. Booking Flow

When the user selects:

```text
Book Appointment
```

show services:

```text
Select a service

General Consultation
Teeth Cleaning
Root Canal
Orthodontic Consultation
```

After selecting a service, fetch compatible doctors from the backend.

Example:

```text
Select your doctor

Dr. Ananya Sharma
Dr. Priya Kapoor
Any Available Doctor
```

Then ask for a date.

Prefer a WhatsApp Flow date selector if practical.

Otherwise provide options such as:

```text
Today
Tomorrow
Choose another date
```

The backend must then calculate available appointment slots.

Example:

```text
Available appointments

10:00 AM
10:30 AM
11:30 AM
2:00 PM
4:30 PM
5:30 PM
```

The patient selects a slot.

Then collect:

```text
Patient Name
```

The WhatsApp phone number should be captured automatically from the webhook rather than asking the patient to enter it again.

Show final confirmation:

```text
Confirm Appointment

Patient: Rahul Sharma
Service: General Consultation
Doctor: Dr. Ananya Sharma
Date: 21 September 2026
Time: 5:30 PM

[ Confirm ]
[ Cancel ]
```

Only create the appointment after **Confirm** is pressed.

---

# 9. Successful Booking

After successful booking:

```text
✅ Appointment Confirmed

Booking ID: AST-1042

Dr. Ananya Sharma
General Consultation

21 September 2026
5:30 PM

Astra Dental Clinic

Thank you!
```

Provide:

```text
[ Manage Appointment ]
```

where supported.

---

# 10. Availability Engine

This is one of the most important components.

The backend must calculate availability dynamically.

Example:

Doctor schedule:

```text
09:00 - 13:00
14:00 - 18:00
```

For a 30-minute service, generate:

```text
09:00
09:30
10:00
10:30
11:00
11:30
12:00
12:30

14:00
14:30
15:00
15:30
16:00
16:30
17:00
17:30
```

Suppose the database contains appointments at:

```text
10:30
14:00
16:30
```

The API should return:

```text
09:00
09:30
10:00
11:00
11:30
12:00
12:30
14:30
15:00
15:30
16:00
17:00
17:30
```

Never display a booked slot as available.

---

# 11. Longer Appointments

Service duration must affect availability.

Example:

Root Canal:

```text
duration = 60 minutes
```

If:

```text
10:30 - 11:00
```

is already occupied, then:

```text
10:00 - 11:00
```

cannot be offered for a 60-minute Root Canal.

Availability calculations must check the complete requested interval for overlap.

Use proper datetime overlap logic rather than comparing only appointment start times.

---

# 12. Prevent Double Booking

This is mandatory.

When the user initially sees:

```text
5:30 PM
```

it may be available.

But another patient could book it before the first user confirms.

Therefore:

```text
User selects 5:30
        ↓
Confirmation screen
        ↓
User presses Confirm
        ↓
CHECK AVAILABILITY AGAIN
        ↓
Create appointment
```

If unavailable:

```text
Sorry, this appointment was just booked.

Please select another available time.
```

Then return updated availability.

Where practical, enforce the relevant booking constraint at the database/application transaction level as well.

---

# 13. Appointment Status

Appointments should support:

```text
CONFIRMED
CANCELLED
COMPLETED
```

Optionally:

```text
NO_SHOW
```

Do not delete cancelled appointments.

Change their status to:

```text
CANCELLED
```

---

# 14. View Appointment

Patient selects:

```text
View Appointment
```

Use their WhatsApp phone number to search upcoming appointments.

Example response:

```text
Your Upcoming Appointment

Booking ID: AST-1042

Dr. Ananya Sharma
General Consultation

21 September 2026
5:30 PM

[ Manage Appointment ]
```

If multiple upcoming appointments exist, allow the user to select one.

---

# 15. Cancel Appointment

Patient selects an appointment and chooses:

```text
Cancel Appointment
```

Ask for confirmation:

```text
Are you sure you want to cancel?

Dr. Ananya Sharma
21 September
5:30 PM

[ Yes, Cancel ]
[ Keep Appointment ]
```

After confirmation:

```text
Appointment Cancelled

Booking ID: AST-1042
```

The slot must immediately become available again.

---

# 16. Reschedule Appointment

Patient selects:

```text
Reschedule
```

The system should:

```text
Existing Appointment
        ↓
Fetch Available Dates / Slots
        ↓
Patient selects new slot
        ↓
Confirmation
        ↓
Recheck availability
        ↓
Update appointment
```

Do NOT cancel the existing appointment before the new slot has successfully been reserved/updated.

---

# 17. Database Models

Implement approximately the following.

## Clinic

```text
id
name
phone
address
created_at
```

---

## Doctor

```text
id
clinic_id
name
specialization
active
created_at
```

---

## Service

```text
id
clinic_id
name
duration_minutes
active
```

---

## DoctorService

Many-to-many relationship:

```text
doctor_id
service_id
```

---

## DoctorSchedule

```text
id
doctor_id
day_of_week
start_time
end_time
```

A doctor can have multiple schedule blocks per day.

Example:

```text
Monday
09:00 - 13:00

Monday
14:00 - 18:00
```

---

## Patient

```text
id
name
phone
created_at
```

Phone should be unique or normalized consistently.

---

## Appointment

```text
id
booking_reference
patient_id
doctor_id
service_id
start_datetime
end_datetime
status
created_at
updated_at
```

Store proper datetime values rather than separate date/time strings where practical.

---

# 18. Suggested API

## Services

```text
GET /services
```

---

## Doctors

```text
GET /doctors

GET /doctors?service_id=1
```

---

## Availability

```text
GET /availability
```

Parameters:

```text
doctor_id
service_id
date
```

Example:

```text
/availability?doctor_id=1&service_id=1&date=2026-09-21
```

Response:

```json
{
  "doctor_id": 1,
  "date": "2026-09-21",
  "slots": [
    "09:00",
    "09:30",
    "10:00",
    "11:30",
    "14:00",
    "16:30"
  ]
}
```

---

## Create Appointment

```text
POST /appointments
```

Example request:

```json
{
  "patient_name": "Rahul Sharma",
  "phone": "+919876543210",
  "doctor_id": 1,
  "service_id": 1,
  "start_datetime": "2026-09-21T17:30:00"
}
```

---

## Patient Appointments

```text
GET /appointments
```

Allow lookup by normalized phone number.

---

## Cancel

```text
POST /appointments/{appointment_id}/cancel
```

---

## Reschedule

```text
POST /appointments/{appointment_id}/reschedule
```

---

# 19. WhatsApp Webhook

Implement:

```text
GET /webhooks/whatsapp
```

for Meta webhook verification.

Implement:

```text
POST /webhooks/whatsapp
```

for incoming events.

The webhook layer should remain thin.

Do NOT place appointment business logic directly inside the webhook handler.

Use service classes/functions.

Example:

```text
WhatsApp webhook
       ↓
Parse interaction
       ↓
Booking service
       ↓
Database
       ↓
WhatsApp response
```

---

# 20. Conversation State

Because the interaction involves multiple steps, maintain deterministic state.

Example states:

```text
MAIN_MENU

BOOK_SELECT_SERVICE

BOOK_SELECT_DOCTOR

BOOK_SELECT_DATE

BOOK_SELECT_SLOT

BOOK_ENTER_NAME

BOOK_CONFIRM

MANAGE_SELECT_APPOINTMENT

CANCEL_CONFIRM

RESCHEDULE_SELECT_DATE

RESCHEDULE_SELECT_SLOT

RESCHEDULE_CONFIRM
```

For the demo, conversation state may be stored in SQLite.

Create something similar to:

```text
ConversationSession

id
phone
state
context_json
updated_at
```

Example `context_json`:

```json
{
  "service_id": 1,
  "doctor_id": 2,
  "date": "2026-09-21",
  "slot": "17:30",
  "patient_name": "Rahul Sharma"
}
```

Do not depend on an LLM to determine conversation state.

---

# 21. Project Structure

Use a clean structure similar to:

```text
whatsapp-clinic-booking/

├── app/
│   ├── main.py
│   ├── config.py
│
│   ├── api/
│   │   ├── appointments.py
│   │   ├── availability.py
│   │   ├── doctors.py
│   │   ├── services.py
│   │   └── whatsapp.py
│
│   ├── models/
│   │   ├── clinic.py
│   │   ├── doctor.py
│   │   ├── service.py
│   │   ├── schedule.py
│   │   ├── patient.py
│   │   ├── appointment.py
│   │   └── conversation.py
│
│   ├── schemas/
│   │   ├── appointment.py
│   │   ├── availability.py
│   │   └── doctor.py
│
│   ├── services/
│   │   ├── availability_service.py
│   │   ├── booking_service.py
│   │   ├── conversation_service.py
│   │   └── whatsapp_service.py
│
│   ├── database/
│   │   ├── database.py
│   │   └── seed.py
│
│   └── utils/
│       ├── datetime_utils.py
│       └── phone_utils.py
│
├── tests/
│   ├── test_availability.py
│   ├── test_booking.py
│   └── test_rescheduling.py
│
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

Minor changes to this structure are acceptable if they improve the implementation.

---

# 22. Environment Variables

Create `.env.example`.

Example:

```text
DATABASE_URL=sqlite:///./clinic.db

WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=

CLINIC_TIMEZONE=Asia/Kolkata
```

Never commit actual credentials.

---

# 23. Security

At minimum:

* Validate webhook requests where supported
* Keep credentials in environment variables
* Never expose Meta tokens to frontend/user
* Validate all appointment inputs
* Normalize phone numbers
* Prevent users from managing appointments belonging to another phone number
* Avoid logging secrets
* Sanitize/validate webhook payloads

---

# 24. Testing

Write tests for the booking engine independently from WhatsApp.

Mandatory tests:

### Availability

```text
Doctor schedule generates correct slots.
```

### Existing Appointment

```text
Booked slot disappears from availability.
```

### Cancellation

```text
Cancelled slot becomes available again.
```

### 60-Minute Service

```text
A 60-minute service cannot overlap a 30-minute existing appointment.
```

### Double Booking

```text
Two confirmed appointments cannot occupy overlapping intervals for the same doctor.
```

### Rescheduling

```text
Rescheduling frees the old slot and occupies the new slot.
```

### Closed Day

```text
No availability is returned when the doctor does not work.
```

---

# 25. README

Write a complete README explaining:

1. Project purpose
2. Architecture
3. Requirements
4. Python environment setup
5. Installing dependencies
6. Creating `.env`
7. Creating/seeding SQLite
8. Starting FastAPI
9. Testing through Swagger
10. Creating the Meta WhatsApp developer/test setup
11. Configuring webhook verification
12. Exposing localhost using a free tunnel
13. Connecting Meta webhook to FastAPI
14. Testing the complete WhatsApp booking flow

The project should be runnable by another developer following only the README.

---

# 26. Development Order

Implement in this order.

## Milestone 1: Database

Create:

* models
* SQLite connection
* migrations or reliable initialization
* seed script
* doctors
* services
* schedules

Verify seeded data.

---

## Milestone 2: Availability Engine

Implement:

```text
get_available_slots()
```

Write tests.

Do not continue until availability calculations are reliable.

---

## Milestone 3: Booking Engine

Implement:

```text
create_appointment()

cancel_appointment()

reschedule_appointment()

get_patient_appointments()
```

Write tests.

---

## Milestone 4: REST API

Expose functionality through FastAPI.

Verify everything through:

```text
/docs
```

The complete booking lifecycle must work without WhatsApp first.

---

## Milestone 5: WhatsApp Integration

Implement Meta:

```text
webhook verification
incoming messages
interactive messages
interactive responses
outgoing messages
```

Connect WhatsApp actions to existing booking services.

Do not duplicate booking logic inside the WhatsApp integration.

---

## Milestone 6: WhatsApp Booking UX

Implement:

```text
Main Menu
   ↓
Service
   ↓
Doctor
   ↓
Date
   ↓
Available Slot
   ↓
Patient Name
   ↓
Confirmation
   ↓
Booking Created
```

Then implement:

```text
View Appointment
Cancel Appointment
Reschedule Appointment
```

---

# 27. Definition of Done

The MVP is complete when the following demonstration works from a real WhatsApp client:

```text
Open WhatsApp
      ↓
Message Astra Dental Clinic
      ↓
Receive booking menu
      ↓
Tap Book Appointment
      ↓
Select service
      ↓
Select doctor
      ↓
Select date
      ↓
Receive REAL available slots
      ↓
Select slot
      ↓
Enter name
      ↓
Confirm
      ↓
Appointment stored in SQLite
      ↓
Receive WhatsApp confirmation
```

Then:

```text
View Appointment
```

must show the newly created appointment.

Then:

```text
Cancel Appointment
```

must cancel it.

After cancellation
