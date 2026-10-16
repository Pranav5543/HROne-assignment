"""Run against a REAL MongoDB 6+:  MONGO_URI=... MONGO_DB=hrone_perf python tests/check_explain_100k.py
Seeds ~100k attendance records (no indexes), starts the app (which creates them), then checks that
every explain plan has IXSCAN and no COLLSCAN, and prints the latency of each endpoint.
Drops the collections in MONGO_DB first."""
import json, os, random, sys, time
os.environ.setdefault("MONGO_DB", "hrone_perf")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import date, datetime, timedelta, timezone
from fastapi.testclient import TestClient
import app.main as m

m.db.employees.drop(); m.db.attendance_logs.drop()
random.seed(1)
depts = ["Eng", "Ops", "HR", "Sales", "Finance"]
emps = [dict(emp_code=f"EMP{i:04d}", name=f"N{i}", email="e@x.com", department=depts[i % 5], shift_start="09:30",
             shift_end="18:30", joined_on="2025-01-01", created_at=datetime(2025, 1, 1, tzinfo=timezone.utc)) for i in range(1, 401)]
m.db.employees.insert_many(emps)
docs = []
for e in emps:
    for n in range(250):
        d = date(2026, 1, 1) + timedelta(days=n)
        pi = datetime(d.year, d.month, d.day, 4, 0, tzinfo=timezone.utc) + timedelta(minutes=random.randint(0, 60))
        late = random.choice([0, 0, 0, 15, 30])
        docs.append(dict(emp_code=e["emp_code"], date=d.isoformat(), status=random.choice(["PRESENT"] * 8 + ["WFH", "LEAVE"]),
                         punch_in=pi, punch_out=pi + timedelta(hours=9), work_hours=9.0, late_minutes=late, overtime_minutes=0,
                         half_day=False, history=[]))
m.db.attendance_logs.insert_many(docs)
print("seeded", m.db.attendance_logs.count_documents({}), "attendance records")

c = TestClient(m.app).__enter__()
calls = {
    "attendance_list": {},
    "employee_monthly": {"emp_code": "EMP0001", "month": "2026-05"},
    "department_summary": {"month": "2026-05"},
    "late_leaderboard": {"month": "2026-05", "limit": 10, "department": "Eng"},
    "department_trend": {"department": "Eng", "from": "2026-04-01", "to": "2026-06-30"},
}
ok = True
for name, params in calls.items():
    t = time.time(); r = c.get(f"/admin/explain/{name}", params=params); dt = time.time() - t
    s = json.dumps(r.json()); good = r.status_code == 200 and "IXSCAN" in s and "COLLSCAN" not in s
    ok &= good; print(f"explain {name:20s} {r.status_code} IXSCAN={'IXSCAN' in s} COLLSCAN={'COLLSCAN' in s} {dt*1000:.0f}ms {'OK' if good else 'FAIL'}")
for path, params in [("/attendance", {"page": 3}), ("/attendance", {"emp_code": "EMP0001"}), ("/attendance", {"status": "LEAVE", "date_from": "2026-03-01", "date_to": "2026-03-31"}),
                     ("/analytics/employees/EMP0001/monthly", {"month": "2026-05"}), ("/analytics/departments/summary", {"month": "2026-05"}),
                     ("/analytics/leaderboard/late", {"month": "2026-05"}), ("/analytics/departments/Eng/trend", {"from": "2026-04-01", "to": "2026-06-30"})]:
    t = time.time(); r = c.get(path, params=params); print(f"{path:42s} {r.status_code} {(time.time()-t)*1000:.0f}ms")
    ok &= r.status_code == 200
print("ALL OK" if ok else "SOMETHING FAILED")
