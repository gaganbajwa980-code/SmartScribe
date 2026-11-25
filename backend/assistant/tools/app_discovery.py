"""
Safe application discovery for SmartScribe.
Searches Start Menu and Desktop shortcuts to locate installed applications.
Never executes arbitrary paths without validation.
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# Prohibited executable extensions (must not launch)
PROHIBITED_EXEC_EXTENSIONS = {'.bat', '.cmd', '.ps1', '.vbs', '.sh', '.msi', '.dll', '.sys', '.scr'}

# Allowed executable extensions for discovered apps
ALLOWED_EXEC_EXTENSIONS = {'.exe', '.com'}

# Safe search directories (read-only, no execution)
_SEARCH_ROOTS = [
    Path(os.environ.get('APPDATA', '')) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs',
    Path(os.environ.get('PROGRAMDATA', '')) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs',
    Path.home() / 'Desktop',
    Path(os.environ.get('PUBLIC', '')) / 'Desktop',
]


@dataclass
class DiscoveryResult:
    """Result of an application discovery search."""
    found: bool
    canonical_name: str = ""
    launch_candidates: List[str] = field(default_factory=list)  # Validated absolute exe paths only
    source: str = "not_found"  # 'start_menu' | 'desktop' | 'not_found'
    display_name: str = ""
    validation_error: str = ""  # Set if candidates exist but failed validation


def _resolve_lnk_target(lnk_path: Path) -> Optional[str]:
    """Resolves a .lnk shortcut to its target executable path.
    Returns None if resolution fails, target is not an exe, or target is prohibited.
    """
    try:
        import win32com.client
        shell = win32com.client.Dispatch('WScript.Shell')
        shortcut = shell.CreateShortcut(str(lnk_path))
        target = shortcut.TargetPath
        if not target:
            return None
        target_path = Path(target)
        # Must be absolute
        if not target_path.is_absolute():
            return None
        # Must have allowed extension
        if target_path.suffix.lower() not in ALLOWED_EXEC_EXTENSIONS:
            return None
        # Must not be prohibited
        if target_path.suffix.lower() in PROHIBITED_EXEC_EXTENSIONS:
            return None
        # Must exist on disk
        if not target_path.exists():
            return None
        return str(target_path)
    except Exception:
        return None


def discover_app(name: str, max_results: int = 3) -> DiscoveryResult:
    """Searches Start Menu and Desktop for an application matching 'name'.
    
    Only returns validated, absolute executable paths.
    Never resolves to scripts, DLLs, or prohibited file types.
    """
    name_clean = name.lower().strip()
    if not name_clean or len(name_clean) < 2:
        return DiscoveryResult(found=False, source="not_found",
                               validation_error="Search name too short or empty.")
    
    candidates = []
    source = "not_found"
    
    for search_root in _SEARCH_ROOTS:
        if not search_root.exists():
            continue
        try:
            for lnk_file in search_root.rglob('*.lnk'):
                # Check name match (stem matches query tokens)
                stem = lnk_file.stem.lower()
                if name_clean in stem or stem in name_clean or _fuzzy_match(name_clean, stem):
                    target = _resolve_lnk_target(lnk_file)
                    if target and target not in candidates:
                        candidates.append(target)
                        src = 'desktop' if 'Desktop' in str(lnk_file) else 'start_menu'
                        if source == 'not_found':
                            source = src
                    if len(candidates) >= max_results:
                        break
        except Exception:
            continue
        if len(candidates) >= max_results:
            break
    
    if candidates:
        return DiscoveryResult(
            found=True,
            canonical_name=name_clean,
            launch_candidates=candidates,
            source=source,
            display_name=name.title()
        )
    return DiscoveryResult(found=False, source="not_found", canonical_name=name_clean)


def _fuzzy_match(query: str, target: str) -> bool:
    """True if all significant query tokens appear in target."""
    tokens = [t for t in query.split() if len(t) > 2]
    if not tokens:
        return False
    return all(t in target for t in tokens)
