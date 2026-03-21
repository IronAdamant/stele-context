"""Search quality evaluation suite for Stele.

Creates a synthetic but realistic corpus of code and text documents,
defines an eval set of queries with expected relevant documents, and
measures recall@5 and recall@10 for each.

Focuses on known weak spots: stemming, CamelCase splitting, cross-concept
matching, exact keywords, and mixed natural-language + code queries.

Usage:
    python benchmarks/eval_search_quality.py
"""

import os
import sys
import tempfile
from typing import Dict, List, Set, Tuple

# ---------------------------------------------------------------------------
# Corpus: 55 synthetic documents spanning Python, JS, config, docs
# ---------------------------------------------------------------------------

CORPUS: Dict[str, str] = {}

# --- Python code files ---

CORPUS["auth_service.py"] = """\
import hashlib
import secrets

class AuthenticationService:
    \"\"\"Handles user authentication, login, and session tokens.\"\"\"

    def __init__(self, db):
        self.db = db
        self.active_sessions = {}

    def authenticate(self, username, password):
        \"\"\"Authenticate a user with username and password.\"\"\"
        user = self.db.find_user(username)
        if user is None:
            return None
        hashed = hashlib.sha256(password.encode()).hexdigest()
        if hashed != user.password_hash:
            return None
        token = secrets.token_hex(32)
        self.active_sessions[token] = username
        return token

    def verify_token(self, token):
        return self.active_sessions.get(token)

    def logout(self, token):
        self.active_sessions.pop(token, None)
"""

CORPUS["db_connection.py"] = """\
import sqlite3

class DatabaseConnection:
    \"\"\"Manages SQLite database connections and query execution.\"\"\"

    def __init__(self, path):
        self.path = path
        self.conn = None

    def connect(self):
        \"\"\"Open a connection to the database.\"\"\"
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        return self.conn

    def disconnect(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def execute_query(self, sql, params=None):
        cursor = self.conn.cursor()
        cursor.execute(sql, params or ())
        return cursor.fetchall()

    def execute_write(self, sql, params=None):
        cursor = self.conn.cursor()
        cursor.execute(sql, params or ())
        self.conn.commit()
        return cursor.lastrowid
"""

CORPUS["cache_manager.py"] = """\
import time
from collections import OrderedDict

class LRUCache:
    \"\"\"Least-recently-used cache with TTL expiration.\"\"\"

    def __init__(self, max_size=1000, ttl=300):
        self.max_size = max_size
        self.ttl = ttl
        self._store = OrderedDict()
        self._timestamps = {}

    def get(self, key):
        if key not in self._store:
            return None
        if time.time() - self._timestamps[key] > self.ttl:
            self.invalidate(key)
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def set(self, key, value):
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = value
        self._timestamps[key] = time.time()
        while len(self._store) > self.max_size:
            oldest = next(iter(self._store))
            self.invalidate(oldest)

    def invalidate(self, key):
        self._store.pop(key, None)
        self._timestamps.pop(key, None)

    def clear_all(self):
        self._store.clear()
        self._timestamps.clear()
"""

CORPUS["http_server.py"] = """\
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

class RequestHandler(BaseHTTPRequestHandler):
    \"\"\"HTTP request handler with JSON API support.\"\"\"

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok"})
        elif self.path == "/api/users":
            self._respond(200, {"users": []})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length))
        self._respond(201, {"received": body})

    def _respond(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

def start_server(host="0.0.0.0", port=8080):
    server = HTTPServer((host, port), RequestHandler)
    server.serve_forever()
"""

CORPUS["file_parser.py"] = """\
import csv
import json
import os

def parse_csv(filepath):
    \"\"\"Parse a CSV file and return rows as list of dicts.\"\"\"
    with open(filepath) as f:
        reader = csv.DictReader(f)
        return list(reader)

def parse_json(filepath):
    \"\"\"Parse a JSON file and return the data.\"\"\"
    with open(filepath) as f:
        return json.load(f)

def detect_file_type(filepath):
    \"\"\"Detect file type from extension.\"\"\"
    ext = os.path.splitext(filepath)[1].lower()
    return {".csv": "csv", ".json": "json", ".txt": "text"}.get(ext, "unknown")

def read_lines(filepath):
    with open(filepath) as f:
        return f.readlines()
"""

