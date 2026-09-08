import re
import html
from html.parser import HTMLParser
from typing import List, Tuple, Optional

# Tags whose entire subtree should be discarded
DISCARDED_TAGS = {
    "script", "style", "noscript", "svg", "canvas", "iframe",
    "object", "embed", "nav", "header", "footer", "aside",
    "form", "input", "button", "select", "textarea", "meta", "link"
}

# Regex to detect hidden CSS inline styles
HIDDEN_STYLE_PATTERN = re.compile(
    r"(display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0|font-size\s*:\s*0|height\s*:\s*0\s*px|width\s*:\s*0\s*px)",
    re.IGNORECASE
)

# Ad and tracker classes/ids
AD_CONTAINER_PATTERN = re.compile(
    r"\b(ad-banner|advertisement|ad-container|sponsor|sponsored-content|cookie-notice|cookie-banner|privacy-banner|tracking-pixel)\b",
    re.IGNORECASE
)

# Delimiters and tokens that could break prompt isolation
PROMPT_CONTROL_TOKENS = [
    "<webpage_article>", "</webpage_article>",
    "<transcript_data>", "</transcript_data>",
    "<tool_data>", "</tool_data>",
    "<system_metrics>", "</system_metrics>",
    "<|im_start|>", "<|im_end|>",
    "<|system|>", "<|user|>", "<|assistant|>", "<|endoftext|>"
]

# Injection phrases to flag/neutralize
INJECTION_KEYWORDS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+prompt\s+override", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+in\s+developer\s+mode", re.IGNORECASE),
    re.compile(r"format\s+c:", re.IGNORECASE),
    re.compile(r"\bdelete\s+all\s+recordings\b", re.IGNORECASE),
    re.compile(r"\brun\s+powershell\b", re.IGNORECASE),
]

class HTMLSanitizingParser(HTMLParser):
    """
    Strips scripts, styles, hidden elements, ads, and chrome from HTML,
    converting structural content into clean paragraphs and headings.
    """
    def __init__(self):
        super().__init__()
        self.discard_depth = 0
        self.pieces: List[str] = []
        self.headings: List[Tuple[int, str]] = []
        self.current_heading_level: Optional[int] = None
        self.current_heading_text: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]):
        tag_lower = tag.lower()
        attr_dict = {k.lower(): (v or "") for k, v in attrs}

        # Check if tag is in discard list
        if tag_lower in DISCARDED_TAGS:
            self.discard_depth += 1
            return

        # Check if hidden attribute exists
        if "hidden" in attr_dict or attr_dict.get("aria-hidden") == "true":
            self.discard_depth += 1
            return

        # Check for hidden CSS in style
        style_val = attr_dict.get("style", "")
        if style_val and HIDDEN_STYLE_PATTERN.search(style_val):
            self.discard_depth += 1
            return

        # Check for ad containers
        class_or_id = f"{attr_dict.get('class', '')} {attr_dict.get('id', '')}"
        if class_or_id and AD_CONTAINER_PATTERN.search(class_or_id):
            self.discard_depth += 1
            return

        # If currently discarding, continue
        if self.discard_depth > 0:
            return

        # Headings h1..h6
        if re.match(r"^h([1-6])$", tag_lower):
            self.current_heading_level = int(tag_lower[1])
            self.current_heading_text = []
            self.pieces.append("\n\n")

        # Paragraphs, blockquotes, article, section, div, li
        elif tag_lower in ("p", "div", "article", "section", "blockquote"):
            self.pieces.append("\n\n")
        elif tag_lower == "li":
            self.pieces.append("\n• ")
        elif tag_lower == "br":
            self.pieces.append("\n")

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        if tag_lower in DISCARDED_TAGS:
            if self.discard_depth > 0:
                self.discard_depth -= 1
            return

        if self.discard_depth > 0:
            # We don't track arbitrary tag closing depth for style/hidden to avoid imbalance,
            # but standard parser handles simple tag closures.
            # To be safe, if a discarded non-void tag closes:
            self.discard_depth -= 1
            return

        if re.match(r"^h([1-6])$", tag_lower):
            if self.current_heading_level and self.current_heading_text:
                htext = "".join(self.current_heading_text).strip()
                if htext:
                    self.headings.append((self.current_heading_level, htext))
            self.current_heading_level = None
            self.current_heading_text = []
            self.pieces.append("\n\n")

        elif tag_lower in ("p", "div", "article", "section", "blockquote"):
            self.pieces.append("\n")

    def handle_data(self, data: str):
        if self.discard_depth > 0:
            return
        cleaned = data.replace("\xa0", " ").replace("\r", "")
        self.pieces.append(cleaned)
        if self.current_heading_level:
            self.current_heading_text.append(cleaned)

    def get_clean_text(self) -> str:
        raw = "".join(self.pieces)
        # Normalize excessive newlines
        lines = [line.strip() for line in raw.split("\n")]
        # Remove consecutive empty lines
        compact_lines = []
        last_empty = False
        for l in lines:
            if not l:
                if not last_empty:
                    compact_lines.append("")
                    last_empty = True
            else:
                compact_lines.append(l)
                last_empty = False
        return "\n".join(compact_lines).strip()


def sanitize_html_to_text(html_content: str) -> Tuple[str, List[Tuple[int, str]], bool]:
    """
    Parses HTML, removes scripts/styles/hidden/ad elements, and extracts clean text and headings.
    Returns:
      (clean_text, list_of_headings, injection_detected_bool)
    """
    if not html_content:
        return "", [], False

    parser = HTMLSanitizingParser()
    try:
        parser.feed(html_content)
        parser.close()
    except Exception:
        # Fallback regex strip if HTML parser encounters unrecoverable syntax
        stripped = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
        stripped = re.sub(r"<[^>]+>", " ", stripped)
        stripped = html.unescape(stripped)
        clean_text = re.sub(r"\s+", " ", stripped).strip()
        return clean_text, [], False

    clean_text = parser.get_clean_text()
    headings = parser.headings

    # Defend against prompt-breaking control delimiters
    clean_text = neutralize_prompt_tokens(clean_text)

    # Check for adversarial prompt injection patterns
    injection_detected = False
    for pat in INJECTION_KEYWORDS:
        if pat.search(clean_text):
            injection_detected = True
            # Sanitize the match by wrapping it with [POTENTIAL INSTRUCTION FILTERED]
            clean_text = pat.sub("[POTENTIAL INSTRUCTION NEUTRALIZED IN ARTICLE TEXT]", clean_text)

    return clean_text, headings, injection_detected


def neutralize_prompt_tokens(text: str) -> str:
    """Neutralizes control delimiters that could break out of LLM XML tags or ChatML."""
    if not text:
        return ""
    result = text
    for token in PROMPT_CONTROL_TOKENS:
        if token in result:
            safe_replacement = token.replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;")
            result = result.replace(token, safe_replacement)
    return result
