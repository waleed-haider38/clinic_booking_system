# Clinic Booking System

A backend appointment-booking system for clinics, built with Django and Django REST
Framework. Patients book appointments with doctors based on real, conflict-free
availability, instead of relying on phone calls or manual scheduling.

## Problem Statement

Small and mid-sized clinics often manage patient appointments manually — through
phone calls, paper registers, or scattered spreadsheets. This leads to double-booked
doctor slots, missed appointments due to a lack of reminders, and no easy way for
patients to see a doctor's real-time availability. This system solves that by letting
patients book appointments online with their preferred doctor, see only genuinely open
time slots, and (eventually) receive automatic reminders — while giving clinic staff a
reliable, conflict-free schedule to manage.

## Tech Stack

- **Language / Framework:** Python, Django, Django REST Framework
- **Auth:** JWT (djangorestframework-simplejwt)
- **API Docs:** drf-spectacular (Swagger / Redoc)
- **Database:** PostgreSQL
- **Async tasks:** Celery + Redis (background email notifications, scheduled reminders)
- **Testing:** pytest-django, coverage.py
- **Code quality:** black, isort, flake8
- **Planned (later phases):** Docker, GitHub Actions CI

## User Roles

- **Patient** — books, views, and cancels their own appointments
- **Doctor** — defines availability and services, sees their own appointments
- **Admin** — manages doctors and oversees all appointments

## Entity-Relationship Design

```
USER (id, email, password, role)
  |
  +-- (1:1) --> DOCTOR_PROFILE (id, user_id, specialty, license_number, bio)
  |               |
  |               +-- (1:N) --> AVAILABILITY (id, doctor_id, day_of_week, start_time, end_time)
  |               +-- (1:N) --> SERVICE (id, doctor_id, name, duration_minutes, price)
  |               +-- (1:N) --> APPOINTMENT (id, patient_id, doctor_id, service_id, start_time, end_time, status)
  |
  +-- (1:1) --> PATIENT_PROFILE (id, user_id, date_of_birth, medical_history_notes)
```

**Design notes:**

- `User` is a single source of truth for authentication (extends Django's
  `AbstractUser`, adds a `role` field). It never holds role-specific data.
- `DoctorProfile` and `PatientProfile` are one-to-one extensions of `User`, holding
  data specific to each role.
- `Appointment` always references `DoctorProfile` / `PatientProfile`, never `User`
  directly, to keep foreign keys consistent.
- `Appointment.clean()` prevents overlapping bookings for the same doctor, enforces a
  minimum booking-notice period, and is backed by a database-level exclusion
  constraint (see Phase 4 below).

## API Overview

Base URL: `/api/`

| Endpoint                             | Method       | Access                                                                     |
| ------------------------------------ | ------------ | -------------------------------------------------------------------------- |
| `/api/token/`                        | POST         | Public — obtain JWT access/refresh tokens                                  |
| `/api/token/refresh/`                | POST         | Public — refresh an access token                                           |
| `/api/users/`                        | GET/POST/... | Admin only                                                                 |
| `/api/doctors/`                      | GET/POST/... | Public read, doctor/admin write                                            |
| `/api/doctors/{id}/available-slots/` | GET          | Public — returns open time slots for a doctor on a given date              |
| `/api/patients/`                     | GET/POST/... | Authenticated, scoped to own profile                                       |
| `/api/availabilities/`               | GET/POST/... | Public read, doctor-only write                                             |
| `/api/services/`                     | GET/POST/... | Public read, doctor-only write                                             |
| `/api/appointments/`                 | GET/POST/... | Authenticated, scoped to own bookings                                      |
| `/api/appointments/{id}/cancel/`     | POST         | Authenticated — cancels an appointment, subject to the cancellation window |
| `/api/docs/`                         | GET          | Swagger UI — interactive API docs                                          |

### Background Notifications (Phase 5)

Booking-related emails are sent asynchronously through Celery, so the API responds
immediately instead of waiting on email delivery:

- **Confirmation email** — sent when a booking is created, triggered via
  `transaction.on_commit()` so it only fires after the database row is actually
  committed (avoiding a race between the worker and an uncommitted transaction).
- **Cancellation email** — sent when an appointment is cancelled, same
  `on_commit()` pattern.