CORPUS["error_handling.py"] = """\
import logging
import traceback

logger = logging.getLogger(__name__)

class AppError(Exception):
    \"\"\"Base application error with error code and message.\"\"\"
    def __init__(self, message, code=500):
        super().__init__(message)
        self.code = code
        self.message = message

class NotFoundError(AppError):
    def __init__(self, resource):
        super().__init__(f"{resource} not found", code=404)

class ValidationError(AppError):
    def __init__(self, field, reason):
        super().__init__(f"Validation failed for {field}: {reason}", code=400)

def handle_error(error):
    \"\"\"Log error and return a structured error response dict.\"\"\"
    logger.error("Error occurred: %s", error)
    logger.debug("Traceback: %s", traceback.format_exc())
    if isinstance(error, AppError):
        return {"error": error.message, "code": error.code}
    return {"error": "Internal server error", "code": 500}
"""

CORPUS["user_model.py"] = """\
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class User:
    username: str
    email: str
    password_hash: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    is_active: bool = True
    role: str = "user"

    def has_permission(self, permission):
        role_permissions = {
            "admin": {"read", "write", "delete", "manage_users"},
            "editor": {"read", "write"},
            "user": {"read"},
        }
        return permission in role_permissions.get(self.role, set())

@dataclass
class UserProfile:
    user: User
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
"""

CORPUS["test_auth.py"] = """\
import unittest
from unittest.mock import MagicMock

class TestAuthenticationService(unittest.TestCase):

    def setUp(self):
        self.mock_db = MagicMock()
        # from auth_service import AuthenticationService
        # self.service = AuthenticationService(self.mock_db)

    def test_successful_login(self):
        \"\"\"Users with correct credentials should get a session token.\"\"\"
        pass

    def test_failed_login_wrong_password(self):
        \"\"\"Wrong password returns None.\"\"\"
        pass

    def test_verify_valid_token(self):
        \"\"\"Valid tokens return the associated username.\"\"\"
        pass

    def test_logout_invalidates_token(self):
        \"\"\"After logout, token verification should fail.\"\"\"
        pass
"""

CORPUS["data_pipeline.py"] = """\
import os
from typing import List, Dict

def extract_records(source_path: str) -> List[Dict]:
    \"\"\"Extract raw records from source file.\"\"\"
    records = []
    with open(source_path) as f:
        for line in f:
            parts = line.strip().split(",")
            if len(parts) >= 3:
                records.append({"id": parts[0], "name": parts[1], "value": parts[2]})
    return records

def transform_records(records: List[Dict]) -> List[Dict]:
    \"\"\"Clean and normalize extracted records.\"\"\"
    return [
        {**r, "name": r["name"].strip().lower(), "value": float(r["value"])}
        for r in records
        if r["value"].strip()
    ]

def load_records(records: List[Dict], dest_path: str) -> int:
    \"\"\"Load transformed records into destination.\"\"\"
    count = 0
    with open(dest_path, "a") as f:
        for r in records:
            f.write(f"{r['id']},{r['name']},{r['value']}\\n")
            count += 1
    return count
"""

CORPUS["string_utils.py"] = """\
import re
import unicodedata

def slugify(text):
    \"\"\"Convert text to URL-friendly slug.\"\"\"
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\\w\\s-]", "", text.lower())
    return re.sub(r"[-\\s]+", "-", text).strip("-")

def camel_to_snake(name):
    \"\"\"Convert CamelCase to snake_case.\"\"\"
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\\1_\\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\\1_\\2", s).lower()

def truncate(text, max_length=100, suffix="..."):
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix

def word_count(text):
    return len(text.split())
"""

CORPUS["rate_limiter.py"] = """\
import time
import threading

class RateLimiter:
    \"\"\"Token bucket rate limiter for API throttling.\"\"\"

    def __init__(self, max_requests=100, window_seconds=60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests = {}
        self._lock = threading.Lock()

    def is_allowed(self, client_id):
        now = time.time()
        with self._lock:
            if client_id not in self._requests:
                self._requests[client_id] = []
            timestamps = self._requests[client_id]
            timestamps[:] = [t for t in timestamps if now - t < self.window]
            if len(timestamps) >= self.max_requests:
                return False
            timestamps.append(now)
            return True

    def reset(self, client_id):
        with self._lock:
            self._requests.pop(client_id, None)
"""

# --- JavaScript files ---

