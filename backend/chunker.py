import re

def format_timestamp(seconds):
    """Formats float seconds into [MM:SS] or [HH:MM:SS]."""
    if seconds is None:
        return "[00:00]"
    sec = int(seconds)
    hours = sec // 3600
    minutes = (sec % 3600) // 60
    rem_secs = sec % 60
    if hours > 0:
        return f"[{hours:02d}:{minutes:02d}:{rem_secs:02d}]"
    return f"[{minutes:02d}:{rem_secs:02d}]"

def parse_timestamp_str(text):
    """Extracts first timestamp in seconds from line like '[01:23] Hello'."""
    m = re.search(r"\[(\d+):(\d+)(?::(\d+))?\]", text)
    if not m:
        return 0.0
    if m.group(3):
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return int(m.group(1)) * 60 + int(m.group(2))

def create_transcript_chunks(segments_or_text, target_words=450, overlap_segments=1):
    """
    Splits transcript into timestamp-preserving, overlapping chunks.
    Accepts either:
      1. A list of segment dicts: [{'start': 0.0, 'end': 5.0, 'text': '...', 'speaker': '...'}]
      2. A raw transcript string with timestamped lines: '[00:01] Speaker: Hello...'
    Returns a list of chunk dicts:
      [
        {
          'chunk_id': 'chunk_1',
          'chunk_index': 0,
          'start_time': 0.0,
          'end_time': 125.4,
          'start_timestamp': '[00:00]',
          'end_timestamp': '[02:05]',
          'speaker': 'Speaker 1',
          'text': '[00:00] Speaker 1: Hello...',
          'word_count': 455,
          'segment_count': 18
        },
        ...
      ]
    """
    if not segments_or_text:
        return []

    # Normalize into a list of segment dicts
    segments = []
    if isinstance(segments_or_text, str):
        lines = [l.strip() for l in segments_or_text.splitlines() if l.strip()]
        for idx, line in enumerate(lines):
            t_sec = parse_timestamp_str(line)
            # Clean timestamp prefix from text if present
            clean_text = re.sub(r"^\[\d+:\d+(?::\d+)?\]\s*", "", line)
            segments.append({
                "start": float(t_sec),
                "end": float(t_sec + 4.0),
                "speaker": "Speaker 1",
                "text": clean_text
            })
    elif isinstance(segments_or_text, list):
        for s in segments_or_text:
            if isinstance(s, dict):
                segments.append({
                    "start": float(s.get("start", 0.0)),
                    "end": float(s.get("end", 0.0)),
                    "speaker": s.get("speaker", "Speaker 1"),
                    "text": str(s.get("text", "")).strip()
                })

    if not segments:
        return []

    chunks = []
    current_chunk_segs = []
    current_word_count = 0
    chunk_index = 0

    for i, seg in enumerate(segments):
        words_in_seg = len(seg["text"].split())
        current_chunk_segs.append(seg)
        current_word_count += words_in_seg

        # If target word count reached and we have more segments remaining
        if current_word_count >= target_words and i < len(segments) - 1:
            start_time = current_chunk_segs[0]["start"]
            end_time = current_chunk_segs[-1]["end"]
            start_ts = format_timestamp(start_time)
            end_ts = format_timestamp(end_time)

            formatted_lines = [
                f"[{format_timestamp(s['start'])}] {s.get('speaker', 'Speaker')}: {s['text']}"
                for s in current_chunk_segs
            ]
            chunk_text = "\n".join(formatted_lines)

            chunks.append({
                "chunk_id": f"chunk_{chunk_index + 1}",
                "chunk_index": chunk_index,
                "start_time": round(start_time, 2),
                "end_time": round(end_time, 2),
                "start_timestamp": start_ts,
                "end_timestamp": end_ts,
                "speaker": current_chunk_segs[0].get("speaker", "Speaker 1"),
                "text": chunk_text,
                "word_count": current_word_count,
                "segment_count": len(current_chunk_segs)
            })

            chunk_index += 1

            # Keep modest overlap for context continuity
            overlap_count = min(overlap_segments, len(current_chunk_segs))
            current_chunk_segs = current_chunk_segs[-overlap_count:] if overlap_count > 0 else []
            current_word_count = sum(len(s["text"].split()) for s in current_chunk_segs)

    # Final trailing chunk
    if current_chunk_segs:
        start_time = current_chunk_segs[0]["start"]
        end_time = current_chunk_segs[-1]["end"]
        start_ts = format_timestamp(start_time)
        end_ts = format_timestamp(end_time)

        formatted_lines = [
            f"[{format_timestamp(s['start'])}] {s.get('speaker', 'Speaker')}: {s['text']}"
            for s in current_chunk_segs
        ]
        chunk_text = "\n".join(formatted_lines)

        chunks.append({
            "chunk_id": f"chunk_{chunk_index + 1}",
            "chunk_index": chunk_index,
            "start_time": round(start_time, 2),
            "end_time": round(end_time, 2),
            "start_timestamp": start_ts,
            "end_timestamp": end_ts,
            "speaker": current_chunk_segs[0].get("speaker", "Speaker 1"),
            "text": chunk_text,
            "word_count": sum(len(s["text"].split()) for s in current_chunk_segs),
            "segment_count": len(current_chunk_segs)
        })

    return chunks
