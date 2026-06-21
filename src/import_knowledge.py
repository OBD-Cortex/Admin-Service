import os
import sys
import asyncio
import hashlib
import argparse
from datetime import datetime
from bson.json_util import loads

# Add the current directory to python path so it can import core
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.database import db, client

def get_doc_hash(doc):
    """
    Generate a unique, stable SHA-256 hash representing the document's content.
    This allows deduplication across databases even if ObjectIds (_id) differ.
    """
    text = doc.get("text") or ""
    source = doc.get("source") or ""
    chunk = doc.get("chunk_number")
    page = doc.get("page_number")
    
    # Build a stable key combining metadata and text content
    key = f"{source}:{chunk}:{page}:{text}"
    return hashlib.sha256(key.encode('utf-8')).hexdigest()

def stream_documents(filepath):
    """
    Stream and parse JSON documents from a large JSON array file line-by-line.
    Uses a lightweight state machine to track braces and quotes, avoiding
    loading the entire file into memory and handling curly braces inside text fields.
    """
    current_doc_lines = []
    in_document = False
    brace_depth = 0
    in_string = False
    escape_next = False
    
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            stripped = line.strip()
            if not in_document:
                if stripped == '[{' or stripped == '{':
                    in_document = True
                    current_doc_lines = ['{']
                    brace_depth = 1
                    in_string = False
                    escape_next = False
            else:
                current_doc_lines.append(line)
                for char in line:
                    if escape_next:
                        escape_next = False
                        continue
                    if char == '\\':
                        escape_next = True
                        continue
                    if char == '"':
                        in_string = not in_string
                        continue
                    if not in_string:
                        if char == '{':
                            brace_depth += 1
                        elif char == '}':
                            brace_depth -= 1
                
                if brace_depth == 0:
                    doc_str = "".join(current_doc_lines).strip()
                    if doc_str.endswith(','):
                        doc_str = doc_str[:-1].strip()
                    if doc_str.endswith(']'):
                        doc_str = doc_str[:-1].strip()
                    try:
                        yield loads(doc_str)
                    except Exception as e:
                        print(f"Error parsing document: {e}")
                    in_document = False

async def main():
    parser = argparse.ArgumentParser(description="Directly import a JSON array dump into a MongoDB collection.")
    parser.add_argument("file_path", nargs="?", default="rag_db.knowledge.json", help="Path to the JSON file to import")
    parser.add_argument("collection", nargs="?", default="knowledge", help="MongoDB collection name (default: knowledge)")
    args = parser.parse_args()
    
    file_path = args.file_path
    col_name = args.collection
    
    # Resolve file path
    if not os.path.isabs(file_path):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        path_options = [
            os.path.abspath(file_path),
            os.path.join(os.path.dirname(script_dir), file_path),
            os.path.join(script_dir, file_path)
        ]
        resolved_path = None
        for p in path_options:
            if os.path.exists(p):
                resolved_path = p
                break
        if not resolved_path:
            print(f"ERROR: File not found: {file_path}")
            print("Checked paths:")
            for p in path_options:
                print(f"  - {p}")
            sys.exit(1)
        file_path = resolved_path
        
    print(f"[*] Starting import from {file_path} into collection '{col_name}'")
    
    # 1. Verify connection
    try:
        await client.admin.command("ping")
        print("[✓] MongoDB Connected successfully.")
    except Exception as e:
        print(f"[!] MongoDB Connection Failed: {e}")
        sys.exit(1)
        
    col = db[col_name]
    
    # 2. Fetch existing documents and compute content hashes
    print(f"[*] Fetching existing documents from '{col_name}' collection to compute content hashes...")
    existing_hashes = set()
    try:
        async for doc in col.find({}, {"text": 1, "source": 1, "chunk_number": 1, "page_number": 1}):
            h = get_doc_hash(doc)
            existing_hashes.add(h)
        print(f"[✓] Found {len(existing_hashes)} unique existing documents in database.")
    except Exception as e:
        print(f"[!] Failed to fetch existing documents: {e}")
        sys.exit(1)
        
    # 3. Stream, filter, and batch insert
    batch_size = 500
    batch = []
    total_imported = 0
    total_skipped = 0
    first_doc_source = None
    
    print("[*] Importing remaining documents in batches...")
    
    try:
        for doc in stream_documents(file_path):
            if not first_doc_source and doc.get("source"):
                first_doc_source = doc.get("source")
                
            doc_hash = get_doc_hash(doc)
            if doc_hash in existing_hashes:
                total_skipped += 1
                continue
                
            batch.append(doc)
            if len(batch) >= batch_size:
                await col.insert_many(batch)
                total_imported += len(batch)
                print(f"    [-] Imported {total_imported} new documents (skipped {total_skipped} duplicates)...")
                batch = []
                
        # Insert any remaining documents
        if batch:
            await col.insert_many(batch)
            total_imported += len(batch)
            print(f"    [-] Imported {total_imported} new documents (skipped {total_skipped} duplicates)...")
            
        print(f"[✓] Import complete!")
        print(f"    [-] New documents imported: {total_imported}")
        print(f"    [-] Existing documents skipped: {total_skipped}")
        
        # Verify final count
        final_count = await col.count_documents({})
        print(f"[✓] Verification: Collection now contains {final_count} documents.")
        
        # 4. Update the matching job status in the jobs collection
        if first_doc_source:
            col_jobs = db["jobs"]
            # Find the most recent job matching the source filename
            job = await col_jobs.find_one(
                {"filename": first_doc_source},
                sort=[("created_at", -1)]
            )
            if job:
                print(f"[*] Updating matching job '{job['_id']}' status in 'jobs' collection...")
                await col_jobs.update_one(
                    {"_id": job["_id"]},
                    {
                        "$set": {
                            "status": "completed",
                            "progress": f"Import completed directly from backup JSON. Total documents: {final_count}.",
                            "error_message": None,
                            "updated_at": datetime.utcnow()
                        }
                    }
                )
                print("[✓] Job status updated successfully.")
            else:
                print(f"[-] No matching job found in 'jobs' collection for filename '{first_doc_source}'.")
                
    except Exception as e:
        print(f"[!] Error during import: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
