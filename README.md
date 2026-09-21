# static-wado-rs (serve only dicom part 10 objects from your hard drive at launch time without the ability to change anything) 

A lightweight, dependency-free static WADO-RS server written in pure Python.

Developed as a reference simulator for DICOMweb retrieval transactions within the context of the **IHE Shareazone** project and the **IHE MADO** (Mobile Access to DICOM Objects) profile.

---

## Overview

`static-wado-rs` serves DICOM studies directly from local file systems using standard RESTful DICOMweb retrieve transactions (WADO-RS). It is designed specifically for testing, simulation, and educational purposes.

### Design Principles

- **Zero external dependencies**: Built entirely with Python's standard library (`http.server`, `struct`, `zlib`, `dataclasses`, `json`, `argparse`, `pathlib`, `urllib`). Does not require third-party libraries such as `pydicom`, `numpy`, or web frameworks.
- **Direct byte delivery**: Files and extracted frame streams are served exactly as stored on disk without transcoding, re-encoding, or decompressing pixel data.
- **Educational DICOM Part 10 parser**: Includes a clean, self-contained binary parser demonstrating how to read Part 10 headers, dataset elements, nested sequences, and encapsulated fragments using standard Python modules.

---

## Supported Transactions

The server implements the core DICOM PS3.18 WADO-RS retrieve transactions:

| Transaction | HTTP Path | Media Types |
|---|---|---|
| **Retrieve Study** | `GET /studies/{study}` | `multipart/related; type="application/dicom"` |
| **Retrieve Series** | `GET /studies/{study}/series/{series}` | `multipart/related; type="application/dicom"` |
| **Retrieve Instance** | `GET /studies/{study}/series/{series}/instances/{instance}` | `multipart/related; type="application/dicom"` |
| **Retrieve Metadata** | `GET /studies/{study}[/series/{series}[/instances/{instance}]]/metadata` | `application/dicom+json`, `application/json` |
| **Retrieve Bulkdata** | `GET /studies/.../instances/{instance}/bulkdata[/{tagPath}]` | `application/octet-stream`, `multipart/related` |
| **Retrieve Frames** | `GET /studies/.../instances/{instance}/frames/{framelist}` | `multipart/related; type="..."` |

*Note: In accordance with its static delivery design, image decoding and rendering pipelines are omitted (`/rendered` and `/thumbnail` endpoints return HTTP 404).*

---

## Getting Started

### Prerequisites

- Python 3.8 or higher

### Usage

Start the server by specifying the directory containing DICOM Part 10 files:

```bash
python wado_server.py --dir /path/to/dicom/files --port 10104
```

### Command-Line Arguments

```text
usage: wado_server [-h] [--dir PATH] [--host ADDRESS] [--port PORT] [--no-recursive]

options:
  -h, --help            show this help message and exit
  --dir, -d PATH        directory holding DICOM Part 10 files (default: .)
  --host, -H ADDRESS    bind address (default: 0.0.0.0)
  --port, -p PORT       TCP port (default: 10104)
  --no-recursive        catalog only the top-level directory
```

Navigating to `http://localhost:10104/` in a browser provides an overview of all catalogued studies, series, and instances, along with sample query links.

---

## Standards and Specifications

This implementation adheres to the following standards:

- **DICOM PS3.3**: Information Object Definitions (Specific Character Sets, Table C.12-2 to C.12-5)
- **DICOM PS3.5**: Data Structures and Encoding (VR Definitions, Undefined-Length Sequences)
- **DICOM PS3.6**: Data Dictionary
- **DICOM PS3.10**: Media Storage and File Format
- **DICOM PS3.18**: Web Services (WADO-RS, Annex F DICOM JSON Model, Bulk Data References)
- **IHE Radiology**: Mobile Access to DICOM Objects (MADO) / IHE Shareazone

---

## Authors & License

- **Author**: Nick Hermans (`nick.hermans@uzleuven.be`), UZ Leuven
- **Context**: IHE Shareazone
- **License**: [BSD 3-Clause License](LICENSE)

```
Copyright (c) 2026, Nick Hermans (nick.hermans@uzleuven.be), UZ Leuven
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the conditions of the BSD 3-Clause License are met.

This software is provided "as is" for testing, research, and educational purposes.
Neither Nick Hermans nor UZ Leuven assumes any liability arising from its use.
```
