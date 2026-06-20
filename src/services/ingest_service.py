import datetime
import re
import polars as pl
from llama_cloud import LlamaCloud
from fastapi.concurrency import run_in_threadpool

from core.config import LLAMA_INDEX_API_KEY
from core.database import col_knowledge, col_jobs
from core.models import embed_model

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
# Number of texts sent to the model in one forward pass.
# 16 is the sweet-spot for harrier-270M on a single vCPU: it amortises the
# attention overhead without blowing the activation memory budget.
EMBED_BATCH = 16

# ---------------------------------------------------------------------------
# Job status helper
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Shared encoding helper
# ---------------------------------------------------------------------------

async def _encode_batch(docs: list) -> list:
    """Encode a list of doc dicts in-place (adds 'embedding' key). Returns docs."""
    texts = [d["text"] for d in docs]
    vectors = await run_in_threadpool(
        embed_model.encode,
        texts,
        convert_to_numpy=True,
        show_progress_bar=False,
        normalize_embeddings=True,   # required for cosine-similarity retrieval
    )
    vectors = vectors.tolist()
    for doc, vec in zip(docs, vectors):
        doc["embedding"] = vec
    return docs

# ---------------------------------------------------------------------------
# PDF ingestion
# ---------------------------------------------------------------------------

async def ingest_pdf(filepath: str, filename: str, job_id: str = None) -> dict:
    """Parses a PDF manual using LlamaCloud, embeds pages, and saves to MongoDB."""
    try:
        await update_job_status(job_id, "processing", "Checking existing index...")

        # --- Checkpoint: count already-indexed pages for this source ---
        already_indexed = await col_knowledge.count_documents({"source": filename})

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

        # Step 1: Gather non-empty pages
        await update_job_status(job_id, "processing", "Extracting parsed pages...")
        all_pages = []
        for i, page in enumerate(result.markdown.pages):
            text_content = page.markdown
            if not text_content or not text_content.strip():
                continue
            all_pages.append({
                "text": text_content,
                "source": filename,
                "page_number": i + 1,
                "doc_type": "repair_manual"
            })

        total_pages = len(all_pages)

        if already_indexed >= total_pages > 0:
            msg = f"Skipped: PDF {filename} already fully indexed ({total_pages} pages)."
            await update_job_status(job_id, "completed", msg)
            return {"status": "skipped", "message": msg}

        if already_indexed > 0:
            await update_job_status(
                job_id, "processing",
                f"Resuming from checkpoint ({already_indexed}/{total_pages} pages already indexed)..."
            )

        # Step 2: Embed in batches of EMBED_BATCH, skip already-indexed pages
        upload_count = 0
        pending: list = []

        for idx, page_doc in enumerate(all_pages):
            if idx < already_indexed:
                continue

            pending.append(page_doc)

            if len(pending) >= EMBED_BATCH:
                processed = already_indexed + upload_count + len(pending)
                await update_job_status(
                    job_id, "processing",
                    f"Vectorizing pages ({processed}/{total_pages})..."
                )
                await _encode_batch(pending)
                await col_knowledge.insert_many(pending)
                upload_count += len(pending)
                pending = []

        # Flush remainder
        if pending:
            processed = already_indexed + upload_count + len(pending)
            await update_job_status(
                job_id, "processing",
                f"Vectorizing pages ({processed}/{total_pages})..."
            )
            await _encode_batch(pending)
            await col_knowledge.insert_many(pending)
            upload_count += len(pending)

        total_indexed = already_indexed + upload_count
        msg = f"Completed: Successfully indexed {upload_count} new pages ({total_indexed} total)."
        await update_job_status(job_id, "completed", msg)
        return {"status": "success", "message": msg, "count": upload_count}

    except Exception as e:
        err_msg = str(e)
        await update_job_status(job_id, "failed", error=err_msg)
        raise e

# ---------------------------------------------------------------------------
# CSV ingestion
# ---------------------------------------------------------------------------

