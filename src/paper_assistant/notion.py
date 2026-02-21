"""Notion integration and two-way synchronization."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import mistune

from paper_assistant.config import Config
from paper_assistant.models import Paper, PaperMetadata, ProcessingStatus, ReadingStatus, SourceType
from paper_assistant.storage import StorageManager
from paper_assistant.summarizer import (
    SummarizationResult,
    find_one_pager,
    format_summary_file,
    parse_summary_sections,
)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_plain_text(items: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in items:
        pt = item.get("plain_text", "")
        if not pt:
            pt = item.get("text", {}).get("content", "")
        if not pt and item.get("type") == "equation":
            pt = item.get("equation", {}).get("expression", "")
        parts.append(pt)
    return "".join(parts)


def _to_rich_text(text: str, chunk_size: int = 1800) -> list[dict[str, Any]]:
    """Simple plain-text rich_text builder (fallback for non-markdown contexts)."""
    text = text or ""
    if not text:
        return [{"type": "text", "text": {"content": ""}}]

    parts: list[dict[str, Any]] = []
    for start in range(0, len(text), chunk_size):
        parts.append(
            {
                "type": "text",
                "text": {"content": text[start : start + chunk_size]},
            }
        )
    return parts


# ---------------------------------------------------------------------------
# Mistune AST → Notion rich_text / block conversion
# ---------------------------------------------------------------------------

_CHUNK_LIMIT = 1800

_md_parser = mistune.create_markdown(renderer=None, plugins=["math", "strikethrough", "table"])

# Match $$...$$ anywhere (inline or block) and normalise to the
# three-line format that mistune's math plugin expects for block_math:
#   $$
#   expression
#   $$
_DISPLAY_MATH_RE = re.compile(r"\$\$\s*(.+?)\s*\$\$", re.DOTALL)


def _normalise_display_math(md: str) -> str:
    """Rewrite ``$$...$$`` into the three-line block format mistune requires."""
    return _DISPLAY_MATH_RE.sub(lambda m: f"\n\n$$\n{m.group(1)}\n$$\n\n", md)


def _inline_to_rich_text(
    children: list[dict[str, Any]],
    annotations: dict[str, bool] | None = None,
    link_url: str | None = None,
) -> list[dict[str, Any]]:
    """Recursively convert mistune inline AST nodes to Notion rich_text items."""
    if annotations is None:
        annotations = {}
    items: list[dict[str, Any]] = []
    for node in children:
        ntype = node.get("type", "")
        if ntype == "text":
            raw = node.get("raw", "")
            if not raw:
                continue
            rt: dict[str, Any] = {"type": "text", "text": {"content": raw}}
            if link_url:
                rt["text"]["link"] = {"url": link_url}
            if annotations:
                rt["annotations"] = {**annotations}
            items.append(rt)
        elif ntype == "strong":
            merged = {**annotations, "bold": True}
            items.extend(
                _inline_to_rich_text(node.get("children", []), merged, link_url)
            )
        elif ntype == "emphasis":
            merged = {**annotations, "italic": True}
            items.extend(
                _inline_to_rich_text(node.get("children", []), merged, link_url)
            )
        elif ntype == "strikethrough":
            merged = {**annotations, "strikethrough": True}
            items.extend(
                _inline_to_rich_text(node.get("children", []), merged, link_url)
            )
        elif ntype == "codespan":
            raw = node.get("raw", "")
            rt = {"type": "text", "text": {"content": raw}}
            merged = {**annotations, "code": True}
            rt["annotations"] = merged
            if link_url:
                rt["text"]["link"] = {"url": link_url}
            items.append(rt)
        elif ntype == "link":
            url = node.get("attrs", {}).get("url", "")
            items.extend(
                _inline_to_rich_text(node.get("children", []), annotations, url)
            )
        elif ntype == "inline_math":
            expr = node.get("raw", "").strip("$").strip()
            items.append({"type": "equation", "equation": {"expression": expr}})
        elif ntype == "softbreak":
            items.append({"type": "text", "text": {"content": "\n"}})
        else:
            # Fallback: render raw text if present
            raw = node.get("raw", "")
            if raw:
                rt = {"type": "text", "text": {"content": raw}}
                if annotations:
                    rt["annotations"] = {**annotations}
                if link_url:
                    rt["text"]["link"] = {"url": link_url}
                items.append(rt)
    return items


def _chunk_rich_text(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split any rich_text item whose content exceeds the Notion API limit."""
    result: list[dict[str, Any]] = []
    for item in items:
        if item.get("type") == "equation":
            result.append(item)
            continue
        content = item.get("text", {}).get("content", "")
        if len(content) <= _CHUNK_LIMIT:
            result.append(item)
            continue
        for start in range(0, len(content), _CHUNK_LIMIT):
            chunk = dict(item)
            chunk["text"] = {**item["text"], "content": content[start : start + _CHUNK_LIMIT]}
            result.append(chunk)
    return result


