from llama_cpp import Llama

print("Loading AI model...")

llm = Llama(
    model_path="models/model.gguf",
    n_ctx=2048,
    n_threads=4,
    verbose=False
)

print("Model loaded successfully!")

response = llm(
    "Explain supervised learning in two simple sentences.",
    max_tokens=100,
    temperature=0.2
)

answer = response["choices"][0]["text"]

print("\nAI RESPONSE:")
print(answer)