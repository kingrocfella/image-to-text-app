"""Worker functions for processing RAG PDF jobs."""

import asyncio
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Dict

from fastapi import UploadFile
from langchain_qdrant import QdrantVectorStore
from qdrant_client.models import FieldCondition, Filter, MatchValue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import get_database_url
from app.utils import (
    delete_temp_file,
    get_rag_cloudmodel_response,
    get_rag_ollama_response,
    models_supported,
)
from app.utils.logger import logger
from app.utils.rag_vectorstore import load_existing_vectorstore, process_new_pdf

# Database setup for worker
# Use NullPool because each asyncio.run() creates a new event loop,
# and connection pools don't work well across different event loops
database_url = get_database_url()
engine = create_async_engine(database_url, echo=False, poolclass=NullPool)
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def process_rag_job_async(job_data: Dict[str, Any]) -> Dict[str, Any]:
    """Async implementation of RAG job processing."""
    query = job_data["query"]
    model = job_data["model"]
    user_id = uuid.UUID(job_data["user_id"])
    past_request_id = job_data.get("past_request_id")
    pdf_file_path = job_data.get("pdf_file_path")
    pdf_filename = job_data.get("pdf_filename", "uploaded.pdf")
    logger.info("Processing RAG job")

    # Validate model
    if model not in models_supported:
        raise ValueError(f"Invalid model: {model}")

    # Create database session
    async with AsyncSessionLocal() as db:
        vectorstore: QdrantVectorStore
        current_request_id: str
        tmp_file_path: str | None = None

        # Handle existing request or new PDF
        if past_request_id:
            logger.info("Loading existing vectorstore")
            vectorstore, current_request_id = await load_existing_vectorstore(
                past_request_id, user_id, db
            )
        elif pdf_file_path and Path(pdf_file_path).exists():
            # Create a mock UploadFile-like object for process_new_pdf
            logger.info("Preparing uploaded PDF for processing")
            with open(pdf_file_path, "rb") as f:
                file_content = f.read()

            file_obj = BytesIO(file_content)
            upload_file = UploadFile(
                filename=pdf_filename,
                file=file_obj,
            )

            logger.info("Processing new PDF")

            vectorstore, current_request_id, tmp_file_path = await process_new_pdf(
                upload_file, user_id, db
            )
            logger.info("Successfully processed new PDF")
        else:
            logger.error("RAG job is missing an accessible PDF or request ID")
            raise ValueError("Either past_request_id or pdf_file_path must be provided")

        try:
            # Search for relevant documents
            logger.info(
                "Searching for relevant documents with request_id: %s",
                current_request_id,
            )
            filter_condition = Filter(
                must=[
                    FieldCondition(
                        key="metadata.request_id",
                        match=MatchValue(value=current_request_id),
                    )
                ]
            )

            logger.info("Searching with filter for request_id: %s", current_request_id)

            # Search with filter
            search_results = await asyncio.to_thread(
                vectorstore.similarity_search,
                query,
                k=100,
                filter=filter_condition,
            )

            logger.info(
                "Found %s documents with request_id filter", len(search_results)
            )

            # If no results with filter, try without filter
            if not search_results:
                logger.warning("Filter returned no results, trying without filter")
                all_results = await asyncio.to_thread(
                    vectorstore.similarity_search,
                    query,
                    k=100,
                )
                if all_results:
                    search_results = all_results
                    logger.info(
                        "Found %s documents without filter", len(search_results)
                    )

            relevant_context = (
                "\n\n".join([doc.page_content for doc in search_results])
                if search_results
                else ""
            )

            # Get response from RAG model
            response: str | None = None
            if model == models_supported["ollama"]:
                response = await get_rag_ollama_response(query, relevant_context)
            else:
                response = get_rag_cloudmodel_response(query, relevant_context, model)

            return {
                "content": response,
                "request_id": current_request_id,
            }

        finally:
            delete_temp_file(tmp_file_path)
