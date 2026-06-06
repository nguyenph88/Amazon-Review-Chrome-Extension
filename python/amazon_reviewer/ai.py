"""AI review generation — DeepSeek and Gemini providers."""
from __future__ import annotations

import requests

from .config import AIConfig
from .text import keyword_for, strip_markdown

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def build_prompt(title: str, rating: int, word_count: int, keyword: str | None = None) -> str:
    sentiment = keyword or keyword_for(rating)
    return (
        f'Write a genuine-sounding Amazon product review for "{title}". '
        f"Rating: {rating} out of 5 stars, so the overall tone should be {sentiment}. "
        f"Keep it under {word_count} words. "
        "Write in first person as a customer who actually bought and used it. "
        "Mention 1-2 concrete details, what you liked or disliked, and whether you'd "
        "recommend it. Vary your sentence length so it reads naturally. "
        "Do not use markdown, asterisks, bullet points, headings, or emoji. "
        "Return only the review text with no preamble or title."
    )


def generate_review(cfg: AIConfig, title: str, rating: int, keyword: str | None = None) -> str:
    """Generate a review and return cleaned plain text."""
    if not cfg.api_key:
        raise RuntimeError(
            f"No API key for provider '{cfg.provider}'. Set it in python/.env "
            f"({'GEMINI_API_KEY' if cfg.provider == 'gemini' else 'DEEPSEEK_API_KEY'})."
        )

    prompt = build_prompt(title, rating, cfg.word_count, keyword)

    if cfg.provider == "gemini":
        raw = _gemini(cfg, prompt)
    elif cfg.provider == "deepseek":
        raw = _deepseek(cfg, prompt)
    else:
        raise RuntimeError(f"Unknown AI provider: {cfg.provider!r}")

    return strip_markdown(raw)


def _deepseek(cfg: AIConfig, prompt: str) -> str:
    resp = requests.post(
        DEEPSEEK_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        },
        json={
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": cfg.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": cfg.temperature,
            "max_tokens": 2048,
        },
        timeout=90,
    )
    if not resp.ok:
        raise RuntimeError(f"DeepSeek API error {resp.status_code}: {resp.text}")
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _gemini(cfg: AIConfig, prompt: str) -> str:
    url = GEMINI_URL.format(model=cfg.model)
    resp = requests.post(
        url,
        params={"key": cfg.api_key},
        headers={"Content-Type": "application/json"},
        json={
            "system_instruction": {"parts": [{"text": cfg.system_prompt}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": cfg.temperature},
        },
        timeout=90,
    )
    if not resp.ok:
        raise RuntimeError(f"Gemini API error {resp.status_code}: {resp.text}")
    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:  # pragma: no cover
        raise RuntimeError(f"Unexpected Gemini response: {data}") from exc
