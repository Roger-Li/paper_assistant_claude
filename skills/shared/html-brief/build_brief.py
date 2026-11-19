#!/usr/bin/env python3
"""Fetch sources for, build, and lint a Paper Assistant HTML research brief.

Skill helper for the HTML brief workflow (see
``src/paper_assistant/prompts/paper_html_summary_instructions.md``). It is not
part of the ``paper-assist`` CLI and never touches ``index.json``.

    build_brief.py fetch 2610.22293 --out .artifacts/summarize-paper/2610.22293
    build_brief.py math  .artifacts/summarize-paper/2610.22293/sources/paper.html --grep Delta
    build_brief.py build .artifacts/summarize-paper/2610.22293

``fetch`` pins a version and downloads the abs page, the arXiv HTML, the PDF and
every figure asset the HTML references, then writes anchor-annotated text.
``build`` turns ``brief.src.html`` + ``evidence.json`` into one self-contained
``summary.html`` and lints it; it exits non-zero when a check fails.
"""

from __future__ import annotations

import argparse
import base64
import copy
import html
import json
import mimetypes
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

KIT_DIR = Path(__file__).resolve().parent
DEFAULT_USER_AGENT = "paper-assistant-html-brief/1.0"
ARXIV_HTML_PREFIX = "https://arxiv.org/html/"
WORDS_PER_MINUTE = 200

# Parts a learning-oriented brief is expected to carry (``data-part`` values).
EXPECTED_PARTS = (
    "verdict",
    "objectives",
    "primers",
    "mechanism",
    "worked-example",
    "evidence",
    "skeptic",
    "check",
    "next",
    "notation",
    "glossary",
    "sources",
)
MIN_CHECK_QUESTIONS = 3
PLACEHOLDER_RE = re.compile(r"\{\{|\}\}|\bTODO\b|\bTKTK\b|lorem ipsum", re.I)
_INVISIBLE_RE = re.compile(r"[\s⁡⁢⁣​  ]+")
_NUMERIC_RE = re.compile(r"^[+\-]?\d[\d,]*(\.\d+)?%?$")


# ───────────────────────────── source document ──────────────────────────────


def _norm(text: str) -> str:
    """Normalize text for comparison: drop whitespace/invisibles, unify minus."""
    return _INVISIBLE_RE.sub("", text).replace("−", "-")


def _strip_annotations(tag: Tag) -> Tag:
    clone = copy.copy(tag)
    for node in clone.find_all(["annotation", "annotation-xml"]):
        node.decompose()
    return clone


def _clean_mathml(math: Tag, *, display: str | None = None) -> str:
    """Return compact MathML: no TeX annotations, ids, classes or xrefs."""
    clone = _strip_annotations(math)
    for sem in clone.find_all("semantics"):
        sem.unwrap()
    keep_root = {"alttext", "display"}
    for node in [clone, *clone.find_all(True)]:
        for attr in list(node.attrs):
            if attr in {"id", "class", "xref", "intent", "href"} or attr.startswith("data-"):
                del node.attrs[attr]
            elif node is clone and attr not in keep_root and attr not in {"mathvariant"}:
                del node.attrs[attr]
    if display:
        clone["display"] = display
    return str(clone)


class SourceDoc:
    """The downloaded arXiv (LaTeXML) HTML, indexed for anchors, math and rows."""

    def __init__(self, markup: str):
        self.soup = BeautifulSoup(markup, "html.parser")
        self.ids = {tag["id"] for tag in self.soup.find_all(id=True)}
        self._math: dict[str, Tag] = {}
        for math in self.soup.find_all("math"):
            alt = math.get("alttext")
            if alt and alt not in self._math:
                self._math[alt] = math

    @classmethod
    def load(cls, path: Path) -> "SourceDoc":
        return cls(path.read_text(encoding="utf-8"))

    def alttexts(self) -> list[str]:
        return sorted(self._math, key=lambda s: (len(s), s))

    def inline_math(self, alttext: str) -> str | None:
        math = self._math.get(alttext)
        return _clean_mathml(math, display="inline") if math is not None else None

    def equation(self, element_id: str) -> str | None:
        element = self.soup.find(id=element_id)
        if element is None:
            return None
        maths = [element] if element.name == "math" else element.find_all("math")
        if not maths:
            return None
        return "".join(_clean_mathml(m, display="block") for m in maths)

    def text(self, element_id: str) -> str | None:
        element = self.soup.find(id=element_id)
        if element is None:
            return None
        return _norm(_strip_annotations(element).get_text(" "))

    def rows(self, element_id: str) -> list[list[str]] | None:
        element = self.soup.find(id=element_id)
        if element is None:
            return None
        rows = []
        for tr in _strip_annotations(element).find_all("tr"):
            rows.append([_norm(cell.get_text(" ")) for cell in tr.find_all(["td", "th"])])
        return rows


