# REVIEW.md

Review of the starter `app/main.py`. Each row: where, what is wrong, how you would notice it, how I fixed it.

| # | Where | What is wrong | How you'd notice it | How I fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | Builds shift start with `punch_in.replace(hour=...)` on a **UTC** value, so shift start is 5h30m off (IST). | Punch in at 09:45 IST on a 09:30 shift: it should be 15 late minutes, but the starter returns 0, because it compares 04:15 UTC with 09:30. | Convert to IST, build `shift_start` as an IST instant on the attendance date. |
| 2 | `compute_late_minutes` | Floors minutes **first**, then tests `> 10`. 09:40:01 floors to 10, so it is not late. | 09:40:01 must give 10; starter gives 0. | Test `seconds > 600` on whole seconds, then floor to minutes. |
| 3 | `compute_late_minutes` | Ignores overnight shifts (R1): a 02:00 punch-in on a 22:00 shift is measured against 22:00 *today*. | Overnight employee punching in at 21:55 or 02:00. | Attendance date is derived by R1 first; late is measured from that date's shift start. |
| 4 | all helpers | Sub-second parts are not truncated (R1). | `09:40:00.900` counts as late. | `trunc_sec` before every calculation, and the truncated value is stored. |
| 5 | `compute_work_hours` | Python `round()` (banker's rounding on binary floats), not half-up. | round(0.015, 2) gives 0.01, but half-up should give 0.02. | `Decimal` arithmetic with `ROUND_HALF_UP`. |
| 6 | `compute_overtime` | No 30-minute minimum (R3); `date_str` is parsed with `fromisoformat` and an IST tz, but overnight shifts end the **next** day; subtracting a naive Mongo datetime from an aware one raises `TypeError`. | Out at 18:45 on an 18:30 shift gives 15, should be 0; a punch-out calculation using a stored (naive) datetime raises a TypeError. | Shift-end instant built per R1 (next day if overnight), aware datetimes everywhere, threshold applied. |
| 7 | `MongoClient(...)` | Not `tz_aware`, so every datetime read back is naive and mixes badly with IST maths. | `TypeError: can't subtract offset-naive and offset-aware`. | `tz_aware=True`; `as_utc` guard for safety. |
| 8 | `EmployeeIn` | No validation at all: emp_code format, email, `HH:MM`, `joined_on`, lengths, `shift_start == shift_end`. | `POST` with `shift_start: "25:99"` returns 201. | Constrained types and a model validator, so bad input is 422. |
| 9 | `create_employee` | Check-then-insert race and no unique index, so two parallel creates both succeed. | 10 parallel POSTs, same emp_code, give several 201. | Unique index on `emp_code`; `DuplicateKeyError` becomes 409. |
| 10 | `create_employee` | `created_at = datetime.now()` is local, naive time, and the response returns a datetime, not epoch millis. | Response has an ISO string / wrong offset. | UTC, ms precision, returned as epoch ms. |
| 11 | `list_employees` | `skip = page * page_size` skips the whole first page. | `page=1` never shows the first 20 employees. | `(page - 1) * page_size`. |
| 12 | `list_employees` | `total` counts the whole collection, not the filtered set; no sort. | `?department=Eng` shows the wrong total; order is arbitrary. | `count_documents(q)`, sort by `emp_code`, `page >= 1`, `page_size <= 100`. |
| 13 | `punch_in` | Unknown employee: `emp["shift_start"]` on `None` gives a 500. | `POST` with an unknown emp_code. | 404. |
| 14 | `punch_in` | Uses `datetime.fromtimestamp(ms / 1000)`: server-local time zone, naive. The date is therefore **not** the IST date, and `punched_at=0` is treated as "now". | Server in UTC: a 00:30 IST punch-in lands on the previous date. | Convert from UTC ms, derive the IST date per R1. |
| 15 | `punch_in` | `punched_at` accepts floats or seconds-sized numbers; `status` is any string, so `ABSENT` or `"banana"` are accepted. | `status: "LEAVE"` returns 201. | Strict epoch-ms type with range, status limited to presence values. |
| 16 | `punch_in` | Check-then-insert race plus no unique index, so duplicates for the same day. | Parallel identical punch-ins give several 201. | Unique `(emp_code, date)`; `DuplicateKeyError` becomes 409. |
| 17 | `punch_in` response | Leaks `id` (ObjectId string) and returns datetimes, not epoch ms. | Contract says `_id`/ids never appear. | Shared `out_record` serialiser. |
| 18 | `list_attendance` | Loads **every** matching document into memory, sorts in Python, slices in Python. | Slow and memory-heavy at 100k records. | Indexed `find` with sort, skip, limit and a count. |
| 19 | `list_attendance` | Sort is only `date desc` (no `emp_code` tie-break); `date_from > date_to` not rejected; invalid dates/status accepted; `page=0` gives a negative slice. | `?page=0`, `?status=bad`. | Validated params, full sort, 422 on bad ranges. |
| 20 | `list_attendance` | Legacy documents without `history`/`half_day` are returned raw, and the response carries `id`. | The sample "legacy record". | Documented defaults in `out_record`. |
| 21 | `/health` | Always returns ok, even if MongoDB is down. | Stop MongoDB, call `/health`. | Pings MongoDB, 503 on failure. |
| 22 | startup | No indexes, no connection timeout. | `explain` shows COLLSCAN; waits about 30 seconds for the default server selection timeout. | Idempotent index creation at startup (with background retry), 5 s server selection timeout. |

**Looked at and decided it was fine:** `load_dotenv()` (it does not override real environment variables, which is what the contract wants); plain `def` endpoints with a synchronous PyMongo client (FastAPI runs them in a thread pool and `MongoClient` is thread-safe, so concurrency is fine); `doc.pop("_id")` after `insert_one` (correct, it only mutates the local dict).

**Deliberately not reused as-is:** the starter helpers. They were all wrong in some way (rows 1-6), so they were rewritten rather than patched.
