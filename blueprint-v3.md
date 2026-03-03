# IT Portal Automation — Blueprint v3

## Notice Extraction, OCR Parsing & Consolidated Dashboard

*Updated March 2026 — Incorporates Winman CA-ERP learnings + Notice OCR pipeline*

---

## 1. Winman Learnings Adopted

| Winman Pattern | Our Adoption | Why |
|---|---|---|
| Headed browser mode | Playwright in headed mode for login + navigation | Avoids anti-bot detection; Winman never uses headless |
| Portal export buttons first | Excel Download on e-Proceedings as primary extraction | Officially supported, less likely to break on portal updates |
| Credential + session persistence | Save session cookies/tokens after login, reuse for API calls | Winman stores credentials locally; we store session tokens |
| Sequential UI with user pauses | Navigate → pause for OTP/CAPTCHA → resume pattern | Proven reliable; no CAPTCHA-solving attempts |
| Visible browser | User sees the browser working (builds trust + avoids detection) | Winman's core principle — never hide the automation |

### Where We Go Beyond Winman

Winman focuses on **filing** (ITR, 3CD, audit reports). It does NOT extract Outstanding Demands, e-Proceedings notices, or Worklist items. Our project fills this gap — proactive monitoring across multiple PANs with notice parsing and change detection.

---

## 2. Architecture — The Full Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│                    EXTRACTION PIPELINE                       │
│                                                             │
│  ┌──────────┐   ┌──────────────┐   ┌───────────────────┐   │
│  │ Playwright│──▶│ Excel Export  │──▶│ Parse Excel into  │   │
│  │ Login +   │   │ (Portal's    │   │ structured JSON    │   │
│  │ Navigate  │   │  Download)   │   │ Sort by AY         │   │
│  └──────────┘   └──────────────┘   └───────┬───────────┘   │
│                                             │               │
│                                             ▼               │
│                  ┌──────────────────────────────────────┐   │
│                  │ For each row in Excel:               │   │
│                  │   1. Click "View Notice" link        │   │
│                  │   2. Download Notice PDF             │   │
│                  │   3. Run RapidOCR on PDF             │   │
│                  │   4. Extract structured fields       │   │
│                  │   5. Store in DB (SQLite/Postgres)   │   │
│                  └──────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ OUTPUTS:                                             │   │
│  │  • Master Excel (sorted by AY, all PANs)            │   │
│  │  • Notice PDFs (organized: /PAN/AY/notice_xxx.pdf)  │   │
│  │  • Parsed notice data in DB                          │   │
│  │  • Diff report (what changed since last run)         │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Detailed Extraction Flow

### Phase 1: Login & Navigate (Headed Playwright)

```
Step 1: Launch Chromium in headed mode (user-visible)
Step 2: Navigate to incometax.gov.in
Step 3: Enter PAN/password from credential store
Step 4: ⏸️ PAUSE — Wait for user to enter OTP/CAPTCHA
Step 5: Detect successful login (dashboard URL or element)
Step 6: Save session cookies to cookie jar
Step 7: If CA account → use "Of Other PAN/TAN" to switch to client PAN
```

### Phase 2: Excel Export as Starting Point

The key Winman-inspired insight — **use the portal's own export before intercepting APIs**.

```
For e-Proceedings:
  Step 1: Navigate to e-Proceedings tab
  Step 2: Click "Excel Download" button (portal's built-in feature)
  Step 3: Wait for download to complete
  Step 4: Parse downloaded Excel file

For Outstanding Demands:
  Step 1: Navigate to Outstanding Demands section
  Step 2: Check if Excel/CSV export exists
  Step 3: If yes → download and parse
  Step 4: If no → fall back to API interception or DOM scraping
```

**Why Excel-first is better:**

- Portal-sanctioned feature (not scraping)
- Structured data out of the box
- Less likely to break on UI changes
- Contains assessment year, notice type, date, status in columns
- Acts as the **index/manifest** for notice downloads

### Phase 3: Notice PDF Download

The Excel gives us the manifest. Now we download each actual notice PDF.

```
For each row in the Excel:
  Step 1: Extract notice reference / proceeding ID
  Step 2: Navigate to notice detail page (or use API endpoint with saved cookies)
  Step 3: Click "Download Notice" / "View Notice" button
  Step 4: Save PDF to organized folder structure:
          /data/{PAN}/{AY}/{notice_type}_{section}_{date}_{ref_id}.pdf
  Step 5: Log download status (success/fail/already-exists)
  Step 6: Throttle — 2-3 second delay between downloads
  Step 7: Deduplicate using SHA256 file hash
```