def row_matches(cells: list[str], needles: list[str]) -> bool:
    """True when every needle is in one row: numbers by equality, text by containment."""
    for needle in needles:
        wanted = _norm(str(needle))
        if _NUMERIC_RE.match(wanted):
            if wanted not in cells:
                return False
        elif not any(wanted in cell for cell in cells):
            return False
    return True


def html_to_markdown(markup: str) -> str:
    """Convert arXiv LaTeXML HTML to anchor-annotated text for reading.

    Headings carry ``{#anchor}``, figures/tables are labelled with their ids and
    asset paths, and math is kept as ``$latex$`` from each ``alttext``.
    """
    soup = BeautifulSoup(markup, "html.parser")
    for junk in soup.select("script, style, nav, button, .ltx_page_footer, .package-alerts"):
        junk.decompose()
    for math in soup.find_all("math"):
        alt = math.get("alttext") or math.get_text()
        wrapped = f"$${alt}$$" if math.get("display") == "block" else f"${alt}$"
        math.replace_with(wrapped)
    article = soup.select_one("article") or soup.body or soup
    lines: list[str] = []

    def text(node: Tag) -> str:
        return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()

    def table_lines(table: Tag) -> list[str]:
        out = []
        for tr in table.find_all("tr"):
            out.append("| " + " | ".join(text(c) for c in tr.find_all(["td", "th"])) + " |")
        return out

    def walk(node: Tag) -> None:
        for child in node.children:
            if not isinstance(child, Tag):
                continue
            classes = " ".join(child.get("class", []))
            name = child.name
            if re.fullmatch(r"h[1-6]", name):
                owner = child.find_parent(["section", "div"], id=True)
                anchor = f"  {{#{owner['id']}}}" if owner else ""
                lines.append("\n" + "#" * int(name[1]) + " " + text(child) + anchor)
            elif name == "figure":
                assets = [n.get("src") or n.get("data") for n in child.find_all(["img", "object"])]
                kind = "TABLE" if "ltx_table" in classes else "FIGURE"
                lines.append(f"\n[{kind} #{child.get('id', '')}] assets={[a for a in assets if a]}")
                for table in child.find_all("table"):
                    if table.find_parent("table") is None:
                        lines.extend(table_lines(table))
                for listing in child.select(".ltx_listingline"):
                    lines.append("    " + text(listing))
                caption = child.find("figcaption")
                if caption:
                    lines.append("CAPTION: " + text(caption))
            elif name == "table":
                lines.append(f"\n[EQUATION #{child.get('id', '')}] " + text(child))
            elif name == "p" or ("ltx_para" in classes and not child.find(["figure", "table", "section"])):
                content = text(child)
                if content:
                    lines.append(content)
            elif name in ("ul", "ol"):
                for item in child.find_all("li", recursive=False):
                    lines.append("- " + text(item))
            elif name == "li" and "ltx_bibitem" in classes:
                lines.append(f"- [#{child.get('id', '')}] " + text(child))
            else:
                walk(child)

    walk(article)
    return "\n".join(lines) + "\n"


# ───────────────────────────────── fetch ────────────────────────────────────


