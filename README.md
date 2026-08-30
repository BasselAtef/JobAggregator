# AI Job Search

A small Python/Flask service that reads job listings from an Excel file (`ai_jobs.xlsx`, e.g. exported from an n8n scraping workflow), sends them to an LLM (Groq or a local Ollama model), and returns a filtered, structured JSON list of jobs — optionally narrowed down by a free-text search query.

It can run as:
- a one-off **CLI script**, printing (and optionally forwarding to n8n) the results, or
- a **Flask API server** (`/run-search`) that a frontend (`index.html`, a browser app, n8n's HTTP Request node, etc.) can call.

## Features

- Reads the last 20 rows of an `.xlsx` file and hands them to an LLM with instructions to:
  - skip jobs with "Senior" in the title,
  - skip jobs with a missing/`NaN` description,
  - optionally only return jobs relevant to a given **search query**,
  - return a clean JSON array of `title`, `company`, `fetched_at`, `url`, `description`.
    
`NOTE: You can modify the output of the LLM according to your own preference.`

- Supports two LLM providers, switchable per request:
  - **Groq** (cloud, via the `groq` SDK or a REST fallback)
  - **Ollama** (local model, via the `ollama` Python client)
- CORS-enabled so a browser frontend on a different port/origin can call it directly.
- Optional push of results to an **n8n webhook**.


## Requirements

```bash
pip install pandas requests flask flask-cors openpyxl
pip install groq          # only needed if you use the Groq provider
pip install ollama        # only needed if you use the local Ollama provider
pip install python-dotenv # optional, lets you use a .env file for GROQ_API_KEY
```

`openpyxl` is required by pandas to read `.xlsx` files.

If you plan to use Ollama, install and run it separately ([ollama.com](https://ollama.com)) and make sure the model you reference (e.g. `qwen3.5:4b`) has been pulled: `ollama pull qwen3.5:4b`.

## Configuration

| Setting | How to set it | Default |
|---|---|---|
| Excel file path | `JOB_SEARCH_FILE_PATH` env var, `--file` CLI flag, or `file_path` in the API request | `ai_jobs.xlsx` |
| Groq API key | `GROQ_API_KEY` env var (or a `.env` file), `--groq-key` CLI flag, or `groq_api_key` in the API request | — |


## Usage

### CLI

```bash
# Basic run with Groq (default provider)
python job_search.py --file ai_jobs.xlsx

# Use local Ollama instead
python job_search.py --provider ollama --model qwen3.5:4b

# Only return jobs matching a query
python job_search.py --search-query "automation testing"

# Raise the token limit if results are getting cut off
python job_search.py --max-tokens 8000

# Push results to an n8n webhook after running
python job_search.py --webhook https://your-n8n-instance/webhook/xxxx
```

Full flag list:

```
--server            Run as a Flask API server on port 5000
--webhook URL        n8n webhook URL to push results to
--provider           "groq" (default) or "ollama"
--model              Model name (e.g. qwen3.5:4b for Ollama, openai/gpt-oss-20b for Groq)
--groq-key           Groq API key (optional if GROQ_API_KEY is set)
--file               Path to the ai_jobs.xlsx file
--max-tokens         Max tokens for the LLM response (default 4096)
--search-query       Only show jobs relevant to this search query
```

### Server mode

```bash
python job_search.py --server
# -> Starting Flask API server on http://0.0.0.0:5000
```

#### `POST /run-search`

Accepts either query-string parameters or a JSON body.

```javascript
fetch("http://localhost:5000/run-search", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    provider: "groq",              // or "ollama"
    model: "openai/gpt-oss-20b",   // optional, provider-specific default used otherwise
    searchQuery: "automation testing",
    max_tokens: 4096,              // optional
    file_path: "ai_jobs.xlsx"      // optional
  })
})
  .then(res => res.json())
  .then(jobs => console.log(jobs));
```

**Important:** the `Content-Type: application/json` header is required — without it, Flask won't parse the JSON body and the request will silently fall back to defaults (e.g. provider `groq` even if you meant to send `ollama`).

Response is either:
- a JSON array of job objects, or
- `{ "error": "...", "raw_response": "..." }` if the LLM's output couldn't be parsed (e.g. it got cut off — try raising `max_tokens`).

#### `GET /providers`

Returns the available providers and their default models, useful for populating a dropdown in a frontend:

```json
{
  "providers": [
    { "id": "groq", "label": "Groq", "default_model": "openai/gpt-oss-20b" },
    { "id": "ollama", "label": "Ollama (local)", "default_model": "qwen3.5:4b" }
  ]
}
```

## Troubleshooting

- **Results get cut off / JSON parse errors:** raise `--max-tokens` (CLI) or `max_tokens` (API request). The default is 4096; more jobs with longer descriptions need more.
- **"Not enough Groq credits" while trying to use Ollama:** the request is still resolving to the Groq provider. Check that:
  1. Your fetch call sends `Content-Type: application/json`.
  2. The key in your request body is exactly `provider` (not `llmProvider` or similar).
  3. The value is `"ollama"`.
  
  The server logs the resolved provider on every `/run-search` call — check your terminal output to confirm what was actually received.
- **CORS errors in the browser console:** make sure `flask-cors` is installed (`pip install flask-cors`); the server prints a warning on startup if it's missing.
- **`Failed to load Excel data`:** confirm the file path is correct and the file has `title`/`company`/`fetched_at`/`url`/`description` columns (any missing columns are simply omitted).

## Notes

- The LLM-based search-query filtering is not a strict keyword match — it depends on the model's judgment, so results can vary between providers/models, especially for short or vague queries.
- `debug=False` is set on the Flask server intentionally; don't flip this on in anything reachable outside your own machine.
