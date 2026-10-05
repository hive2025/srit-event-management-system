"""
Google Drive API v3 Storage Module for SRIT Event Management
Handles event media uploads, downloads, folder management, and deletions in Google Drive.
"""

import os
import io
import json
import logging
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# Constants & Scopes
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]

DEFAULT_ROOT_FOLDER_ID = "1nTcP02DIkSA0Ail3tqxvYQeneZn5wttZ"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def get_root_folder_id():
    """Retrieve Google Drive Root Folder ID from environment or default."""
    return os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", DEFAULT_ROOT_FOLDER_ID).strip() or DEFAULT_ROOT_FOLDER_ID


def get_drive_credentials():
    """
    Retrieve and refresh OAuth 2.0 credentials for Google Drive API.
    Supports:
    1. Environment variables: GOOGLE_DRIVE_CLIENT_ID, GOOGLE_DRIVE_CLIENT_SECRET, GOOGLE_DRIVE_REFRESH_TOKEN
    2. Environment JSON: GOOGLE_DRIVE_TOKEN_JSON or GOOGLE_DRIVE_CREDENTIALS_JSON
    3. Stored token files: token.json, drive_token.json
    4. Service account fallback: GOOGLE_APPLICATION_CREDENTIALS
    """
    creds = None

    # Option 1: Direct env vars for client ID + client secret + refresh token
    client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("GOOGLE_DRIVE_REFRESH_TOKEN", "").strip()

    if client_id and client_secret and refresh_token:
        try:
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=SCOPES,
            )
        except Exception as e:
            logger.warning(f"Failed to create Drive credentials from env vars: {e}")

    # Option 2: GOOGLE_DRIVE_TOKEN_JSON env var
    if not creds:
        token_json_str = os.environ.get("GOOGLE_DRIVE_TOKEN_JSON", "").strip()
        if token_json_str:
            try:
                token_data = json.loads(token_json_str)
                creds = Credentials.from_authorized_user_info(token_data, SCOPES)
            except Exception as e:
                logger.warning(f"Failed to load Drive credentials from GOOGLE_DRIVE_TOKEN_JSON: {e}")

    # Option 3: Local token files
    if not creds:
        for token_filename in ("drive_token.json", "token.json", os.path.join(BASE_DIR, "drive_token.json"), os.path.join(BASE_DIR, "token.json")):
            if os.path.isfile(token_filename):
                try:
                    creds = Credentials.from_authorized_user_file(token_filename, SCOPES)
                    break
                except Exception as e:
                    logger.warning(f"Failed to load credentials from {token_filename}: {e}")

    # Option 4: Fallback to Service Account if present in GOOGLE_APPLICATION_CREDENTIALS
    if not creds:
        sa_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if sa_path and os.path.isfile(sa_path):
            try:
                from google.oauth2 import service_account
                creds = service_account.Credentials.from_service_account_file(sa_path, scopes=SCOPES)
            except Exception as e:
                logger.warning(f"Service account fallback for Drive failed: {e}")

    # Auto-refresh expired credentials
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            logger.error(f"Error refreshing Google Drive token: {e}")

    return creds


def is_drive_configured():
    """Return True if valid Google Drive credentials are configured."""
    try:
        creds = get_drive_credentials()
        return creds is not None and (creds.valid or creds.refresh_token is not None)
    except Exception:
        return False


def get_drive_service(creds=None):
    """Build and return an authorized Google Drive API v3 resource service."""
    if creds is None:
        creds = get_drive_credentials()
    if not creds:
        raise RuntimeError(
            "Google Drive credentials not configured. Please provide GOOGLE_DRIVE_REFRESH_TOKEN, "
            "GOOGLE_DRIVE_CLIENT_ID, GOOGLE_DRIVE_CLIENT_SECRET, or drive_token.json."
        )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def get_or_create_event_folder(event_id, drive_service=None):
    """
    Get or create the folder 'Event_<event_id>' under the root Drive folder.
    Returns the Google Drive folder ID.
    """
    service = drive_service or get_drive_service()
    root_id = get_root_folder_id()
    folder_name = f"Event_{event_id}"

    # Search for existing folder
    query = (
        f"mimeType = 'application/vnd.google-apps.folder' and "
        f"name = '{folder_name}' and "
        f"'{root_id}' in parents and "
        f"trashed = false"
    )

    try:
        results = service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            pageSize=1,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute()
        files = results.get("files", [])
        if files:
            return files[0]["id"]
    except Exception as e:
        logger.warning(f"Error checking for existing event folder {folder_name}: {e}")

    # Create new event folder under root
    folder_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [root_id],
    }
    try:
        created_folder = service.files().create(
            body=folder_metadata,
            fields="id, name",
            supportsAllDrives=True,
        ).execute()
        return created_folder.get("id")
    except Exception as e:
        logger.error(f"Error creating Google Drive event folder {folder_name}: {e}")
        raise


