"""
Google Drive OAuth 2.0 Setup Utility
-----------------------------------
Run this script once to authorize Google Drive API access and generate OAuth 2.0 credentials.

Usage:
    python setup_google_drive_oauth.py [--credentials-file credentials.json]

Outputs:
    - Creates `drive_token.json` locally.
    - Prints environment variable entries (GOOGLE_DRIVE_REFRESH_TOKEN, GOOGLE_DRIVE_CLIENT_ID, etc.)
      that can be added directly to your `.env` file for production deployment.
"""

import os
import sys
import json
import argparse

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]

DEFAULT_ROOT_FOLDER_ID = "1nTcP02DIkSA0Ail3tqxvYQeneZn5wttZ"


def main():
    parser = argparse.ArgumentParser(description="Authenticate Google Drive OAuth 2.0 and save token.")
    parser.add_argument(
        "--credentials-file",
        "-c",
        default="client_secret.json",
        help="Path to the OAuth 2.0 Client Secret JSON downloaded from Google Cloud Console (default: client_secret.json or credentials.json)",
    )
    parser.add_argument(
        "--output-token",
        "-o",
        default="drive_token.json",
        help="Destination path for generated token JSON (default: drive_token.json)",
    )
    args = parser.parse_args()

    cred_file = args.credentials_file
    if not os.path.isfile(cred_file):
        for candidate in ("credentials.json", "client_secret.json", "oauth_credentials.json"):
            if os.path.isfile(candidate):
                cred_file = candidate
                break

    if not os.path.isfile(cred_file):
        print("=" * 70)
        print("GOOGLE DRIVE OAUTH SETUP")
        print("=" * 70)
        print(f"Error: Client secret file '{cred_file}' not found.")
        print("\nTo set up Google Drive OAuth:")
        print("1. Go to Google Cloud Console (https://console.cloud.google.com/)")
        print("2. Enable the Google Drive API for your project.")
        print("3. Go to APIs & Services > Credentials > Create Credentials > OAuth client ID.")
        print("4. Select Application Type: 'Desktop App'.")
        print("5. Download the JSON file and save it as 'client_secret.json' in this directory.")
        print("6. Re-run: python setup_google_drive_oauth.py")
        print("=" * 70)
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Error: google-auth-oauthlib is not installed. Run: pip install google-auth-oauthlib")
        sys.exit(1)

    print(f"Reading OAuth 2.0 client secrets from '{cred_file}'...")
    flow = InstalledAppFlow.from_client_secrets_file(cred_file, SCOPES)
    creds = flow.run_local_server(port=0)

    # Save credentials to output token JSON file
    token_data = json.loads(creds.to_json())
    output_path = os.path.abspath(args.output_token)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)

    print("\n" + "=" * 70)
    print("SUCCESS: Google Drive OAuth credentials obtained!")
    print("=" * 70)
    print(f"Token saved to: {output_path}")
    print("\nAdd the following lines to your `.env` file for secure deployment:")
    print("-" * 70)
    print(f"GOOGLE_DRIVE_ROOT_FOLDER_ID={DEFAULT_ROOT_FOLDER_ID}")
    print(f"GOOGLE_DRIVE_CLIENT_ID={token_data.get('client_id', '')}")
    print(f"GOOGLE_DRIVE_CLIENT_SECRET={token_data.get('client_secret', '')}")
    print(f"GOOGLE_DRIVE_REFRESH_TOKEN={token_data.get('refresh_token', '')}")
    print("-" * 70)
    print("\nGoogle Drive API is now ready for SRIT Event Management Photographs & Media storage.")


if __name__ == "__main__":
    main()
