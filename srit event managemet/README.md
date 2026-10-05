# SRIT HIVE – College Event Management Portal

A complete local Flask + SQLite event management website for Sri Ramakrishna Institute of Technology (SRIT) HIVE.

## Features

- Student and Faculty registration through Firebase Authentication
- Login/logout with role-based dashboards
- Student profile
- Browse and register for events
- My Events and event status tracking
- Faculty event creation
- Faculty event management
- Event updates/announcements
- Participant list and attendance marking
- QR-style check-in token support
- Automatic participation/winner certificate generation as PDF
- Download certificates
- Event reports and statistics
- Feedback and ratings
- Search/filter events
- Responsive SRIT HIVE-inspired UI
- Firebase Authentication for sign-in and password flows
- Firestore user profiles
- SQLite compatibility cache for preserved integer IDs and event relationships

## Run in VS Code

1. Extract the ZIP.
2. Open the extracted folder in VS Code.
3. Open Terminal.
4. Create a virtual environment:

   Windows:
   `python -m venv .venv`
   `.venv\Scripts\activate`

   macOS/Linux:
   `python3 -m venv .venv`
   `source .venv/bin/activate`

5. Install:
   `pip install -r requirements.txt`

6. Start:
   `python app.py`

7. Open:
   `http://127.0.0.1:5000`

## Accounts

Create accounts from the website using an official SRIT email address. Password reset and password changes are handled by Firebase Authentication.

## Certificate workflow

Faculty opens an event -> participants -> mark attendance -> choose participant type (Participation/Winner) -> Generate Certificate.

The PDF is created in `certificates/` and can be downloaded from the student's dashboard.

## Notes

The reference URL supplied in the request could not be fetched reliably from this environment because the Streamlit site returned a redirect-loop error. The implementation therefore follows the supplied handwritten workflow and SRIT HIVE/event-management structure, with a clean dashboard layout and the requested certificate automation.

For production deployment, replace the development secret key, add CSRF protection, configure Firebase credentials securely, and use a production WSGI server.
