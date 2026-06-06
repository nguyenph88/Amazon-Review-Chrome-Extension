"""Amazon review-your-purchases flow built on a Playwright page."""
from __future__ import annotations

import re
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, TimeoutError as PWTimeout

from .config import Config
from .selectors import resolved


_STAR_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


@dataclass
class ReviewItem:
    asin: str
    title: str
    image_url: str
    review_url: str


@dataclass
class ProductMeta:
    asin: str
    title: str
    image_urls: list[str] = field(default_factory=list)


@dataclass
class SubmitResult:
    submitted: bool
    ok: bool
    detail: str
    responses: list[dict] = field(default_factory=list)
    # verified: True/False once confirmed via listing re-check; None if unknown.
    verified: bool | None = None
    verify_detail: str = ""


class AmazonFlow:
    def __init__(self, page: Page, cfg: Config):
        self.page = page
        self.cfg = cfg
        self.sel = resolved(cfg.selectors)

    # -- low-level helpers -------------------------------------------------
    def _first(self, key: str, timeout: int = 4000):
        """Return first locator matching any selector under `key`, or None."""
        for css in self.sel.get(key, []):
            loc = self.page.locator(css).first
            try:
                loc.wait_for(state="attached", timeout=timeout)
                return loc
            except PWTimeout:
                continue
        return None

    def screenshot(self, name: str) -> Path:
        path = self.cfg.root / name
        try:
            self.page.screenshot(path=str(path), full_page=True)
        except Exception:
            pass
        return path

    # -- auth --------------------------------------------------------------
    def is_logged_in(self) -> bool:
        url = self.page.url
        if "ap/signin" in url or "/ap/" in url:
            return False
        # Positive signal first: the account nav greets a logged-in user by
        # name ("Hello, Peter"), but says "Hello, sign in" when logged out.
        try:
            nav = self.page.locator("#nav-link-accountList").first
            if nav.count() > 0:
                text = (nav.inner_text(timeout=2000) or "").strip().lower()
                if "sign in" in text:
                    return False
                if text.startswith("hello"):
                    return True
        except Exception:
            pass
        for css in self.sel["signin_markers"]:
            if self.page.locator(css).count() > 0:
                return False
        return True

    def goto_listing(self) -> None:
        self.page.goto(self.cfg.amazon.review_listing_url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)

    def wait_for_manual_login(self, timeout_s: int = 300) -> bool:
        """Block until the user logs in (used in headed `login` command)."""
        print("Complete the Amazon login in the browser window...")
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(2)
            try:
                url = self.page.url
                if "signin" in url or "/ap/" in url:
                    continue  # still in the auth flow (password, OTP, ...)
                # Off the auth pages — confirm by reloading the listing and
                # checking Amazon doesn't bounce us back to sign-in.
                self.goto_listing()
                if self.is_logged_in():
                    return True
            except Exception:
                continue  # page mid-navigation; check again next tick
        return self.is_logged_in()

    # -- listing -----------------------------------------------------------
    def list_items(self) -> list[ReviewItem]:
        """Scrape reviewable items off the listing page (DOM-tolerant)."""
        self.page.wait_for_timeout(1000)
        raw = self.page.evaluate(_LISTING_SCRAPER_JS)
        items: list[ReviewItem] = []
        seen: set[str] = set()
        base = self.cfg.amazon.base_url
        for it in raw:
            asin = it.get("asin") or ""
            href = it.get("href") or ""
            if not href:
                continue
            if not href.startswith("http"):
                href = base.rstrip("/") + href
            key = asin or href
            if key in seen:
                continue
            seen.add(key)
            items.append(ReviewItem(
                asin=asin,
                title=(it.get("title") or "Unknown product").strip(),
                image_url=it.get("image") or "",
                review_url=href,
            ))
        return items

    # -- product metadata --------------------------------------------------
    def scrape_product_meta(self, asin: str, max_images: int) -> ProductMeta:
        """Open /dp/<asin> in a new tab and pull title + images."""
        url = f"{self.cfg.amazon.base_url}/dp/{asin}"
        tab = self.page.context.new_page()
        try:
            tab.goto(url, wait_until="domcontentloaded")
            tab.wait_for_timeout(1500)
            title = ""
            for css in self.sel["product_title"]:
                loc = tab.locator(css).first
                if loc.count() > 0:
                    title = (loc.inner_text() or "").strip()
                    if title:
                        break
            images = tab.evaluate(_PRODUCT_IMAGES_JS)
        finally:
            tab.close()

        images = _dedupe_upscale(images)[: max(max_images, 0)]
        return ProductMeta(asin=asin, title=title or f"Product {asin}", image_urls=images)

    # -- form filling ------------------------------------------------------
    def open_review_form(self, item: ReviewItem) -> None:
        self.page.goto(item.review_url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)

    def fill_form(self, rating: int, title: str, body: str, image_paths: list[Path]) -> dict:
        """Fill star rating, title, body and attach images. Returns a report."""
        report: dict[str, Any] = {"rating": False, "title": False, "body": False, "images": 0}

        # star rating — must happen first; the submit button only renders after.
        report["rating"] = self._click_star(rating)

        # body text
        body_loc = self._first("review_text")
        if body_loc is not None:
            body_loc.fill(body)
            report["body"] = True

        # title
        title_loc = self._first("review_title")
        if title_loc is not None:
            title_loc.fill(title)
            report["title"] = True

        # images
        if image_paths:
            n, err = self._upload_images(image_paths)
            report["images"] = n
            if err:
                report["images_error"] = err

        return report

    def _upload_images(self, image_paths: list[Path]) -> tuple[int, str | None]:
        """Attach images to Amazon's custom uploader.

        Amazon's media widget ignores a plain `input.files =` assignment. The
        working path is to trigger the widget's own click, answer the resulting
        file chooser, and let it run its S3 flow:
            POST media-upload/get-signed-url  ->  PUT <signed S3 url>
        We confirm success by waiting for that S3 PUT to return 2xx (the DOM
        thumbnail is unreliable to detect). The hidden input is `multiple=false`,
        so images are uploaded one at a time.
        """
        uploaded = 0
        last_err: str | None = None
        for path in image_paths:
            ok, err = self._upload_one(str(path))
            if ok:
                uploaded += 1
            else:
                last_err = err
        return uploaded, (None if uploaded == len(image_paths) else last_err)

    def _upload_one(self, path: str) -> tuple[bool, str | None]:
        wrapper = self._first("media_upload_wrapper", timeout=4000)
        if wrapper is None:
            return self._upload_one_direct(path)

        def is_s3_put(resp) -> bool:
            return resp.request.method == "PUT" and "amazonaws" in resp.url

        try:
            with self.page.expect_response(is_s3_put, timeout=25000) as resp_info:
                with self.page.expect_file_chooser(timeout=6000) as fc:
                    wrapper.click()
                fc.value.set_files(path)
            resp = resp_info.value
            if 200 <= resp.status < 300:
                self.page.wait_for_timeout(800)
                return True, None
            return False, f"S3 upload returned {resp.status}"
        except PWTimeout:
            return False, "upload did not complete (no S3 PUT seen)"
        except Exception as exc:  # pragma: no cover
            return False, str(exc)

    def _upload_one_direct(self, path: str) -> tuple[bool, str | None]:
        """Fallback: set the hidden input directly + nudge React with events."""
        file_loc = self._first("file_input", timeout=3000)
        if file_loc is None:
            return False, "file input / upload widget not found"
        try:
            file_loc.set_input_files(path)
            self.page.evaluate(
                """() => {
                    const i = document.querySelector('input[type=file]');
                    if (!i) return;
                    ['input','change','blur'].forEach(t =>
                        i.dispatchEvent(new Event(t, {bubbles: true})));
                }"""
            )
            self.page.wait_for_timeout(3000)
            return True, "file set directly (S3 upload not confirmed; verify in headed mode)"
        except Exception as exc:  # pragma: no cover
            return False, str(exc)

    def _click_star(self, rating: int) -> bool:
        """Click the star matching `rating` (1-5). Prefers aria-label match."""
        word = _STAR_WORDS.get(rating, "five")
        tmpl = self.sel.get("star_by_label_template")
        if tmpl:
            loc = self.page.locator(tmpl.format(word=word)).first
            try:
                loc.wait_for(state="visible", timeout=4000)
                loc.click()
                self.page.wait_for_timeout(800)
                return True
            except PWTimeout:
                pass
        # fallback: nth star in the rating container
        for css in self.sel.get("star_container", []):
            stars = self.page.locator(css)
            try:
                if stars.count() >= rating:
                    stars.nth(rating - 1).click()
                    self.page.wait_for_timeout(800)
                    return True
            except Exception:
                continue
        return False

    # -- submit ------------------------------------------------------------
    def submit(self, dry_run: bool, asin: str | None = None) -> SubmitResult:
        if dry_run:
            return SubmitResult(submitted=False, ok=True, detail="Dry run — form filled but not submitted.")

        captured: list[dict] = []

        def on_response(resp):
            url = resp.url
            method = resp.request.method
            # The definitive submit call (confirmed live):
            #   POST .../review-your-purchases/api/v1/reviews/create-review?create_review_token=...
            is_create = "reviews/create-review" in url
            is_api = "review-your-purchases/api" in url
            is_submitish = any(t in url for t in ("submit", "create-review", "publish-review"))
            if (method == "POST" and (is_api or is_submitish)) or is_submitish:
                if "media-upload" in url:  # ignore the image-upload calls
                    return
                captured.append({
                    "method": method, "url": url, "status": resp.status,
                    "is_create_review": is_create,
                })

        self.page.on("response", on_response)

        btn = self._find_submit_button()
        if btn is None:
            self.page.remove_listener("response", on_response)
            return SubmitResult(False, False, "Submit button not found.", captured)

        try:
            btn.click()
        except Exception as exc:
            self.page.remove_listener("response", on_response)
            return SubmitResult(False, False, f"Click failed: {exc}", captured)

        self.page.wait_for_timeout(5000)
        self.page.remove_listener("response", on_response)

        # Signal 1: did the create-review POST return 2xx? Prefer the exact
        # endpoint; fall back to any submit-ish POST.
        create_calls = [r for r in captured if r.get("is_create_review")]
        submit_calls = create_calls or [r for r in captured if r["method"] == "POST"]
        api_ok = None
        if submit_calls:
            api_ok = any(200 <= r["status"] < 300 for r in submit_calls)

        # Signal 2: on-page confirmation text.
        page_ok = self._page_says_thanks()

        ok = bool(api_ok) if api_ok is not None else (page_ok or True)
        if api_ok is True:
            detail = "Submit POST returned 2xx."
        elif api_ok is False:
            detail = "Submit POST returned a non-2xx status (see responses)."
        elif page_ok:
            detail = "On-page confirmation detected (no submit POST captured)."
        else:
            detail = "Clicked submit; no submit POST captured — verifying via listing."

        result = SubmitResult(submitted=True, ok=ok, detail=detail, responses=captured)

        # Signal 3 (authoritative): the item should drop off the pending listing.
        if asin:
            verified, vdetail = self.verify_posted(asin)
            result.verified = verified
            result.verify_detail = vdetail
            if verified:
                result.ok = True
        return result

    def verify_posted(self, asin: str) -> tuple[bool | None, str]:
        """Reload the pending listing; a posted review drops off it."""
        try:
            self.goto_listing()
            self.page.wait_for_timeout(1500)
            items = self.list_items()
            still_pending = any(it.asin == asin for it in items)
            if not items:
                return None, "Could not re-read listing to verify."
            if still_pending:
                return False, f"ASIN {asin} still appears in the pending review listing."
            return True, f"ASIN {asin} no longer in the pending listing — review posted."
        except Exception as exc:  # pragma: no cover
            return None, f"Verification check failed: {exc}"

    def _page_says_thanks(self) -> bool:
        try:
            return self.page.evaluate(
                """() => /thank you|review (has been )?(submitted|posted|published)|"""
                """successfully submitted/i.test(document.body.innerText)"""
            )
        except Exception:
            return False

    def _find_submit_button(self):
        btn = self._first("submit_button", timeout=3000)
        if btn is not None:
            return btn
        for text in self.sel["submit_button_text"]:
            loc = self.page.get_by_role("button", name=re.compile(text, re.I)).first
            if loc.count() > 0:
                return loc
        return None