- **Reminder email** — a Celery Beat–scheduled task periodically checks for
  `confirmed` appointments starting within the next 24 hours and haven't had a
  reminder sent yet (tracked via a `reminder_sent` flag), then emails each one.

Locally, emails are sent through Django's console backend (`MAILERS` setting) and
printed to the terminal running the Celery worker — no real mail server is required
for development.

**Key engineering decisions:**

- Role-based permissions (`patient` / `doctor` / `admin`) enforced via custom DRF
  permission classes, not just serializer-level checks.
- `get_queryset()` overrides scope list results per user (a patient never sees another
  patient's appointments), separate from object-level permission checks.
- The `available-slots` endpoint computes real free time by subtracting existing
  appointments from a doctor's declared `Availability`, rather than exposing raw
  availability and leaving conflict-checking to the client.
- Double-booking is prevented with two independent layers (see "How I Solved
  Double-Booking" below) rather than a single check, so a bug in one layer can't by
  itself corrupt the schedule.

## How I Solved Double-Booking

**The problem:** two patients could hit "book" for the same doctor and time slot
within milliseconds of each other. A naive "check if the slot is free, then create the
appointment" flow has a race condition — both requests can pass the free-slot check
before either one has actually saved, resulting in two confirmed appointments for the
same doctor at the same time.

**The fix — defense in depth, two independent layers:**

1. **Application-level lock** (`AppointmentViewSet.perform_create`): before checking
   for conflicts, the request locks the doctor's profile row with
   `select_for_update()` inside `transaction.atomic()`. This forces a second,
   concurrent request for the same doctor to wait until the first transaction
   commits — so the conflict check and the save happen as one atomic unit, not two
   separate steps that can interleave.

2. **Database-level exclusion constraint** (`Appointment.Meta.constraints`): a
   Postgres `ExclusionConstraint` (using the `btree_gist` extension) rejects, at the
   database engine level, any two `pending`/`confirmed` appointments for the same
   doctor whose time ranges overlap — combining the separate `start_time`/`end_time`
   columns into a single range via a small `TSTZRANGE()` wrapper. This is the backstop:
   even if the application-level lock were ever bypassed or buggy, the database itself
   refuses the conflicting row.

**Additional business rules**, enforced in `Appointment.clean()` and the `cancel`
action:

- **Minimum notice period** — a new appointment must start at least 1 hour from now.
- **Cancellation window** — an appointment can't be cancelled within 2 hours of its
  start time.

**Proof it works — automated tests** (`clinic/tests.py`):

- `NoticePeriodTest` — asserts booking within the minimum notice period raises a
  validation error.
- `CancellationWindowTest` — asserts cancelling within the cancellation window is
  rejected.
- `DoubleBookingRaceConditionTest` — fires two simultaneous booking requests (via
  `threading`, using `TransactionTestCase` so real transactions are exercised) for the
  same doctor and slot, and asserts that exactly one succeeds and exactly one is
  rejected — both at the application level and confirmed by a final database count.

All three tests pass, giving direct evidence that the double-booking fix holds up
under concurrent load, not just in the single-request happy path.

## Project Status

### ✅ Phase 1 — Planning & Data Modeling

- Problem statement and user roles defined
- ER diagram designed (6 entities: User, DoctorProfile, PatientProfile, Availability,
  Service, Appointment)
- Django project + PostgreSQL connected via environment variables

### ✅ Phase 2 — Core Models & Admin

- Custom `User` model with role field (`patient` / `doctor` / `admin`)
- `DoctorProfile`, `PatientProfile`, `Availability`, `Service`, `Appointment` models
  built and migrated
- All models registered and manageable in Django admin
- Basic double-booking validation implemented via `Appointment.clean()`

### ✅ Phase 3 — REST API

- Serializers for all six models, with cross-field validation (e.g. `start_time` <
  `end_time`) and reuse of the model-level overlap check
- Full CRUD via DRF `ModelViewSet`s, wired through a `DefaultRouter`
- JWT authentication (login + refresh) via `djangorestframework-simplejwt`
- Custom role-based permission classes (`IsDoctorOrReadOnly`,
  `IsOwnerPatientOrDoctorReadOnly`, `IsAdminRole`)
- Per-user queryset scoping so patients/doctors only ever see their own data
- `available-slots` endpoint: computes real open time slots for a doctor on a given
  date, accounting for existing bookings
- Conflict re-check on appointment creation as a first line of defense against
  double-booking
- Live Swagger/OpenAPI docs via `drf-spectacular` at `/api/docs/`

### ✅ Phase 4 — Concurrency & Booking Integrity

- Reproduced the double-booking race condition and confirmed it with a failing test
  before fixing it
- **Application-level fix:** `select_for_update()` + `transaction.atomic()` in
  `perform_create()`, locking the doctor row for the duration of the conflict check
  and save
- **Database-level fix:** Postgres `ExclusionConstraint` (via `btree_gist`) on
  `Appointment`, blocking overlapping `pending`/`confirmed` appointments for the same
  doctor at the schema level
- Business rules added: minimum 1-hour booking notice, 2-hour cancellation window
- New `POST /api/appointments/{id}/cancel/` action, enforcing the cancellation window
- Three automated tests added (notice period, cancellation window, and a
  multi-threaded race-condition test), all passing
- See "How I Solved Double-Booking" above for the full write-up

### ✅ Phase 5 — Async Tasks: Notifications & Reminders

- Configured Celery with Redis as the broker and result backend
  (`config/celery.py`, run via `celery -A config worker` and `celery -A config beat`)
- Three background tasks added in `clinic/tasks.py`:
  - `send_confirmation_email` — fired on booking creation
  - `send_cancellation_email` — fired on appointment cancellation
  - `send_appointment_reminders` — runs on a Celery Beat schedule, emails patients
    with a `confirmed` appointment starting in the next 24 hours
- Tasks are triggered via `transaction.on_commit()` from the view layer, so a
  task is never queued for a database row that hasn't been committed yet
- Added a `reminder_sent` boolean field on `Appointment` to prevent the same
  appointment from generating duplicate reminders across scheduler runs
- Verified end-to-end locally: booking and cancelling appointments through the
  API produces the expected email output in the Celery worker's console

### ✅ Phase 6 — Testing & Code Quality

- Migrated test running to `pytest` + `pytest-django` (existing `TestCase`-based
  tests required no changes to run under pytest)
- Added `coverage.py` reporting; **86% overall test coverage**, exceeding the
  70% target
- Added 20+ new tests, focused on the areas with the least existing coverage:
  - Permission classes (`IsAdminRole`, `IsDoctorOrReadOnly`,
    `IsOwnerPatientOrDoctorReadOnly`) — tested directly against
    `has_permission()` / `has_object_permission()` for each role and ownership
    scenario
  - `AppointmentViewSet` — successful booking creation, conflict rejection,
    successful cancellation, cancellation-window rejection, and rejection of
    cancelling an already-cancelled appointment
- Applied `isort` and `black` across the codebase for consistent import
  ordering and formatting
- Ran `flake8` and removed all flagged unused imports
  (`django.shortcuts.render`, `celery.schedules.crontab`)
- Updated `.gitignore` to exclude generated artifacts (`htmlcov/`, `.coverage`,
  `.pytest_cache/`, Celery Beat's local schedule file)

### Upcoming

- Phase 7: Docker + CI/CD + deployment
- Phase 8: Final polish, documentation, and demo

## Local Setup

1. Clone the repo and create a virtual environment:

   ```bash
   python3 -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Create a `.env` file in the project root (see `.env.example` for the required
   variables — database name, user, password, host, port, and Django secret key).

4. Create the PostgreSQL database:

   ```bash
   createdb clinic_db
   ```

5. Run migrations:

   ```bash
   python manage.py migrate
   ```

6. Create a superuser and run the server:

   ```bash
   python manage.py createsuperuser
   python manage.py runserver
   ```

7. Visit `http://127.0.0.1:8000/admin/` to manage data, or
   `http://127.0.0.1:8000/api/docs/` to explore and test the API.

## Running Tests

```bash
python manage.py test clinic
```

This runs the full test suite, including the double-booking race-condition test,
against a temporary test database that is created and destroyed automatically — your
real database is never touched.

## Environment Variables

See `.env.example` for the full list. At minimum you'll need:

```
DB_NAME=clinic_db
DB_USER=postgres
DB_PASSWORD=yourpassword
DB_HOST=localhost
DB_PORT=5432
SECRET_KEY=your-django-secret-key
```
