from pathlib import Path

from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, case, text

from app.config import APP_TITLE
from app.database import engine, Base, get_db
from app.models import Client, Proceeding, Demand, SyncLog, NoticeFile, NoticeParsed
from app.api import router as api_router

from datetime import date, timedelta

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

Base.metadata.create_all(bind=engine)

app = FastAPI(title=APP_TITLE)

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

def _format_inr(value) -> str:
    """Format a number in Indian numbering system (e.g. 12,34,567)."""
    if value is None:
        return "—"
    value = float(value)
    if value < 0:
        return f"-{_format_inr(-value)}"
    s = f"{value:,.2f}"
    # Convert international format to Indian: 1,234,567.00 -> 12,34,567.00
    parts = s.split(".")
    integer_part = parts[0].replace(",", "")
    decimal_part = parts[1] if len(parts) > 1 else "00"
    if len(integer_part) <= 3:
        return f"₹{integer_part}.{decimal_part}"
    last3 = integer_part[-3:]
    remaining = integer_part[:-3]
    groups = []
    while remaining:
        groups.insert(0, remaining[-2:])
        remaining = remaining[:-2]
    formatted = ",".join(groups) + "," + last3
    return f"₹{formatted}.{decimal_part}"


templates.env.filters["inr"] = _format_inr
templates.env.globals["today"] = date.today


# ---------------------------------------------------------------------------
# Dashboard pages
# ---------------------------------------------------------------------------

@app.get("/")
def dashboard_home(request: Request, db: Session = Depends(get_db)):
    """Overview dashboard with key metrics and charts."""
    total_clients = db.query(func.count(Client.pan)).scalar() or 0
    total_proceedings = db.query(func.count(Proceeding.id)).scalar() or 0
    pending_proceedings = (
        db.query(func.count(Proceeding.id))
        .filter(Proceeding.status == "pending")
        .scalar()
        or 0
    )
    overdue_proceedings = (
        db.query(func.count(Proceeding.id))
        .filter(Proceeding.status == "pending", Proceeding.response_due_date < date.today())
        .scalar()
        or 0
    )
    due_within_7 = (
        db.query(func.count(Proceeding.id))
        .filter(
            Proceeding.status == "pending",
            Proceeding.response_due_date >= date.today(),
            Proceeding.response_due_date <= date.today() + timedelta(days=7),
        )
        .scalar()
        or 0
    )

    total_demand_amount = (
        db.query(func.sum(Demand.total_amount))
        .filter(Demand.status.in_(["outstanding", "disputed"]))
        .scalar()
        or 0
    )
    total_demands = (
        db.query(func.count(Demand.id))
        .filter(Demand.status.in_(["outstanding", "disputed"]))
        .scalar()
        or 0
    )

    # Status distribution for chart
    status_dist = (
        db.query(Proceeding.status, func.count(Proceeding.id))
        .group_by(Proceeding.status)
        .all()
    )
    status_labels = [r[0] for r in status_dist]
    status_counts = [r[1] for r in status_dist]

    # Demand by AY for chart
    demand_by_ay = (
        db.query(Demand.assessment_year, func.sum(Demand.total_amount))
        .filter(Demand.status.in_(["outstanding", "disputed"]))
        .group_by(Demand.assessment_year)
        .order_by(Demand.assessment_year)
        .all()
    )
    ay_labels = [r[0] for r in demand_by_ay]
    ay_amounts = [float(r[1] or 0) for r in demand_by_ay]

    # Proceedings by notice type for chart
    type_dist = (
        db.query(Proceeding.notice_type, func.count(Proceeding.id))
        .group_by(Proceeding.notice_type)
        .all()
    )
    type_labels = [r[0] for r in type_dist]
    type_counts = [r[1] for r in type_dist]

    # Recent activity — last 10 sync logs
    recent_syncs = db.query(SyncLog).order_by(SyncLog.completed_at.desc()).limit(10).all()

    # Upcoming deadlines — next 5
    upcoming = (
        db.query(Proceeding)
        .filter(Proceeding.status == "pending", Proceeding.response_due_date >= date.today())
        .order_by(Proceeding.response_due_date.asc())
        .limit(5)
        .all()
    )

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "title": APP_TITLE,
            "total_clients": total_clients,
            "total_proceedings": total_proceedings,
            "pending_proceedings": pending_proceedings,
            "overdue_proceedings": overdue_proceedings,
            "due_within_7": due_within_7,
            "total_demand_amount": total_demand_amount,
            "total_demands": total_demands,
            "status_labels": status_labels,
            "status_counts": status_counts,
            "ay_labels": ay_labels,
            "ay_amounts": ay_amounts,
            "type_labels": type_labels,
            "type_counts": type_counts,
            "recent_syncs": recent_syncs,
            "upcoming": upcoming,
        },
    )


