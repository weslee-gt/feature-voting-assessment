def test_list_requests_all(client):
    resp = client.get("/requests")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 12
    assert len(data["items"]) == 12


def test_list_filter_q(client):
    resp = client.get("/requests", params={"q": "csv"})
    data = resp.json()
    assert data["total"] == 1
    assert "CSV" in data["items"][0]["title"]


def test_list_sort_support_default(client):
    resp = client.get("/requests")
    items = resp.json()["items"]
    assert items[0]["support"] >= items[1]["support"]


def test_list_sort_newest(client):
    resp = client.get("/requests", params={"sort": "newest"})
    items = resp.json()["items"]
    assert items[0]["createdAt"] >= items[1]["createdAt"]


def test_list_invalid_status_returns_empty(client):
    resp = client.get("/requests", params={"status": "bogus"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_list_anonymous_voted_by_me_false(client):
    items = client.get("/requests").json()["items"]
    assert all(item["votedByMe"] is False for item in items)


def test_get_request_detail_shape(client):
    resp = client.get("/requests/req-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "released"
    assert data["officialResponse"] is not None
    assert len(data["comments"]) == 3
    assert len(data["activity"]) == 5
    assert data["mergedFrom"][0]["id"] == "req-10"
    assert data["canVote"] is False


def test_get_request_redirected(client):
    data = client.get("/requests/req-10").json()
    assert data["status"] == "redirected"
    assert data["mergedInto"] == "req-1"
    assert data["mergedIntoRequest"]["id"] == "req-1"
    assert data["canVote"] is False


def test_get_request_missing(client):
    resp = client.get("/requests/req-999")
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_create_request(client, login):
    headers = login("sam@example.com")
    resp = client.post(
        "/requests", json={"title": "New idea", "description": "A longer description of the idea"},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "under_review"
    assert data["author"]["id"] == "u-2"
    assert data["canVote"] is True
    assert data["title"] == "New idea"


def test_create_request_requires_auth(client):
    resp = client.post("/requests", json={"title": "x"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


def test_create_request_empty_title(client, login):
    headers = login("sam@example.com")
    resp = client.post("/requests", json={"title": "   "}, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID"


def test_create_request_short_description(client, login):
    headers = login("sam@example.com")
    resp = client.post(
        "/requests",
        json={"title": "Valid title", "description": "too short"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID"
    assert "Description" in resp.json()["message"]


def test_update_request_short_description(client, login):
    headers = login("sam@example.com")
    resp = client.patch(
        "/requests/req-1",
        json={"title": "Renamed", "description": "short"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID"


def test_create_request_id_continues_sequence(client, login):
    headers = login("sam@example.com")
    data = client.post(
        "/requests",
        json={"title": "seq", "description": "Sequence check description"},
        headers=headers,
    ).json()
    assert data["id"] == "req-13"


def test_update_request_author(client, login):
    headers = login("sam@example.com")
    resp = client.patch(
        "/requests/req-1",
        json={"title": "Renamed", "description": "new description text"},
        headers=headers,
    )
    assert resp.json()["title"] == "Renamed"
    assert resp.json()["description"] == "new description text"


def test_update_request_non_owner_forbidden(client, login):
    headers = login("alex@example.com")
    resp = client.patch("/requests/req-1", json={"title": "hack"}, headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"


def test_update_request_admin_any(client, login):
    headers = login("lee@example.com")
    resp = client.patch(
        "/requests/req-1",
        json={"title": "admin edit", "description": "Edited by an admin user"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "admin edit"


def test_delete_request_cascades(client, login):
    headers = login("lee@example.com")
    before = client.get("/requests/req-4").json()
    assert before["support"] == 6
    resp = client.delete("/requests/req-4", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"id": "req-4"}
    assert client.get("/requests/req-4").status_code == 404
    assert client.get("/requests").json()["total"] == 11


def test_delete_request_non_owner_forbidden(client, login):
    headers = login("alex@example.com")
    resp = client.delete("/requests/req-1", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"