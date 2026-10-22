"""Extract application-form fields from plain HTML (Lever pages, or any rendered page)."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from job_applier.forms.models import FieldType, FormField, Option

_SKIP_TYPES = {"hidden", "submit", "button", "reset", "image", "search", "password"}
_REQUIRED_MARK = re.compile(r"[\s✱*]+$")
_CONTAINERS = {"application-question", "form-group", "field", "question", "form-field"}


def _clean(text: str) -> str:
    return _REQUIRED_MARK.sub("", re.sub(r"\s+", " ", text)).strip()


def _container(el: Tag) -> Tag | None:
    """The element wrapping one whole question (label + inputs)."""
    fallback = None
    for parent in el.parents:
        if not isinstance(parent, Tag) or parent.name in ("form", "body"):
            break
        if set(parent.get("class", [])) & _CONTAINERS:
            return parent
        if fallback is None and parent.name == "fieldset":
            fallback = parent
        if fallback is None and parent.name == "li" and parent.select_one("legend, .application-label, .label"):
            fallback = parent
    return fallback


def _label_for(soup: BeautifulSoup, el: Tag) -> tuple[str, bool]:
    """Return (label text, required marker seen)."""
    candidates: list[Tag] = []
    if el.get("id"):
        candidates += soup.find_all("label", attrs={"for": el["id"]})
    box = _container(el)
    if box is not None:
        for sel in (".application-label", "legend", "label", ".label"):
            found = box.select_one(sel)
            if found is not None:
                candidates.append(found)
                break
    wrapping = el.find_parent("label")
    if wrapping is not None and el.get("type") not in ("radio", "checkbox"):
        candidates.append(wrapping)
    for cand in candidates:
        raw = cand.get_text(" ", strip=True)
        text = _clean(raw)
        if text:
            required = "✱" in raw or "*" in raw or cand.select_one(".required") is not None
            return text, required
    for attr in ("aria-label", "placeholder", "name"):
        if el.get(attr):
            return _clean(str(el[attr])), False
    return "", False


def _option_label(soup: BeautifulSoup, el: Tag) -> str:
    if el.get("id"):
        lab = soup.find("label", attrs={"for": el["id"]})
        if lab is not None:
            return _clean(lab.get_text(" ", strip=True))
    parent = el.find_parent("label")
    if parent is not None:
        return _clean(parent.get_text(" ", strip=True))
    sibling = el.find_next_sibling(string=True)
    return _clean(str(sibling)) if sibling else str(el.get("value", ""))


def _section(name: str, label: str) -> str:
    if name.startswith("eeo") or re.search(r"\b(gender|race|ethnicity|veteran|disability)\b", label, re.I):
        return "eeo"
    if name.startswith("cards[") or name.startswith("question"):
        return "custom"
    return "standard"


def parse_form(html: str, form_selector: str | None = None) -> list[FormField]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(form_selector) if form_selector else soup.find("form")
    root = root or soup
    fields: dict[str, FormField] = {}

    for el in root.find_all(["input", "select", "textarea"]):
        name = el.get("name") or el.get("id")
        if not name:
            continue
        input_type = (el.get("type") or "text").lower() if el.name == "input" else el.name
        if input_type in _SKIP_TYPES:
            continue
        required_attr = el.has_attr("required") or el.get("aria-required") == "true"

        if input_type in ("radio", "checkbox"):
            group_label, req = _group_label(soup, el)
            field = fields.get(name)
            if field is None:
                ftype: FieldType = "radio" if input_type == "radio" else "checkbox"
                field = FormField(
                    key=name, label=group_label, type=ftype, required=required_attr or req,
                    section=_section(name, group_label),  # type: ignore[arg-type]
                )
                fields[name] = field
            field.options.append(Option(label=_option_label(soup, el), value=str(el.get("value", "on"))))
            field.required = field.required or required_attr
            continue

        label, req = _label_for(soup, el)
        if el.name == "select":
            ftype = "multi_select" if el.has_attr("multiple") else "select"
            options = [
                Option(label=_clean(o.get_text(" ", strip=True)), value=str(o.get("value", o.get_text(strip=True))))
                for o in el.find_all("option")
                if o.get("value", o.get_text(strip=True)) not in ("", None)
            ]
        else:
            ftype = {
                "textarea": "textarea", "email": "email", "tel": "phone", "url": "url",
                "number": "number", "date": "date", "file": "file",
            }.get(input_type, "text")  # type: ignore[assignment]
            options = []
        fields[name] = FormField(
            key=name, label=label, type=ftype, required=required_attr or req, options=options,
            section=_section(name, label),  # type: ignore[arg-type]
        )
    return list(fields.values())


def _group_label(soup: BeautifulSoup, el: Tag) -> tuple[str, bool]:
    box = _container(el)
    if box is not None:
        for sel in (".application-label", "legend", ".label"):
            found = box.select_one(sel)
            if found is not None:
                raw = found.get_text(" ", strip=True)
                return _clean(raw), "✱" in raw or "*" in raw or found.select_one(".required") is not None
        first = box.find("label")
        if first is not None and first.find("input") is None:
            raw = first.get_text(" ", strip=True)
            return _clean(raw), "✱" in raw or "*" in raw
    return _clean(str(el.get("name", ""))), False
