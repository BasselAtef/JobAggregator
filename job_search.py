import argparse
import json
import os
import re
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

try:
    from flask_cors import CORS
except ImportError:
    CORS = None

app = Flask(__name__)
if CORS is not None:
    # Needed so a browser-based frontend on a different origin/port can call this API.
    CORS(app)
else:
    print("Warning: flask-cors not installed. A browser frontend on another origin "
          "will be blocked by CORS. Run: pip install flask-cors")

# Default path; can be overridden per-run via --file/env var/request param.
DEFAULT_FILE_PATH = os.environ.get("JOB_SEARCH_FILE_PATH", "ai_jobs.xlsx")


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

def get_ollama_completion(prompt, model="qwen3.5:4b", max_tokens=4096):
    """Query local Ollama model."""
    if ollama is None:
        raise ImportError("ollama python package is not installed.")
    client = ollama.Client(timeout=600)
    response = client.chat(
        model=model,
        think=False,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0, "num_predict": max_tokens}
    )
    return response.message.content

def get_groq_completion(prompt, api_key=None, model="openai/gpt-oss-20b", max_tokens=4096):
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
            max_tokens=max_tokens,
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
        "temperature": 0,
        "max_tokens": max_tokens,

    }
    
    res = requests.post(url, headers=headers, json=payload, timeout=60)
    if res.status_code != 200:
        err_msg = f"Groq API Error ({res.status_code}): {res.text}"
        print(err_msg)
        raise RuntimeError(err_msg)
    
    data = res.json()
    return data["choices"][0]["message"]["content"]

def get_completion(prompt, provider="groq", model=None, groq_api_key=None, max_tokens=4096):
    """Router for LLM completion providers (groq / ollama)."""
    provider_clean = (provider or "groq").lower()
    if provider_clean == "groq":
        model_name = model or "openai/gpt-oss-20b"
        return get_groq_completion(prompt, api_key=groq_api_key, model=model_name, max_tokens=max_tokens)
    else:
        model_name = model or "qwen3.5:4b"
        return get_ollama_completion(prompt, model=model_name, max_tokens=max_tokens)

def _extract_json_array(raw_response):
    """Strip markdown code fences, then pull out the JSON array."""
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw_response.strip(), flags=re.MULTILINE)
    match = re.search(r'\[.*\]', cleaned, re.DOTALL)
    if not match:
        return None, "No JSON array found in response (it may have been cut off before the closing ']')."
    try:
        return json.loads(match.group(0)), None
    except json.JSONDecodeError as e:
        return None, f"Response looks truncated or malformed JSON: {e}"

def run_job_search(file_path=DEFAULT_FILE_PATH, provider="groq", model=None, groq_api_key=None, max_tokens=4096, query=None):
    records_text = load_data(file_path)

    if not records_text:
        return {"error": f"Failed to load Excel data from '{file_path}'"}

    query_line = f'Display jobs according to query: "{query}".' if query else 'Display jobs according to user query.'

    final_prompt = f"""
You are a helpful assistant. Below is data from an uploaded xlsx file.

SKIP any job that has the word "Senior".
SKIP any job that has with "description": "NaN".
{query_line}
Return ONLY a valid JSON array containing up to 10 job objects with exact keys:
"title", "company", "fetched_at", "url", "description"

Data:
{records_text}
"""

    print(f"Sending prompt using provider: '{provider}'...")
    try:
        raw_response = get_completion(final_prompt, provider=provider, model=model,
                                       groq_api_key=groq_api_key, max_tokens=max_tokens)
    except Exception as e:
        print(f"Error during completion: {e}")
        return {"error": str(e)}

    if not raw_response or not raw_response.strip():
        return {"error": f"No response received from {provider}."}

    parsed_data, err = _extract_json_array(raw_response)
    if parsed_data is not None:
        return parsed_data

    print(f"JSON parsing warning: {err}")
    return {"error": err, "raw_response": raw_response}

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

# Flask API endpoint — usable by both n8n and a browser-based frontend
@app.route('/run-search', methods=['GET', 'POST'])
def api_run_search():
    provider = request.args.get('provider') or request.headers.get('X-LLM-Provider')
    model = request.args.get('model')
    groq_api_key = request.args.get('groq_api_key') or request.headers.get('X-Groq-Api-Key')
    req_file_path = request.args.get('file_path')
    max_tokens = request.args.get('max_tokens', type=int)
    search_query = request.args.get('query') or request.args.get('search_query')

    if request.is_json and request.json:
        data = request.json
        provider = data.get('provider') or provider
        model = data.get('model') or model
        groq_api_key = data.get('groq_api_key') or groq_api_key
        req_file_path = data.get('file_path') or req_file_path
        max_tokens = data.get('max_tokens') or max_tokens
        search_query = data.get('query') or data.get('search_query') or search_query

    provider = (provider or "groq").lower()
    file_path = req_file_path or DEFAULT_FILE_PATH
    max_tokens = max_tokens or 4096

    results = run_job_search(file_path=file_path, provider=provider, model=model,
                              groq_api_key=groq_api_key, max_tokens=max_tokens, query=search_query)
    return jsonify(results)


@app.route('/providers', methods=['GET'])
def api_providers():
    """Lets a frontend populate a provider dropdown dynamically."""
    return jsonify({
        "providers": [
            {"id": "groq", "label": "Groq", "default_model": "openai/gpt-oss-20b"},
            {"id": "ollama", "label": "Ollama (local)", "default_model": "qwen3.5:4b"},
        ]
    })

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job Search & n8n Integration Script")
    parser.add_argument("--server", action="store_true", help="Run as Flask API server on port 5000")
    parser.add_argument("--webhook", type=str, help="n8n Webhook URL to push results to")
    parser.add_argument("--provider", type=str, choices=["ollama", "groq"], default="groq", help="LLM Provider: groq or ollama")
    parser.add_argument("--model", type=str, help="Model name (e.g., qwen3.5:4b for Ollama, openai/gpt-oss-20b for Groq)")
    parser.add_argument("--groq-key", type=str, help="Groq API key (optional if GROQ_API_KEY environment variable is set)")
    parser.add_argument("--file", type=str, default=DEFAULT_FILE_PATH, help="Path to the ai_jobs.xlsx file")
    parser.add_argument("--max-tokens", type=int, default=4096, help="Max tokens for the LLM response (raise this if output gets cut off)")
    parser.add_argument("--query", type=str, help="Custom job search query (e.g. 'Python developer')")
    args = parser.parse_args()

    if args.server:
        print("Starting Flask API server on http://0.0.0.0:5000 (reachable as host.docker.internal:5000 from Docker containers) ...")
        app.run(host="0.0.0.0", port=5000, debug=False)
    else:
        results = run_job_search(file_path=args.file, provider=args.provider, model=args.model,
                                  groq_api_key=args.groq_key, max_tokens=args.max_tokens, query=args.query)
        print("\n--- RESULTS ---")
        print(json.dumps(results, indent=2))
        print("---------------------\n")

        if args.webhook:
            push_to_n8n(results, args.webhook)
