import threading
from typing import Optional, Dict, Any

from backend.browser.context import BrowserPageContext
from backend.browser.extractor import extract_article_from_html, chunk_article_text
from backend.browser.sanitizer import sanitize_html_to_text
from backend.browser.url_ingest import ingest_public_url

class BrowserManager:
    """
    Singleton coordinator for Browser Intelligence.
    Tracks active BrowserPageContext, coordinates URL ingestion (Tier 1)
    and browser extension DOM ingestion (Tier 2).
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(BrowserManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._page_lock = threading.Lock()
        self._current_context: Optional[BrowserPageContext] = None
        self._initialized = True

    def set_current_page(self, context: BrowserPageContext) -> BrowserPageContext:
        """Stores the validated, sanitized active BrowserPageContext."""
        with self._page_lock:
            self._current_context = context
        return self._current_context

    def get_current_page(self) -> Optional[BrowserPageContext]:
        """Returns current active BrowserPageContext if available."""
        with self._page_lock:
            return self._current_context

    def clear_current_page(self) -> bool:
        """Clears the active browser page context."""
        with self._page_lock:
            self._current_context = None
        return True

    def ingest_url(self, url: str) -> BrowserPageContext:
        """
        Tier 1 Ingestion: Public URL fetch + SSRF check + extraction.
        Updates active browser context.
        """
        context = ingest_public_url(url)
        with self._page_lock:
            self._current_context = context
        return context

    def process_extension_payload(self, payload: Dict[str, Any]) -> BrowserPageContext:
        """
        Tier 2 Ingestion: Processes payload received from Manifest V3 extension.
        Enforces least-privilege sanitization and updates active browser context.
        """
        if not isinstance(payload, dict):
            raise ValueError("Extension payload must be a JSON object.")

        url = str(payload.get("url", "")).strip()
        title = str(payload.get("title", "")).strip()
        html_content = payload.get("html")
        raw_text = payload.get("text") or payload.get("extracted_text")

        if html_content and isinstance(html_content, str) and html_content.strip():
            # If extension sent rendered DOM HTML, run full structural article extraction
            context = extract_article_from_html(html_content, url=url, source_type="extension")
            if title:
                context.title = title
        elif raw_text and isinstance(raw_text, str) and raw_text.strip():
            # If extension already performed text extraction on client side
            clean_text, headings_tuples, inj_detected = sanitize_html_to_text(raw_text)
            status = "filtered" if inj_detected else ("partial" if len(clean_text.split()) < 50 else "success")
            headings = [{"level": lvl, "text": txt} for lvl, txt in headings_tuples]
            chunks = chunk_article_text(clean_text)
            context = BrowserPageContext(
                url=url,
                domain="",
                title=title or "Webpage Document",
                extracted_text=clean_text,
                headings=headings,
                source_type="extension",
                extraction_status=status,
                word_count=len(clean_text.split()),
                chunks=chunks,
                metadata={}
            )
        else:
            context = BrowserPageContext(
                url=url,
                domain="",
                title=title or "Empty Page",
                extracted_text="",
                headings=[],
                source_type="extension",
                extraction_status="empty_unreadable",
                word_count=0,
                chunks=[],
                metadata={"error": "No content provided by extension"}
            )

        with self._page_lock:
            self._current_context = context
        return context
