# ⚡ Autonomous AI Web Crawler & Postman Collection Exporter

An open-source, production-grade Python CLI tool that autonomously crawls any target web application (including authenticated apps), exercises non-destructive UI functionalities across all navigation tabs, intercepts background API calls (XHR, Fetch, and GraphQL), correlates every API request to the active UI tab and action, enriches/renames endpoints using Google Gemini structured outputs, and exports a ready-to-import **Postman Collection v2.1.0** `.json` file organized into folders by Tab.

---

## 🌟 Key Features

- **Autonomous Multi-Tab Exploration:** Discovers all navigation tabs, sidebars, and sub-menus using Playwright.
- **Automated Authentication:** Locates username/email and password fields, submits credentials, verifies authenticated session state, and ensures zero credential leakage.
- **Strict Safety Guardrails:** Blocks destructive UI actions (`delete`, `remove`, `pay`, `checkout`, `cancel subscription`, `logout`, etc.) before click dispatch.
- **Precision Network Sniffer:** Intercepts exclusively API traffic (`xhr` and `fetch`), drops static assets (`.js`, `.css`, images, fonts) and telemetry domains (`google-analytics.com`, `hotjar.com`, etc.).
- **Header & Token Sanitization:** Automatically masks `Authorization`, `Cookie`, and `X-Api-Key` tokens with `{{auth_token}}` / `{{auth_cookie}}`.
- **GraphQL Operation Extraction:** Disambiguates requests sharing the `/graphql` URL path by parsing `operationName` or query syntax.
- **UI Context Correlation:** Stamps every network request with the exact active Tab name, UI action, and timestamp.
- **Smart Deduplication:** Consolidates repeated pagination or polling requests into distinct endpoints with occurrence counts.
- **Gemini Structured Output Enrichment:** Renames endpoints into action-oriented request names, standardizes folder names, and writes concise descriptions using Google Gemini (`gemini-3.8-flash` or configurable).
- **Postman Collection Format v2.1.0 Compliance:** Strictly verified against the official JSON schema (`https://schema.getpostman.com/json/collection/v2.1.0/collection.json`) with `{{baseUrl}}` variable templating.

---

## 🏗️ Architecture

```
                                      ┌──────────────┐
                                      │   main.py    │
                                      │ (CLI Typer)  │
                                      └──────┬───────┘
                                             │
                      ┌──────────────────────┼──────────────────────┐
                      ▼                      ▼                      ▼
               ┌─────────────┐        ┌─────────────┐        ┌─────────────┐
               │  config.py  │        │  models.py  │        │ auth_handler│
               │ (Pydantic)  │        │ (Schemas)   │        │ (Login Flow)│
               └─────────────┘        └─────────────┘        └─────────────┘
                                             │
                                             ▼
                                    ┌─────────────────┐
                                    │  agent_runner   │
                                    │ (Playwright AI) │
                                    └────────┬────────┘
                                             │
                      ┌──────────────────────┴──────────────────────┐
                      ▼                                             ▼
             ┌─────────────────┐                           ┌─────────────────┐
             │ network_sniffer │◄──────────────────────────┤ context_tracker │
             │  (XHR / Fetch)  │                           │  (Tabs/Actions) │
             └────────┬────────┘                           └─────────────────┘
                      │
                      ▼
             ┌─────────────────┐
             │endpoint_process │ (Deduplication + Gemini Structured Output)
             └────────┬────────┘
                      │
                      ▼
             ┌─────────────────┐
             │postman_exporter │ ===> <sanitized_domain>_postman_collection.json
             │  (Schema v2.1)  │
             └─────────────────┘
```

---

## 🚀 Quickstart & Installation

### 1. Prerequisites
- Python 3.11+
- Google Gemini API Key ([Get a free key here](https://aistudio.google.com/app/apikey))

### 2. Setup Virtual Environment & Install Dependencies
```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install requirements
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env` and insert your Gemini API Key:
```bash
cp .env.example .env
```
Edit `.env`:
```ini
GEMINI_API_KEY=AIzaSy...
GEMINI_MODEL=gemini-3.8-flash
DEFAULT_MAX_STEPS=40
HEADLESS=false
```

---

## 💻 CLI Usage

```bash
# View CLI options and help
python main.py --help
```

### Example 1: Crawl Public Web Application (Headed Live View)
```bash
python main.py --url https://opensource-demo.orangehrmlive.com --max-steps 25 --output-dir ./output
```

### Example 2: Crawl Authenticated Application (Headless Mode)
```bash
python main.py \
  --url https://app.example.com/login \
  --username "admin@example.com" \
  --password "SecureP@ssw0rd!" \
  --max-steps 40 \
  --headless \
  --output-dir ./output
```

---

## 🧪 Running Automated Tests

```bash
# Run complete test suite (37 tests across all 5 phases)
pytest -v

# Run individual phase tests
pytest -v tests/test_config.py      # Phase 1 Config
pytest -v tests/test_models.py      # Phase 1 Models
pytest -v tests/test_interceptor.py # Phase 2 Sniffer & Context
pytest -v tests/test_crawler.py     # Phase 3 Agent & Auth
pytest -v tests/test_ai_mapper.py   # Phase 4 Deduplication & Gemini
pytest -v tests/test_exporter.py    # Phase 5 Postman v2.1 Exporter
pytest -v tests/test_e2e.py         # Full End-to-End Pipeline
```
