"""Use extracted evidence to constrain competitor provenance."""

import re

from app.schemas import Competition


def check_sources(section: Competition, evidence: str) -> None:
    """Downgrade unsupported on-site labels. Mentions alone cannot verify a rivalry."""
    for competitor in section.competitors:
        pattern = r"(?<!\w)" + re.escape(competitor.name) + r"(?!\w)"
        if not re.search(pattern, evidence, re.IGNORECASE):
            competitor.source = "ai_suggestion"
