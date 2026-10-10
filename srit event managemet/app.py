import os
import json
import uuid
import secrets
import logging
import requests
from urllib import error as urllib_error
from urllib import request as urllib_request
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, send_from_directory, send_file, abort
)
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from certificate_generator import generate_certificate
from report_generator import generate_event_pdf_report, generate_sop_pdf_report
from google_drive_storage import (
    upload_file_to_drive,
    download_drive_file,
    delete_drive_file,
    is_drive_configured,
    get_root_folder_id,
)
import firebase_admin
from firebase_admin import auth as admin_auth
from firebase_admin import firestore

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CERT_DIR = os.path.join(BASE_DIR, "certificates")
CERT_ASSETS_DIR = os.path.join(BASE_DIR, "static", "cert_assets")  # logos/sigs for cert templates
ALLOWED_REPORT_TYPES = {"event_report", "sop_report"}
ALLOWED_REPORT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
ALLOWED_REPORT_IMAGE_MIME_TYPES = {"image/jpeg", "image/png"}
MAX_REPORT_UPLOAD_BYTES = 5 * 1024 * 1024


def load_startup_environment():
    """Load local environment settings without overriding the process environment."""
    loaded_files = []
    for env_path in (os.path.join(BASE_DIR, ".env"), os.path.join(os.getcwd(), ".env")):
        if env_path in loaded_files or not os.path.isfile(env_path):
            continue
        try:
            with open(env_path, "r", encoding="utf-8") as env_file:
                for raw_line in env_file:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and not os.environ.get(key):
                        os.environ[key] = value
            loaded_files.append(env_path)
        except Exception:
            continue
    return loaded_files


STARTUP_ENV_FILES = load_startup_environment()
FIREBASE_WEB_API_KEY = os.environ.get("FIREBASE_WEB_API_KEY", "")
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "")
FIREBASE_AUTH_DOMAIN = os.environ.get("FIREBASE_AUTH_DOMAIN", "")


def production_environment():
    return any(
        os.environ.get(name, "").strip().lower() == "production"
        for name in ("FLASK_ENV", "APP_ENV", "ENV")
    )


def get_secret_key():
    secret_key = (os.environ.get("SECRET_KEY") or "").strip()
    if secret_key:
        return secret_key
    if production_environment():
        raise RuntimeError("SECRET_KEY environment variable is required in production.")
    return secrets.token_urlsafe(32)


app = Flask(__name__)
app.config["SECRET_KEY"] = get_secret_key()
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
csrf = CSRFProtect(app)
logger = app.logger

os.makedirs(CERT_DIR, exist_ok=True)
os.makedirs(CERT_ASSETS_DIR, exist_ok=True)


_firestore_client = None
_firestore_connection_message = "Firestore has not been checked."
FIREBASE_ADMIN_INITIALIZED = False


USER_PROFILE_MIRROR_COLLECTION = "user_profiles"
OFFICIAL_EMAIL_DOMAIN = "sritcbe.ac.in"
USER_ROLES = ("student", "faculty", "admin")
EVENT_STATUSES = ("Upcoming", "Open", "Ongoing", "Completed", "Cancelled")
CERTIFICATE_TYPES = ("Participation", "Winner", "Merit")


EVENT_PROFILE_FIELDS = (
    "id", "title", "academic_year", "program_name", "auto_credit_quarter",
    "organizing_details", "spc_mapping", "objectives", "benefits", "venue",
    "event_date", "start_time", "end_time", "estimated_expenditure", "capacity",
    "registration_fee", "description", "status", "created_by", "created_at",
)


class FirestoreEventCreationError(RuntimeError):
    """Fail closed when Firestore Event creation cannot be verified."""
    def __init__(self, message):
        super().__init__(message)


class FirestoreEventReadError(RuntimeError):
    """Fail closed when Firestore-authoritative Event data cannot be read."""


REGISTRATION_PROFILE_FIELDS = (
    "id", "event_id", "student_id", "registered_at",
    "attendance", "checkin_token", "certificate_type", "certificate_file",
)


class FirestoreRegistrationCreationError(RuntimeError):
    """Fail closed when Firestore Registration creation cannot be verified."""
    def __init__(self, message):
        super().__init__(message)


class FirestoreRegistrationReadError(RuntimeError):
    """Fail closed when Firestore-authoritative Registration data cannot be read."""


class FirestoreUpdateCreationError(RuntimeError):
    """Fail closed when Firestore Update creation cannot be verified."""
    def __init__(self, message):
        super().__init__(message)


class FirestoreFeedbackCreationError(RuntimeError):
    """Fail closed when Firestore Feedback creation cannot be verified."""
    def __init__(self, message):
        super().__init__(message)


class FirestoreFeedbackReadError(RuntimeError):
    """Fail closed when Firestore-authoritative Feedback data cannot be read."""


class FirestoreUserProfileReadError(RuntimeError):
    """Fail closed when Firestore-authoritative User Profile data cannot be read."""


class FirestoreTranscriptCreationError(RuntimeError):
    """Fail closed when Firestore Transcript creation cannot be verified."""
    def __init__(self, message):
        super().__init__(message)


class FirestoreUserProfileCreationError(RuntimeError):
    """Fail closed when Firestore-authoritative User Profile creation fails."""


def get_firestore_connection_message():
    """Return the latest message describing Firestore connection status."""
    return _firestore_connection_message


def report_upload_size(uploaded_file):
    """Return the size in bytes of an uploaded file without consuming the stream permanently."""
    current_position = uploaded_file.stream.tell()
    uploaded_file.stream.seek(0, os.SEEK_END)
    size = uploaded_file.stream.tell()
    uploaded_file.stream.seek(current_position)
    return size


def report_upload_has_image_signature(header_bytes):
    """Return True if initial stream bytes match common JPEG or PNG file signatures."""
    if not header_bytes:
        return False
    if header_bytes.startswith(b"\xff\xd8\xff"):
        return True
    if header_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    return False


def validate_report_photo_upload(uploaded_file):
    """Validate an uploaded report photo for safe extension, size, and header content."""
    filename = secure_filename(uploaded_file.filename or "")
    if not filename:
        return None, "Uploaded file must include a valid filename."
    _, ext = os.path.splitext(filename)
    if ext.lower() not in ALLOWED_REPORT_IMAGE_EXTENSIONS:
        return None, "Report photos must be in JPEG (.jpg, .jpeg) or PNG (.png) format."
    if uploaded_file.mimetype and uploaded_file.mimetype.lower() not in ALLOWED_REPORT_IMAGE_MIME_TYPES:
        return None, "Report photos must use a valid image MIME type (image/jpeg or image/png)."
    if report_upload_size(uploaded_file) > MAX_REPORT_UPLOAD_BYTES:
        return None, "Report photos must be smaller than 5 MB per image."
    header = uploaded_file.stream.read(16)
    uploaded_file.stream.seek(0)
    if not report_upload_has_image_signature(header):
        return None, "Uploaded file does not match a valid JPEG or PNG image signature."
    return filename, None


def mask_firebase_config_value(value):
    """Return a masked configuration value suitable for startup logging."""
    if not value:
        return "missing"
    return f"loaded length={len(value)} prefix={value[:6]}..."


def mask_auth_email(email):
    """Mask the email username while keeping the domain visible for auth diagnostics."""
    email = email or ""
    if "@" not in email:
        return "<invalid-email>"
    username, domain = email.split("@", 1)
    if len(username) <= 2:
        masked_username = username[:1] + "*"
    else:
        masked_username = username[:1] + ("*" * (len(username) - 2)) + username[-1:]
    return f"{masked_username}@{domain}"


def log_firebase_auth_startup_diagnostics():
    """Log Firebase configuration state without changing authentication behavior."""
    credential_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    web_api_key = os.environ.get("FIREBASE_WEB_API_KEY")
    fallback_api_key = os.environ.get("FIREBASE_API_KEY")
    auth_domain = os.environ.get("FIREBASE_AUTH_DOMAIN")
    configured_project_id = os.environ.get("FIREBASE_PROJECT_ID")
    credential_exists = bool(credential_path and os.path.isfile(credential_path))
    service_account_project_id = None

    if credential_exists:
        try:
            with open(credential_path, "r", encoding="utf-8") as credential_file:
                service_account_project_id = json.load(credential_file).get("project_id")
        except Exception as exc:
            app.logger.warning("Firebase startup diagnostics could not read service-account project_id: %s", exc)

    client = get_firestore_client()
    admin_project_id = None
    if client is not None:
        try:
            import firebase_admin
            admin_project_id = firebase_admin.get_app().project_id
        except Exception as exc:
            app.logger.warning("Firebase startup diagnostics could not read Admin SDK project_id: %s", exc)

    web_project_matches_service_account = (
        service_account_project_id == configured_project_id
        if service_account_project_id and configured_project_id
        else "unknown"
    )

    app.logger.info(
        "Firebase startup diagnostics: env_files_loaded=%s, GOOGLE_APPLICATION_CREDENTIALS exists=%s, "
        "service_account_project_id=%s, FIREBASE_WEB_API_KEY=%s, FIREBASE_API_KEY=%s, "
        "FIREBASE_AUTH_DOMAIN=%s, web_config_project_id=%s, admin_sdk_project_id=%s, "
        "web_project_matches_service_account=%s",
        STARTUP_ENV_FILES or "none",
        credential_exists,
        service_account_project_id or "missing",
        mask_firebase_config_value(web_api_key),
        mask_firebase_config_value(fallback_api_key),
        auth_domain or "missing",
        configured_project_id or "missing",
        admin_project_id or "unavailable",
        web_project_matches_service_account,
    )



def get_firestore_client():
    """Initialize and return the Firestore client."""
    global _firestore_client
    global _firestore_connection_message
    global FIREBASE_ADMIN_INITIALIZED

    if _firestore_client is not None:
        return _firestore_client

    credential_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    credential_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

    try:
        try:
            firebase_app = firebase_admin.get_app()
            FIREBASE_ADMIN_INITIALIZED = True
        except ValueError:
            from firebase_admin import credentials as fb_credentials

            if credential_json:
                # Railway: load credentials from an environment variable
                service_account_info = json.loads(credential_json)
                cred = fb_credentials.Certificate(service_account_info)
                firebase_app = firebase_admin.initialize_app(cred)

            elif credential_path and os.path.isfile(credential_path):
                # Local development or a host with a mounted credential file
                cred = fb_credentials.Certificate(credential_path)
                firebase_app = firebase_admin.initialize_app(cred)

            else:
                _firestore_connection_message = (
                    "Firestore is not configured. Set "
                    "FIREBASE_SERVICE_ACCOUNT_JSON or provide a valid "
                    "GOOGLE_APPLICATION_CREDENTIALS file path."
                )
                FIREBASE_ADMIN_INITIALIZED = False
                return None

            FIREBASE_ADMIN_INITIALIZED = True

        _firestore_client = firestore.client(firebase_app)

    except Exception:
        app.logger.exception("Firebase Firestore initialization failed.")
        _firestore_connection_message = (
            "Firestore initialization failed. Check the Firebase credentials "
            "and project configuration."
        )
        FIREBASE_ADMIN_INITIALIZED = False
        return None

    _firestore_connection_message = "Firestore client initialized."
    return _firestore_client



def firestore_collection(name):
    """Return a Firestore collection by namespace."""
    client = get_firestore_client()
    if client is None:
        raise RuntimeError("Firestore is unavailable.")
    return client.collection(name)


def int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_canonical_user_uid(user_ref, email=None):
    """Resolve any user reference (legacy numeric ID, Firebase UID, or email) to canonical Firebase UID."""
    if not user_ref and not email:
        return ""
    ref_str = str(user_ref or "").strip()
    if email:
        em = str(email).strip().lower()
        user_by_em = find_firestore_user_by_email(em)
        if user_by_em and user_by_em.get("firebase_uid") and len(str(user_by_em["firebase_uid"])) > 20 and not str(user_by_em["firebase_uid"]).isdigit():
            return str(user_by_em["firebase_uid"])
    if ref_str:
        if len(ref_str) > 20 and not ref_str.isdigit():
            return ref_str
        prof = read_firestore_user_profile_by_firebase_uid(ref_str)
        if prof:
            if prof.get("firebase_uid") and len(str(prof["firebase_uid"])) > 20 and not str(prof["firebase_uid"]).isdigit():
                return str(prof["firebase_uid"])
            if prof.get("email"):
                canonical_user = find_firestore_user_by_email(prof["email"])
                if canonical_user and canonical_user.get("firebase_uid") and len(str(canonical_user["firebase_uid"])) > 20 and not str(canonical_user["firebase_uid"]).isdigit():
                    return str(canonical_user["firebase_uid"])
    return ref_str


def user_reference_matches(value, firebase_uid=None):
    """Return whether a stored user reference matches the Firebase UID."""
    if value is None or firebase_uid is None:
        return False
    if str(value) == str(firebase_uid):
        return True
    c_val = get_canonical_user_uid(value)
    c_uid = get_canonical_user_uid(firebase_uid)
    if c_val and c_uid and c_val == c_uid:
        return True
    return False


def event_profile_data(event):
    """Return the Firestore representation of an Event profile."""
    profile = {field: event.get(field) for field in EVENT_PROFILE_FIELDS}
    return profile


def firestore_event_ref(event_id):
    """Return the Firestore document reference for an Event ID."""
    return firestore_collection("events").document(str(event_id))


def firestore_event_matches(ref, profile):
    """Return whether a Firestore Event document matches the profile."""
    snapshot = ref.get()
    confirmed = snapshot.to_dict() if snapshot.exists else None
    return bool(confirmed) and all(confirmed.get(field) == value for field, value in profile.items() if field != "registrations_count")


def normalize_firestore_event(snapshot):
    """Return a template-compatible Event profile from Firestore authority."""
    if not snapshot.exists:
        return None
    event = snapshot.to_dict()
    try:
        event_id = int(event.get("id", snapshot.id))
    except (TypeError, ValueError) as exc:
        raise FirestoreEventReadError("Firestore Event profile has an invalid event ID.") from exc
    if str(event_id) != str(snapshot.id):
        raise FirestoreEventReadError("Firestore Event profile ID does not match its document ID.")

    # Hard-required fields — crash only on these
    for field in ("title", "event_date", "status", "created_by"):
        if event.get(field) in (None, ""):
            raise FirestoreEventReadError(f"Firestore Event profile is missing {field}.")

    # created_at: backward-compatible — derive from snapshot.create_time if absent
    if not event.get("created_at"):
        try:
            ct = snapshot.create_time  # google.protobuf.Timestamp or datetime
            if hasattr(ct, "isoformat"):
                event["created_at"] = ct.isoformat()
            elif hasattr(ct, "ToDatetime"):
                from datetime import timezone as _tz
                event["created_at"] = ct.ToDatetime(tzinfo=_tz.utc).isoformat()
            else:
                event["created_at"] = str(ct) if ct else ""
        except Exception:
            event["created_at"] = ""

    # Optional fields — fill with safe defaults so templates never KeyError
    event["id"] = event_id
    event["program_name"] = event.get("program_name") or ""
    event["venue"] = event.get("venue") or event.get("location") or ""
    event["academic_year"] = event.get("academic_year") or ""
    event["auto_credit_quarter"] = event.get("auto_credit_quarter") or ""
    event["organizing_details"] = event.get("organizing_details") or ""
    event["spc_mapping"] = event.get("spc_mapping") or ""
    event["objectives"] = event.get("objectives") or ""
    event["benefits"] = event.get("benefits") or ""
    event["start_time"] = event.get("start_time") or ""
    event["end_time"] = event.get("end_time") or ""
    event["estimated_expenditure"] = float(event.get("estimated_expenditure") or 0)
    event["capacity"] = int(event.get("capacity") or 0)
    event["registration_fee"] = float(event.get("registration_fee") or 0)
    event["description"] = event.get("description") or ""
    event["speaker_name"] = event.get("speaker_name") or ""
    event["speaker_designation"] = event.get("speaker_designation") or ""
    event["speaker_organization"] = event.get("speaker_organization") or ""
    event["speaker_email"] = event.get("speaker_email") or ""
    event["speaker_contact"] = event.get("speaker_contact") or ""
    event["speaker_expertise"] = event.get("speaker_expertise") or ""
    event["speaker_bio"] = event.get("speaker_bio") or ""
    event.setdefault("registrations_count", 0)
    return event



def read_firestore_event(event_id, created_by=None):
    """Read one Firestore-authoritative Event by integer ID."""
    try:
        event = normalize_firestore_event(firestore_event_ref(event_id).get())
    except FirestoreEventReadError:
        raise
    except Exception as exc:
        raise FirestoreEventReadError("Firestore Event profile could not be read.") from exc
    if not event:
        return None
    if created_by is not None and not user_reference_matches(
        event.get("created_by"),
        firebase_uid=created_by,
    ):
        return None
    return event


