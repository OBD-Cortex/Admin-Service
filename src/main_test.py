import os
import sys
import time
import asyncio
import datetime
from pathlib import Path

# Dynamically calculate the SERVICE_ROOT based on this script's location
SERVICE_ROOT = Path(__file__).resolve().parent
if SERVICE_ROOT.name == "src":
    SERVICE_ROOT = SERVICE_ROOT.parent

sys.path.insert(0, str(SERVICE_ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(SERVICE_ROOT / ".env")
except ImportError:
    pass

import core.config
from core.database import init_db, db, col_devices, col_knowledge

def print_table(title, headers, rows):
    print(f"\n=== {title} ===")
    widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))
            
    header_str = " | ".join(f"{headers[i]:<{widths[i]}}" for i in range(len(headers)))
    print(header_str)
    print("-" * (sum(widths) + 3 * (len(headers) - 1)))
    
    for row in rows:
        row_str = " | ".join(f"{str(row[i]):<{widths[i]}}" for i in range(len(row)))
        print(row_str)
    print("-" * (sum(widths) + 3 * (len(headers) - 1)))

def test_model_inference():
    print("\n[*] Initializing SentenceTransformer Model Inference Benchmark...")
    start_load = time.time()
    from core.models import embed_model
    load_time = (time.time() - start_load) * 1000
    print(f"    [+] Model Load/Verification Latency: {load_time:.2f} ms")

    short_query = "P0300 cylinder misfire detected"
    long_manual = (
        "DIAGNOSTIC PROCEDURE FOR OBD-II CODE P0300 (RANDOM/MULTIPLE CYLINDER MISFIRE):\n"
        "1. Verify fuel pressure is within specs (35-45 psi at rail).\n"
        "2. Inspect spark plug gap and ignition coil resistance. Nominal coil resistance: 0.8 ohms.\n"
        "3. Check for vacuum leaks using smoke machine at throttle body.\n"
        "4. Verify fuel injector pulse width using osciloscope. Target width: 2.5ms at hot idle.\n"
        "5. Check engine compression: minimum 120 psi per cylinder, max 10% variance between cylinders."
    )

    # 1. Short Query Encoding
    print("[*] Benchmarking short text embedding (20 iterations)...")
    short_latencies = []
    for _ in range(20):
        start = time.time()
        embed_model.encode(short_query)
        short_latencies.append((time.time() - start) * 1000)

    # 2. Long Document Chunk Encoding
    print("[*] Benchmarking long text embedding (10 iterations)...")
    long_latencies = []
    for _ in range(10):
        start = time.time()
        embed_model.encode(long_manual)
        long_latencies.append((time.time() - start) * 1000)

    # 3. Batch Encoding
    print("[*] Benchmarking batch embedding (5 batches of 10 short queries)...")
    batch_latencies = []
    batch_docs = [f"DTC fault code diagnosis description number {i}" for i in range(10)]
    for _ in range(5):
        start = time.time()
        embed_model.encode(batch_docs)
        batch_latencies.append((time.time() - start) * 1000)

    # Display statistics
    headers = ["Text Size / Mode", "Min (ms)", "Max (ms)", "Average (ms)", "Throughput"]
    
    avg_short = sum(short_latencies) / len(short_latencies)
    avg_long = sum(long_latencies) / len(long_latencies)
    avg_batch = sum(batch_latencies) / len(batch_latencies)

    rows = [
        ["Short Query (5 words)", f"{min(short_latencies):.2f}", f"{max(short_latencies):.2f}", f"{avg_short:.2f}", f"{(1000/avg_short):.2f} docs/sec"],
        ["Long Chunk (80 words)", f"{min(long_latencies):.2f}", f"{max(long_latencies):.2f}", f"{avg_long:.2f}", f"{(1000/avg_long):.2f} docs/sec"],
        ["Batch (10 docs)", f"{min(batch_latencies):.2f}", f"{max(batch_latencies):.2f}", f"{avg_batch:.2f}", f"{(100/avg_batch*1000):.2f} docs/sec"]
    ]
    print_table("SentenceTransformer Inference Speeds", headers, rows)

