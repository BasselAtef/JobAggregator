import ollama
import pandas as pd

# Load the spreadsheet
try:
    data = pd.read_excel("ai_jobs.xlsx")
except Exception as e:
    print(f"Error loading ai_jobs.xlsx: {e}")
    raise SystemExit

# Grab the last 20 rows and truncate descriptions to keep prompt lightweight
last_batch = data.tail(20).copy()
if "description" in last_batch.columns:
    last_batch["description"] = last_batch["description"].astype(str).str[:150] + "..."

cols = [c for c in ["title", "company", "fetched_at", "url", "description"] if c in last_batch.columns]
records_text = last_batch[cols].to_string(index=False)

def get_completion(prompt, model="qwen3.5:4b"):
    client = ollama.Client(timeout=600)
    response = client.chat(
        model=model,
        think=False,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0}
    )
    return response.message.content

# Prompt        
final_prompt = f"""
You are a helpful assistant. Below is data from an uploaded xlsx file.

SKIP any job that have the word "Senior"

Print only 10 results in this json syntax: 
```
job index:"", job_title: "", fetched_at: ""(DD-MM-YYYY), job_url: "", job_description: "(summarize in 30-40 words)"
```

Data:
{records_text}
"""

print("Sending prompt to Ollama...")
response_text = get_completion(final_prompt)

if response_text is not None and response_text.strip():
    print("\n--- RESULTS ---")
    print(response_text)
    print("---------------------\n")
else:
    print("No response received or empty response returned. Check your Ollama connection and model availability.")  
