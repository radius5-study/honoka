"""Tasks: validation, filtering, paging, partial updates and concurrent edits."""

from taskboard_support import SignIn, body

from honoka.testing import Client


def project(client: Client, headers: dict[str, str]) -> str:
    resp = client.post("/projects", json={"name": "P"}, headers=headers)
    return f"/projects/{body(resp)['id']}/tasks"


def test_a_new_task_gets_defaults_and_a_location(client: Client, sign_in: SignIn) -> None:
    ann = sign_in("ann@example.com")
    tasks = project(client, ann)
    resp = client.post(tasks, json={"title": "Write docs"}, headers=ann)
    assert resp.status == 201
    assert resp.headers["location"] == f"{tasks}/1"
    task = body(resp)
    assert (task["status"], task["priority"], task["due"], task["version"]) == ("todo", 2, None, 1)


def test_invalid_tasks_are_refused_with_the_offending_field(
    client: Client, sign_in: SignIn
) -> None:
    ann = sign_in("ann@example.com")
    tasks = project(client, ann)
    for bad, field in (
        ({"title": ""}, "title"),
        ({"title": "x", "status": "someday"}, "status"),
        ({"title": "x", "priority": 9}, "priority"),
        ({"title": "x", "due": "tomorrow"}, "due"),
    ):
        resp = client.post(tasks, json=bad, headers=ann)
        assert resp.status == 400, bad
        fields = body(resp)["fields"]
        assert isinstance(fields, list) and fields[0]["path"] == field, bad
    assert client.post(tasks, content=b"{not json", headers=ann).status == 400


def test_listing_filters_searches_and_pages(client: Client, sign_in: SignIn) -> None:
    ann = sign_in("ann@example.com")
    tasks = project(client, ann)
    for title, status in (("Buy milk", "todo"), ("Buy 100% oat", "doing"), ("Call Bob", "done")):
        client.post(tasks, json={"title": title, "status": status}, headers=ann)

    def titles(query: str) -> list[object]:
        page = body(client.get(f"{tasks}?{query}", headers=ann))
        items = page["items"]
        assert isinstance(items, list)
        return [item["title"] for item in items]

    assert titles("") == ["Buy milk", "Buy 100% oat", "Call Bob"]
    assert titles("status=done") == ["Call Bob"]
    assert titles("q=buy") == ["Buy milk", "Buy 100% oat"]
    assert titles("q=%25") == ["Buy 100% oat"]  # a literal percent sign, not a wildcard
    page = body(client.get(f"{tasks}?limit=2&offset=1", headers=ann))
    assert [page["total"], page["limit"], page["offset"]] == [3, 2, 1]
    assert titles("limit=2&offset=1") == ["Buy 100% oat", "Call Bob"]
    for bad in ("limit=0", "limit=101", "offset=-1", "status=nope"):
        assert client.get(f"{tasks}?{bad}", headers=ann).status == 400, bad


def test_a_patch_changes_only_what_it_sends(client: Client, sign_in: SignIn) -> None:
    ann = sign_in("ann@example.com")
    tasks = project(client, ann)
    client.post(
        tasks, json={"title": "Draft", "description": "keep", "due": "2026-12-01"}, headers=ann
    )

    done = body(client.patch(f"{tasks}/1", json={"status": "done"}, headers=ann))
    assert (done["status"], done["title"], done["description"], done["due"]) == (
        "done",
        "Draft",
        "keep",
        "2026-12-01",
    )
    assert done["version"] == 2
    cleared = body(client.patch(f"{tasks}/1", json={"due": None}, headers=ann))
    assert (cleared["due"], cleared["version"]) == (None, 3)


def test_a_stale_version_is_refused_and_changes_nothing(client: Client, sign_in: SignIn) -> None:
    ann = sign_in("ann@example.com")
    tasks = project(client, ann)
    client.post(tasks, json={"title": "Shared"}, headers=ann)
    client.patch(f"{tasks}/1", json={"title": "Edited by me", "version": 1}, headers=ann)

    stale = client.patch(f"{tasks}/1", json={"title": "Edited by you", "version": 1}, headers=ann)
    assert (stale.status, body(stale)["error"]) == (409, "version_conflict")
    assert body(client.get(f"{tasks}/1", headers=ann))["title"] == "Edited by me"
    current = client.patch(f"{tasks}/1", json={"title": "Third", "version": 2}, headers=ann)
    assert body(current)["title"] == "Third"


def test_missing_tasks_are_404_for_every_verb(client: Client, sign_in: SignIn) -> None:
    ann = sign_in("ann@example.com")
    tasks = project(client, ann)
    for resp in (
        client.get(f"{tasks}/9", headers=ann),
        client.patch(f"{tasks}/9", json={"title": "x"}, headers=ann),
        client.delete(f"{tasks}/9", headers=ann),
    ):
        assert (resp.status, body(resp)["error"]) == (404, "task_not_found")


def test_deleting_a_task_and_deleting_its_project(client: Client, sign_in: SignIn) -> None:
    ann = sign_in("ann@example.com")
    tasks = project(client, ann)
    client.post(tasks, json={"title": "a"}, headers=ann)
    client.post(tasks, json={"title": "b"}, headers=ann)

    assert client.delete(f"{tasks}/1", headers=ann).status == 204
    assert client.get(f"{tasks}/1", headers=ann).status == 404
    assert body(client.get(tasks, headers=ann))["total"] == 1

    client.delete("/projects/1", headers=ann)
    assert client.get(f"{tasks}/2", headers=ann).status == 404  # the project went, so did its tasks


def test_nobody_reaches_tasks_through_a_project_that_is_not_theirs(
    client: Client, sign_in: SignIn
) -> None:
    ann, bob = sign_in("ann@example.com"), sign_in("bob@example.com")
    tasks = project(client, ann)
    client.post(tasks, json={"title": "secret"}, headers=ann)

    attempts = (
        client.get(tasks, headers=bob),
        client.post(tasks, json={"title": "planted"}, headers=bob),
        client.get(f"{tasks}/1", headers=bob),
        client.patch(f"{tasks}/1", json={"title": "changed"}, headers=bob),
        client.delete(f"{tasks}/1", headers=bob),
    )
    assert [r.status for r in attempts] == [404] * 5
    assert body(client.get(tasks, headers=ann))["total"] == 1
    assert body(client.get(f"{tasks}/1", headers=ann))["title"] == "secret"


def test_a_task_is_served_with_an_etag_that_answers_304(client: Client, sign_in: SignIn) -> None:
    ann = sign_in("ann@example.com")
    tasks = project(client, ann)
    client.post(tasks, json={"title": "cache me"}, headers=ann)
    first = client.get(f"{tasks}/1", headers=ann)
    tag = first.headers["etag"]
    again = client.get(f"{tasks}/1", headers={**ann, "if-none-match": tag})
    assert (again.status, again.text) == (304, "")
    client.patch(f"{tasks}/1", json={"title": "changed"}, headers=ann)
    assert client.get(f"{tasks}/1", headers={**ann, "if-none-match": tag}).status == 200
