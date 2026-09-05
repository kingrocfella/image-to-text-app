# ScanGenAI API

A FastAPI-based REST API service that provides OCR (Optical Character Recognition) for images, RAG (Retrieval-Augmented Generation) for PDF documents, and Speech-to-Text conversion for audio files. Built with PaddleOCR, LangChain, OpenAI Whisper, Qdrant vector search, and asynchronous job processing via Dramatiq and Redis.

🌐 **Live Demo**: [https://kingsley-api.name.ng/docs](https://kingsley-api.name.ng/docs)

## Features

- 🖼️ **Image to Text Conversion**: Upload images and extract text using advanced OCR
- 📄 **RAG with PDF**: Upload PDFs and query them using Retrieval-Augmented Generation with vector search
- 🎤 **Sound to Text Conversion**: Upload audio files and transcribe them using OpenAI Whisper model
- ⚡ **Background Job Processing**: Heavy tasks processed asynchronously via Dramatiq and Redis
- 🔐 **Authentication System**: JWT-based authentication with PostgreSQL
- 👤 **User Management**: Register, login, email verification, and token refresh
- 🔒 **Protected Routes**: Secure endpoints with token-based authentication
- 🐳 **Docker Support**: Fully containerized with Docker and Docker Compose
- 🔄 **Auto-reload**: Development mode with automatic file watching and reloading
- 📚 **Interactive API Docs**: Swagger UI documentation at `/docs`
- 🎨 **Custom Error Pages**: Beautiful 404 error page for invalid routes
- 🔍 **Vector Search**: Qdrant vector database for semantic search and RAG

## Tech Stack

- **Framework**: FastAPI
- **Database**: PostgreSQL (with SQLAlchemy ORM)
- **Vector Database**: Qdrant
- **Queue/Cache**: Redis with Dramatiq
- **Authentication**: JWT (PyJWT), bcrypt for password hashing
- **OCR Engine**: PaddleOCR
- **Speech-to-Text**: OpenAI Whisper (via Hugging Face Transformers)
- **RAG Framework**: LangChain (with OpenAI embeddings)
- **LLM Options**: Multiple cloud models (OpenAI GPT, Google Gemini, DeepSeek) or Ollama (local LLM)
- **ML Framework**: PyTorch
- **Python Version**: 3.11
- **Container**: Docker & Docker Compose

## Quick Start

### Prerequisites

- Docker and Docker Compose installed
- (Optional) Python 3.11+ for local development

### Running with Docker

1. **Clone the repository** (if applicable):

   ```bash
   git clone https://github.com/leonfrontier/image-to-text-app
   cd image-to-text-app
   ```

2. **Create environment file**:

   ```bash
   cp .env.example .env
   ```

3. **Start the service**:

   ```bash
   docker-compose up --build
   ```

4. **Access the API**:

   - API Base URL: `http://localhost:8000`
   - API Documentation: `http://localhost:8000/docs`
   - Health Check: `http://localhost:8000/health`
   - PostgreSQL: `postgresql://localhost:5432`
   - Qdrant Dashboard: `http://localhost:6333/dashboard`
   - Ollama API: `http://localhost:11434`
   - Redis: `redis://localhost:6379`

5. **Configure environment variables**:
   - Make sure you update your .env with your own values.

## Project Structure

```
image-to-text-app/
├── app/
│   ├── __init__.py              # App package initialization
│   ├── main.py                  # FastAPI application entry point
│   ├── database/
│   │   ├── __init__.py          # Database module exports
│   │   ├── postgres.py          # PostgreSQL connection and configuration
│   │   ├── postgres_models.py   # Database models (User, TokenBlacklist, PDFRequest)
│   │   └── redis.py             # Redis broker and results backend configuration
│   ├── dependencies/
│   │   ├── __init__.py          # Dependencies module exports
│   │   └── dependencies.py      # FastAPI dependencies for authentication
│   ├── middleware/
│   │   ├── __init__.py          # Middleware module exports
│   │   └── logging_middleware.py  # Request/response logging middleware
│   ├── queues/
│   │   ├── __init__.py          # Queue module exports
│   │   ├── job_queue.py         # Dramatiq actors and job enqueuing functions
│   │   └── job_status.py        # Unified job status checking utilities
│   ├── workers/
│   │   ├── __init__.py          # Workers module exports
│   │   ├── image_worker.py      # Image-to-text processing worker
│   │   ├── sound_worker.py      # Sound-to-text processing worker (Whisper)
│   │   └── rag_worker.py        # RAG PDF processing worker
│   ├── schemas/
│   │   ├── __init__.py          # Schemas module exports
│   │   ├── schemas.py           # Pydantic models for API responses
│   │   └── auth_schemas.py      # Authentication request/response models
│   ├── utils/
│   │   ├── __init__.py          # Utils module exports
│   │   ├── auth_utils.py        # Authentication utilities (JWT, password hashing)
│   │   ├── file_utils.py        # File utilities (temp file cleanup)
│   │   ├── logger.py            # Logging configuration
│   │   ├── utils.py             # Utility functions for image/audio validation
│   │   ├── rag_cloudmodel_response.py  # RAG utilities for cloud models
│   │   ├── rag_ollama_response.py      # RAG utilities for Ollama integration
│   │   ├── rag_vectorstore.py          # Vector store utilities (load/process PDFs)
│   │   └── constants.py                # Model constants and configuration
│   ├── routes/
│   │   ├── __init__.py          # Router initialization
│   │   ├── health.py            # Health check endpoint
│   │   ├── auth.py              # Authentication endpoints
│   │   ├── image_to_text.py     # Image to text conversion endpoint
│   │   ├── sound_to_text.py     # Sound to text conversion endpoint
│   │   ├── rag_with_pdf.py      # RAG with PDF endpoint
│   │   └── jobs.py              # Unified job status endpoint
│   └── templates/
│       └── NotFound.html        # 404 error page
├── tests/
│   ├── __init__.py              # Tests package initialization
│   ├── conftest.py              # Pytest fixtures and configuration
│   ├── test_auth.py             # Authentication route tests
│   ├── test_image_to_text.py    # Image conversion route tests
│   ├── test_sound_to_text.py    # Sound to text conversion route tests
│   ├── test_rag_with_pdf.py     # RAG with PDF route tests
│   └── test_jobs.py             # Unified job status endpoint tests
├── logs/                        # Application logs directory
├── Dockerfile                   # Docker image configuration
├── docker-compose.yml           # Docker Compose configuration (development)
├── docker-compose.prod.yml      # Docker Compose configuration (production)
├── requirements.txt             # Python dependencies
├── pytest.ini                   # Pytest configuration
├── pyproject.toml               # Python project configuration
├── pyrightconfig.json           # Pyright type checker configuration
├── .gitignore                   # Git ignore rules
└── README.md                    # This file
```

## Docker Configuration

### Development Mode

The `docker-compose.yml` includes:

- PostgreSQL database service with health checks
- Qdrant vector database service with health checks
- Redis service for job queuing and results storage
- Ollama service for local LLM inference
- Web service with hot-reload for development
- Worker service for background job processing (Dramatiq)
- Volume mounting for hot-reload during development
- Shared volume for temporary files between web and worker services
- Environment variable loading from `.env` file
- Persistent data volumes for PostgreSQL, Qdrant, Redis, and Ollama

### Production Mode

The `docker-compose.prod.yml` includes:

- PostgreSQL database service
- Qdrant vector database service
- Redis service for job queuing and results storage
- Ollama service for local LLM inference
- Web service without hot-reload
- Worker service for background job processing (Dramatiq)
- Persistent data volumes for all services
- Services wait for dependencies to be healthy before starting


## Error Handling

- **400 Bad Request**: Invalid model, missing required parameters, or validation errors
- **403 Forbidden**: Unauthorized access or unverified email
- **404 Not Found**: Custom HTML error page for invalid routes or request ID not found
- **500 Internal Server Error**: Standard FastAPI error responses

## Dependencies

Key dependencies include:

- `fastapi` - Web framework
- `sqlalchemy` - SQL toolkit and ORM
- `asyncpg` - Async PostgreSQL driver
- `PyJWT` - JWT token handling
- `passlib[bcrypt]` - Password hashing
- `python-jose` - JWT encoding/decoding
- `email-validator` - Email validation
- `paddleocr` - OCR engine
- `transformers` - Hugging Face Transformers library (for Whisper model)
- `torch` - PyTorch for ML models
- `librosa` - Audio analysis library
- `soundfile` - Audio file I/O library
- `numpy` - Numerical computing library
- `uvicorn` - ASGI server
- `pydantic` - Data validation
- `langchain` - RAG framework
- `langchain-openai` - OpenAI embeddings integration
- `langchain-qdrant` - Qdrant vector store integration
- `qdrant-client` - Qdrant Python client
- `ollama` - Ollama Python client for local LLM inference
- `pypdf` - PDF processing library
- `openai` - OpenAI Python client (for GPT, Gemini, and DeepSeek models)
- `dramatiq[redis]` - Background task queue
- `redis` - Redis Python client

See `requirements.txt` for the complete list.

## Authentication

### Token Usage

1. After login, you receive both `access_token` and `refresh_token`
2. Use `access_token` in the `Authorization` header for protected routes:
   ```
   Authorization: Bearer <access_token>
   ```
3. When the access token expires, use `/auth/refresh` to get a new access token
4. Call `/auth/logout` to invalidate tokens when logging out

### Protected Routes

- `/convert/image/text` - Requires valid access token and verified email
- `/convert/sound/text` - Requires valid access token and verified email
- `/pdf/get/response` - Requires valid access token and verified email
- `/job/{message_id}` - Requires valid access token and verified email
- `/auth/refresh` - Requires valid refresh token
- `/auth/logout` - Requires valid access token

## Testing

The project includes comprehensive tests using `pytest` and `pytest-asyncio` for async testing.

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/test_auth.py

# Run with verbose output
pytest -v
```

### Test Structure

- `tests/conftest.py` - Pytest fixtures and configuration
- `tests/test_auth.py` - Authentication route tests
- `tests/test_image_to_text.py` - Image conversion route tests
- `tests/test_sound_to_text.py` - Sound to text conversion route tests
- `tests/test_rag_with_pdf.py` - RAG with PDF route tests
- `tests/test_jobs.py` - Unified job status endpoint tests

### Test Coverage

Tests cover:

- User registration and authentication
- Token refresh and logout
- Protected route access
- Image-to-text job queueing
- Sound-to-text job queueing
- RAG with PDF job queueing
- Unified job status checking
- Error handling

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test your changes
5. Submit a pull request