def _children_rich_text(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract rich_text from a node's children (inline AST nodes)."""
    children = node.get("children", [])
    items = _inline_to_rich_text(children)
    return _chunk_rich_text(items) or _to_rich_text("")


def _block_text_rich_text(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Handle list_item > block_text or paragraph children."""
    children = node.get("children", [])
    all_inline: list[dict[str, Any]] = []
    for child in children:
        ctype = child.get("type", "")
        if ctype in ("block_text", "paragraph"):
            all_inline.extend(child.get("children", []))
        else:
            all_inline.append(child)
    items = _inline_to_rich_text(all_inline)
    return _chunk_rich_text(items) or _to_rich_text("")


def _strip_summary_wrapper(raw: str) -> str:
    """Strip YAML + duplicate metadata header from saved summary file."""
    body = raw
    if body.startswith("---"):
        end_idx = body.find("---", 3)
        if end_idx != -1:
            body = body[end_idx + 3 :].lstrip()

    hr_idx = body.find("\n---\n")
    if hr_idx != -1 and hr_idx < 400:
        body = body[hr_idx + 5 :].lstrip()

    return body.strip()


def _load_local_summary_markdown(config: Config, paper: Paper) -> str:
    if not paper.summary_path:
        return ""
    summary_path = config.data_dir / paper.summary_path
    if not summary_path.exists():
        return ""
    return _strip_summary_wrapper(summary_path.read_text(encoding="utf-8"))


def _ast_node_to_blocks(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a single mistune AST node into one or more Notion blocks."""
    ntype = node.get("type", "")

    if ntype == "heading":
        level = node.get("attrs", {}).get("level", 1)
        level = min(max(level, 1), 3)
        block_type = f"heading_{level}"
        return [
            {
                "object": "block",
                "type": block_type,
                block_type: {"rich_text": _children_rich_text(node)},
            }
        ]

    if ntype == "paragraph":
        rt = _children_rich_text(node)
        return [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt}}]

    if ntype == "list":
        ordered = node.get("attrs", {}).get("ordered", False)
        list_type = "numbered_list_item" if ordered else "bulleted_list_item"
        blocks: list[dict[str, Any]] = []
        for item_node in node.get("children", []):
            rt_inlines: list[dict[str, Any]] = []
            nested_blocks: list[dict[str, Any]] = []
            for child in item_node.get("children", []):
                ctype = child.get("type", "")
                if ctype in ("block_text", "paragraph"):
                    rt_inlines.extend(child.get("children", []))
                elif ctype == "list":
                    nested_blocks.extend(_ast_node_to_blocks(child))
                # skip blank_line nodes
            rt = _chunk_rich_text(_inline_to_rich_text(rt_inlines)) or _to_rich_text("")
            block_payload: dict[str, Any] = {"rich_text": rt}
            if nested_blocks:
                block_payload["children"] = nested_blocks
            blocks.append(
                {"object": "block", "type": list_type, list_type: block_payload}
            )
        return blocks

    if ntype == "block_quote":
        # Flatten all children paragraphs into one quote block
        all_inline: list[dict[str, Any]] = []
        for child in node.get("children", []):
            if child.get("type") in ("paragraph", "block_text"):
                all_inline.extend(child.get("children", []))
        rt = _inline_to_rich_text(all_inline)
        rt = _chunk_rich_text(rt) or _to_rich_text("")
        return [{"object": "block", "type": "quote", "quote": {"rich_text": rt}}]

    if ntype == "block_code":
        language = node.get("attrs", {}).get("info", "") or "plain text"
        raw = node.get("raw", "").rstrip("\n")
        return [
            {
                "object": "block",
                "type": "code",
                "code": {
                    "language": language,
                    "rich_text": _to_rich_text(raw),
                },
            }
        ]

    if ntype == "block_math":
        expr = node.get("raw", "")
        return [
            {
                "object": "block",
                "type": "equation",
                "equation": {"expression": expr},
            }
        ]

    if ntype == "thematic_break":
        return [{"object": "block", "type": "divider", "divider": {}}]

    if ntype == "table":
        # Collect all rows: header cells first, then body rows
        rows: list[list[dict[str, Any]]] = []
        has_header = False
        for section in node.get("children", []):
            stype = section.get("type", "")
            if stype == "table_head":
                has_header = True
                header_cells = section.get("children", [])
                rows.append(header_cells)
            elif stype == "table_body":
                for row_node in section.get("children", []):
                    rows.append(row_node.get("children", []))
        if not rows:
            return []
        table_width = max(len(r) for r in rows)
        notion_rows: list[dict[str, Any]] = []
        for row_cells in rows:
            cells: list[list[dict[str, Any]]] = []
            for cell_node in row_cells:
                cell_rt = _chunk_rich_text(
                    _inline_to_rich_text(cell_node.get("children", []))
                ) or _to_rich_text("")
                cells.append(cell_rt)
            # Pad to table_width if row has fewer cells
            while len(cells) < table_width:
                cells.append(_to_rich_text(""))
            notion_rows.append(
                {
                    "object": "block",
                    "type": "table_row",
                    "table_row": {"cells": cells},
                }
            )
        return [
            {
                "object": "block",
                "type": "table",
                "table": {
                    "table_width": table_width,
                    "has_column_header": has_header,
                    "children": notion_rows,
                },
            }
        ]

    if ntype == "blank_line":
        return []

    # Fallback: try to extract text
    raw = node.get("raw", "")
    if raw:
        return [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": _to_rich_text(raw.strip())},
            }
        ]
    return []


