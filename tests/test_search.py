"""Tests for paper_assistant.search."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from paper_assistant.config import Config
from paper_assistant.models import (
    Paper,
    PaperMetadata,
    ProcessingStatus,
    ReadingStatus,
)
from paper_assistant.search import (
    EmbeddingsNotAvailableError,
    SearchCancelledError,
    SearchManager,
    _strip_summary_header,
    get_search_manager,
)
from paper_assistant.storage import StorageManager


def _make_config(tmp_path: Path, **overrides) -> Config:
    defaults = {
        "data_dir": tmp_path,
        "qmd_enabled": True,
        "qmd_command": ["qmd"],
        "qmd_index_name": "test-index",
        "qmd_collection_name": "papers",
    }
    defaults.update(overrides)
    return Config(**defaults)


def _make_paper(
    paper_id: str = "2503.10291",
    title: str = "Test Paper",
    tags: list[str] | None = None,
    summary_path: str | None = "papers/[Paper][2503.10291] Test Paper.md",
    reading_status: ReadingStatus = ReadingStatus.UNREAD,
) -> Paper:
    return Paper(
        metadata=PaperMetadata(
            arxiv_id=paper_id,
            title=title,
            authors=["Alice", "Bob"],
            abstract="Abstract text",
            published=datetime(2025, 3, 13, tzinfo=timezone.utc),
            categories=["cs.CV"],
            arxiv_url=f"https://arxiv.org/abs/{paper_id}",
            pdf_url=f"https://arxiv.org/pdf/{paper_id}",
        ),
        tags=tags or ["RL", "Reasoning"],
        status=ProcessingStatus.COMPLETE,
        summary_path=summary_path,
        reading_status=reading_status,
    )


# --- get_search_manager ---


class TestGetSearchManager:
    def test_returns_none_when_disabled(self, tmp_path):
        config = _make_config(tmp_path, qmd_enabled=False)
        assert get_search_manager(config) is None

    @patch("paper_assistant.search.SearchManager.is_available", return_value=False)
    def test_returns_none_when_binary_missing(self, _mock, tmp_path):
        config = _make_config(tmp_path)
        assert get_search_manager(config) is None

    @patch("paper_assistant.search.SearchManager.is_available", return_value=True)
    def test_returns_manager_when_available(self, _mock, tmp_path):
        config = _make_config(tmp_path)
        mgr = get_search_manager(config)
        assert mgr is not None
        assert isinstance(mgr, SearchManager)


# --- is_available ---


class TestIsAvailable:
    @patch("subprocess.run")
    def test_available(self, mock_run, tmp_path):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        config = _make_config(tmp_path)
        mgr = SearchManager(config)
        assert mgr.is_available() is True
        # Should call without cwd (no data_dir dependency)
        call_kwargs = mock_run.call_args[1]
        assert "cwd" not in call_kwargs

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_not_available(self, _mock, tmp_path):
        config = _make_config(tmp_path)
        mgr = SearchManager(config)
        assert mgr.is_available() is False

    @patch("subprocess.run")
    def test_cached(self, mock_run, tmp_path):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        config = _make_config(tmp_path)
        mgr = SearchManager(config)
        mgr.is_available()
        mgr.is_available()
        assert mock_run.call_count == 1

    @patch("subprocess.run")
    def test_works_without_data_dir(self, mock_run):
        """is_available works even when data_dir doesn't exist yet."""
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        config = _make_config(Path("/nonexistent/path"))
        mgr = SearchManager(config)
        assert mgr.is_available() is True


# --- Command construction ---