CORPUS["loginHandler.js"] = """\
const bcrypt = require('bcrypt');
const jwt = require('jsonwebtoken');

class LoginHandler {
    constructor(userRepository, jwtSecret) {
        this.userRepository = userRepository;
        this.jwtSecret = jwtSecret;
    }

    async handleLogin(req, res) {
        const { username, password } = req.body;
        const user = await this.userRepository.findByUsername(username);
        if (!user) {
            return res.status(401).json({ error: 'Invalid credentials' });
        }
        const isValid = await bcrypt.compare(password, user.passwordHash);
        if (!isValid) {
            return res.status(401).json({ error: 'Invalid credentials' });
        }
        const token = jwt.sign({ userId: user.id }, this.jwtSecret, { expiresIn: '24h' });
        return res.json({ token, user: { id: user.id, username: user.username } });
    }
}

module.exports = LoginHandler;
"""

CORPUS["apiRouter.js"] = """\
const express = require('express');
const router = express.Router();

router.get('/users', async (req, res) => {
    const users = await req.db.query('SELECT * FROM users');
    res.json(users);
});

router.get('/users/:id', async (req, res) => {
    const user = await req.db.query('SELECT * FROM users WHERE id = ?', [req.params.id]);
    if (!user) return res.status(404).json({ error: 'User not found' });
    res.json(user);
});

router.post('/users', async (req, res) => {
    const { username, email } = req.body;
    const id = await req.db.query('INSERT INTO users (username, email) VALUES (?, ?)', [username, email]);
    res.status(201).json({ id, username, email });
});

router.delete('/users/:id', async (req, res) => {
    await req.db.query('DELETE FROM users WHERE id = ?', [req.params.id]);
    res.status(204).end();
});

module.exports = router;
"""

CORPUS["eventEmitter.js"] = """\
class EventEmitter {
    constructor() {
        this.listeners = new Map();
    }

    on(event, callback) {
        if (!this.listeners.has(event)) {
            this.listeners.set(event, []);
        }
        this.listeners.get(event).push(callback);
        return this;
    }

    emit(event, ...args) {
        const handlers = this.listeners.get(event) || [];
        handlers.forEach(handler => handler(...args));
        return handlers.length > 0;
    }

    off(event, callback) {
        const handlers = this.listeners.get(event);
        if (handlers) {
            const index = handlers.indexOf(callback);
            if (index > -1) handlers.splice(index, 1);
        }
        return this;
    }

    once(event, callback) {
        const wrapper = (...args) => {
            this.off(event, wrapper);
            callback(...args);
        };
        return this.on(event, wrapper);
    }
}

module.exports = EventEmitter;
"""

CORPUS["fetchWrapper.js"] = """\
async function fetchJSON(url, options = {}) {
    const response = await fetch(url, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
    });
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    return response.json();
}

async function postJSON(url, data, options = {}) {
    return fetchJSON(url, { method: 'POST', body: JSON.stringify(data), ...options });
}

async function retryFetch(url, maxRetries = 3, delay = 1000) {
    for (let i = 0; i < maxRetries; i++) {
        try {
            return await fetchJSON(url);
        } catch (err) {
            if (i === maxRetries - 1) throw err;
            await new Promise(r => setTimeout(r, delay * (i + 1)));
        }
    }
}

module.exports = { fetchJSON, postJSON, retryFetch };
"""

# --- Config files ---

CORPUS["config.yaml"] = """\
# Application configuration
app:
  name: MyService
  version: 2.1.0
  environment: production

database:
  host: localhost
  port: 5432
  name: myservice_db
  pool_size: 20
  timeout: 30

redis:
  host: localhost
  port: 6379
  ttl: 3600

logging:
  level: INFO
  format: json
  file: /var/log/myservice.log

api:
  rate_limit: 1000
  cors_origins:
    - https://app.example.com
    - https://admin.example.com
"""

CORPUS["docker-compose.yml"] = """\
version: '3.8'
services:
  app:
    build: .
    ports:
      - "8080:8080"
    environment:
      DATABASE_URL: postgresql://db:5432/myservice
      REDIS_URL: redis://redis:6379
    depends_on:
      - db
      - redis

  db:
    image: postgres:15
    environment:
      POSTGRES_DB: myservice
      POSTGRES_PASSWORD: secret
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

volumes:
  pgdata:
"""

CORPUS["Makefile"] = """\
.PHONY: all test lint format build clean deploy

all: lint test build

test:
\tpytest tests/ -v --cov=src --cov-report=term

lint:
\truff check src/ tests/
\tmypy src/

format:
\truff format src/ tests/

build:
\tdocker build -t myservice:latest .

clean:
\trm -rf dist/ build/ *.egg-info __pycache__
\tfind . -name '*.pyc' -delete

deploy:
\tdocker push myservice:latest
\tkubectl apply -f k8s/
"""

