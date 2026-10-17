"""Projects belong to one user; nobody else can even tell they exist."""

from taskboard_support import SignIn, body, rows

from honoka.testing import Client


def test_a_project_can_be_created_listed_renamed_and_deleted(
    client: Client, sign_in: SignIn
) -> None:
    ann = sign_in("ann@example.com")
    created = client.post("/projects", json={"name": "Launch"}, headers=ann)
    assert (created.status, body(created)["name"]) == (201, "Launch")

    assert [p["name"] for p in rows(client.get("/projects", headers=ann))] == ["Launch"]
    assert body(client.get("/projects/1", headers=ann))["id"] == 1

    renamed = client.patch("/projects/1", json={"name": "Ship"}, headers=ann)
    assert body(renamed)["name"] == "Ship"
    untouched = client.patch("/projects/1", json={}, headers=ann)
    assert body(untouched)["name"] == "Ship"

    assert client.delete("/projects/1", headers=ann).status == 204
    assert client.get("/projects/1", headers=ann).status == 404
    assert rows(client.get("/projects", headers=ann)) == []


def test_a_blank_name_is_a_validation_error(client: Client, sign_in: SignIn) -> None:
    ann = sign_in("ann@example.com")
    resp = client.post("/projects", json={"name": ""}, headers=ann)
    assert (resp.status, body(resp)["error"]) == (400, "validation_failed")


def test_another_users_project_answers_as_if_it_did_not_exist(
    client: Client, sign_in: SignIn
) -> None:
    ann, bob = sign_in("ann@example.com"), sign_in("bob@example.com")
    client.post("/projects", json={"name": "Private"}, headers=ann)

    assert rows(client.get("/projects", headers=bob)) == []
    for resp in (
        client.get("/projects/1", headers=bob),
        client.patch("/projects/1", json={"name": "Mine now"}, headers=bob),
        client.delete("/projects/1", headers=bob),
    ):
        assert (resp.status, body(resp)) == (404, {"error": "project_not_found"})
    assert body(client.get("/projects/1", headers=ann))["name"] == "Private"


def test_an_id_that_is_not_a_number_is_not_a_project(client: Client, sign_in: SignIn) -> None:
    ann = sign_in("ann@example.com")
    assert client.get("/projects/abc", headers=ann).status == 404
