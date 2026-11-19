"""Tests for the HTML brief skill helper (skills/shared/html-brief/build_brief.py)."""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "skills" / "shared" / "html-brief"
_SPEC = importlib.util.spec_from_file_location("build_brief", KIT_DIR / "build_brief.py")
build_brief = importlib.util.module_from_spec(_SPEC)
sys.modules["build_brief"] = build_brief  # dataclasses resolve annotations via sys.modules
_SPEC.loader.exec_module(build_brief)

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

SOURCE_HTML = """<html><body><article>
<section id="S1"><h2>1 Introduction</h2>
<div class="ltx_para" id="S1.p1"><p>The probe loss rises by
<math alttext="13.0" display="inline"><semantics><mn>13.0</mn><annotation encoding="application/x-tex">13.0</annotation></semantics></math>
when the update <math alttext="\\Delta W" display="inline" id="S1.p1.m2" class="ltx_Math"><semantics><mrow><mi mathvariant="normal">Δ</mi><mi>W</mi></mrow><annotation encoding="application/x-tex">\\Delta W</annotation></semantics></math> is unconstrained.</p></div>
<table id="S1.E1" class="ltx_equation"><tr><td><math alttext="y=Wx" display="block" id="S1.E1.m1"><semantics><mrow><mi>y</mi><mo>=</mo><mi>W</mi><mi>x</mi></mrow><annotation encoding="application/x-tex">y=Wx</annotation></semantics></math></td></tr></table>
<figure id="S1.F1" class="ltx_figure"><img src="0000.00001v2/x1.png" alt="Refer to caption"><object data="0000.00001v2/plot.svg"></object>
<img src="https://elsewhere.example/tracker.png"><img src="../../etc/x.png">
<figcaption>Figure 1: A figure.</figcaption></figure>
</section>
<figure id="S2.T1" class="ltx_table"><table>
<tr><th>Method</th><th>ID</th><th>Prior</th><th>Delta</th></tr>
<tr><td>SFT</td><td>77.6</td><td>55.5</td><td><math alttext="-25.5"><semantics><mrow><mo>−</mo><mn>25.5</mn></mrow><annotation encoding="application/x-tex">-25.5</annotation></semantics></math></td></tr>
<tr><td>SFT+SST</td><td><span class="ltx_font_bold">80.1</span></td><td>80.0</td><td>-1.0</td></tr>
</table><figcaption>Table 1: Results.</figcaption></figure>
</article></body></html>"""

BRIEF_SRC = """<!doctype html>
<html lang="en" data-paper="0000.00001v2"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Test brief</title>
<!-- brief:kit-css -->
</head><body><div class="page">
<aside class="rail"><nav><ol>
<li><a href="#minute"><span>In one minute</span><span class="rt" data-rt="minute"></span></a></li>
<li><a href="#idea"><span>The idea</span><span class="rt" data-rt="idea"></span></a></li>
<li><a href="#reference"><span>Reference</span><span class="rt" data-rt="reference"></span></a></li>
</ol></nav></aside>
<main>
<header><h1>A Paper</h1><p><a href="https://arxiv.org/abs/0000.00001v2">Abstract</a> <span data-rt-total></span></p></header>
<section id="minute" data-part="verdict">
<h2>SFT in a heading is not linked</h2>
<p>Plain SFT loses 25.5 points while SFT+SST loses 1.0. Later SFT is mentioned again.
<a class="cite" href="https://arxiv.org/html/0000.00001v2#S2.T1">Table 1</a></p>
</section>
<section id="idea" data-part="mechanism">
<h2>The idea</h2>
<p>SFT applies the whole update <span data-m="\\Delta W"></span>; the probe loss rises by 13.0. <code>SFT</code></p>
<div class="eq" data-eq="S1.E1"></div>
<figure class="paper-fig"><img src="sources/0000.00001v2/x1.png" alt="A one pixel test figure."></figure>
<table data-ledger-table="main"><caption>Results</caption></table>
<details class="check" id="q1" data-review="#idea"><summary>Why?</summary><div class="answer"><p>Because.</p></div></details>
</section>
<section id="reference" data-optional>
<h2>Reference</h2>
<dl class="glossary" data-part="glossary"><dt id="g-sft" data-match="SFT">SFT</dt><dd>Supervised fine-tuning.</dd></dl>
</section>
</main></div>
<!-- brief:kit-js -->
</body></html>"""

