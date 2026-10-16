"""
Employee Attendance & Analytics API (FastAPI + MongoDB).

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB  (a local .env is loaded for convenience; real environment variables win)

Layout of this file
  1. configuration, client, indexes
  2. time helpers and business rules R1-R5 (pure functions, no I/O)
  3. validation types and request models
  4. (de)serialisation helpers
  5. query / pipeline builders (shared by the endpoints AND the explain endpoint)
  6. endpoints
"""
import json
import logging
import os
import random
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal, Optional

from bson import json_util
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AfterValidator, BaseModel, Field, StringConstraints, model_validator
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import AutoReconnect, DuplicateKeyError, PyMongoError

load_dotenv()  # override=False: variables already in the environment take priority over .env

log = logging.getLogger("attendance")

# --------------------------------------------------------------------------- #
# 1. Configuration, client, indexes
# --------------------------------------------------------------------------- #
# The fallbacks are for local development only; the grader injects both variables.
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

# tz_aware=True: PyMongo hands back aware UTC datetimes instead of naive ones.
client = MongoClient(
    MONGO_URI,
    tz_aware=True,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    maxPoolSize=100,
)
db = client[MONGO_DB]
employees = db["employees"]
logs = db["attendance_logs"]

# (collection, keys, options). All idempotent: create_index is a no-op when it already exists.
INDEXES = [
    # natural key + the guarantee behind race-safe punch-in; also serves PATCH, punch-out lookup
    # and the per-employee monthly summary
    ("attendance_logs", [("emp_code", 1), ("date", 1)], {"unique": True, "name": "uniq_emp_date"}),
    # default list order (date desc, emp_code asc), date-range scans for every monthly analytic
    ("attendance_logs", [("date", -1), ("emp_code", 1)], {"name": "date_desc_emp"}),
    # status-filtered list, same sort
    ("attendance_logs", [("status", 1), ("date", -1), ("emp_code", 1)], {"name": "status_date_emp"}),
    # emp_code is the business key: unique, enforced by the database
    ("employees", [("emp_code", 1)], {"unique": True, "name": "uniq_emp_code"}),
    # list employees by department sorted by emp_code; trend roster
    ("employees", [("department", 1), ("emp_code", 1)], {"name": "dept_emp_code"}),
    # headcount: department + joined_on range
    ("employees", [("department", 1), ("joined_on", 1)], {"name": "dept_joined_on"}),
    # headcount across all departments
    ("employees", [("joined_on", 1)], {"name": "joined_on"}),
]


def ensure_indexes() -> bool:
    ok = True
    for coll, keys, opts in INDEXES:
        try:
            db[coll].create_index(keys, **opts)
        except AutoReconnect as exc:  # MongoDB unreachable: stop now, do not wait per index
            log.error("MongoDB unreachable while creating indexes: %s", exc)
            return False
        except PyMongoError as exc:  # keep the app alive; health/other endpoints still work
            ok = False
            log.error("index %s on %s failed: %s", opts.get("name"), coll, exc)
    return ok


