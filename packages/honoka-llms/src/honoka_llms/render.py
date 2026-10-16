"""Render an OpenAPI document (as built by honoka-openapi) into llms.txt markdown."""

from typing import cast

type Node = dict[str, object]

_CONSTRAINTS = (
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
    "default",
)


def _node(value: object) -> Node:
    return cast(Node, value) if isinstance(value, dict) else {}


def _operations(document: Node) -> list[tuple[str, str, Node]]:
    return [
        (method, path, _node(op))
        for path, ops in _node(document.get("paths")).items()
        for method, op in _node(ops).items()
    ]


def _pointer(path: str) -> str:
    return path.replace("~", "~0").replace("/", "~1")


def _ref_name(schema: Node) -> str | None:
    ref = schema.get("$ref")
    return str(ref).rsplit("/", 1)[-1] if isinstance(ref, str) else None


def _type_of(schema: Node) -> str:
    name = _ref_name(schema)
    if name is not None:
        return f"`{name}`"
    options = schema.get("anyOf")
    if isinstance(options, list):
        return " | ".join(_type_of(_node(o)) for o in options)
    kind = schema.get("type")
    if kind == "array":
        return f"array of {_type_of(_node(schema.get('items')))}"
    return str(kind) if kind is not None else "any"


def render_index(
    document: Node, *, title: str, description: str | None, spec_path: str, full_path: str
) -> str:
    lines = [f"# {title}", ""]
    if description is not None:
        lines += [f"> {description}", ""]
    lines += ["## Endpoints", ""]
    for method, path, op in _operations(document):
        link = f"{spec_path}#/paths/{_pointer(path)}/{method}"
        summary = op.get("summary")
        suffix = f": {summary}" if isinstance(summary, str) else ""
        lines.append(f"- [{method.upper()} {path}]({link}){suffix}")
    lines += [
        "",
        "## Optional",
        "",
        f"- [OpenAPI document]({spec_path}): every endpoint and schema, machine-readable",
        f"- [Full reference]({full_path}): parameters, bodies and responses of every endpoint",
        "",
    ]
    return "\n".join(lines)


def _fields(schemas: Node, schema: Node) -> list[str]:
    name = _ref_name(schema)
    component = _node(schemas.get(name)) if name is not None else schema
    required = component.get("required")
    required_names = set(required) if isinstance(required, list) else set()
    lines = []
    for field, raw in _node(component.get("properties")).items():
        sub = _node(raw)
        parts = [_type_of(sub), "required" if field in required_names else "optional"]
        parts += [f"{key} {sub[key]}" for key in _CONSTRAINTS if key in sub]
        lines.append(f"  - `{field}`: {', '.join(parts)}")
    return lines


def render_full(document: Node, *, title: str, description: str | None) -> str:
    schemas = _node(_node(document.get("components")).get("schemas"))
    lines = [f"# {title}", ""]
    if description is not None:
        lines += [f"> {description}", ""]
    for method, path, op in _operations(document):
        lines += [f"## {method.upper()} {path}", ""]
        summary, details = op.get("summary"), op.get("description")
        for text in (summary, details):
            if isinstance(text, str):
                lines += [text, ""]
        parameters = op.get("parameters")
        if isinstance(parameters, list) and parameters:
            lines.append("Parameters:")
            for raw in parameters:
                p = _node(raw)
                need = "required" if p.get("required") else "optional"
                lines.append(
                    f"- `{p.get('name')}` ({p.get('in')}, {_type_of(_node(p.get('schema')))}, {need})"
                )
            lines.append("")
        body = _node(op.get("requestBody"))
        for media, content in _node(body.get("content")).items():
            schema = _node(_node(content).get("schema"))
            lines.append(f"Request body ({media}): {_type_of(schema)}")
            lines += _fields(schemas, schema)
            lines.append("")
        lines.append("Responses:")
        for status, raw in _node(op.get("responses")).items():
            response = _node(raw)
            label = str(response.get("description", ""))
            media = next(iter(_node(response.get("content")).values()), None)
            name = _ref_name(_node(_node(media).get("schema"))) if media is not None else None
            lines.append(
                f"- {status}: `{name}`: {label}" if name is not None else f"- {status}: {label}"
            )
        lines.append("")
    return "\n".join(lines)
