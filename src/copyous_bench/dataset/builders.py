"""Strategy objects that build the content of one clipboard entry per type."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, NamedTuple

from .templates import CHARACTERS, CODE_TEMPLATES, LANGUAGES, TEXT_TEMPLATES

TOKEN = "benchmark"


class BuiltContent(NamedTuple):
    content: str
    metadata: dict[str, Any] | None = None


class EntryBuilder(ABC):
    """Builds the content of entries of one type.

    Builders may be stateful (rotating through languages or characters), so a
    fresh set must be created for every master-list generation.
    """

    type_name: ClassVar[str]

    def __init__(self, token: str = TOKEN) -> None:
        self.token = token

    @abstractmethod
    def build(self, index: int, has_token: bool) -> BuiltContent:  # pragma: no cover
        ...


class TextBuilder(EntryBuilder):
    type_name = "Text"

    def build(self, index: int, has_token: bool) -> BuiltContent:
        template = TEXT_TEMPLATES[index % len(TEXT_TEMPLATES)]
        content = f"{template}\n[#{index:04d}]"
        if has_token:
            content = f"{self.token} note {index:04d}: {content}"
        return BuiltContent(content)


class CodeBuilder(EntryBuilder):
    type_name = "Code"

    def __init__(self, token: str = TOKEN) -> None:
        super().__init__(token)
        self._count = 0

    def build(self, index: int, has_token: bool) -> BuiltContent:
        language_id, language_name = LANGUAGES[self._count % len(LANGUAGES)]
        self._count += 1
        marker = f"{self.token} " if has_token else ""
        content = f"// {marker}sample {index:04d}\n{CODE_TEMPLATES[language_id]}"
        return BuiltContent(content, {"language": {"id": language_id, "name": language_name}})


class LinkBuilder(EntryBuilder):
    type_name = "Link"

    def build(self, index: int, has_token: bool) -> BuiltContent:
        slug = self.token if has_token else "article"
        metadata = {
            "title": f"{'Benchmark' if has_token else 'Article'} notes {index:04d}",
            "description": f"A short description for link {index:04d} used by the clipboard dataset.",
            "image": None,
        }
        return BuiltContent(f"https://example.com/{slug}/{index:04d}", metadata)


class ColorBuilder(EntryBuilder):
    type_name = "Color"

    def build(self, index: int, has_token: bool) -> BuiltContent:
        return BuiltContent(f"#{(index * 2654435761) & 0xFFFFFF:06x}")


class CharacterBuilder(EntryBuilder):
    type_name = "Character"

    def __init__(self, token: str = TOKEN) -> None:
        super().__init__(token)
        self._count = 0

    def build(self, index: int, has_token: bool) -> BuiltContent:
        character = CHARACTERS[self._count % len(CHARACTERS)]
        self._count += 1
        return BuiltContent(character)


def default_builders(token: str = TOKEN) -> dict[str, EntryBuilder]:
    classes = (TextBuilder, CodeBuilder, LinkBuilder, ColorBuilder, CharacterBuilder)
    return {cls.type_name: cls(token) for cls in classes}