CORPUS["pyproject.toml"] = """\
[project]
name = "myservice"
version = "2.1.0"
description = "A backend API service"
requires-python = ">=3.10"
dependencies = [
    "flask>=3.0",
    "sqlalchemy>=2.0",
    "pydantic>=2.0",
    "redis>=5.0",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-cov", "ruff", "mypy"]

[tool.ruff]
line-length = 88
target-version = "py310"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --strict-markers"
"""

# --- Documentation / text files ---

CORPUS["README_setup.md"] = """\
# Getting Started

## Installation

Install dependencies with pip:

    pip install -e ".[dev]"

## Configuration

Copy `.env.example` to `.env` and fill in your database credentials.
The application reads configuration from environment variables and
falls back to `config.yaml` for defaults.

## Running Locally

    python -m myservice serve --port 8080

The health check endpoint is at `/health`. The API documentation
is available at `/docs` when running in development mode.
"""

CORPUS["README_api.md"] = """\
# API Reference

## Authentication

All API endpoints require a Bearer token in the Authorization header.
Obtain a token by POSTing to `/api/auth/login` with username and password.

## Endpoints

### GET /api/users
Returns a paginated list of users.

### GET /api/users/:id
Returns a single user by ID. Returns 404 if not found.

### POST /api/users
Create a new user. Requires `username` and `email` in the request body.

### DELETE /api/users/:id
Delete a user. Requires admin role.
"""

CORPUS["CONTRIBUTING.md"] = """\
# Contributing Guide

## Development Workflow

1. Fork the repository
2. Create a feature branch from `main`
3. Write tests for your changes
4. Run `make lint` and `make test` to verify
5. Submit a pull request

## Code Style

We use ruff for linting and formatting. Run `make format` before committing.
All functions must have docstrings. Type hints are required for public APIs.

## Testing

Write unit tests for all new functionality. Integration tests go in
`tests/integration/`. Aim for 90% code coverage on new code.
"""

CORPUS["architecture.md"] = """\
# Architecture Overview

## System Components

The application follows a layered architecture:

1. **HTTP Layer** - Request handling, routing, middleware
2. **Service Layer** - Business logic, validation, orchestration
3. **Data Layer** - Database access, caching, external APIs

## Data Flow

Requests come in through the HTTP layer, are validated and routed
to the appropriate service. Services coordinate data access through
repositories and return structured responses.

## Caching Strategy

We use Redis for session storage and response caching. The LRU cache
handles in-memory caching for frequently accessed data. Cache invalidation
uses a pub/sub pattern through Redis channels.
"""

CORPUS["deployment.md"] = """\
# Deployment Guide

## Docker

Build the image with `docker build -t myservice .` and push to
the container registry. The Dockerfile uses multi-stage builds
for a minimal production image.

## Kubernetes

Apply the manifests in `k8s/` directory. The deployment uses
3 replicas with health checks and resource limits configured.

## Environment Variables

- `DATABASE_URL` - PostgreSQL connection string
- `REDIS_URL` - Redis connection string
- `JWT_SECRET` - Secret key for JWT token signing
- `LOG_LEVEL` - Logging verbosity (DEBUG, INFO, WARNING, ERROR)
"""

CORPUS["security.md"] = """\
# Security Practices

## Authentication

User passwords are hashed using bcrypt with a cost factor of 12.
Session tokens are JWT with 24-hour expiry. Refresh tokens are
stored server-side with 30-day expiry.

## Input Validation

All user input is validated using Pydantic models before processing.
SQL queries use parameterized statements to prevent injection.

## Rate Limiting

API endpoints are rate-limited to 1000 requests per minute per client.
Burst allowance is 50 requests. Rate limit headers are included in
every response (X-RateLimit-Remaining, X-RateLimit-Reset).

## CORS

Cross-origin requests are restricted to whitelisted domains defined
in the configuration file.
"""

CORPUS["changelog.md"] = """\
# Changelog

## v2.1.0 (2026-03-15)
- Added rate limiting to all API endpoints
- Improved error handling with structured error responses
- Added Redis caching layer for user queries
- Fixed memory leak in WebSocket connection handler

## v2.0.0 (2026-02-01)
- Migrated from Flask to FastAPI
- Added OpenAPI documentation
- Switched to async database driver
- Breaking: Changed authentication from cookies to JWT
"""

# --- More Python for diversity ---