def _http_get(url: str, user_agent: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - fixed https hosts
        return response.read()


def asset_urls(markup: str, base_url: str) -> dict[str, str]:
    """Map relative figure asset paths in the source HTML to verified arXiv URLs."""
    soup = BeautifulSoup(markup, "html.parser")
    found: dict[str, str] = {}
    for node in soup.find_all(["img", "object"]):
        ref = node.get("src") or node.get("data")
        if not ref or ref.startswith("data:") or "://" in ref:
            continue
        url = urljoin(base_url, ref).split("#", 1)[0]
        if url.startswith(ARXIV_HTML_PREFIX):
            found[ref] = url
    return found


def fetch(arxiv_id: str, out: Path, *, user_agent: str = DEFAULT_USER_AGENT, pause: float = 0.4,
          getter=_http_get) -> dict:
    match = re.fullmatch(r"(\d{4}\.\d{4,5})(v\d+)?", arxiv_id)
    if not match:
        raise SystemExit(f"Not an arXiv ID: {arxiv_id!r} (expected e.g. 2610.22293 or 2610.22293v1)")
    base, version = match.group(1), match.group(2)
    sources = out / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    abs_markup = getter(f"https://arxiv.org/abs/{base}{version or ''}", user_agent).decode("utf-8", "replace")
    (sources / "abs.html").write_text(abs_markup, encoding="utf-8")
    if not version:
        versions = [int(v) for v in re.findall(rf"/abs/{re.escape(base)}v(\d+)", abs_markup)]
        version = f"v{max(versions)}" if versions else "v1"
        if not versions:
            warnings.append("Could not read the version history; assumed v1.")
    abs_soup = BeautifulSoup(abs_markup, "html.parser")

    def meta(name: str) -> list[str]:
        return [m.get("content", "") for m in abs_soup.find_all("meta", attrs={"name": name})]

    pinned = f"{base}{version}"
    metadata = {
        "id": base,
        "version": version,
        "title": (meta("citation_title") or [""])[0],
        "authors": [" ".join(reversed([p.strip() for p in a.split(",", 1)])) for a in meta("citation_author")],
        "submitted": (meta("citation_date") or [""])[0].replace("/", "-"),
        "abs": f"https://arxiv.org/abs/{pinned}",
        "html": f"{ARXIV_HTML_PREFIX}{pinned}",
        "pdf": f"https://arxiv.org/pdf/{pinned}",
        "has_html": False,
        "assets": [],
    }

    time.sleep(pause)
    try:
        (sources / "paper.pdf").write_bytes(getter(metadata["pdf"], user_agent))
    except OSError as exc:
        warnings.append(f"PDF download failed: {exc}")

    time.sleep(pause)
    try:
        paper_markup = getter(metadata["html"], user_agent).decode("utf-8", "replace")
    except OSError as exc:
        warnings.append(
            f"No arXiv HTML for {pinned} ({exc}). Work from sources/paper.pdf; cite PDF pages, "
            "and expect the build to skip anchor, math and ledger-row verification."
        )
        paper_markup = ""
    if paper_markup:
        metadata["has_html"] = True
        (sources / "paper.html").write_text(paper_markup, encoding="utf-8")
        (out / "paper.md").write_text(html_to_markdown(paper_markup), encoding="utf-8")
        for ref, url in sorted(asset_urls(paper_markup, metadata["html"]).items()):
            target = (sources / ref).resolve()
            if sources.resolve() not in target.parents:
                warnings.append(f"Skipped asset outside sources/: {ref}")
                continue
            time.sleep(pause)
            try:
                data = getter(url, user_agent)
            except OSError as exc:
                warnings.append(f"Asset download failed: {ref} ({exc})")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            metadata["assets"].append(str(Path("sources") / ref))

    metadata["warnings"] = warnings
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return metadata


# ───────────────────────────────── build ────────────────────────────────────


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def as_dict(self) -> dict:
        return {"ok": not self.errors, "errors": self.errors, "warnings": self.warnings, "stats": self.stats}


_TOKEN_RE = re.compile(r"(<script\b.*?</script\s*>|<style\b.*?</style\s*>|<!--.*?-->|<[^>]+>)", re.S | re.I)
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][\w:-]*)([^>]*)>", re.S)
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_NO_TERM_TAGS = {
    "a", "code", "pre", "kbd", "h1", "h2", "h3", "h4", "summary", "button", "svg", "math",
    "nav", "label", "th", "caption", "dt", "textarea", "select",
}