# --------------------------------------------------------------------------
# image helpers
# --------------------------------------------------------------------------
def download_images(urls: list[str], dest_dir: Path) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, url in enumerate(urls):
        if not url:
            continue
        try:
            ext = ".jpg"
            m = re.search(r"\.(jpg|jpeg|png|webp)", url, re.I)
            if m:
                ext = "." + m.group(1).lower()
            out = dest_dir / f"product-{i}{ext}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp, out.open("wb") as fh:
                fh.write(resp.read())
            paths.append(out)
        except Exception as exc:  # pragma: no cover
            print(f"  ! could not download image {url}: {exc}")
    return paths


def _dedupe_upscale(urls: list[str]) -> list[str]:
    """Strip Amazon size tokens (_AC_US40_ etc.) to get full-res, dedupe."""
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if not u:
            continue
        full = re.sub(r"\._[^.]+_\.", ".", u)
        if full in seen:
            continue
        seen.add(full)
        out.append(full)
    return out


# --------------------------------------------------------------------------
# in-page scraper scripts
# --------------------------------------------------------------------------
_LISTING_SCRAPER_JS = r"""
() => {
  const getAsin = (href) => {
    const m = (href || '').match(/[?&]asin=([A-Z0-9]+)/i);
    return m ? m[1] : '';
  };
  const reviewLinkSel = 'a[href*="review-your-purchases/?"][href*="asin="]';
  const countLinks = (el) => el.querySelectorAll(reviewLinkSel).length;

  const anchors = Array.from(document.querySelectorAll(reviewLinkSel));
  const byAsin = {};

  for (const a of anchors) {
    const href = a.getAttribute('href') || '';
    const asin = getAsin(href);
    if (!asin) continue;

    // Find this link's tightest "card": climb while the ancestor still
    // contains exactly one review link (i.e. only this item).
    let card = a;
    let parent = a.parentElement;
    while (parent && countLinks(parent) === 1) {
      card = parent;
      parent = parent.parentElement;
    }

    const img = card.querySelector('img');
    let title = (img && img.getAttribute('alt')) ? img.getAttribute('alt').trim() : '';
    if (!title) {
      const t = card.querySelector('[class*="title"], h2, h3, .a-text-bold');
      title = t ? t.textContent.trim() : a.textContent.trim();
    }

    // Prefer the entry with the most info if we see an asin twice.
    const prev = byAsin[asin];
    const cand = { asin, href, image: img ? img.src : '', title };
    if (!prev || (cand.title && cand.title.length > (prev.title || '').length)) {
      byAsin[asin] = cand;
    }
  }
  return Object.values(byAsin);
}
"""

_PRODUCT_IMAGES_JS = r"""
() => {
  const urls = [];
  const main = document.querySelector('#imgTagWrapperId img, #landingImage, img#landingImage');
  if (main && main.src) urls.push(main.src);
  document.querySelectorAll('#altImages img, li.imageThumbnail img').forEach((img) => {
    if (img.src) urls.push(img.src);
  });
  // dynamic image data attribute (hi-res)
  document.querySelectorAll('img[data-old-hires]').forEach((img) => {
    const h = img.getAttribute('data-old-hires');
    if (h) urls.push(h);
  });
  return urls;
}
"""
