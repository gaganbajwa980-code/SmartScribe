import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

# Fields explicitly prohibited from storage or transmission
SENSITIVE_FIELD_DENYLIST = {
    "cookie", "cookies", "document_cookie",
    "authorization", "auth_token", "bearer_token", "jwt",
    "password", "passwords", "secret", "secrets",
    "local_storage", "localstorage", "session_storage", "sessionstorage",
    "credentials", "credential", "session_tokens", "session_token",
    "form_fields", "form_data", "inputs", "input_values"
}

ALLOWED_SOURCE_TYPES = {"url_ingest", "extension", "manual"}

ALLOWED_STATUSES = {
    "success",
    "partial",
    "dynamic_page_requires_extension",
    "unsafe_url",
    "empty_unreadable",
    "filtered",
    "error"
}

@dataclass
class BrowserPageContext:
    """
    Standardized, privacy-safe representation of an active browser webpage or article.
    Strictly excludes cookies, credentials, session tokens, passwords, and form inputs.
    """
    url: str
    domain: str
    title: str
    extracted_text: str
    headings: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    source_type: str = "url_ingest"  # 'url_ingest' | 'extension' | 'manual'
    extraction_status: str = "success"  # See ALLOWED_STATUSES
    word_count: int = 0
    summary: Optional[str] = None
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Enforce sanitization on fields
        if not self.domain and self.url:
            try:
                parsed = urlparse(self.url)
                self.domain = (parsed.hostname or "").lower()
            except Exception:
                self.domain = ""
        else:
            self.domain = (self.domain or "").lower().strip()

        self.title = (self.title or "").strip()
        self.extracted_text = (self.extracted_text or "").strip()
        
        if not self.word_count and self.extracted_text:
            self.word_count = len(self.extracted_text.split())

        if self.source_type not in ALLOWED_SOURCE_TYPES:
            self.source_type = "manual"

        if self.extraction_status not in ALLOWED_STATUSES:
            self.extraction_status = "partial"

        # Sanitize metadata to ensure no sensitive keys exist
        if self.metadata:
            cleaned_meta = {}
            for k, v in self.metadata.items():
                if str(k).lower().strip() not in SENSITIVE_FIELD_DENYLIST:
                    cleaned_meta[k] = v
            self.metadata = cleaned_meta

    def to_dict(self) -> Dict[str, Any]:
        """Returns clean dictionary with strictly allowed fields."""
        return {
            "url": self.url,
            "domain": self.domain,
            "title": self.title,
            "headings": self.headings,
            "extracted_text": self.extracted_text,
            "timestamp": self.timestamp,
            "source_type": self.source_type,
            "extraction_status": self.extraction_status,
            "word_count": self.word_count,
            "summary": self.summary,
            "chunks": self.chunks,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BrowserPageContext":
        """
        Safely construct BrowserPageContext from incoming dictionary.
        Explicitly strips any sensitive credentials or denylisted fields.
        """
        if not isinstance(data, dict):
            raise ValueError("BrowserPageContext payload must be a dictionary.")

        # Purge any denylisted fields immediately
        sanitized = {k: v for k, v in data.items() if str(k).lower().strip() not in SENSITIVE_FIELD_DENYLIST}

        raw_url = str(sanitized.get("url", "")).strip()
        raw_domain = str(sanitized.get("domain", "")).strip()
        raw_title = str(sanitized.get("title", "")).strip()
        raw_text = str(sanitized.get("extracted_text", sanitized.get("text", ""))).strip()
        headings = sanitized.get("headings", [])
        if not isinstance(headings, list):
            headings = []

        ts = sanitized.get("timestamp")
        try:
            ts = float(ts) if ts is not None else time.time()
        except (ValueError, TypeError):
            ts = time.time()

        source_type = str(sanitized.get("source_type", "manual")).strip()
        if source_type not in ALLOWED_SOURCE_TYPES:
            source_type = "manual"

        status = str(sanitized.get("extraction_status", sanitized.get("status", "success"))).strip()
        if status not in ALLOWED_STATUSES:
            status = "partial"

        wc = sanitized.get("word_count")
        try:
            wc = int(wc) if wc is not None else len(raw_text.split())
        except (ValueError, TypeError):
            wc = len(raw_text.split())

        summary = sanitized.get("summary")
        if summary is not None:
            summary = str(summary).strip()

        chunks = sanitized.get("chunks", [])
        if not isinstance(chunks, list):
            chunks = []

        meta = sanitized.get("metadata", {})
        if not isinstance(meta, dict):
            meta = {}
        # Ensure metadata has no sensitive keys
        clean_meta = {k: v for k, v in meta.items() if str(k).lower().strip() not in SENSITIVE_FIELD_DENYLIST}

        return cls(
            url=raw_url,
            domain=raw_domain,
            title=raw_title,
            headings=headings,
            extracted_text=raw_text,
            timestamp=ts,
            source_type=source_type,
            extraction_status=status,
            word_count=wc,
            summary=summary,
            chunks=chunks,
            metadata=clean_meta
        )