def _markdown_to_blocks(markdown: str) -> list[dict[str, Any]]:
    """Convert markdown into Notion blocks with full inline formatting and math."""
    ast = _md_parser(_normalise_display_math(markdown or ""))
    if not isinstance(ast, list):
        ast = []

    blocks: list[dict[str, Any]] = []
    for node in ast:
        blocks.extend(_ast_node_to_blocks(node))

    if not blocks:
        blocks.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": _to_rich_text("No summary available.")},
            }
        )
    return blocks


def _blocks_to_markdown(blocks: list[dict[str, Any]], indent: int = 0) -> str:
    prefix = "  " * indent
    lines: list[str] = []
    for block in blocks:
        if not block or block.get("archived"):
            continue
        block_type = block.get("type")
        payload = block.get(block_type, {}) if block_type else {}
        rich_text = payload.get("rich_text", [])
        text = _read_plain_text(rich_text).strip()

        if block_type == "heading_1":
            lines.append(f"{prefix}# {text}")
        elif block_type == "heading_2":
            lines.append(f"{prefix}## {text}")
        elif block_type == "heading_3":
            lines.append(f"{prefix}### {text}")
        elif block_type == "bulleted_list_item":
            lines.append(f"{prefix}- {text}")
            children = payload.get("children", [])
            if children:
                nested = _blocks_to_markdown(children, indent + 1)
                if nested:
                    lines.append(nested)
        elif block_type == "numbered_list_item":
            lines.append(f"{prefix}1. {text}")
            children = payload.get("children", [])
            if children:
                nested = _blocks_to_markdown(children, indent + 1)
                if nested:
                    lines.append(nested)
        elif block_type == "quote":
            lines.append(f"{prefix}> {text}")
        elif block_type == "code":
            lang = payload.get("language", "")
            lines.append(f"{prefix}```{lang}".rstrip())
            lines.append(text)
            lines.append(f"{prefix}```")
        elif block_type == "equation":
            expr = payload.get("expression", "")
            lines.append(f"{prefix}$$")
            lines.append(f"{prefix}{expr}")
            lines.append(f"{prefix}$$")
        elif block_type == "divider":
            lines.append(f"{prefix}---")
        elif block_type == "table":
            table_rows = payload.get("children", [])
            for i, row_block in enumerate(table_rows):
                row_payload = row_block.get("table_row", {})
                cells = row_payload.get("cells", [])
                cell_texts = [
                    _read_plain_text(cell).strip() for cell in cells
                ]
                lines.append(f"{prefix}| " + " | ".join(cell_texts) + " |")
                if i == 0 and payload.get("has_column_header"):
                    lines.append(
                        f"{prefix}| " + " | ".join("---" for _ in cells) + " |"
                    )
        elif block_type == "paragraph":
            lines.append(f"{prefix}{text}" if text else "")

        if lines and lines[-1] != "":
            lines.append("")

    result = "\n".join(lines)
    return result.strip() if indent == 0 else result.rstrip()


def _dedupe_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for tag in tags:
        t = tag.strip()
        if t and t not in seen:
            ordered.append(t)
            seen.add(t)
    return ordered


def _parse_reading_status(value: str | None) -> ReadingStatus | None:
    if not value:
        return None
    try:
        return ReadingStatus(value)
    except ValueError:
        return None


@dataclass
class NotionPaper:
    page_id: str
    arxiv_id: str | None
    source_slug: str | None
    title: str
    authors: list[str]
    tags: list[str]
    reading_status: str | None
    summary_markdown: str
    summary_last_modified: datetime | None
    local_last_modified: datetime | None
    archived: bool
    notion_last_edited_time: datetime

    @property
    def paper_id(self) -> str | None:
        """Return the best available identifier."""
        return self.arxiv_id or self.source_slug or None

    @property
    def remote_modified_at(self) -> datetime:
        return self.summary_last_modified or self.notion_last_edited_time


