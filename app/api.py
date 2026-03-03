"""
REST API routes for programmatic access and AJAX calls.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import Client, Proceeding, Demand, SyncLog

router = APIRouter(tags=["api"])


@router.get("/stats")
def api_stats(db: Session = Depends(get_db)):
    """Summary statistics for the dashboard header."""
    total_clients = db.query(func.count(Client.pan)).scalar() or 0
    pending = (
        db.query(func.count(Proceeding.id))
        .filter(Proceeding.status == "pending")
        .scalar()
        or 0
    )
    overdue = (
        db.query(func.count(Proceeding.id))
        .filter(Proceeding.status == "pending", Proceeding.response_due_date < date.today())
        .scalar()
        or 0
    )
    total_demand = float(
        db.query(func.sum(Demand.total_amount))
        .filter(Demand.status.in_(["outstanding", "disputed"]))
        .scalar()
        or 0
    )
    return {
        "total_clients": total_clients,
        "pending_proceedings": pending,
        "overdue_proceedings": overdue,
        "total_outstanding_demand": total_demand,
    }


@router.get("/proceedings")
def api_proceedings(
    db: Session = Depends(get_db),
    pan: str = "",
    ay: str = "",
    status: str = "",
    limit: int = Query(default=100, le=500),
    offset: int = 0,
):
    q = db.query(Proceeding)
    if pan:
        q = q.filter(Proceeding.pan == pan)
    if ay:
        q = q.filter(Proceeding.assessment_year == ay)
    if status:
        q = q.filter(Proceeding.status == status)
    total = q.count()
    rows = (
        q.order_by(Proceeding.response_due_date.asc().nullslast())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": p.id,
                "pan": p.pan,
                "client_name": p.client.name,
                "assessment_year": p.assessment_year,
                "notice_type": p.notice_type,
                "section": p.section,
                "date_of_issue": str(p.date_of_issue) if p.date_of_issue else None,
                "response_due_date": str(p.response_due_date) if p.response_due_date else None,
                "status": p.status,
                "days_until_due": p.days_until_due,
                "urgency": p.urgency,
            }
            for p in rows
        ],
    }


@router.get("/demands")
def api_demands(
    db: Session = Depends(get_db),
    pan: str = "",
    ay: str = "",
    status: str = "",
    limit: int = Query(default=100, le=500),
    offset: int = 0,
):
    q = db.query(Demand)
    if pan:
        q = q.filter(Demand.pan == pan)
    if ay:
        q = q.filter(Demand.assessment_year == ay)
    if status:
        q = q.filter(Demand.status == status)
    total = q.count()
    rows = (
        q.order_by(Demand.total_amount.desc().nullslast())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": d.id,
                "pan": d.pan,
                "client_name": d.client.name,
                "assessment_year": d.assessment_year,
                "section": d.section,
                "demand_amount": float(d.demand_amount or 0),
                "interest_amount": float(d.interest_amount or 0),
                "total_amount": float(d.total_amount or 0),
                "ao_name": d.ao_name,
                "ao_jurisdiction": d.ao_jurisdiction,
                "status": d.status,
                "urgency_level": d.urgency_level,
            }
            for d in rows
        ],
    }


@router.get("/clients")
def api_clients(db: Session = Depends(get_db)):
    clients = db.query(Client).order_by(Client.name).all()
    return [
        {
            "pan": c.pan,
            "name": c.name,
            "last_synced": str(c.last_synced) if c.last_synced else None,
            "open_proceedings": c.open_proceedings_count,
            "total_demand": c.total_demand_amount,
        }
        for c in clients
    ]


@router.get("/deadlines")
def api_deadlines(db: Session = Depends(get_db), days: int = Query(default=30, le=365)):
    """Upcoming deadlines within the next N days."""
    cutoff = date.today() + timedelta(days=days)
    rows = (
        db.query(Proceeding)
        .filter(
            Proceeding.status == "pending",
            Proceeding.response_due_date <= cutoff,
        )
        .order_by(Proceeding.response_due_date.asc())
        .all()
    )
    return [
        {
            "id": p.id,
            "pan": p.pan,
            "client_name": p.client.name,
            "assessment_year": p.assessment_year,
            "section": p.section,
            "response_due_date": str(p.response_due_date),
            "days_until_due": p.days_until_due,
            "urgency": p.urgency,
            "is_overdue": p.is_overdue,
        }
        for p in rows
    ]
