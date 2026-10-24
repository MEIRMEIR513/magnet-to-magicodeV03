import asyncio
import base64
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from apify import Actor

try:
    import libtorrent as lt
except ImportError:
    lt = None

try:
    import requests
except ImportError:
    requests = None

MAGICODE_API = "https://send.magicode.me"
MAX_FILE_SIZE = 10_000_000_000
CHUNK_SIZE = 2 * 1024 * 1024  # 2MB chunks
UPLOAD_TIMEOUT = 60 * 60  # 1 hour

# XOR encryption key
ENCRYPTION_KEY = b"magnet2magicode_secret_key_2024"


def xor_encrypt(data: str, key: bytes) -> str:
    """Encrypt a string using XOR cipher and return base64 encoded result."""
    data_bytes = data.encode('utf-8')
    encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(data_bytes))
    return base64.b64encode(encrypted).decode('utf-8')


def get_upload_keys(filename: str, size: int) -> dict:
    """Get upload keys from magicode API."""
    url = f"{MAGICODE_API}/send-file/prep-upload"
    headers = {
        "Content-Type": "application/json",
        "Origin": MAGICODE_API,
        "Referer": f"{MAGICODE_API}/",
    }
    data = {"size": size, "filename": filename}

    response = requests.post(url, headers=headers, json=data, timeout=30)
    response.raise_for_status()
    return response.json()


def upload_chunk(file_path: Path, position: int, length: int, key_upload: str, key_file: str) -> dict:
    """Upload a single chunk to magicode."""
    url = f"{MAGICODE_API}/send-file/data-upload"
    params = {
        "position": position,
        "length": length,
        "keyUpload": key_upload,
        "keyFile": key_file,
    }
    headers = {
        "Origin": MAGICODE_API,
        "Referer": f"{MAGICODE_API}/",
    }

    # Read chunk from file
    with open(file_path, "rb") as f:
        f.seek(position)
        chunk = f.read(length)

    files = {"file": (file_path.name, chunk)}
    response = requests.post(url, params=params, headers=headers, files=files, timeout=UPLOAD_TIMEOUT)
    response.raise_for_status()
    return response.json()


def upload_file(file_path: Path, root: Path) -> dict:
    """Upload a single file to magicode using HTTP API."""
    size = file_path.stat().st_size
    filename = file_path.name

    if size > MAX_FILE_SIZE:
        raise ValueError(f"{filename} exceeds Magicode's 10 GB per-file limit")

    Actor.log.info("Getting upload keys for %s", filename)
    keys = get_upload_keys(filename, size)
    key_upload = keys["keyUpload"]
    key_file = keys["keyFile"]

    Actor.log.info("Uploading %s (%.2f MB) in chunks", filename, size / (1024 * 1024))

    position = 0
    chunk_num = 0
    total_chunks = (size + CHUNK_SIZE - 1) // CHUNK_SIZE

    while position < size:
        chunk_num += 1
        length = min(CHUNK_SIZE, size - position)

        Actor.log.info(
            "Uploading chunk %d/%d (%d-%d bytes)",
            chunk_num, total_chunks, position, position + length
        )

        result = upload_chunk(file_path, position, length, key_upload, key_file)

        if result.get("ok") and result.get("end"):
            break
        elif not result.get("ok"):
            raise RuntimeError(f"Upload failed for {filename}: {result}")

        position += length

    # Construct download URL
    download_url = f"{MAGICODE_API}/send-file/file/{key_file}/view"

    object_name = file_path.relative_to(root).as_posix()
    encrypted_url = xor_encrypt(download_url, ENCRYPTION_KEY)

    return {
        "name": object_name,
        "size": size,
        "encrypted_url": encrypted_url,
    }


def upload_files_parallel(files: list[Path], root: Path, max_workers: int = 2) -> list[dict]:
    """Upload multiple files in parallel using ThreadPoolExecutor."""
    uploaded = []
    encrypted_urls_kvs = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(upload_file, file_path, root): file_path
            for file_path in files
        }

        for future in asyncio.as_completed(asyncio.wrap_future(future, asyncio.get_event_loop()) for future in futures):
            file_path = futures[asyncio.wrap_future(future, asyncio.get_event_loop())]
            try:
                result = future.result()
                uploaded.append(result)
                encrypted_urls_kvs[result["name"]] = result["encrypted_url"]
                Actor.log.info("Uploaded %s to Magicode", result["name"])
            except Exception as e:
                Actor.log.error("Failed to upload %s: %s", file_path, e)
                raise

    return uploaded, encrypted_urls_kvs


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        magnet_link = actor_input.get("magnetLink", "").strip()

        missing = [
            name for name, value in (("magnetLink", magnet_link),) if not value
        ]
        if missing:
            raise ValueError("Required input is missing: " + ", ".join(missing))
        if lt is None:
            raise RuntimeError("libtorrent is not installed")
        if requests is None:
            raise RuntimeError("requests is not installed")

        temp_dir = tempfile.mkdtemp(prefix="magnet-to-magicode-")
        Actor.log.info("Starting magnet download")
        try:
            session = lt.session()
            handle = lt.add_magnet_uri(session, magnet_link, {
                "save_path": temp_dir,
                "storage_mode": lt.storage_mode_t(2),
            })

            Actor.log.info("Waiting for torrent metadata")
            while not handle.has_metadata():
                await asyncio.sleep(1)

            torrent_info = handle.get_torrent_info()
            Actor.log.info(
                "Metadata received; download size %.2f MB",
                torrent_info.total_size() / (1024 * 1024),
            )
            handle.set_sequential_download(True)
            last_log = 0.0
            while not handle.is_seed():
                status = handle.status()
                now = time.monotonic()
                if now - last_log >= 10:
                    Actor.log.info(
                        "Download progress: %.1f%% at %.1f KB/s",
                        status.progress * 100,
                        status.download_rate / 1024,
                    )
                    last_log = now
                await asyncio.sleep(1)
            session.pause()

            files = sorted(
                path for path in Path(temp_dir).rglob("*")
                if path.is_file() and not path.name.startswith(".")
            )
            if not files:
                raise RuntimeError("Torrent completed but no files were found")
            Actor.log.info(
                "Downloaded %d file(s); uploading to Magicode with parallel uploads",
                len(files),
            )

            # Upload files in parallel
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=2) as executor:
                uploaded, encrypted_urls_kvs = await loop.run_in_executor(
                    executor,
                    lambda: upload_files_parallel(files, Path(temp_dir), max_workers=2)
                )

            output = {
                "downloaded": True,
                "uploaded": True,
                "destination": "send.magicode.me",
                "compressed": False,
                "totalFiles": len(files),
                "uploadedFiles": len(uploaded),
                "totalSize": sum(item["size"] for item in uploaded),
                "files": uploaded,
            }
            await Actor.push_data(output)
            await Actor.set_value("ENCRYPTED_DOWNLOAD_URLS", encrypted_urls_kvs)
            await Actor.set_value("DECRYPTION_KEY", ENCRYPTION_KEY.decode('utf-8', errors='ignore'))
            Actor.log.info(
                "Completed: uploaded %d file(s) to Magicode", len(uploaded)
            )
        finally:
            shutil.rmtree(temp_dir)
            Actor.log.info("Temporary download directory removed")


if __name__ == "__main__":
    asyncio.run(main())
