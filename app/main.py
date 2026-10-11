"""Employee attendance and analytics API."""
import calendar
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

load_dotenv()
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
MIN_EPOCH = 100_000_000_000
MAX_EPOCH = 4_102_444_800_000
PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}
STATUSES = PRESENCE | {"ABSENT", "LEAVE"}

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)
db = client[os.getenv("MONGO_DB", "attendance_db")]


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("must be a valid YYYY-MM-DD date") from exc


def _shift(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("must be a valid HH:MM time") from exc


def _epoch(value: int | None) -> datetime | None:
    if value is None:
        return None
    if type(value) is not int or not MIN_EPOCH <= value <= MAX_EPOCH:
        raise ValueError("must be epoch milliseconds in the supported range")
    return datetime.fromtimestamp(value / 1000, UTC).replace(microsecond=0)


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _millis(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def _round(value: float | Decimal, places: int = 2) -> float:
    quant = Decimal(1).scaleb(-places)
    result = Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP)
    return float(result)


def _shift_end(attendance_date: str, employee: dict[str, Any]) -> datetime:
    start = _shift(employee["shift_start"])
    end = _shift(employee["shift_end"])
    day = _date(attendance_date)
    if end <= start:
        day += timedelta(days=1)
    return datetime.combine(day, end, IST).astimezone(UTC).replace(tzinfo=UTC)


def attendance_date(punched_at: datetime, employee: dict[str, Any]) -> str:
    local = punched_at.astimezone(IST)
    day = local.date()
    if _shift(employee["shift_end"]) <= _shift(employee["shift_start"]) and local.time() < _shift(employee["shift_end"]):
        day -= timedelta(days=1)
    return day.isoformat()


def compute_late_minutes(punch_in: datetime, employee: dict[str, Any]) -> int:
    local = punch_in.astimezone(IST)
    shift_start = _shift(employee["shift_start"])
    shift_end = _shift(employee["shift_end"])
    start_day = local.date()
    if shift_end <= shift_start and local.time() < shift_end:
        start_day -= timedelta(days=1)
    start = datetime.combine(start_day, shift_start, IST).astimezone(UTC)
    elapsed = int((punch_in - start).total_seconds() // 60)
    return elapsed if elapsed > 10 else 0


def compute_derived(punch_in: datetime | None, punch_out: datetime | None, employee: dict[str, Any]) -> dict[str, Any]:
    late = compute_late_minutes(punch_in, employee) if punch_in else 0
    if not punch_in or not punch_out:
        return {"late_minutes": late, "work_hours": None, "overtime_minutes": 0, "half_day": False}
    seconds = (punch_out - punch_in).total_seconds()
    hours = _round(seconds / 3600)
    overtime = int(max(0, (punch_out - _shift_end(attendance_date(punch_in, employee), employee)).total_seconds()) // 60)
    if overtime < 30:
        overtime = 0
    return {"late_minutes": late, "work_hours": hours, "overtime_minutes": overtime, "half_day": hours < 4.50}


def _public(value: Any) -> Any:
    if isinstance(value, datetime):
        return _millis(value)
    if isinstance(value, list):
        return [_public(item) for item in value]
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items() if key != "_id"}
    return value


class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def valid_joined_on(cls, value: str) -> str:
        _date(value)
        return value

    @model_validator(mode="after")
    def distinct_shift(self) -> "EmployeeIn":
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: int | None = Field(default=None, ge=MIN_EPOCH, le=MAX_EPOCH)
    status: str = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def integer_epoch(cls, value: int | None) -> int | None:
        if value is not None and type(value) is not int:
            raise ValueError("must be an integer epoch millisecond")
        return value

    @field_validator("status")
    @classmethod
    def presence_status(cls, value: str) -> str:
        if value not in PRESENCE:
            raise ValueError("status must be PRESENT, WFH, or ON_DUTY")
        return value


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: int | None = Field(default=None, ge=MIN_EPOCH, le=MAX_EPOCH)

    @field_validator("punched_at")
    @classmethod
    def integer_epoch(cls, value: int | None) -> int | None:
        if value is not None and type(value) is not int:
            raise ValueError("must be an integer epoch millisecond")
        return value


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str | None = None
    punch_in: int | None = Field(default=None, ge=MIN_EPOCH, le=MAX_EPOCH)
    punch_out: int | None = Field(default=None, ge=MIN_EPOCH, le=MAX_EPOCH)
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str | None) -> str | None:
        if value is not None and value not in STATUSES:
            raise ValueError("invalid status")
        return value

    @field_validator("punch_in", "punch_out")
    @classmethod
    def integer_epoch(cls, value: int | None) -> int | None:
        if value is not None and type(value) is not int:
            raise ValueError("must be an integer epoch millisecond")
        return value


def _month_bounds(month: str) -> tuple[str, str]:
    try:
        year, mon = map(int, month.split("-"))
        if len(month) != 7 or not 1 <= mon <= 12:
            raise ValueError
        last = calendar.monthrange(year, mon)[1]
        return month + "-01", f"{year:04d}-{mon:02d}-{last:02d}"
    except ValueError as exc:
        raise HTTPException(422, "month must be YYYY-MM") from exc


def _working_days(first: date, last: date) -> int:
    if last < first:
        return 0
    return sum((first + timedelta(days=i)).weekday() < 5 for i in range((last - first).days + 1))


def _present_value(record: dict[str, Any]) -> float:
    return 0.5 if record.get("half_day", False) else 1.0


def _record(doc: dict[str, Any]) -> dict[str, Any]:
    result = dict(doc)
    result.setdefault("history", [])
    result.setdefault("half_day", False)
    result.setdefault("late_minutes", 0)
    result.setdefault("overtime_minutes", 0)
    return _public(result)


def create_indexes() -> None:
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_emp_code")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="department_joined")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_day")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="attendance_date_emp")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING), ("punch_in", DESCENDING)], name="attendance_emp_date")


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_indexes()
    yield


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    try:
        db.command("ping")
    except Exception as exc:
        raise HTTPException(503, "MongoDB unavailable") from exc
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn) -> dict[str, Any]:
    doc = body.model_dump()
    doc["created_at"] = _now()
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(409, "emp_code already exists") from exc
    return _public(doc)


