import os
import sys
import datetime
import asyncio
import re
import polars as pl
from llama_cloud import LlamaCloud
from fastapi.concurrency import run_in_threadpool

from core.config import LLAMA_INDEX_API_KEY
from core.database import col_knowledge, col_jobs
from core.models import embed_model

async def update_job_status(job_id: str, status: str, progress: str = None, error: str = None):
    """Updates the status of a background ingestion job in MongoDB."""
    if not job_id:
        return
    update_data = {
        "status": status,
        "updated_at": datetime.datetime.utcnow()
    }
    if progress is not None:
        update_data["progress"] = progress
    if error is not None:
        update_data["error_message"] = error
        
    await col_jobs.update_one({"_id": job_id}, {"$set": update_data})

async def ingest_pdf(filepath: str, filename: str, job_id: str = None) -> dict:
    """Parses a PDF manual using LlamaCloud, embeds pages, and saves to MongoDB."""
    try:
        await update_job_status(job_id, "processing", "Checking duplicates...")
        
        # 0. Deduplicate: Check if already indexed in local MongoDB
        if await col_knowledge.find_one({"source": filename}):
            msg = f"Skipped: PDF {filename} already indexed in database."
            await update_job_status(job_id, "completed", msg)
            return {"status": "skipped", "message": msg}

        # 1. Deduplicate: Check if file already exists on LlamaCloud
        await update_job_status(job_id, "processing", "Connecting to LlamaCloud...")
        
        if not LLAMA_INDEX_API_KEY:
            raise ValueError("LLAMA_INDEX_API_KEY environment variable is not configured.")
            
        llama_client = LlamaCloud(api_key=LLAMA_INDEX_API_KEY)
        
        existing_files = await run_in_threadpool(llama_client.files.list)
        file_obj = None
        for f in existing_files:
            f_name = getattr(f, 'name', getattr(f, 'file_name', ''))
            if f_name == filename:
                file_obj = f
                print(f"   [o] Reusing existing LlamaCloud file ID: {file_obj.id}")
                break

        if not file_obj:
            await update_job_status(job_id, "processing", "Uploading PDF to LlamaCloud...")
            file_obj = await run_in_threadpool(llama_client.files.create, file=filepath, purpose="parse")
        
        await update_job_status(job_id, "processing", "LlamaCloud parsing PDF (this can take 1-2 minutes)...")
        result = await run_in_threadpool(
            llama_client.parsing.parse,
            file_id=file_obj.id,
            tier="agentic",
            version="latest",
            expand=["markdown"]
        )
        
        upload_count = 0
        batch_docs = []
        
        # Step 1: Gather plain text documents from pages
        await update_job_status(job_id, "processing", "Extracting parsed pages...")
        for i, page in enumerate(result.markdown.pages):
            text_content = page.markdown
            if not text_content or not text_content.strip(): 
                continue
            
            batch_docs.append({
                "text": text_content,
                "source": filename,
                "page_number": i + 1,
                "doc_type": "repair_manual"
            })

        # Step 2: Batch Encode and Bulk Insert in chunks of 4 to avoid memory exhaustion
        if batch_docs:
            total_pages = len(batch_docs)
            sub_batch_size = 4
            for idx in range(0, total_pages, sub_batch_size):
                sub_batch = batch_docs[idx : idx + sub_batch_size]
                processed = min(idx + sub_batch_size, total_pages)
                await update_job_status(job_id, "processing", f"Vectorizing pages ({processed}/{total_pages})...")
                
                texts = [doc["text"] for doc in sub_batch]
                vectors = await run_in_threadpool(embed_model.encode, texts)
                vectors = vectors.tolist()
                
                for doc, vector in zip(sub_batch, vectors):
                    doc["embedding"] = vector
                    
                await col_knowledge.insert_many(sub_batch)
                upload_count += len(sub_batch)
                await asyncio.sleep(0.05)

        msg = f"Completed: Successfully indexed {upload_count} pages."
        await update_job_status(job_id, "completed", msg)
        return {"status": "success", "message": msg, "count": upload_count}
    except Exception as e:
        err_msg = str(e)
        await update_job_status(job_id, "failed", error=err_msg)
        raise e