class TestRunQmd:
    @patch("subprocess.run")
    def test_command_construction(self, mock_run, tmp_path):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        config = _make_config(tmp_path, qmd_command=["npx", "@tobilu/qmd"])
        mgr = SearchManager(config)
        mgr._run_qmd(["search", "test"])

        mock_run.assert_called_once_with(
            ["npx", "@tobilu/qmd", "--index", "test-index", "search", "test"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )

    @pytest.mark.skipif(os.name == "nt", reason="POSIX process group behavior")
    def test_cancellable_command_terminates_process_group(self, tmp_path):
        process_started = threading.Event()
        process_group_terminated = threading.Event()
        process_terminated_directly = threading.Event()
        cancel_event = threading.Event()
        errors = []
        popen_kwargs = {}

        class HangingProcess:
            pid = 4242
            returncode = None

            def __init__(self, *args, **kwargs):
                popen_kwargs.update(kwargs)
                process_started.set()

            def communicate(self, timeout=None):
                if not process_group_terminated.is_set():
                    raise subprocess.TimeoutExpired("qmd", timeout)
                self.returncode = -15
                return "", ""

            def terminate(self):
                process_terminated_directly.set()

            def kill(self):
                process_terminated_directly.set()

        config = _make_config(tmp_path)
        mgr = SearchManager(config)

        def run_search():
            try:
                mgr.search("test", cancel_event=cancel_event)
            except Exception as exc:
                errors.append(exc)

        def terminate_group(pid, sig):
            assert pid == HangingProcess.pid
            assert sig == signal.SIGTERM
            process_group_terminated.set()

        with (
            patch("subprocess.Popen", HangingProcess),
            patch("paper_assistant.search.os.killpg", side_effect=terminate_group) as mock_killpg,
        ):
            search_thread = threading.Thread(target=run_search)
            search_thread.start()
            assert process_started.wait(timeout=1)
            cancel_event.set()
            search_thread.join(timeout=2)

        assert not search_thread.is_alive()
        assert popen_kwargs["start_new_session"] is True
        mock_killpg.assert_called_once_with(HangingProcess.pid, signal.SIGTERM)
        assert process_group_terminated.is_set()
        assert not process_terminated_directly.is_set()
        assert len(errors) == 1
        assert isinstance(errors[0], SearchCancelledError)


# --- setup ---


class TestSetup:
    @patch("subprocess.run")
    def test_setup_creates_collection(self, mock_run, tmp_path):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        config = _make_config(tmp_path)
        mgr = SearchManager(config)
        mgr.setup()

        assert (tmp_path / "search").is_dir()
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "collection" in cmd
        assert "add" in cmd

    @patch("subprocess.run")
    def test_setup_idempotent_stderr(self, mock_run, tmp_path):
        """'already exists' on stderr is swallowed."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "qmd", stderr="Collection 'papers' already exists."
        )
        config = _make_config(tmp_path)
        mgr = SearchManager(config)
        mgr.setup()

    @patch("subprocess.run")
    def test_setup_idempotent_stdout(self, mock_run, tmp_path):
        """'already exists' on stdout is swallowed (qmd outputs there)."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "qmd", output="Collection 'papers' already exists.", stderr=""
        )
        config = _make_config(tmp_path)
        mgr = SearchManager(config)
        # Should not raise
        mgr.setup()


# --- Search doc generation ---


class TestSearchDocGeneration:
    def test_sync_paper_writes_doc(self, tmp_path):
        config = _make_config(tmp_path)
        # Create a summary file
        (tmp_path / "papers").mkdir(exist_ok=True)
        summary_content = (
            "---\ntitle: \"Test Paper\"\narxiv_id: 2503.10291\n---\n\n"
            "# Test Paper\n\n**arXiv**: [2503.10291](https://arxiv.org/abs/2503.10291)\n\n---\n\n"
            "# One-Pager\n\nThis is the summary body."
        )
        (tmp_path / "papers" / "[Paper][2503.10291] Test Paper.md").write_text(summary_content)

        storage = StorageManager(config)
        paper = _make_paper()
        storage.add_paper(paper)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            mgr = SearchManager(config)
            mgr.sync_paper("2503.10291", storage)

        doc_path = tmp_path / "search" / "2503.10291.md"
        assert doc_path.exists()
        content = doc_path.read_text()
        assert 'paper_id: "2503.10291"' in content
        assert 'title: "Test Paper"' in content
        assert "tags:" in content
        assert "reading_status: unread" in content
        assert "One-Pager" in content
        assert "This is the summary body." in content

        # sync_paper must run BOTH qmd update (BM25) and qmd embed (vectors),
        # otherwise hybrid search degrades silently for newly-imported papers.
        subcommands = [c[0][0][-1] for c in mock_run.call_args_list]
        assert subcommands.count("update") == 1
        assert subcommands.count("embed") == 1

    def test_sync_paper_noop_without_summary(self, tmp_path):
        config = _make_config(tmp_path)
        storage = StorageManager(config)
        paper = _make_paper(summary_path=None)
        storage.add_paper(paper)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            mgr = SearchManager(config)
            mgr.sync_paper("2503.10291", storage)

        assert not (tmp_path / "search" / "2503.10291.md").exists()
        mock_run.assert_not_called()

    def test_sync_paper_creates_search_dir(self, tmp_path):
        config = _make_config(tmp_path)
        (tmp_path / "papers").mkdir(exist_ok=True)
        summary_content = "---\ntitle: \"Test\"\n---\n\n# Test\n\n---\n\nBody."
        (tmp_path / "papers" / "[Paper][2503.10291] Test Paper.md").write_text(summary_content)

        storage = StorageManager(config)
        paper = _make_paper()
        storage.add_paper(paper)

        assert not (tmp_path / "search").exists()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            mgr = SearchManager(config)
            mgr.sync_paper("2503.10291", storage)

        assert (tmp_path / "search").is_dir()