CORPUS["websocket_handler.py"] = """\
import asyncio
import json

class WebSocketHandler:
    \"\"\"Manages WebSocket connections and message broadcasting.\"\"\"

    def __init__(self):
        self.connections = set()

    async def on_connect(self, websocket):
        self.connections.add(websocket)
        await websocket.send(json.dumps({"type": "connected"}))

    async def on_disconnect(self, websocket):
        self.connections.discard(websocket)

    async def broadcast(self, message):
        \"\"\"Send a message to all connected clients.\"\"\"
        if not self.connections:
            return
        payload = json.dumps(message)
        await asyncio.gather(
            *(ws.send(payload) for ws in self.connections),
            return_exceptions=True,
        )

    async def on_message(self, websocket, raw):
        data = json.loads(raw)
        if data.get("type") == "ping":
            await websocket.send(json.dumps({"type": "pong"}))
        else:
            await self.broadcast(data)
"""

CORPUS["migrations.py"] = """\
\"\"\"Database migration runner with version tracking.\"\"\"

MIGRATIONS = {
    1: [
        "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, email TEXT, password_hash TEXT)",
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, user_id INTEGER, created_at TIMESTAMP)",
    ],
    2: [
        "ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'",
        "ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT 1",
    ],
    3: [
        "CREATE TABLE rate_limits (client_id TEXT PRIMARY KEY, request_count INTEGER, window_start TIMESTAMP)",
        "CREATE INDEX idx_rate_limits_window ON rate_limits(window_start)",
    ],
}

def get_current_version(conn):
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_versions").fetchone()
        return row[0] or 0
    except Exception:
        conn.execute("CREATE TABLE schema_versions (version INTEGER, applied_at TIMESTAMP)")
        return 0

def migrate(conn, target=None):
    current = get_current_version(conn)
    if target is None:
        target = max(MIGRATIONS.keys())
    for version in sorted(MIGRATIONS.keys()):
        if version > current and version <= target:
            for sql in MIGRATIONS[version]:
                conn.execute(sql)
            conn.execute("INSERT INTO schema_versions VALUES (?, datetime('now'))", (version,))
    conn.commit()
"""

CORPUS["task_scheduler.py"] = """\
import heapq
import time
import threading
from typing import Callable

class TaskScheduler:
    \"\"\"Priority-based task scheduler with delayed execution.\"\"\"

    def __init__(self):
        self._queue = []
        self._lock = threading.Lock()
        self._running = False

    def schedule(self, func: Callable, delay: float = 0, priority: int = 0):
        run_at = time.time() + delay
        with self._lock:
            heapq.heappush(self._queue, (run_at, priority, func))

    def run_pending(self):
        now = time.time()
        to_run = []
        with self._lock:
            while self._queue and self._queue[0][0] <= now:
                _, _, func = heapq.heappop(self._queue)
                to_run.append(func)
        for func in to_run:
            func()
        return len(to_run)

    def pending_count(self):
        with self._lock:
            return len(self._queue)
"""

CORPUS["retry_decorator.py"] = """\
import time
import functools
import logging

logger = logging.getLogger(__name__)

def retry(max_attempts=3, delay=1.0, backoff=2.0, exceptions=(Exception,)):
    \"\"\"Decorator that retries a function on failure with exponential backoff.\"\"\"
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts:
                        logger.error("All %d attempts failed for %s", max_attempts, func.__name__)
                        raise
                    logger.warning("Attempt %d/%d failed: %s. Retrying in %.1fs", attempt, max_attempts, e, current_delay)
                    time.sleep(current_delay)
                    current_delay *= backoff
        return wrapper
    return decorator
"""

CORPUS["pagination.py"] = """\
from typing import List, Any, Dict
from dataclasses import dataclass

@dataclass
class Page:
    items: List[Any]
    total: int
    page: int
    per_page: int
    has_next: bool
    has_prev: bool

def paginate(items: List[Any], page: int = 1, per_page: int = 20) -> Page:
    \"\"\"Paginate a list of items.\"\"\"
    total = len(items)
    start = (page - 1) * per_page
    end = start + per_page
    return Page(
        items=items[start:end],
        total=total,
        page=page,
        per_page=per_page,
        has_next=end < total,
        has_prev=page > 1,
    )

def paginate_query(query_func, page=1, per_page=20, **kwargs) -> Dict:
    total = query_func(count=True, **kwargs)
    items = query_func(offset=(page - 1) * per_page, limit=per_page, **kwargs)
    return {"items": items, "total": total, "page": page, "per_page": per_page}
"""