async def ingest_csv(filepath: str, filename: str, job_id: str = None) -> dict:
    """Parses a CSV database using Pandas/Polars, embeds rows in batches, and saves to MongoDB."""
    try:
        await update_job_status(job_id, "processing", "Checking duplicates...")
        
        # 0. Deduplicate: Check if already indexed in local MongoDB
        if await col_knowledge.find_one({"source": filename}):
            msg = f"Skipped: CSV {filename} already indexed in database."
            await update_job_status(job_id, "completed", msg)
            return {"status": "skipped", "message": msg}

        await update_job_status(job_id, "processing", "Reading CSV data...")
        df = await run_in_threadpool(lambda: pl.read_csv(filepath, null_values=[""]).fill_null("")) 
        
        upload_count = 0
        batch_docs = []
        batch_size = 4
        total_rows = len(df)
        
        for index, row in enumerate(df.iter_rows(named=True)):
            row_text = ", ".join([f"{col}: {val}" for col, val in row.items() if val != ""])
            batch_docs.append({
                "text": row_text,
                "source": filename,
                "row_number": index + 1,
                "doc_type": "dtc_database" 
            })
            
            if len(batch_docs) >= batch_size:
                processed = index + 1
                await update_job_status(job_id, "processing", f"Vectorizing CSV rows ({processed}/{total_rows})...")
                texts = [doc["text"] for doc in batch_docs]
                vectors = await run_in_threadpool(embed_model.encode, texts)
                vectors = vectors.tolist()
                
                for doc, vector in zip(batch_docs, vectors):
                    doc["embedding"] = vector
                    
                await col_knowledge.insert_many(batch_docs)
                upload_count += len(batch_docs)
                batch_docs = []
                await asyncio.sleep(0.05)
                
        if batch_docs:
            await update_job_status(job_id, "processing", f"Vectorizing remaining CSV rows ({total_rows}/{total_rows})...")
            texts = [doc["text"] for doc in batch_docs]
            vectors = await run_in_threadpool(embed_model.encode, texts)
            vectors = vectors.tolist()
            for doc, vector in zip(batch_docs, vectors): 
                doc["embedding"] = vector
            await col_knowledge.insert_many(batch_docs)
            upload_count += len(batch_docs)
            
        msg = f"Completed: Successfully indexed {upload_count} rows."
        await update_job_status(job_id, "completed", msg)
        return {"status": "success", "message": msg, "count": upload_count}
    except Exception as e:
        err_msg = str(e)
        await update_job_status(job_id, "failed", error=err_msg)
        raise e

async def ingest_text(filepath: str, filename: str, job_id: str = None) -> dict:
    """Parses a text file, chunks it appropriately, embeds, and saves to MongoDB."""
    try:
        await update_job_status(job_id, "processing", "Checking duplicates...")
        
        if await col_knowledge.find_one({"source": filename}):
            msg = f"Skipped: Text file {filename} already indexed in database."
            await update_job_status(job_id, "completed", msg)
            return {"status": "skipped", "message": msg}

        await update_job_status(job_id, "processing", "Reading text data...")
        content = await run_in_threadpool(lambda: open(filepath, 'r', encoding='utf-8').read())
            
        # ------------------------------------------------------------
        # Determine chunking strategy based on filename
        # ------------------------------------------------------------
        is_dtc_file = "dtc" in filename.lower()

        if is_dtc_file:
            # Split DTC file by detecting the start of each DTC entry
            # Pattern: newline (or start) followed by a DTC code and " is an OBD-II"
            entry_starts = re.split(r'(?=\n[PBCU][0-9A-F]{4} is an OBD-II)', content)
            # The first part may be empty or contain a header before the first code
            chunks = []
            for part in entry_starts:
                part = part.strip()
                if not part:
                    continue
                # Keep only the first 2000 characters for embedding
                chunks.append(part[:2000])
        else:
            # For other text files (e.g. owner's manual), split into paragraphs
            paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
            # Optionally merge small paragraphs to create larger "page" chunks
            merged = []
            current = ""
            for para in paragraphs:
                if len(current) + len(para) < 1500:
                    current += para + "\n\n"
                else:
                    if current:
                        merged.append(current.strip())
                    current = para + "\n\n"
            if current:
                merged.append(current.strip())
            chunks = merged if merged else paragraphs  # fallback to individual paragraphs

        total_chunks = len(chunks)
        upload_count = 0
        batch_docs = []
        batch_size = 4

        for index, chunk_text in enumerate(chunks):
            batch_docs.append({
                "text": chunk_text,
                "source": filename,
                "chunk_number": index + 1,
                "doc_type": "dtc_entry" if is_dtc_file else "text_document"
            })

            if len(batch_docs) >= batch_size:
                processed = index + 1
                await update_job_status(job_id, "processing", f"Vectorizing text chunks ({processed}/{total_chunks})...")
                texts = [doc["text"] for doc in batch_docs]
                vectors = await run_in_threadpool(embed_model.encode, texts)
                vectors = vectors.tolist()

                for doc, vector in zip(batch_docs, vectors):
                    doc["embedding"] = vector

                await col_knowledge.insert_many(batch_docs)
                upload_count += len(batch_docs)
                batch_docs = []
                await asyncio.sleep(0.05)

        # Process remaining batch
        if batch_docs:
            await update_job_status(job_id, "processing", f"Vectorizing remaining text chunks ({total_chunks}/{total_chunks})...")
            texts = [doc["text"] for doc in batch_docs]
            vectors = await run_in_threadpool(embed_model.encode, texts)
            vectors = vectors.tolist()
            for doc, vector in zip(batch_docs, vectors):
                doc["embedding"] = vector
            await col_knowledge.insert_many(batch_docs)
            upload_count += len(batch_docs)

        msg = f"Completed: Successfully indexed {upload_count} chunks."
        await update_job_status(job_id, "completed", msg)
        return {"status": "success", "message": msg, "count": upload_count}

    except Exception as e:
        err_msg = str(e)
        await update_job_status(job_id, "failed", error=err_msg)
        raise e