@dataclass
class SyncReport:
    dry_run: bool
    local_created: int = 0
    local_updated: int = 0
    local_archived: int = 0
    notion_created: int = 0
    notion_updated: int = 0
    notion_archived: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=_utc_now)
    finished_at: datetime | None = None

    def finalize(self) -> None:
        self.finished_at = _utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "local_created": self.local_created,
            "local_updated": self.local_updated,
            "local_archived": self.local_archived,
            "notion_created": self.notion_created,
            "notion_updated": self.notion_updated,
            "notion_archived": self.notion_archived,
            "skipped": self.skipped,
            "warnings": self.warnings,
            "errors": self.errors,
            "actions": self.actions,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class NotionClient:
    """Thin async Notion API client for sync use-cases."""

    def __init__(
        self,
        token: str,
        database_id: str,
        *,
        api_base: str = NOTION_API_BASE,
        notion_version: str = NOTION_VERSION,
    ) -> None:
        self.token = token
        self.database_id = database_id
        self.api_base = api_base.rstrip("/")
        self.notion_version = notion_version
        self._property_keys: dict[str, str] | None = None

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.notion_version,
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        url = f"{self.api_base}{path}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method,
                url,
                headers=self._headers,
                json=json_payload,
                params=params,
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            if len(detail) > 1000:
                detail = detail[:1000] + "...(truncated)"
            request_id = exc.response.headers.get("x-request-id")
            req = f"{method} {path}"
            if request_id:
                raise RuntimeError(
                    f"Notion API error {exc.response.status_code} on {req} "
                    f"(request_id={request_id}): {detail}"
                ) from exc
            raise RuntimeError(
                f"Notion API error {exc.response.status_code} on {req}: {detail}"
            ) from exc

        if response.content:
            return response.json()
        return {}

    async def _ensure_property_keys(self) -> dict[str, str]:
        """Resolve canonical property names to actual database property keys."""
        if self._property_keys is not None:
            return self._property_keys

        data = await self._request("GET", f"/databases/{self.database_id}")
        properties = data.get("properties", {})
        types_by_name = {
            name: value.get("type")
            for name, value in properties.items()
            if isinstance(value, dict)
        }

        expected = {
            "arxiv_id": "rich_text",
            "title": "title",
            "authors": "rich_text",
            "tags": "multi_select",
            "reading_status": "select",
            "summary_last_modified": "date",
            "local_last_modified": "date",
            "archived": "checkbox",
        }
        # Optional properties that won't raise errors if missing
        optional = {
            "source_slug": "rich_text",
        }

        resolved: dict[str, str] = {}
        for canonical, expected_type in expected.items():
            if types_by_name.get(canonical) == expected_type:
                resolved[canonical] = canonical
                continue

            if canonical == "title" and types_by_name.get("Name") == "title":
                resolved[canonical] = "Name"
                continue

            case_matches = [
                name
                for name, prop_type in types_by_name.items()
                if name.lower() == canonical.lower() and prop_type == expected_type
            ]
            if case_matches:
                resolved[canonical] = case_matches[0]
                continue

            available = ", ".join(
                f"{name}:{prop_type}" for name, prop_type in sorted(types_by_name.items())
            )
            raise ValueError(
                "Notion database schema mismatch. Missing property "
                f"'{canonical}' with type '{expected_type}'. "
                f"Available properties: {available}"
            )

        # Resolve optional properties (no error if missing)
        for canonical, expected_type in optional.items():
            if types_by_name.get(canonical) == expected_type:
                resolved[canonical] = canonical
            else:
                case_matches = [
                    name
                    for name, prop_type in types_by_name.items()
                    if name.lower() == canonical.lower() and prop_type == expected_type
                ]
                if case_matches:
                    resolved[canonical] = case_matches[0]

        self._property_keys = resolved
        return resolved

    def _property_key(self, canonical: str) -> str:
        if self._property_keys is None or canonical not in self._property_keys:
            raise RuntimeError("Notion property mapping is not initialized.")
        return self._property_keys[canonical]

    async def list_papers(self) -> list[NotionPaper]:
        await self._ensure_property_keys()
        pages: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            payload: dict[str, Any] = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            data = await self._request(
                "POST",
                f"/databases/{self.database_id}/query",
                json_payload=payload,
            )
            pages.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        page_candidates = [p for p in pages if p.get("object") == "page"]
        markdowns = await asyncio.gather(
            *(self.fetch_page_markdown(p["id"]) for p in page_candidates)
        )
        records: list[NotionPaper] = []
        for page, markdown in zip(page_candidates, markdowns):
            records.append(self._parse_page(page, markdown))
        return records

    def _parse_page(self, page: dict[str, Any], summary_markdown: str) -> NotionPaper:
        props = page.get("properties", {})
        keys = self._property_keys or {}

        def prop_text(canonical_name: str) -> str:
            prop = props.get(keys.get(canonical_name, canonical_name), {})
            prop_type = prop.get("type")
            if prop_type == "title":
                return _read_plain_text(prop.get("title", []))
            if prop_type == "rich_text":
                return _read_plain_text(prop.get("rich_text", []))
            return ""

        def prop_tags(canonical_name: str) -> list[str]:
            prop = props.get(keys.get(canonical_name, canonical_name), {})
            if prop.get("type") == "multi_select":
                return [item.get("name", "") for item in prop.get("multi_select", []) if item.get("name")]
            return []

        def prop_select(canonical_name: str) -> str | None:
            prop = props.get(keys.get(canonical_name, canonical_name), {})
            if prop.get("type") == "select":
                selected = prop.get("select")
                if selected:
                    return selected.get("name")
            return None

        def prop_checkbox(canonical_name: str) -> bool:
            prop = props.get(keys.get(canonical_name, canonical_name), {})
            if prop.get("type") == "checkbox":
                return bool(prop.get("checkbox"))
            return False

        def prop_date(canonical_name: str) -> datetime | None:
            prop = props.get(keys.get(canonical_name, canonical_name), {})
            if prop.get("type") != "date":
                return None
            date_obj = prop.get("date")
            if not date_obj:
                return None
            return _parse_iso_datetime(date_obj.get("start"))

        notion_last_edited = _parse_iso_datetime(page.get("last_edited_time")) or _utc_now()

        return NotionPaper(
            page_id=page["id"],
            arxiv_id=prop_text("arxiv_id") or None,
            source_slug=prop_text("source_slug") or None,
            title=prop_text("title"),
            authors=[a.strip() for a in prop_text("authors").split(",") if a.strip()],
            tags=_dedupe_tags(prop_tags("tags")),
            reading_status=prop_select("reading_status"),
            summary_markdown=summary_markdown,
            summary_last_modified=prop_date("summary_last_modified"),
            local_last_modified=prop_date("local_last_modified"),
            archived=prop_checkbox("archived") or bool(page.get("archived")),
            notion_last_edited_time=notion_last_edited,
        )

    _LIST_BLOCK_TYPES = {"bulleted_list_item", "numbered_list_item"}

    async def _fetch_blocks(self, parent_id: str) -> list[dict[str, Any]]:
        """Paginate through all child blocks of a parent."""
        blocks: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = await self._request(
                "GET",
                f"/blocks/{parent_id}/children",
                params=params,
            )
            blocks.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return blocks

    async def _fetch_blocks_recursive(self, parent_id: str) -> list[dict[str, Any]]:
        """Fetch blocks and recursively populate children for list items."""
        blocks = await self._fetch_blocks(parent_id)
        for block in blocks:
            btype = block.get("type", "")
            if block.get("has_children") and btype in self._LIST_BLOCK_TYPES:
                children = await self._fetch_blocks_recursive(block["id"])
                block[btype]["children"] = children
        return blocks

    async def fetch_page_markdown(self, page_id: str) -> str:
        blocks = await self._fetch_blocks_recursive(page_id)
        return _blocks_to_markdown(blocks)

    def _build_properties(
        self,
        *,
        arxiv_id: str,
        title: str,
        authors: list[str],
        tags: list[str],
        reading_status: ReadingStatus,
        summary_modified_at: datetime,
        local_modified_at: datetime,
        archived: bool,
        source_slug: str | None = None,
    ) -> dict[str, Any]:
        arxiv_id_key = self._property_key("arxiv_id")
        title_key = self._property_key("title")
        authors_key = self._property_key("authors")
        tags_key = self._property_key("tags")
        reading_status_key = self._property_key("reading_status")
        summary_modified_key = self._property_key("summary_last_modified")
        local_modified_key = self._property_key("local_last_modified")
        archived_key = self._property_key("archived")

        props: dict[str, Any] = {
            arxiv_id_key: {"rich_text": _to_rich_text(arxiv_id)},
            title_key: {"title": _to_rich_text(title[:200])},
            authors_key: {"rich_text": _to_rich_text(", ".join(authors))},
            tags_key: {"multi_select": [{"name": t} for t in _dedupe_tags(tags)]},
            reading_status_key: {"select": {"name": reading_status.value}},
            summary_modified_key: {"date": {"start": summary_modified_at.isoformat()}},
            local_modified_key: {"date": {"start": local_modified_at.isoformat()}},
            archived_key: {"checkbox": archived},
        }

        # Write source_slug only if the Notion DB has the column
        if source_slug and "source_slug" in (self._property_keys or {}):
            slug_key = self._property_key("source_slug")
            props[slug_key] = {"rich_text": _to_rich_text(source_slug)}

        return props

    async def create_page(
        self,
        *,
        paper: Paper,
        summary_markdown: str,
        summary_modified_at: datetime,
        include_audio: Path | None,
    ) -> NotionPaper:
        await self._ensure_property_keys()
        blocks = _markdown_to_blocks(summary_markdown)
        payload = {
            "parent": {"database_id": self.database_id},
            "properties": self._build_properties(
                arxiv_id=paper.metadata.arxiv_id or "",
                title=paper.metadata.title,
                authors=paper.metadata.authors,
                tags=paper.tags,
                reading_status=paper.reading_status,
                summary_modified_at=summary_modified_at,
                local_modified_at=paper.local_modified_at,
                archived=paper.archived_at is not None or paper.reading_status == ReadingStatus.ARCHIVED,
                source_slug=paper.metadata.source_slug,
            ),
            "children": blocks[:100],
        }
        page = await self._request("POST", "/pages", json_payload=payload)

        page_id = page["id"]
        if len(blocks) > 100:
            for idx in range(100, len(blocks), 100):
                await self.append_blocks(page_id, blocks[idx : idx + 100])

        markdown = await self.fetch_page_markdown(page_id)
        page_obj = await self._request("GET", f"/pages/{page_id}")
        return self._parse_page(page_obj, markdown)

    async def update_page(
        self,
        *,
        page_id: str,
        paper: Paper,
        summary_markdown: str,
        summary_modified_at: datetime,
        include_audio: Path | None,
        archived: bool,
    ) -> NotionPaper:
        await self._ensure_property_keys()
        payload = {
            "properties": self._build_properties(
                arxiv_id=paper.metadata.arxiv_id or "",
                title=paper.metadata.title,
                authors=paper.metadata.authors,
                tags=paper.tags,
                reading_status=paper.reading_status,
                summary_modified_at=summary_modified_at,
                local_modified_at=paper.local_modified_at,
                archived=archived,
                source_slug=paper.metadata.source_slug,
            ),
            "archived": archived,
        }
        await self._request("PATCH", f"/pages/{page_id}", json_payload=payload)

        blocks = _markdown_to_blocks(summary_markdown)
        await self.replace_page_body(page_id, blocks)

        markdown = await self.fetch_page_markdown(page_id)
        page_obj = await self._request("GET", f"/pages/{page_id}")
        return self._parse_page(page_obj, markdown)

    async def append_blocks(self, page_id: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        await self._request(
            "PATCH",
            f"/blocks/{page_id}/children",
            json_payload={"children": blocks},
        )

    async def replace_page_body(self, page_id: str, new_blocks: list[dict[str, Any]]) -> None:
        existing: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = await self._request(
                "GET",
                f"/blocks/{page_id}/children",
                params=params,
            )
            existing.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        # Archive old blocks so each sync reflects current local summary cleanly.
        for block in existing:
            block_id = block.get("id")
            if block_id:
                await self._request("PATCH", f"/blocks/{block_id}", json_payload={"archived": True})

        for idx in range(0, len(new_blocks), 100):
            await self.append_blocks(page_id, new_blocks[idx : idx + 100])

    async def set_archived(self, page_id: str, archived: bool) -> None:
        await self._ensure_property_keys()
        archived_key = self._property_key("archived")
        await self._request(
            "PATCH",
            f"/pages/{page_id}",
            json_payload={"archived": archived, "properties": {archived_key: {"checkbox": archived}}},
        )

    async def attach_audio(self, page_id: str, audio_path: Path) -> None:
        upload_id = await self._upload_file(audio_path)
        block = {
            "object": "block",
            "type": "file",
            "file": {"type": "file_upload", "file_upload": {"id": upload_id}},
        }
        await self.append_blocks(page_id, [block])

    async def _upload_file(self, file_path: Path) -> str:
        create_payload = {
            "filename": file_path.name,
            "content_type": "audio/mpeg",
            "mode": "single_part",
        }
        created = await self._request(
            "POST",
            "/file_uploads",
            json_payload=create_payload,
            timeout=120.0,
        )
        upload_id = created.get("id")
        if not upload_id:
            raise RuntimeError("Notion upload API did not return upload id")

        send_url = f"{self.api_base}/file_uploads/{upload_id}/send"
        async with httpx.AsyncClient(timeout=120.0) as client:
            with file_path.open("rb") as fp:
                resp = await client.post(
                    send_url,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Notion-Version": self.notion_version,
                    },
                    files={"file": (file_path.name, fp, "audio/mpeg")},
                )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            if len(detail) > 1000:
                detail = detail[:1000] + "...(truncated)"
            request_id = exc.response.headers.get("x-request-id")
            if request_id:
                raise RuntimeError(
                    "Notion API error "
                    f"{exc.response.status_code} on POST /file_uploads/{upload_id}/send "
                    f"(request_id={request_id}): {detail}"
                ) from exc
            raise RuntimeError(
                "Notion API error "
                f"{exc.response.status_code} on POST /file_uploads/{upload_id}/send: {detail}"
            ) from exc

        return upload_id


