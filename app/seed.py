"""
Seed the database with realistic demo data for the IT Demand Dashboard.

Run with:  python -m app.seed
"""

import random
from datetime import date, datetime, timedelta

from app.database import engine, Base, SessionLocal
from app.models import Client, Proceeding, Demand, SyncLog, NoticeFile, NoticeParsed

# ---------------------------------------------------------------------------
# Demo data constants
# ---------------------------------------------------------------------------

CLIENTS = [
    ("ABCDE1234F", "Rajesh Kumar & Associates", "MNOPQ5678R", "rajesh@example.com", "9876543210"),
    ("FGHIJ5678K", "Priya Sharma Enterprises", None, "priya@example.com", "9876543211"),
    ("KLMNO9012P", "Vikram Singh Trading Co.", "MNOPQ5678R", "vikram@example.com", "9876543212"),
    ("PQRST3456U", "Anita Desai Consulting", None, "anita@example.com", "9876543213"),
    ("UVWXY7890Z", "Suresh Patel Industries", "MNOPQ5678R", "suresh@example.com", "9876543214"),
    ("BCDEF2345G", "Meena Iyer & Co.", None, "meena@example.com", "9876543215"),
    ("GHIJK6789L", "Arjun Reddy Exports", "MNOPQ5678R", "arjun@example.com", "9876543216"),
    ("LMNOP0123Q", "Kavita Nair Legal Services", None, "kavita@example.com", "9876543217"),
    ("QRSTU4567V", "Deepak Gupta Manufacturing", "MNOPQ5678R", "deepak@example.com", "9876543218"),
    ("VWXYZ8901A", "Lakshmi Textiles Pvt Ltd", None, "lakshmi@example.com", "9876543219"),
    ("CDEFG3456H", "Rohit Malhotra HUF", None, "rohit@example.com", "9876543220"),
    ("HIJKL7890M", "Sneha Joshi Architects", "MNOPQ5678R", "sneha@example.com", "9876543221"),
]

AYS = ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]

NOTICE_TYPES = [
    ("scrutiny", "143(2)"),
    ("scrutiny", "143(1)"),
    ("demand", "156"),
    ("rectification", "154"),
    ("intimation", "143(1)(a)"),
    ("reassessment", "148"),
    ("penalty", "271(1)(c)"),
]

STATUSES = ["pending", "responded", "closed", "partially_complied"]
DEMAND_STATUSES = ["outstanding", "partially_paid", "disputed", "paid", "closed"]
SYNC_TYPES = ["excel_export", "notice_download", "ocr_parse"]

AO_NAMES = [
    "Sh. R.K. Verma", "Sh. A.K. Singh", "Smt. P. Mishra",
    "Sh. V.K. Sharma", "Smt. K. Reddy", "Sh. M. Patel",
]
JURISDICTIONS = [
    "Ward 1(1), Delhi", "Circle 2(1), Mumbai", "Ward 3(2), Bangalore",
    "Circle 1(1), Chennai", "Ward 4(1), Kolkata", "Circle 5(2), Hyderabad",
]


