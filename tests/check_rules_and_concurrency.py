"""Run against a REAL MongoDB:  MONGO_URI=... MONGO_DB=hrone_test python tests/check_rules_and_concurrency.py
Drops the collections in MONGO_DB first, so never point it at data you care about."""
import os, sys
os.environ.setdefault("MONGO_DB", "hrone_test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fastapi.testclient import TestClient
import app.main as m
m.db.employees.drop(); m.db.attendance_logs.drop()
c = TestClient(m.app).__enter__()   # runs startup -> creates indexes
import bson, threading, collections
from datetime import datetime, timezone, date
UTC = timezone.utc
I = lambda s: datetime.fromisoformat(s).astimezone(UTC)
ms = lambda s: int(I(s).timestamp()*1000)
# R2 examples
for t, exp in [("09:40:00",0),("09:40:01",10),("10:15:59",45),("09:25:00",0),("09:30:00",0)]:
    assert m.compute_late_minutes(I(f"2026-07-06T{t}+05:30"), "09:30", date(2026,7,6)) == exp, t
assert m.compute_late_minutes(I("2026-07-06T09:40:00.900+05:30"), "09:30", date(2026,7,6)) == 0   # truncation
# R3
d = date(2026,7,6)
assert m.compute_overtime(I("2026-07-06T18:59:59+05:30"), d, "09:30","18:30") == 0
assert m.compute_overtime(I("2026-07-06T19:00:00+05:30"), d, "09:30","18:30") == 30
assert m.compute_overtime(I("2026-07-06T19:10:59+05:30"), d, "09:30","18:30") == 40
assert m.compute_overtime(I("2026-07-07T06:40:00+05:30"), d, "22:00","06:00") == 40   # overnight
# R4 half-up: 4.5 h exactly-ish ; 18 s = 0.005 h -> 0.01
assert m.compute_work_hours(I("2026-07-06T10:00:00+05:30"), I("2026-07-06T10:00:18+05:30")) == 0.01
assert m.compute_work_hours(I("2026-07-06T10:00:00+05:30"), I("2026-07-06T10:00:54+05:30")) == 0.02  # 0.015
# R1 overnight date
assert m.attendance_date(I("2026-07-07T03:00:00+05:30"), "22:00","06:00") == date(2026,7,6)
assert m.attendance_date(I("2026-07-07T06:00:00+05:30"), "22:00","06:00") == date(2026,7,7)
assert m.attendance_date(I("2026-07-06T21:55:00+05:30"), "22:00","06:00") == date(2026,7,6)
# date is IST not UTC: 00:30 IST on the 7th is 19:00Z on the 6th
assert m.attendance_date(I("2026-07-07T00:30:00+05:30"), "09:30","18:30") == date(2026,7,7)
print("rule unit checks ok")

# overnight end-to-end (sample doc 7)
c.post("/employees", json=dict(emp_code="EMP0005", name="N", email="n@x.com", department="Ops", shift_start="22:00", shift_end="06:00", joined_on="2026-01-05"))
r = c.post("/attendance/punch-in", json=dict(emp_code="EMP0005", punched_at=ms("2026-07-06T21:55:00+05:30"))); print(r.json()["date"], r.json()["late_minutes"])
r = c.post("/attendance/punch-out", json=dict(emp_code="EMP0005", punched_at=ms("2026-07-07T06:40:00+05:30"))); print(r.status_code, r.json()["work_hours"], r.json()["overtime_minutes"], r.json()["half_day"])
# punch-out validations
c.post("/employees", json=dict(emp_code="EMP0006", name="N", email="n@x.com", department="Eng", joined_on="2026-01-05"))
c.post("/attendance/punch-in", json=dict(emp_code="EMP0006", punched_at=ms("2026-07-07T09:30:00+05:30")))
print("eq", c.post("/attendance/punch-out", json=dict(emp_code="EMP0006", punched_at=ms("2026-07-07T09:30:00+05:30"))).status_code,
      "before", c.post("/attendance/punch-out", json=dict(emp_code="EMP0006", punched_at=ms("2026-07-07T09:00:00+05:30"))).status_code,
      ">24h", c.post("/attendance/punch-out", json=dict(emp_code="EMP0006", punched_at=ms("2026-07-08T09:30:01+05:30"))).status_code,
      "==24h", c.post("/attendance/punch-out", json=dict(emp_code="EMP0006", punched_at=ms("2026-07-08T09:30:00+05:30"))).status_code)
print("unknown emp", c.post("/attendance/punch-out", json=dict(emp_code="EMP4242")).status_code)
# half day
c.post("/attendance/punch-in", json=dict(emp_code="EMP0006", punched_at=ms("2026-07-09T09:30:00+05:30")))
r = c.post("/attendance/punch-out", json=dict(emp_code="EMP0006", punched_at=ms("2026-07-09T13:59:30+05:30"))); print(r.json()["work_hours"], r.json()["half_day"])

# concurrency: 20 simultaneous punch-ins, then 20 punch-outs, then 15 PATCHes with different values
def par(fn, n):
    res = []; ts = [threading.Thread(target=lambda i=i: res.append(fn(i))) for i in range(n)]
    [t.start() for t in ts]; [t.join() for t in ts]; return res
c.post("/employees", json=dict(emp_code="EMP0007", name="N", email="n@x.com", department="Eng", joined_on="2026-01-05"))
print(collections.Counter(par(lambda i: c.post("/attendance/punch-in", json=dict(emp_code="EMP0007", punched_at=ms("2026-07-10T09:31:00+05:30")+i*1000)).status_code, 20)))
print(collections.Counter(par(lambda i: c.post("/attendance/punch-out", json=dict(emp_code="EMP0007", punched_at=ms("2026-07-10T18:40:00+05:30")+i*1000)).status_code, 20)))
res = par(lambda i: c.patch("/attendance/EMP0007/2026-07-10", json=dict(punch_in=ms("2026-07-10T09:00:00+05:30")+i*60000, reason="concurrent edit", regularized_by=f"u{i}")).status_code, 15)
rec = c.get("/attendance", params={"emp_code": "EMP0007"}).json()["items"][0]
print(collections.Counter(res), "history entries:", len(rec["history"]))
print(collections.Counter(par(lambda i: c.patch("/attendance/EMP0007/2026-07-10", json=dict(punch_in=ms("2026-07-10T09:07:00+05:30"), reason="same again", regularized_by="x")).status_code, 10)))
print(collections.Counter(par(lambda i: c.post("/employees", json=dict(emp_code="EMP0099", name="N", email="n@x.com", department="Eng", joined_on="2026-01-05")).status_code, 10)))

# every pipeline must be BSON-encodable
for p in (m.pipeline_employee_monthly("EMP0001","2026-07"), m.pipeline_department_summary("2026-07", None), m.pipeline_department_summary("2026-07","Eng"),
          m.pipeline_late_leaderboard("2026-07", 10, "Eng"), m.pipeline_department_trend("Eng", date(2026,7,1), date(2026,7,31))):
    bson.encode({"p": p})
print("bson ok")
