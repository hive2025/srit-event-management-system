# SOP & Report Generator Redesign

- [x] 1. Rewrite `generate_sop_pdf_report()` in `report_generator.py` — official SRIT template (items 1–15, header image, dynamic logos, dynamic row heights, signature KeepTogether, repeating header)
- [x] 2. Update `generate_report()` in `app.py` — read new SOP form fields, pass `sop_template`
- [x] 3. Add SOP template Firestore helpers and REST endpoints in `app.py` (`sop_templates/{event_id}`, asset uploads, reset)
- [x] 4. Update `manage_event.html` — SOP Template Customizer card (letterhead replace, logo drag/hide/reorder/resize, live save, reset)
- [x] 5. Automated verification: `python -m py_compile` and full 7-step test suite (`test_sop_suite.py`) — ALL PASS.