**Folder Structure:**

```
/data/
  ABCDE1234F/
    AY2024-25/
      scrutiny_notice_143_2024-01-15_REF001.pdf
      response_due_2024-02-15_REF002.pdf
    AY2023-24/
      demand_notice_156_2023-08-10_REF003.pdf
  XYZAB5678G/
    AY2024-25/
      ...
```

### Phase 4: OCR Parsing with RapidOCR

**Why RapidOCR:**

- Open-source, no API costs (critical for bulk processing)
- Python-native, runs locally
- Handles scanned PDFs and image-based notices
- Lighter than Tesseract, faster inference
- Works offline — no tax notice data leaves your machine

**OCR Pipeline:**

```python
from rapidocr_onnxruntime import RapidOCR
import fitz  # PyMuPDF for PDF → images

ocr = RapidOCR()

def process_notice(pdf_path):
    # 1. Convert PDF pages to images
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        pix = page.get_pixmap(dpi=300)  # High DPI for better OCR
        img = pix.tobytes("png")
        pages.append(img)

    # 2. Run OCR on each page
    full_text = ""
    for img in pages:
        result, _ = ocr(img)
        if result:
            page_text = "\n".join([line[1] for line in result])
            full_text += page_text + "\n---PAGE BREAK---\n"

    # 3. Extract structured fields
    parsed = extract_notice_fields(full_text)
    return parsed
```

### Phase 5: Structured Field Extraction (Post-OCR)

**Hybrid Approach (Recommended):**

1. **First pass — Regex (free, fast):**

```
Fields to extract:
  - Notice u/s (section number): regex for "section \d+"
  - Assessment Year: regex for "AY \d{4}-\d{2}" or "Assessment Year"
  - Date of Issue: date patterns (DD/MM/YYYY, DD-MM-YYYY)
  - Due Date for Response: date patterns
  - Demand Amount: currency patterns (Rs. / INR / ₹)
  - AO Details: officer name, jurisdiction, ward/circle
  - Key paragraphs: reason for notice, additions proposed
```

2. **If confidence low (missing fields, ambiguous) — Claude API fallback:**

```
Send OCR text to Claude API with structured extraction prompt:
  "Extract the following fields from this income tax notice:
   - Section under which notice is issued
   - Assessment Year
   - Date of issue
   - Response due date
   - Demand amount (if any)
   - AO name and jurisdiction
   - Summary of key issues raised (2-3 lines)
   Return as JSON."
```

3. **Always store both** raw OCR text AND structured fields
4. This keeps ~80% of processing local/free while maintaining ~95% accuracy

---

## 4. Data Storage Schema

### SQLite (Phase 1) → PostgreSQL (Phase 2)

```sql
-- Master client table
CREATE TABLE clients (
    pan TEXT PRIMARY KEY,
    name TEXT,
    ca_pan TEXT,
    last_synced DATETIME
);

-- Excel export data (the manifest)
CREATE TABLE proceedings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pan TEXT,
    assessment_year TEXT,         -- "2024-25"
    notice_type TEXT,             -- "scrutiny", "demand", "rectification"
    section TEXT,                 -- "143(2)", "156", "154"
    date_of_issue DATE,
    response_due_date DATE,
    status TEXT,                  -- "pending", "responded", "closed"
    portal_ref_id TEXT,           -- Reference from portal
    excel_row_data JSON,          -- Raw Excel row as JSON backup
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (pan) REFERENCES clients(pan)
);

-- Downloaded notice PDFs
CREATE TABLE notice_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proceeding_id INTEGER,
    file_path TEXT,               -- Local path to PDF
    file_hash TEXT,               -- SHA256 for dedup/change detection
    download_status TEXT,         -- "success", "failed", "pending"
    downloaded_at DATETIME,
    FOREIGN KEY (proceeding_id) REFERENCES proceedings(id)
);

-- OCR-parsed notice content
CREATE TABLE notice_parsed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_file_id INTEGER,
    raw_ocr_text TEXT,            -- Full OCR output (always stored)
    section TEXT,                 -- Extracted section
    assessment_year TEXT,         -- Extracted AY
    date_of_issue DATE,
    response_due_date DATE,
    demand_amount DECIMAL,
    ao_name TEXT,
    ao_jurisdiction TEXT,
    key_issues TEXT,              -- Summary of issues raised
    extraction_method TEXT,       -- "regex", "llm", "hybrid"
    confidence_score REAL,        -- 0.0 to 1.0
    parsed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (notice_file_id) REFERENCES notice_files(id)
);

-- Outstanding demands (separate pipeline)
CREATE TABLE demands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pan TEXT,
    assessment_year TEXT,
    section TEXT,
    demand_amount DECIMAL,
    interest_amount DECIMAL,
    total_amount DECIMAL,
    ao_name TEXT,
    ao_jurisdiction TEXT,
    status TEXT,
    last_checked DATETIME,
    FOREIGN KEY (pan) REFERENCES clients(pan)
);

-- Change tracking / audit log
CREATE TABLE sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pan TEXT,
    sync_type TEXT,               -- "excel_export", "notice_download", "ocr_parse"
    records_found INTEGER,
    records_new INTEGER,
    records_changed INTEGER,
    errors TEXT,
    started_at DATETIME,
    completed_at DATETIME
);
```