EVIDENCE = {
    "paper": {"id": "0000.00001", "version": "v2"},
    "tables": {
        "main": {
            "anchor": "S2.T1",
            "columns": ["Method", "ID", "Prior"],
            "numeric": ["ID", "Prior"],
            "verify": ["Method", "ID", "Prior"],
            "rows": [["SFT", "77.6", "55.5"], ["SFT+SST", "80.1", "80.0"]],
        }
    },
    "claims": [
        {"id": "probe", "anchor": "S1.p1", "transcribed": ["13.0"]},
        {"id": "delta", "anchor": "S2.T1", "rows": [["SFT", "77.6", "-25.5"]], "transcribed": ["25.5"]},
    ],
}


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    assets = tmp_path / "sources" / "0000.00001v2"
    assets.mkdir(parents=True)
    (assets / "x1.png").write_bytes(PNG_1X1)
    (tmp_path / "sources" / "paper.html").write_text(SOURCE_HTML, encoding="utf-8")
    (tmp_path / "brief.src.html").write_text(BRIEF_SRC, encoding="utf-8")
    (tmp_path / "evidence.json").write_text(json.dumps(EVIDENCE), encoding="utf-8")
    return tmp_path


def _rewrite(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_build_produces_one_verified_self_contained_file(workdir: Path) -> None:
    report = build_brief.build(workdir)

    assert report.errors == []
    built = (workdir / "summary.html").read_text(encoding="utf-8")
    assert "brief:kit" not in built
    assert "--measure" in built and "check-summary" in built  # kit CSS and JS inlined
    assert 'src="data:image/png;base64,' in built
    assert "<mi>y</mi>" in built and "annotation" not in built and "S1.E1.m1" not in built
    assert '<td data-col="ID" class="num">80.1</td>' in built
    assert "1 min" in built and "min read" in built
    assert report.stats["source_anchors_verified"] == 1
    assert report.stats["ledger_values_verified"] == 5  # 2 table rows, 2 transcribed values, 1 claim row
    assert report.stats["images_embedded"] == 1
    check = json.loads((workdir / "brief-check.json").read_text(encoding="utf-8"))
    assert check["ok"] is True


def test_glossary_terms_link_once_per_section_outside_headings_and_code(workdir: Path) -> None:
    build_brief.build(workdir)
    built = (workdir / "summary.html").read_text(encoding="utf-8")

    assert built.count('<a class="term" href="#g-sft">SFT</a>') == 2  # one per section
    assert "<h2>SFT in a heading is not linked</h2>" in built
    assert "<code>SFT</code>" in built
    assert "SFT+SST loses" in built  # compound method names are left alone
    assert '<dt id="g-sft" data-match="SFT">SFT</dt>' in built


@pytest.mark.parametrize(
    ("target", "old", "new", "expected"),
    [
        ("brief.src.html", "#S2.T1", "#S9.T9", "Source anchor does not exist"),
        ("brief.src.html", "abs/0000.00001v2", "abs/0000.00001", "not pinned to 0000.00001v2"),
        ("brief.src.html", 'data-m="\\Delta W"', 'data-m="\\nabla W"', "no math with that alttext"),
        ("brief.src.html", 'data-eq="S1.E1"', 'data-eq="S7.E7"', "no equation with that id"),
        ("brief.src.html", "x1.png", "missing.png", "Image not found"),
        ("brief.src.html", 'data-review="#idea"', 'data-review="#nowhere"', "data-review"),
        ("brief.src.html", "<p>Because.</p>", '<p>Because. <a href="#gone">x</a></p>', "Broken in-page link: #gone"),
        ("brief.src.html", "<p>Because.</p>", "<p>TODO write this.</p>", "Placeholder text"),
        ("brief.src.html", "<!-- brief:kit-js -->", '<!-- brief:kit-js --><script src="https://cdn.example/x.js"></script>', "external or un-embedded"),
        ("brief.src.html", 'alt="A one pixel test figure."', 'alt=""', "Image without alt text"),
        ("evidence.json", '"80.1"', '"80.7"', "no row of #S2.T1 contains all of"),
        ("evidence.json", '"13.0"', '"31.0"', "is not in the text of #S1.p1"),
        ("evidence.json", '"anchor": "S1.p1"', '"anchor": "S1.p9"', "source anchor #S1.p9 does not exist"),
    ],
)
def test_lint_catches_fidelity_and_portability_errors(
    workdir: Path, target: str, old: str, new: str, expected: str
) -> None:
    _rewrite(workdir / target, old, new)

    report = build_brief.build(workdir)

    assert any(expected in message for message in report.errors), report.errors
    assert json.loads((workdir / "brief-check.json").read_text(encoding="utf-8"))["ok"] is False


def test_build_without_sources_warns_instead_of_claiming_verification(workdir: Path) -> None:
    (workdir / "sources" / "paper.html").unlink()
    _rewrite(workdir / "brief.src.html", '<span data-m="\\Delta W"></span>', "the update")
    _rewrite(workdir / "brief.src.html", '<div class="eq" data-eq="S1.E1"></div>', "")

    report = build_brief.build(workdir)

    assert report.errors == []
    assert any("cannot be verified" in message for message in report.warnings)
    assert report.stats["ledger_values_verified"] == 0


def test_row_matches_requires_exact_numbers_but_allows_label_containment() -> None:
    cells = ["SFT+SST+R10", "84.0", "80.2", "-0.8"]
    assert build_brief.row_matches(cells, ["SST+R10", "84.0", "-0.8"])
    assert not build_brief.row_matches(cells, ["SFT+SST+R10", "4.0"])  # 4.0 is not 84.0
    assert not build_brief.row_matches(cells, ["SDFT", "84.0"])


def test_source_rows_normalise_math_minus_and_bold_cells() -> None:
    rows = build_brief.SourceDoc(SOURCE_HTML).rows("S2.T1")
    assert ["SFT", "77.6", "55.5", "-25.5"] in rows
    assert ["SFT+SST", "80.1", "80.0", "-1.0"] in rows


def test_reading_times_leave_optional_sections_out_of_the_total(workdir: Path) -> None:
    long_text = " ".join(["word"] * 600)
    _rewrite(workdir / "brief.src.html", "<p>Because.</p>", f"<p>{long_text}</p>")
    _rewrite(workdir / "brief.src.html", "<dd>Supervised fine-tuning.</dd>", f"<dd>{long_text}</dd>")

    report = build_brief.build(workdir)

    assert report.stats["sections"]["idea"] == 1  # the 600 words sit inside <details>
    assert report.stats["sections"]["reference"] == 3
    assert report.stats["reading_minutes"] == 2  # minute + idea; reference is optional
    assert report.stats["reading_minutes_with_details"] == 1 + 3 + 3


def test_html_to_markdown_keeps_anchors_math_and_asset_paths() -> None:
    text = build_brief.html_to_markdown(SOURCE_HTML)

    assert "## 1 Introduction  {#S1}" in text
    assert "$\\Delta W$" in text and "$$y=Wx$$" in text
    assert "[FIGURE #S1.F1]" in text and "0000.00001v2/plot.svg" in text
    assert "[TABLE #S2.T1]" in text and "| SFT+SST | 80.1 | 80.0 | -1.0 |" in text


def test_fetch_pins_latest_version_and_only_downloads_arxiv_assets(tmp_path: Path) -> None:
    abs_page = (
        '<meta name="citation_title" content="A Paper"><meta name="citation_author" content="Lovelace, Ada">'
        '<meta name="citation_date" content="2026/01/02">'
        '<a href="/abs/0000.00001v1">v1</a><a href="/abs/0000.00001v2">v2</a>'
    )
    calls: list[str] = []

    def getter(url: str, user_agent: str) -> bytes:
        calls.append(url)
        assert user_agent == build_brief.DEFAULT_USER_AGENT
        if "/abs/" in url:
            return abs_page.encode()
        if url.endswith("/html/0000.00001v2"):
            return SOURCE_HTML.encode()
        return PNG_1X1

    metadata = build_brief.fetch("0000.00001", tmp_path, pause=0, getter=getter)

    assert metadata["version"] == "v2" and metadata["has_html"] is True
    assert metadata["authors"] == ["Ada Lovelace"] and metadata["submitted"] == "2026-01-02"
    assert metadata["assets"] == ["sources/0000.00001v2/plot.svg", "sources/0000.00001v2/x1.png"]
    assert (tmp_path / "sources" / "0000.00001v2" / "x1.png").read_bytes() == PNG_1X1
    assert "{#S1}" in (tmp_path / "paper.md").read_text(encoding="utf-8")
    assert all(url.startswith("https://arxiv.org/") for url in calls)
    assert not any("elsewhere.example" in url or "/etc/" in url for url in calls)
    assert json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))["html"].endswith("0000.00001v2")