async def ingest_csv(filepath: str, filename: str, job_id: str = None) -> dict:
    """Parses a CSV database using Polars, embeds rows in batches, and saves to MongoDB."""
    try:
        await update_job_status(job_id, "processing", "Reading CSV data...")
        df = await run_in_threadpool(lambda: pl.read_csv(filepath, null_values=[""]).fill_null(""))

        total_rows = len(df)

        # --- Checkpoint ---
        already_indexed = await col_knowledge.count_documents({"source": filename})

        if already_indexed >= total_rows > 0:
            msg = f"Skipped: CSV {filename} already fully indexed ({total_rows} rows)."
            await update_job_status(job_id, "completed", msg)
            return {"status": "skipped", "message": msg}

        if already_indexed > 0:
            await update_job_status(
                job_id, "processing",
                f"Resuming from checkpoint ({already_indexed}/{total_rows} rows already indexed)..."
            )

        upload_count = 0
        pending: list = []

        for index, row in enumerate(df.iter_rows(named=True)):
            if index < already_indexed:
                continue

            row_text = ", ".join([f"{col}: {val}" for col, val in row.items() if val != ""])
            pending.append({
                "text": row_text,
                "source": filename,
                "row_number": index + 1,
                "doc_type": "dtc_database"
            })

            if len(pending) >= EMBED_BATCH:
                processed = already_indexed + upload_count + len(pending)
                await update_job_status(
                    job_id, "processing",
                    f"Vectorizing CSV rows ({processed}/{total_rows})..."
                )
                await _encode_batch(pending)
                await col_knowledge.insert_many(pending)
                upload_count += len(pending)
                pending = []

        # Flush remainder
        if pending:
            processed = already_indexed + upload_count + len(pending)
            await update_job_status(
                job_id, "processing",
                f"Vectorizing CSV rows ({processed}/{total_rows})..."
            )
            await _encode_batch(pending)
            await col_knowledge.insert_many(pending)
            upload_count += len(pending)

        total_indexed = already_indexed + upload_count
        msg = f"Completed: Successfully indexed {upload_count} new rows ({total_indexed} total)."
        await update_job_status(job_id, "completed", msg)
        return {"status": "success", "message": msg, "count": upload_count}

    except Exception as e:
        err_msg = str(e)
        await update_job_status(job_id, "failed", error=err_msg)
        raise e

# ---------------------------------------------------------------------------
# Text / Markdown ingestion
# ---------------------------------------------------------------------------

async def ingest_text(filepath: str, filename: str, job_id: str = None) -> dict:
    """Parses a text file, chunks it, embeds in batches, and saves to MongoDB."""
    try:
        await update_job_status(job_id, "processing", "Reading text data...")
        content = await run_in_threadpool(lambda: open(filepath, 'r', encoding='utf-8').read())

        # ------------------------------------------------------------------
        # Chunking strategy
        # ------------------------------------------------------------------
        is_dtc_file = "dtc" in filename.lower()

        if is_dtc_file:
            # Split on the start of each DTC entry
            entry_starts = re.split(r'(?=\n[PBCU][0-9A-F]{4} is an OBD-II)', content)
            chunks = [part.strip()[:2000] for part in entry_starts if part.strip()]
        else:
            # Merge short paragraphs into ~1500-char page chunks
            paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
            merged: list = []
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
            chunks = merged if merged else paragraphs

        total_chunks = len(chunks)

        # --- Checkpoint: count already-indexed chunks for this source ---
        already_indexed = await col_knowledge.count_documents({"source": filename})

        if already_indexed >= total_chunks > 0:
            msg = f"Skipped: {filename} already fully indexed ({total_chunks} chunks)."
            await update_job_status(job_id, "completed", msg)
            return {"status": "skipped", "message": msg}

        if already_indexed > 0:
            await update_job_status(
                job_id, "processing",
                f"Resuming from checkpoint ({already_indexed}/{total_chunks} chunks already indexed)..."
            )

        doc_type = "dtc_entry" if is_dtc_file else "text_document"
        upload_count = 0
        pending: list = []

        for index, chunk_text in enumerate(chunks):
            # Skip chunks already committed to the DB in a previous run
            if index < already_indexed:
                continue

            pending.append({
                "text": chunk_text,
                "source": filename,
                "chunk_number": index + 1,
                "doc_type": doc_type
            })

            if len(pending) >= EMBED_BATCH:
                processed = already_indexed + upload_count + len(pending)
                await update_job_status(
                    job_id, "processing",
                    f"Vectorizing text chunks ({processed}/{total_chunks})..."
                )
                await _encode_batch(pending)
                await col_knowledge.insert_many(pending)
                upload_count += len(pending)
                pending = []

        # Flush remainder
        if pending:
            processed = already_indexed + upload_count + len(pending)
            await update_job_status(
                job_id, "processing",
                f"Vectorizing text chunks ({processed}/{total_chunks})..."
            )
            await _encode_batch(pending)
            await col_knowledge.insert_many(pending)
            upload_count += len(pending)

        total_indexed = already_indexed + upload_count
        msg = f"Completed: Successfully indexed {upload_count} new chunks ({total_indexed} total)."
        await update_job_status(job_id, "completed", msg)
        return {"status": "success", "message": msg, "count": upload_count}

    except Exception as e:
        err_msg = str(e)
        await update_job_status(job_id, "failed", error=err_msg)
        raise e
