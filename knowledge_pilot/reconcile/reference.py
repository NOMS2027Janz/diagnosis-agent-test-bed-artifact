"""The thin shared reference: an identity-only anchor per concept."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReferenceEntry:
    id: str
    label: str
    definition: str = ""
    example: str = ""
    cls: str = ""
    synonyms: tuple[str, ...] = ()


@dataclass
class Reference:
    id: str
    kind: str
    entries: list[ReferenceEntry]

    @property
    def ids(self) -> set[str]:
        return {e.id for e in self.entries}

    @classmethod
    def from_json(cls, path: str | Path) -> "Reference":
        d = json.loads(Path(path).read_text())
        entries = [
            ReferenceEntry(
                id=e["id"],
                label=e.get("label", e["id"]),
                definition=e.get("definition", ""),
                example=e.get("example", ""),
                cls=e.get("class", ""),
                synonyms=tuple(e.get("synonyms", [])),
            )
            for e in d["entries"]
        ]
        return cls(id=d.get("id", ""), kind=d.get("kind", "lexical"), entries=entries)