# --- delete_paper ---


class TestDeletePaper:
    def test_delete_removes_file(self, tmp_path):
        config = _make_config(tmp_path)
        search_dir = tmp_path / "search"
        search_dir.mkdir()
        doc = search_dir / "2503.10291.md"
        doc.write_text("test")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            mgr = SearchManager(config)
            mgr.delete_paper("2503.10291")

        assert not doc.exists()

    def test_delete_missing_file_ok(self, tmp_path):
        config = _make_config(tmp_path)
        (tmp_path / "search").mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            mgr = SearchManager(config)
            mgr.delete_paper("nonexistent")
            # Should not raise


# --- batch_sync ---


class TestBatchSync:
    def test_writes_multiple_docs_single_update(self, tmp_path):
        config = _make_config(tmp_path)
        (tmp_path / "papers").mkdir(exist_ok=True)
        for pid in ["2503.10291", "2503.99999"]:
            summary = f"---\ntitle: \"Paper {pid}\"\n---\n\n# Paper\n\n---\n\nBody for {pid}."
            (tmp_path / "papers" / f"[Paper][{pid}] Paper {pid}.md").write_text(summary)

        storage = StorageManager(config)
        for pid in ["2503.10291", "2503.99999"]:
            paper = _make_paper(
                paper_id=pid,
                title=f"Paper {pid}",
                summary_path=f"papers/[Paper][{pid}] Paper {pid}.md",
            )
            storage.add_paper(paper)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            mgr = SearchManager(config)
            mgr.batch_sync(["2503.10291", "2503.99999"], storage)

        assert (tmp_path / "search" / "2503.99999.md").exists()
        assert (tmp_path / "search" / "2503.10291.md").exists()
        # Both qmd update and qmd embed must run exactly once across the batch:
        # writing per-paper is fine, but invoking qmd twice per paper would be
        # wasteful and embedding only some of them would leave hybrid degraded.
        update_calls = [c for c in mock_run.call_args_list if "update" in c[0][0]]
        embed_calls = [c for c in mock_run.call_args_list if "embed" in c[0][0]]
        assert len(update_calls) == 1
        assert len(embed_calls) == 1


# --- rebuild_all ---


class TestRebuildAll:
    def test_rebuild_skips_papers_without_summary(self, tmp_path):
        config = _make_config(tmp_path)
        (tmp_path / "papers").mkdir(exist_ok=True)
        summary = "---\ntitle: \"Paper\"\n---\n\n# Paper\n\n---\n\nBody."
        (tmp_path / "papers" / "[Paper][2503.10291] Paper.md").write_text(summary)

        storage = StorageManager(config)
        storage.add_paper(_make_paper(paper_id="2503.10291", summary_path="papers/[Paper][2503.10291] Paper.md"))
        storage.add_paper(_make_paper(paper_id="2503.99999", summary_path=None))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            mgr = SearchManager(config)
            mgr.rebuild_all(storage)

        assert (tmp_path / "search" / "2503.10291.md").exists()
        assert not (tmp_path / "search" / "2503.99999.md").exists()


# --- JSON parsing ---


