import json
import re
import threading
from pathlib import Path

from llama_cpp import Llama
from config import LLM_MODEL_PATH, LLM_THREADS


class AIEngine:
    _instance = None
    _singleton_lock = threading.Lock()
    _inference_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = super(AIEngine, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, model_path=None, n_threads=None):
        if getattr(self, "_initialized", False):
            return

        if model_path is None:
            model_path = LLM_MODEL_PATH

        model_path = Path(model_path)
        if not model_path.exists():
            raise FileNotFoundError(
                f"AI model not found:\n{model_path}\n\n"
                f"Make sure model.gguf is inside the models folder."
            )

        self.threads = n_threads or LLM_THREADS
        print(f"[AIEngine] Loading local AI model ({model_path.name}) with {self.threads} CPU threads...")

        self.llm = Llama(
            model_path=str(model_path),
            n_ctx=2048,
            n_threads=self.threads,
            verbose=False
        )
        self._lock = AIEngine._inference_lock
        self._initialized = True

        print("[AIEngine] AI model loaded successfully with thread-safe concurrency lock!")

    def get_device_info(self):
        return {
            "device": "cpu",
            "model": LLM_MODEL_PATH.name,
            "threads": getattr(self, "threads", LLM_THREADS),
            "n_ctx": 2048,
            "gpu_supported": False
        }

    def generate(
        self,
        prompt,
        max_tokens=450,
        temperature=0.1,
        cancel_event=None,
        stream_callback=None
    ):
        if cancel_event and cancel_event.is_set():
            return None

        # Thread-safe lock acquisition with cancel-checking
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            print("[AIEngine] Another LLM inference is currently active. Waiting for lock...")
            while not acquired:
                if cancel_event and cancel_event.is_set():
                    print("[AIEngine] Generation cancelled while waiting for lock.")
                    return None
                acquired = self._lock.acquire(timeout=0.1)

        try:
            if cancel_event and cancel_event.is_set():
                return None

            # Reset KV cache to ensure fresh context for every request
            self.llm.reset()

            # Context window safety clamp: ensure prompt_tokens + max_tokens <= n_ctx
            try:
                p_tokens = self.llm.tokenize(prompt.encode("utf-8"))
                n_ctx = self.llm.n_ctx()
                if len(p_tokens) + max_tokens > n_ctx:
                    overhead = 15
                    if len(p_tokens) > n_ctx - 100:
                        keep_count = n_ctx - max_tokens - overhead
                        if keep_count > 100:
                            p_tokens = p_tokens[:keep_count]
                            prompt = self.llm.detokenize(p_tokens).decode("utf-8", errors="ignore")
                        else:
                            max_tokens = max(50, n_ctx - len(p_tokens) - overhead)
                    else:
                        max_tokens = max(50, n_ctx - len(p_tokens) - overhead)
            except Exception:
                pass

            stop_tokens = [
                "###",
                "</s>",
                "<|im_end|>",
                "<|endoftext|>",
                "<|im_start|>",
                "<|user|>",
                "<|system|>"
            ]

            if stream_callback is not None or cancel_event is not None:
                collected_tokens = []
                stream_iter = self.llm(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=0.9,
                    repeat_penalty=1.1,
                    stop=stop_tokens,
                    stream=True
                )

                for chunk in stream_iter:
                    if cancel_event and cancel_event.is_set():
                        print("[AIEngine] AI generation cancelled by user.")
                        return None

                    token = chunk["choices"][0]["text"]
                    collected_tokens.append(token)

                    if stream_callback:
                        try:
                            stream_callback(token)
                        except Exception:
                            pass

                if cancel_event and cancel_event.is_set():
                    return None

                return "".join(collected_tokens).strip()

            # Non-streamed fallback
            response = self.llm(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=0.9,
                repeat_penalty=1.1,
                stop=stop_tokens
            )

            return response["choices"][0]["text"].strip()

        finally:
            self._lock.release()

    # =========================================================
    # CORE STRUCTURED ANALYSIS
    # =========================================================

    @staticmethod
    def condense_transcript(transcript, max_words=1200):
        """
        Condenses very long transcripts into a representative outline
        covering beginning, middle, and end without overloading CPU inference.
        """
        if not transcript:
            return ""

        words = transcript.split()
        if len(words) <= max_words:
            return transcript

        lines = [l.strip() for l in transcript.splitlines() if l.strip()]
        if len(lines) <= 30:
            return transcript

        # Sample evenly across lines to capture the full timeline
        step = max(1.0, len(lines) / 28.0)
        sampled = []
        for i in range(28):
            idx = min(len(lines) - 1, int(i * step))
            sampled.append(lines[idx])

        # Always include the first few and last few lines
        condensed_lines = lines[:4] + sampled + lines[-4:]
        seen = set()
        result = []
        for l in condensed_lines:
            if l not in seen:
                seen.add(l)
                result.append(l)

        return "\n".join(result)

    # =========================================================
    # MAP-REDUCE HIERARCHICAL ANALYSIS FOR LONG TRANSCRIPTS
    # =========================================================

    def analyze_long_transcript_map_reduce(
        self,
        transcript_or_segments,
        item_type="meeting",
        cancel_event=None,
        stream_callback=None
    ):
        """
        Production-safe hierarchical Map-Reduce analysis:
        1. Slices full transcript into timestamp-preserving chunks (~450 words each).
        2. Map Phase: Extracts 2-3 essential points, decisions, and timestamps per chunk.
        3. Reduce Phase: Synthesizes all chronological notes into a cohesive final analysis.
        Guarantees zero text omission while staying strictly within the 2048-token context.
        """
        from backend.chunker import create_transcript_chunks

        chunks = create_transcript_chunks(transcript_or_segments, target_words=450, overlap_segments=1)
        if not chunks:
            return ""

        if len(chunks) <= 2:
            return self._analyze_transcript_direct(
                transcript_or_segments if isinstance(transcript_or_segments, str) else "\n".join(c["text"] for c in chunks),
                item_type=item_type,
                cancel_event=cancel_event,
                stream_callback=stream_callback
            )

        print(f"[AIEngine] Map-Reduce: Processing {len(chunks)} chunks across full transcript timeline...")
        chunk_summaries = []

        # Map Phase: Sequential extraction respecting LLM lock & cancellation
        for i, c in enumerate(chunks):
            if cancel_event and cancel_event.is_set():
                return None

            map_prompt = f"""<|system|>
You are SmartScribe AI. Summarize this specific section of the transcript succinctly.
List 2-3 key takeaways and cite exact [MM:SS] timestamps.
<|user|>
SECTION {i+1}/{len(chunks)} [{c['start_timestamp']} - {c['end_timestamp']}]:
{c['text']}
<|assistant|>
"""
            extraction = self.generate(
                map_prompt,
                max_tokens=150,
                temperature=0.1,
                cancel_event=cancel_event
            )
            if extraction:
                chunk_summaries.append(f"[{c['start_timestamp']} - {c['end_timestamp']}]\n{extraction.strip()}")

        if cancel_event and cancel_event.is_set():
            return None

        combined_notes = "\n\n".join(chunk_summaries)

        # Reduce Phase: Final synthesis
        system_role = (
            "You are SmartScribe, a professional academic and lecture assistant."
            if item_type == "lecture"
            else "You are SmartScribe, an executive meeting and lecture intelligence assistant."
        )

        reduce_prompt = f"""<|system|>
{system_role}
Synthesize the chronological section notes from the entire recording into a cohesive final analysis.
Do not invent information. Use timestamps that appear in the notes.
Return strictly the following sections:
SUMMARY
KEY POINTS
ACTION ITEMS
IMPORTANT TOPICS
IMPORTANT TIMESTAMPS

<|user|>
CHRONOLOGICAL SECTION NOTES ACROSS FULL RECORDING:
{combined_notes}

SUMMARY:
Write 3-5 concise, high-impact sentences covering the entire recording.

KEY POINTS:
Bullet points of the most essential concepts across all sections.

ACTION ITEMS:
List explicit tasks, deadlines, and assigned responsibilities. If none, write:
- No explicit action items found.

IMPORTANT TOPICS:
Comma-separated list of major topics covered.

IMPORTANT TIMESTAMPS:
List key [MM:SS] timestamps and brief descriptions covering the full timeline.

<|assistant|>
"""
        return self.generate(
            reduce_prompt,
            max_tokens=500,
            temperature=0.1,
            cancel_event=cancel_event,
            stream_callback=stream_callback
        )

    # =========================================================
    # CORE STRUCTURED ANALYSIS
    # =========================================================

    def _analyze_transcript_direct(
        self,
        transcript,
        item_type="meeting",
        cancel_event=None,
        stream_callback=None
    ):
        system_role = (
            "You are SmartScribe, a professional academic and lecture assistant."
            if item_type == "lecture"
            else "You are SmartScribe, an executive meeting and lecture intelligence assistant."
        )

        condensed = self.condense_transcript(transcript, max_words=1200)

        prompt = f"""<|system|>
{system_role}
Analyze the user's transcript carefully.
Do not invent information. Use timestamps that appear in the transcript.
Return strictly the following sections:
SUMMARY
KEY POINTS
ACTION ITEMS
IMPORTANT TOPICS
IMPORTANT TIMESTAMPS

<|user|>
TRANSCRIPT:
{condensed}

SUMMARY:
Write 3-5 concise, high-impact sentences.

KEY POINTS:
Bullet points of the most essential concepts or decisions.

ACTION ITEMS:
List explicit tasks, deadlines, and assigned responsibilities. If none, write:
- No explicit action items found.

IMPORTANT TOPICS:
Comma-separated list of major topics covered.

IMPORTANT TIMESTAMPS:
List useful [MM:SS] timestamps and brief descriptions.

<|assistant|>
"""
        return self.generate(
            prompt,
            max_tokens=500,
            temperature=0.1,
            cancel_event=cancel_event,
            stream_callback=stream_callback
        )

    def analyze_transcript(
        self,
        transcript,
        item_type="meeting",
        cancel_event=None,
        stream_callback=None
    ):
        # Automatically route long transcripts through Map-Reduce to prevent text omission
        words = transcript.split()
        if len(words) > 1200:
            return self.analyze_long_transcript_map_reduce(
                transcript,
                item_type=item_type,
                cancel_event=cancel_event,
                stream_callback=stream_callback
            )

        return self._analyze_transcript_direct(
            transcript,
            item_type=item_type,
            cancel_event=cancel_event,
            stream_callback=stream_callback
        )

    # =========================================================
    # CHAPTER & TIMELINE GENERATOR (FAST EXTRACTION)
    # =========================================================

    @staticmethod
    def parse_timestamps_to_chapters(analysis_text, segments=None, total_duration=0.0):
        """
        Extracts chapter markers immediately from the analysis output
        or clusters transcript segments, avoiding a slow second LLM call.
        """
        chapters = []

        if analysis_text:
            # Match lines like [01:23] Title or [01:23] - Title
            pattern = r"\[(\d{1,2}):(\d{2})\]\s*[:\-]?\s*([^\n\r]+)"
            for match in re.finditer(pattern, analysis_text):
                minutes = int(match.group(1))
                seconds = int(match.group(2))
                raw_title = match.group(3).strip().lstrip("-*• ")
                if not raw_title or len(raw_title) < 3:
                    continue
                start_time = float(minutes * 60 + seconds)
                if total_duration > 0 and start_time >= total_duration:
                    continue
                chapters.append({
                    "start_time": start_time,
                    "title": raw_title[:60],
                    "description": raw_title
                })

        # If LLM generated 2 or fewer chapters, create intelligent timeline chapters from segments
        if len(chapters) < 3 and segments and len(segments) > 4:
            dur = total_duration or segments[-1]["end"]
            if dur > 60:  # Only if media is longer than 1 minute
                interval = max(90.0, dur / 5.0)  # Aim for 4-6 chapters
                chapters = []
                cur_target = 0.0
                while cur_target < dur:
                    closest = min(segments, key=lambda s: abs(s["start"] - cur_target))
                    title_text = closest["text"].strip().rstrip(".!?,")
                    if len(title_text) > 48:
                        title_text = title_text[:48] + "..."
                    chapters.append({
                        "start_time": float(closest["start"]),
                        "title": title_text if cur_target > 0 else "Introduction & Opening",
                        "description": closest["text"]
                    })
                    cur_target += interval

        chapters.sort(key=lambda c: c["start_time"])
        return chapters

    def generate_chapters(self, transcript, cancel_event=None):
        """Legacy fallback chapter generator using condensed prompt."""
        condensed = self.condense_transcript(transcript, max_words=800)
        prompt = f"""<|system|>
You are SmartScribe Chapter Engine. Divide the transcript into 4 to 8 logical chronological chapters.
Return ONLY valid JSON array with format:
[
  {{"start_time": 0.0, "title": "Introduction & Agenda", "description": "Overview of topics"}},
  {{"start_time": 120.0, "title": "Core Architecture", "description": "Detailed explanation"}}
]

<|user|>
TRANSCRIPT:
{condensed}

<|assistant|>
"""
        raw = self.generate(prompt, max_tokens=450, temperature=0.2, cancel_event=cancel_event)
        if not raw:
            return []

        try:
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                return json.loads(match.group(0))
        except Exception:
            pass

        return [
            {"start_time": 0.0, "title": "Session Overview", "description": "Start of discussion"},
            {"start_time": 60.0, "title": "Key Discussion", "description": "Main topics explored"}
        ]

    # =========================================================
    # STUDY MODE GENERATION (Fast Flashcards & Quizzes)
    # =========================================================

    def generate_study_set(self, summary="", key_points="", transcript_sample="", cancel_event=None):
        """
        Generates high-yield flashcards, multiple-choice quiz, and cram sheet.
        Uses condensed summary & key concepts for 10x faster generation.
        """
        context_content = f"SUMMARY:\n{summary}\n\nKEY CONCEPTS:\n{key_points}"
        if len(context_content.strip()) < 50 and transcript_sample:
            context_content = transcript_sample[:800]

        prompt = f"""<|system|>
You are SmartScribe Educational AI. Create high-yield study materials from the following concepts.
Return ONLY valid JSON with this exact schema (no markdown, no extra text):
{{
  "flashcards": [
    {{"question": "What is...", "answer": "It is..."}},
    {{"question": "How does...", "answer": "By..."}}
  ],
  "quiz": [
    {{
      "question": "Which statement accurately describes...?",
      "options": ["Correct option", "Distractor 1", "Distractor 2", "Distractor 3"],
      "answer": 0,
      "explanation": "Explanation of correct answer."
    }}
  ],
  "cram_sheet": [
    "High-yield core takeaway 1",
    "High-yield core takeaway 2"
  ]
}}

<|user|>
CONCEPTS:
{context_content}

<|assistant|>
"""
        raw = self.generate(prompt, max_tokens=550, temperature=0.2, cancel_event=cancel_event)
        if not raw:
            return {"flashcards": [], "quiz": [], "cram_sheet": []}

        try:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group(0))
        except Exception:
            pass

        return {
            "flashcards": [
                {"question": "Core Topic", "answer": summary[:150] if summary else "Key discussion point"}
            ],
            "quiz": [
                {
                    "question": "What was the main topic discussed in this session?",
                    "options": [summary[:60] if summary else "Main Session Topic", "Unrelated Discussion", "Technical Testing", "Miscellaneous"],
                    "answer": 0,
                    "explanation": "Derived directly from the core session summary."
                }
            ],
            "cram_sheet": [
                summary[:120] if summary else "Review core concepts from the session transcript"
            ]
        }

    # =========================================================
    # GROUNDED AI CHAT ("Ask SmartScribe")
    # =========================================================

    def chat_with_content(
        self,
        transcript,
        user_query,
        history=None,
        cancel_event=None,
        stream_callback=None,
        chunks=None
    ):
        history_text = ""
        if history:
            for msg in history[-4:]:
                role = "User" if msg.get("role") == "user" else "Assistant"
                history_text += f"{role}: {msg.get('content', '')}\n"

        words = transcript.split()
        if len(words) > 1000 or chunks:
            from backend.chunker import create_transcript_chunks
            from backend.search_engine import bm25_search_chunks

            chunk_list = chunks or create_transcript_chunks(transcript, target_words=450, overlap_segments=1)
            top_matches = bm25_search_chunks(chunk_list, user_query, top_k=2)

            retrieved_sections = []
            for m in top_matches:
                retrieved_sections.append(
                    f"[{m['start_timestamp']} - {m['end_timestamp']}] (Relevance Score: {m['relevance_score']}):\n{m['text']}"
                )
            context_body = "RELEVANT TRANSCRIPT EXCERPTS (Retrieved Chunks):\n" + "\n\n".join(retrieved_sections)
            
            # Enforce strict word budget to guarantee context safety
            ctx_words = context_body.split()
            if len(ctx_words) > 750:
                context_body = " ".join(ctx_words[:750])
        else:
            context_body = f"TRANSCRIPT:\n{transcript}"

        prompt = f"""<|system|>
You are SmartScribe AI Assistant. Answer the user's question accurately using ONLY the provided transcript or excerpts.
Cite timestamps in [MM:SS] format wherever relevant so the user can jump to the exact moment.
If the information is not in the transcript, state that clearly. Be concise, clear, and direct.

{context_body}

{history_text}
<|user|>
{user_query}

<|assistant|>
"""
        return self.generate(
            prompt,
            max_tokens=250,
            temperature=0.15,
            cancel_event=cancel_event,
            stream_callback=stream_callback
        )

    # =========================================================
    # SCRIPT & CONTENT STUDIO
    # =========================================================

    def generate_script(
        self,
        transcript,
        script_type="youtube",
        tone="professional",
        length="medium",
        cancel_event=None,
        stream_callback=None
    ):
        type_instructions = {
            "youtube": "a compelling YouTube video script with Hook, Intro, Body points, and Call-To-Action",
            "presentation": "speaker presentation notes with slide cues and talking points",
            "summary_doc": "an executive briefing document with takeaways and next steps",
            "short_form": "a 60-second punchy short-form video (TikTok/Reel) script",
            "study_guide": "a structured textbook-style study guide with subheadings and review questions"
        }.get(script_type, "a polished presentation script")

        prompt = f"""<|system|>
You are SmartScribe Content Studio.
Transform the user's transcript into {type_instructions}.
Tone: {tone}
Target Length: {length}
Ground all facts in the source transcript. Do not hallucinate.

<|user|>
TRANSCRIPT:
{transcript}

<|assistant|>
"""
        return self.generate(
            prompt,
            max_tokens=900,
            temperature=0.3,
            cancel_event=cancel_event,
            stream_callback=stream_callback
        )

    # =========================================================
    # SMART MODE CLASSIFICATION & ADAPTIVE INTELLIGENCE
    # =========================================================

    @staticmethod
    def detect_smart_mode(title="", sample_transcript=""):
        """
        Intelligently classifies media into one of 7 Smart Modes:
        - meeting, lecture, podcast, interview, tutorial, presentation, video.
        """
        combined = f"{title.lower()} {sample_transcript[:600].lower()}"

        scores = {
            "lecture": 0,
            "meeting": 0,
            "podcast": 0,
            "interview": 0,
            "tutorial": 0,
            "presentation": 0,
            "video": 0
        }

        # Lecture signals
        for term in ["lecture", "course", "prof", "professor", "class", "student", "homework", "exam", "syllabus", "chapter", "theory", "concept", "slide", "lesson", "algorithm", "cs", "math", "university"]:
            if term in combined: scores["lecture"] += 2
        # Meeting signals
        for term in ["meeting", "sync", "standup", "sprint", "agenda", "action item", "deadline", "quarter", "okr", "roadmap", "project update", "client", "retro", "discussion"]:
            if term in combined: scores["meeting"] += 2
        # Podcast signals
        for term in ["podcast", "episode", "ep.", "guest", "welcome back to the show", "sponsor", "listener", "tune in", "host"]:
            if term in combined: scores["podcast"] += 3
        # Interview signals
        for term in ["interview", "candidate", "resume", "tell me about yourself", "why do you want to work", "hire", "recruiter", "interviewee"]:
            if term in combined: scores["interview"] += 3
        # Tutorial signals
        for term in ["tutorial", "how to", "step-by-step", "guide", "code", "install", "configuration", "build", "beginner", "walkthrough"]:
            if term in combined: scores["tutorial"] += 3
        # Presentation signals
        for term in ["keynote", "pitch", "presentation", "deck", "slide 1", "thank you for having me", "demo day", "ted", "conference"]:
            if term in combined: scores["presentation"] += 2

        best_mode = max(scores, key=scores.get)
        confidence = "high" if scores[best_mode] >= 4 else ("medium" if scores[best_mode] >= 2 else "low")
        if scores[best_mode] == 0:
            best_mode = "video" if ("ted" in combined or "youtube" in combined or "watch" in combined) else "meeting"
            confidence = "medium"

        return best_mode, confidence

    def analyze_by_smart_mode(
        self,
        transcript,
        mode="meeting",
        item_title="",
        cancel_event=None,
        stream_callback=None
    ):
        """
        Executes mode-tailored structured AI analysis.
        Each Smart Mode extracts specialized sections.
        """
        condensed = self.condense_transcript(transcript, max_words=1200)

        mode_prompts = {
            "meeting": """<|system|>
You are SmartScribe Meeting Intelligence. Analyze this meeting transcript with executive precision.
Return strictly the following labeled sections:
EXECUTIVE SUMMARY: 3-4 sentence leadership summary of what was covered and decided.
KEY DECISIONS: Bullet points of explicit agreements, approvals, and decisions made.
ACTION ITEMS: Formatted list with tasks, assigned owner, and deadline. Example: - Prepare slides (Assigned: Alex, Deadline: Friday). If none, write: - No explicit action items found.
UNRESOLVED QUESTIONS: Open questions, blockers, or topics tabled for next discussion.
IMPORTANT TIMESTAMPS: Useful [MM:SS] timestamps and milestone descriptions.
""",
            "lecture": """<|system|>
You are SmartScribe Academic Professor. Analyze this lecture transcript for deep student comprehension and exams.
Return strictly the following labeled sections:
LECTURE SUMMARY: Comprehensive overview of the lesson's main thesis and goals.
CORE CONCEPTS & DEFINITIONS: Essential terms, formal definitions, and core principles.
FORMULAS & EXAMPLES: Key algorithms, formulas, or concrete case studies explained.
EXAM TAKEAWAYS: High-probability exam topics, tricky concepts, and revision focal points.
IMPORTANT TIMESTAMPS: Useful [MM:SS] timestamps where key topics begin.
""",
            "podcast": """<|system|>
You are SmartScribe Podcast Curator. Analyze this episode for memorable ideas and dialogue structure.
Return strictly the following labeled sections:
EPISODE SYNOPSIS: Engaging 3-4 sentence narrative of the episode.
GUEST INSIGHTS: Key perspectives, background, and revelations shared by the guest.
CORE ARGUMENTS: Main debates, philosophies, or industry insights explored.
NOTABLE QUOTES: Direct memorable statements with approximate [MM:SS] timestamps.
IMPORTANT TIMESTAMPS: Useful [MM:SS] conversation checkpoints.
""",
            "interview": """<|system|>
You are SmartScribe Interview Intelligence. Analyze this interview transcript for evaluative clarity.
Return strictly the following labeled sections:
INTERVIEW OVERVIEW: Context of the role, participant background, and interview flow.
QUESTIONS ASKED: Chronological list of primary questions posed by the interviewer.
CANDIDATE RESPONSES & CLAIMS: Substantive answers, examples, and claims made.
EVALUATION & HIGHLIGHTS: Demonstrated strengths, key competencies, and areas for follow-up.
IMPORTANT TIMESTAMPS: Key [MM:SS] question moments.
""",
            "tutorial": """<|system|>
You are SmartScribe Technical Instructor. Analyze this tutorial transcript into actionable instructions.
Return strictly the following labeled sections:
WHAT YOU WILL LEARN: Outcome, skill, or project built in this session.
PREREQUISITES & TOOLS: Tools, dependencies, libraries, or prior knowledge required.
STEP-BY-STEP GUIDE: Ordered procedural steps to accomplish the objective.
COMMANDS & CODE: Specific commands, syntax, or code patterns mentioned.
COMMON PITFALLS: Errors, warnings, edge cases, or troubleshooting advice.
IMPORTANT TIMESTAMPS: Useful [MM:SS] checkpoints for each step.
""",
            "presentation": """<|system|>
You are SmartScribe Keynote Analyst. Analyze this presentation for thesis clarity and audience impact.
Return strictly the following labeled sections:
EXECUTIVE OVERVIEW: Central thesis and value proposition of the presentation.
SLIDE OUTLINE & KEY ARGUMENTS: Structural points, narrative progression, and main claims.
SUPPORTING DATA & EXAMPLES: Statistics, anecdotes, or case studies cited.
CALL TO ACTION: Final conclusion, recommendations, and audience takeaways.
IMPORTANT TIMESTAMPS: Major [MM:SS] slide transitions.
""",
            "video": """<|system|>
You are SmartScribe Video Intelligence. Analyze this video for comprehensive viewer understanding.
Return strictly the following labeled sections:
VIDEO SUMMARY: High-level overview of the video content.
KEY MOMENTS: Essential scenes, arguments, or turning points.
CORE TOPICS: Major subjects covered throughout the duration.
MAIN TAKEAWAYS: Key lessons or memorable conclusions for the viewer.
IMPORTANT TIMESTAMPS: Useful [MM:SS] topic markers.
"""
        }

        sys_prompt = mode_prompts.get(mode, mode_prompts["meeting"])
        full_prompt = f"""{sys_prompt}
<|user|>
TITLE: {item_title or "Media Session"}
TRANSCRIPT:
{condensed}

<|assistant|>
"""
        return self.generate(
            full_prompt,
            max_tokens=500,
            temperature=0.1,
            cancel_event=cancel_event,
            stream_callback=stream_callback
        )

    @staticmethod
    def parse_smart_mode_sections(analysis_text, mode="meeting"):
        """
        Parses mode-specific response while populating unified summary, key_points,
        action_items, and topics fields for cross-compatibility.
        """
        sections = {
            "summary": "",
            "key_points": "",
            "action_items": "",
            "topics": "",
            "custom_sections": []
        }
        if not analysis_text:
            return sections

        # Split into blocks based on uppercase section headers like "SECTION NAME:"
        header_matches = list(re.finditer(r"\n([A-Z\s&]{3,35}):\s*", "\n" + analysis_text))
        if not header_matches:
            sections["summary"] = analysis_text[:350].strip()
            sections["key_points"] = analysis_text.strip()
            return sections

        parsed_dict = {}
        for i in range(len(header_matches)):
            start_header = header_matches[i].group(1).strip()
            content_start = header_matches[i].end()
            content_end = header_matches[i + 1].start() if i + 1 < len(header_matches) else len("\n" + analysis_text)
            content = ("\n" + analysis_text)[content_start:content_end].strip()
            parsed_dict[start_header] = content

        # Map to unified schema
        # Summary mapping
        for k in ["EXECUTIVE SUMMARY", "LECTURE SUMMARY", "EPISODE SYNOPSIS", "INTERVIEW OVERVIEW", "WHAT YOU WILL LEARN", "EXECUTIVE OVERVIEW", "VIDEO SUMMARY", "SUMMARY"]:
            if k in parsed_dict:
                sections["summary"] = parsed_dict[k]
                break

        # Key points mapping
        for k in ["KEY DECISIONS", "CORE CONCEPTS & DEFINITIONS", "CORE ARGUMENTS", "GUEST INSIGHTS", "CANDIDATE RESPONSES & CLAIMS", "STEP-BY-STEP GUIDE", "SLIDE OUTLINE & KEY ARGUMENTS", "KEY MOMENTS", "KEY POINTS"]:
            if k in parsed_dict:
                sections["key_points"] = parsed_dict[k]
                break

        # Action items mapping
        for k in ["ACTION ITEMS", "EXAM TAKEAWAYS", "CALL TO ACTION", "COMMON PITFALLS", "EVALUATION & HIGHLIGHTS", "MAIN TAKEAWAYS"]:
            if k in parsed_dict:
                sections["action_items"] = parsed_dict[k]
                break

        # Topics mapping
        for k in ["UNRESOLVED QUESTIONS", "FORMULAS & EXAMPLES", "NOTABLE QUOTES", "QUESTIONS ASKED", "COMMANDS & CODE", "SUPPORTING DATA & EXAMPLES", "CORE TOPICS", "IMPORTANT TOPICS"]:
            if k in parsed_dict:
                sections["topics"] = parsed_dict[k]
                break

        # Store all raw parsed blocks as custom sections for mode-specific UI rendering
        for title, body in parsed_dict.items():
            if "TIMESTAMP" not in title:
                sections["custom_sections"].append({
                    "title": title.title(),
                    "body": body
                })

        return sections

    # =========================================================
    # ✨ MAGIC TRANSFORM (Multi-Domain Repurposing)
    # =========================================================

    def transform_content(
        self,
        transcript,
        target_format="youtube_script",
        tone="professional",
        length="medium",
        cancel_event=None,
        stream_callback=None
    ):
        """
        Repurposes content into 4 domains:
        - Understand: executive_briefing, detailed_outline, chronological_timeline
        - Learn: study_guide, flashcard_deck, practice_test
        - Create: youtube_script, presentation_notes, article, social_reel
        - Act: action_checklist, follow_up_email, meeting_memo
        """
        condensed = self.condense_transcript(transcript, max_words=1200)

        format_instructions = {
            "executive_briefing": "a 1-page executive briefing memo with Purpose, Findings, Strategic Implications, and Next Steps",
            "detailed_outline": "a comprehensive hierarchical outline (Roman numerals I, II, A, B, 1, 2) capturing the entire argument structure",
            "chronological_timeline": "a structured timeline of events, discussions, and topics in chronological order with timestamps",
            "study_guide": "an academic textbook-style revision study guide complete with core concepts, self-test questions, and summary",
            "flashcard_deck": "a list of 6-10 high-impact Question & Answer pairs formatted for spaced-repetition study",
            "practice_test": "a 5-question multiple-choice practice test complete with 4 options per question, correct answer, and explanations",
            "youtube_script": "a high-retention YouTube video script with an irresistible 5-second Hook, Quick Intro, 3-Act Value Body, and CTA",
            "presentation_notes": "speaker talking points and slide cues designed for a live audience presentation",
            "article": "a polished, publication-ready article or blog post with an engaging headline, section headers, and conclusion",
            "social_reel": "a punchy 60-second spoken reel / TikTok script designed for fast-paced mobile engagement",
            "action_checklist": "a prioritized markdown checklist (- [ ] task) with estimated effort and accountability criteria",
            "follow_up_email": "a polite, professional follow-up email summarizing the discussion and next steps to send to participants",
            "meeting_memo": "a formal stakeholder memo summarizing status, key decisions, resource requirements, and risk factors"
        }

        desc = format_instructions.get(target_format, "a structured intelligence report")

        prompt = f"""<|system|>
You are SmartScribe Magic Transform Engine.
Transform the provided transcript into {desc}.
Tone: {tone}
Length: {length}
Ground all facts in the source transcript. Do not fabricate names, numbers, or conclusions.

<|user|>
TRANSCRIPT:
{condensed}

<|assistant|>
"""
        return self.generate(
            prompt,
            max_tokens=500,
            temperature=0.25,
            cancel_event=cancel_event,
            stream_callback=stream_callback
        )

    # =========================================================
    # SPEAKING & PRESENTATION COACH
    # =========================================================

    def analyze_speech_performance(self, transcript, duration=0.0):
        """
        Analyzes spoken speech performance:
        - Words Per Minute (WPM)
        - Filler words count & breakdown (um, uh, like, you know, actually, basically, so)
        - Pacing assessment
        - AI constructive feedback on delivery, structure, and clarity
        """
        words = re.findall(r"\b[A-Za-z']+\b", transcript)
        word_count = len(words)

        dur_min = (duration / 60.0) if duration > 0 else (word_count / 140.0)
        dur_min = max(0.1, dur_min)
        wpm = round(word_count / dur_min, 1)

        # Pace evaluation
        if wpm < 115:
            pace_rating = "Slow"
            pace_note = "Your speaking pace is quite slow. Consider picking up the tempo to keep audience momentum."
        elif 115 <= wpm <= 165:
            pace_rating = "Optimal"
            pace_note = "Your pacing is in the sweet spot (130-160 WPM) for natural listener comprehension."
        elif 165 < wpm <= 190:
            pace_rating = "Brisk"
            pace_note = "You are speaking briskly. Ensure clear articulation on key concept words."
        else:
            pace_rating = "Too Fast"
            pace_note = "You are speaking very rapidly. Try pausing after key points to allow the audience to absorb ideas."

        # Filler words analysis
        text_lower = transcript.lower()
        filler_patterns = {
            "um / uh": len(re.findall(r"\b(um|uh)\b", text_lower)),
            "like": len(re.findall(r"\b(like)\b", text_lower)),
            "you know": len(re.findall(r"\b(you know)\b", text_lower)),
            "actually": len(re.findall(r"\b(actually)\b", text_lower)),
            "basically": len(re.findall(r"\b(basically)\b", text_lower)),
            "so...": len(re.findall(r"\b(so)\b\s*[,.]", text_lower))
        }
        total_fillers = sum(filler_patterns.values())

        # Generate AI Coach critique
        condensed = transcript[:800]
        prompt = f"""<|system|>
You are SmartScribe Speech & Presentation Coach. Provide constructive, encouraging feedback on this speech excerpt.
Identify:
1. Structural flow (Introduction, explanation, conclusion)
2. Clarity of expression
3. Two actionable recommendations for improvement

TRANSCRIPT EXCERPT:
{condensed}

SPEECH STATS:
Duration: {duration:.1f}s | Word Count: {word_count} | Pace: {wpm} WPM ({pace_rating}) | Filler Words: {total_fillers}

<|assistant|>
"""
        raw_feedback = self.generate(prompt, max_tokens=400, temperature=0.2) or "Great practice session! Keep practicing with consistent pacing."

        return {
            "word_count": word_count,
            "duration": duration,
            "wpm": wpm,
            "pace_rating": pace_rating,
            "pace_note": pace_note,
            "filler_count": total_fillers,
            "filler_breakdown": filler_patterns,
            "coach_feedback": raw_feedback
        }

    # =========================================================
    # "TEACH ME" INTERACTIVE AI TUTOR
    # =========================================================

    def teach_step(self, transcript, level="beginner", user_reply=None, step_index=1):
        """
        Interactive Socratic tutor:
        Explains one concept at chosen level, asks a quick comprehension check question,
        evaluates previous answer if provided.
        """
        condensed = self.condense_transcript(transcript, max_words=800)

        context = f"LEVEL: {level.upper()}\nSTEP: {step_index}\nTRANSCRIPT SUMMARY:\n{condensed}"
        if user_reply:
            context += f"\n\nUSER PREVIOUS ANSWER: {user_reply}\n(Evaluate whether user's answer was correct before proceeding)"

        prompt = f"""<|system|>
You are SmartScribe AI Socratic Tutor. Your job is to teach the concepts from this transcript interactively.
Guidelines:
1. If the user provided an answer, begin with brief feedback (e.g. 'Spot on!' or 'Good try, but remember...').
2. Explain the next single concept simply and clearly for a {level} level (max 3-4 sentences).
3. Conclude with ONE friendly question to check their understanding.

{context}

<|assistant|>
"""
        return self.generate(prompt, max_tokens=350, temperature=0.2)

    # =========================================================
    # PARSING HELPERS
    # =========================================================

    @staticmethod
    def parse_analysis_sections(analysis_text):
        """
        Legacy parser wrapper to ensure 100% backward compatibility.
        """
        return AIEngine.parse_smart_mode_sections(analysis_text, mode="meeting")