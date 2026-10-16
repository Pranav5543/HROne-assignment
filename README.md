# Employee Attendance & Analytics API

FastAPI + MongoDB. All application code is in `app/main.py`.

## Run
```bash
pip install -r requirements.txt
export MONGO_URI=mongodb://localhost:27017 MONGO_DB=attendance_db   # real env vars win over .env
uvicorn app.main:app --port 8000
```
On Windows PowerShell, use `$env:MONGO_URI="mongodb://localhost:27017"` and `$env:MONGO_DB="attendance_db"`, or configure them in a `.env` file.

Indexes are created at startup (idempotent). `GET /health` is 200 only when MongoDB answers.

## Status
Every endpoint in `openapi.yaml` is implemented, including `/admin/explain/{endpoint}`. Nothing is missing.
Analytics are MongoDB aggregation pipelines (`$lookup`, `$unionWith`, `$setWindowFields`, `$densify`). Explain output comes from the same builder functions the endpoints use.

## Choices worth knowing
* Rounding is half-up everywhere (R4, R8): `Decimal` in Python, and a `floor(x*10^n + 0.5)` expression in pipelines (MongoDB's `$round` is banker's rounding).
* `moving_avg_7d` averages the already-rounded `attendance_rate` values, as the field is reported.
* Trend `range > 92 days` means more than 92 calendar days counted inclusively.
* A PATCH that changes none of status / punch_in / punch_out is 422. If it only became a no-op because a concurrent PATCH won the race, it is 409.
* PATCH uses optimistic concurrency (compare-and-set on the record and its history length, then re-plan), so history entries are never lost.
* Punch-out earlier than the open punch-in is 422 (not 404); an employee with no record at all is 404.
* Logs for employees that do not exist are ignored in analytics; logs dated before `joined_on` are still counted.

## Tests (optional, need a real MongoDB; they drop the database named in `MONGO_DB`)
```bash
MONGO_DB=hrone_test python tests/check_rules_and_concurrency.py
MONGO_DB=hrone_test python tests/check_analytics.py        # compares every analytic with a brute-force Python oracle
MONGO_DB=hrone_perf python tests/check_explain_100k.py     # 100k records: IXSCAN / no COLLSCAN + latencies
```
