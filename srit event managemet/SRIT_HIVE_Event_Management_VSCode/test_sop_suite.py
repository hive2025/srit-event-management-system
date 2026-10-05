import os
import sys
import tempfile
import json
import shutil
from datetime import datetime

# Setup paths
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from report_generator import (
    generate_sop_pdf_report,
    DEFAULT_LOGOS,
    DEFAULT_LETTERHEAD,
    DEFAULT_ASSETS
)
from PIL import Image as PILImage

try:
    import pypdf
    HAVE_PYPDF = True
except ImportError:
    HAVE_PYPDF = False

def run_tests():
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_sop_outputs")
    os.makedirs(output_dir, exist_ok=True)
    print("=== SOP GENERATION TEST SUITE ===")
    print(f"Output directory: {output_dir}")

    # Standard test event data
    base_event = {
        "id": "test_event_1",
        "title": "Industrial Automation & AI Workshop",
        "program_name": "Workshop",
        "event_date": "2026-09-24",
        "start_time": "09:30 AM",
        "end_time": "04:30 PM",
        "venue": "Auditorium 1",
        "capacity": 120,
        "organizing_details": "HIVE Innovation Hub",
        "estimated_expenditure": "15000",
        "objectives": "To impart hands-on skills in Industrial IoT and PLC programming for engineering students.",
        "benefits": "Students gain direct industry-standard automation exposure and certification.",
        "spc_mapping": "Aligns with NAAC Criteria 3 and boosts college-industry linkage.",
        "speaker_name": "Dr. K. S. Ramanathan",
        "speaker_designation": "Chief Technical Officer",
        "speaker_organization": "Bosch Rexroth India",
        "speaker_contact": "+91 9876543210",
        "speaker_email": "ramanathan@bosch.com",
    }

    base_inputs = {
        "dept_assoc_club": "HIVE, Department of Electrical & Electronics Engineering",
        "nature_of_programme": "Two-Day Hands-on Workshop",
        "coordinator": "Dr. R. Selvakumar & Ms. N. Sanjana",
        "participants": "UG & PG Students (EEE, ECE, CSE)",
        "total_audience": "120 Internal + 25 External",
        "resource_person_name": "Dr. K. S. Ramanathan",
        "resource_person_designation": "Chief Technical Officer",
        "resource_person_org": "Bosch Rexroth India",
        "resource_person_address": "Bangalore Campus, Electronic City",
        "resource_person_phone": "+91 9876543210",
        "resource_person_email": "ramanathan@bosch.com",
        "estimated_expenditure": "Rs 15,000",
        "fund_source": "HOD Discretionary Fund + Registration",
        "objective": "To train students in modern PLC architectures, SCADA systems, and edge IoT devices.",
        "student_contribution": "Provides students with industry-relevant skills and practical project experience.",
        "institution_contribution": "Enhances college reputation, NAAC Score, and industry placement rates.",
        "sop_date": "24.09.2026",
    }

    # ─────────────────────────────────────────────────────────────
    # TEST 1: Normal SOP (Default Letterhead + Default Logos)
    # ─────────────────────────────────────────────────────────────
    print("\n--- TEST 1: Normal SOP Generation (Default) ---")
    pdf_1 = os.path.join(output_dir, "1_normal_sop.pdf")
    generate_sop_pdf_report(
        pdf_path=pdf_1,
        event_data=base_event,
        user_inputs=base_inputs,
        attendance_stats={},
        feedback_stats={},
        transcript_text="",
        photo_paths=[],
        sop_template=None  # defaults
    )
    assert os.path.isfile(pdf_1) and os.path.getsize(pdf_1) > 0, "Normal SOP PDF generation failed!"
    print(f"[PASS] Normal SOP generated successfully: {pdf_1} ({os.path.getsize(pdf_1)} bytes)")
    if HAVE_PYPDF:
        reader = pypdf.PdfReader(pdf_1)
        print(f"       Page count: {len(reader.pages)}")

    # ─────────────────────────────────────────────────────────────
    # TEST 2: Very Long Content in Rows 13–15 (Wrapping Test)
    # ─────────────────────────────────────────────────────────────
    print("\n--- TEST 2: Long Content in Rows 13-15 ---")
    long_inputs = dict(base_inputs)
    long_inputs["objective"] = (
        "This comprehensive workshop aims to bridge the gap between academic curricula and industrial automation requirements. "
        "Participants will explore PLC architecture, ladder logic programming, HMI integration, SCADA dashboards, and real-time sensor interfacing. "
        "The curriculum covers Siemens S7-1200, Allen Bradley MicroLogix, and Beckhoff EtherCAT systems with hands-on labs. " * 3
    )
    long_inputs["student_contribution"] = (
        "Students will develop core competencies in hardware-in-the-loop simulation, industrial networking protocols (Modbus, Profinet, MQTT), "
        "and automated fault diagnosis. These practical competencies directly enhance employability in automotive, manufacturing, and process industries. "
        "Each participant will complete a mini-project certified by industry experts. " * 3
    )
    long_inputs["institution_contribution"] = (
        "Organizing high-impact technical programmes reinforces Sri Ramakrishna Institute of Technology's standing as a leading autonomous institution. "
        "It supports NBA accreditation Criterion 4 and NAAC Criterion 3, fosters industry consultancy linkages, and enhances institutional brand recognition. "
        "Furthermore, outcomes will be published as student project whitepapers. " * 4
    )
    pdf_2 = os.path.join(output_dir, "2_long_content_sop.pdf")
    generate_sop_pdf_report(
        pdf_path=pdf_2,
        event_data=base_event,
        user_inputs=long_inputs,
        attendance_stats={},
        feedback_stats={},
        transcript_text="",
        photo_paths=[],
        sop_template=None
    )
    assert os.path.isfile(pdf_2) and os.path.getsize(pdf_2) > 0, "Long content SOP generation failed!"
    print(f"[PASS] Long Content SOP generated successfully: {pdf_2} ({os.path.getsize(pdf_2)} bytes)")
    if HAVE_PYPDF:
        reader = pypdf.PdfReader(pdf_2)
        print(f"       Page count: {len(reader.pages)}")

    # ─────────────────────────────────────────────────────────────
    # TEST 3: Multi-page SOP with Header Repeating
    # ─────────────────────────────────────────────────────────────
    print("\n--- TEST 3: Multi-Page SOP (Spanning 2+ pages, Header Repeating) ---")
    multipage_inputs = dict(long_inputs)
    multipage_inputs["objective"] = long_inputs["objective"] * 2
    multipage_inputs["student_contribution"] = long_inputs["student_contribution"] * 2
    multipage_inputs["institution_contribution"] = long_inputs["institution_contribution"] * 2
    pdf_3 = os.path.join(output_dir, "3_multipage_sop.pdf")
    generate_sop_pdf_report(
        pdf_path=pdf_3,
        event_data=base_event,
        user_inputs=multipage_inputs,
        attendance_stats={},
        feedback_stats={},
        transcript_text="",
        photo_paths=[],
        sop_template=None
    )
    assert os.path.isfile(pdf_3) and os.path.getsize(pdf_3) > 0, "Multi-page SOP generation failed!"
    print(f"[PASS] Multi-page SOP generated: {pdf_3} ({os.path.getsize(pdf_3)} bytes)")
    if HAVE_PYPDF:
        reader = pypdf.PdfReader(pdf_3)
        pages_count = len(reader.pages)
        print(f"       Page count: {pages_count}")
        assert pages_count >= 2, f"Expected 2 or more pages, got {pages_count}"
        p2_text = reader.pages[1].extract_text()
        assert "Description" in p2_text or "Details" in p2_text, "Repeated header text expected on Page 2!"
        print("       [PASS] Repeated header verified on Page 2.")

    # ─────────────────────────────────────────────────────────────
    # TEST 4: Optional Custom Rows (Field 16+)
    # ─────────────────────────────────────────────────────────────
    print("\n--- TEST 4: Optional Custom Rows (Field 16+) ---")
    custom_rows_inputs = dict(base_inputs)
    custom_rows_inputs["custom_rows"] = [
        {"num": 16, "label": "Industry Partner / Sponsor", "value": "Bosch Rexroth Pvt Ltd (Sponsored hardware kits)", "is_question": False},
        {"num": 17, "label": "Safety and Compliance Plan", "value": "All participants equipped with ESD wristbands and certified lab PPE.", "is_question": True},
    ]
    pdf_4 = os.path.join(output_dir, "4_custom_rows_sop.pdf")
    generate_sop_pdf_report(
        pdf_path=pdf_4,
        event_data=base_event,
        user_inputs=custom_rows_inputs,
        attendance_stats={},
        feedback_stats={},
        transcript_text="",
        photo_paths=[],
    )
    assert os.path.isfile(pdf_4) and os.path.getsize(pdf_4) > 0
    if HAVE_PYPDF:
        p4_text = pypdf.PdfReader(pdf_4).pages[0].extract_text()
        assert "Industry Partner" in p4_text or "16" in p4_text
    print(f"[PASS] Custom Rows SOP generated: {pdf_4}")

    # ─────────────────────────────────────────────────────────────
    # TEST 5: Editable Event Content Verification
    # ─────────────────────────────────────────────────────────────
    print("\n--- TEST 5: Editable Event Content Verification ---")
    edited_inputs = dict(base_inputs)
    edited_inputs["dept_assoc_club"] = "Department of Artificial Intelligence & Data Science"
    edited_inputs["nature_of_programme"] = "Faculty Development Programme"
    edited_inputs["coordinator"] = "Dr. M. Vignesh & Dr. P. Anand"
    edited_inputs["estimated_expenditure"] = "Rs 45,000"
    edited_inputs["fund_source"] = "AICTE Training and Learning (ATAL) Academy Grant"
    pdf_5 = os.path.join(output_dir, "5_edited_event_sop.pdf")
    generate_sop_pdf_report(
        pdf_path=pdf_5,
        event_data=base_event,
        user_inputs=edited_inputs,
        attendance_stats={},
        feedback_stats={},
        transcript_text="",
        photo_paths=[],
    )
    assert os.path.isfile(pdf_5) and os.path.getsize(pdf_5) > 0
    if HAVE_PYPDF:
        p5_text = pypdf.PdfReader(pdf_5).pages[0].extract_text()
        assert "Artificial Intelligence" in p5_text and "ATAL" in p5_text
    print(f"[PASS] Edited event content SOP generated: {pdf_5}")

    # ─────────────────────────────────────────────────────────────
    # TEST 6: Selected Subset of Initiative Logos (e.g. HIVE & E-Cell only)
    # ─────────────────────────────────────────────────────────────
    print("\n--- TEST 6: Selected Subset of Logos (Only HIVE & E-Cell) ---")
    pdf_6 = os.path.join(output_dir, "6_subset_logos_sop.pdf")
    generate_sop_pdf_report(
        pdf_path=pdf_6,
        event_data=base_event,
        user_inputs=base_inputs,
        attendance_stats={},
        feedback_stats={},
        transcript_text="",
        photo_paths=[],
        sop_template={"included_logos": ["hive", "ecell"]},
    )
    assert os.path.isfile(pdf_6) and os.path.getsize(pdf_6) > 0, "Subset logo SOP generation failed!"
    print(f"[PASS] Subset (HIVE & E-Cell) SOP generated successfully: {pdf_6} ({os.path.getsize(pdf_6)} bytes)")

    # ─────────────────────────────────────────────────────────────
    # TEST 7: Zero Initiative Logos (Unselected/Omitted)
    # ─────────────────────────────────────────────────────────────
    print("\n--- TEST 7: Zero Initiative Logos Selected (Clean Omission) ---")
    pdf_7 = os.path.join(output_dir, "7_zero_initiative_logos_sop.pdf")
    generate_sop_pdf_report(
        pdf_path=pdf_7,
        event_data=base_event,
        user_inputs=base_inputs,
        attendance_stats={},
        feedback_stats={},
        transcript_text="",
        photo_paths=[],
        sop_template={"included_logos": []},
    )
    assert os.path.isfile(pdf_7) and os.path.getsize(pdf_7) > 0, "Zero initiative logos SOP generation failed!"
    print(f"[PASS] Zero initiative logos SOP generated successfully: {pdf_7} ({os.path.getsize(pdf_7)} bytes)")

    # ─────────────────────────────────────────────────────────────
    # TEST 8: Custom Upload with Selected Initiative Logos
    # ─────────────────────────────────────────────────────────────
    print("\n--- TEST 8: Custom Replacement with Selected Initiative Logos ---")
    custom_logos_dir = os.path.join(output_dir, "custom_test_logos")
    os.makedirs(custom_logos_dir, exist_ok=True)

    custom_logos_map = {}
    logo_specs = {
        "hive": (100, 60, (220, 160, 20)),
        "idealab": (110, 60, (140, 40, 180)),
    }
    for slot_k, (w, h, rgb) in logo_specs.items():
        img_path = os.path.join(custom_logos_dir, f"custom_{slot_k}.png")
        img = PILImage.new("RGB", (w, h), color=rgb)
        img.save(img_path)
        custom_logos_map[slot_k] = img_path

    pdf_8 = os.path.join(output_dir, "8_custom_and_subset_logos_sop.pdf")
    generate_sop_pdf_report(
        pdf_path=pdf_8,
        event_data=base_event,
        user_inputs=base_inputs,
        attendance_stats={},
        feedback_stats={},
        transcript_text="",
        photo_paths=[],
        sop_template={
            "included_logos": ["hive", "idealab", "sish"],
            "custom_logos": custom_logos_map,
        },
    )
    assert os.path.isfile(pdf_8) and os.path.getsize(pdf_8) > 0, "Custom & subset logo SOP generation failed!"
    print(f"[PASS] Custom & subset logos SOP generated successfully: {pdf_8} ({os.path.getsize(pdf_8)} bytes)")

    # ─────────────────────────────────────────────────────────────
    # TEST 9: Only Institutional Logos (SNR + SRIT) — No Initiative Logos
    # ─────────────────────────────────────────────────────────────
    print("\n--- TEST 9: Only Institutional Logos (SNR & SRIT only) ---")
    pdf_9 = os.path.join(output_dir, "9_only_snr_srit_sop.pdf")
    generate_sop_pdf_report(
        pdf_path=pdf_9,
        event_data=base_event,
        user_inputs=base_inputs,
        attendance_stats={},
        feedback_stats={},
        transcript_text="",
        photo_paths=[],
        sop_template={"included_logos": ["snr", "srit"]},
    )
    assert os.path.isfile(pdf_9) and os.path.getsize(pdf_9) > 0, "Only SNR/SRIT SOP generation failed!"
    print(f"[PASS] Only SNR/SRIT logos SOP generated successfully: {pdf_9} ({os.path.getsize(pdf_9)} bytes)")

    # ─────────────────────────────────────────────────────────────
    # TEST 10: Only SRIT (Top Right) + HIVE & E-Cell (SNR Omitted)
    # ─────────────────────────────────────────────────────────────
    print("\n--- TEST 10: Only SRIT Top Logo (SNR Omitted) + HIVE & E-Cell ---")
    pdf_10 = os.path.join(output_dir, "10_only_srit_and_initiatives_sop.pdf")
    generate_sop_pdf_report(
        pdf_path=pdf_10,
        event_data=base_event,
        user_inputs=base_inputs,
        attendance_stats={},
        feedback_stats={},
        transcript_text="",
        photo_paths=[],
        sop_template={"included_logos": ["srit", "hive", "ecell"]},
    )
    assert os.path.isfile(pdf_10) and os.path.getsize(pdf_10) > 0, "Only SRIT SOP generation failed!"
    print(f"[PASS] Only SRIT + initiative logos SOP generated successfully: {pdf_10} ({os.path.getsize(pdf_10)} bytes)")

    # ─────────────────────────────────────────────────────────────
    # TEST 11: Only SNR (Top Left) + Idea Lab & IIC (SRIT Omitted)
    # ─────────────────────────────────────────────────────────────
    print("\n--- TEST 11: Only SNR Top Logo (SRIT Omitted) + Idea Lab & IIC ---")
    pdf_11 = os.path.join(output_dir, "11_only_snr_and_initiatives_sop.pdf")
    generate_sop_pdf_report(
        pdf_path=pdf_11,
        event_data=base_event,
        user_inputs=base_inputs,
        attendance_stats={},
        feedback_stats={},
        transcript_text="",
        photo_paths=[],
        sop_template={"included_logos": ["snr", "idealab", "iic"]},
    )
    assert os.path.isfile(pdf_11) and os.path.getsize(pdf_11) > 0, "Only SNR SOP generation failed!"
    print(f"[PASS] Only SNR + initiative logos SOP generated successfully: {pdf_11} ({os.path.getsize(pdf_11)} bytes)")

    # ─────────────────────────────────────────────────────────────
    # TEST 12: Zero Logos At All (All 7 Omitted - Pure Text Letterhead)
    # ─────────────────────────────────────────────────────────────
    print("\n--- TEST 12: Zero Logos At All (All 7 Omitted) ---")
    pdf_12 = os.path.join(output_dir, "12_all_7_logos_omitted_sop.pdf")
    generate_sop_pdf_report(
        pdf_path=pdf_12,
        event_data=base_event,
        user_inputs=base_inputs,
        attendance_stats={},
        feedback_stats={},
        transcript_text="",
        photo_paths=[],
        sop_template={"included_logos": []},
    )
    assert os.path.isfile(pdf_12) and os.path.getsize(pdf_12) > 0, "Zero logos SOP generation failed!"
    print(f"[PASS] Zero logos SOP generated successfully: {pdf_12} ({os.path.getsize(pdf_12)} bytes)")

    # ─────────────────────────────────────────────────────────────
    # TEST 13: Custom Top Institutional Logos (Custom SNR & SRIT) + Custom HIVE
    # ─────────────────────────────────────────────────────────────
    print("\n--- TEST 13: Custom SNR, Custom SRIT & Custom HIVE ---")
    custom_top_map = {}
    top_specs = {
        "snr": (90, 90, (10, 80, 160)),
        "srit": (100, 100, (160, 20, 20)),
        "hive": (120, 70, (20, 160, 80)),
    }
    for slot_k, (w, h, rgb) in top_specs.items():
        img_path = os.path.join(custom_logos_dir, f"custom_{slot_k}.png")
        img = PILImage.new("RGB", (w, h), color=rgb)
        img.save(img_path)
        custom_top_map[slot_k] = img_path

    pdf_13 = os.path.join(output_dir, "13_custom_top_and_initiative_logos_sop.pdf")
    generate_sop_pdf_report(
        pdf_path=pdf_13,
        event_data=base_event,
        user_inputs=base_inputs,
        attendance_stats={},
        feedback_stats={},
        transcript_text="",
        photo_paths=[],
        sop_template={
            "included_logos": ["snr", "srit", "hive"],
            "custom_logos": custom_top_map,
        },
    )
    assert os.path.isfile(pdf_13) and os.path.getsize(pdf_13) > 0, "Custom top logos SOP generation failed!"
    print(f"[PASS] Custom top and initiative logos SOP generated successfully: {pdf_13} ({os.path.getsize(pdf_13)} bytes)")

    print("\n==========================================")
    print("ALL 13 PDF GENERATION TESTS PASSED SUCCESSFULLY!")
    print("==========================================")

if __name__ == "__main__":
    run_tests()