def _should_archive(paper: Paper) -> bool:
    return paper.archived_at is not None or paper.reading_status == ReadingStatus.ARCHIVED


async def _push_local_to_notion(
    *,
    config: Config,
    storage: StorageManager,
    client: NotionClient,
    paper: Paper,
    remote: NotionPaper | None,
    report: SyncReport,
    dry_run: bool,
    sync_time: datetime,
) -> None:
    summary_markdown = _load_local_summary_markdown(config, paper)
    summary_modified_at = paper.local_modified_at
    audio_path = (config.data_dir / paper.audio_path) if paper.audio_path else None
    archived = _should_archive(paper)

    pid = paper.metadata.paper_id
    action = (
        f"push local->{pid} to notion "
        f"({'create' if remote is None else 'update'})"
    )
    report.actions.append(action)
    if dry_run:
        if remote is None:
            report.notion_created += 1
        else:
            report.notion_updated += 1
        return

    if remote is None:
        remote_after = await client.create_page(
            paper=paper,
            summary_markdown=summary_markdown,
            summary_modified_at=summary_modified_at,
            include_audio=audio_path,
        )
        report.notion_created += 1
    else:
        remote_after = await client.update_page(
            page_id=remote.page_id,
            paper=paper,
            summary_markdown=summary_markdown,
            summary_modified_at=summary_modified_at,
            include_audio=audio_path,
            archived=archived,
        )
        report.notion_updated += 1

    storage.set_notion_fields(
        pid,
        notion_page_id=remote_after.page_id,
        notion_modified_at=remote_after.notion_last_edited_time,
        last_synced_at=sync_time,
    )

    if audio_path and audio_path.exists():
        try:
            await client.attach_audio(remote_after.page_id, audio_path)
        except Exception as exc:
            report.warnings.append(
                f"Audio upload failed for {pid}: {exc}"
            )


