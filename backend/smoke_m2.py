"""Manual M2 smoke check against the live server. Not part of the pytest suite."""

import httpx

BASE = "http://127.0.0.1:8000"


def show(label, value):
    print(f"{label}: {value}")


with httpx.Client(base_url=BASE, timeout=20.0) as c:
    r = c.post("/api/trips", json={"title": "上海三日游", "city": "上海"})
    r.raise_for_status()
    trip = r.json()
    show("POST /api/trips", trip)
    trip_id, day_id = trip["trip_id"], trip["day_id"]

    r = c.get(f"/api/trips/{trip_id}")
    snap = r.json()
    show("snapshot days", [(d["day_index"], d["title"]) for d in snap["days"]])

    ids = []
    for name in ("外滩", "南京路"):
        r = c.post(f"/api/trips/{trip_id}/days/{day_id}/places",
                   json={"name": name, "lng": 121.49, "lat": 31.24})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    # insert into the middle -- the transient UNIQUE collision case
    r = c.post(f"/api/trips/{trip_id}/days/{day_id}/places",
               json={"name": "豫园", "lng": 121.4925, "lat": 31.2272,
                     "after_place_id": ids[0]})
    assert r.status_code == 201, r.text
    ids.insert(1, r.json()["id"])

    snap = c.get(f"/api/trips/{trip_id}").json()
    show("after adds", [(p["sort_index"], p["name"], p["locked"], p["status"]) for p in snap["places"]])
    show("trip_id denormalised", {p["trip_id"] == trip_id for p in snap["places"]})

    r = c.delete(f"/api/trips/{trip_id}/places/{ids[1]}")
    show("DELETE place", r.json())
    snap = c.get(f"/api/trips/{trip_id}").json()
    show("after delete", [(p["sort_index"], p["name"]) for p in snap["places"]])

    for name in ("小明", "小明改名"):
        r = c.post(f"/api/trips/{trip_id}/participants",
                   json={"client_id": "c-1", "name": name, "color": "#E8564A"})
        assert r.status_code == 200, r.text
    snap = c.get(f"/api/trips/{trip_id}").json()
    show("participants", [(p["client_id"], p["name"]) for p in snap["participants"]])

    show("GET unknown trip", c.get("/api/trips/ZZZZZZZZ").status_code)

    # No key configured: must surface the Chinese hint, not a stack trace.
    r = c.get("/api/poi/tips", params={"keyword": "外滩"})
    show("GET /api/poi/tips status", r.status_code)
    show("GET /api/poi/tips body", r.json())
