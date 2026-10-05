#!/usr/bin/env python3
"""One‑time Google Drive OAuth authorization script.

Run this locally (e.g. ``python authorize_google_drive.py``) with a Google account that
has access to the Drive folder defined by ``GOOGLE_DRIVE_ROOT_FOLDER_ID``.
The script performs the OAuth 2.0 Installed App flow, obtains a refresh token,
and writes the token JSON to ``drive_token.json`` in the project root.

The generated ``drive_token.json`` should be added to ``.gitignore`` and
its contents can be loaded by the application via the ``GOOGLE_DRIVE_TOKEN_JSON``
environment variable or by keeping the file on the server.
"""

import os
import json
import logging
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials

# The scopes are defined in google_drive_storage.py – we repeat the minimal set.
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive",
]

# Path to a client secret JSON downloaded from Google Cloud Console.
# The developer must place this file as ``client_secret.json`` in the project root.
CLIENT_SECRETS_FILE = os.path.join(os.path.dirname(__file__), "client_secret.json")

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "drive_token.json")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    if not os.path.isfile(CLIENT_SECRETS_FILE):
        logger.error(
            "Client secret file not found: %s. Download it from Google Cloud Console and save as client_secret.json.",
            CLIENT_SECRETS_FILE,
        )
        return

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
    creds: Credentials = flow.run_local_server(port=0)
    # Ensure we have a refresh token – the flow should provide it.
    if not creds.refresh_token:
        logger.error("OAuth flow completed but no refresh token was obtained.")
        return

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }

    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)
    logger.info("Refresh token saved to %s", TOKEN_FILE)
    logger.info(
        "Add the following line to your .env (or deployment secrets) to load the token without a file: \n"
        "GOOGLE_DRIVE_TOKEN_JSON='$(cat %s | tr -d '\n')'", TOKEN_FILE
    )

if __name__ == "__main__":
    main()