def _set_local_from_remote(
    *,
    config: Config,
    storage: StorageManager,
    paper: Paper,
    remote: NotionPaper,
    report: SyncReport,
    dry_run: bool,
    sync_time: datetime,
) -> None:
    remote_ts = remote.remote_modified_at
    local_changed = False

    pid = paper.metadata.paper_id

    # Summary
    local_summary = _load_local_summary_markdown(config, paper)
    remote_summary = remote.summary_markdown.strip()
    if remote_summary and remote_summary != local_summary:
        report.actions.append(f"pull remote summary->{pid}")
        if not dry_run:
            original_status = paper.status
            sections = parse_summary_sections(remote_summary)
            one_pager = find_one_pager(sections)
            summary_result = SummarizationResult(
                full_markdown=remote_summary,
                one_pager=one_pager,
                sections=sections,
                model_used=paper.model_used or "notion-sync",
            )
            formatted = format_summary_file(paper.metadata, summary_result)
            storage.save_summary(pid, formatted, modified_at=remote_ts)
            paper = storage.get_paper(pid) or paper
            paper.status = original_status
            local_changed = True
        else:
            local_changed = True

    # Tags
    remote_tags = _dedupe_tags(remote.tags)
    if remote_tags != _dedupe_tags(paper.tags):
        report.actions.append(f"pull remote tags->{pid}")
        if not dry_run:
            paper.tags = remote_tags
        local_changed = True

    # Reading status
    remote_status = _parse_reading_status(remote.reading_status)
    if remote_status and remote_status != paper.reading_status:
        report.actions.append(f"pull remote reading_status->{pid}")
        if not dry_run:
            paper.reading_status = remote_status
            if remote_status == ReadingStatus.ARCHIVED:
                paper.archived_at = remote_ts
            elif paper.archived_at is not None:
                paper.archived_at = None
        local_changed = True

    if not dry_run and local_changed:
        paper.local_modified_at = remote_ts
        paper.notion_page_id = remote.page_id
        paper.notion_modified_at = remote.notion_last_edited_time
        paper.last_synced_at = sync_time
        storage.add_paper(paper)
        report.local_updated += 1
    elif dry_run and local_changed:
        report.local_updated += 1
    elif not dry_run:
        storage.set_notion_fields(
            pid,
            notion_page_id=remote.page_id,
            notion_modified_at=remote.notion_last_edited_time,
            last_synced_at=sync_time,
        )


