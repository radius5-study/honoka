"""Build an OpenAPI 3.1 document from an app's route table."""

import inspect
import re
import typing
from collections.abc import Mapping

import msgspec

from honoka import App, Json
from honoka.router import RouteInfo

_SKIPPED_METHODS = frozenset({"HEAD", "OPTIONS"})
_PARAM_LOCATIONS = {"query": "query", "header": "header", "cookie": "cookie", "param": "path"}
_REF = "#/components/schemas/{name}"
_VALIDATION_FAILED: dict[str, object] = {
    "type": "object",
    "required": ["error", "fields"],
    "properties": {
        "error": {"type": "string", "const": "validation_failed"},
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["in", "path", "expected"],
                "properties": {
                    "in": {"type": "string"},
                    "path": {"type": "string"},
                    "expected": {"type": "string"},
                    "received": {},
                },
            },
        },
        "schema": {"type": "string"},
    },
}


def _validator_schemas(route: RouteInfo) -> dict[str, object]:
    """The `schemas` of every validator-like middleware given to the route."""
    found: dict[str, object] = {}
    for mw in route.middleware:
        schemas = getattr(mw, "schemas", None)
        if isinstance(schemas, dict):
            found.update({str(k): v for k, v in schemas.items()})
    return found


def _success_type(route: RouteInfo) -> object | None:
    try:
        annotation = typing.get_type_hints(route.handler).get("return")
    except (NameError, TypeError):
        return None
    if typing.get_origin(annotation) is Json:
        args = typing.get_args(annotation)
        return args[0] if args else None
    return None


def _declared_responses(route: RouteInfo) -> dict[int, tuple[object, str]]:
    declared = route.meta.get("responses")
    out: dict[int, tuple[object, str]] = {}
    if isinstance(declared, Mapping):
        for status, value in declared.items():
            if isinstance(value, tuple):
                out[int(status)] = (value[0], str(value[1]))
            else:
                out[int(status)] = (value, "Error")
    return out


def _summary_and_description(handler: object) -> tuple[str | None, str | None]:
    doc = inspect.getdoc(handler)
    if doc is None or doc == "":
        return None, None
    first, _, rest = doc.partition("\n")
    rest = rest.strip()
    return first.strip(), (rest if rest != "" else None)


def _is_struct(tp: object) -> bool:
    return isinstance(tp, type) and issubclass(tp, msgspec.Struct)


def build(app: App, *, title: str, version: str) -> dict[str, object]:
    routes = [
        r
        for r in app.route_table()
        if r.meta.get("openapi") is not False and r.method not in _SKIPPED_METHODS
    ]

    # Pass 1: every type that needs a schema, so msgspec can name and share the components.
    types: list[object] = []
    param_only: set[object] = set()
    referenced: set[object] = set()

    def want(tp: object, *, as_parameters: bool = False) -> None:
        if tp not in types:
            types.append(tp)
        (param_only if as_parameters else referenced).add(tp)

    for route in routes:
        for target, schema in _validator_schemas(route).items():
            want(schema, as_parameters=target in _PARAM_LOCATIONS)
        returned = _success_type(route)
        if returned is not None:
            want(returned)
        for declared, _ in _declared_responses(route).values():
            want(declared)

    refs, components = (
        msgspec.json.schema_components(types, ref_template=_REF) if types else ((), {})
    )
    schema_of = dict(zip(types, refs, strict=True))

    def parameters_of(struct: object, location: str) -> list[dict[str, object]]:
        ref = schema_of[struct]
        name = str(ref["$ref"]).rsplit("/", 1)[-1] if "$ref" in ref else ""
        component = components.get(name, {})
        required = set(component.get("required", []))
        return [
            {
                "name": prop.replace("_", "-") if location == "header" else prop,
                "in": location,
                "required": prop in required,
                "schema": subschema,
            }
            for prop, subschema in component.get("properties", {}).items()
        ]

    paths: dict[str, dict[str, object]] = {}
    seen_ids: set[str] = set()
    uses_validation_failed = False
    for route in routes:
        schemas = _validator_schemas(route)
        operation: dict[str, object] = {}
        op_id = getattr(route.handler, "__name__", route.method.lower())
        if op_id in seen_ids:
            op_id = f"{op_id}_{route.method.lower()}"
        seen_ids.add(op_id)
        operation["operationId"] = op_id
        summary, description = _summary_and_description(route.handler)
        if summary is not None and summary != "":
            operation["summary"] = summary
        if description is not None:
            operation["description"] = description

        parameters: list[dict[str, object]] = []
        path_names = re.findall(r"\{(\w+)\}", route.path)
        documented_path: set[str] = set()
        for target, location in _PARAM_LOCATIONS.items():
            if target in schemas:
                found = parameters_of(schemas[target], location)
                parameters.extend(found)
                if location == "path":
                    documented_path = {str(p["name"]) for p in found}
        parameters = [
            *(
                {"name": n, "in": "path", "required": True, "schema": {"type": "string"}}
                for n in path_names
                if n not in documented_path
            ),
            *parameters,
        ]
        if parameters:
            operation["parameters"] = parameters

        for target, media in (
            ("json", "application/json"),
            ("form", "application/x-www-form-urlencoded"),
        ):
            if target in schemas:
                operation["requestBody"] = {
                    "required": True,
                    "content": {media: {"schema": schema_of[schemas[target]]}},
                }

        responses: dict[str, object] = {}
        success_status = route.meta.get("status", 200)
        success: dict[str, object] = {"description": "OK"}
        success_type = _success_type(route)
        if success_type is not None:
            success["content"] = {"application/json": {"schema": schema_of[success_type]}}
        responses[str(success_status)] = success
        if schemas:
            uses_validation_failed = True
            responses["400"] = {
                "description": "Validation failed",
                "content": {
                    "application/json": {"schema": {"$ref": _REF.format(name="ValidationFailed")}}
                },
            }
        for status, (declared, text) in _declared_responses(route).items():
            responses[str(status)] = {
                "description": text,
                "content": {"application/json": {"schema": schema_of[declared]}},
            }
        operation["responses"] = responses
        paths.setdefault(route.path, {})[route.method.lower()] = operation

    keep = {
        name: schema
        for name, schema in components.items()
        if not any(
            "$ref" in schema_of[tp] and str(schema_of[tp]["$ref"]).endswith(f"/{name}")
            for tp in param_only - referenced
        )
    }
    if uses_validation_failed:
        keep["ValidationFailed"] = _VALIDATION_FAILED
    document: dict[str, object] = {
        "openapi": "3.1.0",
        "info": {"title": title, "version": version},
        "paths": paths,
    }
    if keep:
        document["components"] = {"schemas": keep}
    return document
