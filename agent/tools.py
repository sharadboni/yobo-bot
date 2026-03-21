"""Tool definitions and executors for LLM tool calling."""
from __future__ import annotations
import asyncio
import logging
import random
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus
import httpx
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS
import os
from agent.config import SEARCH_MAX_RESULTS
from agent.sanitize import sanitize_tool_output

log = logging.getLogger(__name__)

_SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
_TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# Lazy-loaded shared browser instance
_browser = None
_playwright = None

# Rotate user agents to reduce fingerprinting
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]


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
            "name": "news_search",
            "description": (
                "Search for recent news articles on a topic. Use this instead of web_search "
                "when the user asks about news, current events, headlines, or what happened recently. "
                "Returns headlines with summaries from major news sources via RSS feeds."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The news topic to search for",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wikipedia",
            "description": (
                "Look up a topic on Wikipedia. Use this for factual questions about people, "
                "places, history, science, concepts, or anything encyclopedic. Returns a "
                "summary and the full article text. Free and unlimited."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The topic to look up on Wikipedia",
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


def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


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


# --- Search providers ---

async def _search_duckduckgo(query: str) -> list[dict]:
    """DuckDuckGo search via ddgs library."""
    results = DDGS().text(query, max_results=SEARCH_MAX_RESULTS)
    return [
        {"title": r["title"], "body": r["body"], "url": r["href"]}
        for r in results
    ]


async def _search_bing_scrape(query: str) -> list[dict]:
    """Scrape Bing search results via headless browser with UA rotation."""
    browser = await _get_browser()
    page = await browser.new_page(user_agent=_random_ua())
    try:
        url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en&cc=US"
        await page.goto(url, wait_until="load", timeout=15000)
        await asyncio.sleep(random.uniform(1.5, 3.0))  # random delay
        return await page.evaluate(JS_BING_EXTRACT, SEARCH_MAX_RESULTS)
    finally:
        await page.close()


async def _search_tavily(query: str) -> list[dict]:
    """Search via Tavily API (1,000 free/month, AI-optimized with extracted content)."""
    if not _TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY not set")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": _TAVILY_API_KEY,
                "query": query,
                "max_results": SEARCH_MAX_RESULTS,
                "include_answer": True,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    results = []
    # Tavily can return a direct answer
    answer = data.get("answer", "")
    if answer:
        results.append({"title": "Tavily Answer", "body": answer, "url": ""})
    for r in data.get("results", [])[:SEARCH_MAX_RESULTS]:
        results.append({
            "title": r.get("title", ""),
            "body": r.get("content", ""),
            "url": r.get("url", ""),
        })
    return results


async def _search_serper(query: str) -> list[dict]:
    """Google search via Serper.dev API (2,500 free/month)."""
    if not _SERPER_API_KEY:
        raise RuntimeError("SERPER_API_KEY not set")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": SEARCH_MAX_RESULTS},
            headers={"X-API-KEY": _SERPER_API_KEY, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        {"title": r["title"], "body": r.get("snippet", ""), "url": r["link"]}
        for r in data.get("organic", [])[:SEARCH_MAX_RESULTS]
    ]


JS_YAHOO_EXTRACT = """(max) => {
    const results = [];
    const items = document.querySelectorAll('#web li, .algo');
    for (const el of items) {
        const a = el.querySelector('h3 a, a.d-ib');
        const snippet = el.querySelector('.compText p, .fz-ms');
        if (!a || !a.href || a.href.includes('yahoo.com/search')) continue;
        if (a.textContent.trim().length < 5) continue;
        results.push({
            title: a.textContent.trim(),
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


async def _search_yahoo_scrape(query: str) -> list[dict]:
    """Scrape Yahoo search results via headless browser."""
    browser = await _get_browser()
    page = await browser.new_page(user_agent=_random_ua())
    try:
        url = f"https://search.yahoo.com/search?p={quote_plus(query)}"
        await page.goto(url, wait_until="load", timeout=15000)
        await asyncio.sleep(random.uniform(1.5, 3.0))
        return await page.evaluate(JS_YAHOO_EXTRACT, SEARCH_MAX_RESULTS)
    finally:
        await page.close()



# Providers in fallback order
_SEARCH_PROVIDERS = [
    ("duckduckgo", _search_duckduckgo),
    ("tavily", _search_tavily),
    ("bing", _search_bing_scrape),
    ("serper", _search_serper),
    ("yahoo", _search_yahoo_scrape),
]


async def web_search(query: str) -> str:
    """Execute a web search with provider fallback. Output is sanitized."""
    last_err = None
    for name, provider in _SEARCH_PROVIDERS:
        try:
            results = await provider(query)
            if results:
                log.info("[search] %s returned %d results", name, len(results))
                raw = _format_results(results)
                return sanitize_tool_output(raw, source="web_search")
        except Exception as e:
            log.warning("[search] %s failed: %s", name, e)
            last_err = e

    return f"All search providers failed. Last error: {last_err}"


# --- News search via RSS feeds ---

_RSS_FEEDS = {
    "google_news": "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
    "reuters": "https://news.google.com/rss/search?q={query}+site:reuters.com&hl=en-US&gl=US&ceid=US:en",
    "bbc": "https://news.google.com/rss/search?q={query}+site:bbc.com&hl=en-US&gl=US&ceid=US:en",
    "ap": "https://news.google.com/rss/search?q={query}+site:apnews.com&hl=en-US&gl=US&ceid=US:en",
}


def _parse_rss(xml_text: str, max_items: int) -> list[dict]:
    """Parse RSS XML into a list of results."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = root.findall(".//item")
    results = []
    for item in items[:max_items]:
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        desc = item.findtext("description", "").strip()
        pub_date = item.findtext("pubDate", "").strip()
        if not title or not link:
            continue
        # Clean HTML from description
        import re
        desc = re.sub(r"<[^>]+>", "", desc).strip()
        body = f"{desc} ({pub_date})" if pub_date else desc
        results.append({"title": title, "body": body, "url": link})
    return results


async def _news_google_rss(query: str) -> list[dict]:
    """Fetch news from Google News RSS."""
    url = _RSS_FEEDS["google_news"].format(query=quote_plus(query))
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": _random_ua()})
        resp.raise_for_status()
    return _parse_rss(resp.text, SEARCH_MAX_RESULTS)


async def _news_source_rss(query: str, source: str) -> list[dict]:
    """Fetch news from a specific source via Google News RSS."""
    url = _RSS_FEEDS[source].format(query=quote_plus(query))
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": _random_ua()})
        resp.raise_for_status()
    return _parse_rss(resp.text, SEARCH_MAX_RESULTS)


async def news_search(query: str) -> str:
    """Search for news via RSS feeds with fallback. Output is sanitized."""
    # Try Google News RSS first (aggregates all sources)
    try:
        results = await _news_google_rss(query)
        if results:
            log.info("[news] google_news returned %d results", len(results))
            raw = _format_results(results)
            return sanitize_tool_output(raw, source="news_search")
    except Exception as e:
        log.warning("[news] google_news failed: %s", e)

    # Try individual source feeds
    for source in ("reuters", "bbc", "ap"):
        try:
            results = await _news_source_rss(query, source)
            if results:
                log.info("[news] %s returned %d results", source, len(results))
                raw = _format_results(results)
                return sanitize_tool_output(raw, source="news_search")
        except Exception as e:
            log.warning("[news] %s failed: %s", source, e)

    # Fall back to regular web search with news keywords
    return await web_search(f"{query} latest news")


async def wikipedia(query: str) -> str:
    """Look up a topic on Wikipedia using the REST API."""
    headers = {"User-Agent": "YoboBot/1.0 (https://github.com/yobo-bot)"}
    async with httpx.AsyncClient(timeout=10, headers=headers, follow_redirects=True) as client:
        # Search for the best matching article
        resp = await client.get(
            "https://en.wikipedia.org/w/rest.php/v1/search/page",
            params={"q": query, "limit": 1},
        )
        resp.raise_for_status()
        pages = resp.json().get("pages", [])
        if not pages:
            return f"No Wikipedia article found for: {query}"

        title = pages[0]["title"]
        key = pages[0].get("key", title.replace(" ", "_"))

        # Get the article summary
        resp = await client.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(key)}",
        )
        resp.raise_for_status()
        summary_data = resp.json()
        summary = summary_data.get("extract", "")
        url = summary_data.get("content_urls", {}).get("desktop", {}).get("page", "")

        if not summary:
            return f"Wikipedia article '{title}' found but no content available."

        # Also get more content from the mobile text endpoint
        full_text = summary
        try:
            resp = await client.get(
                f"https://en.wikipedia.org/api/rest_v1/page/mobile-text/{quote_plus(key)}",
            )
            if resp.status_code == 200:
                sections = resp.json().get("sections", [])
                texts = [summary]
                for s in sections[:5]:
                    text = s.get("text", "")
                    # Strip HTML
                    import re
                    text = re.sub(r"<[^>]+>", "", text).strip()
                    if text:
                        texts.append(text)
                full_text = "\n\n".join(texts)
        except Exception:
            pass  # summary is enough

        truncated = full_text[:PAGE_TEXT_LIMIT]
        return sanitize_tool_output(
            f"**{title}**\n{url}\n\n{truncated}",
            source="wikipedia",
        )


async def read_page(url: str) -> str:
    """Fetch a URL and return its main text content."""
    from agent.sanitize import validate_url

    # Validate URL before fetching
    is_safe, reason = validate_url(url)
    if not is_safe:
        log.warning("[read_page] Blocked URL %s: %s", url, reason)
        return f"Cannot access this URL: {reason}"

    browser = await _get_browser()
    page = await browser.new_page(user_agent=_random_ua())
    try:
        resp = await page.goto(url, wait_until="load", timeout=15000)
        if not resp or resp.status >= 400:
            return f"Failed to load page: HTTP {resp.status if resp else 'no response'}"
        await asyncio.sleep(1)
        text = await page.evaluate(JS_PAGE_TEXT, PAGE_TEXT_LIMIT)
        if not text:
            return "Page loaded but no readable text content found."
        return sanitize_tool_output(text, source="read_page")
    except Exception as e:
        return f"Failed to read page: {e}"
    finally:
        await page.close()


# Map tool names to executor functions
TOOL_EXECUTORS = {
    "web_search": web_search,
    "news_search": news_search,
    "wikipedia": wikipedia,
    "read_page": read_page,
}