async def _import_remote_only(
    *,
    config: Config,
    storage: StorageManager,
    remote: NotionPaper,
    report: SyncReport,
    dry_run: bool,
    sync_time: datetime,
) -> None:
    from paper_assistant.arxiv import fetch_metadata

    rid = remote.paper_id
    if not rid:
        report.warnings.append(f"Skipping Notion page {remote.page_id}: missing arxiv_id and source_slug.")
        report.skipped += 1
        return

    report.actions.append(f"import notion->{rid} to local")
    if dry_run:
        report.local_created += 1
        return

    if remote.arxiv_id:
        # arXiv paper — fetch full metadata from arXiv API
        try:
            metadata = await fetch_metadata(remote.arxiv_id, config=config)
        except Exception as exc:  # pragma: no cover - exercised via integration
            report.errors.append(f"Failed to fetch metadata for {remote.arxiv_id}: {exc}")
            report.skipped += 1
            return
    else:
        # Web article — build metadata from what Notion provides
        metadata = PaperMetadata(
            source_type=SourceType.WEB,
            source_slug=remote.source_slug,
            title=remote.title or remote.source_slug or "Untitled",
            authors=remote.authors,
        )

    reading_status = _parse_reading_status(remote.reading_status) or ReadingStatus.UNREAD
    paper = Paper(
        metadata=metadata,
        tags=_dedupe_tags(remote.tags),
        reading_status=reading_status,
        local_modified_at=remote.remote_modified_at,
        notion_page_id=remote.page_id,
        notion_modified_at=remote.notion_last_edited_time,
        last_synced_at=sync_time,
        archived_at=remote.remote_modified_at if remote.archived else None,
        model_used="manual",
    )
    storage.add_paper(paper)

    if remote.summary_markdown.strip():
        sections = parse_summary_sections(remote.summary_markdown)
        one_pager = find_one_pager(sections)
        summary_result = SummarizationResult(
            full_markdown=remote.summary_markdown,
            one_pager=one_pager,
            sections=sections,
            model_used="manual",
        )
        formatted = format_summary_file(metadata, summary_result)
        storage.save_summary(rid, formatted, modified_at=remote.remote_modified_at)
        updated = storage.get_paper(rid)
        if updated:
            updated.status = ProcessingStatus.COMPLETE
            updated.notion_page_id = remote.page_id
            updated.notion_modified_at = remote.notion_last_edited_time
            updated.last_synced_at = sync_time
            storage.add_paper(updated)

    if remote.archived:
        storage.set_archived(rid, True, modified_at=remote.remote_modified_at)
        report.local_archived += 1

    report.local_created += 1