def read_firestore_events(filter_type=None, limit=None, search=None, status=None, created_by=None):
    """Read a list of Events from Firestore authority with filtering, search, and limits."""
    try:
        events = []
        for snapshot in firestore_collection("events").stream():
            try:
                event = normalize_firestore_event(snapshot)
            except FirestoreEventReadError as read_err:
                app.logger.warning("Skipping malformed Firestore event %s: %s", snapshot.id, read_err)
                continue
            if not event:
                continue

            if created_by is not None and not user_reference_matches(
                event.get("created_by"),
                firebase_uid=created_by,
            ):
                continue

            ev_status = (event.get("status") or "").strip()

            if status:
                if ev_status.lower() != status.strip().lower():
                    continue

            if filter_type and filter_type.strip().lower() != "all":
                ft = filter_type.strip().lower()
                if ft == "upcoming":
                    if ev_status.lower() not in ("upcoming", "open", "ongoing"):
                        continue
                elif ft == "completed":
                    if ev_status.lower() != "completed":
                        continue
                elif ft == "ongoing":
                    if ev_status.lower() != "ongoing":
                        continue
                elif ev_status.lower() != ft:
                    continue

            if search:
                needle = search.strip().lower()
                searchable = (
                    event.get("title") or "",
                    event.get("program_name") or "",
                    event.get("venue") or "",
                    event.get("description") or "",
                    event.get("category") or "",
                )
                if not any(needle in val.lower() for val in searchable):
                    continue

            events.append(event)
    except Exception as exc:
        app.logger.error("Firestore Event authority read failed: %s", exc)
        raise FirestoreEventReadError("Firestore Event authority is unavailable.") from exc

    events.sort(key=lambda event: event.get("event_date") or "")

    if limit is not None and isinstance(limit, int) and limit > 0:
        events = events[:limit]

    return events


def list_firestore_events(created_by=None, status=None, query=None):
    """Read Events from Firestore authority with template-compatible filters."""
    return read_firestore_events(created_by=created_by, status=status, search=query)


def create_firestore_authoritative_event(profile):
    """Create a new Firestore-authoritative Event."""
    event = dict(profile)
    try:
        if event.get("id") is None:
            existing_ids = []
            for snapshot in firestore_collection("events").stream():
                try:
                    existing_ids.append(int(snapshot.id))
                except (TypeError, ValueError):
                    # Skip documents with non-integer IDs (e.g. UID-keyed docs from older imports)
                    continue
            next_id = (max(existing_ids) if existing_ids else 0) + 1
        else:
            next_id = int(event["id"])

        for event_id in range(next_id, next_id + 100):
            candidate = {**event, "id": event_id}
            candidate.pop("created_by_sqlite_id", None)
            ref = firestore_event_ref(event_id)
            try:
                ref.create(candidate)
            except Exception as create_error:
                if event.get("id") is not None:
                    raise
                try:
                    if ref.get().exists:
                        continue
                except Exception:
                    pass
                raise create_error
            if not firestore_event_matches(ref, candidate):
                try:
                    ref.delete()
                except Exception as cleanup_error:
                    app.logger.error("Firestore Event cleanup failed after creation verification failure for event %s: %s", event_id, cleanup_error)
                raise FirestoreEventCreationError("Firestore Event creation could not be verified.")
            return read_firestore_event(event_id)
    except FirestoreEventCreationError:
        raise
    except Exception as create_error:
        raise FirestoreEventCreationError("Firestore Event creation failed.") from create_error
    raise FirestoreEventCreationError("Firestore Event ID allocation failed.")


def update_firestore_authoritative_event(event_id, updates):
    """Update and verify Firestore-authoritative Event metadata."""
    existing = read_firestore_event(event_id)
    if not existing:
        raise FirestoreEventReadError("Firestore Event profile is unavailable.")
    profile = {**existing, **updates}
    profile.pop("creator", None)
    profile.pop("registrations_count", None)
    profile.pop("created_by_sqlite_id", None)
    ref = firestore_event_ref(event_id)
    ref.set(profile)
    if not firestore_event_matches(ref, profile):
        raise RuntimeError("Firestore Event update could not be verified.")
    return read_firestore_event(event_id)


def delete_firestore_authoritative_event(event_id):
    """Delete and verify one Firestore-authoritative Event document."""
    ref = firestore_event_ref(event_id)
    ref.delete()
    if ref.get().exists:
        raise RuntimeError("Firestore Event delete could not be verified.")


def event_from_firestore(event):
    """Return a template-compatible Event dict."""
    return dict(event)


def event_profiles_from_firestore(events):
    """Return template-compatible Event rows."""
    return [event_from_firestore(event) for event in events]


def attach_firestore_event_creators(events):
    """Attach creator names from Firestore User profiles to existing Event rows."""
    creator_ids = sorted({event.get("created_by") for event in events if event.get("created_by") is not None}, key=str)
    creator_names = {}
    if creator_ids:
        creator_names = {
            user_id: profile["name"]
            for user_id, profile in read_firestore_user_profiles(creator_ids).items()
            if profile
        }
    for event in events:
        event["creator"] = creator_names.get(event.get("created_by"))
    return events


def registration_profile_data(registration):
    """Return the non-sensitive Firestore authority profile for a Registration."""
    return {
        "id": registration["id"],
        "event_id": registration["event_id"],
        "student_id": registration["student_id"],
        "registered_at": registration.get("registered_at"),
        "attendance": int(registration.get("attendance") or 0),
        "checkin_token": registration.get("checkin_token"),
        "certificate_type": registration.get("certificate_type"),
        "certificate_file": registration.get("certificate_file"),
    }


def firestore_registration_ref(registration_id):
    """Return the Firestore document reference for a Registration ID."""
    return firestore_collection("registrations").document(str(registration_id))


def firestore_registration_key(event_id, student_id):
    """Return the natural identity string for an (event_id, student_id) registration."""
    return f"{event_id}:{student_id}"


def firestore_registration_doc_id(event_id, student_id):
    """Return the deterministic Firestore document ID for a registration pair."""
    return f"{event_id}_{student_id}"


def firestore_registration_matches(ref, profile):
    """Return whether a Firestore Registration document matches the profile."""
    snapshot = ref.get()
    confirmed = snapshot.to_dict() if snapshot.exists else None
    return bool(confirmed) and all(confirmed.get(field) == value for field, value in profile.items() if field not in ("event", "student"))


def normalize_firestore_registration(snapshot):
    """Return a template-compatible Registration profile from Firestore authority."""
    if not snapshot.exists:
        return None
    registration = snapshot.to_dict()
    try:
        event_id = int(registration.get("event_id"))
    except (TypeError, ValueError) as exc:
        raise FirestoreRegistrationReadError("Firestore Registration has an invalid event ID.") from exc
    student_id = registration.get("student_id") or registration.get("student_firebase_uid")
    if not student_id:
        raise FirestoreRegistrationReadError("Firestore Registration is missing a student identifier.")
    registration["id"] = snapshot.id
    registration["event_id"] = event_id
    registration["student_id"] = student_id
    registration["registered_at"] = registration.get("registered_at") or ""
    registration["attendance"] = int(registration.get("attendance") or 0)
    att_status = registration.get("attendance_status")
    if not att_status:
        att_status = "present" if registration["attendance"] == 1 else "registered"
    registration["attendance_status"] = att_status
    registration["checkin_token"] = registration.get("checkin_token") or ""
    registration["certificate_type"] = registration.get("certificate_type") or ""
    registration["certificate_file"] = registration.get("certificate_file") or ""
    return registration


def list_firestore_registrations(event_id=None, student_id=None, include_event=False, include_user=False):
    """Read Registrations from Firestore authority and enrich profiles from Firestore, deduplicated per (event_id, student_id)."""
    try:
        snapshots = firestore_collection("registrations").stream()
        registrations_by_key = {}
        for snapshot in snapshots:
            registration = normalize_firestore_registration(snapshot)
            if not registration:
                continue
            
            ev_id = str(registration.get("event_id"))
            st_id = str(registration.get("student_id"))
            
            if event_id is not None and ev_id != str(event_id):
                continue
            if student_id is not None and not user_reference_matches(
                registration.get("student_id"),
                firebase_uid=student_id,
            ):
                continue

            raw_email = registration.get("user_email") or registration.get("student_email")
            canonical_st_id = get_canonical_user_uid(st_id, email=raw_email)
            dedup_key = (ev_id, canonical_st_id)
            if dedup_key in registrations_by_key:
                existing = registrations_by_key[dedup_key]
                # Merge attendance: prefer present/absent over pending
                if (registration.get("attendance_status") in ("present", "attended") or registration.get("attendance") == 1):
                    existing["attendance"] = 1
                    existing["attendance_status"] = "present"
                elif registration.get("attendance_status") == "absent" and existing.get("attendance_status") != "present":
                    existing["attendance"] = 0
                    existing["attendance_status"] = "absent"

                # Certificate is only valid if student is PRESENT
                if existing.get("attendance_status") == "present":
                    if registration.get("certificate_file") and not existing.get("certificate_file"):
                        existing["certificate_file"] = registration.get("certificate_file")
                        existing["certificate_type"] = registration.get("certificate_type")
                else:
                    existing["certificate_file"] = None
                    existing["certificate_type"] = None

                # Keep earliest registration time if present
                if registration.get("registered_at") and (not existing.get("registered_at") or registration.get("registered_at") < existing.get("registered_at")):
                    existing["registered_at"] = registration.get("registered_at")
                # Prefer canonical doc ID and canonical student_id
                canonical_id = firestore_registration_doc_id(ev_id, canonical_st_id)
                if registration.get("id") == canonical_id or existing.get("id") != canonical_id:
                    existing["id"] = canonical_id
                existing["student_id"] = canonical_st_id
            else:
                if registration.get("attendance_status") == "absent":
                    registration["certificate_file"] = None
                    registration["certificate_type"] = None
                registration["student_id"] = canonical_st_id
                registrations_by_key[dedup_key] = registration

        registrations = list(registrations_by_key.values())
    except Exception as exc:
        app.logger.error("Firestore Registration authority read failed: %s", exc)
        raise FirestoreRegistrationReadError("Firestore Registration authority is unavailable.") from exc

    if include_event and registrations:
        event_ids = sorted({registration.get("event_id") for registration in registrations if registration.get("event_id") is not None})
        event_profiles = {}
        if event_ids:
            event_profiles = {
                evt_id: read_firestore_event(evt_id)
                for evt_id in event_ids
            }
            if any(evt is None for evt in event_profiles.values()):
                raise FirestoreEventReadError("A referenced Firestore Event profile is unavailable.")
        for registration in registrations:
            evt = event_profiles.get(registration.get("event_id"))
            if evt:
                registration["title"] = evt["title"]
                registration["event_date"] = evt["event_date"]
                registration["venue"] = evt["venue"]
                registration["status"] = evt["status"]

    if include_user and registrations:
        student_ids = sorted({registration.get("student_id") for registration in registrations if registration.get("student_id") is not None}, key=str)
        user_profiles = {}
        if student_ids:
            user_profiles = read_firestore_user_profiles(student_ids)
        for registration in registrations:
            usr = user_profiles.get(str(registration.get("student_id"))) or user_profiles.get(registration.get("student_id"))
            if usr:
                registration["student_name"] = usr.get("name")
                registration["student_email"] = usr.get("email")
                registration["name"] = usr.get("name") or registration.get("student_name") or "Student"
                registration["email"] = usr.get("email") or registration.get("student_email") or ""
                registration["reg_no"] = usr.get("reg_no") or ""
                registration["department"] = usr.get("department") or ""
                registration["year"] = usr.get("year") or ""
            else:
                registration["name"] = registration.get("student_name") or "Student"
                registration["email"] = registration.get("student_email") or ""
                registration["reg_no"] = registration.get("reg_no") or ""
                registration["department"] = registration.get("department") or ""
                registration["year"] = registration.get("year") or ""

    registrations.sort(key=lambda reg: reg.get("registered_at") or "", reverse=True)
    return registrations


def get_firestore_registration(registration_id, event_id=None, student_id=None):
    """Read one Registration from Firestore authority."""
    try:
        registration = normalize_firestore_registration(firestore_registration_ref(registration_id).get())
    except Exception as exc:
        raise FirestoreRegistrationReadError("Firestore Registration could not be read.") from exc
    if not registration:
        return None
    if event_id is not None and str(registration.get("event_id")) != str(event_id):
        return None
    if student_id is not None and not user_reference_matches(
        registration.get("student_id"),
        firebase_uid=student_id,
    ):
        return None
    return registration


def find_firestore_registration(event_id, student_id):
    """Return the Firestore-authoritative Registration for an event/student pair."""
    registrations = list_firestore_registrations(event_id=event_id, student_id=student_id)
    return registrations[0] if registrations else None


def resolve_firestore_registration_for_event(event_id, submitted_registration_id):
    """Resolve a form-submitted Registration identifier to the authoritative Firestore document."""
    submitted_registration_id = str(submitted_registration_id)
    registration = get_firestore_registration(submitted_registration_id, event_id=event_id)
    if registration:
        return registration
    for candidate in list_firestore_registrations(event_id=event_id):
        if str(candidate.get("id")) == submitted_registration_id:
            return candidate
    return None


def count_firestore_registrations(event_id=None, student_id=None, attendance=None, certificate=False):
    """Count Firestore-authoritative Registrations with simple filters."""
    registrations = list_firestore_registrations(event_id=event_id, student_id=student_id)
    if attendance is not None:
        registrations = [reg for reg in registrations if reg.get("attendance") == attendance]
    if certificate:
        registrations = [reg for reg in registrations if reg.get("certificate_file")]
    return len(registrations)


def registration_counts_by_event(registrations):
    """Return Registration totals by event ID."""
    counts = {}
    for registration in registrations:
        event_id = registration.get("event_id")
        if event_id is not None:
            counts[str(event_id)] = counts.get(str(event_id), 0) + 1
    return counts


def read_firestore_registrations_for_user(user_id):
    """Read all registrations for a given user / Firebase UID."""
    return list_firestore_registrations(student_id=user_id, include_event=True)


def read_firestore_registrations_for_event(event_id):
    """Read all registrations for a given event ID."""
    return list_firestore_registrations(event_id=event_id, include_user=True)


def read_firestore_user_profiles(user_ids):
    """Read multiple user profiles from Firestore by ID / Firebase UID."""
    if not user_ids:
        return {}
    profiles = {}
    for uid in user_ids:
        if not uid:
            continue
        prof = read_firestore_user_profile_by_firebase_uid(str(uid))
        if prof:
            profiles[str(uid)] = prof
    return profiles


def create_firestore_authoritative_registration(
    event_id,
    student_id=None,
    capacity=None,
    user_firebase_uid=None,
    user_name=None,
    user_email=None,
):
    """Create a Registration in Firestore with atomic duplicate and capacity checks."""
    raw_student_id = str(student_id or user_firebase_uid or "")
    if not raw_student_id:
        raise ValueError("student_id or user_firebase_uid is required.")

    canonical_uid = get_canonical_user_uid(raw_student_id, email=user_email)
    student_id = canonical_uid or raw_student_id

    if capacity is None:
        ev = read_firestore_event(event_id)
        if ev:
            capacity = ev.get("capacity") or 0

    client = get_firestore_client()
    if client is None:
        raise RuntimeError("Firestore is unavailable.")
    collection = client.collection("registrations")
    registration_id = firestore_registration_doc_id(event_id, student_id)
    ref = collection.document(registration_id)
    transaction = client.transaction()

    from google.cloud import firestore

    @firestore.transactional
    def create_in_transaction(transaction):
        existing = ref.get(transaction=transaction)
        if existing.exists:
            return normalize_firestore_registration(existing), "duplicate"

        # Check all existing registrations to prevent any duplicate for this (event_id, student_id)
        all_snapshots = list(collection.stream(transaction=transaction))
        event_registrations = []
        for snap in all_snapshots:
            candidate = normalize_firestore_registration(snap)
            if candidate and str(candidate.get("event_id")) == str(event_id):
                event_registrations.append(candidate)
                cand_st_id = candidate.get("student_id")
                if user_reference_matches(cand_st_id, firebase_uid=student_id) or (
                    user_email and str(candidate.get("user_email", "")).strip().lower() == str(user_email).strip().lower()
                ):
                    return candidate, "duplicate"

        if capacity and len(event_registrations) >= capacity:
            return None, "capacity"

        token = secrets.token_urlsafe(10)
        profile = {
            "id": registration_id,
            "registration_key": firestore_registration_key(event_id, student_id),
            "event_id": int(event_id) if str(event_id).isdigit() else str(event_id),
            "student_id": student_id,
            "user_name": user_name or "",
            "user_email": user_email or "",
            "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "attendance": 0,
            "attendance_status": "registered",
            "checkin_token": token,
            "certificate_type": None,
            "certificate_file": None,
        }
        transaction.create(ref, profile)
        return profile, "created"

    registration, state = create_in_transaction(transaction)
    if state == "created":
        confirmed = ref.get().to_dict()
        if not confirmed:
            raise RuntimeError("Firestore Registration creation could not be verified.")
    return registration, state