@app.get("/proceedings")
def proceedings_page(
    request: Request,
    db: Session = Depends(get_db),
    status: str = "",
    ay: str = "",
    pan: str = "",
    sort: str = "due_date",
):
    """All proceedings — filterable, sortable."""
    q = db.query(Proceeding).join(Client)

    if status:
        q = q.filter(Proceeding.status == status)
    if ay:
        q = q.filter(Proceeding.assessment_year == ay)
    if pan:
        q = q.filter(Proceeding.pan.ilike(f"%{pan}%"))

    sort_map = {
        "due_date": Proceeding.response_due_date.asc().nullslast(),
        "ay_desc": Proceeding.assessment_year.desc(),
        "ay_asc": Proceeding.assessment_year.asc(),
        "date_issued": Proceeding.date_of_issue.desc().nullslast(),
        "status": Proceeding.status.asc(),
    }
    q = q.order_by(sort_map.get(sort, Proceeding.response_due_date.asc().nullslast()))

    proceedings = q.all()

    # Get unique AYs and statuses for filter dropdowns
    all_ays = [r[0] for r in db.query(Proceeding.assessment_year).distinct().order_by(Proceeding.assessment_year.desc()).all()]
    all_statuses = [r[0] for r in db.query(Proceeding.status).distinct().all()]

    return templates.TemplateResponse(
        "proceedings.html",
        {
            "request": request,
            "title": "All Proceedings",
            "proceedings": proceedings,
            "all_ays": all_ays,
            "all_statuses": all_statuses,
            "current_status": status,
            "current_ay": ay,
            "current_pan": pan,
            "current_sort": sort,
        },
    )


@app.get("/deadlines")
def deadlines_page(request: Request, db: Session = Depends(get_db)):
    """Upcoming deadlines sorted by due date."""
    overdue = (
        db.query(Proceeding)
        .join(Client)
        .filter(Proceeding.status == "pending", Proceeding.response_due_date < date.today())
        .order_by(Proceeding.response_due_date.asc())
        .all()
    )
    upcoming = (
        db.query(Proceeding)
        .join(Client)
        .filter(
            Proceeding.status == "pending",
            Proceeding.response_due_date >= date.today(),
        )
        .order_by(Proceeding.response_due_date.asc())
        .all()
    )

    return templates.TemplateResponse(
        "deadlines.html",
        {
            "request": request,
            "title": "Upcoming Deadlines",
            "overdue": overdue,
            "upcoming": upcoming,
        },
    )


@app.get("/demands")
def demands_page(
    request: Request,
    db: Session = Depends(get_db),
    status: str = "",
    ay: str = "",
    sort: str = "amount_desc",
):
    """Outstanding demands sorted by total amount."""
    q = db.query(Demand).join(Client)

    if status:
        q = q.filter(Demand.status == status)
    if ay:
        q = q.filter(Demand.assessment_year == ay)

    sort_map = {
        "amount_desc": Demand.total_amount.desc().nullslast(),
        "amount_asc": Demand.total_amount.asc().nullslast(),
        "ay_desc": Demand.assessment_year.desc(),
    }
    q = q.order_by(sort_map.get(sort, Demand.total_amount.desc().nullslast()))

    demands = q.all()

    total = sum(float(d.total_amount or 0) for d in demands)
    all_ays = [r[0] for r in db.query(Demand.assessment_year).distinct().order_by(Demand.assessment_year.desc()).all()]
    all_statuses = [r[0] for r in db.query(Demand.status).distinct().all()]

    return templates.TemplateResponse(
        "demands.html",
        {
            "request": request,
            "title": "Outstanding Demands",
            "demands": demands,
            "total": total,
            "all_ays": all_ays,
            "all_statuses": all_statuses,
            "current_status": status,
            "current_ay": ay,
            "current_sort": sort,
        },
    )


@app.get("/clients")
def clients_page(request: Request, db: Session = Depends(get_db)):
    """Client list grouped with summary stats."""
    clients = db.query(Client).order_by(Client.name).all()
    return templates.TemplateResponse(
        "clients.html",
        {"request": request, "title": "Clients", "clients": clients},
    )


@app.get("/clients/{pan}")
def client_detail(pan: str, request: Request, db: Session = Depends(get_db)):
    """Detail view for a single client."""
    client = db.query(Client).filter(Client.pan == pan).first()
    if not client:
        return templates.TemplateResponse(
            "404.html", {"request": request, "title": "Not Found"}, status_code=404
        )
    proceedings = (
        db.query(Proceeding)
        .filter(Proceeding.pan == pan)
        .order_by(Proceeding.assessment_year.desc(), Proceeding.response_due_date.asc().nullslast())
        .all()
    )
    demands = (
        db.query(Demand)
        .filter(Demand.pan == pan)
        .order_by(Demand.total_amount.desc().nullslast())
        .all()
    )
    syncs = (
        db.query(SyncLog)
        .filter(SyncLog.pan == pan)
        .order_by(SyncLog.completed_at.desc())
        .limit(20)
        .all()
    )
    return templates.TemplateResponse(
        "client_detail.html",
        {
            "request": request,
            "title": f"{client.name} ({pan})",
            "client": client,
            "proceedings": proceedings,
            "demands": demands,
            "syncs": syncs,
        },
    )


@app.get("/sync-log")
def sync_log_page(request: Request, db: Session = Depends(get_db)):
    """Sync log — history of data synchronization runs."""
    logs = db.query(SyncLog).order_by(SyncLog.completed_at.desc()).all()
    return templates.TemplateResponse(
        "sync_log.html",
        {"request": request, "title": "Sync Log", "logs": logs},
    )


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------
app.include_router(api_router, prefix="/api")