def upload_file_to_drive(file_data, filename, content_type, event_id, drive_service=None):
    """
    Upload a file directly to the event's Google Drive folder.
    file_data can be a Werkzeug FileStorage, bytes, or file-like object (io.BytesIO).
    Returns dict of file metadata:
      {
        'drive_file_id': ...,
        'drive_folder_id': ...,
        'drive_file_name': ...,
        'drive_web_view_url': ...,
        'file_size': ...,
        'content_type': ...,
      }
    """
    service = drive_service or get_drive_service()
    folder_id = get_or_create_event_folder(event_id, drive_service=service)

    # Prepare stream and determine size
    if isinstance(file_data, bytes):
        stream = io.BytesIO(file_data)
        file_size = len(file_data)
    elif hasattr(file_data, "read"):
        if hasattr(file_data, "seek"):
            file_data.seek(0, os.SEEK_END)
            file_size = file_data.tell()
            file_data.seek(0)
        else:
            raw = file_data.read()
            file_size = len(raw)
            stream = io.BytesIO(raw)
            file_data = stream
        stream = file_data
    else:
        raise ValueError("Invalid file_data type for Drive upload")

    media = MediaIoBaseUpload(stream, mimetype=content_type, resumable=True)
    file_metadata = {
        "name": filename,
        "parents": [folder_id],
    }

    try:
        uploaded_file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, mimeType, size, webViewLink, createdTime",
            supportsAllDrives=True,
        ).execute()

        actual_size = int(uploaded_file.get("size") or file_size)
        return {
            "drive_file_id": uploaded_file.get("id"),
            "drive_folder_id": folder_id,
            "drive_file_name": uploaded_file.get("name") or filename,
            "drive_web_view_url": uploaded_file.get("webViewLink", ""),
            "file_size": actual_size,
            "content_type": uploaded_file.get("mimeType") or content_type,
            "created_time": uploaded_file.get("createdTime", ""),
        }
    except Exception as e:
        logger.error(f"Error uploading file {filename} to Google Drive: {e}")
        raise


def download_drive_file(drive_file_id, drive_service=None):
    """
    Download a file from Google Drive by its file ID.
    Returns tuple: (bytes_content, content_type, filename)
    """
    service = drive_service or get_drive_service()
    try:
        # Fetch metadata
        meta = service.files().get(
            fileId=drive_file_id,
            fields="id, name, mimeType, size",
            supportsAllDrives=True,
        ).execute()
        filename = meta.get("name", "file")
        content_type = meta.get("mimeType", "application/octet-stream")

        # Download media content
        request = service.files().get_media(fileId=drive_file_id, supportsAllDrives=True)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()

        fh.seek(0)
        return fh.getvalue(), content_type, filename
    except Exception as e:
        logger.error(f"Error downloading file {drive_file_id} from Google Drive: {e}")
        raise


def delete_drive_file(drive_file_id, drive_service=None):
    """
    Permanently delete or trash a file in Google Drive by its file ID.
    Returns True on success.
    """
    service = drive_service or get_drive_service()
    try:
        service.files().delete(fileId=drive_file_id, supportsAllDrives=True).execute()
        return True
    except HttpError as he:
        if he.resp.status == 404:
            # Already deleted
            return True
        logger.error(f"HttpError deleting Google Drive file {drive_file_id}: {he}")
        return False
    except Exception as e:
        logger.error(f"Error deleting Google Drive file {drive_file_id}: {e}")
        return False