CORPUS["config_loader.py"] = """\
import os
import json
from typing import Any, Dict, Optional

class ConfigLoader:
    \"\"\"Loads configuration from environment variables and JSON files.\"\"\"

    def __init__(self, config_path: Optional[str] = None):
        self._config: Dict[str, Any] = {}
        if config_path and os.path.exists(config_path):
            with open(config_path) as f:
                self._config = json.load(f)

    def get(self, key: str, default: Any = None) -> Any:
        env_key = key.upper().replace(".", "_")
        env_val = os.environ.get(env_key)
        if env_val is not None:
            return env_val
        parts = key.split(".")
        obj = self._config
        for part in parts:
            if isinstance(obj, dict) and part in obj:
                obj = obj[part]
            else:
                return default
        return obj

    def require(self, key: str) -> Any:
        value = self.get(key)
        if value is None:
            raise ValueError(f"Required config key missing: {key}")
        return value
"""

CORPUS["middleware.py"] = """\
import time
import logging

logger = logging.getLogger(__name__)

class LoggingMiddleware:
    \"\"\"Logs request method, path, status code, and response time.\"\"\"

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        start = time.time()
        method = environ.get("REQUEST_METHOD")
        path = environ.get("PATH_INFO")

        def custom_start_response(status, headers, exc_info=None):
            elapsed = (time.time() - start) * 1000
            logger.info("%s %s -> %s (%.1fms)", method, path, status, elapsed)
            return start_response(status, headers, exc_info)

        return self.app(environ, custom_start_response)

class CORSMiddleware:
    \"\"\"Adds CORS headers to responses.\"\"\"

    def __init__(self, app, allowed_origins=None):
        self.app = app
        self.origins = allowed_origins or ["*"]

    def __call__(self, environ, start_response):
        def add_cors_headers(status, headers, exc_info=None):
            headers.append(("Access-Control-Allow-Origin", self.origins[0]))
            headers.append(("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE"))
            headers.append(("Access-Control-Allow-Headers", "Content-Type, Authorization"))
            return start_response(status, headers, exc_info)
        return self.app(environ, add_cors_headers)
"""

CORPUS["validators.py"] = """\
import re
from typing import Optional

def validate_email(email: str) -> bool:
    \"\"\"Validate email format using a simple regex pattern.\"\"\"
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

def validate_password(password: str) -> Optional[str]:
    \"\"\"Check password strength. Returns error message or None.\"\"\"
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if not re.search(r'[A-Z]', password):
        return "Password must contain an uppercase letter"
    if not re.search(r'[0-9]', password):
        return "Password must contain a digit"
    return None

def validate_username(username: str) -> Optional[str]:
    if len(username) < 3:
        return "Username must be at least 3 characters"
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        return "Username can only contain letters, numbers, and underscores"
    return None

def sanitize_html(text: str) -> str:
    return re.sub(r'<[^>]+>', '', text)
"""

# Fillers to push corpus to 55 documents

CORPUS["env.example"] = """\
DATABASE_URL=postgresql://localhost:5432/myservice
REDIS_URL=redis://localhost:6379
JWT_SECRET=change-me-in-production
LOG_LEVEL=INFO
PORT=8080
"""

CORPUS["gitignore.txt"] = """\
__pycache__/
*.pyc
.env
node_modules/
dist/
build/
*.egg-info
.mypy_cache/
.pytest_cache/
.coverage
"""

CORPUS["logger_config.py"] = """\
import logging
import sys

def setup_logging(level=logging.INFO, fmt="json"):
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        formatter = logging.Formatter(
            '{"time": "%(asctime)s", "level": "%(levelname)s", "msg": "%(message)s"}'
        )
    else:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    return root
"""

CORPUS["health_check.py"] = """\
import time

class HealthCheck:
    \"\"\"Service health check with dependency status.\"\"\"

    def __init__(self, db=None, cache=None):
        self.db = db
        self.cache = cache
        self.start_time = time.time()

    def check(self):
        status = {"status": "ok", "uptime": time.time() - self.start_time, "checks": {}}
        if self.db:
            try:
                self.db.execute_query("SELECT 1")
                status["checks"]["database"] = "ok"
            except Exception:
                status["checks"]["database"] = "error"
                status["status"] = "degraded"
        if self.cache:
            try:
                self.cache.set("_health", "1")
                status["checks"]["cache"] = "ok"
            except Exception:
                status["checks"]["cache"] = "error"
                status["status"] = "degraded"
        return status
"""

# ---------------------------------------------------------------------------
# Evaluation queries
# ---------------------------------------------------------------------------