---

## 5. Master Excel Output

### Sorted by Assessment Year — Five Sheets

**Sheet 1: "All Proceedings"** (sorted by AY descending)

| PAN | Client Name | AY | Section | Notice Type | Date Issued | Due Date | Amount | Status | Key Issues | PDF Path |
|-----|-------------|-----|---------|-------------|-------------|----------|--------|--------|------------|----------|

**Sheet 2: "By Client"** (grouped by PAN, then AY within each)

**Sheet 3: "Upcoming Deadlines"** (sorted by due date ascending, only open items)

| Due Date | Days Left | PAN | Client | AY | Section | Action Required |
|----------|-----------|-----|--------|----|---------|-----------------|

**Sheet 4: "Outstanding Demands"** (sorted by total amount descending)

| PAN | Client | AY | Section | Demand | Interest | Total | AO | Status |
|-----|--------|----|---------|--------|----------|-------|----|--------|

**Sheet 5: "Sync Log"** (last sync per PAN, sorted by date descending)

---

## 6. Implementation Plan — Week by Week

### Week 1-2: Foundation (Login & Session)

- Set up Playwright headed browser
- Build login flow with OTP pause mechanism
- Test PAN switching ("Of Other PAN/TAN")
- Save session cookies to reusable cookie jar
- **Gate:** Can you log in and navigate reliably?

### Week 3-4: Excel Export Pipeline

- Navigate to e-Proceedings → click Excel Download
- Parse Excel with openpyxl/pandas
- Sort all rows by Assessment Year
- Store rows in SQLite `proceedings` table
- Build diff detection (compare with previous export)
- **Gate:** Can you get a clean, sorted manifest?

### Week 5-6: Notice PDF Download

- From Excel manifest, iterate each proceeding
- Navigate to notice detail page or use API with saved cookies
- Download PDF, handle portal's download mechanism
- Organize into `/PAN/AY/` folder structure
- Deduplicate using SHA256 file hash
- Handle failures with retry queue
- **Gate:** Do you have all PDFs organized correctly?

### Week 7-8: OCR + Field Extraction

- Install RapidOCR + PyMuPDF
- Convert PDFs to images at 300 DPI
- Run OCR → get raw text per page
- Build regex extractors for common notice patterns:
  - Section numbers, AY, dates, amounts, AO details
- Test on 20-30 real notices, measure field accuracy
- Add Claude API fallback for low-confidence extractions
- Store parsed data in `notice_parsed` table
- **Gate:** Are extracted fields accurate for 90%+ of notices?

### Week 9-10: Outstanding Demands + Consolidation

- Navigate to Outstanding Demands section
- Extract demand details (API intercept or DOM scrape)
- Parse interest computation, AO jurisdiction details
- Build consolidated Master Excel generator (all 5 sheets)
- **Gate:** Does the Master Excel give a complete picture?

### Week 11-12: Multi-PAN Loop + Scheduling

- Loop entire pipeline across multiple client PANs
- Sequential processing (one PAN at a time, throttled)
- Schedule via cron or n8n workflow
- Build change alert system (email/Slack on new notices)
- Error handling, retry logic, comprehensive logging
- **Gate:** Does it run unattended (except OTP) for 50+ PANs?

---

## 7. Key Decisions

### Decision 1: OCR Engine

