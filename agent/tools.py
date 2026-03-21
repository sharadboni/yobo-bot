"""Tool definitions and executors for LLM tool calling."""
from __future__ import annotations
import asyncio
import logging
from urllib.parse import quote_plus
from duckduckgo_search import DDGS
from agent.config import SEARCH_MAX_RESULTS

log = logging.getLogger(__name__)

# Lazy-loaded shared browser instance
_browser = None
_playwright = None

SAFARI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)

# Max chars of page text to return to the LLM
PAGE_TEXT_LIMIT = 4000

# OpenAI function calling format
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web and return a list of results with titles, snippets, and URLs. "
                "Use this for questions about weather, news, prices, current events, or anything "
                "that needs up-to-date data. If the snippets don't contain enough information, "
                "use read_page to visit the most promising URLs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": (
                "Fetch and read the text content of a web page URL. "
                "Use this after web_search to get detailed information from a specific result. "
                "Returns the main text content of the page."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to read",
                    },
                },
                "required": ["url"],
            },
        },
    },
]


def _format_results(results: list[dict]) -> str:
    """Format search results into a consistent string."""
    if not results:
        return ""
    return "\n\n".join(
        f"[{i+1}] **{r['title']}**\n{r['body']}\n{r['url']}"
        for i, r in enumerate(results)
    )


async def _get_browser():
    """Get or create a shared headless browser instance."""
    global _browser, _playwright
    if _browser and _browser.is_connected():
        return _browser
    from playwright.async_api import async_playwright
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(headless=True)
    log.info("Playwright browser launched")
    return _browser


JS_BING_EXTRACT = """(max) => {
    const results = [];
    const headings = document.querySelectorAll('h2 a, h3 a');
    for (const a of headings) {
        if (!a.href || a.href.includes('bing.com/search') || a.href.includes('javascript:')) continue;
        const container = a.closest('li') || a.closest('[class]')?.parentElement;
        if (!container) continue;
        const snippetEl = container.querySelector('p, [class*="snippet"], [class*="caption"]');
        const snippet = snippetEl ? snippetEl.textContent.trim() : '';
        if (a.textContent.trim().length < 5) continue;
        results.push({
            title: a.textContent.trim(),
            body: snippet,
            url: a.href,
        });
    }
    const seen = new Set();
    return results.filter(r => {
        if (seen.has(r.title)) return false;
        seen.add(r.title);
        return true;
    }).slice(0, max);
}"""

JS_GOOGLE_EXTRACT = """(max) => {
    const results = [];
    const items = document.querySelectorAll('#search .g, #rso .g');
    for (const el of items) {
        const a = el.querySelector('a[href^="http"]');
        const h3 = el.querySelector('h3');
        const snippet = el.querySelector('[data-sncf], .VwiC3b, .IsZvec');
        if (!a || !h3) continue;
        results.push({
            title: h3.textContent.trim(),
            body: snippet ? snippet.textContent.trim() : '',
            url: a.href,
        });
    }
    const seen = new Set();
    return results.filter(r => {
        if (seen.has(r.title)) return false;
        seen.add(r.title);
        return true;
    }).slice(0, max);
}"""

JS_PAGE_TEXT = """(limit) => {
    // Remove script, style, nav, header, footer, aside elements
    const remove = document.querySelectorAll('script, style, nav, header, footer, aside, [role="navigation"], [role="banner"], .nav, .menu, .sidebar, .ad, .ads, .cookie');
    remove.forEach(el => el.remove());

    // Get main content or fall back to body
    const main = document.querySelector('main, article, [role="main"], .content, .post, .article, #content')
        || document.body;

    const text = main.innerText || '';

    // Clean up: collapse whitespace, trim
    return text.replace(/\\n{3,}/g, '\\n\\n').replace(/[ \\t]+/g, ' ').trim().slice(0, limit);
}"""


async def _search_duckduckgo(query: str) -> list[dict]:
    """DuckDuckGo search — free, no key, but rate limited."""
    results = DDGS().text(query, max_results=SEARCH_MAX_RESULTS)
    return [
        {"title": r["title"], "body": r["body"], "url": r["href"]}
        for r in results
    ]


async def _search_bing_scrape(query: str) -> list[dict]:
    """Scrape Bing search results via headless browser."""
    browser = await _get_browser()
    page = await browser.new_page(user_agent=SAFARI_UA)
    try:
        url = f"https://www.bing.com/search?q={quote_plus(query)}"
        await page.goto(url, wait_until="load", timeout=15000)
        await asyncio.sleep(2)
        return await page.evaluate(JS_BING_EXTRACT, SEARCH_MAX_RESULTS)
    finally:
        await page.close()


async def _search_google_scrape(query: str) -> list[dict]:
    """Scrape Google search results via headless browser."""
    browser = await _get_browser()
    page = await browser.new_page(user_agent=SAFARI_UA)
    try:
        url = f"https://www.google.com/search?q={quote_plus(query)}"
        await page.goto(url, wait_until="load", timeout=15000)
        await asyncio.sleep(2)
        return await page.evaluate(JS_GOOGLE_EXTRACT, SEARCH_MAX_RESULTS)
    finally:
        await page.close()


# Search providers in fallback order
_SEARCH_PROVIDERS = [
    ("duckduckgo", _search_duckduckgo),
    ("bing", _search_bing_scrape),
    ("google", _search_google_scrape),
]


async def web_search(query: str) -> str:
    """Execute a web search with provider fallback."""
    last_err = None
    for name, provider in _SEARCH_PROVIDERS:
        try:
            results = await provider(query)
            if results:
                log.info("[search] %s returned %d results", name, len(results))
                return _format_results(results)
        except Exception as e:
            log.warning("[search] %s failed: %s", name, e)
            last_err = e

    return f"All search providers failed. Last error: {last_err}"


async def read_page(url: str) -> str:
    """Fetch a URL and return its main text content."""
    from agent.sanitize import validate_url

    # Validate URL before fetching
    is_safe, reason = validate_url(url)
    if not is_safe:
        log.warning("[read_page] Blocked URL %s: %s", url, reason)
        return f"Cannot access this URL: {reason}"

    browser = await _get_browser()
    page = await browser.new_page(user_agent=SAFARI_UA)
    try:
        resp = await page.goto(url, wait_until="load", timeout=15000)
        if not resp or resp.status >= 400:
            return f"Failed to load page: HTTP {resp.status if resp else 'no response'}"
        await asyncio.sleep(1)
        text = await page.evaluate(JS_PAGE_TEXT, PAGE_TEXT_LIMIT)
        if not text:
            return "Page loaded but no readable text content found."
        return text
    except Exception as e:
        return f"Failed to read page: {e}"
    finally:
        await page.close()


# Map tool names to executor functions
TOOL_EXECUTORS = {
    "web_search": web_search,
    "read_page": read_page,
}