def _index_retry_loop() -> None:
    while not ensure_indexes():
        time.sleep(2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # One synchronous attempt so the indexes exist before the first request. If MongoDB is not
    # reachable yet, keep retrying in the background instead of crashing the process.
    if not ensure_indexes():
        threading.Thread(target=_index_retry_loop, daemon=True).start()
    yield


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")



class Unprocessable(Exception):
    """Business-level validation failure -> 422 with FastAPI's default body shape."""

    def __init__(self, msg: str, loc: tuple = ("body",)):
        self.msg, self.loc = msg, loc


@app.exception_handler(Unprocessable)
async def _unprocessable_handler(_: Request, exc: Unprocessable):
    return JSONResponse(
        status_code=422,
        content={"detail": [{"loc": list(exc.loc), "msg": exc.msg, "type": "value_error"}]},
    )


@app.exception_handler(PyMongoError)
async def _mongo_handler(_: Request, exc: PyMongoError):
    log.error("MongoDB error: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "database unavailable"})


# --------------------------------------------------------------------------- #
# 2. Time helpers and business rules
# --------------------------------------------------------------------------- #
UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
ONE_SECOND = timedelta(seconds=1)
ONE_MS = timedelta(milliseconds=1)

PRESENCE = ["PRESENT", "WFH", "ON_DUTY"]
NON_PRESENCE = ["ABSENT", "LEAVE"]
GRACE_SECONDS = 10 * 60
MIN_OVERTIME_MINUTES = 30
HALF_DAY_BELOW_HOURS = 4.5
MAX_SHIFT = timedelta(hours=24)


def as_utc(dt: datetime) -> datetime:
    """Seeded data or a driver default may be naive; Mongo's naive values are UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def ms_to_dt(ms: int) -> datetime:
    return EPOCH + timedelta(milliseconds=ms)


def dt_to_ms(dt: datetime) -> int:
    return (as_utc(dt) - EPOCH) // ONE_MS  # exact integer arithmetic, no float rounding


def trunc_sec(dt: datetime) -> datetime:
    """R1: instants are truncated to whole seconds before any calculation."""
    return as_utc(dt).replace(microsecond=0)


def now_ms_precision() -> datetime:
    n = datetime.now(UTC)
    return n.replace(microsecond=(n.microsecond // 1000) * 1000)


def hm(value: str) -> tuple[int, int]:
    h, m = value.split(":")
    return int(h), int(m)


def is_overnight(shift_start: str, shift_end: str) -> bool:
    return hm(shift_end) <= hm(shift_start)


def ist_at(day: date, hhmm: str) -> datetime:
    h, m = hm(hhmm)
    return datetime(day.year, day.month, day.day, h, m, tzinfo=IST)


def attendance_date(punch_in: datetime, shift_start: str, shift_end: str) -> date:
    """R1: IST calendar date of the punch-in; an overnight shift's early-morning punch-in belongs
    to the shift that started the previous day."""
    local = trunc_sec(punch_in).astimezone(IST)
    day = local.date()
    if is_overnight(shift_start, shift_end) and (local.hour, local.minute) < hm(shift_end):
        day -= timedelta(days=1)
    return day


def shift_end_instant(day: date, shift_start: str, shift_end: str) -> datetime:
    end_day = day + timedelta(days=1) if is_overnight(shift_start, shift_end) else day
    return ist_at(end_day, shift_end)


def compute_late_minutes(punch_in: datetime, shift_start: str, day: date) -> int:
    """R2: late only if strictly more than 10:00 after shift start; then floor minutes since start."""
    secs = (trunc_sec(punch_in) - ist_at(day, shift_start)) // ONE_SECOND
    return secs // 60 if secs > GRACE_SECONDS else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    """R4: seconds / 3600, 2 decimals, half-up, exactly (Decimal, not binary floats)."""
    secs = (trunc_sec(punch_out) - trunc_sec(punch_in)) // ONE_SECOND
    return float((Decimal(secs) / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out: datetime, day: date, shift_start: str, shift_end: str) -> int:
    """R3: floor minutes after shift end, only when that is >= 30."""
    secs = (trunc_sec(punch_out) - shift_end_instant(day, shift_start, shift_end)) // ONE_SECOND
    minutes = secs // 60
    return minutes if minutes >= MIN_OVERTIME_MINUTES else 0


def derive(emp: dict, day_str: str, status: str, punch_in, punch_out) -> dict:
    """All derived fields for a record, from its final state (R2-R5)."""
    zero = {"work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False}
    if status not in PRESENCE or punch_in is None:
        return zero
    start = emp.get("shift_start", "09:30")
    end = emp.get("shift_end", "18:30")
    day = date.fromisoformat(day_str)
    out = dict(zero, late_minutes=compute_late_minutes(punch_in, start, day))
    if punch_out is not None:
        wh = compute_work_hours(punch_in, punch_out)
        out.update(
            work_hours=wh,
            overtime_minutes=compute_overtime(punch_out, day, start, end),
            half_day=wh < HALF_DAY_BELOW_HOURS,
        )
    return out


def half_up(value: Decimal, places: int) -> float:
    return float(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def count_weekdays(first: date, last: date) -> int:
    """Mon-Fri days in [first, last] without iterating over the whole range."""
    if last < first:
        return 0
    total = (last - first).days + 1
    full, rest = divmod(total, 7)
    count = full * 5
    for i in range(rest):
        if (first + timedelta(days=full * 7 + i)).weekday() < 5:
            count += 1
    return count


def weekday_strings(first: date, last: date) -> list[str]:
    return [
        (first + timedelta(days=i)).isoformat()
        for i in range((last - first).days + 1)
        if (first + timedelta(days=i)).weekday() < 5
    ]


def month_bounds(month: str) -> tuple[date, date]:
    year, mon = int(month[:4]), int(month[5:7])
    first = date(year, mon, 1)
    nxt = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    return first, nxt - timedelta(days=1)


# --------------------------------------------------------------------------- #
# 3. Validation types and request models
# --------------------------------------------------------------------------- #
def _check_date(v: str) -> str:
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        raise ValueError("not a valid calendar date (YYYY-MM-DD)")
    return v


def _check_month(v: str) -> str:
    if int(v[:4]) < 1:
        raise ValueError("year out of range")
    return v


DateStr = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"), AfterValidator(_check_date)]
MonthStr = Annotated[
    str, StringConstraints(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$"), AfterValidator(_check_month)
]
HHMM = Annotated[str, StringConstraints(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]
# strict=True: floats (1.5, 1783312500000.0), strings and bools are rejected; the lower bound rejects
# values that look like seconds.
EpochMs = Annotated[int, Field(strict=True, ge=100_000_000_000, le=4_102_444_800_000)]
Status = Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
PresenceStatus = Literal["PRESENT", "WFH", "ON_DUTY"]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


class EmployeeIn(BaseModel):
    emp_code: Annotated[str, StringConstraints(pattern=r"^EMP[0-9]{4,6}$")]
    name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    email: Annotated[str, StringConstraints(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
    department: Annotated[str, StringConstraints(min_length=1, max_length=50)]
    shift_start: HHMM = "09:30"
    shift_end: HHMM = "18:30"
    joined_on: DateStr

    @model_validator(mode="after")
    def _shifts_differ(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInIn(BaseModel):
    emp_code: Annotated[str, StringConstraints(min_length=1)]
    punched_at: Optional[EpochMs] = None
    status: PresenceStatus = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: Annotated[str, StringConstraints(min_length=1)]
    punched_at: Optional[EpochMs] = None


class RegularizeIn(BaseModel):
    status: Optional[Status] = None
    punch_in: Optional[EpochMs] = None
    punch_out: Optional[EpochMs] = None
    reason: Annotated[str, StringConstraints(min_length=5, max_length=200)]
    regularized_by: Annotated[str, StringConstraints(min_length=1, max_length=50)]


# --------------------------------------------------------------------------- #
# 4. (De)serialisation helpers
# --------------------------------------------------------------------------- #
def _ms(value):
    return dt_to_ms(value) if isinstance(value, datetime) else value


def out_employee(d: dict) -> dict:
    return {
        "emp_code": d["emp_code"],
        "name": d.get("name"),
        "email": d.get("email"),
        "department": d.get("department"),
        "shift_start": d.get("shift_start", "09:30"),
        "shift_end": d.get("shift_end", "18:30"),
        "joined_on": d.get("joined_on"),
        "created_at": _ms(d.get("created_at")),
    }


def out_record(d: dict) -> dict:
    """Stored attendance document -> API shape. Missing legacy fields get their documented
    defaults; _id never leaves; every instant (also inside history) becomes epoch millis."""
    history = []
    for h in d.get("history") or []:
        changes = {
            k: {"from": _ms(v.get("from")), "to": _ms(v.get("to"))}
            for k, v in (h.get("changes") or {}).items()
        }
        history.append({"at": _ms(h.get("at")), "by": h.get("by"), "reason": h.get("reason"), "changes": changes})
    return {
        "emp_code": d["emp_code"],
        "date": d["date"],
        "status": d["status"],
        "punch_in": _ms(d.get("punch_in")),
        "punch_out": _ms(d.get("punch_out")),
        "work_hours": d.get("work_hours"),
        "late_minutes": d.get("late_minutes") or 0,
        "overtime_minutes": d.get("overtime_minutes") or 0,
        "half_day": bool(d.get("half_day", False)),
        "history": history,
    }


def paginate(coll, query: dict, sort: list, page: int, page_size: int, total: int, project=None) -> list:
    skip = (page - 1) * page_size
    if skip >= total:  # also protects against absurd page numbers overflowing the int64 skip
        return []
    return list(coll.find(query, project).sort(sort).skip(skip).limit(page_size))


# --------------------------------------------------------------------------- #
# 5. Query and pipeline builders (shared with /admin/explain)
# --------------------------------------------------------------------------- #
ATT_SORT = [("date", -1), ("emp_code", 1)]


def attendance_filter(emp_code, date_from, date_to, status) -> dict:
    if date_from and date_to and date_from > date_to:
        raise Unprocessable("date_from must not be after date_to", ("query", "date_from"))
    q: dict[str, Any] = {}
    if emp_code is not None:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    return q


def hu(expr: Any, places: int) -> dict:
    """Round-half-up inside a pipeline. MongoDB's $round is banker's rounding, which R8 forbids.
    The 1e-9 absorbs binary-float noise on exact ties (e.g. 0.12345 * 1e4 = 1234.4999999999998)
    and is far smaller than the distance of any real non-tie value from .5."""
    scale = 10 ** places
    return {"$divide": [{"$floor": {"$add": [{"$multiply": [expr, scale]}, 0.500000001]}}, scale]}


def f_late() -> dict:
    return {"$ifNull": ["$late_minutes", 0]}


def f_late_flag() -> dict:
    return {"$cond": [{"$gt": [f_late(), 0]}, 1, 0]}


def f_present() -> dict:
    return {"$in": ["$status", PRESENCE]}


def f_halves(weekdays: Optional[list]) -> dict:
    """Present-day weight in half-days (2 = full day, 1 = half day, 0 = not present), kept as an
    integer so sums are exact. weekdays=None means any day of the week counts (trend)."""
    cond = f_present() if weekdays is None else {"$and": [f_present(), {"$in": ["$date", weekdays]}]}
    return {"$cond": [cond, {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 1, 2]}, 0]}


def date_range(first: str, last: str) -> dict:
    return {"$gte": first, "$lte": last}


def pipeline_employee_monthly(emp_code: str, month: str) -> list:
    first, last = month_bounds(month)
    return [
        {"$match": {"emp_code": emp_code, "date": date_range(first.isoformat(), last.isoformat())}},
        {
            "$group": {
                "_id": None,
                "halves": {"$sum": f_halves(weekday_strings(first, last))},
                "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                "late_count": {"$sum": f_late_flag()},
                "total_late_minutes": {"$sum": f_late()},
                "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
            }
        },
    ]


def pipeline_department_summary(month: str, department: Optional[str]) -> list:
    first, last = month_bounds(month)
    has_wh = {"$and": [f_present(), {"$isNumber": "$work_hours"}]}
    sums = lambda src: {  # noqa: E731  (same accumulators for both $group stages)
        "halves": {"$sum": f"${src}halves"},
        "wh_sum": {"$sum": f"${src}wh_sum"},
        "wh_n": {"$sum": f"${src}wh_n"},
        "late_count": {"$sum": f"${src}late_count"},
        "late_total": {"$sum": f"${src}late_total"},
        "leave": {"$sum": f"${src}leave"},
        "on_duty": {"$sum": f"${src}on_duty"},
    }
    headcount_branch: list = [{"$match": {"joined_on": {"$lte": last.isoformat()}}}]
    if department is not None:
        headcount_branch[0]["$match"]["department"] = department
    headcount_branch.append({"$group": {"_id": "$department", "headcount": {"$sum": 1}}})

    pipe: list = [
        {"$match": {"date": date_range(first.isoformat(), last.isoformat())}},
        # 1) collapse to one row per employee so the join below runs once per employee, not per log
        {
            "$group": {
                "_id": "$emp_code",
                "halves": {"$sum": f_halves(weekday_strings(first, last))},
                "wh_sum": {"$sum": {"$cond": [has_wh, "$work_hours", 0]}},
                "wh_n": {"$sum": {"$cond": [has_wh, 1, 0]}},
                "late_count": {"$sum": f_late_flag()},
                "late_total": {"$sum": f_late()},
                "leave": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                "on_duty": {"$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}},
            }
        },
        # 2) department lives on the employee; logs of unknown employees disappear in $unwind
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "pipeline": [{"$project": {"_id": 0, "department": 1}}],
                "as": "e",
            }
        },
        {"$unwind": "$e"},
    ]
    if department is not None:
        pipe.append({"$match": {"e.department": department}})
    pipe += [
        {"$group": dict({"_id": "$e.department"}, **sums(""))},
        # 3) headcount comes from the employees collection, so people without logs still count
        {"$unionWith": {"coll": "employees", "pipeline": headcount_branch}},
        {"$group": dict({"_id": "$_id", "headcount": {"$sum": "$headcount"}}, **sums(""))},
        {"$match": {"headcount": {"$gt": 0}}},
        {"$sort": {"_id": 1}},
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": {"$divide": ["$halves", 2]},
                "avg_work_hours": {
                    "$cond": [{"$gt": ["$wh_n", 0]}, hu({"$divide": ["$wh_sum", "$wh_n"]}, 2), None]
                },
                "late_count": 1,
                "total_late_minutes": "$late_total",
                "leave_count": "$leave",
                "on_duty_count": "$on_duty",
            }
        },
    ]
    return pipe


def pipeline_late_leaderboard(month: str, limit: int, department: Optional[str]) -> list:
    first, last = month_bounds(month)
    pipe: list = [
        {"$match": {"date": date_range(first.isoformat(), last.isoformat())}},
        {"$group": {"_id": "$emp_code", "total": {"$sum": f_late()}, "cnt": {"$sum": f_late_flag()}}},
        {"$match": {"total": {"$gt": 0}}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "pipeline": [{"$project": {"_id": 0, "name": 1, "department": 1}}],
                "as": "e",
            }
        },
        {"$unwind": "$e"},
    ]
    if department is not None:
        pipe.append({"$match": {"e.department": department}})  # rank inside the department only
    pipe += [
        # standard competition ranking (1,2,2,4) via the window function, NOT the array index
        {"$setWindowFields": {"sortBy": {"total": -1}, "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},  # limit is applied AFTER ranking
        {"$sort": {"total": -1, "_id": 1}},
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$e.name",
                "department": "$e.department",
                "total_late_minutes": "$total",
                "late_count": "$cnt",
            }
        },
    ]
    return pipe


def pipeline_department_trend(department: str, d_from: date, d_to: date) -> list:
    f, t = d_from.isoformat(), d_to.isoformat()
    lo = datetime(d_from.year, d_from.month, d_from.day, tzinfo=UTC)
    hi = datetime(d_to.year, d_to.month, d_to.day, tzinfo=UTC) + timedelta(days=1)  # upper bound is exclusive
    return [
        # roster of the department (index: department)
        {"$match": {"department": department}},
        {"$project": {"_id": 0, "emp_code": 1, "joined_on": 1}},
        # each employee's logs inside the range (index: emp_code, date)
        {
            "$lookup": {
                "from": "attendance_logs",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "pipeline": [
                    {"$match": {"date": date_range(f, t)}},
                    {"$project": {"_id": 0, "date": 1, "p": f_halves(None), "late": f_late_flag()}},
                ],
                "as": "logs",
            }
        },
        # one stream of per-day events: the logs, plus one "joined" event per employee (clamped into
        # the range) so that headcount can be a running total of joins
        {
            "$project": {
                "events": {
                    "$concatArrays": [
                        "$logs",
                        [
                            {
                                "date": {"$min": [{"$max": ["$joined_on", f]}, t]},
                                "joins": {"$cond": [{"$lte": ["$joined_on", t]}, 1, 0]},
                            }
                        ],
                    ]
                }
            }
        },
        {"$unwind": "$events"},
        {
            "$group": {
                "_id": "$events.date",
                "p": {"$sum": "$events.p"},
                "late_count": {"$sum": "$events.late"},
                "joins": {"$sum": "$events.joins"},
            }
        },
        {"$addFields": {"d": {"$dateFromString": {"dateString": "$_id", "format": "%Y-%m-%d", "onError": None}}}},
        {"$match": {"d": {"$type": "date"}}},
        # gap filling: one document per calendar day, weekends and empty days included
        {"$densify": {"field": "d", "range": {"step": 1, "unit": "day", "bounds": [lo, hi]}}},
        # headcount = running total of joins
        {
            "$setWindowFields": {
                "sortBy": {"d": 1},
                "output": {"headcount": {"$sum": "$joins", "window": {"documents": ["unbounded", "current"]}}},
            }
        },
        {
            "$addFields": {
                "date": {"$dateToString": {"date": "$d", "format": "%Y-%m-%d"}},
                "is_working_day": {"$lte": [{"$isoDayOfWeek": "$d"}, 5]},
                "present_count": {"$divide": [{"$ifNull": ["$p", 0]}, 2]},
                "late_count": {"$ifNull": ["$late_count", 0]},
            }
        },
        {
            "$addFields": {
                "attendance_rate": {
                    "$cond": [
                        {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                        hu({"$divide": ["$present_count", "$headcount"]}, 4),
                        None,
                    ]
                }
            }
        },
        # 7-day moving average over the (already rounded) rates; $avg skips nulls, and the window
        # never reaches before the first row of the range
        {
            "$setWindowFields": {
                "sortBy": {"d": 1},
                "output": {"moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}},
            }
        },
        {"$sort": {"d": 1}},
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": hu("$moving_avg_7d", 4),
            }
        },
    ]


def validate_trend_range(d_from: str, d_to: str) -> tuple[date, date]:
    a, b = date.fromisoformat(d_from), date.fromisoformat(d_to)
    if b < a:
        raise Unprocessable("'to' must not be before 'from'", ("query", "to"))
    if (b - a).days + 1 > 92:
        raise Unprocessable("range must not exceed 92 days", ("query", "to"))
    return a, b


# --------------------------------------------------------------------------- #
# 6. Endpoints
# --------------------------------------------------------------------------- #
@app.get("/", include_in_schema=False)
def root():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return RedirectResponse(url="/docs")


@app.get("/app", include_in_schema=False)
def app_view():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return RedirectResponse(url="/docs")


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
    except PyMongoError:
        raise HTTPException(503, "MongoDB is not reachable")
    return {"status": "ok"}


# ---- employees ------------------------------------------------------------ #
@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = now_ms_precision()
    try:
        employees.insert_one(doc)  # the unique index decides; no check-then-insert race
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return out_employee(doc)


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: Page = 1, page_size: PageSize = 20):
    q = {} if department is None else {"department": department}
    total = employees.count_documents(q)
    rows = paginate(employees, q, [("emp_code", 1)], page, page_size, total)
    return {"items": [out_employee(r) for r in rows], "total": total, "page": page, "page_size": page_size}


# ---- attendance ----------------------------------------------------------- #
@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    ts = trunc_sec(ms_to_dt(body.punched_at) if body.punched_at is not None else datetime.now(UTC))
    start, end = emp.get("shift_start", "09:30"), emp.get("shift_end", "18:30")
    day = attendance_date(ts, start, end)
    doc = {
        "emp_code": body.emp_code,
        "date": day.isoformat(),
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, start, day),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        logs.insert_one(doc)  # unique (emp_code, date): exactly one concurrent request wins
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    return out_record(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    ts = trunc_sec(ms_to_dt(body.punched_at) if body.punched_at is not None else datetime.now(UTC))
    for _ in range(5):
        # most recent record whose punch_in is at or before the punch-out (works across midnight)
        rec = logs.find_one(
            {"emp_code": body.emp_code, "punch_in": {"$lte": ts}},
            sort=[("date", -1), ("punch_in", -1)],
        )
        if rec is None:
            # Nothing at or before punched_at. If the employee does have an OPEN record that starts
            # *after* it, the caller is punching out before punching in: that is a 422 (the
            # contract's "punched_at <= punch_in"), not a missing record.
            later_open = logs.find_one(
                {"emp_code": body.emp_code, "punch_out": None, "punch_in": {"$gt": ts}}, {"_id": 1}
            )
            if later_open is not None:
                raise Unprocessable("punched_at must be after the punch-in", ("body", "punched_at"))
            raise HTTPException(404, "no punch-in found")
        if rec.get("punch_out") is not None:
            raise HTTPException(409, "already punched out")
        p_in = trunc_sec(rec["punch_in"])
        if ts <= p_in:
            raise Unprocessable("punched_at must be after the punch-in", ("body", "punched_at"))
        if ts - p_in > MAX_SHIFT:
            raise Unprocessable("punch-out is more than 24 hours after the punch-in", ("body", "punched_at"))
        fields = derive(emp, rec["date"], rec["status"], p_in, ts)
        fields["punch_out"] = ts
        # compare-and-set: only succeeds while the record is still open and unchanged
        updated = logs.find_one_and_update(
            {"_id": rec["_id"], "punch_out": None, "punch_in": rec["punch_in"], "status": rec["status"]},
            {"$set": fields},
            return_document=ReturnDocument.AFTER,
        )
        if updated is not None:
            return out_record(updated)
        # lost a race: loop, re-read, and answer from the new state (usually 409)
    raise HTTPException(409, "record is being modified concurrently")


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[DateStr] = None,
    date_to: Optional[DateStr] = None,
    status: Optional[Status] = None,
    page: Page = 1,
    page_size: PageSize = 20,
):
    q = attendance_filter(emp_code, date_from, date_to, status)
    total = logs.estimated_document_count() if not q else logs.count_documents(q)
    rows = paginate(logs, q, ATT_SORT, page, page_size, total, {"_id": 0})
    return {"items": [out_record(r) for r in rows], "total": total, "page": page, "page_size": page_size}


def plan_regularization(emp: dict, rec: dict, body: RegularizeIn) -> tuple[dict, dict]:
    """Pure function: (new field values, changes) for a correction, or raises Unprocessable."""
    for name in ("status", "punch_in", "punch_out"):
        if name in body.model_fields_set and getattr(body, name) is None:
            raise Unprocessable(f"{name} must not be null", ("body", name))
    start, end = emp.get("shift_start", "09:30"), emp.get("shift_end", "18:30")
    old_in = trunc_sec(rec["punch_in"]) if rec.get("punch_in") is not None else None
    old_out = trunc_sec(rec["punch_out"]) if rec.get("punch_out") is not None else None
    give_in, give_out = "punch_in" in body.model_fields_set, "punch_out" in body.model_fields_set
    new_status = body.status or rec["status"]

    if new_status in NON_PRESENCE:
        if give_in or give_out:
            raise Unprocessable("ABSENT/LEAVE clear the punch times; do not send punch_in/punch_out")
        new_in = new_out = None
    else:
        new_in = trunc_sec(ms_to_dt(body.punch_in)) if give_in else old_in
        new_out = trunc_sec(ms_to_dt(body.punch_out)) if give_out else old_out
        if new_in is None:
            raise Unprocessable("a presence status requires a punch_in")
        if give_in and attendance_date(new_in, start, end).isoformat() != rec["date"]:
            raise Unprocessable("punch_in must stay on the record's attendance date", ("body", "punch_in"))
        if new_out is not None:
            if new_out <= new_in:
                raise Unprocessable("punch_out must be after punch_in", ("body", "punch_out"))
            if new_out - new_in > MAX_SHIFT:
                raise Unprocessable("punch_out must be within 24 hours of punch_in", ("body", "punch_out"))

    new = {"status": new_status, "punch_in": new_in, "punch_out": new_out}
    if (new_status, new_in, new_out) == (rec["status"], old_in, old_out):
        raise Unprocessable("the request changes nothing")
    new.update(derive(emp, rec["date"], new_status, new_in, new_out))
    old = {
        "status": rec["status"],
        "punch_in": old_in,
        "punch_out": old_out,
        "work_hours": rec.get("work_hours"),
        "late_minutes": rec.get("late_minutes") or 0,
        "overtime_minutes": rec.get("overtime_minutes") or 0,
        "half_day": bool(rec.get("half_day", False)),
    }
    changes = {k: {"from": old[k], "to": new[k]} for k in new if old[k] != new[k]}
    return new, changes


@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code: str, date: Annotated[DateStr, Path()], body: RegularizeIn):
    emp = employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    lost_a_race = False
    for _ in range(30):
        rec = logs.find_one({"emp_code": emp_code, "date": date})
        if rec is None:
            raise HTTPException(404, "no attendance record for that date")
        try:
            new, changes = plan_regularization(emp, rec, body)
        except Unprocessable as exc:
            if lost_a_race and exc.msg == "the request changes nothing":
                raise HTTPException(409, "concurrent correction already applied this change")
            raise
        entry = {"at": now_ms_precision(), "by": body.regularized_by, "reason": body.reason, "changes": changes}
        n_hist = len(rec.get("history") or [])
        # optimistic concurrency: apply only if nobody changed the record (or its history) since we
        # read it; otherwise re-read and re-plan so no history entry is ever overwritten
        updated = logs.find_one_and_update(
            {
                "_id": rec["_id"],
                "status": rec["status"],
                "punch_in": rec.get("punch_in"),
                "punch_out": rec.get("punch_out"),
                "$expr": {"$eq": [{"$size": {"$ifNull": ["$history", []]}}, n_hist]},
            },
            {"$set": new, "$push": {"history": entry}},
            return_document=ReturnDocument.AFTER,
        )
        if updated is not None:
            return out_record(updated)
        lost_a_race = True
    raise HTTPException(409, "too many concurrent corrections, please retry")


# ---- analytics ------------------------------------------------------------ #
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: MonthStr):
    emp = employees.find_one({"emp_code": emp_code}, {"joined_on": 1})
    if emp is None:
        raise HTTPException(404, "employee not found")
    first, last = month_bounds(month)
    try:
        start = max(first, date.fromisoformat(emp["joined_on"]))
    except (KeyError, TypeError, ValueError):
        start = first
    working_days = count_weekdays(start, last)
    agg = next(iter(logs.aggregate(pipeline_employee_monthly(emp_code, month))), None) or {}
    halves = int(agg.get("halves", 0))
    pct = None
    if working_days > 0:
        pct = half_up(Decimal(halves * 100) / Decimal(2 * working_days), 2)
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": halves / 2,
        "leave_days": int(agg.get("leave_days", 0)),
        "late_count": int(agg.get("late_count", 0)),
        "total_late_minutes": int(agg.get("total_late_minutes", 0)),
        "total_overtime_minutes": int(agg.get("total_overtime_minutes", 0)),
        "attendance_pct": pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(month: MonthStr, department: Optional[str] = None):
    items = list(logs.aggregate(pipeline_department_summary(month, department)))
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: MonthStr, limit: Annotated[int, Query(ge=1, le=50)] = 10, department: Optional[str] = None):
    items = list(logs.aggregate(pipeline_late_leaderboard(month, limit, department)))
    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_: Annotated[DateStr, Query(alias="from")],
    to: DateStr,
):
    d_from, d_to = validate_trend_range(from_, to)
    if employees.find_one({"department": department}, {"_id": 1}) is None:
        raise HTTPException(404, "unknown department")
    items = list(employees.aggregate(pipeline_department_trend(department, d_from, d_to)))
    return {"department": department, "items": items}


# ---- admin: explain ------------------------------------------------------- #
def _need(**params) -> None:
    for name, value in params.items():
        if value is None:
            raise Unprocessable(f"'{name}' is required for this endpoint", ("query", name))


def _jsonable(doc: Any) -> Any:
    return json.loads(json_util.dumps(doc))


@app.get("/admin/explain/{endpoint}")
def explain(
    endpoint: Literal["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"],
    emp_code: Optional[str] = None,
    month: Optional[MonthStr] = None,
    department: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    date_from: Optional[DateStr] = None,
    date_to: Optional[DateStr] = None,
    status: Optional[Status] = None,
    from_: Annotated[Optional[DateStr], Query(alias="from")] = None,
    to: Optional[DateStr] = None,
    page: Page = 1,
    page_size: PageSize = 20,
):
    if endpoint == "attendance_list":
        q = attendance_filter(emp_code, date_from, date_to, status)
        cmd: dict = {
            "find": "attendance_logs",
            "filter": q,
            "sort": dict(ATT_SORT),
            "skip": min((page - 1) * page_size, 2**31),
            "limit": page_size,
        }
        coll = "attendance_logs"
    else:
        if endpoint == "employee_monthly":
            _need(emp_code=emp_code, month=month)
            pipe, coll = pipeline_employee_monthly(emp_code, month), "attendance_logs"
        elif endpoint == "department_summary":
            _need(month=month)
            pipe, coll = pipeline_department_summary(month, department), "attendance_logs"
        elif endpoint == "late_leaderboard":
            _need(month=month)
            pipe, coll = pipeline_late_leaderboard(month, limit, department), "attendance_logs"
        else:
            _need(department=department, **{"from": from_, "to": to})
            a, b = validate_trend_range(from_, to)
            pipe, coll = pipeline_department_trend(department, a, b), "employees"
        cmd = {"aggregate": coll, "pipeline": pipe, "cursor": {}}
    result = db.command({"explain": cmd, "verbosity": "executionStats"})
    return {"endpoint": endpoint, "collection": coll, "explain": _jsonable(result)}