def cleanup_duplicate_firestore_registrations(event_id=None):
    """Clean up existing duplicate registration documents in Firestore safely.
    Preserves certificates, attendance, and all valid data onto the canonical doc."""
    try:
        client = get_firestore_client()
        if client is None:
            return
        collection = client.collection("registrations")
        snapshots = list(collection.stream())

        # Group by (event_id, canonical_student_id)
        grouped = {}
        for snap in snapshots:
            reg = normalize_firestore_registration(snap)
            if not reg:
                continue
            ev_id = str(reg.get("event_id"))
            st_id = str(reg.get("student_id"))
            if event_id is not None and ev_id != str(event_id):
                continue
            raw_email = reg.get("user_email") or reg.get("student_email")
            canonical_st_id = get_canonical_user_uid(st_id, email=raw_email)
            key = (ev_id, canonical_st_id)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append((snap.id, reg, snap.to_dict() or {}))

        for (ev_id, canonical_st_id), docs in grouped.items():
            if len(docs) > 1:
                canonical_doc_id = firestore_registration_doc_id(ev_id, canonical_st_id)
                merged_data = {
                    "id": canonical_doc_id,
                    "registration_key": firestore_registration_key(ev_id, canonical_st_id),
                    "event_id": int(ev_id) if ev_id.isdigit() else ev_id,
                    "student_id": canonical_st_id,
                    "user_name": "",
                    "user_email": "",
                    "registered_at": "",
                    "attendance": 0,
                    "attendance_status": "registered",
                    "checkin_token": "",
                    "certificate_type": None,
                    "certificate_file": None,
                }

                # Consolidate values across duplicates
                for doc_id, norm_reg, raw_dict in docs:
                    if raw_dict.get("user_name") and not merged_data["user_name"]:
                        merged_data["user_name"] = raw_dict["user_name"]
                    if raw_dict.get("user_email") and not merged_data["user_email"]:
                        merged_data["user_email"] = raw_dict["user_email"]
                    if raw_dict.get("registered_at"):
                        if not merged_data["registered_at"] or raw_dict["registered_at"] < merged_data["registered_at"]:
                            merged_data["registered_at"] = raw_dict["registered_at"]
                    # Preserve attendance (prefer present/absent over pending)
                    if raw_dict.get("attendance") == 1 or raw_dict.get("attendance_status") in ("present", "attended"):
                        merged_data["attendance"] = 1
                        merged_data["attendance_status"] = "present"
                    elif raw_dict.get("attendance_status") == "absent" and merged_data["attendance_status"] != "present":
                        merged_data["attendance"] = 0
                        merged_data["attendance_status"] = "absent"
                    # Preserve certificate metadata
                    if raw_dict.get("certificate_file"):
                        merged_data["certificate_file"] = raw_dict["certificate_file"]
                        merged_data["certificate_type"] = raw_dict.get("certificate_type")
                        if raw_dict.get("certificate_generated_at"):
                            merged_data["certificate_generated_at"] = raw_dict["certificate_generated_at"]
                    if raw_dict.get("checkin_token") and not merged_data["checkin_token"]:
                        merged_data["checkin_token"] = raw_dict["checkin_token"]

                if not merged_data["checkin_token"]:
                    merged_data["checkin_token"] = secrets.token_urlsafe(10)

                # Set the merged canonical doc
                collection.document(canonical_doc_id).set(merged_data)

                # Delete redundant duplicate docs that are not the canonical doc
                for doc_id, norm_reg, raw_dict in docs:
                    if doc_id != canonical_doc_id:
                        collection.document(doc_id).delete()
                        logger.info(f"Cleaned duplicate registration doc {doc_id} for event {ev_id}, student {canonical_st_id}")
    except Exception as e:
        logger.warning(f"Error cleaning duplicate registrations: {e}")


def update_firestore_authoritative_registration(registration_id, updates):
    """Update and verify Firestore-authoritative Registration metadata."""
    ref = firestore_registration_ref(registration_id)
    before = ref.get()
    if not before.exists:
        raise RuntimeError("Firestore Registration profile is unavailable for update.")
    profile = {**before.to_dict(), **updates}
    ref.set(profile)
    confirmed = ref.get().to_dict()
    if not confirmed:
        raise RuntimeError("Firestore Registration update could not be verified.")
    return normalize_firestore_registration(ref.get())


def delete_firestore_authoritative_registration(registration_id):
    """Delete and verify a Firestore-authoritative Registration idempotently."""
    ref = firestore_registration_ref(registration_id)
    ref.delete()
    if ref.get().exists:
        raise RuntimeError("Firestore Registration delete could not be verified.")


# ---------------------------------------------------------
# FIRESTORE EVENT UPDATES HELPERS
# ---------------------------------------------------------
def firestore_update_ref(update_id):
    return firestore_collection('updates').document(str(update_id))


def normalize_firestore_update(doc):
    if not doc or not doc.exists:
        return None
    data = doc.to_dict() or {}
    return {
        "id": doc.id,
        "update_id": doc.id,
        "event_id": str(data.get("event_id") or ""),
        "title": data.get("title") or "Event Update",
        "event_title": data.get("event_title") or "",
        "message": data.get("message") or "",
        "created_by": data.get("created_by") or data.get("created_by_firebase_uid") or "",
        "created_by_firebase_uid": data.get("created_by_firebase_uid") or data.get("created_by") or "",
        "created_at": data.get("created_at") or "",
        "timestamp": data.get("timestamp") or data.get("created_at") or "",
    }


def read_firestore_updates_for_event(event_id):
    query = firestore_collection('updates').where('event_id', '==', str(event_id))
    docs = query.stream()
    updates = [normalize_firestore_update(d) for d in docs]
    updates = [u for u in updates if u]
    updates.sort(key=lambda x: str(x.get("created_at") or x.get("timestamp") or ""), reverse=True)
    return updates


def read_all_firestore_updates(limit=10):
    try:
        docs = firestore_collection('updates').limit(limit).stream()
        updates = [normalize_firestore_update(d) for d in docs]
        updates = [u for u in updates if u]
        updates.sort(key=lambda x: str(x.get("created_at") or x.get("timestamp") or ""), reverse=True)
        return updates
    except Exception:
        return []


def create_firestore_authoritative_update(event_id, message, created_by_firebase_uid=None, title=""):
    update_id = str(uuid.uuid4())
    ref = firestore_collection('updates').document(update_id)
    now_iso = datetime.now(timezone.utc).isoformat()
    ev = read_firestore_event(event_id)
    ev_title = (ev.get("title") if ev else "") or ""
    data = {
        "update_id": update_id,
        "event_id": str(event_id),
        "title": title or "Event Update",
        "event_title": ev_title,
        "message": message,
        "created_by": created_by_firebase_uid,
        "created_by_firebase_uid": created_by_firebase_uid,
        "created_at": now_iso,
        "timestamp": now_iso,
    }
    ref.set(data)
    doc = ref.get()
    if not doc.exists:
        raise RuntimeError("Firestore Update creation could not be verified.")
    return normalize_firestore_update(doc)


# ---------------------------------------------------------
# FIRESTORE FEEDBACK HELPERS
# ---------------------------------------------------------
def firestore_feedback_ref(feedback_id):
    return firestore_collection('feedback').document(str(feedback_id))


def normalize_firestore_feedback(doc):
    if not doc or not doc.exists:
        return None
    data = doc.to_dict() or {}
    q1 = int(data.get("q1_rating") or data.get("rating") or 5)
    q2 = int(data.get("q2_rating") or data.get("rating") or 5)
    q3 = int(data.get("q3_rating") or data.get("rating") or 5)
    q4 = data.get("q4_response") or data.get("comments") or data.get("feedback_text") or ""
    q5 = data.get("q5_response") or ""
    student_uid = data.get("student_uid") or data.get("user_firebase_uid") or data.get("user_id") or ""
    student_name = data.get("student_name") or data.get("user_name") or data.get("name") or ""
    submitted_at = data.get("submitted_at") or data.get("created_at") or ""
    return {
        "id": doc.id,
        "feedback_id": doc.id,
        "event_id": str(data.get("event_id") or ""),
        "student_uid": student_uid,
        "user_id": student_uid,
        "user_firebase_uid": student_uid,
        "student_name": student_name,
        "user_name": student_name,
        "submitted_at": submitted_at,
        "created_at": submitted_at,
        "q1_rating": q1,
        "q2_rating": q2,
        "q3_rating": q3,
        "q4_response": q4,
        "q5_response": q5,
        "rating": q1,
        "comments": q4,
    }


def read_firestore_feedback_for_event(event_id):
    query = firestore_collection('feedback').where('event_id', '==', str(event_id))
    docs = query.stream()
    feedbacks = [normalize_firestore_feedback(d) for d in docs]
    feedbacks = [f for f in feedbacks if f]

    # Deduplicate per student to ensure exactly one feedback record per student per event
    deduped = {}
    for fb in feedbacks:
        st_uid = get_canonical_user_uid(fb.get("student_uid")) or fb.get("student_uid")
        key = str(st_uid)
        if key not in deduped or (fb.get("submitted_at") or "") > (deduped[key].get("submitted_at") or ""):
            deduped[key] = fb

    result = list(deduped.values())
    result.sort(key=lambda x: str(x.get("submitted_at") or ""), reverse=True)
    return result


def count_firestore_feedback(event_id=None, user_id=None):
    try:
        col = firestore_collection('feedback')
        if event_id:
            return len(read_firestore_feedback_for_event(event_id))
        if user_id:
            c_uid = get_canonical_user_uid(user_id) or str(user_id)
            return len(list(col.where('student_uid', '==', c_uid).stream()))
        return len(list(col.limit(200).stream()))
    except Exception:
        return 0


def create_firestore_authoritative_feedback(
    event_id,
    user_firebase_uid,
    q1_rating=5,
    q2_rating=5,
    q3_rating=5,
    q4_response="",
    q5_response="",
    rating=None,
    comments=None,
):
    """Store or update student feedback idempotently with the 5 common questions."""
    # Support legacy rating / comments if passed
    if rating is not None and not q1_rating:
        q1_rating = rating
    if comments and not q4_response:
        q4_response = comments

    student_uid = get_canonical_user_uid(user_firebase_uid) or str(user_firebase_uid)
    user_prof = read_firestore_user_profile_by_firebase_uid(student_uid)
    student_name = (user_prof.get("name") if user_prof else None) or "Student"
    now_iso = datetime.now(timezone.utc).isoformat()

    feedback_doc_id = f"{event_id}_{student_uid}"
    ref = firestore_collection('feedback').document(feedback_doc_id)

    # Check for existing feedback by this user for this event to avoid duplicates
    existing_feedback = read_firestore_feedback_for_event(event_id)
    target_ref = ref
    for fb in existing_feedback:
        if user_reference_matches(fb.get("student_uid") or fb.get("user_firebase_uid") or fb.get("user_id"), student_uid):
            doc_id = fb.get("id") or fb.get("feedback_id")
            if doc_id:
                target_ref = firestore_collection('feedback').document(str(doc_id))
            break

    data = {
        "id": target_ref.id,
        "feedback_id": target_ref.id,
        "event_id": str(event_id),
        "student_uid": student_uid,
        "user_id": student_uid,
        "user_firebase_uid": student_uid,
        "student_name": student_name,
        "user_name": student_name,
        "submitted_at": now_iso,
        "created_at": now_iso,
        "q1_rating": int(q1_rating),
        "q2_rating": int(q2_rating),
        "q3_rating": int(q3_rating),
        "q4_response": str(q4_response or "").strip(),
        "q5_response": str(q5_response or "").strip(),
        "rating": int(q1_rating),
        "comments": str(q4_response or "").strip(),
    }
    target_ref.set(data)
    doc = target_ref.get()
    if not doc.exists:
        raise RuntimeError("Firestore Feedback creation could not be verified.")
    return normalize_firestore_feedback(doc)


# ---------------------------------------------------------
# FIRESTORE TRANSCRIPTS HELPERS
# ---------------------------------------------------------
def firestore_transcript_ref(transcript_id):
    return firestore_collection('transcripts').document(str(transcript_id))


def normalize_firestore_transcript(doc):
    if not doc or not doc.exists:
        return None
    data = doc.to_dict() or {}
    raw_text = data.get("raw_text") or data.get("transcript_text") or data.get("transcript") or ""
    return {
        "id": doc.id,
        "transcript_id": doc.id,
        "event_id": str(data.get("event_id") or ""),
        "user_id": data.get("user_id") or data.get("user_firebase_uid") or "",
        "user_firebase_uid": data.get("user_firebase_uid") or data.get("user_id") or "",
        "title": data.get("title") or "Event Transcript",
        # 'transcript' is the canonical key the template uses; 'raw_text' is the storage key
        "transcript": raw_text,
        "raw_text": raw_text,
        "summary": data.get("summary") or data.get("summary_text") or "",
        "summary_text": data.get("summary_text") or data.get("summary") or "",
        "audio_url": data.get("audio_url") or "",
        "key_points": data.get("key_points") or [],
        "action_items": data.get("action_items") or [],
        "topics": data.get("topics") or [],
        "duration_seconds": data.get("duration_seconds") or 0,
        "status": data.get("status") or "completed",
        "created_at": data.get("created_at") or "",
        "updated_at": data.get("updated_at") or "",
        # 'creator' is used by manage_event.html; populated by route if needed, default ""
        "creator": data.get("creator") or "",
    }


def read_firestore_transcript(transcript_id):
    ref = firestore_transcript_ref(transcript_id)
    doc = ref.get()
    return normalize_firestore_transcript(doc)


def read_firestore_transcripts_for_event(event_id):
    query = firestore_collection('transcripts').where('event_id', '==', str(event_id))
    docs = query.stream()
    items = [normalize_firestore_transcript(d) for d in docs]
    items = [t for t in items if t]
    items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return items


