import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any

@dataclass
class FileTargetSpec:
    raw_query: str
    requested_name: Optional[str] = None
    requested_type: Optional[str] = None       # "pdf", "recording", "audio", "video", "note", "document", "spreadsheet", etc.
    expected_extensions: List[str] = field(default_factory=list)
    semantic_filters: List[str] = field(default_factory=list)
    temporal_filter: Optional[str] = None     # "latest", "newest", "recent", "oldest", "first", "yesterday"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_query": self.raw_query,
            "requested_name": self.requested_name,
            "requested_type": self.requested_type,
            "expected_extensions": list(self.expected_extensions),
            "semantic_filters": list(self.semantic_filters),
            "temporal_filter": self.temporal_filter
        }

@dataclass
class StructuredTarget:
    is_found: bool = False
    resolved_path: Optional[Path] = None
    matched_filename: Optional[str] = None
    spec: Optional[FileTargetSpec] = None
    is_ambiguous: bool = False
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_found": self.is_found,
            "resolved_path": str(self.resolved_path) if self.resolved_path else None,
            "matched_filename": self.matched_filename,
            "spec": self.spec.to_dict() if self.spec else None,
            "is_ambiguous": self.is_ambiguous,
            "candidates_count": len(self.candidates),
            "error_message": self.error_message
        }