@app.get("/employees")
def list_employees(department: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    q = {"department": department} if department else {}
    total = db.employees.count_documents(q)
    docs = db.employees.find(q, {"_id": 0}).sort("emp_code", ASCENDING).skip((page - 1) * page_size).limit(page_size)
    return {"items": [_public(doc) for doc in docs], "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn) -> dict[str, Any]:
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if not employee:
        raise HTTPException(404, "employee not found")
    punched_at = _epoch(body.punched_at) or _now()
    record_date = attendance_date(punched_at, employee)
    derived = compute_derived(punched_at, None, employee)
    doc = {"emp_code": body.emp_code, "date": record_date, "status": body.status, "punch_in": punched_at,
           "punch_out": None, **derived, "history": []}
    try:
        result = db.attendance_logs.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(409, "already punched in for this date") from exc
    doc["_id"] = result.inserted_id
    return _record(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn) -> dict[str, Any]:
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if not employee:
        raise HTTPException(404, "employee not found")
    punched_at = _epoch(body.punched_at) or _now()
    candidate = db.attendance_logs.find_one({"emp_code": body.emp_code, "punch_in": {"$lte": punched_at}},
                                             sort=[("punch_in", DESCENDING)])
    if not candidate:
        raise HTTPException(404, "no punch-in found")
    punch_in_value = candidate["punch_in"]
    if punched_at <= punch_in_value or punched_at > punch_in_value + timedelta(hours=24):
        raise HTTPException(422, "punched_at must be after punch_in and within 24 hours")
    derived = compute_derived(punch_in_value, punched_at, employee)
    updated = db.attendance_logs.find_one_and_update(
        {"_id": candidate["_id"], "punch_out": None},
        {"$set": {"punch_out": punched_at, **derived}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "record is already punched out")
    return _record(updated)


@app.get("/attendance")
def list_attendance(emp_code: str | None = None, date_from: str | None = None, date_to: str | None = None,
                     status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    if date_from:
        _date(date_from)
    if date_to:
        _date(date_to)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must not be after date_to")
    q: dict[str, Any] = {}
    if emp_code:
        q["emp_code"] = emp_code
    if status:
        if status not in STATUSES:
            raise HTTPException(422, "invalid status")
        q["status"] = status
    if date_from or date_to:
        q["date"] = {key: value for key, value in (("$gte", date_from), ("$lte", date_to)) if value}
    total = db.attendance_logs.count_documents(q)
    docs = db.attendance_logs.find(q).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip((page - 1) * page_size).limit(page_size)
    return {"items": [_record(doc) for doc in docs], "total": total, "page": page, "page_size": page_size}


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn) -> dict[str, Any]:
    _date(date)
    employee = db.employees.find_one({"emp_code": emp_code})
    if not employee:
        raise HTTPException(404, "employee not found")
    old = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not old:
        raise HTTPException(404, "attendance record not found")
    old_in, old_out, old_status = old.get("punch_in"), old.get("punch_out"), old["status"]
    new_status = body.status if body.status is not None else old_status
    if new_status in {"ABSENT", "LEAVE"}:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "ABSENT and LEAVE cannot have punch times")
        new_in = new_out = None
    else:
        new_in = _epoch(body.punch_in) if body.punch_in is not None else old_in
        new_out = _epoch(body.punch_out) if body.punch_out is not None else old_out
        if new_in is None:
            raise HTTPException(422, "presence status requires punch_in")
        if attendance_date(new_in, employee) != date:
            raise HTTPException(422, "punch_in must belong to the record date")
        if new_out is not None and (new_out <= new_in or new_out > new_in + timedelta(hours=24)):
            raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
    new_derived = compute_derived(new_in, new_out, employee) if new_status in PRESENCE else {
        "late_minutes": 0, "work_hours": None, "overtime_minutes": 0, "half_day": False}
    proposed = {"status": new_status, "punch_in": new_in, "punch_out": new_out, **new_derived}
    fields = ["status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day"]
    changes = {field: {"from": old.get(field), "to": proposed[field]} for field in fields if old.get(field) != proposed[field]}
    if not changes:
        raise HTTPException(422, "request does not change the record")
    history = {"at": _now(), "by": body.regularized_by, "reason": body.reason,
               "changes": changes}
    update = {"$set": proposed, "$push": {"history": history}}
    history_filter = {"history": old.get("history", [])} if "history" in old else {
        "$or": [{"history": {"$exists": False}}, {"history": []}]
    }
    updated = db.attendance_logs.find_one_and_update(
        {"_id": old["_id"], **history_filter}, update, return_document=ReturnDocument.AFTER)
    if updated is None:
        raise HTTPException(409, "record was changed concurrently")
    return _record(updated)


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str) -> dict[str, Any]:
    employee = db.employees.find_one({"emp_code": emp_code})
    if not employee:
        raise HTTPException(404, "employee not found")
    first, last = _month_bounds(month)
    records = list(db.attendance_logs.aggregate([
        {"$match": {"emp_code": emp_code, "date": {"$gte": first, "$lte": last}}},
        {"$project": {"_id": 0, "status": 1, "date": 1, "half_day": 1, "late_minutes": 1,
                      "overtime_minutes": 1}},
    ]))
    start = max(_date(first), _date(employee["joined_on"]))
    working = _working_days(start, _date(last))
    present = sum(_present_value(r) for r in records if r["status"] in PRESENCE and _date(r["date"]).weekday() < 5)
    return {"emp_code": emp_code, "month": month, "working_days": working, "present_days": _round(present),
            "leave_days": sum(r["status"] == "LEAVE" for r in records),
            "late_count": sum((r.get("late_minutes") or 0) > 0 for r in records),
            "total_late_minutes": sum(r.get("late_minutes") or 0 for r in records),
            "total_overtime_minutes": sum(r.get("overtime_minutes") or 0 for r in records),
            "attendance_pct": _round(present / working * 100) if working else None}


def _department_records(department: str | None, first: str, last: str) -> list[dict[str, Any]]:
    match: dict[str, Any] = {"joined_on": {"$lte": last}}
    if department:
        match["department"] = department
    return list(db.employees.aggregate([
        {"$match": match},
        {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code"},
                     "pipeline": [{"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$code"]},
                         {"$gte": ["$date", first]}, {"$lte": ["$date", last]}]}}}], "as": "logs"}},
        {"$project": {"_id": 0, "emp_code": 1, "department": 1, "joined_on": 1, "logs": 1}},
    ]))


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: str | None = None) -> dict[str, Any]:
    first, last = _month_bounds(month)
    grouped: dict[str, dict[str, Any]] = {}
    for employee in _department_records(department, first, last):
        item = grouped.setdefault(employee["department"], {"department": employee["department"], "headcount": 0,
            "present_days": 0.0, "hours": [], "late_count": 0, "total_late_minutes": 0, "leave_count": 0, "on_duty_count": 0})
        item["headcount"] += 1
        for record in employee["logs"]:
            if record["status"] in PRESENCE:
                if _date(record["date"]).weekday() < 5:
                    item["present_days"] += _present_value(record)
                if record.get("work_hours") is not None:
                    item["hours"].append(record["work_hours"])
            item["late_count"] += (record.get("late_minutes") or 0) > 0
            item["total_late_minutes"] += record.get("late_minutes") or 0
            item["leave_count"] += record["status"] == "LEAVE"
            item["on_duty_count"] += record["status"] == "ON_DUTY"
    items = []
    for item in sorted(grouped.values(), key=lambda x: x["department"]):
        items.append({**{key: item[key] for key in ("department", "headcount", "late_count", "total_late_minutes", "leave_count", "on_duty_count")},
                      "present_days": _round(item["present_days"]), "avg_work_hours": _round(sum(item["hours"]) / len(item["hours"])) if item["hours"] else None})
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = Query(10, ge=1, le=50), department: str | None = None) -> dict[str, Any]:
    first, last = _month_bounds(month)
    match = {"date": {"$gte": first, "$lte": last}}
    pipeline: list[dict[str, Any]] = [{"$match": match}, {"$group": {"_id": "$emp_code",
        "total": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
        "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}},
        {"$match": {"total": {"$gt": 0}}}, {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"}]
    if department:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline.append({"$project": {"_id": 0, "emp_code": "$_id", "name": "$employee.name", "department": "$employee.department",
                                  "total_late_minutes": "$total", "late_count": 1}})
    rows = list(db.attendance_logs.aggregate(pipeline))
    rows.sort(key=lambda x: (-x["total_late_minutes"], x["emp_code"]))
    rank = 0
    previous = None
    for index, row in enumerate(rows, 1):
        if row["total_late_minutes"] != previous:
            rank = index
            previous = row["total_late_minutes"]
        row["rank"] = rank
    return {"month": month, "items": [row for row in rows if row["rank"] <= limit]}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_: str = Query(..., alias="from"), to: str = Query(...)) -> dict[str, Any]:
    start, end = _date(from_), _date(to)
    if end < start:
        raise HTTPException(422, "to must not be before from")
    if (end - start).days + 1 > 92:
        raise HTTPException(422, "trend range cannot exceed 92 days")
    if not db.employees.find_one({"department": department}):
        raise HTTPException(404, "department not found")
    employees = list(db.employees.find({"department": department, "joined_on": {"$lte": to}},
                                       {"_id": 0, "emp_code": 1, "joined_on": 1}))
    employee_by_code = {employee["emp_code"]: employee for employee in employees}
    codes = list(employee_by_code)
    logs = list(db.attendance_logs.aggregate([
        {"$match": {"emp_code": {"$in": codes}, "date": {"$gte": from_, "$lte": to}}},
        {"$group": {"_id": {"date": "$date", "emp_code": "$emp_code"},
                    "present_count": {"$sum": {"$cond": [{"$in": ["$status", list(PRESENCE)]},
                                                         {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0]}},
                    "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}},
    ]))
    by_date: dict[str, dict[str, float]] = {}
    for record in logs:
        code = record["_id"]["emp_code"]
        if employee_by_code[code]["joined_on"] > record["_id"]["date"]:
            continue
        day = record["_id"]["date"]
        totals = by_date.setdefault(day, {"present_count": 0, "late_count": 0})
        totals["present_count"] += record["present_count"]
        totals["late_count"] += record["late_count"]
    joined_dates = [_date(employee["joined_on"]) for employee in employees]
    rows = []
    rate_rows: list[float | None] = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        key = day.isoformat()
        headcount = sum(joined_on <= day for joined_on in joined_dates)
        record = by_date.get(key, {"present_count": 0, "late_count": 0})
        working = day.weekday() < 5
        rate = _round(record["present_count"] / headcount, 4) if working and headcount else None
        rate_rows.append(rate)
        window = [value for value in rate_rows[-7:] if value is not None]
        rows.append({"date": key, "is_working_day": working, "headcount": headcount,
                     "present_count": _round(record["present_count"]), "late_count": record["late_count"],
                     "attendance_rate": rate, "moving_avg_7d": _round(sum(window) / len(window), 4) if window else None})
    return {"department": department, "from": from_, "to": to, "items": rows}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str, emp_code: str | None = None, month: str | None = None,
                     department: str | None = None, limit: int = Query(10, ge=1, le=50),
                     date_from: str | None = None, date_to: str | None = None, status: str | None = None,
                     from_: str | None = Query(None, alias="from"), to: str | None = None,
                     page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    if endpoint not in {"attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"}:
        raise HTTPException(422, "unknown endpoint")
    if endpoint == "attendance_list":
        query: dict[str, Any] = {}
        if emp_code: query["emp_code"] = emp_code
        if status: query["status"] = status
        if date_from or date_to: query["date"] = {k: v for k, v in (("$gte", date_from), ("$lte", date_to)) if v}
        explained = db.command("explain", {"find": db.attendance_logs.name, "filter": query, "sort": {"date": -1, "emp_code": 1}, "skip": (page - 1) * page_size, "limit": page_size}, verbosity="executionStats")
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explained}
    if endpoint in {"employee_monthly", "department_summary", "late_leaderboard"} and not month:
        raise HTTPException(422, "month is required")
    if endpoint == "department_trend" and (not from_ or not to):
        raise HTTPException(422, "from and to are required")
    if month:
        first, last = _month_bounds(month)
    else:
        first, last = from_, to
    match: dict[str, Any] = {"date": {"$gte": first, "$lte": last}}
    if emp_code:
        match["emp_code"] = emp_code
    if status:
        match["status"] = status
    pipeline: list[dict[str, Any]] = [{"$match": match}]
    if department:
        pipeline.extend([
            {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "employee"}},
            {"$match": {"employee.department": department}},
        ])
    if endpoint == "late_leaderboard":
        pipeline.extend([
            {"$group": {"_id": "$emp_code", "total": {"$sum": {"$ifNull": ["$late_minutes", 0]}}}},
            {"$sort": {"total": -1, "_id": 1}},
            {"$skip": 0},
            {"$limit": limit},
        ])
    elif endpoint == "attendance_list":
        pipeline.extend([{"$sort": {"date": -1, "emp_code": 1}},
                         {"$skip": (page - 1) * page_size}, {"$limit": page_size}])
    explained = db.command("explain", {"aggregate": db.attendance_logs.name, "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
    return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explained}