def glossary_terms(markup: str) -> list[tuple[str, list[str]]]:
    """Read ``<dt id="g-…" data-match="a|b">Label</dt>`` entries from the glossary."""
    terms = []
    for match in re.finditer(r"<dt\b([^>]*)>(.*?)</dt>", markup, re.S | re.I):
        attrs, label = match.group(1), re.sub(r"<[^>]+>", "", match.group(2)).strip()
        id_match = re.search(r'\bid="(g-[^"]+)"', attrs)
        if not id_match:
            continue
        pattern_match = re.search(r'\bdata-match="([^"]*)"', attrs)
        patterns = [p.strip() for p in html.unescape(pattern_match.group(1)).split("|")] if pattern_match else [label]
        terms.append((id_match.group(1), [p for p in patterns if p]))
    return terms


def autolink_terms(markup: str, terms: list[tuple[str, list[str]]]) -> tuple[str, int]:
    """Link the first use of each glossary term in every section of ``<main>``.

    Text inside links, headings, code, captions, SVG/MathML, the glossary itself
    and anything marked ``data-no-terms`` is left alone.
    """
    if not terms:
        return markup, 0
    alternatives = []
    lookup: dict[str, str] = {}
    for term_id, patterns in terms:
        for pattern in patterns:
            case_sensitive = sum(ch.isupper() for ch in pattern) >= 2
            key = pattern if case_sensitive else pattern.lower()
            lookup.setdefault(key, term_id)
            alternatives.append((len(pattern), re.escape(pattern) if case_sensitive else f"(?i:{re.escape(pattern)})"))
    alternatives.sort(key=lambda item: -item[0])
    # A trailing "s" is accepted so plurals link without listing them twice.
    finder = re.compile(r"(?<![\w+\-/])((?:" + "|".join(a for _, a in alternatives) + r")s?)(?![\w+\-/])")

    main = re.search(r"<main\b[^>]*>.*?</main\s*>", markup, re.S | re.I)
    if not main:
        return markup, 0
    stack: list[tuple[str, bool]] = []
    seen: set[str] = set()
    linked = 0
    out: list[str] = []
    for token in _TOKEN_RE.split(main.group(0)):
        if not token:
            continue
        tag = _TAG_RE.fullmatch(token) if token.startswith("<") else None
        if token.startswith("<"):
            out.append(token)
            if not tag:
                continue
            closing, name, attrs = tag.group(1), tag.group(2).lower(), tag.group(3)
            if closing:
                if any(entry[0] == name for entry in stack):
                    while stack and stack.pop()[0] != name:
                        pass
                continue
            if name == "section":
                seen = set()
            if name in _VOID or attrs.rstrip().endswith("/"):
                continue
            skip = (
                name in _NO_TERM_TAGS
                or "data-no-terms" in attrs
                or bool(re.search(r'class="[^"]*\bglossary\b', attrs))
            )
            stack.append((name, skip))
            continue
        if any(skip for _, skip in stack):
            out.append(token)
            continue

        def replace(match: re.Match[str]) -> str:
            nonlocal linked
            found = match.group(1)
            candidates = [found, found.lower()]
            if found.endswith("s"):
                candidates += [found[:-1], found[:-1].lower()]
            term_id = next((lookup[c] for c in candidates if c in lookup), None)
            if not term_id or term_id in seen:
                return found
            seen.add(term_id)
            linked += 1
            return f'<a class="term" href="#{term_id}">{found}</a>'

        out.append(finder.sub(replace, token))
    return markup[: main.start()] + "".join(out) + markup[main.end():], linked


