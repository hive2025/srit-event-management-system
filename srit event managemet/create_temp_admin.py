import os
from datetime import datetime

import firebase_admin
from firebase_admin import auth, firestore


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_PROFILE_COLLECTION = "user_profiles"


def load_local_environment():
    for env_path in (os.path.join(BASE_DIR, ".env"), os.path.join(os.getcwd(), ".env")):
        if not os.path.isfile(env_path):
            continue
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


def require_temp_admin_credentials():
    email = (os.environ.get("TEMP_ADMIN_EMAIL") or "").strip().lower()
    password = os.environ.get("TEMP_ADMIN_PASSWORD") or ""
    if not email:
        raise RuntimeError("TEMP_ADMIN_EMAIL is required.")
    if not password:
        raise RuntimeError("TEMP_ADMIN_PASSWORD is required.")
    return email, password


def initialize_firebase():
    try:
        return firebase_admin.get_app()
    except ValueError:
        return firebase_admin.initialize_app()


def get_auth_user_by_email(email):
    try:
        return auth.get_user_by_email(email)
    except auth.UserNotFoundError:
        return None


def firestore_profile_from_snapshot(snapshot):
    if not snapshot.exists:
        return None
    profile = snapshot.to_dict() or {}
    profile["_doc_id"] = snapshot.id
    return profile


def profiles_equivalent(left, right):
    fields = (
        "id", "sqlite_user_id", "firebase_uid", "name", "reg_no", "department",
        "year", "email", "phone", "role", "created_at",
    )
    return all(left.get(field) == right.get(field) for field in fields)


def find_profile_by_field(db, field, value):
    if not value:
        return None
    if field == "firebase_uid":
        uid_snapshot = db.collection(USER_PROFILE_COLLECTION).document(str(value)).get()
        if uid_snapshot.exists:
            return firestore_profile_from_snapshot(uid_snapshot)
    matches = list(
        db.collection(USER_PROFILE_COLLECTION)
        .where(field, "==", value)
        .limit(3)
        .stream()
    )
    preferred = None
    unique_profiles = []
    for match in matches:
        profile = firestore_profile_from_snapshot(match)
        if not profile:
            continue
        if profile.get("firebase_uid") and match.id == profile["firebase_uid"]:
            preferred = profile
        if not any(profiles_equivalent(profile, existing) for existing in unique_profiles):
            unique_profiles.append(profile)
    if preferred:
        return preferred
    if len(unique_profiles) > 1:
        raise RuntimeError(f"Multiple Firestore profiles found for {field}. No changes were made.")
    return unique_profiles[0] if unique_profiles else None


def existing_firestore_user_ids(db):
    ids = set()
    for snapshot in db.collection(USER_PROFILE_COLLECTION).stream():
        profile = snapshot.to_dict() or {}
        for value in (snapshot.id, profile.get("id"), profile.get("sqlite_user_id")):
            try:
                ids.add(int(value))
            except (TypeError, ValueError):
                continue
    return ids


def next_available_profile_id(db):
    used_ids = existing_firestore_user_ids(db)
    candidate = (max(used_ids) if used_ids else 0) + 1
    while db.collection(USER_PROFILE_COLLECTION).document(str(candidate)).get().exists:
        candidate += 1
    return candidate


def create_admin_profile(db, firebase_user, email):
    profile_id = next_available_profile_id(db)
    created_at = datetime.now().isoformat(timespec="seconds")
    profile = {
        "id": profile_id,
        "sqlite_user_id": profile_id,
        "firebase_uid": firebase_user.uid,
        "name": "Temporary Admin",
        "reg_no": "",
        "department": "Administration",
        "year": "",
        "email": email,
        "phone": "",
        "role": "admin",
        "created_at": created_at,
    }
    db.collection(USER_PROFILE_COLLECTION).document(firebase_user.uid).create(profile)
    return profile


def ensure_uid_profile_document(db, firebase_user, profile):
    profile = dict(profile)
    profile.pop("_doc_id", None)
    profile["firebase_uid"] = firebase_user.uid
    uid_ref = db.collection(USER_PROFILE_COLLECTION).document(firebase_user.uid)
    if not uid_ref.get().exists:
        uid_ref.create(profile)
    return firestore_profile_from_snapshot(uid_ref.get())


def main():
    load_local_environment()
    email, password = require_temp_admin_credentials()
    initialize_firebase()
    db = firestore.client()

    profile_by_email = find_profile_by_field(db, "email", email)
    if profile_by_email and profile_by_email.get("role") != "admin":
        raise RuntimeError("A non-admin Firestore profile already uses TEMP_ADMIN_EMAIL. No changes were made.")

    firebase_user = get_auth_user_by_email(email)
    auth_state = "existing"
    if firebase_user is None:
        firebase_user = auth.create_user(email=email, password=password)
        auth_state = "created"

    profile_by_uid = find_profile_by_field(db, "firebase_uid", firebase_user.uid)
    if profile_by_uid and profile_by_uid.get("role") != "admin":
        raise RuntimeError("A non-admin Firestore profile already uses this Firebase UID. No changes were made.")

    profile = profile_by_uid or profile_by_email
    profile_state = "existing" if profile else "created"
    if profile is None:
        profile = create_admin_profile(db, firebase_user, email)
    else:
        profile = ensure_uid_profile_document(db, firebase_user, profile)

    verified_user = auth.get_user(firebase_user.uid)
    verified_profile = find_profile_by_field(db, "firebase_uid", verified_user.uid)
    if not verified_user or not verified_profile or verified_profile.get("role") != "admin":
        raise RuntimeError("Temporary admin verification failed.")

    print("Temporary admin provisioning complete.")
    print(f"Firebase Auth user: {auth_state}")
    print(f"Firestore admin profile: {profile_state}")
    print(f"Email: {verified_user.email}")
    print(f"Firebase UID: {verified_user.uid}")
    print(f"Firestore profile document: {verified_profile['_doc_id']}")
    print("Password was read from TEMP_ADMIN_PASSWORD and was not printed or stored by this script.")


if __name__ == "__main__":
    main()