class FileTargetResolver:
    """
    Structured Target Resolution Engine for SmartScribe desktop file actions.
    Parses user requests into explicit constraints (type, extension, semantic keywords, temporal filters)
    and resolves them deterministically against approved workspace roots.
    """

    TYPE_EXTENSIONS: Dict[str, List[str]] = {
        "pdf": [".pdf"],
        "recording": [".wav", ".mp3", ".m4a", ".mp4", ".aac", ".flac"],
        "audio": [".wav", ".mp3", ".m4a", ".aac", ".flac"],
        "video": [".mp4", ".mkv", ".avi", ".mov"],
        "note": [".md", ".txt", ".docx"],
        "notes": [".md", ".txt", ".docx"],
        "document": [".pdf", ".docx", ".txt", ".md"],
        "documents": [".pdf", ".docx", ".txt", ".md"],
        "spreadsheet": [".xlsx", ".csv", ".xls"],
        "image": [".png", ".jpg", ".jpeg"],
        "text": [".txt", ".md"]
    }

    STOP_WORDS = {
        "open", "launch", "play", "show", "view", "display", "find", "get", "load",
        "my", "the", "me", "a", "an", "this", "that", "it", "them", "something",
        "site", "website", "please", "can", "you",
        "i", "need", "file", "files", "folder", "folders", "document", "documents",
        "called", "named", "titled", "with", "name", "for", "in", "to", "of", "from"
    }

    TEMPORAL_PATTERNS = [
        (re.compile(r"\b(latest|newest|most recent|recent)\b", re.IGNORECASE), "latest"),
        (re.compile(r"\b(oldest|first)\b", re.IGNORECASE), "oldest"),
        (re.compile(r"\b(yesterday(?:'s)?)\b", re.IGNORECASE), "yesterday")
    ]

    TYPE_PATTERNS = [
        (re.compile(r"\b(pdf|pdfs)\b", re.IGNORECASE), "pdf"),
        (re.compile(r"\b(smartscribe recording|audio recording|recordings?|audio|lectures?)\b", re.IGNORECASE), "recording"),
        (re.compile(r"\b(notes?|markdown)\b", re.IGNORECASE), "note"),
        (re.compile(r"\b(spreadsheets?|excel|csv)\b", re.IGNORECASE), "spreadsheet"),
        (re.compile(r"\b(documents?|docs?)\b", re.IGNORECASE), "document"),
        (re.compile(r"\b(videos?)\b", re.IGNORECASE), "video"),
        (re.compile(r"\b(images?|photos?|screenshots?)\b", re.IGNORECASE), "image")
    ]

    @classmethod
    def parse_query(cls, query: str) -> FileTargetSpec:
        """
        Parses a natural language or structured file query into a FileTargetSpec.
        """
        q = (query or "").strip()
        q_lower = q.lower()

        temporal_filter = None
        for pattern, temp_val in cls.TEMPORAL_PATTERNS:
            if pattern.search(q_lower):
                temporal_filter = temp_val
                break

        requested_type = None
        expected_extensions: List[str] = []
        for pattern, t_val in cls.TYPE_PATTERNS:
            if pattern.search(q_lower):
                requested_type = t_val
                expected_extensions = list(cls.TYPE_EXTENSIONS.get(t_val, []))
                break

        # Check for explicit filename with extension (e.g. "lecture.pdf", "rec_01.wav")
        requested_name = None
        m_filename = re.search(r"\b([a-zA-Z0-9_.-]+\.[a-zA-Z0-9]{2,5})\b", q)
        if m_filename:
            fn = m_filename.group(1).strip()
            # Verify it's not just a trailing sentence dot on a word
            if "." in fn and not fn.endswith("."):
                requested_name = fn
                ext = Path(fn).suffix.lower()
                if ext and ext not in expected_extensions:
                    expected_extensions = [ext] + [e for e in expected_extensions if e != ext]
                if not requested_type:
                    for t_name, exts in cls.TYPE_EXTENSIONS.items():
                        if ext in exts:
                            requested_type = t_name
                            break

        # Extract semantic keywords by stripping stop words, temporal tokens, type tokens
        clean_text = q_lower
        for pattern, _ in cls.TEMPORAL_PATTERNS:
            clean_text = pattern.sub(" ", clean_text)
        for pattern, _ in cls.TYPE_PATTERNS:
            clean_text = pattern.sub(" ", clean_text)

        # Remove punctuation except underscores and hyphens
        clean_text = re.sub(r"[^\w\s-]", " ", clean_text)
        ext_tokens = {e.lstrip(".").lower() for e in expected_extensions}
        raw_tokens = [t for t in clean_text.split() if t not in cls.STOP_WORDS and t not in ext_tokens and len(t) > 1]

        semantic_filters = []
        for token in raw_tokens:
            if token not in semantic_filters:
                semantic_filters.append(token)
            # If token contains underscore or hyphen, also add sub-tokens
            if "_" in token or "-" in token:
                for sub in re.split(r"[_-]", token):
                    if sub and sub not in cls.STOP_WORDS and sub not in ext_tokens and len(sub) > 1 and sub not in semantic_filters:
                        semantic_filters.append(sub)

        # Fallback for bare "latest" or "latest recording"
        if not requested_type and not expected_extensions:
            if q_lower in ("latest", "last", "newest"):
                temporal_filter = "latest"
                requested_type = "recording"
                expected_extensions = list(cls.TYPE_EXTENSIONS["recording"])

        return FileTargetSpec(
            raw_query=q,
            requested_name=requested_name,
            requested_type=requested_type,
            expected_extensions=expected_extensions,
            semantic_filters=semantic_filters,
            temporal_filter=temporal_filter
        )

    @classmethod
    def resolve(cls, spec: FileTargetSpec, roots: List[Path]) -> StructuredTarget:
        """
        Resolves a FileTargetSpec against candidate files in approved roots.
        Enforces strict extension matching, semantic filtering, and temporal ranking.
        """
        candidates: List[Dict[str, Any]] = []

        for root in roots:
            p_root = Path(root).resolve()
            if not p_root.exists() or not p_root.is_dir():
                continue

            # Iterate files directly and one level deep in subdirectories
            for f in p_root.rglob("*"):
                try:
                    if not f.is_file():
                        continue
                    
                    # Ignore hidden files or cache directories
                    if any(part.startswith(".") or part in ("__pycache__", "node_modules") for part in f.parts):
                        continue

                    f_ext = f.suffix.lower()
                    f_name_lower = f.name.lower()
                    f_stem_lower = f.stem.lower()

                    # Constraint 1: Extension Match
                    if spec.expected_extensions:
                        if f_ext not in spec.expected_extensions:
                            continue

                    # Constraint 2: Requested Name Match
                    exact_name_match = False
                    if spec.requested_name:
                        if f_name_lower == spec.requested_name.lower():
                            exact_name_match = True
                        elif spec.requested_name.lower() in f_name_lower:
                            pass
                        else:
                            continue

                    # Constraint 3: Semantic Keyword Match
                    semantic_score = 0
                    if spec.semantic_filters:
                        matched_keywords = [k for k in spec.semantic_filters if k in f_stem_lower or k in f_name_lower]
                        if not matched_keywords and not exact_name_match:
                            continue
                        semantic_score = len(matched_keywords)

                    st = f.stat()
                    candidates.append({
                        "path": f,
                        "name": f.name,
                        "mtime": st.st_mtime,
                        "size": st.st_size,
                        "exact_name_match": exact_name_match,
                        "semantic_score": semantic_score
                    })
                except Exception:
                    continue

        if not candidates:
            type_desc = spec.requested_type or "file"
            sem_desc = f" matching {spec.semantic_filters}" if spec.semantic_filters else ""
            return StructuredTarget(
                is_found=False,
                resolved_path=None,
                matched_filename=None,
                spec=spec,
                is_ambiguous=False,
                candidates=[],
                error_message=f"No matching {type_desc}{sem_desc} found in approved workspace folders."
            )

        # Ranking Candidates:
        # 1. Exact name matches get highest priority (score +1000)
        # 2. Higher semantic score
        # 3. Temporal sorting
        now = time.time()
        yesterday_target = now - 86400

        def sort_key(item: Dict[str, Any]):
            exact_bonus = 1000 if item["exact_name_match"] else 0
            sem = item["semantic_score"]
            mtime = item["mtime"]

            if spec.temporal_filter in ("latest", "newest", "recent"):
                # Maximize exact, semantic score, then mtime
                return (exact_bonus + sem, mtime)
            elif spec.temporal_filter in ("oldest", "first"):
                # Maximize exact, semantic score, but minimize mtime
                return (exact_bonus + sem, -mtime)
            elif spec.temporal_filter == "yesterday":
                # Minimize distance to yesterday
                diff = abs(mtime - yesterday_target)
                return (exact_bonus + sem, -diff)
            else:
                # Default: semantic score then latest mtime
                return (exact_bonus + sem, mtime)

        candidates.sort(key=sort_key, reverse=True)

        top = candidates[0]
        # Check ambiguity: if top two candidates have the exact same score and mtime
        is_ambiguous = False
        if len(candidates) > 1:
            cand1 = candidates[0]
            cand2 = candidates[1]
            if (cand1["exact_name_match"] == cand2["exact_name_match"] and
                cand1["semantic_score"] == cand2["semantic_score"] and
                abs(cand1["mtime"] - cand2["mtime"]) < 0.001):
                is_ambiguous = True

        return StructuredTarget(
            is_found=True,
            resolved_path=top["path"],
            matched_filename=top["name"],
            spec=spec,
            is_ambiguous=is_ambiguous,
            candidates=candidates,
            error_message=None
        )