def _visible_words(fragment: str, *, include_details: bool) -> int:
    if not include_details:
        fragment = re.sub(r"<details\b.*?</details\s*>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<(script|style|svg|math)\b.*?</\1\s*>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return len(re.findall(r"[^\W_]+(?:['’.\-][^\W_]+)*", html.unescape(fragment)))


def section_reading_times(markup: str) -> dict[str, tuple[int, int, bool]]:
    """Minutes for each top-level ``<section id>`` in ``<main>``: (read, with details, optional).

    A section marked ``data-optional`` (reference material) is timed but left
    out of the linear reading total.
    """
    main = re.search(r"<main\b[^>]*>(.*?)</main\s*>", markup, re.S | re.I)
    if not main:
        return {}
    body = main.group(1)
    times: dict[str, tuple[int, int, bool]] = {}
    depth, start, current, optional = 0, 0, None, False
    for match in re.finditer(r"<(/?)section\b([^>]*)>", body, re.I):
        if not match.group(1):
            if depth == 0:
                id_match = re.search(r'\bid="([^"]+)"', match.group(2))
                current, start = (id_match.group(1) if id_match else None), match.end()
                optional = "data-optional" in match.group(2)
            depth += 1
        else:
            depth -= 1
            if depth == 0 and current:
                chunk = body[start:match.start()]
                minutes = [
                    max(1, round(_visible_words(chunk, include_details=flag) / WORDS_PER_MINUTE))
                    for flag in (False, True)
                ]
                times[current] = (minutes[0], minutes[1], optional)
    return times


def render_ledger_table(spec: dict) -> str:
    columns = spec["columns"]
    numeric = set(spec.get("numeric", []))

    def cls(column: str) -> str:
        return ' class="num"' if column in numeric else ""

    head = "".join(f'<th scope="col"{cls(c)}>{html.escape(c)}</th>' for c in columns)
    body = []
    for row in spec["rows"]:
        cells = row["cells"] if isinstance(row, dict) else row
        marker = ' data-derived="1"' if isinstance(row, dict) and row.get("derived") else ""
        tds = "".join(
            f'<td data-col="{html.escape(c)}"{cls(c)}>{html.escape(str(v))}</td>' for c, v in zip(columns, cells)
        )
        body.append(f"<tr{marker}>{tds}</tr>")
    return f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody>"


def _inside(path: Path, parent: Path) -> bool:
    resolved, root = path.resolve(), parent.resolve()
    return resolved == root or root in resolved.parents


def build(workdir: Path, *, src_name: str = "brief.src.html", out_name: str = "summary.html",
          evidence_name: str = "evidence.json", source_name: str = "sources/paper.html") -> Report:
    report = Report()
    src_path = workdir / src_name
    markup = src_path.read_text(encoding="utf-8")
    evidence_path = workdir / evidence_name
    evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.exists() else {}
    if not evidence:
        report.warn(f"No {evidence_name}: numbers in this brief are not checked against the source.")
    source_path = workdir / source_name
    source = SourceDoc.load(source_path) if source_path.exists() else None
    if source is None:
        report.warn(f"No {source_name}: source anchors, math tokens and ledger rows cannot be verified.")

    # 1. Paper math, copied from the source's own MathML.
    def fill_equation(match: re.Match[str]) -> str:
        mathml = source.equation(match.group(3)) if source else None
        if mathml is None:
            report.error(f"data-eq=\"{match.group(3)}\": no equation with that id in the source HTML.")
            return match.group(0)
        return match.group(1) + mathml + match.group(4)

    def fill_inline(match: re.Match[str]) -> str:
        alttext = html.unescape(match.group(3))
        mathml = source.inline_math(alttext) if source else None
        if mathml is None:
            report.error(f"data-m=\"{alttext}\": no math with that alttext in the source (try the `math` command).")
            return match.group(0)
        return match.group(1) + mathml + match.group(4)

    markup, equations = re.subn(r'(<(div|p|span)\b[^>]*\bdata-eq="([^"]+)"[^>]*>)\s*(</\2>)', fill_equation, markup)
    markup, inline_math = re.subn(r'(<(span)\b[^>]*\bdata-m="([^"]+)"[^>]*>)\s*(</\2>)', fill_inline, markup)

    # 2. Ledger tables: evidence.json is the single source for tabulated numbers.
    tables = evidence.get("tables", {})

    def fill_table(match: re.Match[str]) -> str:
        name = match.group(2)
        if name not in tables:
            report.error(f"data-ledger-table=\"{name}\": no such table in {evidence_name}.")
            return match.group(0)
        return match.group(1) + match.group(3) + render_ledger_table(tables[name]) + match.group(4)

    markup, ledger_tables = re.subn(
        r'(<table\b[^>]*\bdata-ledger-table="([^"]+)"[^>]*>)(.*?)(</table>)', fill_table, markup, flags=re.S
    )

    # 3. Glossary auto-links, then reading times.
    markup, linked_terms = autolink_terms(markup, glossary_terms(markup))
    times = section_reading_times(markup)
    for section_id, (read, _full, _optional) in times.items():
        markup = re.sub(
            rf'(<span\b[^>]*\bdata-rt="{re.escape(section_id)}"[^>]*>)[^<]*(</span>)', rf"\g<1>{read} min\g<2>", markup
        )
    total_read = sum(read for read, _, optional in times.values() if not optional)
    total_full = sum(full for _, full, _ in times.values())
    markup = re.sub(
        r"(<span\b[^>]*\bdata-rt-total\b[^>]*>)[^<]*(</span>)",
        rf"\g<1>{total_read} min read · {total_full} min with every detail open\g<2>",
        markup,
    )

    # 4. Embed local images so the brief works offline as one file.
    embedded = 0

    def embed(match: re.Match[str]) -> str:
        nonlocal embedded
        ref = html.unescape(match.group(2))
        if ref.startswith("data:") or "://" in ref:
            return match.group(0)
        path = workdir / ref
        if not _inside(path, workdir) or not path.is_file():
            report.error(f"Image not found inside the brief folder: {ref}")
            return match.group(0)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        embedded += 1
        return f'{match.group(1)}data:{mime};base64,{base64.b64encode(path.read_bytes()).decode("ascii")}{match.group(3)}'

    markup = re.sub(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', embed, markup)

    # 5. Inline the kit last, so its code is never rewritten by the steps above.
    for token, filename, wrapper in (
        ("<!-- brief:kit-css -->", "brief.css", "<style>\n{}\n</style>"),
        ("<!-- brief:kit-js -->", "brief.js", "<script>\n{}\n</script>"),
    ):
        if token not in markup:
            report.error(f"Missing {token} in {src_name}.")
            continue
        markup = markup.replace(token, wrapper.format((KIT_DIR / filename).read_text(encoding="utf-8")))

    lint(markup, report, evidence=evidence, source=source)
    report.stats.update(
        {
            "bytes": len(markup.encode("utf-8")),
            "equations": equations,
            "inline_math": inline_math,
            "ledger_tables": ledger_tables,
            "glossary_links": linked_terms,
            "images_embedded": embedded,
            "reading_minutes": total_read,
            "reading_minutes_with_details": total_full,
            "sections": {key: value[0] for key, value in times.items()},
        }
    )
    (workdir / out_name).write_text(markup, encoding="utf-8")
    (workdir / "brief-check.json").write_text(
        json.dumps(report.as_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


# ───────────────────────────────── lint ─────────────────────────────────────


def _verify_rows(report: Report, source: SourceDoc, label: str, anchor: str, needles: list[str]) -> bool:
    rows = source.rows(anchor)
    if rows is None:
        report.error(f"{label}: source anchor #{anchor} does not exist.")
        return False
    if not any(row_matches(cells, needles) for cells in rows):
        report.error(f"{label}: no row of #{anchor} contains all of {needles}.")
        return False
    return True


def lint(markup: str, report: Report, *, evidence: dict, source: SourceDoc | None) -> None:
    soup = BeautifulSoup(markup, "html.parser")
    paper = evidence.get("paper", {})
    pinned = f"{paper.get('id', '')}{paper.get('version', '')}" if paper.get("id") else ""

    # Document basics.
    if not soup.find("html") or not soup.find("html").get("lang"):
        report.error("<html> needs a lang attribute.")
    if not soup.find("title") or not soup.find("title").get_text(strip=True):
        report.error("Missing <title>.")
    if not soup.find("meta", attrs={"name": "viewport"}):
        report.error("Missing viewport meta.")
    if not soup.find("main"):
        report.error("Missing <main>.")

    # Portable: nothing may load from the network.
    for node in soup.find_all(["script", "img", "iframe", "object", "embed", "source", "video", "audio", "link"]):
        ref = node.get("src") or node.get("data") or (node.get("href") if node.name == "link" else None)
        if node.name in {"iframe", "object", "embed"}:
            report.error(f"<{node.name}> is not allowed in a self-contained brief.")
        elif node.name == "link" and "stylesheet" in (node.get("rel") or []):
            report.error(f"External stylesheet: {ref}")
        elif ref and not ref.startswith("data:") and node.name != "link":
            report.error(f"<{node.name}> loads an external or un-embedded resource: {ref[:80]}")
    styles = " ".join(s.get_text() for s in soup.find_all("style"))
    styles += " ".join(n.get("style", "") for n in soup.find_all(style=True))
    if re.search(r"@import|url\(\s*['\"]?(https?:)?//", styles):
        report.error("CSS loads a remote resource (@import or url(http…)).")
    for node in soup.find_all(string=PLACEHOLDER_RE):
        if node.find_parent(["script", "style", "pre", "code"]) is None:
            report.error(f"Placeholder text left in the brief: {str(node).strip()[:70]!r}")
    for node in soup.find_all(attrs={"data-eq": True}) + soup.find_all(attrs={"data-m": True}):
        if not node.find("math"):
            report.error(f"Unfilled math token: {node.get('data-eq') or node.get('data-m')}")

    # Structure.
    seen: set[str] = set()
    for node in soup.find_all(id=True):
        if node.find_parent(["svg", "math"]) is not None:
            continue
        if node["id"] in seen:
            report.error(f"Duplicate id: {node['id']}")
        seen.add(node["id"])
    ids = {node["id"] for node in soup.find_all(id=True)}
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if href.startswith("#") and href[1:] and href[1:] not in ids:
            report.error(f"Broken in-page link: {href}")
    for details in soup.find_all("details"):
        if not details.find("summary", recursive=False):
            report.error("<details> without a <summary>.")
    for img in soup.find_all("img"):
        alt = (img.get("alt") or "").strip()
        if not alt:
            report.error("Image without alt text.")
        elif alt.lower() in {"refer to caption", "figure", "image"}:
            report.warn(f"Generic alt text: {alt!r}. Describe what the figure shows.")

    # Learning parts.
    parts = {node["data-part"] for node in soup.find_all(attrs={"data-part": True})}
    for part in EXPECTED_PARTS:
        if part not in parts:
            report.warn(f"No data-part=\"{part}\". Add it, or say in the report why this paper does not need it.")
    checks = soup.select("details.check")
    if "check" in parts and len(checks) < MIN_CHECK_QUESTIONS:
        report.warn(f"Only {len(checks)} check-yourself question(s); aim for at least {MIN_CHECK_QUESTIONS}.")
    for check in checks:
        target = check.get("data-review", "")
        if not target.startswith("#") or target[1:] not in ids:
            report.error(f"Check-yourself question needs data-review=\"#section-id\" (got {target!r}).")
    used_terms = {a["href"][1:] for a in soup.select("a.term[href^='#']")}
    for dt in soup.select("dl.glossary dt[id]"):
        if dt["id"] not in used_terms:
            report.warn(f"Glossary entry never used in the text: {dt.get_text(strip=True)}")
    nav_targets = [a["href"][1:] for a in soup.select(".rail nav a[href^='#']")]
    for section in (soup.find("main") or soup).find_all("section", id=True, recursive=False):
        if section["id"] not in nav_targets:
            report.warn(f"Section #{section['id']} is missing from the rail navigation.")

    # Source links: version-pinned, and every arXiv HTML anchor must exist.
    verified_anchors = 0
    for link in soup.find_all("a", href=True):
        href = link["href"]
        match = re.match(r"https://arxiv\.org/(abs|html|pdf)/(\d{4}\.\d{4,5})(v\d+)?(?:#(.+))?$", href)
        if not match or (paper.get("id") and match.group(2) != paper["id"]):
            continue
        if pinned and f"{match.group(2)}{match.group(3) or ''}" != pinned:
            report.error(f"Source link is not pinned to {pinned}: {href}")
        if match.group(1) == "html" and match.group(4):
            if source is None:
                continue
            if match.group(4) in source.ids:
                verified_anchors += 1
            else:
                report.error(f"Source anchor does not exist in the downloaded HTML: {href}")
    report.stats["source_anchors_verified"] = verified_anchors

    # Evidence ledger: transcribed numbers must be in the cited source element.
    page_text = _norm((soup.find("main") or soup).get_text(" "))
    checked = 0
    for claim in evidence.get("claims", []):
        label = f"claim {claim.get('id', '?')}"
        anchor = claim.get("anchor")
        transcribed = [str(v) for v in claim.get("transcribed", [])]
        if source is not None and anchor:
            text = source.text(anchor)
            if text is None:
                report.error(f"{label}: source anchor #{anchor} does not exist.")
            else:
                for value in transcribed:
                    if _norm(value) in text:
                        checked += 1
                    else:
                        report.error(f"{label}: {value!r} is not in the text of #{anchor}.")
                for needles in claim.get("rows", []):
                    checked += _verify_rows(report, source, label, anchor, [str(n) for n in needles])
        elif transcribed and not anchor:
            report.warn(f"{label}: transcribed values without an anchor cannot be verified.")
        if claim.get("shown", True):
            for value in transcribed:
                if _norm(value) not in page_text:
                    report.warn(f"{label}: ledger value {value!r} does not appear in the brief.")
    for name, spec in evidence.get("tables", {}).items():
        columns = spec["columns"]
        default_verify = spec.get("verify", [])
        for index, row in enumerate(spec["rows"]):
            cells = row["cells"] if isinstance(row, dict) else row
            if len(cells) != len(columns):
                report.error(f"table {name} row {index}: expected {len(columns)} cells, got {len(cells)}.")
                continue
            options = row if isinstance(row, dict) else {}
            if options.get("derived"):
                continue
            anchor = options.get("anchor", spec.get("anchor"))
            needles = options.get("match") or [cells[columns.index(c)] for c in default_verify]
            if source is None or not anchor or not needles:
                continue
            checked += _verify_rows(report, source, f"table {name} row {index} {cells[:2]}", anchor, [str(n) for n in needles])
    report.stats["ledger_values_verified"] = checked
    report.stats["check_questions"] = len(checks)
    report.stats["words"] = _visible_words(str(soup.find("main") or ""), include_details=True)


# ────────────────────────────────── CLI ─────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="Download version-pinned sources and figure assets for an arXiv paper.")
    p_fetch.add_argument("arxiv_id", help="e.g. 2610.22293 or 2610.22293v1")
    p_fetch.add_argument("--out", type=Path, required=True, help="Brief folder (.artifacts/summarize-paper/<id>)")
    p_fetch.add_argument("--user-agent", default=DEFAULT_USER_AGENT)

    p_math = sub.add_parser("math", help="List LaTeX alttext strings available for data-m tokens.")
    p_math.add_argument("source_html", type=Path)
    p_math.add_argument("--grep", default="", help="Only show alttext containing this substring.")

    p_build = sub.add_parser("build", help="Build and lint summary.html from brief.src.html.")
    p_build.add_argument("workdir", type=Path)
    p_build.add_argument("--src", default="brief.src.html")
    p_build.add_argument("--out", default="summary.html")
    p_build.add_argument("--evidence", default="evidence.json")
    p_build.add_argument("--source-html", default="sources/paper.html")

    args = parser.parse_args(argv)
    if args.command == "fetch":
        metadata = fetch(args.arxiv_id, args.out, user_agent=args.user_agent)
        print(json.dumps(metadata, indent=2, ensure_ascii=False))
        return 0
    if args.command == "math":
        for alttext in SourceDoc.load(args.source_html).alttexts():
            if args.grep in alttext:
                print(alttext)
        return 0
    report = build(args.workdir, src_name=args.src, out_name=args.out, evidence_name=args.evidence,
                   source_name=args.source_html)
    for message in report.errors:
        print(f"ERROR  {message}", file=sys.stderr)
    for message in report.warnings:
        print(f"warn   {message}", file=sys.stderr)
    print(json.dumps({"ok": not report.errors, "errors": len(report.errors), "warnings": len(report.warnings),
                      "output": str(args.workdir / args.out), **report.stats}, indent=2))
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
