import re
import html
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

from backend.browser.context import BrowserPageContext
from backend.browser.sanitizer import sanitize_html_to_text

DYNAMIC_PAGE_MARKERS = [
    re.compile(r'<div\s+id=["\'](?:root|app|__next)["\'][^>]*>\s*</div>', re.IGNORECASE),
    re.compile(r'<div\s+class=["\'](?:app-root|react-root)["\'][^>]*>\s*</div>', re.IGNORECASE),
    re.compile(r'<script[^>]+id=["\']__NEXT_DATA__["\']', re.IGNORECASE),
    re.compile(r'You\s+need\s+to\s+enable\s+JavaScript', re.IGNORECASE),
    re.compile(r'JavaScript\s+is\s+disabled', re.IGNORECASE),
    re.compile(r'<noscript>', re.IGNORECASE)
]

def extract_meta_title_and_tags(html_content: str) -> (str, Dict[str, Any]):
    """Extracts document title and metadata (author, site_name, description)."""
    title = ""
    metadata = {}
    
    # Check <title>
    m_title = re.search(r"<title[^>]*>(.*?)</title>", html_content, flags=re.DOTALL | re.IGNORECASE)
    if m_title:
        title = html.unescape(m_title.group(1).strip())

    # Check OpenGraph title if title is empty
    m_og_title = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
    if not title and m_og_title:
        title = html.unescape(m_og_title.group(1).strip())

    # Site name
    m_site = re.search(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
    if m_site:
        metadata["site_name"] = html.unescape(m_site.group(1).strip())

    # Author
    m_author = re.search(r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
    if m_author:
        metadata["author"] = html.unescape(m_author.group(1).strip())

    # Description
    m_desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
    if m_desc:
        metadata["description"] = html.unescape(m_desc.group(1).strip())

    return title, metadata


def chunk_article_text(text: str, target_words: int = 450, overlap_words: int = 50) -> List[Dict[str, Any]]:
    """
    Breaks article text into timestamp-less semantic chunks for BM25 retrieval.
    Preserves paragraph breaks where possible.
    """
    if not text:
        return []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]

    chunks: List[Dict[str, Any]] = []
    curr_paras: List[str] = []
    curr_words = 0
    chunk_idx = 0

    for p in paragraphs:
        p_len = len(p.split())
        curr_paras.append(p)
        curr_words += p_len

        if curr_words >= target_words:
            chunk_text = "\n\n".join(curr_paras)
            chunks.append({
                "chunk_id": f"art_chunk_{chunk_idx + 1}",
                "chunk_index": chunk_idx,
                "text": chunk_text,
                "word_count": curr_words
            })
            chunk_idx += 1
            # Overlap: keep last paragraph if it is small enough
            if len(curr_paras) > 1 and len(curr_paras[-1].split()) <= overlap_words * 2:
                curr_paras = [curr_paras[-1]]
                curr_words = len(curr_paras[0].split())
            else:
                curr_paras = []
                curr_words = 0

    if curr_paras:
        chunk_text = "\n\n".join(curr_paras)
        chunks.append({
            "chunk_id": f"art_chunk_{chunk_idx + 1}",
            "chunk_index": chunk_idx,
            "text": chunk_text,
            "word_count": curr_words
        })

    return chunks


def extract_article_from_html(
    html_content: str,
    url: str = "",
    source_type: str = "url_ingest"
) -> BrowserPageContext:
    """
    Processes HTML content into a safe, structured BrowserPageContext.
    Differentiates between:
    - Normal static article (status: 'success' or 'partial')
    - Dynamic JS app requiring extension (status: 'dynamic_page_requires_extension')
    - Empty or unreadable page (status: 'empty_unreadable')
    - Page with prompt injection filtered (status: 'filtered')
    """
    domain = ""
    if url:
        try:
            parsed = urlparse(url)
            domain = (parsed.hostname or "").lower()
        except Exception:
            domain = ""

    # 1. Extract metadata & raw title
    meta_title, metadata = extract_meta_title_and_tags(html_content)

    # 2. Sanitize HTML to structural text
    clean_text, headings, injection_detected = sanitize_html_to_text(html_content)

    # 3. Determine title if not found in metadata
    final_title = meta_title
    if not final_title and headings:
        # Use first H1 or H2
        final_title = headings[0][1]
    if not final_title:
        final_title = domain or "Webpage Document"

    # Format headings outline
    headings_list = [{"level": lvl, "text": txt} for lvl, txt in headings]

    # 4. Check word count and characters
    char_count = len(clean_text)
    word_count = len(clean_text.split())

    # 5. Quality & Status Evaluation
    if char_count == 0 or word_count < 5:
        # Check if dynamic page
        is_dynamic = any(marker.search(html_content) for marker in DYNAMIC_PAGE_MARKERS)
        if is_dynamic:
            status = "dynamic_page_requires_extension"
        else:
            status = "empty_unreadable"
    elif char_count < 150 and any(marker.search(html_content) for marker in DYNAMIC_PAGE_MARKERS):
        status = "dynamic_page_requires_extension"
    elif injection_detected:
        status = "filtered"
    elif word_count < 50:
        status = "partial"
    else:
        status = "success"

    # 6. Generate chunks for BM25 retrieval
    chunks = chunk_article_text(clean_text)

    return BrowserPageContext(
        url=url,
        domain=domain,
        title=final_title,
        extracted_text=clean_text,
        headings=headings_list,
        source_type=source_type,
        extraction_status=status,
        word_count=word_count,
        chunks=chunks,
        metadata=metadata
    )
