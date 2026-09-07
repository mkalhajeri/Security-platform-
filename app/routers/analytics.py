"""Statistics on filed incidents: volume trends, who's involved most often,
and recurring patterns.

Computed in Python over a lean projection rather than a MongoDB aggregation
pipeline, because incident/report dates are stored as bare ISO date
*strings* (BSON has no date-only type — see `_json_safe` in
routers/incidents.py), which makes Mongo's date-bucketing operators
awkward to use directly. This is the right tradeoff at the data volumes a
single-organization incident log accumulates (thousands, not millions, of
reports); if that changes, move this to an aggregation pipeline or
precomputed rollups.
"""

from collections import Counter
from enum import Enum

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.auth import get_current_user
from app.database import get_database

router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[Depends(get_current_user)])


class AnalyticsPeriod(str, Enum):
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class CountItem(BaseModel):
    key: str
    count: int


class TrendPoint(BaseModel):
    period: str
    count: int


class RepeatPerson(BaseModel):
    name: str
    id_number: str | None = None
    count: int
    last_seen: str | None = None


class SitePattern(BaseModel):
    site_location: str
    category: str
    count: int


class AnalyticsResponse(BaseModel):
    period: AnalyticsPeriod
    from_date: str | None
    to_date: str | None
    total_incidents: int
    status_breakdown: list[CountItem]
    nature_breakdown: list[CountItem]
    category_breakdown: list[CountItem]
    trend: list[TrendPoint]
    top_sites: list[CountItem]
    top_reporters: list[CountItem]
    top_reviewers: list[CountItem]
    top_approvers: list[CountItem]
    repeat_involved_persons: list[RepeatPerson]
    repeat_site_category_patterns: list[SitePattern]


def _bucket_key(iso_date: str, period: AnalyticsPeriod) -> str:
    """YYYY-MM-DD -> a bucket label. String-sortable == chronologically
    sortable for all three shapes (YYYY, YYYY-MM, YYYY-Qn)."""
    year = iso_date[:4]
    if period == AnalyticsPeriod.YEAR:
        return year
    month = int(iso_date[5:7])
    if period == AnalyticsPeriod.QUARTER:
        quarter = (month - 1) // 3 + 1
        return f"{year}-Q{quarter}"
    return iso_date[:7]


def _top(counter: Counter, top_n: int) -> list[CountItem]:
    return [CountItem(key=key, count=count) for key, count in counter.most_common(top_n) if key]


@router.get("", response_model=AnalyticsResponse)
async def get_analytics(
    db: AsyncIOMotorDatabase = Depends(get_database),
    period: AnalyticsPeriod = Query(default=AnalyticsPeriod.MONTH),
    from_date: str | None = Query(default=None, description="YYYY-MM-DD, inclusive; filters by incident_date"),
    to_date: str | None = Query(default=None, description="YYYY-MM-DD, inclusive; filters by incident_date"),
    top_n: int = Query(default=10, ge=1, le=50),
) -> AnalyticsResponse:
    query: dict = {}
    if from_date or to_date:
        date_range: dict = {}
        if from_date:
            date_range["$gte"] = from_date
        if to_date:
            date_range["$lte"] = to_date
        query["incident_date"] = date_range

    projection = {
        "reported_by": 1,
        "reviewed_by": 1,
        "approved_by": 1,
        "involved_persons": 1,
        "site_location": 1,
        "nature_of_report": 1,
        "incident_categories": 1,
        "status": 1,
        "incident_date": 1,
    }
    docs = [doc async for doc in db["incidents"].find(query, projection)]

    status_counts: Counter = Counter()
    nature_counts: Counter = Counter()
    category_counts: Counter = Counter()
    site_counts: Counter = Counter()
    reporter_counts: Counter = Counter()
    reviewer_counts: Counter = Counter()
    approver_counts: Counter = Counter()
    trend_counts: Counter = Counter()
    site_category_counts: Counter = Counter()

    # Keyed by (name, id_number) so two different people who happen to
    # share a first name aren't conflated when an ID/labour card is on file.
    person_counts: Counter = Counter()
    person_last_seen: dict[tuple[str, str], str] = {}

    for doc in docs:
        status_counts[doc.get("status")] += 1
        nature_counts[doc.get("nature_of_report")] += 1
        site_counts[doc.get("site_location")] += 1

        categories = doc.get("incident_categories") or []
        for category in categories:
            category_counts[category] += 1
            if doc.get("site_location"):
                site_category_counts[(doc["site_location"], category)] += 1

        reporter_counts[doc.get("reported_by")] += 1

        reviewed_by = doc.get("reviewed_by") or {}
        if reviewed_by.get("name"):
            reviewer_counts[reviewed_by["name"]] += 1

        approved_by = doc.get("approved_by") or {}
        if approved_by.get("name"):
            approver_counts[approved_by["name"]] += 1

        incident_date = doc.get("incident_date")
        if incident_date:
            trend_counts[_bucket_key(incident_date, period)] += 1

        for person in doc.get("involved_persons") or []:
            name = (person.get("name") or "").strip()
            if not name:
                continue
            key = (name, person.get("id_number") or "")
            person_counts[key] += 1
            if incident_date and (key not in person_last_seen or incident_date > person_last_seen[key]):
                person_last_seen[key] = incident_date

    trend = [TrendPoint(period=k, count=v) for k, v in sorted(trend_counts.items())]

    repeat_persons = [
        RepeatPerson(name=name, id_number=id_number or None, count=count, last_seen=person_last_seen.get((name, id_number)))
        for (name, id_number), count in person_counts.most_common()
        if count > 1
    ][:top_n]

    repeat_patterns = [
        SitePattern(site_location=site, category=category, count=count)
        for (site, category), count in site_category_counts.most_common()
        if count > 1
    ][:top_n]

    return AnalyticsResponse(
        period=period,
        from_date=from_date,
        to_date=to_date,
        total_incidents=len(docs),
        status_breakdown=_top(status_counts, 50),
        nature_breakdown=_top(nature_counts, 50),
        category_breakdown=_top(category_counts, 50),
        trend=trend,
        top_sites=_top(site_counts, top_n),
        top_reporters=_top(reporter_counts, top_n),
        top_reviewers=_top(reviewer_counts, top_n),
        top_approvers=_top(approver_counts, top_n),
        repeat_involved_persons=repeat_persons,
        repeat_site_category_patterns=repeat_patterns,
    )
