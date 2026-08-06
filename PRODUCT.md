# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Three confirmed roles, each with a materially different situation. They are separated in code by `accounts.User.role`.

**Applicants** — students, faculty, and parents at Palawan State University who need a vehicle sticker to bring a vehicle onto campus. They apply on whatever device they have: phone, laptop, or desktop. Their job is to get through a 3-step application (personal details → vehicle details + document uploads → review/submit), receive an appointment for physical inspection, and then collect a sticker. Most will do this once a year and will not become fluent in the interface. Students must additionally supply a Certificate of Registration; anyone who is not the vehicle's owner must supply an authorization letter.

**Security officers** (`role='security'`) — gate personnel on shift, posted at either the Main Gate or the Back Gate. They work from a **guardhouse desktop/monitor at the gate** and a **shared laptop in the office**. Two distinct scenes: at the gate they are watching live scan activity while vehicles are physically arriving (glanceable, at a distance, in daylight); at the office they are working seated and after the fact — reviewing gate logs, filtering, exporting CSV, and producing incident reports as PDFs. They are not the ones who approve applications.

**Sticker administrators** (`role='admin'`) — staff who open and close the registration window, activate appointment dates, review each submitted application against its uploaded documents, approve or reject with a reason, and physically issue stickers by binding an RFID UID to an approved application at the sticker station.

## Product Purpose

PalSU Gate replaces a paper vehicle-sticker process and an unrecorded campus gate with one system: applicants apply and upload documents online, administrators review and issue an RFID sticker tied to a specific plate, and every pass through the gate is scanned and permanently logged.

Success means three things simultaneously: an applicant can complete an application on a phone without needing help; an administrator can decide on an application from the documents on screen without leaving the page; and a security officer can tell at a glance which vehicles are currently inside campus, and can later produce a defensible record of any incident.

## Positioning

The mechanism a generic form builder or a generic access-control product could not truthfully copy: the sticker, the plate number, the appointment, the RFID tag, and the gate log are one continuous chain of custody. A physical inspection appointment is auto-assigned from application submission; a sticker cannot be issued without an RFID UID bound to it; a scan at the gate resolves to an actual named applicant and vehicle; and every administrative action along the way is written to an immutable audit log. The system's value is that the physical gate and the digital record cannot drift apart.

## Operating Context

- **Deployment:** self-hosted on a machine on the university local network (documented in `DEPLOYMENT.md`). SQLite database, WhiteNoise static files, gunicorn for production. Accessed over campus LAN by IP. Timezone `Asia/Manila`.
- **Two gates:** the **Main Gate** and the **Back Gate**. Both are real, in use, and each has its own reader and API key. Gate identity is therefore a genuine dimension of the data, not future-proofing: an officer needs to know *which* gate a vehicle entered and exited by, and `gate_location` already flows through the scan API, the log filters, CSV export, and incident PDFs. Note that a vehicle may enter one gate and leave by the other — "currently inside" is a campus-wide fact, not a per-gate one.
- **Gate hardware:** ESP32-based RFID readers call a key-authenticated API (`/api/scan/`, `/api/register-uid/`, `/api/gate-status/`, `X-API-Key` header per gate). **Hardware is partially built and not yet reliably integrated** — screens are currently exercised with simulated or manual scans. Design must not assume a continuous real scan stream is always present.
- **No notifications exist.** Nothing in the codebase sends email; `EMAIL_BACKEND` is configured but never called. An applicant learns their appointment date, approval, or rejection *only* by logging back in and looking. This makes "My Applications" the sole channel through which consequential news reaches a person.
- **The registration window** is a real institutional constraint, not a UI convenience: applications can only be submitted between an administrator-set start and end date. Appointments are scheduled only on weekdays *after* the window closes, on administrator-activated dates, in 30-minute slots from 08:00–11:30 and 13:00–16:30, default capacity 20 per day, first-come first-served.
- **Overstay rules** encoded in the time tracker: a vehicle inside for more than 12 hours, or still inside past 22:00 curfew, is flagged.
- **Sensitive documents:** OR/CR, driver's licenses, and CORs are uploaded by applicants and served only through an authenticated ownership-checked view (`applications.views.serve_document`), never as public media. Plate numbers and names are masked in some gate views (`gate/masking.py`).
- **Sessions** expire after 8 hours (a shift). Five failed logins lock the account for 30 minutes (django-axes), with a dedicated lockout screen.

## Capabilities and Constraints