| Option | Cost | Accuracy | Speed | Privacy | Verdict |
|--------|------|----------|-------|---------|---------|
| RapidOCR | Free | 85-90% | Fast | Fully local | Best default |
| Tesseract | Free | 75-85% | Slow | Fully local | Fallback |
| Google Vision API | ~₹125/1000 pages | 95%+ | Fast | Cloud | If accuracy critical |
| Claude Vision | ~₹1/page | 95%+ | Medium | Cloud API | Best for structured extraction |
| **RapidOCR + Claude (hybrid)** | **Minimal** | **~95%** | **Good** | **Mostly local** | **✅ Recommended** |

### Decision 2: Storage

| Phase | Storage | Why |
|-------|---------|-----|
| Phase 1 (1-50 PANs) | SQLite | Zero setup, single file, portable |
| Phase 2 (50-500 PANs) | PostgreSQL | Concurrent access, better querying |
| Always | File system for PDFs | PDFs stay as files; DB stores metadata + parsed text |

### Decision 3: Orchestration

| Component | Tool | Why |
|-----------|------|-----|
| Login, navigation, PDF download, OCR | Custom Python script | Full control, error handling, Playwright integration |
| Scheduling, triggers, alerts, Excel generation | n8n workflow | Visual, easy scheduling, webhook triggers, email/Slack |

---

## 8. Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Portal UI changes break Excel export | Medium | Monitor for changes; fall back to API interception |
| Notice PDFs are image-scanned (poor OCR) | High | 300 DPI conversion; Claude API fallback for hard cases |
| OTP required for every PAN switch | High | Test if session persists across switches; batch per session |
| Rate limiting / IP blocking | High | 2-3 sec delays; headed mode; residential proxy if needed |
| IT Act 2025 portal redesign (Apr 2026) | High | Modular architecture; isolate portal-interaction layer |
| Duplicate notice downloads | Low | SHA256 hash dedup before storing |
| OCR misreads demand amounts | Critical | Always store raw PDF; manual verification for amounts > ₹10L |
| Cookie/session expiry mid-run | Medium | Session health check before each PAN; auto re-login if expired |

---

## 9. Tech Stack

```
Login & Navigation:    Playwright (Python) — headed mode
Excel Parsing:         openpyxl / pandas
PDF Download:          Playwright download handling + requests with saved cookies
PDF → Image:           PyMuPDF (fitz) at 300 DPI
OCR Engine:            RapidOCR (onnxruntime backend)
Field Extraction:      Regex (primary) + Claude API (fallback)
Database:              SQLite (Phase 1) → PostgreSQL (Phase 2)
Excel Generation:      openpyxl (Master Excel, 5 sheets)
Orchestration:         n8n (scheduling, alerts, notifications)
File Storage:          Local filesystem (/PAN/AY/ structure)
Change Detection:      SHA256 hash comparison + row-level diff
Alerts:                n8n → Email / Slack / WhatsApp
```

---

## 10. Core Principle

> **Excel Export First, API Intercept Second.**

Always prefer the portal's own export features over API interception or DOM scraping.

1. **Sanctioned by the portal** — you're using a button they built for users
2. **Structured data** — no parsing HTML or intercepting JSON
3. **Survives UI redesigns** — export functionality rarely changes even when UI does
4. **Audit-friendly** — you downloaded the same file any user could
5. **Less detection risk** — normal user behavior pattern

API interception is the **fallback** for data that has no export button (demand interest calculations, AO jurisdiction details, notice PDFs themselves).

---

## 11. Quick Reference — File & Folder Map

```
project/
├── src/
│   ├── login.py              # Playwright login + OTP pause
│   ├── navigator.py          # PAN switching, page navigation
│   ├── excel_exporter.py     # Download + parse portal Excel
│   ├── pdf_downloader.py     # Notice PDF download + organize
│   ├── ocr_parser.py         # RapidOCR + field extraction
│   ├── llm_extractor.py      # Claude API fallback extraction
│   ├── db.py                 # SQLite/Postgres operations
│   ├── excel_generator.py    # Master Excel output (5 sheets)
│   └── diff_detector.py      # Change detection between runs
├── data/
│   └── {PAN}/
│       └── {AY}/
│           └── {notice_type}_{section}_{date}_{ref}.pdf
├── exports/
│   └── master_output_{date}.xlsx
├── config/
│   ├── clients.json          # PAN list + client names
│   └── settings.json         # Delays, paths, API keys
├── db/
│   └── portal.db             # SQLite database
└── logs/
    └── sync_{date}.log
```
