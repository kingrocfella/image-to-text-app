"""Utility functions for RAG vectorstore operations."""

import asyncio
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Tuple

from fastapi import HTTPException, UploadFile, status
from langchain_community.document_loaders import PyPDFLoader
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.database import PDFRequest, User
from app.utils.file_utils import delete_temp_file
from app.utils.logger import logger
from app.utils.upload_security import PDF_MAX_BYTES, validate_pdf_content

QDRANT_URL = os.getenv("QDRANT_URL")
RAG_RETENTION_DAYS = int(os.getenv("RAG_RETENTION_DAYS", "30"))
if RAG_RETENTION_DAYS <= 0:
    raise RuntimeError("RAG_RETENTION_DAYS must be positive")


async def delete_vector_collections(collection_names: list[str]) -> None:
    """Delete Qdrant collections before their ownership records are removed."""
    if not collection_names:
        return
    if not QDRANT_URL:
        raise RuntimeError("QDRANT_URL is required to delete stored document data")

    def _delete() -> None:
        client = QdrantClient(url=QDRANT_URL)
        try:
            for collection_name in collection_names:
                client.delete_collection(collection_name=collection_name)
        finally:
            client.close()

    await asyncio.to_thread(_delete)


async def delete_user_pdf_data(user_id: uuid.UUID, db: AsyncSession) -> None:
    """Permanently remove one user's stored document embeddings and metadata."""
    result = await db.execute(
        select(PDFRequest.collection_name).where(PDFRequest.user_id == user_id)
    )
    collection_names = [str(name) for name in result.scalars().all()]
    await delete_vector_collections(collection_names)
    await db.execute(delete(PDFRequest).where(PDFRequest.user_id == user_id))


async def purge_expired_pdf_data(db: AsyncSession) -> int:
    """Remove document embeddings older than the configured retention window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RAG_RETENTION_DAYS)
    result = await db.execute(select(PDFRequest).where(PDFRequest.created_at < cutoff))
    expired = list(result.scalars().all())
    await delete_vector_collections(
        [str(pdf_request.collection_name) for pdf_request in expired]
    )
    if expired:
        await db.execute(
            delete(PDFRequest).where(PDFRequest.id.in_([item.id for item in expired]))
        )
        await db.commit()
    return len(expired)


async def load_existing_vectorstore(
    past_request_id: str,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> Tuple[QdrantVectorStore, str]:
    """Load an existing vectorstore from a past request_id.

    Args:
        past_request_id: The request_id from a previous PDF upload
        user_id: The user ID to verify access
        db: Database session

    Returns:
        Tuple of (vectorstore, request_id)

    Raises:
        HTTPException: If request_id not found or user doesn't have access
    """
    # Look up the collection_name from database
    result = await db.execute(
        select(PDFRequest).where(
            PDFRequest.request_id == past_request_id,
            PDFRequest.user_id == user_id,
        )
    )
    pdf_request = result.scalar_one_or_none()

    if not pdf_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request ID '{past_request_id}' not found or you don't have access to it.",
        )

    collection_name = str(pdf_request.collection_name)
    logger.info("Loading vectorstore for collection: %s", collection_name)

    # Load existing vectorstore
    embeddings = OpenAIEmbeddings(model="text-embedding-3-large")
    qdrant_client = QdrantClient(url=QDRANT_URL)
    vectorstore = QdrantVectorStore(
        collection_name=collection_name,
        embedding=embeddings,
        client=qdrant_client,
    )

    return vectorstore, past_request_id


async def process_new_pdf(
    pdf: UploadFile,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> Tuple[QdrantVectorStore, str, str]:
    """Process a new PDF upload and create vectorstore.

    Args:
        pdf: The uploaded PDF file
        user_id: The user ID for database storage
        db: Database session

    Returns:
        Tuple of (vectorstore, request_id, tmp_file_path)

    Raises:
        HTTPException: If PDF is invalid or processing fails
    """

    pdf_content_type = (pdf.content_type or "").lower()
    pdf_filename = (pdf.filename or "").lower()

    if pdf_content_type != "application/pdf" and not pdf_filename.endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type. Please upload a PDF file.",
        )

    current_request_id = str(uuid.uuid4())
    tmp_file_path: str | None = None

    try:
        suffix = Path(pdf.filename).suffix if pdf.filename else ""
        content = await pdf.read()
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The uploaded PDF file is empty.",
            )
        # Re-enforce the size and content bounds at the processing sink, not just
        # at the upload route, so any caller of process_new_pdf stays protected.
        if len(content) > PDF_MAX_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="The uploaded PDF file is too large.",
            )
        await asyncio.to_thread(validate_pdf_content, content)
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            tmp_file.write(content)
            tmp_file_path = tmp_file.name

        # Load the PDF file
        loader = PyPDFLoader(tmp_file_path)
        documents = await asyncio.to_thread(loader.load)

        if not documents:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unable to extract content from the uploaded PDF.",
            )

        # Check if we got actual content
        if not any(doc.page_content.strip() for doc in documents):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unable to extract text from the PDF. The PDF may be image-based (scanned) or contain no text. Please ensure the PDF contains selectable text.",
            )

        logger.info("Extracted %s pages from PDF", len(documents))

        # Split the documents into chunks
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000, chunk_overlap=200, add_start_index=True
        )
        split_docs = await asyncio.to_thread(text_splitter.split_documents, documents)

        # Add request_id to metadata for each document
        for doc in split_docs:
            if doc.metadata is None:
                doc.metadata = {}
            doc.metadata["request_id"] = current_request_id

        # Serialize against account deletion. If deletion won, do not create a
        # vector collection that no account can ever reach or erase.
        user_result = await db.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        if user_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account is no longer active.",
            )

        collection_name = f"pdf_collection_{current_request_id.replace('-', '')}"

        # Create vector embeddings
        embeddings = OpenAIEmbeddings(model="text-embedding-3-large")

        # Create vector store and store embeddings and documents in Qdrant
        vectorstore = await asyncio.to_thread(
            QdrantVectorStore.from_documents,
            split_docs,
            embeddings,
            url=QDRANT_URL,
            collection_name=collection_name,
        )

        logger.info("Created vectorstore with collection: %s", collection_name)

        # Store request_id to collection_name mapping in database
        pdf_request = PDFRequest(
            request_id=current_request_id,
            collection_name=collection_name,
            user_id=user_id,
            filename=pdf_filename,
        )
        db.add(pdf_request)
        await db.commit()
        await db.refresh(pdf_request)

        return vectorstore, current_request_id, tmp_file_path or ""

    except HTTPException:
        delete_temp_file(tmp_file_path, silent=True)
        raise
    except Exception as exc:
        delete_temp_file(tmp_file_path, silent=True)
        logger.error("Failed to process PDF: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process the PDF. Please try again later.",
        ) from exc