# Each query: (query_string, expected_doc_ids, category, description)
# expected_doc_ids = set of filenames that are relevant

EvalQuery = Tuple[str, Set[str], str, str]

EVAL_QUERIES: List[EvalQuery] = [
    # -- Stemming tests --
    (
        "authentication service",
        {"auth_service.py", "test_auth.py", "security.md", "loginHandler.js"},
        "stemming",
        "'authentication' should match 'authenticate' in auth_service.py",
    ),
    (
        "validating user input",
        {"validators.py", "security.md"},
        "stemming",
        "'validating' should match 'validate_email', 'validate_password', etc.",
    ),
    (
        "scheduling tasks with priority",
        {"task_scheduler.py"},
        "stemming",
        "'scheduling' should match 'schedule' method in TaskScheduler",
    ),
    (
        "caching strategy with expiration",
        {"cache_manager.py", "architecture.md"},
        "stemming",
        "'caching' should match LRU cache code and architecture docs on caching",
    ),
    # -- CamelCase splitting tests --
    (
        "login handler",
        {"loginHandler.js", "auth_service.py"},
        "camelcase",
        "'login handler' should match 'loginHandler.js' and 'LoginHandler' class",
    ),
    (
        "event emitter pattern",
        {"eventEmitter.js"},
        "camelcase",
        "'event emitter' should match 'eventEmitter.js' and 'EventEmitter' class",
    ),
    (
        "websocket handler",
        {"websocket_handler.py"},
        "camelcase",
        "'websocket handler' should match 'WebSocketHandler' class",
    ),
    # -- Cross-concept tests --
    (
        "database connection",
        {"db_connection.py", "config.yaml", "docker-compose.yml"},
        "cross-concept",
        "'database connection' should match code with db.connect and DB config",
    ),
    (
        "retry with exponential backoff",
        {"retry_decorator.py", "fetchWrapper.js"},
        "cross-concept",
        "Should match retry logic in Python decorator and JS fetch wrapper",
    ),
    (
        "user permissions and roles",
        {"user_model.py", "README_api.md"},
        "cross-concept",
        "Should match has_permission/role in user_model and admin role in API docs",
    ),
    (
        "ETL data pipeline",
        {"data_pipeline.py"},
        "cross-concept",
        "Should match extract/transform/load functions despite no literal 'ETL'",
    ),
    # -- Exact keyword tests --
    (
        "LRUCache",
        {"cache_manager.py"},
        "exact",
        "Exact class name should match perfectly",
    ),
    (
        "RateLimiter max_requests",
        {"rate_limiter.py"},
        "exact",
        "Exact class and attribute names should match perfectly",
    ),
    (
        "DatabaseConnection execute_query",
        {"db_connection.py"},
        "exact",
        "Exact class and method name should match",
    ),
    # -- Mixed natural language + code --
    (
        "how to parse CSV and JSON files",
        {"file_parser.py"},
        "mixed",
        "Natural language question about code functionality",
    ),
    (
        "middleware for logging request duration",
        {"middleware.py"},
        "mixed",
        "Natural language describing LoggingMiddleware functionality",
    ),
    (
        "password hashing with bcrypt",
        {"loginHandler.js", "security.md"},
        "mixed",
        "Concept + library name across JS code and security docs",
    ),
    (
        "docker compose postgres redis setup",
        {"docker-compose.yml", "config.yaml"},
        "mixed",
        "DevOps concepts matching infra config files",
    ),
]


# ---------------------------------------------------------------------------
# Evaluation runner
# ---------------------------------------------------------------------------


def _write_corpus(tmpdir: str) -> Dict[str, str]:
    """Write corpus files to tmpdir. Returns {doc_id: abs_path}."""
    paths = {}
    for doc_id, content in CORPUS.items():
        filepath = os.path.join(tmpdir, doc_id)
        with open(filepath, "w") as f:
            f.write(content)
        paths[doc_id] = filepath
    return paths


def _run_eval(engine, path_to_id: Dict[str, str]) -> List[Dict]:
    """Run all eval queries and return per-query results."""
    results = []
    for query, expected, category, description in EVAL_QUERIES:
        search_results = engine.search(query, top_k=10)

        # Map result document_paths back to doc_ids
        retrieved_ids_10: List[str] = []
        for r in search_results:
            doc_path = r["document_path"]
            doc_id = path_to_id.get(doc_path, doc_path)
            if doc_id not in retrieved_ids_10:
                retrieved_ids_10.append(doc_id)

        retrieved_5 = set(retrieved_ids_10[:5])
        retrieved_10 = set(retrieved_ids_10[:10])

        recall_5 = len(expected & retrieved_5) / len(expected) if expected else 0
        recall_10 = len(expected & retrieved_10) / len(expected) if expected else 0

        results.append(
            {
                "query": query,
                "category": category,
                "description": description,
                "expected": expected,
                "retrieved_5": retrieved_5,
                "retrieved_10": retrieved_10,
                "recall_5": recall_5,
                "recall_10": recall_10,
            }
        )

    return results


