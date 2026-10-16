"""Run against a REAL MongoDB:  MONGO_URI=... MONGO_DB=hrone_test python tests/check_analytics.py
Drops the collections in MONGO_DB first, so never point it at data you care about."""
import os, sys
os.environ.setdefault("MONGO_DB", "hrone_test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fastapi.testclient import TestClient
import app.main as m
m.db.employees.drop(); m.db.attendance_logs.drop()
c = TestClient(m.app).__enter__()   # runs startup -> creates indexes
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
random.seed(7)
UTC = timezone.utc
def hu(x, p): return float(Decimal(x).quantize(Decimal(1).scaleb(-p), rounding=ROUND_HALF_UP))
emps = []
def E(code, dept, joined, ss="09:30", se="18:30"):
    emps.append(dict(emp_code=code, name="N"+code, email="e@x.com", department=dept, shift_start=ss, shift_end=se, joined_on=joined, created_at=datetime(2026,1,1,tzinfo=UTC)))
for i in range(1, 9): E(f"EMP{i:04d}", "Eng", "2026-01-05")
E("EMP0010", "Eng", "2026-07-15")      # mid-month joiner
E("EMP0011", "Eng", "2026-08-01")      # joins after July
E("EMP0012", "Ops", "2026-01-05", "22:00", "06:00")
E("EMP0013", "Ops", "2026-03-01")
E("EMP0014", "Ops", "2026-03-01")      # no logs at all
E("EMP0015", "HR", "2026-09-01")       # dept with headcount 0 in July
m.db.employees.insert_many([dict(e) for e in emps])
STAT = ["PRESENT"]*6 + ["WFH","ON_DUTY","ABSENT","LEAVE"]
docs = []
for e in emps:
    if e["emp_code"] in ("EMP0014",): continue
    for n in range(-3, 36):   # a bit before and after July
        d = date(2026,6,28) + timedelta(days=n)
        if random.random() < 0.25: continue
        st = random.choice(STAT)
        r = dict(emp_code=e["emp_code"], date=d.isoformat(), status=st, punch_in=None, punch_out=None, work_hours=None, late_minutes=0, overtime_minutes=0, half_day=False, history=[])
        if st in ("PRESENT","WFH","ON_DUTY"):
            pi = datetime(d.year,d.month,d.day,4,0,tzinfo=UTC) + timedelta(minutes=random.randint(0,90))
            r["punch_in"] = pi
            r["late_minutes"] = random.choice([0,0,0,15,15,30,45,60])
            if random.random() < 0.8:
                wh = round(random.uniform(2, 11), 2)
                r["punch_out"] = pi + timedelta(hours=wh); r["work_hours"] = wh; r["half_day"] = wh < 4.5
                r["overtime_minutes"] = random.choice([0,0,35,40])
        if random.random() < 0.1: r.pop("history"); r.pop("half_day")
        docs.append(r)
# orphan logs + ties
docs.append(dict(emp_code="EMP9999", date="2026-07-07", status="PRESENT", punch_in=datetime(2026,7,7,4,0,tzinfo=UTC), punch_out=None, work_hours=None, late_minutes=999, overtime_minutes=5, half_day=False, history=[]))
m.db.attendance_logs.insert_many(docs)

# ---------------- independent oracle ----------------
emap = {e["emp_code"]: e for e in emps}
PRES = {"PRESENT","WFH","ON_DUTY"}
def mrecs(month): return [d for d in docs if d["date"].startswith(month) and d["emp_code"] in emap]
def wk(ds): return date.fromisoformat(ds).weekday() < 5
def weight(d): return (0.5 if d.get("half_day") else 1.0) if d["status"] in PRES and wk(d["date"]) else 0.0
def month_end(mo):
    y, mm = int(mo[:4]), int(mo[5:]); 
    return (date(y+(mm==12), mm % 12 + 1, 1) - timedelta(days=1))
def oracle_monthly(code, mo):
    e = emap[code]; first = date.fromisoformat(mo+"-01"); last = month_end(mo)
    start = max(first, date.fromisoformat(e["joined_on"]))
    wd = sum(1 for i in range((last-start).days+1) if (start+timedelta(days=i)).weekday() < 5) if start <= last else 0
    rs = [d for d in docs if d["emp_code"]==code and d["date"].startswith(mo)]
    pd_ = sum(weight(d) for d in rs)
    return dict(emp_code=code, month=mo, working_days=wd, present_days=pd_, leave_days=sum(d["status"]=="LEAVE" for d in rs),
        late_count=sum(d["late_minutes"]>0 for d in rs), total_late_minutes=sum(d["late_minutes"] for d in rs),
        total_overtime_minutes=sum(d["overtime_minutes"] for d in rs), attendance_pct=hu(Decimal(pd_)/wd*100,2) if wd else None)
def oracle_summary(mo, dept=None):
    last = month_end(mo); out = []
    for dp in sorted({e["department"] for e in emps}):
        if dept and dp != dept: continue
        hc = sum(1 for e in emps if e["department"]==dp and date.fromisoformat(e["joined_on"]) <= last)
        if hc == 0: continue
        rs = [d for d in mrecs(mo) if emap[d["emp_code"]]["department"]==dp]
        whs = [d["work_hours"] for d in rs if d["status"] in PRES and d.get("work_hours") is not None]
        out.append(dict(department=dp, headcount=hc, present_days=sum(weight(d) for d in rs),
            avg_work_hours=hu(Decimal(str(sum(Decimal(str(w)) for w in whs)))/len(whs),2) if whs else None,
            late_count=sum(d["late_minutes"]>0 for d in rs), total_late_minutes=sum(d["late_minutes"] for d in rs),
            leave_count=sum(d["status"]=="LEAVE" for d in rs), on_duty_count=sum(d["status"]=="ON_DUTY" for d in rs)))
    return out
def oracle_board(mo, limit, dept=None):
    tot = {}
    for d in mrecs(mo):
        if dept and emap[d["emp_code"]]["department"] != dept: continue
        t = tot.setdefault(d["emp_code"], [0,0]); t[0] += d["late_minutes"]; t[1] += d["late_minutes"]>0
    rows = sorted(((c,t) for c,t in tot.items() if t[0]>0), key=lambda x:(-x[1][0], x[0]))
    out = []
    for i,(c,t) in enumerate(rows):
        rank = 1 + sum(1 for _,u in rows if u[0] > t[0])
        if rank <= limit: out.append(dict(rank=rank, emp_code=c, name=emap[c]["name"], department=emap[c]["department"], total_late_minutes=t[0], late_count=t[1]))
    return out
def oracle_trend(dept, f, t):
    f, t = date.fromisoformat(f), date.fromisoformat(t); rows = []; rates = []
    for i in range((t-f).days+1):
        d = f + timedelta(days=i); ds = d.isoformat()
        hc = sum(1 for e in emps if e["department"]==dept and date.fromisoformat(e["joined_on"]) <= d)
        rs = [x for x in docs if x["date"]==ds and x["emp_code"] in emap and emap[x["emp_code"]]["department"]==dept]
        pc = sum((0.5 if x.get("half_day") else 1.0) for x in rs if x["status"] in PRES)
        wd = d.weekday() < 5
        rate = hu(Decimal(pc)/hc, 4) if wd and hc > 0 else None
        rates.append(rate); w = [r for r in rates[-7:] if r is not None]
        rows.append(dict(date=ds, is_working_day=wd, headcount=hc, present_count=pc, late_count=sum(x["late_minutes"]>0 for x in rs), attendance_rate=rate,
            moving_avg_7d=hu(Decimal(str(sum(Decimal(str(r)) for r in w)))/len(w),4) if w else None))
    return rows

bad = 0
def chk(name, got, exp):
    global bad
    if got != exp:
        bad += 1; print("MISMATCH", name); print(" got", got); print(" exp", exp)
for mo in ("2026-07", "2026-06", "2026-08", "2026-12"):
    for code in emap:
        r = c.get(f"/analytics/employees/{code}/monthly", params={"month": mo}); assert r.status_code == 200, r.text
        chk(f"monthly {code} {mo}", r.json(), oracle_monthly(code, mo))
    for dept in (None, "Eng", "Ops", "HR", "Nope"):
        r = c.get("/analytics/departments/summary", params={"month": mo, **({"department": dept} if dept else {})}); assert r.status_code == 200, r.text
        chk(f"summary {mo} {dept}", r.json()["items"], oracle_summary(mo, dept))
        for lim in (1, 3, 10, 50):
            r = c.get("/analytics/leaderboard/late", params={"month": mo, "limit": lim, **({"department": dept} if dept else {})}); assert r.status_code == 200, r.text
            chk(f"board {mo} {dept} {lim}", r.json()["items"], oracle_board(mo, lim, dept))
for dept in ("Eng", "Ops", "HR"):
    for f, t in (("2026-07-01","2026-07-31"), ("2026-06-25","2026-07-20"), ("2026-07-13","2026-07-13"), ("2026-07-30","2026-09-02"), ("2026-08-28","2026-09-03")):
        r = c.get(f"/analytics/departments/{dept}/trend", params={"from": f, "to": t}); assert r.status_code == 200, r.text
        chk(f"trend {dept} {f} {t}", r.json()["items"], oracle_trend(dept, f, t))
print("ties in July board:", [x["rank"] for x in c.get("/analytics/leaderboard/late", params={"month":"2026-07","limit":50}).json()["items"]][:15])
print("trend errors:", c.get("/analytics/departments/Nope/trend", params={"from":"2026-07-01","to":"2026-07-05"}).status_code, c.get("/analytics/departments/Eng/trend", params={"from":"2026-07-05","to":"2026-07-01"}).status_code, c.get("/analytics/departments/Eng/trend", params={"from":"2026-01-01","to":"2026-04-02"}).status_code, c.get("/analytics/departments/Eng/trend", params={"from":"2026-07-01","to":"2026-09-30"}).status_code)
print("monthly 404:", c.get("/analytics/employees/EMP7777/monthly", params={"month":"2026-07"}).status_code, c.get("/analytics/employees/EMP0001/monthly", params={"month":"2026-13"}).status_code)
print("DONE mismatches:", bad)
