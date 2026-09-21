# IHE SHARAZONE MADO WADO-RS Static DICOMweb Simulator Server

A dependency-free, pure Python WADO-RS (DICOMweb Retrieve) static server designed for **IHE SHARAZONE** and **IHE MADO** (Mobile Access to DICOM Objects) simulation, testing, and developer education.

This simulator has **no dependencies on other toolkits or external libraries** (no `pydicom`, no `numpy`, no `flask`, etc.). It runs on standard Python 3.8+ using only Python standard library modules (`http.server`, `struct`, `zlib`, `dataclasses`, `json`, `argparse`, `pathlib`, `urllib.parse`).

> **NOTICE:** THIS SOFTWARE IS NOT INTENDED FOR CLINICAL OR PRODUCTION USE. IT IS OFFERED SOLELY FOR CONVENIENCE, TESTING, AND SIMULATION WITHIN THE IHE SHARAZONE CONTEXT. STORED BYTES ARE DELIVERED AS HELD ON DISK; NO TRANSCODING, RENDERING, OR DECOMPRESSION PIPELINE IS INCLUDED.

---

## Key Features

- **Pure Python Standard Library**: Runs directly out of the box with `python wado_server.py`. Zero `pip install` required.
- **WADO-RS Retrieve Transactions (DICOM PS3.18 / IHE MADO)**:
  - **Retrieve Study / Series / Instance**:
    - `GET /studies/{study}`
    - `GET /studies/{study}/series/{series}`
    - `GET /studies/{study}/series/{series}/instances/{instance}`
  - **Retrieve Metadata (DICOM JSON Model - PS3.18 Annex F)**:
    - `GET /studies/{study}/metadata`
    - `GET /studies/{study}/series/{series}/metadata`
    - `GET /studies/{study}/series/{series}/instances/{instance}/metadata`
  - **Retrieve Bulkdata**:
    - `GET /studies/{study}/bulkdata`
    - `GET /studies/{study}/series/{series}/bulkdata`
    - `GET /studies/{study}/series/{series}/instances/{instance}/bulkdata`
    - `GET /studies/{study}/series/{series}/instances/{instance}/bulkdata/{tagPath}` (e.g. `.../bulkdata/7FE00010`)
  - **Retrieve Frames**:
    - `GET /studies/{study}/series/{series}/instances/{instance}/frames/{framelist}` (e.g. `.../frames/1,2,3`)
- **Fast Part 10 Parser**: Custom binary parser for DICOM Part 10 files:
  - Supports Explicit VR Little Endian, Implicit VR Little Endian, Explicit VR Big Endian, and Deflated Explicit VR Little Endian (`1.2.840.10008.1.2.1.99`).
  - Correctly indexes and extracts uncompressed native frames as well as encapsulated compressed frames (JPEG, JPEG-LS, JPEG 2000, HTJ2K, RLE, MPEG2/MP4).
  - Handles nested sequence items and undefined-length elements safely up to a depth limit.
- **Multipart MIME Streaming**: Delivers `multipart/related` payloads with exact pre-calculated `Content-Length` headers, boundary generation, and individual part `Content-Type` and `Content-Location` headers.
- **Content Negotiation**: Full support for HTTP `Accept` headers and `?accept=` query parameters per DICOM PS3.18 (handling `type=`, `transfer-syntax=`, and quality `q=` values).
- **CORS Enabled**: Sends permissive Cross-Origin Resource Sharing headers (`Access-Control-Allow-Origin: *`) allowing direct connection from web viewers like OHIF, CornerstoneJS, or custom web frontends.
- **Interactive Index Page**: Visiting the root URL (`/`) in a web browser displays a summary of catalogued studies, series, and instances, along with clickable sample transaction links.
- **No Image Decoding / Rendering**: Deliberately returns HTTP 404 for `/rendered` and `/thumbnail` endpoints because the simulator returns stored bytes directly without bundling image decoders or re-encoding pixels.

---

## Requirements

- Python 3.8 or higher
- No third-party packages needed (pure Python standard library)

---

## Usage

### Starting the Server

Point the server to a directory containing DICOM Part 10 files (files will be discovered recursively by default):

```bash
python wado_server.py --dir /path/to/dicom/files --port 10104
```

### Command-Line Arguments

| Option | Flag | Default | Description |
|---|---|---|---|
| `--dir` | `-d` | `.` | Directory holding DICOM Part 10 files |
| `--host` | `-H` | `0.0.0.0` | Bind address (`0.0.0.0` binds to all network interfaces) |
| `--port` | `-p` | `10104` | Listening TCP port |
| `--no-recursive` | | `False` | Catalog only the top-level directory without descending into subdirectories |

---

## Transaction Examples

Once started (e.g., at `http://localhost:10104`), you can test WADO-RS endpoints:

### Retrieve Metadata (DICOM JSON)
```bash
curl -H "Accept: application/dicom+json" \
  http://localhost:10104/studies/1.2.3.4/metadata
```

### Retrieve Whole Instances (Multipart DICOM Part 10)
```bash
curl -H "Accept: multipart/related; type=\"application/dicom\"" \
  http://localhost:10104/studies/1.2.3.4/series/1.2.3.4.5
```

### Retrieve Pixel Data Frame
```bash
curl -H "Accept: multipart/related; type=\"application/octet-stream\"" \
  http://localhost:10104/studies/1.2.3.4/series/1.2.3.4.5/instances/1.2.3.4.5.6/frames/1
```

---

## Standards Referenced

- **[PS3.3]** DICOM PS3.3: Information Object Definitions (Specific Character Sets C.12.1.1.2)
- **[PS3.5]** DICOM PS3.5: Data Structures and Encoding (VR Definitions, Undefined-Length Sequences)
- **[PS3.6]** DICOM PS3.6: Data Dictionary
- **[PS3.10]** DICOM PS3.10: Media Storage and File Format (Part 10 Header, File Meta Information, Prefix)
- **[PS3.18]** DICOM PS3.18: Web Services (WADO-RS, DICOM JSON Model Annex F, Bulk Data References)
- **IHE MADO**: Mobile Access to DICOM Objects / IHE SHARAZONE

---

## License & Disclaimer

BSD 3-Clause License

Copyright (c) 2026, Nick Hermans (nick.hermans@uzleuven.be), UZ Leuven.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of Nick Hermans, UZ Leuven, nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

### Disclaimer of Liability

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL NICK HERMANS, UZ LEUVEN (HIS COMPANY), OR
CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

PROVIDED AS IS FOR IHE SHARAZONE. NO LIABILITY FOR NICK HERMANS (NICK.HERMANS@UZLEUVEN.BE) AND NO LIABILITY FOR UZ LEUVEN.