def _print_results(results: List[Dict]) -> None:
    """Print formatted results table."""
    col_q = 42
    col_cat = 14
    col_r5 = 10
    col_r10 = 10
    col_status = 8

    header = (
        f"  {'Query':<{col_q}s}"
        f"  {'Category':<{col_cat}s}"
        f"  {'R@5':>{col_r5}s}"
        f"  {'R@10':>{col_r10}s}"
        f"  {'Status':>{col_status}s}"
    )
    sep = "  " + "-" * (col_q + col_cat + col_r5 + col_r10 + col_status + 8)

    print(f"\n{'=' * 78}")
    print("  Stele Search Quality Evaluation")
    print(f"{'=' * 78}")
    print(f"\n  Corpus: {len(CORPUS)} documents | Queries: {len(EVAL_QUERIES)}")
    print(f"\n{header}")
    print(sep)

    for r in results:
        q_display = r["query"]
        if len(q_display) > col_q:
            q_display = q_display[: col_q - 3] + "..."

        # PASS if recall@10 >= 0.5 (at least half of expected docs found)
        status = "PASS" if r["recall_10"] >= 0.5 else "FAIL"

        print(
            f"  {q_display:<{col_q}s}"
            f"  {r['category']:<{col_cat}s}"
            f"  {r['recall_5']:>{col_r5}.0%}"
            f"  {r['recall_10']:>{col_r10}.0%}"
            f"  {status:>{col_status}s}"
        )

    print(sep)

    # Per-category averages
    categories = sorted(set(r["category"] for r in results))
    print(
        f"\n  {'Category':<{col_cat + col_q + 2}s}  {'Avg R@5':>10s}  {'Avg R@10':>10s}"
    )
    print(f"  {'-' * (col_cat + col_q + 26)}")
    for cat in categories:
        cat_results = [r for r in results if r["category"] == cat]
        avg_r5 = sum(r["recall_5"] for r in cat_results) / len(cat_results)
        avg_r10 = sum(r["recall_10"] for r in cat_results) / len(cat_results)
        print(f"  {cat:<{col_cat + col_q + 2}s}  {avg_r5:>10.0%}  {avg_r10:>10.0%}")

    # Overall
    avg_r5 = sum(r["recall_5"] for r in results) / len(results)
    avg_r10 = sum(r["recall_10"] for r in results) / len(results)
    pass_count = sum(1 for r in results if r["recall_10"] >= 0.5)
    fail_count = len(results) - pass_count

    print(f"\n  Overall avg recall@5:  {avg_r5:.0%}")
    print(f"  Overall avg recall@10: {avg_r10:.0%}")
    print(
        f"  Passed: {pass_count}/{len(results)}  |  Failed: {fail_count}/{len(results)}"
    )

    # Show details for failures
    failures = [r for r in results if r["recall_10"] < 0.5]
    if failures:
        print("\n  --- Failed query details ---")
        for r in failures:
            expected_str = ", ".join(sorted(r["expected"]))
            got_str = ", ".join(sorted(r["retrieved_10"])) or "(none)"
            print(f"\n  Query: {r['query']}")
            print(f"    Expected:  {expected_str}")
            print(f"    Got (top10): {got_str}")

    print()


def main() -> None:
    from stele.engine import Stele

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write corpus
        id_to_path = _write_corpus(tmpdir)
        path_to_id = {v: k for k, v in id_to_path.items()}

        # Index
        engine = Stele(storage_dir=tmpdir, enable_coordination=False)
        all_paths = list(id_to_path.values())
        engine.index_documents(all_paths)

        # Evaluate
        results = _run_eval(engine, path_to_id)

        # Report
        _print_results(results)

        # Exit code: fail if overall recall@10 < 40%
        avg_r10 = sum(r["recall_10"] for r in results) / len(results)
        if avg_r10 < 0.40:
            print("  OVERALL FAIL: avg recall@10 below 40% threshold")
            sys.exit(1)


if __name__ == "__main__":
    main()
