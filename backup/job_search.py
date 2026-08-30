import argparse
import json
import os
import re
import sys
import pandas as pd
import requests
from flask import Flask, jsonify, request

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    load_dotenv = None

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    import ollama
except ImportError:
    ollama = None

app = Flask(__name__)

file_path = "C:/Users/PC/n8n-output/ai_jobs.xlsx"


def load_data(file_path="ai_jobs.xlsx"):
    """Load spreadsheet and prepare lightweight text prompt."""
    try:
        if os.path.exists(file_path):
            data = pd.read_excel(file_path)
        elif os.path.exists("ai_jobs.xlsx"):
            print(f"Specified path '{file_path}' not found. Fallback to local 'ai_jobs.xlsx'...")
            data = pd.read_excel("ai_jobs.xlsx")
        else:
            data = pd.read_excel(file_path)
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        return None

    # Grab last 20 rows and truncate descriptions
    last_batch = data.tail(20).copy()
    if "description" in last_batch.columns:
        last_batch["description"] = last_batch["description"].astype(str).str[:150] + "..."

    cols = [c for c in ["title", "company", "fetched_at", "url", "description"] if c in last_batch.columns]
    return last_batch[cols].to_string(index=False)

records_text = load_data(file_path)

def get_ollama_completion(prompt, model="qwen3.5:4b"):
    """Query local Ollama model."""
    if ollama is None:
        raise ImportError("ollama python package is not installed.")
    client = ollama.Client(timeout=600)
    response = client.chat(
        model=model,
        think=False,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0}
    )
    return response.message.content

def get_groq_completion(prompt, api_key=None, model="openai/gpt-oss-20b"):
    """Query Groq API endpoint using Groq SDK or REST API fallback."""
    if load_dotenv:
        load_dotenv()
    key = api_key or os.environ.get("GROQ_API_KEY")
    if not key:
        raise ValueError("Groq API Key is missing. Set GROQ_API_KEY environment variable or pass --groq-key.")
    
    target_model = model or "openai/gpt-oss-20b"
    print(f"Sending request to Groq API (model: {target_model})...")

    if Groq is not None:
        client = Groq(api_key=key)
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=target_model,
            temperature=0,
        )
        return response.choices[0].message.content

    # Fallback HTTP REST request
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": target_model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0
    }
    
    res = requests.post(url, headers=headers, json=payload, timeout=60)
    if res.status_code != 200:
        err_msg = f"Groq API Error ({res.status_code}): {res.text}"
        print(err_msg)
        raise RuntimeError(err_msg)
    
    data = res.json()
    return data["choices"][0]["message"]["content"]

def get_completion(prompt, provider="groq", model=None, groq_api_key=None):
    """Router for LLM completion providers (groq / ollama)."""
    provider_clean = (provider or "groq").lower()
    if provider_clean == "groq":
        model_name = model or "openai/gpt-oss-20b"
        return get_groq_completion(prompt, api_key=groq_api_key, model=model_name)
    else:
        model_name = model or "qwen3.5:4b"
        return get_ollama_completion(prompt, model=model_name)

def run_job_search(file_path, provider="groq", model=None, groq_api_key=None):
    final_prompt = f"""
You are a helpful assistant. Below is data from an uploaded xlsx file.

SKIP any job that has the word "Senior".
SKIP any job that has with "description": "NaN".
Display jobs according to user query.
Return ONLY a valid JSON array containing up to 10 job objects with exact keys:
"title", "company", "fetched_at", "url", "description"

Data:
{records_text}
"""
    
    if not records_text:
        return {"error": "Failed to load Excel data"}

    
    print(f"Sending prompt using provider: '{provider}'...")
    try:
        raw_response = get_completion(final_prompt, provider=provider, model=model, groq_api_key=groq_api_key)
    except Exception as e:
        print(f"Error during completion: {e}")
        return {"error": str(e)}

    if not raw_response or not raw_response.strip():
        return {"error": f"No response received from {provider}."}

    # Attempt to extract JSON from response
    try:
        json_match = re.search(r'\[.*\]', raw_response, re.DOTALL)
        if json_match:
            parsed_data = json.loads(json_match.group(0))
            return parsed_data
    except Exception as e:
        print(f"JSON parsing warning: {e}")

    return {"raw_response": raw_response}

def run_job_search_groq(final_prompt, file_path=file_path, model="openai/gpt-oss-20b", groq_api_key=None):
    records_text = load_data(file_path)
    final_prompt = f"""
You are a helpful assistant. Below is data from an uploaded xlsx file.

SKIP any job that has the word "Senior".
SKIP any job that has with "description": "NaN".
Return ONLY a valid JSON array containing up to 10 job objects with exact keys:
"title", "company", "fetched_at", "url", "description"

Data:
{records_text}
"""
    return run_job_search(file_path=file_path, provider="groq", model=model, groq_api_key=groq_api_key)

def push_to_n8n(data, webhook_url):
    """POST results to n8n webhook."""
    print(f"Sending results to n8n webhook: {webhook_url}")
    try:
        res = requests.post(webhook_url, json=data, timeout=120)
        print(f"n8n Response [{res.status_code}]: {res.text}")
        return res.status_code == 200
    except Exception as e:
        print(f"Failed to post to n8n webhook: {e}")
        return False

# Flask API Endpoint for n8n HTTP Request node
@app.route('/run-search', methods=['GET', 'POST'])
def api_run_search():
    provider = request.args.get('provider') or request.headers.get('X-LLM-Provider')
    model = request.args.get('model')
    groq_api_key = request.args.get('groq_api_key') or request.headers.get('X-Groq-Api-Key')

    if request.is_json and request.json:
        data = request.json
        provider = data.get('provider') or provider
        model = data.get('model') or model
        groq_api_key = data.get('groq_api_key') or groq_api_key

    provider = provider or "groq"
    results = run_job_search(provider=provider, file_path=file_path, model=model, groq_api_key=groq_api_key)
    return jsonify(results)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job Search & n8n Integration Script")
    parser.add_argument("--server", action="store_true", help="Run as Flask API server on port 5000")
    parser.add_argument("--webhook", type=str, help="n8n Webhook URL to push results to")
    parser.add_argument("--provider", type=str, choices=["ollama", "groq"], default="groq", help="LLM Provider: groq or ollama")
    parser.add_argument("--model", type=str, help="Model name (e.g., qwen3.5:4b for Ollama, openai/gpt-oss-20b for Groq)")
    parser.add_argument("--groq-key", type=str, help="Groq API key (optional if GROQ_API_KEY environment variable is set)")
    args = parser.parse_args()

    if args.server:
        print("Starting Flask API server on http://0.0.0.0:5000 (reachable as host.docker.internal:5000 from Docker containers) ...")
        app.run(host="0.0.0.0", port=5000, debug=False)
    else:
        results = run_job_search(provider=args.provider, model=args.model, groq_api_key=args.groq_key)
        print("\n--- RESULTS ---")
        print(json.dumps(results, indent=2))
        print("---------------------\n")

        if args.webhook:
            push_to_n8n(results, args.webhook)

