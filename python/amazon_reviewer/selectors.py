"""Default CSS selectors for the Amazon review flow.

Amazon changes its DOM often, so every value here can be overridden from the
`selectors:` block in config.yaml. Each entry is a list tried in order.
"""
from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    # Sign-in detection: presence of these means we are NOT logged in.
    "signin_markers": [
        "#ap_email",
        "input[name='email']",
        "form[name='signIn']",
        "#nav-link-accountList[data-nav-ref='nav_signin']",
    ],
    # Logged-in marker on amazon chrome.
    "loggedin_markers": [
        "#nav-link-accountList",
        "#nav-tools",
    ],

    # --- Review listing page ---
    # Each "card" on review-your-purchases/listing.
    "listing_cards": [
        "div[data-card-asin]",
        "div.ryp__card",
        "[data-asin] a[href*='review-your-purchases']",
    ],
    # The "Write a review" link inside / for a card.
    "listing_review_link": [
        "a[href*='review-your-purchases/?'][href*='asin=']",
        "a[href*='create-review']",
    ],

    # --- Review form (in-context RYP) ---
    "form_container": [
        "#in-context-ryp-form",
        "#ryp-review-your-purchases-form",
        "form",
    ],
    "review_text": [
        "textarea#reviewText",
        "textarea[name='reviewText']",
    ],
    "review_title": [
        "input#reviewTitle",
        "input[name='reviewTitle']",
    ],
    # Star rating: a span[role=radio] per star, identified by aria-label
    # ("select to rate item four star."). {word} is replaced with one..five.
    "star_by_label_template": "span[role='radio'][aria-label*='{word} star']",
    # Fallback: all rating spans in order; click the nth (1-indexed).
    "star_container": [
        "span[role='radio'].in-context-ryp__form-field--starRating-single",
        "div[role='radiogroup'] span[role='radio']",
        "#in-context-ryp-form span[role='radio']",
    ],
    "media_upload_wrapper": [
        ".in-context-ryp__form-field--mediaUploadInput--custom-wrapper",
    ],
    "file_input": [
        ".in-context-ryp__form-field--mediaUploadInput--custom-wrapper input[type='file']",
        "input[type='file']",
        "input[accept*='image']",
    ],
    # The submit control only appears in the form AFTER a rating is selected.
    "submit_button": [
        "#in-context-ryp-form input[type='submit'].a-button-input",
        "#in-context-ryp-form input[type='submit']",
        "input[type='submit'].a-button-input",
        "#submit-review-button",
        "button[type='submit']",
    ],
    "submit_button_text": ["Submit", "Submit review", "Post your review"],

    # --- Product detail page (/dp/ASIN) ---
    "product_title": ["#productTitle", "span#productTitle"],
    "product_main_image": ["#imgTagWrapperId img", "#landingImage", "img#landingImage"],
    "product_thumbs": [
        "#altImages img",
        "li.imageThumbnail img",
        "#imageBlock img",
    ],
}


def resolved(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge config overrides over the defaults."""
    merged = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    if overrides:
        for key, val in overrides.items():
            merged[key] = val
    return merged
