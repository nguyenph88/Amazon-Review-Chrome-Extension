"""Command-line interface for the AI Review Assistant."""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from . import ai
from .amazon import AmazonFlow, download_images
from .browser import open_session
from .config import Config, load_config
from .text import keyword_for, title_for


def _print_header(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def _prompt_int(msg: str, lo: int, hi: int) -> int:
    while True:
        try:
            v = int(input(msg).strip())
            if lo <= v <= hi:
                return v
        except ValueError:
            pass
        print(f"  Enter a number between {lo} and {hi}.")


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_login(cfg: Config, args) -> int:
    _print_header("Login — a browser window will open")
    with open_session(cfg, headless=False) as s:
        flow = AmazonFlow(s.page, cfg)
        flow.goto_listing()
        if flow.is_logged_in():
            print("Already logged in. Session saved at", cfg.browser.session_dir)
            return 0
        ok = flow.wait_for_manual_login()
        if ok:
            print("Login detected. Session saved at", cfg.browser.session_dir)
            return 0
        print("Login not detected within timeout.")
        return 1


def cmd_list(cfg: Config, args) -> int:
    headless = _headless_from(args, cfg)
    with open_session(cfg, headless=headless, capture=args.capture) as s:
        flow = AmazonFlow(s.page, cfg)
        flow.goto_listing()
        if not flow.is_logged_in():
            print("Not logged in. Run:  python main.py login")
            return 2
        items = flow.list_items()
        _print_items(items)
        if not items:
            flow.screenshot("listing-debug.png")
            print("No items parsed. Saved listing-debug.png — selectors may need updating in config.yaml.")
        return 0


def cmd_run(cfg: Config, args) -> int:
    _apply_overrides(cfg, args)
    dry_run = not args.submit
    headless = _headless_from(args, cfg)

    # Batch mode: --count N picks N random products and processes them one by
    # one with no per-item confirmation.
    batch = args.count is not None and args.count > 1

    with open_session(cfg, headless=headless, capture=args.capture) as s:
        flow = AmazonFlow(s.page, cfg)
        flow.goto_listing()
        if not flow.is_logged_in():
            print("Not logged in. Run:  python main.py login")
            return 2

        items = flow.list_items()
        if not items:
            flow.screenshot("listing-debug.png")
            print("No reviewable items found. Saved listing-debug.png.")
            return 1

        # ----- select the items to review --------------------------------
        if args.count is not None:
            n = min(args.count, len(items))
            selected = random.sample(items, n)
            print(f"Randomly selected {n} of {len(items)} items to review.")
        elif args.item is not None:
            if not (1 <= args.item <= len(items)):
                print("Invalid --item index.")
                return 1
            selected = [items[args.item - 1]]
        else:
            _print_items(items)
            idx = _prompt_int(f"\nPick an item [1-{len(items)}]: ", 1, len(items)) - 1
            selected = [items[idx]]

        # ----- star rating ------------------------------------------------
        if args.stars is not None:
            fixed_rating = args.stars
        elif batch:
            fixed_rating = _prompt_int("Stars for ALL selected items? [1-5]: ", 1, 5)
        else:
            fixed_rating = None  # ask per item

        # batch always runs unattended
        auto = args.yes or batch

        results = []
        for i, item in enumerate(selected, 1):
            rating = fixed_rating if fixed_rating is not None else _prompt_int("How many stars? [1-5]: ", 1, 5)
            _print_header(f"[{i}/{len(selected)}] {item.title[:55]}  ({item.asin})  —  {rating}★")
            rc = _process_one(cfg, flow, item, rating, dry_run, auto, headless, args)
            results.append((item, rating, rc))

        # ----- summary ----------------------------------------------------
        _print_header("SUMMARY")
        worst = 0
        for item, rating, rc in results:
            print(f"  {rc['status']:<10} {rating}★  {item.title[:50]}")
            if rc.get("detail"):
                print(f"             {rc['detail']}")
            if rc["status"] not in ("posted", "dry-run", "filled"):
                worst = 1
        if dry_run and not headless and not batch:
            input("\nPress Enter to close the browser...")
        return worst


def _process_one(cfg, flow, item, rating, dry_run, auto, headless, args) -> dict:
    """Generate + fill (+ optionally submit/verify) a single item."""
    # metadata + images
    print("  Fetching product metadata...")
    meta = flow.scrape_product_meta(item.asin, cfg.amazon.max_images) if item.asin else None
    title_text = meta.title if meta and meta.title else item.title
    image_urls = meta.image_urls if meta else ([item.image_url] if item.image_url else [])
    if args.no_images:
        image_urls = []

    # generate
    keyword = keyword_for(rating, random.randint(0, 8))
    print(f"  Generating review via {cfg.ai.provider} ({cfg.ai.model})...")
    try:
        body = ai.generate_review(cfg.ai, title_text, rating, keyword)
    except Exception as exc:
        print(f"  AI generation failed: {exc}")
        return {"status": "ai-error", "detail": str(exc)}
    review_title = title_for(rating, random.randint(0, 4))
    print(f"  Headline: {review_title}")
    print("  " + body.replace("\n", "\n  "))

    if not auto and not _confirm("\n  Proceed? [y/N] "):
        return {"status": "skipped", "detail": "user declined"}

    # images
    image_paths: list[Path] = []
    if image_urls:
        image_paths = download_images(image_urls, cfg.root / ".tmp_images")

    # Fill the visible review form, then click Submit (the reliable path).
    print("  Opening review form...")
    flow.open_review_form(item)
    report = flow.fill_form(rating, review_title, body, image_paths)
    print("  Fill report:", report)
    if dry_run:
        shot = flow.screenshot(f"dry-run-{item.asin}.png")
        print(f"  DRY RUN — filled, not submitted. Screenshot: {shot.name}")
        return {"status": "dry-run", "detail": f"screenshot {shot.name}"}
    if not auto and not _confirm("  Submit this review for real? [y/N] "):
        return {"status": "skipped", "detail": "user declined submit"}
    print("  Submitting...")
    result = flow.submit(dry_run=False, asin=item.asin)

    print(f"  Detail        : {result.detail}")
    if result.responses:
        for r in result.responses:
            tag = f"[{r.get('status','?')}] {r.get('method','')} {r.get('url','')}"
            print(f"     {tag}")
    if dry_run:
        return {"status": "dry-run", "detail": result.detail}
    print(f"  Verification  : {result.verify_detail or '(none)'}")

    if result.verified is True:
        return {"status": "posted", "detail": result.verify_detail}
    if result.verified is False:
        return {"status": "NOT-posted", "detail": result.verify_detail}
    return {"status": "unverified", "detail": result.detail}


def cmd_capture(cfg: Config, args) -> int:
    _print_header("Capture mode — logging XHR/fetch/graphql traffic")
    with open_session(cfg, headless=False, capture=True) as s:
        flow = AmazonFlow(s.page, cfg)
        flow.goto_listing()
        print(f"Logging to {cfg.root / cfg.amazon.capture_file}")
        print("Browse Amazon (open a review, submit one manually, etc.).")
        input("Press Enter here when done to stop capturing...")
    print("Capture saved.")
    return 0


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _print_items(items) -> None:
    _print_header(f"Reviewable items ({len(items)})")
    for i, it in enumerate(items, 1):
        print(f"  [{i:>2}] {it.title[:70]}")
        print(f"       ASIN {it.asin or '?'}")


def _confirm(msg: str) -> bool:
    return input(msg).strip().lower() in ("y", "yes")


def _headless_from(args, cfg: Config) -> bool:
    if args.headed:
        return False
    if args.headless:
        return True
    return cfg.browser.headless


def _apply_overrides(cfg: Config, args) -> None:
    if args.provider:
        cfg.ai.provider = args.provider
        from .config import _resolve_api_key
        cfg.ai.api_key = _resolve_api_key(args.provider)
    if args.model:
        cfg.ai.model = args.model
    if args.words:
        cfg.ai.word_count = args.words


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="amazon-reviewer",
        description="AI-assisted Amazon review CLI (Playwright). Dry-run by default.",
    )
    p.add_argument("--config", help="Path to config.yaml")

    sub = p.add_subparsers(dest="command")

    def add_common(sp):
        sp.add_argument("--headed", action="store_true", help="Force visible browser")
        sp.add_argument("--headless", action="store_true", help="Force headless browser")
        sp.add_argument("--capture", action="store_true", help="Log network traffic to capture file")

    sp_login = sub.add_parser("login", help="Log in once and save the session")

    sp_list = sub.add_parser("list", help="List reviewable purchases")
    add_common(sp_list)

    sp_run = sub.add_parser("run", help="Full flow: pick, rate, generate, fill, (submit)")
    add_common(sp_run)
    sp_run.add_argument("--submit", action="store_true", help="Actually submit (default: dry run)")
    sp_run.add_argument("--count", type=int, help="Review N random products, one by one, unattended")
    sp_run.add_argument("--item", type=int, help="1-based item index (skip prompt)")
    sp_run.add_argument("--stars", type=int, choices=range(1, 6), help="Star rating applied to all items")
    sp_run.add_argument("--provider", choices=["deepseek", "gemini"], help="Override AI provider")
    sp_run.add_argument("--model", help="Override AI model")
    sp_run.add_argument("--words", type=int, help="Override target word count")
    sp_run.add_argument("--no-images", action="store_true", help="Skip image upload")
    sp_run.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompts")

    sub.add_parser("capture", help="Open a browser and log network traffic for inspection")

    return p


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252; force UTF-8 so stars / smart quotes
    # in product titles don't crash printing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    cfg = load_config(args.config)

    handlers = {
        "login": cmd_login,
        "list": cmd_list,
        "run": cmd_run,
        "capture": cmd_capture,
    }
    try:
        return handlers[args.command](cfg, args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