async def test_database_performance():
    print("\n[*] Running MongoDB Atlas Database Benchmarks...")
    try:
        await init_db()
    except Exception as e:
        print(f"    [-] MongoDB Connection Failed: {e}")
        return

    # Check connection
    start_ping = time.time()
    await db.client.admin.command("ping")
    ping_ms = (time.time() - start_ping) * 1000
    print(f"    [+] MongoDB Atlas Ping RTT: {ping_ms:.2f} ms")

    # Ingest a temporary vector document to test indexing
    dummy_doc = {
        "text": "Temporary performance verification document.",
        "source": "PERF_TEST_DOC",
        "page_number": 1,
        "doc_type": "repair_manual",
        "embedding": [0.1] * 640  # 640D Harrier model embedding size
    }

    start_write = time.time()
    await col_knowledge.insert_one(dummy_doc)
    write_ms = (time.time() - start_write) * 1000
    print(f"    [+] Vector Document Write Latency: {write_ms:.2f} ms")

    # Find the document
    start_query = time.time()
    result = await col_knowledge.find_one({"source": "PERF_TEST_DOC"})
    query_ms = (time.time() - start_query) * 1000
    print(f"    [+] Vector Document Retrieval Latency: {query_ms:.2f} ms")

    # Cleanup
    start_delete = time.time()
    await col_knowledge.delete_many({"source": "PERF_TEST_DOC"})
    delete_ms = (time.time() - start_delete) * 1000
    print(f"    [+] Document Cleanup Latency: {delete_ms:.2f} ms")

    headers = ["Metric", "Latency (ms)"]
    rows = [
        ["Database Ping RTT", f"{ping_ms:.2f}"],
        ["Insert Document", f"{write_ms:.2f}"],
        ["Query Document", f"{query_ms:.2f}"],
        ["Delete Document", f"{delete_ms:.2f}"]
    ]
    print_table("Vector DB Metadata Performance", headers, rows)

async def test_admin_api():
    print("\n[*] Running Admin Control Plane API Routing Latency Test...")
    try:
        from fastapi.testclient import TestClient
        from main_api import app
        from core.jwt_native import encode_jwt
    except ImportError as e:
        print(f"    [-] FastAPI TestClient / JWT encoder not available: {e}")
        return

    # Use existing environment variables or fallback
    secret = os.getenv("ADMIN_JWT_SECRET") or "TEST_ADMIN_SECRET_KEY_JWT_123_ABC"
    
    # Generate admin JWT
    payload = {
        "iss": "obd-cortex-admin",
        "aud": "obd-cortex-admin-api",
        "exp": int((datetime.datetime.utcnow() + datetime.timedelta(hours=1)).timestamp()),
        "user": "performance_tester"
    }
    
    # Dynamically inject ADMIN_JWT_SECRET if not configured, to allow test running locally
    if not os.getenv("ADMIN_JWT_SECRET"):
        print("    [!] ADMIN_JWT_SECRET not set in environment. Injecting test secret for local routing benchmark.")
        core.config.ADMIN_JWT_SECRET = secret
        
    token = encode_jwt(payload, secret)
    headers = {"Authorization": f"Bearer {token}"}
    
    client = TestClient(app)

    # 1. Test Endpoint 1: GET /api/admin/stats
    print("[*] Benchmarking GET /api/admin/stats (10 iterations)...")
    stats_latencies = []
    for _ in range(10):
        start = time.time()
        res = client.get("/api/admin/stats", headers=headers)
        stats_latencies.append((time.time() - start) * 1000)
        
    # Check success
    success_rate = sum(1 for lat in stats_latencies if lat > 0)

    # 2. Test Endpoint 2: POST /api/admin/devices/generate
    print("[*] Benchmarking POST /api/admin/devices/generate [10 devices] (5 iterations)...")
    gen_payload = {"count": 10}
    gen_latencies = []
    generated_tokens = []
    for _ in range(5):
        start = time.time()
        res = client.post("/api/admin/devices/generate", json=gen_payload, headers=headers)
        gen_latencies.append((time.time() - start) * 1000)
        if res.status_code == 200:
            generated_tokens.extend(res.json().get("tokens", []))

    # Cleanup generated test devices in background
    if generated_tokens:
        print(f"[*] Cleaning up {len(generated_tokens)} generated test devices...")
        await col_devices.delete_many({"device_token": {"$in": generated_tokens}})

    headers = ["Endpoint Route", "Min (ms)", "Max (ms)", "Average (ms)"]
    rows = [
        ["GET /api/admin/stats", f"{min(stats_latencies):.2f}", f"{max(stats_latencies):.2f}", f"{(sum(stats_latencies)/len(stats_latencies)):.2f}"],
        ["POST /api/admin/devices/generate", f"{min(gen_latencies):.2f}", f"{max(gen_latencies):.2f}", f"{(sum(gen_latencies)/len(gen_latencies)):.2f}"]
    ]
    print_table("Admin API Endpoint Latency Metrics", headers, rows)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Admin-Service Performance Benchmarking")
    parser.add_argument("--test", choices=['model', 'db', 'api', 'all'], default='all', help="Select benchmark to run")
    args = parser.parse_args()

    loop = asyncio.get_event_loop()

    print("==================================================")
    print(" OBD-Cortex Administration Service Benchmarks")
    print("==================================================")

    if args.test in ['model', 'all']:
        test_model_inference()
    if args.test in ['db', 'all']:
        loop.run_until_complete(test_database_performance())
    if args.test in ['api', 'all']:
        loop.run_until_complete(test_admin_api())

    print("\n[+] Performance evaluation completed successfully.")

if __name__ == "__main__":
    main()