class TestSearchParsing:
    @patch("subprocess.run")
    def test_text_search_parses_json(self, mock_run, tmp_path):
        # Create search doc so paper_id reverse-mapping and title lookup work
        search_dir = tmp_path / "search"
        search_dir.mkdir()
        (search_dir / "2503.10291.md").write_text(
            '---\ntitle: "Real Paper Title"\n---\n\n# One-Pager\n\nBody.'
        )

        mock_run.return_value = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps([
                {
                    "docid": "#abc123",
                    "score": 0,
                    "file": "qmd://papers/2503-10291.md",
                    "title": "One-Pager",
                    "snippet": "test snippet",
                },
            ]),
            stderr="",
        )
        config = _make_config(tmp_path)
        mgr = SearchManager(config)
        results = mgr.search("test query", mode="text")

        assert len(results) == 1
        assert results[0].paper_id == "2503.10291"
        assert results[0].title == "Real Paper Title"
        assert results[0].snippet == "test snippet"

    @patch("subprocess.run")
    def test_vector_search_raises_on_missing_embeddings(self, mock_run, tmp_path):
        mock_run.return_value = subprocess.CompletedProcess(
            [],
            0,
            stdout="[]",
            stderr="Warning: 10 documents (100%) need embeddings. Run 'qmd embed' for better results.",
        )
        config = _make_config(tmp_path)
        mgr = SearchManager(config)

        with pytest.raises(EmbeddingsNotAvailableError):
            mgr.search("test", mode="vector")

    @patch("subprocess.run")
    def test_text_search_empty_results(self, mock_run, tmp_path):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="[]", stderr="")
        config = _make_config(tmp_path)
        mgr = SearchManager(config)
        results = mgr.search("nonexistent", mode="text")
        assert results == []

    @patch("subprocess.run")
    def test_text_search_bad_json(self, mock_run, tmp_path):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="not json", stderr="")
        config = _make_config(tmp_path)
        mgr = SearchManager(config)
        results = mgr.search("test", mode="text")
        assert results == []

    @patch("subprocess.run")
    def test_search_surfaces_nonzero_exit(self, mock_run, tmp_path):
        """Non-zero exit from qmd raises RuntimeError instead of returning []."""
        mock_run.return_value = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="Collection 'papers' not found"
        )
        config = _make_config(tmp_path)
        mgr = SearchManager(config)
        with pytest.raises(RuntimeError, match="Collection 'papers' not found"):
            mgr.search("test", mode="text")

    def test_extract_paper_id_slug(self, tmp_path):
        """Slug-based IDs (no dots) pass through directly."""
        config = _make_config(tmp_path)
        search_dir = tmp_path / "search"
        search_dir.mkdir()
        (search_dir / "my-note-slug.md").write_text("test")
        mgr = SearchManager(config)
        assert mgr._extract_paper_id("qmd://papers/my-note-slug.md") == "my-note-slug"

    def test_extract_paper_id_arxiv_mangled(self, tmp_path):
        """qmd converts dots to dashes; we reverse-map via disk lookup."""
        config = _make_config(tmp_path)
        search_dir = tmp_path / "search"
        search_dir.mkdir()
        (search_dir / "2503.10291.md").write_text("test")
        mgr = SearchManager(config)
        # qmd would report this as 2503-10291.md
        assert mgr._extract_paper_id("qmd://papers/2503-10291.md") == "2503.10291"

    def test_extract_paper_id_empty(self, tmp_path):
        config = _make_config(tmp_path)
        mgr = SearchManager(config)
        assert mgr._extract_paper_id("") is None


# --- _strip_summary_header ---


class TestStripSummaryHeader:
    def test_strips_yaml_and_header(self):
        raw = (
            "---\ntitle: \"Test\"\narxiv_id: 123\n---\n\n"
            "# Test Paper\n\n**arXiv**: [123](url)\n\n---\n\n"
            "# One-Pager\n\nBody text."
        )
        result = _strip_summary_header(raw)
        assert result.startswith("# One-Pager")
        assert "Body text." in result

    def test_no_front_matter(self):
        raw = "Just content without front matter."
        assert _strip_summary_header(raw) == raw


# --- Degraded behavior ---


class TestDegradedBehavior:
    @patch("subprocess.run")
    def test_sync_paper_logs_on_failure(self, mock_run, tmp_path):
        """When SearchManager exists but operation fails, no exception propagates."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "qmd", stderr="disk error")
        config = _make_config(tmp_path)
        (tmp_path / "papers").mkdir(exist_ok=True)
        summary = "---\ntitle: \"T\"\n---\n\n# T\n\n---\n\nB."
        (tmp_path / "papers" / "[Paper][2503.10291] Test Paper.md").write_text(summary)

        storage = StorageManager(config)
        storage.add_paper(_make_paper())

        mgr = SearchManager(config)
        # sync_paper calls _run_qmd(["update"]) which will raise
        with pytest.raises(subprocess.CalledProcessError):
            mgr.sync_paper("2503.10291", storage)


# --- storage.rename_tags returns changed_paper_ids ---


class TestRenameTagsReturn:
    def test_returns_changed_paper_ids(self, tmp_path):
        config = Config(data_dir=tmp_path)
        (tmp_path / "papers").mkdir(exist_ok=True)
        storage = StorageManager(config)
        storage.add_paper(_make_paper(paper_id="p1", tags=["RL", "Vision"]))
        storage.add_paper(_make_paper(paper_id="p2", tags=["NLP"]))

        result = storage.rename_tags([("RL", "Reinforcement-Learning")])
        assert "changed_paper_ids" in result
        assert "p1" in result["changed_paper_ids"]
        assert "p2" not in result["changed_paper_ids"]


# --- SyncReport.touched_paper_ids ---


class TestSyncReportTouchedPaperIds:
    def test_touched_paper_ids_initializes_empty(self):
        from paper_assistant.notion import SyncReport
        report = SyncReport(dry_run=False)
        assert report.touched_paper_ids == set()
