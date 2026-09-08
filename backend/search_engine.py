import math
import re

STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "as", "at", "be", "because", "been", "before", "being", "below",
    "between", "both", "but", "by", "can", "did", "do", "does", "doing", "down",
    "during", "each", "few", "for", "from", "further", "had", "has", "have",
    "having", "he", "her", "here", "hers", "herself", "him", "himself", "his",
    "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me",
    "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on",
    "once", "only", "or", "other", "our", "ours", "ourselves", "out", "over",
    "own", "same", "she", "should", "so", "some", "such", "than", "that", "the",
    "their", "theirs", "them", "themselves", "then", "there", "these", "they",
    "this", "those", "through", "to", "too", "under", "until", "up", "very",
    "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom",
    "why", "with", "would", "you", "your", "yours", "yourself", "yourselves"
}


def _tokenize(text):
    tokens = re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


def bm25_search_chunks(chunks, query, top_k=3, k1=1.5, b=0.75):
    """
    Zero-dependency Okapi BM25 retrieval over transcript chunks.
    Returns the top_k most relevant chunks with metadata:
    chunk_id, start_time, end_time, start_timestamp, end_timestamp, relevance_score, text.
    """
    if not chunks:
        return []

    query_tokens = _tokenize(query)
    if not query_tokens:
        return [
            {
                "chunk_id": c.get("chunk_id", f"chunk_{i+1}"),
                "start_time": c.get("start_time", 0.0),
                "end_time": c.get("end_time", 0.0),
                "start_timestamp": c.get("start_timestamp", "[00:00]"),
                "end_timestamp": c.get("end_timestamp", "[00:00]"),
                "relevance_score": 1.0,
                "text": c.get("text", "")
            }
            for i, c in enumerate(chunks[:top_k])
        ]

    doc_tokens = [_tokenize(c.get("text", "")) for c in chunks]
    doc_lens = [len(dt) for dt in doc_tokens]
    avgdl = sum(doc_lens) / max(1, len(doc_lens))
    N = len(chunks)

    df = {}
    for qt in query_tokens:
        df[qt] = sum(1 for dt in doc_tokens if qt in dt)

    scores = []
    for idx, c in enumerate(chunks):
        dt = doc_tokens[idx]
        dl = doc_lens[idx]
        tf = {}
        for token in dt:
            tf[token] = tf.get(token, 0) + 1

        score = 0.0
        for qt in query_tokens:
            n_q = df.get(qt, 0)
            idf = math.log((N - n_q + 0.5) / (n_q + 0.5) + 1.0)
            f = tf.get(qt, 0)
            if f > 0:
                numerator = f * (k1 + 1.0)
                denominator = f + k1 * (1.0 - b + b * (dl / (avgdl or 1.0)))
                score += idf * (numerator / denominator)

        scores.append((score, idx))

    scores.sort(key=lambda x: x[0], reverse=True)

    top_matches = [s for s in scores if s[0] > 0][:top_k]
    if not top_matches:
        top_matches = scores[:top_k]

    results = []
    for score, idx in top_matches:
        c = chunks[idx]
        results.append({
            "chunk_id": c.get("chunk_id", f"chunk_{idx+1}"),
            "start_time": c.get("start_time", 0.0),
            "end_time": c.get("end_time", 0.0),
            "start_timestamp": c.get("start_timestamp", "[00:00]"),
            "end_timestamp": c.get("end_timestamp", "[00:00]"),
            "relevance_score": round(score, 3),
            "text": c.get("text", "")
        })

    return results


def search_transcript(transcript, query):
    query = query.lower().strip()
    if not query:
        return []

    results = []
    for segment in transcript:
        text = segment["text"]
        if query in text.lower():
            results.append(segment)

    return results


def search_transcript_text(transcript_text, query):
    query = query.lower().strip()
    if not query:
        return []

    results = []
    for line in transcript_text.splitlines():
        if query in line.lower():
            results.append(line)

    return results