def test_fetch_falls_back_to_pdf_when_the_paper_has_no_arxiv_html(tmp_path: Path) -> None:
    def getter(url: str, user_agent: str) -> bytes:
        if "/html/" in url:
            raise OSError("HTTP Error 404: Not Found")
        return b"<a href='/abs/0000.00001v1'>v1</a>" if "/abs/" in url else b"%PDF-1.7"

    metadata = build_brief.fetch("0000.00001v1", tmp_path, pause=0, getter=getter)

    assert metadata["has_html"] is False
    assert (tmp_path / "sources" / "paper.pdf").read_bytes() == b"%PDF-1.7"
    assert any("No arXiv HTML" in warning for warning in metadata["warnings"])


def test_cli_build_exit_code_follows_lint(workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert build_brief.main(["build", str(workdir)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    _rewrite(workdir / "evidence.json", '"77.6"', '"76.6"')
    assert build_brief.main(["build", str(workdir)]) == 1
    captured = capsys.readouterr()
    assert "ERROR" in captured.err and json.loads(captured.out)["ok"] is False


def test_skeleton_declares_every_expected_part_and_kit_token() -> None:
    skeleton = (KIT_DIR / "skeleton.html").read_text(encoding="utf-8")

    for part in build_brief.EXPECTED_PARTS:
        assert f'data-part="{part}"' in skeleton, part
    assert "<!-- brief:kit-css -->" in skeleton and "<!-- brief:kit-js -->" in skeleton
    sections = build_brief.section_reading_times(skeleton)
    for section_id in sections:
        assert f'href="#{section_id}"' in skeleton and f'data-rt="{section_id}"' in skeleton
    assert sections["reference"][2] is True
