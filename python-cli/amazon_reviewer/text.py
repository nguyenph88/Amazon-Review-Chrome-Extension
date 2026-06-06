"""Text cleanup — strip markdown so AI output reads like plain user feedback.

Mirrors the extension's preformatText() but a bit more thorough.
"""
from __future__ import annotations

import re

# Sentiment keyword pools per star rating (ported from the extension).
STAR_KEYWORDS: dict[int, list[str]] = {
    1: [
        "waste of money", "very disappointed", "poor experience",
        "not as advertised", "terrible quality", "regret this purchase",
        "a major letdown", "fails to deliver", "a complete failure",
    ],
    2: [
        "deeply flawed", "wouldn't recommend", "underwhelming",
        "not worth the price", "frustrating to use", "significant drawbacks",
        "expected more", "low quality", "barely functional", "disappointing",
    ],
    3: [
        "average", "gets the job done", "nothing special",
        "decent but not great", "met expectations", "has pros and cons",
        "fair for the price", "could be improved", "serviceable",
    ],
    4: [
        "very good", "a solid choice", "great value", "happy with it",
        "works well", "impressive", "would recommend", "mostly positive",
        "high quality", "almost perfect",
    ],
    5: [
        "absolutely perfect", "exceeded expectations", "outstanding",
        "highly recommend", "excellent quality", "a fantastic purchase",
        "a must-have", "top-notch", "incredible value", "flawless",
    ],
}


def keyword_for(rating: int, index: int | None = None) -> str:
    """Pick a sentiment keyword for a rating. Deterministic if index given."""
    pool = STAR_KEYWORDS.get(rating, STAR_KEYWORDS[5])
    if index is None:
        # Stable-ish default: first keyword. Caller can randomize if desired.
        return pool[0]
    return pool[index % len(pool)]


_TITLE_TEMPLATES: dict[int, list[str]] = {
    1: ["Very disappointed", "Save your money", "Do not buy", "A major letdown", "Fails to deliver"],
    2: ["Not as good as I hoped", "Has serious flaws", "Underwhelming", "Wouldn't recommend", "Expected more"],
    3: ["It's just okay", "Decent but not great", "Has pros and cons", "Gets the job done", "Fair for the price"],
    4: ["A solid choice", "Happy with this purchase", "Works very well", "Would recommend", "Great value"],
    5: ["Absolutely love it", "Exceeded my expectations", "Highly recommend", "A must-have", "Fantastic quality"],
}


def title_for(rating: int, index: int | None = None) -> str:
    """A short review headline for a rating."""
    pool = _TITLE_TEMPLATES.get(rating, _TITLE_TEMPLATES[5])
    return pool[0] if index is None else pool[index % len(pool)]


def strip_markdown(text: str) -> str:
    """Remove markdown / asterisks / emoji-ish formatting from model output."""
    if not text:
        return ""

    t = text
    # bold / italic / strikethrough
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"\*([^*]+)\*", r"\1", t)
    t = re.sub(r"__([^_]+)__", r"\1", t)
    t = re.sub(r"_([^_]+)_", r"\1", t)
    t = re.sub(r"~~([^~]+)~~", r"\1", t)
    # any stray asterisks/underscores left over
    t = t.replace("**", "").replace("*", "")
    # code blocks / inline code
    t = re.sub(r"```[\s\S]*?```", "", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    # headers, blockquotes, hr
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"^>\s*", "", t, flags=re.MULTILINE)
    t = re.sub(r"^[-*_]{3,}$", "", t, flags=re.MULTILINE)
    # links [text](url) -> text
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    # list markers
    t = re.sub(r"^[ \t]*[-*+]\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"^[ \t]*\d+\.\s+", "", t, flags=re.MULTILINE)
    # whitespace cleanup
    t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()
