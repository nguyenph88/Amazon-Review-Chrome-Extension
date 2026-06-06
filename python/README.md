# AI Review Assistant — Python CLI

A terminal port of the Chrome extension. It drives Amazon's **review your
purchases** flow with [Playwright](https://playwright.dev/python/):

1. Open `https://www.amazon.com/review/review-your-purchases/listing` (reusing a
   saved login session).
2. List your reviewable purchases and let you pick one.
3. Scrape the product's title and image(s) from its `/dp/<ASIN>` page.
4. Ask how many stars, then generate a review with **DeepSeek** or **Gemini**
   (provider + model come from `config.yaml`). All `**`/markdown is stripped so
   it reads like plain user feedback.
5. Fill the review form (rating, title, body, image upload).
6. **Dry-run by default** — it stops after filling and saves a screenshot.
   Add `--submit` to actually post, and you'll get the submit response back.

> Note: automating product reviews can conflict with Amazon's policies and FTC
> rules on review authenticity. Use this on your own account for products you
> actually bought, and treat the AI output as a draft you stand behind.

## Setup

```powershell
cd python
python -m pip install -r requirements.txt
python -m playwright install chromium   # if the browser isn't already present

# configure
copy config.example.yaml config.yaml     # then edit provider/model/etc.
copy .env.example .env                    # then paste your API keys
```

Keys live in `.env` (`DEEPSEEK_API_KEY`, `GEMINI_API_KEY`). Provider, model,
word count, headless mode, etc. live in `config.yaml`. Both are gitignored.

## Usage

```powershell
# 1) Log in once — a window opens; finish the login and the session is saved.
python main.py login

# 2) See what you can review
python main.py list

# 3) Full flow, DRY RUN (fills the form, does not submit)
python main.py run

# Non-interactive dry run: item 1, 5 stars, no prompts
python main.py run --item 1 --stars 5 --yes

# Actually submit
python main.py run --item 1 --stars 5 --yes --submit

# Override provider/model per run
python main.py run --provider gemini --model gemini-2.5-flash
```

### Batch mode

`--count N` picks **N random products** and reviews them one by one, with **no
per-item confirmation**. `--stars` applies to all of them.

```powershell
# 5 random products, all 5 stars — DRY RUN (fills each, submits nothing)
python main.py run --count 5 --stars 5

# Same, but actually post each review
python main.py run --count 5 --stars 5 --submit
```

Each item runs the full pipeline (metadata → generate → fill → submit+verify)
and a final SUMMARY table shows the per-item outcome (`posted`, `NOT-posted`,
`unverified`, or `dry-run`).

Common flags for `run`/`list`:

| Flag | Meaning |
|------|---------|
| `--headed` / `--headless` | Force a visible / invisible browser (default from config) |
| `--submit` | Actually post the review (otherwise dry-run) |
| `--count N` | Review N random products unattended (batch mode) |
| `--item N` / `--stars N` | Skip the interactive prompts |
| `--provider` / `--model` / `--words` | Override AI settings |
| `--no-images` | Don't attach product images |
| `--capture` | Log XHR/fetch/GraphQL to `network-capture.jsonl` |
| `-y, --yes` | Skip confirmation prompts |

## How it submits

The tool fills the real review form and clicks Submit. (A direct
`create-review` API replay was prototyped but Amazon's bot protection returns
`403` for replayed requests, so the DOM/click path is what's used — real clicks
satisfy the anti-bot sensor.)

## How submission is verified

After clicking submit, the tool combines up to three signals:

1. **Submit network call** — it watches for the `POST` to
   `…/review-your-purchases/api/…` and checks for a 2xx status.
2. **On-page confirmation** — a "thank you / review submitted" banner.
3. **Listing re-check (authoritative)** — it reloads the pending
   `review-your-purchases` listing; a posted review **drops off** that list, so
   if the ASIN is gone, the review is confirmed posted.

The SUMMARY reports `posted` (confirmed gone from the listing), `NOT-posted`
(still pending — submit likely failed), or `unverified` (clicked, but neither
signal was conclusive — check the `submit-<asin>.png` screenshot). Run
`python main.py capture` during a manual submit to log the exact submit endpoint
so it can be matched precisely / replayed directly.

## Session reuse & headless

The login is stored as a persistent Chrome profile in `browser.session_dir`
(`.session/` by default). After `login`, both headed and headless runs reuse it.
If a run reports *"Not logged in"*, the session expired — run `login` again.

## Network capture (reduce DOM reliance)

Amazon changes its DOM frequently. To discover the underlying requests (e.g. the
GraphQL/REST call that submits a review) so the tool can target them directly:

```powershell
python main.py capture        # opens a window, logs traffic
# browse / submit a review manually, then press Enter
```

This writes every XHR/fetch request and response (URL, method, post body, and a
JSON/text preview) to `network-capture.jsonl`. Inspect it to find the submit
endpoint, then we can switch `submit()` to replay that request directly with
`requests` instead of clicking the DOM button.

## When selectors break

Every CSS selector lives in `amazon_reviewer/selectors.py` and can be overridden
from the `selectors:` block in `config.yaml` without touching code. On failure
the tool saves debug screenshots (`listing-debug.png`, `dry-run-filled.png`,
`submit-result.png`) so you can see what the page looked like.

## Layout

```
python/
  main.py                     entry point
  config.example.yaml         template config
  .env.example                template secrets
  amazon_reviewer/
    cli.py                    argparse commands (login/list/run/capture)
    config.py                 YAML + .env loader
    ai.py                     DeepSeek + Gemini providers
    text.py                   markdown stripping, keywords, titles
    browser.py                persistent Playwright session + net capture
    amazon.py                 listing scrape, metadata, form fill, submit
    selectors.py              overridable CSS selectors
```
