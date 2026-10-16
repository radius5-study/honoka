"""Form bodies: multipart parsing with the standard library, and dotted-key nesting."""

from dataclasses import dataclass
from email.message import Message
from email.parser import BytesParser
from email.policy import HTTP
from typing import cast


@dataclass(frozen=True, slots=True)
class UploadedFile:
    """A file part of a multipart form."""

    filename: str
    content_type: str
    data: bytes


type Leaf = str | UploadedFile
type FormValue = Leaf | list[Leaf] | dict[str, FormValue]


def parse_multipart(content_type: str, body: bytes) -> list[tuple[str, Leaf]]:
    head = f"Content-Type: {content_type}\r\n\r\n".encode()
    message = BytesParser(policy=HTTP).parsebytes(head + body)
    fields: list[tuple[str, Leaf]] = []
    for part in message.iter_parts():
        name = _field_name(part)
        if name is None:
            continue
        data = cast(bytes, part.get_payload(decode=True) or b"")
        filename = part.get_filename()
        if filename is None:
            fields.append((name, data.decode(part.get_content_charset() or "utf-8")))
        else:
            fields.append((name, UploadedFile(filename, part.get_content_type(), data)))
    return fields


def _field_name(part: Message) -> str | None:
    value = part.get_param("name", header="content-disposition")
    return value if isinstance(value, str) else None


def nest_dotted(flat: dict[str, FormValue]) -> dict[str, FormValue]:
    """`{"a.b": 1}` -> `{"a": {"b": 1}}`."""
    nested: dict[str, FormValue] = {}
    for key, value in flat.items():
        *parents, leaf = key.split(".")
        target = nested
        for parent in parents:
            child = target.setdefault(parent, {})
            if not isinstance(child, dict):
                child = target[parent] = {}
            target = child
        target[leaf] = value
    return nested
