"""Projects: the user's own, and nothing else."""

from honoka import App, Context, HTTPError, Json, Response
from honoka_msgspec import validator
from taskboard import repo
from taskboard.deps import BUS, DB, current_user_id
from taskboard.models import ErrorBody, ProjectIn, ProjectOut, ProjectPatch

NOT_FOUND = {404: (ErrorBody, "No such project")}


async def list_projects(c: Context) -> Json[list[ProjectOut]]:
    """Every project of the signed-in user."""
    owner = current_user_id(c)
    return c.json(await c.get(DB).run(lambda conn: repo.list_projects(conn, owner)))


async def create_project(c: Context) -> Json[ProjectOut]:
    owner, data = current_user_id(c), c.req.valid(ProjectIn)
    project = await c.get(DB).run(lambda conn: repo.create_project(conn, owner, data.name))
    return c.json(project, 201)


async def get_project(c: Context) -> Json[ProjectOut]:
    owner, project_id = current_user_id(c), c.req.param("project_id", int)
    project = await c.get(DB).run(lambda conn: repo.get_project(conn, owner, project_id))
    if project is None:
        raise HTTPError(404, "project_not_found")
    return c.json(project)


async def update_project(c: Context) -> Json[ProjectOut]:
    owner, project_id = current_user_id(c), c.req.param("project_id", int)
    patch = c.req.valid(ProjectPatch)
    db = c.get(DB)
    if isinstance(patch.name, str):
        name = patch.name
        project = await db.run(lambda conn: repo.rename_project(conn, owner, project_id, name))
    else:
        project = await db.run(lambda conn: repo.get_project(conn, owner, project_id))
    if project is None:
        raise HTTPError(404, "project_not_found")
    return c.json(project)


async def delete_project(c: Context) -> Response:
    """Deleting a project deletes its tasks."""
    owner, project_id = current_user_id(c), c.req.param("project_id", int)
    deleted = await c.get(DB).run(lambda conn: repo.delete_project(conn, owner, project_id))
    if not deleted:
        raise HTTPError(404, "project_not_found")
    c.get(BUS).close(project_id)  # whoever is watching the project stops hearing from it
    return c.body(b"", 204)


def router() -> App:
    app = App()
    app.get("/", list_projects)
    app.post("/", validator(json=ProjectIn), create_project, status=201)
    app.get("/{project_id:int}", get_project, responses=NOT_FOUND)
    app.patch(
        "/{project_id:int}", validator(json=ProjectPatch), update_project, responses=NOT_FOUND
    )
    app.delete("/{project_id:int}", delete_project, status=204, responses=NOT_FOUND)
    return app
