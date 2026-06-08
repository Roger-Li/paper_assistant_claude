"""qmd-based search integration for Paper Assistant."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from paper_assistant.config import Config
    from paper_assistant.storage import StorageManager

logger = logging.getLogger(__name__)


class EmbeddingsNotAvailableError(Exception):
    """Raised when vector/hybrid search is requested but embeddings are missing."""


class SearchCancelledError(Exception):
    """Raised when a running qmd search is cancelled."""


@dataclass
class SearchResult:
    """A single search hit returned by qmd."""

    paper_id: str
    title: str
    score: float
    snippet: str


class SearchManager:
    """Manages the qmd search index for Paper Assistant."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._available: bool | None = None

    def is_available(self) -> bool:
        """Check qmd binary exists. Cached after first call.

        Uses a plain subprocess (no cwd) so it works before data_dir exists.
        """
        if self._available is not None:
            return self._available
        try:
            subprocess.run(
                [*self._config.qmd_command, "--help"],
                capture_output=True,
                check=False,
            )
            self._available = True
        except FileNotFoundError:
            self._available = False
        return self._available

    def setup(self) -> None:
        """Idempotent: create collection if not already present."""
        search_dir = self._config.search_dir
        search_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._run_qmd([
                "collection", "add",
                str(search_dir.resolve()),
                "--name", self._config.qmd_collection_name,
            ])
        except subprocess.CalledProcessError as exc:
            # "already exists" is expected on repeat runs (qmd prints to stdout)
            combined = (exc.output or "") + (exc.stderr or "")
            if "already exists" not in combined:
                raise

    def sync_paper(self, paper_id: str, storage: StorageManager) -> None:
        """Regenerate search doc for one paper, then refresh BM25 and embeddings."""
        paper = storage.get_paper(paper_id)
        if paper is None or paper.summary_path is None:
            return
        self._write_search_doc(paper_id, storage)
        self._run_qmd(["update"])
        self._run_qmd(["embed"])

    def delete_paper(self, paper_id: str) -> None:
        """Remove search doc and run qmd update."""
        doc_path = self._config.search_dir / f"{paper_id}.md"
        if doc_path.exists():
            doc_path.unlink()
        self._run_qmd(["update"])

    def batch_sync(self, paper_ids: Iterable[str], storage: StorageManager) -> None:
        """Regenerate search docs for multiple papers, single update + embed pass."""
        for pid in paper_ids:
            paper = storage.get_paper(pid)
            if paper is None or paper.summary_path is None:
                continue
            self._write_search_doc(pid, storage)
        self._run_qmd(["update"])
        self._run_qmd(["embed"])

    def rebuild_all(self, storage: StorageManager) -> None:
        """Regenerate ALL search docs from index, single qmd update."""
        search_dir = self._config.search_dir
        search_dir.mkdir(parents=True, exist_ok=True)

        # Remove stale docs
        existing_files = {f.stem for f in search_dir.glob("*.md")}
        papers = storage.list_papers()
        current_ids = set()
        for paper in papers:
            pid = paper.metadata.paper_id
            if paper.summary_path is not None:
                self._write_search_doc(pid, storage)
                current_ids.add(pid)
        for stale_id in existing_files - current_ids:
            (search_dir / f"{stale_id}.md").unlink(missing_ok=True)

        self._run_qmd(["update"])

    def generate_embeddings(self) -> None:
        """Bulk embed pass for `index-rebuild --embed`. Sync hooks already embed
        incrementally; this is the recovery path for out-of-band drift."""
        self._run_qmd(["embed"])

    def search(
        self,
        query: str,
        limit: int = 10,
        mode: str = "text",
        cancel_event: threading.Event | None = None,
    ) -> list[SearchResult]:
        """Run a search query against the qmd index.

        mode="text"   -> qmd search (BM25)
        mode="vector"  -> qmd vsearch (requires embeddings)
        mode="hybrid"  -> qmd query (requires embeddings)
        """
        cmd_map = {"text": "search", "vector": "vsearch", "hybrid": "query"}
        qmd_cmd = cmd_map.get(mode)
        if qmd_cmd is None:
            raise ValueError(f"Unknown search mode: {mode}")

        args = [
            qmd_cmd, query,
            "-c", self._config.qmd_collection_name,
            "-n", str(limit),
            "--json",
        ]
        proc = self._run_qmd(args, check=False, cancel_event=cancel_event)
        stderr = proc.stderr or ""
        stdout = proc.stdout or ""

        # Surface non-zero exits as real errors (corrupt index, missing collection, etc.)
        if proc.returncode != 0:
            raise RuntimeError(
                f"qmd {qmd_cmd} failed (exit {proc.returncode}): {stderr.strip() or stdout.strip()}"
            )

        # Detect missing embeddings for vector/hybrid modes
        if mode in ("vector", "hybrid"):
            if "need embeddings" in stderr or "need embeddings" in stdout:
                raise EmbeddingsNotAvailableError(
                    "Semantic search requires embeddings. "
                    "Run `paper-assist index-rebuild --embed` to enable."
                )

        try:
            raw = json.loads(stdout or "[]")
        except (json.JSONDecodeError, ValueError):
            raw = []

        # Empty vector/hybrid results with embeddings warning → missing embeddings
        if mode in ("vector", "hybrid") and not raw and "embeddings" in stderr.lower():
            raise EmbeddingsNotAvailableError(
                "Semantic search requires embeddings. "
                "Run `paper-assist index-rebuild --embed` to enable."
            )

        results: list[SearchResult] = []
        for item in raw:
            paper_id = self._extract_paper_id(item.get("file", ""))
            if paper_id is None:
                continue
            # qmd title is the first # heading (e.g. "One-Pager"), not useful;
            # look up the real title from the search doc's YAML front matter.
            title = self._read_search_doc_title(paper_id) or item.get("title", "")
            results.append(SearchResult(
                paper_id=paper_id,
                title=title,
                score=item.get("score", 0.0),
                snippet=item.get("snippet", ""),
            ))
        return results

    def _write_search_doc(self, paper_id: str, storage: StorageManager) -> None:
        """Write a derived search document for one paper."""
        paper = storage.get_paper(paper_id)
        if paper is None or paper.summary_path is None:
            return

        search_dir = self._config.search_dir
        search_dir.mkdir(parents=True, exist_ok=True)

        # Read summary body, stripping YAML front matter and title header
        summary_path = self._config.data_dir / paper.summary_path
        if not summary_path.exists():
            return
        raw = summary_path.read_text(encoding="utf-8")
        body = _strip_summary_header(raw)

        # Build search doc with enriched front matter
        meta = paper.metadata
        authors_str = ", ".join(meta.authors) if meta.authors else ""
        tags_yaml = json.dumps(list(paper.tags)) if paper.tags else "[]"

        front_matter_lines = [
            "---",
            f'paper_id: "{paper_id}"',
            f'title: "{meta.title.replace(chr(34), chr(92) + chr(34))}"',
            f"source_type: {meta.source_type.value}",
            f"tags: {tags_yaml}",
            f"reading_status: {paper.reading_status.value}",
            f'authors: "{authors_str}"',
        ]
        if meta.published:
            front_matter_lines.append(f"published: \"{meta.published.strftime('%Y-%m-%d')}\"")
        if meta.arxiv_url:
            front_matter_lines.append(f"url: {meta.arxiv_url}")
        elif meta.source_url:
            front_matter_lines.append(f"url: {meta.source_url}")
        front_matter_lines.append("---")

        doc = "\n".join(front_matter_lines) + "\n\n" + body
        doc_path = search_dir / f"{paper_id}.md"
        doc_path.write_text(doc, encoding="utf-8")

    def _run_qmd(
        self,
        args: list[str],
        *,
        check: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> subprocess.CompletedProcess:
        """Run qmd_command + ["--index", index_name] + args."""
        cmd = [
            *self._config.qmd_command,
            "--index", self._config.qmd_index_name,
            *args,
        ]
        if cancel_event is not None:
            return self._run_qmd_cancellable(cmd, cancel_event, check=check)
        return subprocess.run(
            cmd,
            cwd=self._config.data_dir,
            capture_output=True,
            text=True,
            check=check,
        )

    def _run_qmd_cancellable(
        self,
        cmd: list[str],
        cancel_event: threading.Event,
        *,
        check: bool,
    ) -> subprocess.CompletedProcess:
        """Run qmd while allowing an abandoned web request to terminate it."""
        if cancel_event.is_set():
            raise SearchCancelledError("Search request was cancelled")

        popen_kwargs = {
            "cwd": self._config.data_dir,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(cmd, **popen_kwargs)
        while True:
            if cancel_event.wait(0.05):
                self._terminate_cancelled_process(process)
                raise SearchCancelledError("Search request was cancelled")

            try:
                stdout, stderr = process.communicate(timeout=0.05)
            except subprocess.TimeoutExpired:
                continue

            completed = subprocess.CompletedProcess(
                cmd,
                process.returncode,
                stdout,
                stderr,
            )
            if check and completed.returncode != 0:
                raise subprocess.CalledProcessError(
                    completed.returncode,
                    cmd,
                    output=stdout,
                    stderr=stderr,
                )
            return completed

    def _terminate_cancelled_process(self, process: subprocess.Popen) -> None:
        """Terminate a cancelled qmd process group without blocking indefinitely."""
        self._terminate_process_group(process)
        if self._communicate_cancelled_process(process, timeout=1):
            return

        self._kill_process_group(process)
        if not self._communicate_cancelled_process(process, timeout=1):
            logger.warning("Timed out waiting for cancelled qmd process %s to exit", process.pid)

    @staticmethod
    def _communicate_cancelled_process(process: subprocess.Popen, *, timeout: float) -> bool:
        try:
            process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        return True

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen) -> None:
        if os.name == "nt":
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                return
            except (AttributeError, ProcessLookupError, OSError):
                pass
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                return
            except (ProcessLookupError, OSError):
                pass

        try:
            process.terminate()
        except ProcessLookupError:
            pass

    @staticmethod
    def _kill_process_group(process: subprocess.Popen) -> None:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True,
                    check=False,
                )
                return
            except (FileNotFoundError, OSError):
                pass
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                return
            except (ProcessLookupError, OSError):
                pass

        try:
            process.kill()
        except ProcessLookupError:
            pass

    def _extract_paper_id(self, file_path: str) -> str | None:
        """Extract paper_id from qmd file path like 'qmd://papers/2503-10291.md'.

        qmd converts dots to dashes in its URI scheme, so we reverse-map by
        checking which actual file on disk matches the mangled stem.
        """
        match = re.search(r"/([^/]+)\.md$", file_path)
        if not match:
            return None
        mangled = match.group(1)  # e.g. "2503-10291"

        # Fast path: file with that exact name exists (slug-based IDs have no dots)
        search_dir = self._config.search_dir
        if (search_dir / f"{mangled}.md").exists():
            return mangled

        # Reverse the dot→dash mangling: try restoring dots at each dash position
        # For arXiv IDs like "2503.10291" → "2503-10291", there's typically one dot
        for i, ch in enumerate(mangled):
            if ch == "-":
                candidate = mangled[:i] + "." + mangled[i + 1:]
                if (search_dir / f"{candidate}.md").exists():
                    return candidate

        # Fallback: return the mangled name as-is
        return mangled

    def _read_search_doc_title(self, paper_id: str) -> str | None:
        """Read the title from a search doc's YAML front matter."""
        doc_path = self._config.search_dir / f"{paper_id}.md"
        if not doc_path.exists():
            return None
        try:
            text = doc_path.read_text(encoding="utf-8")
            if not text.startswith("---"):
                return None
            end = text.find("---", 3)
            if end == -1:
                return None
            for line in text[3:end].splitlines():
                if line.startswith("title:"):
                    val = line[6:].strip().strip('"')
                    return val if val else None
        except Exception:
            return None
        return None


def get_search_manager(config: Config) -> SearchManager | None:
    """Return SearchManager if qmd is enabled and available, else None."""
    if not config.qmd_enabled:
        return None
    mgr = SearchManager(config)
    if not mgr.is_available():
        return None
    return mgr


def _strip_summary_header(raw: str) -> str:
    """Strip YAML front matter and title/authors header block from a summary file."""
    body = raw
    # Strip YAML front matter
    if body.startswith("---"):
        end_idx = body.find("---", 3)
        if end_idx != -1:
            body = body[end_idx + 3:].lstrip()

    # Strip title/authors/HR header block
    hr_idx = body.find("\n---\n")
    if hr_idx != -1 and hr_idx < 400:
        body = body[hr_idx + 5:].lstrip()

    return body
