from llama_cpp import Llama


# -----------------------------------
# Load local Qwen model
# -----------------------------------

print("Loading local AI model...")

llm = Llama(
    model_path="models/model.gguf",
    n_ctx=4096,
    n_threads=4,
    verbose=False
)

print("AI model loaded successfully!")


# -----------------------------------
# Transcript from our Whisper test
# -----------------------------------

transcript = """
[00:00] Hello everyone! Today we are going to discuss machine learning.

[00:04] Machine learning is a branch of artificial intelligence
that allows computers to learn patterns from data.

[00:10] There are three common types of machine learning:
supervised learning, unsupervised learning, and reinforcement learning.

[00:17] In supervised learning, the model learns from labeled examples.

[00:21] Classification is used when the output is a category,
while regression is used when the output is a numerical value.

[00:28] For your assignment, build a simple classification model
and compare its accuracy with a baseline model.

[00:34] Please submit the assignment by Friday.

[00:34] In the next lecture, we will discuss model evaluation
and confusion matrices.
"""


# -----------------------------------
# Prompt
# -----------------------------------

prompt = f"""
You are SmartScribe, an intelligent lecture assistant.

Analyze the following lecture transcript.

TRANSCRIPT:
{transcript}

Generate the following:

SUMMARY:
Write a short summary of the lecture.

KEY POINTS:
List the most important concepts.

ACTION ITEMS:
List assignments, tasks, or deadlines mentioned.

IMPORTANT TOPICS:
List the major topics discussed.

IMPORTANT TIMESTAMPS:
Mention useful timestamps from the transcript.

Do not invent information.
"""


# -----------------------------------
# Generate response
# -----------------------------------

print("\nAnalyzing transcript...\n")

response = llm(
    prompt,
    max_tokens=700,
    temperature=0.2
)


answer = response["choices"][0]["text"]

print("=" * 60)
print("SMARTSCRIBE AI ANALYSIS")
print("=" * 60)

print(answer)

print("=" * 60)