def seed():
    """Populate the database with demo data."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # Skip if data already exists
    if db.query(Client).count() > 0:
        print("Database already has data — skipping seed.")
        db.close()
        return

    print("Seeding database with demo data...")
    random.seed(42)  # Reproducible

    # 1. Create clients
    clients = []
    for pan, name, ca_pan, email, phone in CLIENTS:
        c = Client(
            pan=pan,
            name=name,
            ca_pan=ca_pan,
            email=email,
            phone=phone,
            last_synced=datetime.now() - timedelta(hours=random.randint(1, 168)),
        )
        db.add(c)
        clients.append(c)
    db.flush()

    # 2. Create proceedings
    today = date.today()
    proceedings = []
    for client in clients:
        # Each client gets 2-6 proceedings across random AYs
        num_proceedings = random.randint(2, 6)
        chosen_ays = random.sample(AYS, min(num_proceedings, len(AYS)))
        for ay in chosen_ays:
            notice_type, section = random.choice(NOTICE_TYPES)
            issue_date = today - timedelta(days=random.randint(10, 365))
            # Due date: 15-60 days after issue for pending; past for some overdue
            if random.random() < 0.2:
                # Make some overdue
                due_date = today - timedelta(days=random.randint(1, 30))
                status = "pending"
            elif random.random() < 0.3:
                # Due soon
                due_date = today + timedelta(days=random.randint(1, 14))
                status = "pending"
            else:
                due_date = today + timedelta(days=random.randint(15, 120))
                status = random.choice(STATUSES)

            p = Proceeding(
                pan=client.pan,
                assessment_year=ay,
                notice_type=notice_type,
                section=section,
                date_of_issue=issue_date,
                response_due_date=due_date,
                status=status,
                portal_ref_id=f"REF{random.randint(10000, 99999)}",
            )
            db.add(p)
            proceedings.append(p)
    db.flush()

    # 3. Create notice files and parsed data for some proceedings
    for p in proceedings:
        if random.random() < 0.7:  # 70% have downloaded notices
            nf = NoticeFile(
                proceeding_id=p.id,
                file_path=f"/data/{p.pan}/{p.assessment_year}/{p.notice_type}_{p.section}_{p.portal_ref_id}.pdf",
                file_hash=f"sha256_{random.randint(100000, 999999)}",
                download_status="success",
                downloaded_at=datetime.now() - timedelta(hours=random.randint(1, 72)),
            )
            db.add(nf)
            db.flush()

            if random.random() < 0.6:  # 60% of downloads are parsed
                np_ = NoticeParsed(
                    notice_file_id=nf.id,
                    raw_ocr_text=f"[OCR text for notice {p.portal_ref_id}]",
                    section=p.section,
                    assessment_year=p.assessment_year,
                    date_of_issue=p.date_of_issue,
                    response_due_date=p.response_due_date,
                    demand_amount=random.uniform(10000, 500000) if p.notice_type == "demand" else None,
                    ao_name=random.choice(AO_NAMES),
                    ao_jurisdiction=random.choice(JURISDICTIONS),
                    key_issues=random.choice([
                        "Mismatch in TDS credits claimed vs Form 26AS",
                        "Income from other sources not fully disclosed",
                        "Excess deduction claimed under Section 80C",
                        "Capital gains computation discrepancy",
                        "Unexplained cash deposits during demonetization",
                        "Disallowance of expenses under Section 40A(3)",
                    ]),
                    extraction_method=random.choice(["regex", "hybrid"]),
                    confidence_score=round(random.uniform(0.75, 0.98), 2),
                )
                db.add(np_)

    # 4. Create demands
    for client in clients:
        num_demands = random.randint(0, 3)
        for _ in range(num_demands):
            ay = random.choice(AYS)
            demand_amt = round(random.uniform(5000, 2500000), 2)
            interest_amt = round(demand_amt * random.uniform(0.05, 0.25), 2)
            d = Demand(
                pan=client.pan,
                assessment_year=ay,
                section=random.choice(["156", "143(1)", "154", "271(1)(c)"]),
                demand_amount=demand_amt,
                interest_amount=interest_amt,
                total_amount=round(demand_amt + interest_amt, 2),
                ao_name=random.choice(AO_NAMES),
                ao_jurisdiction=random.choice(JURISDICTIONS),
                status=random.choices(
                    DEMAND_STATUSES,
                    weights=[40, 15, 20, 15, 10],
                    k=1,
                )[0],
                last_checked=datetime.now() - timedelta(hours=random.randint(1, 48)),
            )
            db.add(d)

    # 5. Create sync logs
    for client in clients:
        for sync_type in SYNC_TYPES:
            started = datetime.now() - timedelta(hours=random.randint(2, 200))
            duration = timedelta(seconds=random.randint(5, 120))
            sl = SyncLog(
                pan=client.pan,
                sync_type=sync_type,
                records_found=random.randint(1, 15),
                records_new=random.randint(0, 5),
                records_changed=random.randint(0, 3),
                errors=random.choice([None, None, None, "Timeout on page load", "PDF download failed for 1 notice"]),
                status=random.choices(["success", "partial", "failed"], weights=[80, 15, 5], k=1)[0],
                started_at=started,
                completed_at=started + duration,
            )
            db.add(sl)

    db.commit()
    db.close()
    print(f"Seeded: {len(clients)} clients, {len(proceedings)} proceedings, demands, and sync logs.")


if __name__ == "__main__":
    seed()
