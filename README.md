# Magnet to Magicode V03

Downloads files from a magnet or torrent link and uploads each file individually to [send.magicode.me](https://send.magicode.me). Files are not compressed, and the torrent's directory structure is retained in the output file names.

## V03 Changes

- **Fast HTTP uploads** - Direct API calls instead of browser automation (much faster)
- **Parallel uploads** - Uploads 2 files simultaneously for better performance
- **XOR encryption** - URLs are encrypted to prevent blocking by filters
- **Smaller footprint** - No browser/Chromium needed
- **KVS storage** - Encrypted URLs and decryption key stored in Apify KVS

## Input

- `magnetLink` (required): magnet or torrent link.

The Magicode service currently allows files up to 10 GB and deletes them two days after upload. The Actor uploads files using direct HTTP API calls and returns the encrypted download link for each file.

## Output

The Actor reports the destination, file count, total size, and each uploaded file's name, size, and **encrypted** download URL in the Apify dataset. The full URLs are stored in Apify KVS under the key `ENCRYPTED_DOWNLOAD_URLS` with the decryption key in `DECRYPTION_KEY`.

**Important:** To decrypt URLs, use the XOR decryption function with the key from KVS. The dataset only contains encrypted URLs to prevent blocking.

## Repository

- **Owner:** MEIRMEIR513
- **GitHub:** https://github.com/MEIRMEIR513/magnet-to-magicodeV03
