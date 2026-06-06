"""Configuration loading: YAML file + .env for secrets."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


@dataclass
class AIConfig:
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    word_count: int = 200
    temperature: float = 0.8
    system_prompt: str = (
        "You are a real Amazon shopper writing a short, honest, "
        "natural-sounding product review. Never use markdown or emoji."
    )
    api_key: str = ""


@dataclass
class BrowserConfig:
    headless: bool = False
    session_dir: Path = Path(".session")
    slow_mo: int = 0
    locale: str = "en-US"
    timezone: str = "America/New_York"


@dataclass
class AmazonConfig:
    base_url: str = "https://www.amazon.com"
    review_listing_url: str = "https://www.amazon.com/review/review-your-purchases/"
    capture_file: str = "network-capture.jsonl"
    max_images: int = 1


@dataclass
class Config:
    ai: AIConfig = field(default_factory=AIConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    amazon: AmazonConfig = field(default_factory=AmazonConfig)
    selectors: dict[str, Any] = field(default_factory=dict)
    root: Path = field(default_factory=Path.cwd)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(config_path: str | os.PathLike | None = None) -> Config:
    """Load config.yaml (falling back to config.example.yaml) + .env keys."""
    here = Path(__file__).resolve().parent.parent  # the python-cli/ directory

    if load_dotenv is not None:
        load_dotenv(here / ".env")

    if config_path is not None:
        cfg_file = Path(config_path)
    else:
        cfg_file = here / "config.yaml"
        if not cfg_file.exists():
            cfg_file = here / "config.example.yaml"

    raw = _load_yaml(cfg_file)
    ai_raw = raw.get("ai", {}) or {}
    br_raw = raw.get("browser", {}) or {}
    az_raw = raw.get("amazon", {}) or {}

    provider = (ai_raw.get("provider") or "deepseek").lower()
    api_key = _resolve_api_key(provider)

    ai = AIConfig(
        provider=provider,
        model=ai_raw.get("model", "deepseek-chat"),
        word_count=int(ai_raw.get("word_count", 200)),
        temperature=float(ai_raw.get("temperature", 0.8)),
        system_prompt=ai_raw.get("system_prompt") or AIConfig.system_prompt,
        api_key=api_key,
    )

    session_dir = Path(br_raw.get("session_dir", ".session"))
    if not session_dir.is_absolute():
        session_dir = here / session_dir

    browser = BrowserConfig(
        headless=bool(br_raw.get("headless", False)),
        session_dir=session_dir,
        slow_mo=int(br_raw.get("slow_mo", 0)),
        locale=br_raw.get("locale", "en-US"),
        timezone=br_raw.get("timezone", "America/New_York"),
    )

    amazon = AmazonConfig(
        base_url=az_raw.get("base_url", AmazonConfig.base_url),
        review_listing_url=az_raw.get("review_listing_url", AmazonConfig.review_listing_url),
        capture_file=az_raw.get("capture_file", "network-capture.jsonl"),
        max_images=int(az_raw.get("max_images", 1)),
    )

    return Config(
        ai=ai,
        browser=browser,
        amazon=amazon,
        selectors=raw.get("selectors") or {},
        root=here,
    )


def _resolve_api_key(provider: str) -> str:
    if provider == "gemini":
        return os.environ.get("GEMINI_API_KEY", "").strip()
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()
