"""RSS podcast feed generation."""

from __future__ import annotations

from datetime import timezone
from pathlib import Path

from feedgen.feed import FeedGenerator

from paper_assistant.config import Config
from paper_assistant.models import Paper


def generate_feed(
    config: Config,
    papers: list[Paper],
    output_path: Path | None = None,
) -> str:
    """Generate/update the RSS podcast feed XML.

    Creates an iTunes-compatible podcast feed with one episode per paper.

    Args:
        config: Configuration with podcast_title, base_url.
        papers: List of papers (only those with audio are included).
        output_path: Where to write feed.xml (defaults to config.feed_path).

    Returns:
        The XML string of the feed.
    """
    fg = FeedGenerator()
    fg.load_extension("podcast")

    fg.title(config.podcast_title)
    fg.link(href=config.podcast_base_url, rel="self")
    fg.description("AI-generated summaries of ML research papers")
    fg.language("en")
    fg.generator("Paper Assistant")

    fg.podcast.itunes_category("Technology")
    fg.podcast.itunes_author("Paper Assistant")
    fg.podcast.itunes_explicit("no")
    fg.podcast.itunes_summary(
        "Automated audio summaries of ML research papers from arXiv."
    )

    # Add episodes for papers with audio
    for paper in papers:
        if not paper.audio_path:
            continue

        fe = fg.add_entry()
        fe.id(paper.metadata.paper_id)
        fe.title(paper.metadata.title)
        fe.description(paper.metadata.abstract[:500])
        link_url = paper.metadata.source_url or paper.metadata.arxiv_url
        if link_url:
            fe.link(href=link_url)

        # Ensure datetime is timezone-aware
        pub_date = paper.date_added
        if pub_date.tzinfo is None:
            pub_date = pub_date.replace(tzinfo=timezone.utc)
        fe.published(pub_date)

        # Audio enclosure
        audio_filename = paper.audio_path.split("/")[-1]
        audio_url = f"{config.podcast_base_url}/audio/{audio_filename}"

        # Get file size for enclosure
        audio_full_path = config.data_dir / paper.audio_path
        file_size = (
            audio_full_path.stat().st_size if audio_full_path.exists() else 0
        )

        fe.enclosure(audio_url, str(file_size), "audio/mpeg")

    out = output_path or config.feed_path
    out.parent.mkdir(parents=True, exist_ok=True)
    fg.rss_file(str(out))

    return fg.rss_str(pretty=True).decode("utf-8")