**Confirmed capabilities:** role-based registration and login with lockout and password reset; administrator-controlled registration window; 4-step application wizard with temp-file persistence between steps; document upload (OR/CR and driver's license always, COR for students, authorization letter for non-owners); applicant-chosen appointment date and time with per-time-slot capacity; administrator application list, detail review, approve/reject with reason; sticker station that binds a pending RFID UID to an approved application; live gate view with today's entry/exit counts, active passes, hourly traffic, and latest scans; gate log browsing with CSV export; per-log incident report with PDF generation; time tracker for vehicles currently inside and overstays; immutable audit log across auth, application, gate, and admin events.

**Constraints:** Django 5.2 server-rendered templates, Bootstrap 5 + Bootstrap Icons from CDN, crispy-forms. No frontend build step and no JS framework — any design work ships as templates and CSS. SQLite. Django admin lives at `/palsu-system-admin-2025/`. `HTTPS_ENABLED=False` is the current accepted short-term LAN risk documented in `DEPLOYMENT.md`. English only (`LANGUAGE_CODE='en-us'`, `USE_I18N=True`); no Filipino/Tagalog requirement was established.

**Known limitation — payment is not tracked in the system.** The real-world process requires the applicant to pay a fee at the cashier between getting approved and getting their sticker issued, but that payment step happens entirely outside this app (no fee amount, receipt number, or "paid" status is stored anywhere). The intended order — verify documents/vehicle first, approve, *then* pay, *then* issue — is enforced only through the applicant-facing instructions on the "approved" status message (`templates/applications/my_applications.html`), not by any code path; an admin can still issue a sticker without checking payment, since there's nothing in the data model to check against. If a future team wants to close this gap, the natural place is a `paid_at`/`or_number` pair on `StickerApplication`, gating `issue_sticker` on it being set.

**Terminology to keep consistent:** *applicant*, *sticker administrator*, *security officer*; *registration window*; *appointment slot*; *sticker station*; *RFID UID*; *gate log*; *incident report*; *active pass*; statuses are exactly `draft / scheduled / approved / rejected / issued / expired`.

**Planned but not built** — known gaps that design must neither pretend are solved nor permanently design around:

- **Email notification of appointment, approval, and rejection.** Intended; not implemented. Until it ships, the applicant's status view carries the entire burden of telling someone what happened and what to do next. Design should not show a "we emailed you" affordance, and should leave a natural place for notification to arrive later.
- **Renewal.** Stickers expire 365 days after issue via the `expire_stickers` management command, and there is no renewal path — an expired applicant currently has to start over and re-upload every document. A lighter renewal reusing prior details is intended. Design must not dead-end an `expired` applicant with no stated next step.

**Explicitly undecided:** nothing further is deliberately open at this time.

## Brand Commitments

The name **PalSU Gate System** is used throughout the interface and in the Django admin branding.

**No binding visual commitments exist.** The current navy (`#0A1628`) + gold (`#C9A84C`) palette is the author's own choice and is explicitly *not* institutional — future design may change it. There is **no official PalSU seal or logo available**; `static/` is empty and the topbar currently uses a generic Bootstrap icon as a placeholder mark. Future work must not fabricate an institutional seal, crest, or official university branding; a typographic wordmark is the honest solution until a real asset is supplied.

## Evidence on Hand

- Real, working application code across seven Django apps, with a documented deployment guide (`DEPLOYMENT.md`) and a security/reliability audit already committed.
- 22 existing templates covering every role's surfaces.
- **No real user data, no testimonials, no adoption metrics, no pilot results, and no institutional endorsement exist.** Future work must not invent usage numbers, quotes from officers or students, approval from university administration, or claims that the system is in production.
- Gate hardware is partially built; there is no evidence of sustained real-world scanning.

## Product Principles

1. **The record must be defensible.** Anything an administrator or officer does that affects a person's campus access is logged, attributable, and exportable. Design never makes a consequential action feel casual.
2. **The applicant is a one-time visitor, not a power user.** They will not learn the system. Every step must be legible cold, on a phone, without prior context — including what happens next and how long it takes.
3. **The status view is the only messenger.** With no notifications, nothing reaches an applicant unless they come back and look. Their status screen must therefore answer "what happened, what do I do now, and when" without being asked — for every status including `rejected` and `expired`, which are the ones a person most needs to act on.
4. **Two guard scenes, one system.** Live gate views are read at a distance while vehicles are arriving; log and incident views are worked seated and deliberately. Do not design them as the same screen.
5. **Never claim more than exists.** No fabricated seals, endorsements, metrics, or production status. The system's credibility comes from the completeness of its chain of custody, not from decoration.
6. **Degrade honestly.** Hardware is partial and the scan stream may be empty. Empty, stale, and disconnected states are first-class designs, not afterthoughts.

## Accessibility & Inclusion

No formal standard was mandated. Two product-specific needs are established: the guardhouse gate view must remain legible on a fixed monitor read from a distance in daylight, and the applicant flow must be fully usable on a phone, since applicants arrive on mixed devices with no fallback path.
