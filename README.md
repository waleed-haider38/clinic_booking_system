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
- **Planned (later phases):** Celery, Redis, Docker, GitHub Actions CI

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
- `Appointment.clean()` prevents overlapping bookings for the same doctor by checking
  for any time-range intersection against existing `pending`/`confirmed` appointments.

## API Overview (Phase 3)

Base URL: `/api/`

| Endpoint | Method | Access |
|---|---|---|
| `/api/token/` | POST | Public — obtain JWT access/refresh tokens |
| `/api/token/refresh/` | POST | Public — refresh an access token |
| `/api/users/` | GET/POST/... | Admin only |
| `/api/doctors/` | GET/POST/... | Public read, doctor/admin write |
| `/api/doctors/{id}/available-slots/` | GET | Public — returns open time slots for a doctor on a given date |
| `/api/patients/` | GET/POST/... | Authenticated, scoped to own profile |
| `/api/availabilities/` | GET/POST/... | Public read, doctor-only write |
| `/api/services/` | GET/POST/... | Public read, doctor-only write |
| `/api/appointments/` | GET/POST/... | Authenticated, scoped to own bookings |
| `/api/docs/` | GET | Swagger UI — interactive API docs |

**Key engineering decisions:**
- Role-based permissions (`patient` / `doctor` / `admin`) enforced via custom DRF
  permission classes, not just serializer-level checks.
- `get_queryset()` overrides scope list results per user (a patient never sees another
  patient's appointments), separate from object-level permission checks.
- The `available-slots` endpoint computes real free time by subtracting existing
  appointments from a doctor's declared `Availability`, rather than exposing raw
  availability and leaving conflict-checking to the client.
- A conflict re-check runs in `perform_create()` immediately before saving a new
  appointment, as a first line of defense against double-booking (hardened further in
  Phase 4 with `select_for_update()`).

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

### Upcoming
- Phase 4: Concurrency-safe booking (`select_for_update`), business rules
- Phase 5: Async email/reminder notifications (Celery + Redis)
- Phase 6: Automated tests, linting
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