async def sync_notion(
    *,
    config: Config,
    storage: StorageManager,
    paper_id: str | None = None,
    dry_run: bool = False,
    notion_client: NotionClient | None = None,
) -> SyncReport:
    """Run manual Notion two-way sync."""
    if not config.notion_sync_enabled:
        raise ValueError(
            "Notion sync is disabled. Set PAPER_ASSIST_NOTION_SYNC_ENABLED=true to enable."
        )
    if not config.notion_token:
        raise ValueError("PAPER_ASSIST_NOTION_TOKEN is required for Notion sync.")
    if not config.notion_database_id:
        raise ValueError("PAPER_ASSIST_NOTION_DATABASE_ID is required for Notion sync.")

    report = SyncReport(dry_run=dry_run)
    client = notion_client or NotionClient(config.notion_token, config.notion_database_id)
    sync_time = _utc_now()

    local_papers = storage.list_papers(sort_by="date_added", reverse=False)
    remote_papers = await client.list_papers()

    if paper_id:
        local_papers = [
            p
            for p in local_papers
            if p.metadata.paper_id == paper_id or (p.notion_page_id and p.notion_page_id == paper_id)
        ]
        remote_papers = [
            rp for rp in remote_papers
            if rp.page_id == paper_id or rp.arxiv_id == paper_id or rp.source_slug == paper_id
        ]

    remote_by_page = {rp.page_id: rp for rp in remote_papers}
    remote_by_arxiv: dict[str, NotionPaper] = {}
    for rp in remote_papers:
        if not rp.arxiv_id:
            continue
        existing = remote_by_arxiv.get(rp.arxiv_id)
        if existing is None or rp.notion_last_edited_time > existing.notion_last_edited_time:
            remote_by_arxiv[rp.arxiv_id] = rp
    remote_by_slug: dict[str, NotionPaper] = {}
    for rp in remote_papers:
        if not rp.source_slug:
            continue
        existing = remote_by_slug.get(rp.source_slug)
        if existing is None or rp.notion_last_edited_time > existing.notion_last_edited_time:
            remote_by_slug[rp.source_slug] = rp

    processed_remote_ids: set[str] = set()

    for paper in local_papers:
        remote = None
        if paper.notion_page_id and paper.notion_page_id in remote_by_page:
            remote = remote_by_page[paper.notion_page_id]
        elif paper.metadata.arxiv_id and paper.metadata.arxiv_id in remote_by_arxiv:
            remote = remote_by_arxiv[paper.metadata.arxiv_id]
        elif paper.metadata.source_slug and paper.metadata.source_slug in remote_by_slug:
            remote = remote_by_slug[paper.metadata.source_slug]

        if remote is None:
            await _push_local_to_notion(
                config=config,
                storage=storage,
                client=client,
                paper=paper,
                remote=None,
                report=report,
                dry_run=dry_run,
                sync_time=sync_time,
            )
            continue

        processed_remote_ids.add(remote.page_id)

        pid = paper.metadata.paper_id
        local_archived = _should_archive(paper)
        remote_archived = remote.archived
        if local_archived or remote_archived:
            report.actions.append(f"archive propagate->{pid}")
            if dry_run:
                if not local_archived:
                    report.local_archived += 1
                if not remote_archived:
                    report.notion_archived += 1
                continue

            if not local_archived:
                storage.set_archived(pid, True, modified_at=remote.remote_modified_at)
                report.local_archived += 1
            if not remote_archived and config.notion_archive_on_delete:
                await client.set_archived(remote.page_id, True)
                report.notion_archived += 1

            storage.set_notion_fields(
                pid,
                notion_page_id=remote.page_id,
                notion_modified_at=remote.notion_last_edited_time,
                last_synced_at=sync_time,
            )
            continue

        local_ts = paper.local_modified_at
        remote_ts = remote.remote_modified_at
        if remote_ts > local_ts:
            _set_local_from_remote(
                config=config,
                storage=storage,
                paper=paper,
                remote=remote,
                report=report,
                dry_run=dry_run,
                sync_time=sync_time,
            )
        elif local_ts > remote_ts:
            await _push_local_to_notion(
                config=config,
                storage=storage,
                client=client,
                paper=paper,
                remote=remote,
                report=report,
                dry_run=dry_run,
                sync_time=sync_time,
            )
        else:
            report.actions.append(f"no-op->{pid}")
            if not dry_run:
                storage.set_notion_fields(
                    pid,
                    notion_page_id=remote.page_id,
                    notion_modified_at=remote.notion_last_edited_time,
                    last_synced_at=sync_time,
                )

    for remote in remote_papers:
        if remote.page_id in processed_remote_ids:
            continue
        await _import_remote_only(
            config=config,
            storage=storage,
            remote=remote,
            report=report,
            dry_run=dry_run,
            sync_time=sync_time,
        )

    report.finalize()
    return report
