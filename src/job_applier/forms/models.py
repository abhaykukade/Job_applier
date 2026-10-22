"""A normalized application-form field, whatever site it came from."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

FieldType = Literal[
    "text", "textarea", "email", "phone", "url", "number", "date",
    "select", "multi_select", "radio", "checkbox", "boolean", "file", "location",
]


class Option(BaseModel):
    label: str
    value: str


class FormField(BaseModel):
    key: str  # site-specific field name/id used to find the input
    label: str
    type: FieldType = "text"
    required: bool = False
    options: list[Option] = Field(default_factory=list)
    section: Literal["standard", "custom", "eeo"] = "custom"
    description: str = ""
    # Extra locator hints for the filler (e.g. {"selector": "#first_name"}).
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def option_labels(self) -> list[str]:
        return [o.label for o in self.options]

    @property
    def is_choice(self) -> bool:
        return self.type in ("select", "multi_select", "radio", "checkbox", "boolean") and bool(
            self.options
        )