def create_firestore_authoritative_transcript(
    event_id,
    user_firebase_uid,
    raw_text,
    summary_text=None,
    audio_url=None,
    key_points=None,
    action_items=None,
    topics=None,
    duration_seconds=None,
    title=None,
):
    transcript_id = str(uuid.uuid4())
    ref = firestore_collection('transcripts').document(transcript_id)
    now_iso = datetime.now(timezone.utc).isoformat()
    data = {
        "transcript_id": transcript_id,
        "event_id": str(event_id),
        "user_id": user_firebase_uid,
        "user_firebase_uid": user_firebase_uid,
        "title": title or "Event Transcript",
        "raw_text": raw_text or "",
        "summary": summary_text or "",
        "summary_text": summary_text or "",
        "audio_url": audio_url or "",
        "key_points": key_points or [],
        "action_items": action_items or [],
        "topics": topics or [],
        "duration_seconds": int(duration_seconds or 0),
        "status": "completed",
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    ref.set(data)
    doc = ref.get()
    if not doc.exists:
        raise RuntimeError("Firestore Transcript creation could not be verified.")
    return normalize_firestore_transcript(doc)


def update_firestore_authoritative_transcript(transcript_id, updates):
    ref = firestore_transcript_ref(transcript_id)
    before = ref.get()
    if not before.exists:
        raise RuntimeError("Firestore Transcript is unavailable for update.")
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    profile = {**before.to_dict(), **updates}
    ref.set(profile)
    return normalize_firestore_transcript(ref.get())


def delete_firestore_authoritative_transcript(transcript_id):
    ref = firestore_transcript_ref(transcript_id)
    ref.delete()
    if ref.get().exists:
        raise RuntimeError("Firestore Transcript delete could not be verified.")


# ---------------------------------------------------------
# FIRESTORE CERTIFICATE HELPERS
# ---------------------------------------------------------
def normalize_firestore_certificate(doc):
    if not doc or not doc.exists:
        return None
    data = doc.to_dict() or {}
    return {
        "id": doc.id,
        "certificate_id": doc.id,
        "student_uid": str(data.get("student_uid") or data.get("user_id") or data.get("user_firebase_uid") or ""),
        "user_id": str(data.get("user_id") or data.get("student_uid") or data.get("user_firebase_uid") or ""),
        "user_firebase_uid": str(data.get("user_firebase_uid") or data.get("student_uid") or data.get("user_id") or ""),
        "registration_id": str(data.get("registration_id") or ""),
        "event_id": str(data.get("event_id") or ""),
        "event_title": data.get("event_title") or "",
        "event_date": str(data.get("event_date") or ""),
        "student_name": data.get("student_name") or "Student",
        "reg_no": data.get("reg_no") or "",
        "department": data.get("department") or "",
        "certificate_type": data.get("certificate_type") or "Participation",
        "certificate_file": data.get("certificate_file") or data.get("filename") or "",
        "filename": data.get("certificate_file") or data.get("filename") or "",
        "created_at": data.get("created_at") or data.get("generated_at") or "",
        "generated_at": data.get("generated_at") or data.get("created_at") or "",
    }


def delete_firestore_certificate_record(event_id, student_uid):
    """Delete certificate record from Firestore certificates collection."""
    try:
        client = get_firestore_client()
        if client is None:
            return
        # Query and delete any matching certificates
        docs = list(client.collection("certificates")
                    .where("event_id", "==", str(event_id))
                    .where("student_uid", "==", str(student_uid))
                    .stream())
        for d in docs:
            d.reference.delete()

        # Also check user_firebase_uid
        docs2 = list(client.collection("certificates")
                     .where("event_id", "==", str(event_id))
                     .where("user_firebase_uid", "==", str(student_uid))
                     .stream())
        for d in docs2:
            d.reference.delete()

        cert_doc_id = f"cert_{event_id}_{student_uid}"
        client.collection("certificates").document(cert_doc_id).delete()
    except Exception as e:
        logger.warning(f"Error deleting certificate record: {e}")


def read_firestore_certificates_for_user(user_id):
    """Read all certificates for a student Firebase UID from Firestore authority.
    Only returns certificates for events where the student is currently marked PRESENT."""
    if not user_id:
        return []
    try:
        user_regs = read_firestore_registrations_for_user(user_id)
        # Determine events where student is currently marked PRESENT
        present_event_ids = {
            str(r.get("event_id"))
            for r in user_regs
            if (r.get("attendance") == 1 or r.get("attendance_status") in ("present", "attended"))
            and r.get("attendance_status") != "absent"
        }

        client = get_firestore_client()
        certs = []
        if client:
            try:
                for snap in client.collection("certificates").where("student_uid", "==", str(user_id)).stream():
                    normalized = normalize_firestore_certificate(snap)
                    if normalized and str(normalized.get("event_id")) in present_event_ids:
                        certs.append(normalized)
            except Exception as e:
                logger.warning(f"Querying certificates by student_uid: {e}")

            if not certs:
                try:
                    for snap in client.collection("certificates").where("user_firebase_uid", "==", str(user_id)).stream():
                        normalized = normalize_firestore_certificate(snap)
                        if normalized and str(normalized.get("event_id")) in present_event_ids:
                            if normalized["id"] not in {c["id"] for c in certs}:
                                certs.append(normalized)
                except Exception as e:
                    logger.warning(f"Querying certificates by user_firebase_uid: {e}")

        # Fallback / merge with registrations that are present and have certificate_file
        existing_filenames = {c.get("certificate_file") for c in certs if c.get("certificate_file")}
        for r in user_regs:
            ev_id = str(r.get("event_id"))
            if ev_id in present_event_ids:
                cfile = r.get("certificate_file")
                if cfile and cfile not in existing_filenames:
                    certs.append({
                        "id": f"cert_{r.get('event_id')}_{user_id}",
                        "certificate_id": f"cert_{r.get('event_id')}_{user_id}",
                        "student_uid": str(user_id),
                        "user_id": str(user_id),
                        "user_firebase_uid": str(user_id),
                        "registration_id": str(r.get("id") or ""),
                        "event_id": ev_id,
                        "event_title": r.get("title") or "Event",
                        "event_date": str(r.get("event_date") or ""),
                        "student_name": r.get("student_name") or r.get("name") or "Student",
                        "reg_no": r.get("reg_no") or "",
                        "department": r.get("department") or "",
                        "certificate_type": r.get("certificate_type") or "Participation",
                        "certificate_file": cfile,
                        "filename": cfile,
                        "created_at": r.get("registered_at") or "",
                        "generated_at": r.get("registered_at") or "",
                    })
                    existing_filenames.add(cfile)

        certs.sort(key=lambda x: str(x.get("created_at") or x.get("generated_at") or ""), reverse=True)
        return certs
    except Exception as e:
        logger.error(f"Error reading certificates for user {user_id}: {e}")
        return []


def create_firestore_certificate_record(cert_data):
    """Store an authoritative certificate record in Firestore."""
    client = get_firestore_client()
    if client is None:
        raise RuntimeError("Firestore is unavailable.")
    
    cert_id = cert_data.get("certificate_id") or str(uuid.uuid4())
    ref = client.collection("certificates").document(str(cert_id))
    ref.set(cert_data)
    doc = ref.get()
    return normalize_firestore_certificate(doc)


# ---------------------------------------------------------
# CERTIFICATE TEMPLATE HELPERS
# ---------------------------------------------------------
MAX_CERT_ASSET_BYTES = 1 * 1024 * 1024   # 1 MB limit for logo/sig images
ALLOWED_CERT_ASSET_EXTENSIONS = {".jpg", ".jpeg", ".png"}
ALLOWED_CERT_ASSET_MIME_TYPES = {"image/jpeg", "image/png"}

# Logo slot definitions (key -> display label)
CERT_LOGO_SLOTS = {
    "hive_logo":    "HIVE Logo",
    "college_logo": "College / SRIT Logo",
    "snr_logo":     "SNR Logo",
    "org_logo":     "Organization / Custom Logo",
    "custom_logo_1": "Custom Logo 1",
    "custom_logo_2": "Custom Logo 2",
}

# Signature slot definitions (key -> display label)
CERT_SIG_SLOTS = {
    "principal_sig":          "Principal Signature",
    "faculty_coordinator_sig": "Faculty Coordinator Signature",
    "speaker_sig":            "Speaker Signature",
    "org_member_sig":         "Organization Member Signature",
    "custom_sig":             "Custom Signatory",
}


def read_firestore_cert_template(event_id):
    """Read the event-level certificate template from Firestore.
    Returns a dict (may be empty) or {} on any error.
    """
    try:
        client = get_firestore_client()
        if client is None:
            return {}
        doc = client.collection("certificate_templates").document(str(event_id)).get()
        if doc.exists:
            return doc.to_dict() or {}
        return {}
    except Exception as e:
        logger.warning(f"Could not read cert template for event {event_id}: {e}")
        return {}


def save_firestore_cert_template(event_id, data):
    """Write/overwrite the event-level certificate template in Firestore."""
    client = get_firestore_client()
    if client is None:
        raise RuntimeError("Firestore is unavailable.")
    data["event_id"] = str(event_id)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    client.collection("certificate_templates").document(str(event_id)).set(data)


def delete_firestore_cert_template(event_id):
    """Delete the event-level certificate template from Firestore."""
    try:
        client = get_firestore_client()
        if client is None:
            return
        client.collection("certificate_templates").document(str(event_id)).delete()
    except Exception as e:
        logger.warning(f"Could not delete cert template for event {event_id}: {e}")

def _save_cert_asset(event_id, slot_key, file_storage):
    """
    Validate and save a logo/signature image for a certificate template.
    Returns (relative_path_from_BASE_DIR, error_message).
    relative_path is suitable for storage in Firestore and for os.path.join(BASE_DIR, rel_path).
    """
    if not file_storage or not file_storage.filename:
        return None, "No file provided."
    filename = secure_filename(file_storage.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_CERT_ASSET_EXTENSIONS:
        return None, f"Invalid file type '{ext}'. Only JPG/PNG allowed."
    # Read to check size
    file_bytes = file_storage.read()
    if len(file_bytes) > MAX_CERT_ASSET_BYTES:
        size_mb = len(file_bytes) / (1024 * 1024)
        return None, f"File too large ({size_mb:.1f} MB). Maximum 1 MB."
    # Build unique filename and save
    import uuid as _uuid
    unique_name = f"{slot_key}_{_uuid.uuid4().hex[:8]}{ext}"
    event_asset_dir = os.path.join(CERT_ASSETS_DIR, f"event_{event_id}")
    os.makedirs(event_asset_dir, exist_ok=True)
    save_path = os.path.join(event_asset_dir, unique_name)
    with open(save_path, "wb") as f:
        f.write(file_bytes)
    # Relative path from BASE_DIR for storage
    rel_path = os.path.relpath(save_path, BASE_DIR).replace("\\", "/")
    return rel_path, None


# ---------------------------------------------------------
# EVENT PHOTOGRAPHS & MEDIA HELPERS
# ---------------------------------------------------------
MAX_MEDIA_FILE_BYTES = 2 * 1024 * 1024  # 2MB
ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png"}
ALLOWED_PHOTO_MIME_TYPES = {"image/jpeg", "image/png"}
ALLOWED_MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
ALLOWED_MEDIA_MIME_TYPES = {"image/jpeg", "image/png", "application/pdf"}

MEDIA_SLOT_DEFINITIONS = {
    "geotagged_photo_1": {
        "key": "geotagged_photo_1",
        "label": "Geotagged Photo 1",
        "description": "Geotagged image with embedded GPS location data",
        "category": "geotagged",
        "required": True,
        "allowed_types": "JPG, PNG (Max 2MB)",
        "accept": "image/jpeg,image/png",
    },
    "geotagged_photo_2": {
        "key": "geotagged_photo_2",
        "label": "Geotagged Photo 2",
        "description": "Additional geotagged angle or moment",
        "category": "geotagged",
        "required": False,
        "allowed_types": "JPG, PNG (Max 2MB)",
        "accept": "image/jpeg,image/png",
    },
    "geotagged_photo_3": {
        "key": "geotagged_photo_3",
        "label": "Geotagged Photo 3",
        "description": "Additional geotagged angle or moment",
        "category": "geotagged",
        "required": False,
        "allowed_types": "JPG, PNG (Max 2MB)",
        "accept": "image/jpeg,image/png",
    },
    "activity_photo_1": {
        "key": "activity_photo_1",
        "label": "Photo 1 — Banner / Print Material",
        "description": "Print/soft copy of the Banner used for the activity",
        "category": "activity",
        "required": True,
        "allowed_types": "JPG, PNG (Max 2MB)",
        "accept": "image/jpeg,image/png",
    },
    "activity_photo_2": {
        "key": "activity_photo_2",
        "label": "Photo 2 — Speaker / Dais with Banner",
        "description": "Speaker / Dais Photo with banner visible in the background",
        "category": "activity",
        "required": True,
        "allowed_types": "JPG, PNG (Max 2MB)",
        "accept": "image/jpeg,image/png",
    },
    "activity_photo_3": {
        "key": "activity_photo_3",
        "label": "Photo 3 — Student & Staff Participation",
        "description": "Participation of Students and Staff — photo covering guest and participants from front and back",
        "category": "activity",
        "required": True,
        "allowed_types": "JPG, PNG (Max 2MB)",
        "accept": "image/jpeg,image/png",
    },
    "activity_photo_4": {
        "key": "activity_photo_4",
        "label": "Photo 4 — Unique / Activity Moment",
        "description": "Candid moments showing broader student engagement level",
        "category": "activity",
        "required": True,
        "allowed_types": "JPG, PNG (Max 2MB)",
        "accept": "image/jpeg,image/png",
    },
    "activity_photo_5": {
        "key": "activity_photo_5",
        "label": "Photo 5 — Additional Moment",
        "description": "Any other moment that highlights the activity (optional 5th photo)",
        "category": "activity",
        "required": False,
        "allowed_types": "JPG, PNG (Max 2MB)",
        "accept": "image/jpeg,image/png",
    },
    "media_pre_activity": {
        "key": "media_pre_activity",
        "label": "Pre-Activity Social Media Post Screenshot",
        "description": "Screenshot of announcement/invitation posted before the activity",
        "category": "media",
        "required": False,
        "allowed_types": "JPG, PNG, PDF (Max 2MB)",
        "accept": "image/jpeg,image/png,application/pdf",
    },
    "media_post_activity": {
        "key": "media_post_activity",
        "label": "Post-Activity Social Media Post Screenshot",
        "description": "Screenshot of update/highlights posted after the activity",
        "category": "media",
        "required": False,
        "allowed_types": "JPG, PNG, PDF (Max 2MB)",
        "accept": "image/jpeg,image/png,application/pdf",
    },
    "media_coverage": {
        "key": "media_coverage",
        "label": "Media Coverage Screenshot",
        "description": "Screenshot of newspaper articles, online news portals, or newsletters",
        "category": "media",
        "required": False,
        "allowed_types": "JPG, PNG, PDF (Max 2MB)",
        "accept": "image/jpeg,image/png,application/pdf",
    },
}


def format_file_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


def get_event_upload_dir(event_id):
    upload_dir = os.path.join(BASE_DIR, "static", "uploads", f"event_{event_id}")
    os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


def read_firestore_event_media(event_id):
    """Read event media document from Firestore authority."""
    try:
        client = get_firestore_client()
        if client is None:
            return {"event_id": str(event_id), "slots": {}}
        doc = client.collection("event_media").document(str(event_id)).get()
        if doc.exists:
            data = doc.to_dict() or {}
            slots = data.get("slots") or {}
            return {
                "event_id": str(event_id),
                "updated_at": data.get("updated_at") or "",
                "updated_by": data.get("updated_by") or "",
                "slots": slots,
            }
        return {"event_id": str(event_id), "slots": {}}
    except Exception as e:
        logger.error(f"Error reading event media for event {event_id}: {e}")
        return {"event_id": str(event_id), "slots": {}}


def save_firestore_event_media_slot(event_id, slot_key, slot_data):
    """Save or update a specific media slot in Firestore event_media collection."""
    try:
        client = get_firestore_client()
        if client is None:
            raise RuntimeError("Firestore is unavailable.")
        doc_ref = client.collection("event_media").document(str(event_id))
        now_iso = datetime.now(timezone.utc).isoformat()
        
        doc = doc_ref.get()
        if doc.exists:
            doc_ref.update({
                f"slots.{slot_key}": slot_data,
                "updated_at": now_iso,
            })
        else:
            doc_ref.set({
                "event_id": str(event_id),
                "updated_at": now_iso,
                "slots": {slot_key: slot_data},
            })
        return True
    except Exception as e:
        logger.error(f"Error saving media slot {slot_key} for event {event_id}: {e}")
        raise


def delete_firestore_event_media_slot(event_id, slot_key):
    """Delete a specific media slot from Firestore event_media document and remove file from Google Drive / disk."""
    try:
        client = get_firestore_client()
        if client is None:
            return False
        doc_ref = client.collection("event_media").document(str(event_id))
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict() or {}
            slots = data.get("slots") or {}
            slot_info = slots.get(slot_key)
            if slot_info:
                # Delete from Google Drive if drive_file_id is present
                drive_file_id = slot_info.get("drive_file_id")
                if drive_file_id:
                    try:
                        delete_drive_file(drive_file_id)
                    except Exception as de:
                        logger.warning(f"Could not delete Google Drive file {drive_file_id}: {de}")
                # Clean up legacy local file if present
                if slot_info.get("filename"):
                    file_path = os.path.join(get_event_upload_dir(event_id), slot_info["filename"])
                    if os.path.isfile(file_path):
                        try:
                            os.remove(file_path)
                        except Exception as fe:
                            logger.warning(f"Could not remove file {file_path}: {fe}")
            doc_ref.update({
                f"slots.{slot_key}": firestore.DELETE_FIELD,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        return True
    except Exception as e:
        logger.error(f"Error deleting media slot {slot_key} for event {event_id}: {e}")
        return False


def process_uploaded_media_file(event_id, slot_key, file_storage, user_id):
    """Validate, store to Google Drive API (or local fallback), and record metadata in Firestore for a media slot."""
    if not file_storage or not file_storage.filename:
        return False, "No file provided", None

    slot_def = MEDIA_SLOT_DEFINITIONS.get(slot_key)
    if not slot_def:
        return False, f"Invalid slot '{slot_key}'", None

    ext = os.path.splitext(file_storage.filename)[1].lower()
    is_media = (slot_def["category"] == "media")
    allowed_exts = ALLOWED_MEDIA_EXTENSIONS if is_media else ALLOWED_PHOTO_EXTENSIONS

    if ext not in allowed_exts:
        return False, f"Invalid file format for {slot_def['label']}. Allowed: {slot_def['allowed_types']}", None

    # Check file size
    file_storage.seek(0, os.SEEK_END)
    file_size = file_storage.tell()
    file_storage.seek(0)

    if file_size > MAX_MEDIA_FILE_BYTES:
        size_str = format_file_size(file_size)
        return False, f"File for '{slot_def['label']}' exceeds 2MB limit (size: {size_str}). Upload was rejected.", None

    if file_size == 0:
        return False, f"File for '{slot_def['label']}' is empty.", None

    clean_orig_name = secure_filename(file_storage.filename)
    now_iso = datetime.now(timezone.utc).isoformat()
    content_type = file_storage.content_type or ("application/pdf" if ext == ".pdf" else "image/jpeg")

    # Read existing media to get old file information for safe replacement
    existing_media = read_firestore_event_media(event_id)
    old_slot = existing_media.get("slots", {}).get(slot_key) if existing_media else None
    old_drive_file_id = old_slot.get("drive_file_id") if old_slot else None
    old_local_filename = old_slot.get("filename") if old_slot else None

    # Try Google Drive storage first if configured
    if is_drive_configured():
        try:
            drive_result = upload_file_to_drive(
                file_data=file_storage,
                filename=f"{slot_key}_{uuid.uuid4().hex[:8]}_{clean_orig_name}",
                content_type=content_type,
                event_id=event_id,
            )
            slot_data = {
                "slot_key": slot_key,
                "slot_label": slot_def["label"],
                "category": slot_def["category"],
                "required": slot_def["required"],
                "storage_type": "google_drive",
                "drive_file_id": drive_result["drive_file_id"],
                "drive_folder_id": drive_result["drive_folder_id"],
                "drive_file_name": drive_result["drive_file_name"],
                "drive_web_view_url": drive_result.get("drive_web_view_url", ""),
                "filename": drive_result["drive_file_name"],
                "original_filename": clean_orig_name,
                "relative_url": f"/events/{event_id}/media/{slot_key}",
                "file_size": drive_result["file_size"],
                "file_size_formatted": format_file_size(drive_result["file_size"]),
                "uploaded_at": now_iso,
                "uploaded_by": user_id,
                "content_type": drive_result["content_type"],
                "is_pdf": (ext == ".pdf"),
            }
            # Save metadata to Firestore
            save_firestore_event_media_slot(event_id, slot_key, slot_data)

            # After successful upload and Firestore update, clean up old file
            if old_drive_file_id and old_drive_file_id != drive_result["drive_file_id"]:
                try:
                    delete_drive_file(old_drive_file_id)
                except Exception as de:
                    logger.warning(f"Could not delete replaced Drive file {old_drive_file_id}: {de}")
            if old_local_filename:
                old_path = os.path.join(get_event_upload_dir(event_id), old_local_filename)
                if os.path.isfile(old_path):
                    try:
                        os.remove(old_path)
                    except Exception:
                        pass

            return True, f"Uploaded '{slot_def['label']}' to Google Drive successfully.", slot_data
        except Exception as e:
            logger.error(f"Google Drive upload failed for slot {slot_key} in event {event_id}: {e}")
            return False, f"Failed to upload to Google Drive: {str(e)}", None
    else:
        # Fallback to local upload dir if Drive is not configured
        upload_dir = get_event_upload_dir(event_id)
        unique_name = f"{slot_key}_{uuid.uuid4().hex[:8]}_{clean_orig_name}"
        save_path = os.path.join(upload_dir, unique_name)
        file_storage.save(save_path)

        relative_url = f"/events/{event_id}/media/{slot_key}"
        slot_data = {
            "slot_key": slot_key,
            "slot_label": slot_def["label"],
            "category": slot_def["category"],
            "required": slot_def["required"],
            "storage_type": "local",
            "filename": unique_name,
            "original_filename": clean_orig_name,
            "relative_url": relative_url,
            "file_size": file_size,
            "file_size_formatted": format_file_size(file_size),
            "uploaded_at": now_iso,
            "uploaded_by": user_id,
            "content_type": content_type,
            "is_pdf": (ext == ".pdf"),
        }

        # If an old local file was present in this slot, remove it from disk
        if old_local_filename and old_local_filename != unique_name:
            old_path = os.path.join(upload_dir, old_local_filename)
            if os.path.isfile(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass

        save_firestore_event_media_slot(event_id, slot_key, slot_data)
        return True, f"Uploaded '{slot_def['label']}' successfully.", slot_data


# ---------------------------------------------------------
# FIRESTORE USER PROFILE HELPERS
# ---------------------------------------------------------
def firestore_user_ref(firebase_uid):
    uid_str = str(firebase_uid)
    ref = firestore_collection('user_profiles').document(uid_str)
    if ref.get().exists:
        return ref
    docs = list(firestore_collection('user_profiles').where('firebase_uid', '==', uid_str).limit(1).stream())
    if docs:
        return docs[0].reference
    users_ref = firestore_collection('users').document(uid_str)
    if users_ref.get().exists:
        return users_ref
    return ref


def normalize_firestore_user_profile(doc):
    if not doc or not doc.exists:
        return None
    data = doc.to_dict() or {}
    return {
        "id": doc.id,
        "firebase_uid": data.get("firebase_uid") or doc.id,
        "email": data.get("email") or "",
        "name": data.get("name") or "",
        "role": data.get("role") or "student",
        "college_id": data.get("college_id") or data.get("reg_no") or "",
        "reg_no": data.get("reg_no") or data.get("college_id") or "",
        "department": data.get("department") or "",
        "year": data.get("year") or "",
        "phone": data.get("phone") or "",
        "created_at": data.get("created_at") or "",
        "updated_at": data.get("updated_at") or "",
        "last_login_at": data.get("last_login_at") or "",
    }


def read_firestore_user_profile_by_firebase_uid(firebase_uid):
    if not firebase_uid:
        return None
    ref = firestore_user_ref(firebase_uid)
    return normalize_firestore_user_profile(ref.get())


def find_firestore_user_by_email(email):
    if not email:
        return None
    e = email.strip().lower()
    for col in ('user_profiles', 'users'):
        docs = list(firestore_collection(col).where('email', '==', e).limit(1).stream())
        if docs:
            return normalize_firestore_user_profile(docs[0])
    return None


def read_all_firestore_users(limit=100):
    users = []
    seen = set()
    for col in ('user_profiles', 'users'):
        try:
            for doc in firestore_collection(col).limit(limit).stream():
                u = normalize_firestore_user_profile(doc)
                if u and u.get("firebase_uid") and u["firebase_uid"] not in seen:
                    seen.add(u["firebase_uid"])
                    users.append(u)
        except Exception as e:
            logger.warning(f"Error streaming {col}: {e}")
    return users


def create_firestore_user_profile(
    firebase_uid,
    email,
    name,
    role="student",
    college_id=None,
    department=None,
    year=None,
    phone=None,
):
    ref = firestore_user_ref(firebase_uid)
    now_iso = datetime.now(timezone.utc).isoformat()
    data = {
        "firebase_uid": firebase_uid,
        "email": (email or "").strip().lower(),
        "name": name or "",
        "role": role or "student",
        "college_id": college_id or "",
        "department": department or "",
        "year": year or "",
        "phone": phone or "",
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    ref.set(data)
    doc = ref.get()
    if not doc.exists:
        raise RuntimeError("Firestore User profile creation could not be verified.")
    return normalize_firestore_user_profile(doc)


def update_firestore_user_profile(firebase_uid, updates):
    ref = firestore_user_ref(firebase_uid)
    before = ref.get()
    if not before.exists:
        raise RuntimeError("Firestore User profile is unavailable for update.")
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    profile = {**before.to_dict(), **updates}
    ref.set(profile)
    return normalize_firestore_user_profile(ref.get())


def get_user_by_id(user_id):
    # Retrieve user profile by Firebase UID
    return read_firestore_user_profile_by_firebase_uid(user_id)


def get_user_by_email(email):
    # Retrieve user profile by Email
    return find_firestore_user_by_email(email)


# ---------------------------------------------------------
# FIREBASE AUTH EXCEPTION CLASSES
# ---------------------------------------------------------
class FirebaseAuthError(Exception):
    """Base exception for Firebase Authentication errors."""
    pass


class FirebaseInvalidCredentialsError(FirebaseAuthError):
    """Raised when authentication credentials are invalid."""
    pass


class FirebaseUserDisabledError(FirebaseAuthError):
    """Raised when the user account is disabled."""
    pass


class FirebaseTooManyRequestsError(FirebaseAuthError):
    """Raised when too many requests are made to Firebase Auth."""
    pass


class FirebaseEmailAlreadyExistsError(FirebaseAuthError):
    """Raised when an account with the specified email already exists."""
    pass


class FirebaseWeakPasswordError(FirebaseAuthError):
    """Raised when the provided password is too weak."""
    pass


class FirebaseAuthServiceUnavailableError(FirebaseAuthError):
    """Raised when Firebase Auth service is unavailable or unconfigured."""
    pass


# ---------------------------------------------------------
# FIREBASE AUTH HELPERS
# ---------------------------------------------------------
def firebase_auth_configured():
    return bool(FIREBASE_WEB_API_KEY)


def sign_in_with_email_password(email, password):
    if not firebase_auth_configured():
        raise FirebaseAuthServiceUnavailableError("Firebase Web API key is not configured.")
    
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}"
    payload = {
        "email": email,
        "password": password,
        "returnSecureToken": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
    except Exception as e:
        logger.error(f"Firebase Auth network error: {e}")
        raise FirebaseAuthServiceUnavailableError("Network error contacting Firebase Authentication.")
    
    if resp.status_code != 200:
        error_msg = data.get("error", {}).get("message", "INVALID_LOGIN_CREDENTIALS")
        if error_msg in ["EMAIL_NOT_FOUND", "INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS"]:
            raise FirebaseInvalidCredentialsError("Invalid email or password.")
        elif error_msg == "USER_DISABLED":
            raise FirebaseUserDisabledError("This user account has been disabled.")
        elif "TOO_MANY_ATTEMPTS_TRY_LATER" in error_msg:
            raise FirebaseTooManyRequestsError("Too many attempts. Please try again later.")
        else:
            raise FirebaseAuthError(f"Authentication failed: {error_msg}")
    
    return data


def sign_up_with_email_password(email, password):
    if not firebase_auth_configured():
        raise FirebaseAuthServiceUnavailableError("Firebase Web API key is not configured.")
    
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_WEB_API_KEY}"
    payload = {
        "email": email,
        "password": password,
        "returnSecureToken": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
    except Exception as e:
        logger.error(f"Firebase Auth signup network error: {e}")
        raise FirebaseAuthServiceUnavailableError("Network error contacting Firebase Authentication.")
    
    if resp.status_code != 200:
        error_msg = data.get("error", {}).get("message", "SIGNUP_FAILED")
        if error_msg == "EMAIL_EXISTS":
            raise FirebaseEmailAlreadyExistsError("An account with this email address already exists. Please log in.")
        elif error_msg == "OPERATION_NOT_ALLOWED":
            raise FirebaseAuthError("Email/password sign-up is disabled for this Firebase project.")
        elif "WEAK_PASSWORD" in error_msg:
            raise FirebaseWeakPasswordError("The password must be at least 6 characters long.")
        elif "INVALID_EMAIL" in error_msg:
            raise FirebaseAuthError("Please enter a valid email address.")
        elif "TOO_MANY_ATTEMPTS_TRY_LATER" in error_msg:
            raise FirebaseTooManyRequestsError("Too many attempts. Please try again later.")
        else:
            raise FirebaseAuthError(f"Registration failed: {error_msg}")
    
    return data


def send_password_reset_email(email):
    if not firebase_auth_configured():
        raise FirebaseAuthServiceUnavailableError("Firebase Web API key is not configured.")
    
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={FIREBASE_WEB_API_KEY}"
    payload = {
        "requestType": "PASSWORD_RESET",
        "email": email,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
    except Exception as e:
        logger.error(f"Firebase password reset network error: {e}")
        raise FirebaseAuthServiceUnavailableError("Network error contacting Firebase Authentication.")
    
    if resp.status_code != 200:
        error_msg = data.get("error", {}).get("message", "RESET_FAILED")
        if error_msg == "EMAIL_NOT_FOUND":
            raise FirebaseInvalidCredentialsError("No account found with this email address.")
        else:
            raise FirebaseAuthError(f"Password reset failed: {error_msg}")
    
    return data


def rollback_user_registration(firebase_uid=None):
    # Delete a user from Firebase Auth and Firestore if registration fails mid-process
    if not firebase_uid:
        return
    try:
        firestore_user_ref(firebase_uid).delete()
    except Exception as e:
        logger.warning(f"Failed to delete Firestore profile during rollback for {firebase_uid}: {e}")
    
    if FIREBASE_ADMIN_INITIALIZED:
        try:
            admin_auth.delete_user(firebase_uid)
            logger.info(f"Successfully rolled back Firebase Auth user {firebase_uid}")
        except Exception as e:
            logger.warning(f"Failed to delete Firebase Auth user during rollback for {firebase_uid}: {e}")


# ---------------------------------------------------------
# CONTEXT PROCESSORS & DECORATORS
# ---------------------------------------------------------
@app.context_processor
def inject_user():
    user = None
    if "user_id" in session:
        user = read_firestore_user_profile_by_firebase_uid(session["user_id"])
    return dict(current_user=user)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in to access this page.", "warning")
                return redirect(url_for("login", next=request.url))
            user_role = session.get("role", "student")
            if user_role not in allowed_roles:
                flash("You do not have permission to access this page.", "danger")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ---------------------------------------------------------
# AUTH ROUTES
# ---------------------------------------------------------
@app.route("/")
def index():
    upcoming_events = read_firestore_events(filter_type="upcoming", limit=6)
    return render_template("index.html", upcoming_events=upcoming_events, events=upcoming_events)


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        role = request.form.get("role", "student").strip().lower()
        college_id = (request.form.get("college_id") or request.form.get("reg_no") or "").strip()
        department = request.form.get("department", "").strip()
        year = request.form.get("year", "").strip()
        phone = request.form.get("phone", "").strip()

        if not name or not email or not password:
            flash("Name, email, and password are required.", "danger")
            return render_template("register.html")

        if confirm_password and password != confirm_password:
            flash("Passwords do not match. Please verify and try again.", "danger")
            return render_template("register.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "danger")
            return render_template("register.html")

        if role not in ["student", "faculty", "admin"]:
            role = "student"

        # Check existing Firestore user
        existing_user = find_firestore_user_by_email(email)
        if existing_user:
            flash("An account with this email already exists. Please sign in.", "danger")
            return render_template("register.html")

        firebase_uid = None
        try:
            auth_res = sign_up_with_email_password(email, password)
            firebase_uid = auth_res.get("localId")
            if not firebase_uid:
                raise RuntimeError("Firebase did not return a user ID.")

            # Create Firestore user profile
            create_firestore_user_profile(
                firebase_uid=firebase_uid,
                email=email,
                name=name,
                role=role,
                college_id=college_id,
                department=department,
                year=year,
                phone=phone,
            )

            flash("Registration successful! Please log in with your new account.", "success")
            return redirect(url_for("login"))

        except FirebaseEmailAlreadyExistsError:
            flash("An account with this email already exists. Please sign in.", "danger")
            return render_template("register.html")
        except FirebaseWeakPasswordError:
            flash("Password must be at least 6 characters long.", "danger")
            return render_template("register.html")
        except FirebaseTooManyRequestsError:
            flash("Too many attempts. Please try again later.", "danger")
            return render_template("register.html")
        except FirebaseAuthServiceUnavailableError as se:
            flash(str(se), "danger")
            return render_template("register.html")
        except Exception as e:
            logger.error(f"Registration error for {email}: {e}")
            if firebase_uid:
                rollback_user_registration(firebase_uid)
            flash(f"Registration failed: {str(e)}", "danger")
            return render_template("register.html")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email and password are required.", "danger")
            return render_template("login.html")

        try:
            auth_res = sign_in_with_email_password(email, password)
            firebase_uid = auth_res.get("localId")
            id_token = auth_res.get("idToken")

            user_profile = read_firestore_user_profile_by_firebase_uid(firebase_uid)
            if not user_profile:
                # If profile doesn't exist yet, create default
                user_profile = create_firestore_user_profile(
                    firebase_uid=firebase_uid,
                    email=email,
                    name=email.split("@")[0],
                    role="student",
                )

            # Update last login timestamp
            try:
                update_firestore_user_profile(firebase_uid, {"last_login_at": datetime.now(timezone.utc).isoformat()})
            except Exception as e:
                logger.warning(f"Could not update last login for {firebase_uid}: {e}")

            session["user_id"] = firebase_uid
            session["role"] = user_profile.get("role", "student")
            session["name"] = user_profile.get("name", "")
            session["email"] = user_profile.get("email", email)
            session["firebase_id_token"] = id_token

            flash(f"Welcome back, {session['name']}!", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard"))

        except FirebaseInvalidCredentialsError:
            flash("Invalid email or password.", "danger")
        except FirebaseUserDisabledError:
            flash("Your account has been disabled.", "danger")
        except FirebaseTooManyRequestsError:
            flash("Too many failed attempts. Please try again later.", "danger")
        except Exception as e:
            logger.error(f"Login error for {email}: {e}")
            flash(f"Login failed: {str(e)}", "danger")

    return render_template("login.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            flash("Please enter your email address.", "danger")
            return render_template("forgot_password.html")
        try:
            send_password_reset_email(email)
            flash("Password reset email sent! Check your inbox.", "info")
            return redirect(url_for("login"))
        except Exception as e:
            flash(f"Error: {str(e)}", "danger")
    return render_template("forgot_password.html")


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        email = session.get("email")
        if email:
            try:
                send_password_reset_email(email)
                flash("A password reset link has been sent to your email address.", "info")
                return redirect(url_for("profile"))
            except Exception as e:
                flash(f"Could not send reset email: {str(e)}", "danger")
        else:
            flash("Email not found in session.", "danger")
    return render_template("change_password.html")


@app.route("/logout", methods=["GET", "POST"])
@csrf.exempt
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session.get("user_id")
    role = session.get("role", "student")
    user = read_firestore_user_profile_by_firebase_uid(user_id)

    if role == "admin":
        users_list = read_all_firestore_users()
        events_list = read_firestore_events()
        total_users = len(users_list)
        total_events = len(events_list)
        total_registrations = count_firestore_registrations()
        total_feedback = count_firestore_feedback()
        total_certificates = count_firestore_registrations(certificate=True)
        return render_template(
            "admin_dashboard.html",
            user=user,
            users=users_list[:20],
            events=events_list[:10],
            stats={
                "users": total_users,
                "events": total_events,
                "registrations": total_registrations,
                "feedback": total_feedback,
                "certificates": total_certificates,
            },
        )
    elif role == "faculty":
        events = read_firestore_events()
        my_events = [e for e in events if user_reference_matches(e.get("created_by_firebase_uid") or e.get("created_by"), user_id)]
        
        all_regs = list_firestore_registrations()
        reg_counts = registration_counts_by_event(all_regs)
        
        my_event_ids = {str(e.get("id")) for e in my_events}
        my_regs = [r for r in all_regs if str(r.get("event_id")) in my_event_ids]
        
        for e in my_events:
            e["registrations_count"] = reg_counts.get(str(e.get("id")), e.get("registered_count") or 0)
        
        total_events = len(my_events)
        total_registrations = len(my_regs)
        total_attendance = len([r for r in my_regs if r.get("attendance") or r.get("attendance_status") == "attended"])
        total_certificates = len([r for r in my_regs if r.get("certificate_file")])

        return render_template(
            "faculty_dashboard.html",
            user=user,
            events=my_events,
            stats={
                "events": total_events,
                "registrations": total_registrations,
                "attendance": total_attendance,
                "certificates": total_certificates,
            },
        )
    else:
        # Student dashboard
        my_registrations = read_firestore_registrations_for_user(user_id)
        updates_list = read_all_firestore_updates(limit=5)
        certificates_list = read_firestore_certificates_for_user(user_id)

        total_registered = len(my_registrations)
        attended_count = len([r for r in my_registrations if r.get("attendance") or r.get("attendance_status") in ("attended", "present")])
        certs_count = len(certificates_list) if certificates_list else len([r for r in my_registrations if r.get("certificate_file")])
        feedback_count = count_firestore_feedback(user_id=user_id)

        return render_template(
            "student_dashboard.html",
            user=user,
            registrations=my_registrations,
            certificates=certificates_list,
            updates=updates_list,
            stats={
                "registered": total_registered,
                "attended": attended_count,
                "certificates": certs_count,
                "feedback": feedback_count,
            },
        )


# ---------------------------------------------------------
# EVENT ROUTES
# ---------------------------------------------------------
@app.route("/events")
def events():
    search = (request.args.get("q") or request.args.get("search") or "").strip()
    status = (request.args.get("status") or request.args.get("filter") or "").strip()
    filter_val = status if status and status.lower() != "all" else "all"
    events_list = read_firestore_events(filter_type=filter_val, search=search, status=status if status and status.lower() != "all" else None)

    all_regs = list_firestore_registrations()
    reg_counts = registration_counts_by_event(all_regs)
    for ev in events_list:
        ev["registrations_count"] = reg_counts.get(str(ev.get("id")), 0)

    return render_template(
        "events.html",
        events=events_list,
        q=search,
        search=search,
        status=status,
        filter=filter_val,
    )


@app.route("/events/<event_id>")
def event_detail(event_id):
    event = read_firestore_event(event_id)
    if not event:
        flash("Event not found.", "danger")
        return redirect(url_for("events"))

    updates = read_firestore_updates_for_event(event_id)
    feedback_list = read_firestore_feedback_for_event(event_id)
    transcripts = read_firestore_transcripts_for_event(event_id)

    user_registration = None
    user_feedback = None
    if "user_id" in session:
        user_id = session["user_id"]
        registrations = read_firestore_registrations_for_user(user_id)
        for reg in registrations:
            if str(reg.get("event_id")) == str(event_id):
                user_registration = reg
                break
        for fb in feedback_list:
            if user_reference_matches(fb.get("student_uid") or fb.get("user_firebase_uid") or fb.get("user_id"), user_id):
                user_feedback = fb
                break

    registered = bool(user_registration)
    count = count_firestore_registrations(event_id=event_id)

    return render_template(
        "event_detail.html",
        event=event,
        updates=updates,
        feedback_list=feedback_list,
        transcripts=transcripts,
        user_registration=user_registration,
        user_feedback=user_feedback,
        registered=registered,
        count=count,
    )


@app.route("/events/<event_id>/register", methods=["POST"])
@login_required
def register_event(event_id):
    user_id = session.get("user_id")
    event = read_firestore_event(event_id)
    if not event:
        flash("Event not found.", "danger")
        return redirect(url_for("events"))

    # Check already registered
    existing = read_firestore_registrations_for_user(user_id)
    for reg in existing:
        if str(reg.get("event_id")) == str(event_id):
            flash("You are already registered for this event.", "info")
            return redirect(url_for("event_detail", event_id=event_id))

    # Check capacity
    capacity = int(event.get("capacity") or 0)
    registered_count = int(event.get("registered_count") or 0)
    if capacity > 0 and registered_count >= capacity:
        flash("This event is full.", "warning")
        return redirect(url_for("event_detail", event_id=event_id))

    try:
        user_profile = read_firestore_user_profile_by_firebase_uid(user_id)
        user_name = (user_profile.get("name") if user_profile else None) or ""
        user_email = (user_profile.get("email") if user_profile else None) or session.get("email", "")

        reg, state = create_firestore_authoritative_registration(
            event_id=event_id,
            user_firebase_uid=user_id,
            user_name=user_name,
            user_email=user_email,
        )
        if state == "duplicate":
            flash("You are already registered for this event.", "info")
            return redirect(url_for("event_detail", event_id=event_id))
        elif state == "capacity":
            flash("This event is full.", "warning")
            return redirect(url_for("event_detail", event_id=event_id))

        flash("Successfully registered for the event!", "success")
    except Exception as e:
        logger.error(f"Registration error for event {event_id}, user {user_id}: {e}")
        flash(f"Registration failed: {str(e)}", "danger")

    return redirect(url_for("event_detail", event_id=event_id))


@app.route("/events/<event_id>/cancel", methods=["POST"])
@login_required
def cancel_registration(event_id):
    user_id = session.get("user_id")
    registrations = read_firestore_registrations_for_user(user_id)
    target_reg = None
    for reg in registrations:
        if str(reg.get("event_id")) == str(event_id):
            target_reg = reg
            break

    if not target_reg:
        flash("You are not registered for this event.", "warning")
        return redirect(url_for("event_detail", event_id=event_id))

    try:
        reg_id = target_reg.get("id") or target_reg.get("registration_id")
        delete_firestore_authoritative_registration(reg_id)
        flash("Registration cancelled successfully.", "info")
    except Exception as e:
        logger.error(f"Cancel registration error for event {event_id}, user {user_id}: {e}")
        flash(f"Could not cancel registration: {str(e)}", "danger")

    return redirect(url_for("event_detail", event_id=event_id))


@app.route("/events/create", methods=["GET", "POST"])
@role_required("admin", "faculty")
def create_event():
    if request.method == "POST":
        user_id = session.get("user_id")
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        event_date = request.form.get("event_date", "").strip()
        start_time = (request.form.get("start_time") or request.form.get("event_time") or "").strip()
        end_time = request.form.get("end_time", "").strip()
        venue = (request.form.get("venue") or request.form.get("location") or "").strip()
        capacity = request.form.get("capacity", "0").strip()
        category = request.form.get("category", "").strip()
        status = request.form.get("status", "upcoming").strip()
        academic_year = request.form.get("academic_year", "").strip()
        program_name = request.form.get("program_name", "").strip()
        auto_credit_quarter = request.form.get("auto_credit_quarter", "").strip()
        spc_mapping = request.form.get("spc_mapping", "").strip()
        organizing_details = request.form.get("organizing_details", "").strip()
        objectives = request.form.get("objectives", "").strip()
        benefits = request.form.get("benefits", "").strip()
        reg_fee = request.form.get("registration_fee", "0").strip()
        est_exp = request.form.get("estimated_expenditure", "0").strip()
        speaker_name = request.form.get("speaker_name", "").strip()
        speaker_designation = request.form.get("speaker_designation", "").strip()
        speaker_organization = request.form.get("speaker_organization", "").strip()
        speaker_email = request.form.get("speaker_email", "").strip()
        speaker_contact = request.form.get("speaker_contact", "").strip()
        speaker_expertise = request.form.get("speaker_expertise", "").strip()
        speaker_bio = request.form.get("speaker_bio", "").strip()

        if not title or not event_date:
            flash("Title and event date are required.", "danger")
            return render_template("event_form.html", event=None)

        try:
            from datetime import datetime as _dt, timezone as _tz
            event_data = {
                "title": title,
                "description": description,
                "event_date": event_date,
                "event_time": start_time,
                "start_time": start_time,
                "end_time": end_time,
                "venue": venue,
                "location": venue,
                "capacity": int(capacity) if capacity.isdigit() else 0,
                "category": category,
                "status": status,
                "academic_year": academic_year,
                "program_name": program_name,
                "auto_credit_quarter": auto_credit_quarter,
                "spc_mapping": spc_mapping,
                "organizing_details": organizing_details,
                "objectives": objectives,
                "benefits": benefits,
                "registration_fee": float(reg_fee) if reg_fee else 0.0,
                "estimated_expenditure": float(est_exp) if est_exp else 0.0,
                "speaker_name": speaker_name,
                "speaker_designation": speaker_designation,
                "speaker_organization": speaker_organization,
                "speaker_email": speaker_email,
                "speaker_contact": speaker_contact,
                "speaker_expertise": speaker_expertise,
                "speaker_bio": speaker_bio,
                "created_by": user_id,
                "created_by_firebase_uid": user_id,
                "registered_count": 0,
                "created_at": _dt.now(_tz.utc).isoformat(),
            }
            event = create_firestore_authoritative_event(event_data)
            flash("Event created successfully!", "success")
            return redirect(url_for("event_detail", event_id=event["id"]))
        except Exception as e:
            logger.exception("Create event error: %s", e)
            flash(f"Could not create event: {str(e)}", "danger")

    return render_template("event_form.html", event=None)


@app.route("/events/<event_id>/edit", methods=["GET", "POST"])
@role_required("admin", "faculty")
def edit_event(event_id):
    event = read_firestore_event(event_id)
    if not event:
        flash("Event not found.", "danger")
        return redirect(url_for("events"))

    # Faculty can only edit their own events
    user_id = session.get("user_id")
    role = session.get("role")
    if role == "faculty" and not user_reference_matches(event.get("created_by_firebase_uid") or event.get("created_by"), user_id):
        flash("You can only edit your own events.", "danger")
        return redirect(url_for("event_detail", event_id=event_id))

    if request.method == "POST":
        venue = (request.form.get("venue") or request.form.get("location") or "").strip()
        start_time = (request.form.get("start_time") or request.form.get("event_time") or "").strip()
        end_time = request.form.get("end_time", "").strip()
        academic_year = request.form.get("academic_year", "").strip()
        program_name = request.form.get("program_name", "").strip()
        auto_credit_quarter = request.form.get("auto_credit_quarter", "").strip()
        spc_mapping = request.form.get("spc_mapping", "").strip()
        organizing_details = request.form.get("organizing_details", "").strip()
        objectives = request.form.get("objectives", "").strip()
        benefits = request.form.get("benefits", "").strip()
        reg_fee = request.form.get("registration_fee", "0").strip()
        est_exp = request.form.get("estimated_expenditure", "0").strip()

        updates = {
            "title": request.form.get("title", "").strip(),
            "description": request.form.get("description", "").strip(),
            "event_date": request.form.get("event_date", "").strip(),
            "event_time": start_time,
            "start_time": start_time,
            "end_time": end_time,
            "venue": venue,
            "location": venue,
            "capacity": int(request.form.get("capacity", "0") or "0"),
            "category": request.form.get("category", "").strip(),
            "status": request.form.get("status", "upcoming").strip(),
            "academic_year": academic_year,
            "program_name": program_name,
            "auto_credit_quarter": auto_credit_quarter,
            "spc_mapping": spc_mapping,
            "organizing_details": organizing_details,
            "objectives": objectives,
            "benefits": benefits,
            "registration_fee": float(reg_fee) if reg_fee else 0.0,
            "estimated_expenditure": float(est_exp) if est_exp else 0.0,
            "speaker_name": request.form.get("speaker_name", "").strip(),
            "speaker_designation": request.form.get("speaker_designation", "").strip(),
            "speaker_organization": request.form.get("speaker_organization", "").strip(),
            "speaker_email": request.form.get("speaker_email", "").strip(),
            "speaker_contact": request.form.get("speaker_contact", "").strip(),
            "speaker_expertise": request.form.get("speaker_expertise", "").strip(),
            "speaker_bio": request.form.get("speaker_bio", "").strip(),
        }
        try:
            update_firestore_authoritative_event(event_id, updates)
            flash("Event updated successfully!", "success")
            return redirect(url_for("event_detail", event_id=event_id))
        except Exception as e:
            logger.error(f"Edit event error for {event_id}: {e}")
            flash(f"Could not update event: {str(e)}", "danger")

    return render_template("event_form.html", event=event)


@app.route("/events/<event_id>/delete", methods=["POST"])
@role_required("admin", "faculty")
def delete_event(event_id):
    event = read_firestore_event(event_id)
    if not event:
        flash("Event not found.", "danger")
        return redirect(url_for("events"))

    user_id = session.get("user_id")
    role = session.get("role")
    if role == "faculty" and not user_reference_matches(event.get("created_by_firebase_uid") or event.get("created_by"), user_id):
        flash("You can only delete your own events.", "danger")
        return redirect(url_for("event_detail", event_id=event_id))

    try:
        delete_firestore_authoritative_event(event_id)
        flash("Event deleted successfully.", "info")
    except Exception as e:
        logger.error(f"Delete event error for {event_id}: {e}")
        flash(f"Could not delete event: {str(e)}", "danger")

    return redirect(url_for("events"))


@app.route("/events/<event_id>/manage", methods=["GET", "POST"])
@role_required("admin", "faculty")
def manage_event(event_id):
    event = read_firestore_event(event_id)
    if not event:
        flash("Event not found.", "danger")
        return redirect(url_for("events"))

    user_id = session.get("user_id")
    role = session.get("role")
    if role == "faculty" and not user_reference_matches(event.get("created_by_firebase_uid") or event.get("created_by"), user_id):
        flash("You can only manage your own events.", "danger")
        return redirect(url_for("event_detail", event_id=event_id))

    if request.method == "POST":
        action = request.form.get("action")
        if action == "update_status":
            new_status = request.form.get("status", "").strip()
            if new_status:
                try:
                    update_firestore_authoritative_event(event_id, {"status": new_status})
                    flash("Event status updated.", "success")
                except Exception as e:
                    logger.error(f"Error updating status for {event_id}: {e}")
                    flash(f"Could not update status: {e}", "danger")
        elif action == "post_update":
            title = request.form.get("title", "").strip()
            message = request.form.get("message", "").strip()
            if message:
                try:
                    create_firestore_authoritative_update(event_id, message, created_by_firebase_uid=user_id, title=title)
                    flash("Update published successfully.", "success")
                except Exception as e:
                    logger.error(f"Error publishing update for {event_id}: {e}")
                    flash(f"Could not post update: {e}", "danger")
            else:
                flash("Update message cannot be empty.", "warning")
        elif action == "attendance":
            reg_id = request.form.get("registration_id")
            att_status = request.form.get("status")
            att_val = request.form.get("attendance")
            
            if att_status:
                is_present = (att_status.lower() == "present")
                attendance_status = "present" if is_present else "absent"
            elif att_val is not None:
                is_present = bool(int(att_val)) if str(att_val).isdigit() else False
                attendance_status = "present" if is_present else "absent"
            else:
                is_present = False
                attendance_status = "absent"

            if reg_id:
                try:
                    update_data = {
                        "attendance": 1 if is_present else 0,
                        "attendance_status": attendance_status,
                    }
                    if not is_present:
                        # Invalidate/remove certificate metadata when marked absent
                        update_data["certificate_type"] = None
                        update_data["certificate_file"] = None
                        update_data["certificate_generated_at"] = None

                    update_firestore_authoritative_registration(
                        reg_id,
                        update_data,
                    )
                    if not is_present:
                        reg = get_firestore_registration(reg_id, event_id=event_id)
                        st_uid = reg.get("student_id") if reg else None
                        if st_uid:
                            delete_firestore_certificate_record(event_id, st_uid)
                    flash(f"Attendance updated: student marked as {attendance_status.capitalize()}.", "success")
                except Exception as e:
                    logger.error(f"Error updating attendance for {reg_id}: {e}")
                    flash(f"Could not update attendance: {e}", "danger")
        elif action == "certificate":
            reg_id = request.form.get("registration_id")
            cert_type = request.form.get("certificate_type", "Participation")
            if reg_id:
                try:
                    reg = get_firestore_registration(reg_id, event_id=event_id)
                    if not reg:
                        flash("Registration not found.", "danger")
                        return redirect(url_for("manage_event", event_id=event_id))

                    # RULE: Certificate can ONLY be generated after the student is marked PRESENT
                    att_status = (reg.get("attendance_status") or "").lower()
                    is_present = bool(reg.get("attendance") == 1 or att_status in ("present", "attended"))
                    if not is_present or att_status == "absent":
                        flash("Certificates cannot be generated for Absent students. Student must be marked Present first.", "danger")
                        return redirect(url_for("manage_event", event_id=event_id))

                    student_uid = reg.get("student_id")
                    if not student_uid:
                        flash("Student identifier is missing.", "danger")
                        return redirect(url_for("manage_event", event_id=event_id))

                    # Duplicate check: check if certificate is already generated
                    if reg.get("certificate_file"):
                        existing_path = os.path.join(CERT_DIR, reg.get("certificate_file"))
                        if os.path.isfile(existing_path):
                            flash("Certificate has already been generated for this student.", "info")
                            return redirect(url_for("manage_event", event_id=event_id))

                    client = get_firestore_client()
                    if client:
                        existing_certs = list(
                            client.collection("certificates")
                            .where("event_id", "==", str(event_id))
                            .where("student_uid", "==", str(student_uid))
                            .stream()
                        )
                        if existing_certs:
                            cert_doc = existing_certs[0].to_dict()
                            cfile = cert_doc.get("certificate_file") or cert_doc.get("filename")
                            if cfile and os.path.isfile(os.path.join(CERT_DIR, cfile)):
                                update_firestore_authoritative_registration(
                                    reg_id,
                                    {
                                        "certificate_type": cert_doc.get("certificate_type", cert_type),
                                        "certificate_file": cfile,
                                    },
                                )
                                flash("Certificate has already been generated for this student.", "info")
                                return redirect(url_for("manage_event", event_id=event_id))

                    # Read authoritative student profile (do NOT use dummy data)
                    student_profile = read_firestore_user_profile_by_firebase_uid(student_uid) if student_uid else {}
                    student_name = (student_profile.get("name") if student_profile else None) or reg.get("student_name") or reg.get("user_name") or reg.get("name") or "Student"
                    reg_no = (student_profile.get("reg_no") if student_profile else None) or (student_profile.get("college_id") if student_profile else None) or reg.get("reg_no") or ""
                    dept = (student_profile.get("department") if student_profile else None) or reg.get("department") or ""

                    filename = f"cert_{event_id}_{student_uid}.pdf"
                    os.makedirs(CERT_DIR, exist_ok=True)
                    filepath = os.path.join(CERT_DIR, filename)

                    # Read event-level template (if configured); falls back to defaults when {}
                    cert_template = read_firestore_cert_template(event_id) or None

                    generate_certificate(
                        path=filepath,
                        student_name=student_name,
                        reg_no=reg_no,
                        department=dept,
                        event_title=event.get("title", "Event"),
                        event_date=str(event.get("event_date", "")),
                        certificate_type=cert_type,
                        venue=event.get("venue", ""),
                        template=cert_template,
                    )

                    now_iso = datetime.now(timezone.utc).isoformat()
                    cert_record = {
                        "certificate_id": f"cert_{event_id}_{student_uid}",
                        "student_uid": str(student_uid),
                        "user_id": str(student_uid),
                        "user_firebase_uid": str(student_uid),
                        "registration_id": str(reg_id),
                        "event_id": str(event_id),
                        "event_title": event.get("title", "Event"),
                        "event_date": str(event.get("event_date", "")),
                        "student_name": student_name,
                        "reg_no": reg_no,
                        "department": dept,
                        "certificate_type": cert_type,
                        "certificate_file": filename,
                        "filename": filename,
                        "certificate_path": filepath,
                        "created_at": now_iso,
                        "generated_at": now_iso,
                        "generated_by": user_id,
                    }
                    create_firestore_certificate_record(cert_record)

                    update_firestore_authoritative_registration(
                        reg_id,
                        {
                            "certificate_type": cert_type,
                            "certificate_file": filename,
                            "certificate_generated_at": now_iso,
                        },
                    )
                    flash(f"Certificate generated successfully for {student_name}.", "success")
                except Exception as e:
                    logger.error(f"Error generating certificate for {reg_id}: {e}")
                    flash(f"Could not generate certificate: {e}", "danger")

        return redirect(url_for("manage_event", event_id=event_id))

    # Clean up any existing duplicate registration documents for this event in Firestore
    cleanup_duplicate_firestore_registrations(event_id=event_id)

    registrations = read_firestore_registrations_for_event(event_id)
    updates = read_firestore_updates_for_event(event_id)
    feedback_list = read_firestore_feedback_for_event(event_id)
    transcripts = read_firestore_transcripts_for_event(event_id)
    event_media = read_firestore_event_media(event_id)
    media_slots_data = event_media.get("slots") or {}

    geotagged_slots = ["geotagged_photo_1", "geotagged_photo_2", "geotagged_photo_3"]
    geotagged_count = sum(1 for k in geotagged_slots if k in media_slots_data)
    required_keys = ["geotagged_photo_1", "activity_photo_1", "activity_photo_2", "activity_photo_3", "activity_photo_4"]
    required_uploaded_count = sum(1 for k in required_keys if k in media_slots_data)

    media_summary = {
        "slots": media_slots_data,
        "total_uploaded": len(media_slots_data),
        "geotagged_count": geotagged_count,
        "has_geotagged": (geotagged_count > 0),
        "required_total": len(required_keys),
        "required_uploaded": required_uploaded_count,
        "all_required_uploaded": (required_uploaded_count == len(required_keys)),
    }

    photo_count = media_summary.get("total_uploaded", 0)

    return render_template(
        "manage_event.html",
        event=event,
        participants=registrations,
        registrations=registrations,
        updates=updates,
        feedback_list=feedback_list,
        transcripts=transcripts,
        event_media=event_media,
        media_slots=MEDIA_SLOT_DEFINITIONS,
        media_summary=media_summary,
        cert_template=read_firestore_cert_template(event_id) or {},
        cert_logo_slots=CERT_LOGO_SLOTS,
        cert_sig_slots=CERT_SIG_SLOTS,
        photo_count=photo_count,
    )


# ---------------------------------------------------------
# EVENT MEDIA / PHOTOGRAPHS ROUTES
# ---------------------------------------------------------
@app.route("/events/<event_id>/media/upload", methods=["POST"])
@role_required("admin", "faculty")
def upload_event_media(event_id):
    event = read_firestore_event(event_id)
    if not event:
        flash("Event not found.", "danger")
        return redirect(url_for("events"))

    user_id = session.get("user_id")
    role = session.get("role")
    if role == "faculty" and not user_reference_matches(event.get("created_by_firebase_uid") or event.get("created_by"), user_id):
        flash("You can only manage your own events.", "danger")
        return redirect(url_for("event_detail", event_id=event_id))

    target_slot = request.form.get("target_slot")
    upload_count = 0
    error_count = 0

    if target_slot and target_slot in MEDIA_SLOT_DEFINITIONS:
        file_storage = request.files.get("file") or request.files.get(target_slot)
        if file_storage and file_storage.filename:
            success, msg, _ = process_uploaded_media_file(event_id, target_slot, file_storage, user_id)
            if success:
                flash(msg, "success")
                upload_count += 1
            else:
                flash(msg, "danger")
                error_count += 1
        else:
            flash("Please select a file to upload.", "warning")
    else:
        # Check all slots in request.files
        for slot_key in MEDIA_SLOT_DEFINITIONS:
            file_storage = request.files.get(slot_key)
            if file_storage and file_storage.filename:
                success, msg, _ = process_uploaded_media_file(event_id, slot_key, file_storage, user_id)
                if success:
                    upload_count += 1
                else:
                    flash(msg, "danger")
                    error_count += 1

        if upload_count > 0:
            flash(f"Successfully uploaded {upload_count} file(s).", "success")
        elif error_count == 0:
            flash("No new files were selected for upload.", "info")

    return redirect(url_for("manage_event", event_id=event_id) + "#photographs-section")


@app.route("/events/<event_id>/media/<slot_key>/delete", methods=["POST"])
@role_required("admin", "faculty")
def delete_event_media(event_id, slot_key):
    event = read_firestore_event(event_id)
    if not event:
        flash("Event not found.", "danger")
        return redirect(url_for("events"))

    user_id = session.get("user_id")
    role = session.get("role")
    if role == "faculty" and not user_reference_matches(event.get("created_by_firebase_uid") or event.get("created_by"), user_id):
        flash("You can only manage your own events.", "danger")
        return redirect(url_for("event_detail", event_id=event_id))

    if slot_key in MEDIA_SLOT_DEFINITIONS:
        delete_firestore_event_media_slot(event_id, slot_key)
        flash(f"Removed {MEDIA_SLOT_DEFINITIONS[slot_key]['label']}.", "info")
    else:
        flash("Invalid media slot.", "warning")

    return redirect(url_for("manage_event", event_id=event_id) + "#photographs-section")


@app.route("/events/<event_id>/media/<slot_key>")
@login_required
def view_event_media(event_id, slot_key):
    media_data = read_firestore_event_media(event_id)
    slot_info = media_data.get("slots", {}).get(slot_key)
    if not slot_info:
        flash("File not found.", "warning")
        return redirect(url_for("manage_event", event_id=event_id))

    drive_file_id = slot_info.get("drive_file_id")
    if drive_file_id:
        try:
            file_bytes, content_type, filename = download_drive_file(drive_file_id)
            import io
            return send_file(
                io.BytesIO(file_bytes),
                mimetype=content_type or slot_info.get("content_type", "application/octet-stream"),
                as_attachment=False,
                download_name=slot_info.get("original_filename") or filename,
            )
        except Exception as e:
            logger.error(f"Error serving Google Drive file {drive_file_id}: {e}")
            flash("Could not retrieve file from Google Drive.", "danger")
            return redirect(url_for("manage_event", event_id=event_id))

    # Fallback to local storage for legacy files
    filename = slot_info.get("filename")
    if filename:
        upload_dir = get_event_upload_dir(event_id)
        file_path = os.path.join(upload_dir, secure_filename(filename))
        if os.path.isfile(file_path):
            return send_from_directory(upload_dir, secure_filename(filename), as_attachment=False)

    flash("File not found on server.", "warning")
    return redirect(url_for("manage_event", event_id=event_id))


@app.route("/events/<event_id>/update", methods=["POST"])
@role_required("admin", "faculty")
def post_event_update(event_id):
    message = request.form.get("message", "").strip()
    if not message:
        flash("Update message cannot be empty.", "warning")
        return redirect(url_for("manage_event", event_id=event_id))

    user_id = session.get("user_id")
    try:
        create_firestore_authoritative_update(event_id, message, created_by_firebase_uid=user_id)
        flash("Event update posted.", "success")
    except Exception as e:
        logger.error(f"Post update error for {event_id}: {e}")
        flash(f"Could not post update: {str(e)}", "danger")

    return redirect(url_for("manage_event", event_id=event_id))


# ---------------------------------------------------------
# TRANSCRIPT ROUTES
# ---------------------------------------------------------
@app.route("/events/<event_id>/transcript/save", methods=["POST"])
@role_required("admin", "faculty")
def save_transcript(event_id):
    event = read_firestore_event(event_id)
    if not event:
        flash("Event not found.", "danger")
        return redirect(url_for("events"))

    user_id = session.get("user_id")
    raw_text = (request.form.get("transcript") or request.form.get("raw_text") or "").strip()
    summary_text = request.form.get("summary_text", "").strip()
    title = request.form.get("title", "").strip() or "Event Transcript"
    key_points_raw = request.form.get("key_points", "")
    action_items_raw = request.form.get("action_items", "")
    topics_raw = request.form.get("topics", "")
    duration_seconds = request.form.get("duration_seconds", "0") or "0"

    def parse_list(raw):
        items = [x.strip() for x in raw.splitlines() if x.strip()]
        return items

    if not raw_text and not summary_text:
        flash("Transcript text or summary is required.", "danger")
        return redirect(url_for("manage_event", event_id=event_id))

    try:
        create_firestore_authoritative_transcript(
            event_id=event_id,
            user_firebase_uid=user_id,
            raw_text=raw_text,
            summary_text=summary_text,
            title=title,
            key_points=parse_list(key_points_raw),
            action_items=parse_list(action_items_raw),
            topics=parse_list(topics_raw),
            duration_seconds=int(duration_seconds) if duration_seconds.isdigit() else 0,
        )
        flash("Transcript saved successfully.", "success")
    except Exception as e:
        logger.error(f"Save transcript error for {event_id}: {e}")
        flash(f"Could not save transcript: {str(e)}", "danger")

    return redirect(url_for("manage_event", event_id=event_id))


@app.route("/transcripts/<transcript_id>/edit", methods=["GET", "POST"])
@app.route("/events/<event_id>/transcript/<transcript_id>/edit", methods=["GET", "POST"])
@role_required("admin", "faculty")
def edit_transcript(transcript_id, event_id=None):
    transcript = read_firestore_transcript(transcript_id)
    if not transcript:
        flash("Transcript not found.", "danger")
        return redirect(url_for("events"))

    target_event_id = event_id or transcript.get("event_id")

    if request.method == "POST":
        raw_text = (request.form.get("transcript") or request.form.get("raw_text") or "").strip()
        summary = (request.form.get("summary") or request.form.get("summary_text") or "").strip()
        title = request.form.get("title", "").strip() or transcript.get("title", "Event Transcript")

        updates = {
            "title": title,
            "raw_text": raw_text,
            "transcript": raw_text,
            "summary": summary,
            "summary_text": summary,
        }
        try:
            update_firestore_authoritative_transcript(transcript_id, updates)
            flash("Transcript updated.", "success")
            return redirect(url_for("manage_event", event_id=target_event_id))
        except Exception as e:
            logger.error(f"Edit transcript error for {transcript_id}: {e}")
            flash(f"Could not update transcript: {str(e)}", "danger")

    return render_template("edit_transcript.html", transcript=transcript)


@app.route("/transcripts/<transcript_id>/delete", methods=["POST"])
@app.route("/events/<event_id>/transcript/<transcript_id>/delete", methods=["POST"])
@role_required("admin", "faculty")
def delete_transcript(transcript_id, event_id=None):
    transcript = read_firestore_transcript(transcript_id)
    if not transcript:
        flash("Transcript not found.", "danger")
        return redirect(url_for("events"))

    target_event_id = event_id or transcript.get("event_id")
    try:
        delete_firestore_authoritative_transcript(transcript_id)
        flash("Transcript deleted.", "info")
    except Exception as e:
        logger.error(f"Delete transcript error for {transcript_id}: {e}")
        flash(f"Could not delete transcript: {str(e)}", "danger")

    return redirect(url_for("manage_event", event_id=target_event_id))


@app.route("/transcripts/<transcript_id>/export-pdf")
@app.route("/events/<event_id>/transcript/<transcript_id>/export-pdf")
@login_required
def export_transcript_pdf(transcript_id, event_id=None):
    transcript = read_firestore_transcript(transcript_id)
    if not transcript:
        flash("Transcript not found.", "danger")
        return redirect(url_for("events"))

    target_event_id = event_id or transcript.get("event_id")
    event = read_firestore_event(target_event_id)

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        import io

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph(f"Transcript: {transcript.get('title', 'Event Transcript')}", styles["Title"]))
        if event:
            story.append(Paragraph(f"Event: {event.get('title', '')}", styles["Heading2"]))
        story.append(Spacer(1, 12))

        if transcript.get("summary_text") or transcript.get("summary"):
            story.append(Paragraph("Summary", styles["Heading2"]))
            story.append(Paragraph(transcript.get("summary_text") or transcript.get("summary", ""), styles["Normal"]))
            story.append(Spacer(1, 12))

        raw = transcript.get("raw_text") or transcript.get("transcript") or ""
        if raw:
            story.append(Paragraph("Full Transcript", styles["Heading2"]))
            for line in raw.splitlines():
                if line.strip():
                    story.append(Paragraph(line, styles["Normal"]))
            story.append(Spacer(1, 12))

        doc.build(story)
        buf.seek(0)

        from flask import send_file
        return send_file(
            buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"transcript_{transcript_id}.pdf",
        )
    except ImportError:
        flash("PDF export requires reportlab. Please install it.", "danger")
        return redirect(url_for("event_detail", event_id=target_event_id))
    except Exception as e:
        logger.error(f"PDF export error for {transcript_id}: {e}")
        flash(f"PDF export failed: {str(e)}", "danger")
        return redirect(url_for("event_detail", event_id=target_event_id))


# ---------------------------------------------------------
# REPORT & CERTIFICATE ROUTES
# ---------------------------------------------------------
@app.route("/events/<event_id>/report", methods=["GET", "POST"])
@role_required("admin", "faculty")
def generate_report(event_id):
    event = read_firestore_event(event_id)
    if not event:
        flash("Event not found.", "danger")
        return redirect(url_for("events"))

    registrations = read_firestore_registrations_for_event(event_id)
    feedback_list = read_firestore_feedback_for_event(event_id)
    updates = read_firestore_updates_for_event(event_id)
    transcripts = read_firestore_transcripts_for_event(event_id)

    if request.method == "POST":
        report_type = request.form.get("report_type", "event_report")
        if report_type not in ALLOWED_REPORT_TYPES:
            report_type = "event_report"

        user_inputs = {
            # Shared fields (Event Report + SOP)
            "institute_name": request.form.get("institute_name", "Sri Ramakrishna Institute of Technology"),
            "department": request.form.get("department", "Department of Electrical and Electronics Engineering"),
            "speaker_name": request.form.get("speaker_name", ""),
            "coordinator": request.form.get("coordinator", ""),
            "hod": request.form.get("hod", "Dr. Head of Department"),
            "principal": request.form.get("principal", "Dr. Principal"),
            "program_name": request.form.get("program_name", event.get("program_name", "")),
            "summary": request.form.get("summary", ""),
            # SOP-specific fields (items 1–15)
            "dept_assoc_club": request.form.get("dept_assoc_club", ""),
            "nature_of_programme": request.form.get("nature_of_programme", event.get("program_name", "")),
            "participants": request.form.get("participants", "Students (All Years)"),
            "total_audience": request.form.get("total_audience", str(event.get("capacity") or "")),
            "resource_person_name": request.form.get("resource_person_name", ""),
            "resource_person_designation": request.form.get("resource_person_designation", ""),
            "resource_person_org": request.form.get("resource_person_org", ""),
            "resource_person_address": request.form.get("resource_person_address", ""),
            "resource_person_phone": request.form.get("resource_person_phone", ""),
            "resource_person_email": request.form.get("resource_person_email", ""),
            "estimated_expenditure": request.form.get(
                "estimated_expenditure",
                f"Rs {event.get('estimated_expenditure') or 'N/A'}"
            ),
            "fund_source": request.form.get("fund_source", "HOD's Fund"),
            "objective": request.form.get("objective", event.get("objectives", "")),
            "student_contribution": request.form.get("student_contribution", event.get("benefits", "")),
            "institution_contribution": request.form.get("institution_contribution", ""),
            "sop_date": request.form.get("sop_date", ""),
        }


        # Handle uploaded photos
        photo_paths = []
        uploaded_photos = request.files.getlist("photos")
        upload_dir = os.path.join(BASE_DIR, "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        for photo in uploaded_photos:
            if photo and photo.filename:
                ext = os.path.splitext(photo.filename)[1].lower()
                if ext in ALLOWED_REPORT_IMAGE_EXTENSIONS:
                    photo_name = secure_filename(f"report_{event_id}_{uuid.uuid4().hex[:8]}{ext}")
                    save_path = os.path.join(upload_dir, photo_name)
                    photo.save(save_path)
                    photo_paths.append(save_path)

        # If no ad-hoc photos uploaded in form, use photos from event media
        if not photo_paths:
            saved_media = read_firestore_event_media(event_id)
            media_slots_data = saved_media.get("slots") or {}
            media_dir = get_event_upload_dir(event_id)
            for slot_k in ["activity_photo_1", "activity_photo_2", "activity_photo_3", "activity_photo_4", "activity_photo_5", "geotagged_photo_1", "geotagged_photo_2", "geotagged_photo_3"]:
                if slot_k in media_slots_data and not media_slots_data[slot_k].get("is_pdf"):
                    slot_item = media_slots_data[slot_k]
                    drive_fid = slot_item.get("drive_file_id")
                    if drive_fid:
                        try:
                            file_bytes, ctype, fname = download_drive_file(drive_fid)
                            import tempfile
                            temp_img = os.path.join(tempfile.gettempdir(), f"report_drive_{uuid.uuid4().hex[:8]}_{fname}")
                            with open(temp_img, "wb") as f_img:
                                f_img.write(file_bytes)
                            photo_paths.append(temp_img)
                        except Exception as e:
                            logger.warning(f"Could not load photo from Drive for report: {e}")
                    else:
                        fn = slot_item.get("filename")
                        if fn:
                            fp = os.path.join(media_dir, fn)
                            if os.path.isfile(fp):
                                photo_paths.append(fp)

        # Attendance stats
        total_registered = len(registrations)
        present_count = len([r for r in registrations if r.get("attendance") or r.get("attendance_status") == "attended"])
        absent_count = max(0, total_registered - present_count)
        att_percentage = (present_count / total_registered * 100.0) if total_registered > 0 else 0.0
        certs_generated = len([r for r in registrations if r.get("certificate_file")])

        attendance_stats = {
            "registered": total_registered,
            "present": present_count,
            "absent": absent_count,
            "percentage": att_percentage,
            "certificates_generated": certs_generated,
        }

        # Feedback stats
        ratings = [int(f.get("rating") or 0) for f in feedback_list]
        avg_rating = (sum(ratings) / len(ratings)) if ratings else 0.0
        feedback_stats = {
            "total": len(feedback_list),
            "avg_rating": avg_rating,
            "distribution": {i: ratings.count(i) for i in range(1, 6)},
        }

        # Latest transcript text
        transcript_text = "No transcript available."
        if transcripts:
            transcript_text = transcripts[0].get("raw_text") or transcripts[0].get("transcript") or "No transcript available."

        # Generate report PDF
        import tempfile
        pdf_name = f"{report_type}_{event_id}_{uuid.uuid4().hex[:8]}.pdf"
        pdf_path = os.path.join(tempfile.gettempdir(), pdf_name)

        # Handle optional initiative logos for SOP (selection + optional custom uploads)
        sop_custom_logos = {}
        sop_included_logos = []
        if report_type == "sop_report":
            has_logo_selection = ("sop_logo_selection_active" in request.form)
            sop_logo_dir = os.path.join(upload_dir, f"sop_logos_{event_id}")
            os.makedirs(sop_logo_dir, exist_ok=True)
            for logo_key in ["snr", "srit", "hive", "ecell", "idealab", "iic", "sish"]:
                is_selected = (request.form.get(f"include_logo_{logo_key}") == "on") if has_logo_selection else True
                if is_selected:
                    sop_included_logos.append(logo_key)
                    logo_file = request.files.get(f"logo_{logo_key}")
                    if logo_file and logo_file.filename:
                        ext = os.path.splitext(logo_file.filename)[1].lower()
                        if ext in ALLOWED_REPORT_IMAGE_EXTENSIONS:
                            logo_name = secure_filename(f"sop_logo_{event_id}_{logo_key}_{uuid.uuid4().hex[:6]}{ext}")
                            save_logo_path = os.path.join(sop_logo_dir, logo_name)
                            logo_file.save(save_logo_path)
                            sop_custom_logos[logo_key] = save_logo_path

        try:
            if report_type == "sop_report":
                generate_sop_pdf_report(
                    pdf_path=pdf_path,
                    event_data=event,
                    user_inputs=user_inputs,
                    attendance_stats=attendance_stats,
                    feedback_stats=feedback_stats,
                    transcript_text=transcript_text,
                    photo_paths=photo_paths,
                    sop_template={
                        "custom_logos": sop_custom_logos,
                        "included_logos": sop_included_logos,
                    },
                )
            else:
                generate_event_pdf_report(
                    pdf_path=pdf_path,
                    report_type=report_type,
                    event_data=event,
                    user_inputs=user_inputs,
                    attendance_stats=attendance_stats,
                    feedback_stats=feedback_stats,
                    transcript_text=transcript_text,
                    photo_paths=photo_paths,
                )

            return send_file(
                pdf_path,
                mimetype="application/pdf",
                as_attachment=True,
                download_name=f"{report_type}_{event_id}.pdf",
            )
        except Exception as e:
            logger.error(f"Error generating {report_type} for event {event_id}: {e}")
            flash(f"Could not generate report: {e}", "danger")
            return redirect(url_for("manage_event", event_id=event_id))

    avg_rating = 0
    if feedback_list:
        ratings = [int(f.get("rating") or 0) for f in feedback_list]
        avg_rating = sum(ratings) / len(ratings) if ratings else 0

    return render_template(
        "report.html",
        event=event,
        registrations=registrations,
        feedback_list=feedback_list,
        updates=updates,
        transcripts=transcripts,
        avg_rating=round(avg_rating, 1),
        total_registrations=len(registrations),
        total_feedback=len(feedback_list),
    )


@app.route("/certificates/<path:filename>")
@app.route("/events/<event_id>/certificate")
@login_required
def download_certificate(event_id=None, filename=None):
    user_id = session.get("user_id")
    role = session.get("role", "student")

    if filename:
        safe_name = secure_filename(os.path.basename(filename))
        file_path = os.path.join(CERT_DIR, safe_name)
        if not os.path.isfile(file_path):
            flash("Certificate file not found.", "warning")
            return redirect(url_for("dashboard"))

        # Security check: student can only download their own certificate if marked PRESENT
        if role == "student":
            user_certs = read_firestore_certificates_for_user(user_id)
            user_files = {c.get("certificate_file") for c in user_certs if c.get("certificate_file")}
            user_files.update({c.get("filename") for c in user_certs if c.get("filename")})

            if safe_name not in user_files:
                flash("Access denied: You cannot download this certificate or your attendance is not marked Present.", "danger")
                return redirect(url_for("dashboard")), 403

        return send_from_directory(CERT_DIR, safe_name, as_attachment=True)

    if event_id:
        user_certs = read_firestore_certificates_for_user(user_id)
        match = next((c for c in user_certs if str(c.get("event_id")) == str(event_id)), None)
        target_file = match.get("certificate_file") if match else None

        if not target_file:
            flash("No certificate is available for this event (student must be marked Present and certificate generated).", "warning")
            return redirect(url_for("dashboard"))

        safe_name = secure_filename(os.path.basename(target_file))
        file_path = os.path.join(CERT_DIR, safe_name)
        if not os.path.isfile(file_path):
            flash("Certificate file not found on server.", "warning")
            return redirect(url_for("dashboard"))

        return send_from_directory(CERT_DIR, safe_name, as_attachment=True)

    return redirect(url_for("dashboard"))


# ---------------------------------------------------------
# CERTIFICATE TEMPLATE ROUTES
# ---------------------------------------------------------
@app.route("/events/<event_id>/certificate-template", methods=["POST"])
@role_required("admin", "faculty")
def save_certificate_template(event_id):
    event = read_firestore_event(event_id)
    if not event:
        flash("Event not found.", "danger")
        return redirect(url_for("events"))

    user_id = session.get("user_id")
    role = session.get("role")
    if role == "faculty" and not user_reference_matches(event.get("created_by_firebase_uid") or event.get("created_by"), user_id):
        flash("You can only configure certificate templates for your own events.", "danger")
        return redirect(url_for("event_detail", event_id=event_id))

    action = request.form.get("action", "save")

    if action == "reset":
        delete_firestore_cert_template(event_id)
        flash("Certificate template reset to system defaults.", "info")
        return redirect(url_for("manage_event", event_id=event_id) + "#certificate-template-section")

    # Load existing template to preserve uploaded assets if not replaced
    existing = read_firestore_cert_template(event_id) or {}
    existing_logos_map = {lg.get("slot"): lg for lg in (existing.get("logos") or []) if lg.get("slot")}
    existing_sigs_map = {sg.get("slot"): sg for sg in (existing.get("signatures") or []) if sg.get("slot")}

    # Text and styling fields
    template_data = {
        "institution_name": request.form.get("institution_name", "").strip(),
        "tagline": request.form.get("tagline", "").strip(),
        "body_text": request.form.get("body_text", "").strip(),
        "footer_line1": request.form.get("footer_line1", "").strip(),
        "footer_line2": request.form.get("footer_line2", "").strip(),
        "bg_color": request.form.get("bg_color", "#f7fbff").strip() or "#f7fbff",
        "border_color": request.form.get("border_color", "#0b4f6c").strip() or "#0b4f6c",
        "name_color": request.form.get("name_color", "#0b4f6c").strip() or "#0b4f6c",
        "badge_color": request.form.get("badge_color", "#d39b2a").strip() or "#d39b2a",
    }

    # Process logos
    processed_logos = []
    for slot_key, slot_label in CERT_LOGO_SLOTS.items():
        show = bool(request.form.get(f"show_logo_{slot_key}"))
        order_val = request.form.get(f"order_logo_{slot_key}", "1")
        try:
            order = int(order_val)
        except ValueError:
            order = 1
        width_val = request.form.get(f"width_logo_{slot_key}", "60")
        try:
            width = int(width_val)
        except ValueError:
            width = 60

        # Existing logo path
        prev_logo = existing_logos_map.get(slot_key, {})
        logo_path = prev_logo.get("path", "")

        # Check for new uploaded file
        uploaded_file = request.files.get(f"file_logo_{slot_key}")
        if uploaded_file and uploaded_file.filename:
            rel_path, err = _save_cert_asset(event_id, f"logo_{slot_key}", uploaded_file)
            if err:
                flash(f"Error uploading {slot_label}: {err}", "danger")
            else:
                logo_path = rel_path

        processed_logos.append({
            "slot": slot_key,
            "label": slot_label,
            "path": logo_path,
            "show": show,
            "order": order,
            "width": width,
            "height": 48,
        })

    template_data["logos"] = processed_logos

    # Process signatures
    processed_sigs = []
    for slot_key, slot_label in CERT_SIG_SLOTS.items():
        show = bool(request.form.get(f"show_sig_{slot_key}"))
        name = request.form.get(f"name_sig_{slot_key}", "").strip()
        designation = request.form.get(f"desig_sig_{slot_key}", "").strip()
        order_val = request.form.get(f"order_sig_{slot_key}", "1")
        try:
            order = int(order_val)
        except ValueError:
            order = 1

        prev_sig = existing_sigs_map.get(slot_key, {})
        sig_path = prev_sig.get("path", "")

        uploaded_file = request.files.get(f"file_sig_{slot_key}")
        if uploaded_file and uploaded_file.filename:
            rel_path, err = _save_cert_asset(event_id, f"sig_{slot_key}", uploaded_file)
            if err:
                flash(f"Error uploading {slot_label}: {err}", "danger")
            else:
                sig_path = rel_path

        processed_sigs.append({
            "slot": slot_key,
            "label": slot_label,
            "path": sig_path,
            "name": name,
            "designation": designation,
            "show": show,
            "order": order,
        })

    template_data["signatures"] = processed_sigs

    try:
        save_firestore_cert_template(event_id, template_data)
        flash("Certificate template saved successfully!", "success")
    except Exception as e:
        logger.error(f"Error saving certificate template for event {event_id}: {e}")
        flash(f"Could not save certificate template: {e}", "danger")

    return redirect(url_for("manage_event", event_id=event_id) + "#certificate-template-section")


@app.route("/events/<event_id>/certificate-preview")
@role_required("admin", "faculty")
def preview_certificate_template(event_id):
    event = read_firestore_event(event_id)
    if not event:
        flash("Event not found.", "danger")
        return redirect(url_for("events"))

    user_id = session.get("user_id")
    role = session.get("role")
    if role == "faculty" and not user_reference_matches(event.get("created_by_firebase_uid") or event.get("created_by"), user_id):
        flash("Access denied.", "danger")
        return redirect(url_for("event_detail", event_id=event_id))

    template = read_firestore_cert_template(event_id) or None

    preview_filename = f"preview_cert_{event_id}.pdf"
    os.makedirs(CERT_DIR, exist_ok=True)
    preview_path = os.path.join(CERT_DIR, preview_filename)

    try:
        generate_certificate(
            path=preview_path,
            student_name="SAMPLE STUDENT NAME",
            reg_no="2026SAMPLE01",
            department="Department of Computer Science and Engineering",
            event_title=event.get("title", "Sample Event Title"),
            event_date=str(event.get("event_date", "2026-09-16")),
            certificate_type="Participation",
            venue=event.get("venue", "Campus Auditorium"),
            template=template,
        )
        return send_from_directory(CERT_DIR, preview_filename, as_attachment=False, mimetype="application/pdf")
    except Exception as e:
        logger.error(f"Error generating preview certificate for event {event_id}: {e}")
        flash(f"Could not generate preview: {e}", "danger")
        return redirect(url_for("manage_event", event_id=event_id) + "#certificate-template-section")


# ---------------------------------------------------------
# FEEDBACK ROUTE
# ---------------------------------------------------------
@app.route("/events/<event_id>/feedback", methods=["GET", "POST"])
@login_required
def feedback(event_id):
    event = read_firestore_event(event_id)
    if not event:
        flash("Event not found.", "danger")
        return redirect(url_for("events"))

    user_id = session.get("user_id")

    if request.method == "POST":
        q1_rating = request.form.get("q1_rating") or request.form.get("rating", "")
        q2_rating = request.form.get("q2_rating") or request.form.get("rating", "")
        q3_rating = request.form.get("q3_rating") or request.form.get("rating", "")
        q4_response = (request.form.get("q4_response") or request.form.get("comments") or request.form.get("comment") or "").strip()
        q5_response = (request.form.get("q5_response") or "").strip()

        # Validation: all 5 questions are required
        if not (q1_rating and q2_rating and q3_rating and q4_response and q5_response):
            flash("Please answer all 5 feedback questions before submitting.", "danger")
            existing_feedback = read_firestore_feedback_for_event(event_id)
            user_feedback = None
            for fb in existing_feedback:
                if user_reference_matches(fb.get("student_uid") or fb.get("user_firebase_uid") or fb.get("user_id"), user_id):
                    user_feedback = fb
                    break
            return render_template("feedback.html", event=event, user_feedback=user_feedback)

        try:
            q1 = max(1, min(5, int(q1_rating)))
            q2 = max(1, min(5, int(q2_rating)))
            q3 = max(1, min(5, int(q3_rating)))
        except (ValueError, TypeError):
            flash("Invalid rating values provided.", "danger")
            return redirect(url_for("feedback", event_id=event_id))

        try:
            create_firestore_authoritative_feedback(
                event_id=event_id,
                user_firebase_uid=user_id,
                q1_rating=q1,
                q2_rating=q2,
                q3_rating=q3,
                q4_response=q4_response,
                q5_response=q5_response,
            )
            flash("Feedback submitted successfully. Thank you for your feedback!", "success")
            return redirect(url_for("event_detail", event_id=event_id))
        except Exception as e:
            logger.error(f"Feedback error for {event_id}, {user_id}: {e}")
            flash(f"Could not submit feedback: {str(e)}", "danger")

    existing_feedback = read_firestore_feedback_for_event(event_id)
    user_feedback = None
    for fb in existing_feedback:
        if user_reference_matches(fb.get("student_uid") or fb.get("user_firebase_uid") or fb.get("user_id"), user_id):
            user_feedback = fb
            break

    return render_template("feedback.html", event=event, user_feedback=user_feedback)


# ---------------------------------------------------------
# PROFILE ROUTE
# ---------------------------------------------------------
@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user_id = session.get("user_id")
    user = read_firestore_user_profile_by_firebase_uid(user_id)

    if request.method == "POST":
        updates = {
            "name": request.form.get("name", "").strip(),
            "phone": request.form.get("phone", "").strip(),
            "department": request.form.get("department", "").strip(),
            "year": request.form.get("year", "").strip(),
            "college_id": request.form.get("college_id", "").strip(),
        }
        # Filter out empty strings to avoid overwriting with blanks
        updates = {k: v for k, v in updates.items() if v}

        try:
            updated = update_firestore_user_profile(user_id, updates)
            session["name"] = updated.get("name", session.get("name", ""))
            flash("Profile updated successfully.", "success")
            return redirect(url_for("profile"))
        except Exception as e:
            logger.error(f"Profile update error for {user_id}: {e}")
            flash(f"Could not update profile: {str(e)}", "danger")

    my_registrations = read_firestore_registrations_for_user(user_id)
    return render_template("profile.html", user=user, registrations=my_registrations)


# ---------------------------------------------------------
# ERROR HANDLERS
# ---------------------------------------------------------
@app.errorhandler(404)
def not_found_error(error):
    return render_template("errors/404.html"), 404


@app.errorhandler(403)
def forbidden_error(error):
    return render_template("errors/403.html"), 403


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return render_template("errors/500.html"), 500


# ---------------------------------------------------------
# STARTUP
# ---------------------------------------------------------
log_firebase_auth_startup_diagnostics()

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0").lower() in ("1", "true")
    app.run(host=host, port=port, debug=debug)
