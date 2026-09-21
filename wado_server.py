#!/usr/bin/env python3
"""a dependency-free static WADO-RS server
THIS IS NOT SUITABLE FOR PRODUCTION USE, THIS IS OFFERED HERE FOR YOUR CONVIENIENCE ONLY.
The documented retrieve transactions are:

    GET /studies/{study}/series/{series}
    GET /studies/{study}/series/{series}/instances/{instance}

The Retrieve Rendered Transaction and the Retrieve Thumbnail Transaction are
deliberately absent: it has no sense to implement them as this server doesn't contain no
image decoder, no pixel pipeline, and no image encoder, so `/rendered` and
`/thumbnail` always return 404. Stored bytes are returned exactly as they are held
on disk; nothing is ever transcoded, decompressed for display, or otherwise reinterpreted.

this server module will learn developers how DICOM Part 10 files are read with a parser
written using Python standard modules

Tags are handled throughout as single 32-bit integers, 0xGGGGEEEE, which is
also the key format of the DICOM JSON Model and of bulkdata references.

Author & License:
    Nick Hermans (nick.hermans@uzleuven.be), UZ Leuven
    BSD 3-Clause License
    Provided AS IS for IHE SHARAZONE / SHAREAZONE. No liability for Nick Hermans or UZ Leuven.

Index of Referenced Standards & Specifications:
    [PS3.3]   DICOM PS3.3: Information Object Definitions
              - Section C.12.1.1.2: Specific Character Set (Defined Terms)
              - Tables C.12-2 to C.12-5: Defined Terms for Character Sets
              https://dicom.nema.org/medical/dicom/current/output/html/part03.html#sect_C.12.1.1.2

    [PS3.5]   DICOM PS3.5: Data Structures and Encoding
              - Section 6.2 & 8.1.1: VR Definitions & Undefined-Length UN Sequences
              https://dicom.nema.org/medical/dicom/current/output/html/part05.html

    [PS3.6]   DICOM PS3.6: Data Dictionary
              - Registry of Data Elements & VR Dictionary
              https://dicom.nema.org/medical/dicom/current/output/html/part06.html

    [PS3.10]  DICOM PS3.10: Media Storage and File Format
              - Part 10 File Format Header, File Meta Information, & Prefix
              https://dicom.nema.org/medical/dicom/current/output/html/part10.html

    [PS3.18]  DICOM PS3.18: Web Services (WADO-RS)
              - Section 10.4.1.1.2: Metadata & Bulk Data References
              - Table 8.7.3-5: Encapsulated Frame Media Types
              - Annex F: DICOM JSON Model
              https://dicom.nema.org/medical/dicom/current/output/html/part18.html
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import struct
import uuid
import zlib
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO, Dict, Iterator, List, Optional, Sequence, Tuple

from urllib.parse import parse_qs, unquote, urlsplit


# Values defined by the DICOM Standard ([PS3.5], [PS3.6], [PS3.10], [PS3.18]) since 20 years

Implicit_Vr_Little_Endian = "1.2.840.10008.1.2"
Explicit_Vr_Little_Endian = "1.2.840.10008.1.2.1"
Deflated_Explicit_Vr_Little_Endian = "1.2.840.10008.1.2.1.99"
Explicit_Vr_Big_Endian = "1.2.840.10008.1.2.2"

#: transfer syntaxes whose Pixel Data is native (not encapsulated)
#: Check once if the transfer syntax is native so that a frame is a fixed-size slice.
Native_Transfer_Syntaxes = frozenset({
    Implicit_Vr_Little_Endian,
    Explicit_Vr_Little_Endian,
    Deflated_Explicit_Vr_Little_Endian,
    Explicit_Vr_Big_Endian,
})

#: [PS3.18 Table 8.7.3-5]: the media type that carries a single encapsulated
#: frame of each compressed transfer syntax.
Encapsulated_Media_Types = {
    "1.2.840.10008.1.2.4.50": "image/jpeg",
    "1.2.840.10008.1.2.4.51": "image/jpeg",
    "1.2.840.10008.1.2.4.57": "image/jpeg",
    "1.2.840.10008.1.2.4.70": "image/jpeg",
    "1.2.840.10008.1.2.4.80": "image/jls",
    "1.2.840.10008.1.2.4.81": "image/jls",
    "1.2.840.10008.1.2.4.90": "image/jp2",
    "1.2.840.10008.1.2.4.91": "image/jp2",
    "1.2.840.10008.1.2.4.92": "image/jpx",
    "1.2.840.10008.1.2.4.93": "image/jpx",
    "1.2.840.10008.1.2.4.201": "image/jphc",
    "1.2.840.10008.1.2.4.202": "image/jphc",
    "1.2.840.10008.1.2.4.203": "image/jphc",
    "1.2.840.10008.1.2.4.100": "video/mpeg2",
    "1.2.840.10008.1.2.4.101": "video/mpeg2",
    "1.2.840.10008.1.2.4.102": "video/mp4",
    "1.2.840.10008.1.2.4.103": "video/mp4",
    "1.2.840.10008.1.2.4.104": "video/mp4",
    "1.2.840.10008.1.2.4.105": "video/mp4",
    "1.2.840.10008.1.2.4.106": "video/mp4",
    "1.2.840.10008.1.2.5": "image/dicom-rle",
}

Dicom_Media_Type = "application/dicom"
Dicom_Json_Media_Type = "application/dicom+json"
Json_Media_Type = "application/json"
Octet_Stream_Media_Type = "application/octet-stream"

File_Meta_Group_Length = 0x00020000
Media_Sop_Class_Uid = 0x00020002
Media_Sop_Instance_Uid = 0x00020003
Transfer_Syntax_Uid = 0x00020010
Specific_Character_Set = 0x00080005
Sop_Class_Uid = 0x00080016
Sop_Instance_Uid = 0x00080018
Study_Instance_Uid = 0x0020000D
Series_Instance_Uid = 0x0020000E
Instance_Number = 0x00200013
Samples_Per_Pixel = 0x00280002
Number_Of_Frames = 0x00280008
Rows = 0x00280010
Columns = 0x00280011
Bits_Allocated = 0x00280100
Float_Pixel_Data = 0x7FE00008
Double_Pixel_Data = 0x7FE00009
Pixel_Data = 0x7FE00010
Pixel_Data_Tags = frozenset({Pixel_Data, Float_Pixel_Data, Double_Pixel_Data})

Item = 0xFFFEE000
Item_Delimiter = 0xFFFEE00D
Sequence_Delimiter = 0xFFFEE0DD
Delimiters = frozenset({Item_Delimiter, Sequence_Delimiter})
Undefined_Length = 0xFFFFFFFF

#: guards against pathologically nested (or hostile) sequences
Max_Sequence_Depth = 24

Long_Vrs = frozenset({"OB", "OD", "OF", "OL", "OV", "OW", "SQ", "UC", "UN", "UR", "UT", "UV"})
Valid_Vrs = frozenset({
    "AE", "AS", "AT", "CS", "DA", "DS", "DT", "FD", "FL", "IS", "LO", "LT","OB", "OD", "OF", "OL", "OV", "OW", "PN", "SH", "SL", "SQ", "SS", "ST", "SV", "TM", "UC", "UI", "UL", "UN", "UR", "US", "UT", "UV",
})
#: values of these VRs are never inlined in metadata; they become BulkDataURI
Bulk_Vrs = frozenset({"OB", "OD", "OF", "OL", "OV", "OW", "UN"})
#: VRs whose value is binary and byte-order dependent mapped to a struct code
Binary_Vrs = {"FL": "f", "FD": "d", "SL": "i", "SS": "h", "SV": "q", "UL": "I", "US": "H", "UV": "Q"}
#: vrs that cannot be multi-valued, so a backslash is ordinary text
Single_Value_Vrs = frozenset({"LT", "ST", "UT", "UR"})

#: [PS3.3 C.12.1.1.2] Defined terms mapped to Python codecs.
#: The groups follow [PS3.3] Tables C.12-2 through C.12-5, rather than a codec list.
Character_Sets = {
    # Single-byte character sets without code extensions ([PS3.3 Table C.12-2]).
    "": "ascii",
    "ISO_IR 6": "ascii",
    "ISO_IR 13": "shift_jis",
    "ISO_IR 100": "latin-1",
    "ISO_IR 101": "iso8859-2",
    "ISO_IR 109": "iso8859-3",
    "ISO_IR 110": "iso8859-4",
    "ISO_IR 126": "iso8859-7",
    "ISO_IR 127": "iso8859-6",
    "ISO_IR 138": "iso8859-8",
    "ISO_IR 144": "iso8859-5",
    "ISO_IR 148": "iso8859-9",
    "ISO_IR 166": "iso8859-11",
    "ISO_IR 203": "iso8859-15",

    # Single-byte character sets with code extensions ([PS3.3 Table C.12-3]).
    "ISO 2022 IR 6": "ascii",
    "ISO 2022 IR 13": "shift_jis",
    "ISO 2022 IR 100": "latin-1",
    "ISO 2022 IR 101": "iso8859-2",
    "ISO 2022 IR 109": "iso8859-3",
    "ISO 2022 IR 110": "iso8859-4",
    "ISO 2022 IR 126": "iso8859-7",
    "ISO 2022 IR 127": "iso8859-6",
    "ISO 2022 IR 138": "iso8859-8",
    "ISO 2022 IR 144": "iso8859-5",
    "ISO 2022 IR 148": "iso8859-9",
    "ISO 2022 IR 166": "iso8859-11",
    "ISO 2022 IR 203": "iso8859-15",

    # Multi-byte character sets with code extensions ([PS3.3 Table C.12-4]).
    "ISO 2022 IR 58": "gb2312",
    "ISO 2022 IR 87": "iso2022_jp",
    "ISO 2022 IR 149": "euc_kr",
    "ISO 2022 IR 159": "iso2022_jp_2",

    # Multi-byte character sets without code extensions ([PS3.3 Table C.12-5]).
    "GB18030": "gb18030",
    "GBK": "gbk",
    "ISO_IR 192": "utf-8",
}

_Tag_Dictionary: Dict[int, str] = {}


def _Load_Dictionary(Spec: str) -> None:
    for Entry in Spec.split():
        Tag, _, Vr = Entry.partition(":")
        _Tag_Dictionary[int(Tag, 16)] = Vr


# A pragmatic subset of the [PS3.6] data dictionary. Implicit VR Little Endian
# files carry no VR, and the DICOM JSON Model [PS3.18 Annex F] requires one, so a table is
# unavoidable. Tags outside it decode as UN, which the JSON Model permits and
# which sends the value to bulkdata rather than guessing at: its type.
_Load_Dictionary("""
00281090:CS 00281103:US 00280010:US 0040B020:SQ 00281201:OW 00080062:UI
0040A168:SQ 00080013:TM 00180083:DS 00180085:SH 00080094:SH 00280051:CS
0040A491:CS 00400251:TM 00380050:LO 00082111:ST 00181050:DS 00400005:TM
00280100:US 00283002:US 00400250:DA 00281101:US 00280101:US 00280004:CS
00186000:DS 00181160:SH 0040A170:SQ 00201206:IS 00102160:SH 00080070:LO
00200013:IS 00380400:LO 00281056:CS 00180089:IS 00209056:SH 00380300:LO
00180090:DS 00080022:DA 00280120:US 00181315:CS 00081090:LO 00200032:DS
0040A180:US 0040A043:SQ 00080021:DA 00280102:US 0020000D:UI 00400003:TM
00185100:CS 00181114:DS 00181090:IS 00082112:SQ 00101040:LO 00209128:UL
00280003:US 00180022:CS 00280107:US 00100020:LO 00283003:LO 00400253:SH
00080005:CS 00181164:DS 00200037:DS 00180081:DS 00089209:CS 00281203:OW
00181110:DS 00280008:IS 00201041:DS 00102203:CS 00282112:DS 00181153:IS
001021B0:LT 00101030:DS 00181100:DS 7FE00009:OD 00200052:UI 00080031:TM
00180080:DS 00204000:LT 00280103:US 00080008:CS 00180060:DS 0040A525:SQ
00181190:DS 00280006:US 00080061:CS 00180050:DS 00089206:CS 0040A121:DA
00380020:DA 00281202:OW 00280009:AT 00209057:UL 00289145:SQ 00400280:ST
00281053:DS 00181151:IS 7FE00008:OF 00200012:IS 00181155:CS 00101000:LO
00181120:DS 00081190:UR 00380500:LO 00401001:SH 00080068:CS 00280301:CS
00181147:CS 00100030:DA 00201208:IS 0040A300:SQ 0040A123:PN 00289001:UL
00281102:US 00180084:DS 7FE00001:OV 00201204:IS 00080014:UI 00281040:CS
0040A124:UI 00080056:CS 00281052:DS 00201202:IS 00400260:SQ 00080012:DA
0008103E:LO 00400020:CS 00081060:PN 0040A30A:DS 00201200:IS 00400008:SQ
00400275:SQ 00080020:DA 00181250:SH 00100021:LO 00281199:UI 00289110:SQ
00081120:SQ 00181170:IS 00180086:IS 00104000:LT 00181111:DS 0008002A:DT
00408302:DS 00082218:SQ 00181130:DS 00201002:IS 00181060:DS 00200060:CS
00181030:LO 00081115:SQ 7FE00002:OV 00200020:CS 0020000E:UI 00080030:TM
00281050:DS 00181201:TM 00080050:SH 00080052:CS 00200011:IS 00280030:DS
00080033:TM 0040A073:SQ 00189087:FD 0040A504:SQ 00181000:LO 00081030:LO
00101020:DS 00180010:LO 00080054:AE 00400241:AE 00081032:SQ 00281051:DS
00181152:IS 00283010:SQ 00181312:CS 00080090:PN 00280002:US 0040A040:CS
00281041:DS 00181020:LO 00280034:IS 00081070:PN 0040A030:DT 00081050:PN
00100010:PN 00280106:US 00181040:LO 00080023:DA 00081150:UI 00281055:LO
00209221:SQ 00200010:SH 00400002:DA 00181200:DA 00089007:CS 00181063:DS
00089207:CS 00081155:UI 00100040:CS 00282114:CS 00180021:CS 00400007:LO
0040A010:CS 0040A160:UT 0040A195:SQ 00080081:ST 00283004:LO 00280014:US
00189004:CS 00100022:CS 00400244:DA 00081111:SQ 00400254:LO 00181140:CS
00281054:LO 00283000:SQ 00321032:PN 7FE00010:OW 00400245:TM 00100032:TM
00080060:CS 00089208:CS 00380021:TM 00201209:IS 00181210:SH 00102180:SH
00081160:IS 00380010:LO 00180024:LO 0040A493:CS 00180020:CS 00081048:PN
00080080:LO 00080064:CS 00081010:SH 00101001:PN 00321060:LO 00081040:LO
00080016:UI 00201040:LO 00100024:SQ 00181251:SH 00400009:SH 00101010:AS
00081110:SQ 00180023:CS 00081080:LO 00289002:UL 00180087:DS 0040A730:SQ
00180082:DS 00181088:IS 00289132:SQ 00280121:US 00400004:DA 00181314:DS
00200062:CS 00181316:DS 00080032:TM 00400555:SQ 00089205:CS 00282110:CS
00081140:SQ 00189073:FD 00280011:US 00181150:IS 00180088:DS 00180015:CS
00283006:OW 00181310:US 00080018:UI 00209222:SQ
""")

_Overlay_Vrs = {
    0x0010: "US", 0x0011: "US", 0x0022: "LO", 0x0040: "CS", 0x0045: "LO",
    0x0050: "SS", 0x0051: "US", 0x0100: "US", 0x0102: "US", 0x1500: "LO",
    0x3000: "OW",
}


def Dictionary_Vr(Tag: int) -> str:
    """the actual VR of a tag read from a data set that carries none"""
    Group, Element_Val = Tag >> 16, Tag & 0xFFFF
    if Element_Val == 0x0000:
        return "UL"  # Group Length.
    if Group & 1 and 0x0010 <= Element_Val <= 0x00FF:
        return "LO"  # Private Creator.
    if not Group & 1 and 0x6000 <= Group <= 0x60FF:
        return _Overlay_Vrs.get(Element_Val, "UN")
    return _Tag_Dictionary.get(Tag, "UN")


def Tag_Key(Tag: int) -> str:
    """The eight-digit form used by the JSON model and bulkdata references."""
    return f"{Tag:08X}"


def Tag_Label(Tag: int) -> str:
    """The `(gggg,eeee)` form used in diagnostics."""
    return f"({Tag >> 16:04X},{Tag & 0xFFFF:04X})"


# Errors

class MalformedDICOM(ValueError):
    """raised when a file cannot be parsed safely."""


class UnsupportedInstance(RuntimeError):
    """Raised when a valid instance cannot be served by this implementation,"""


class NoSuchResource(Exception):
    """raised when the addressed resource does not exist"""


class UnacceptableMedia(Exception):
    """Raised when no available representation satisfies the Accept header."""

# Data element decoding

class Encoding:
    """byte order and VR mode of one data set, with precompiled decoders for actual parsing"""

    __slots__ = ("Explicit", "Little", "_Tag", "_Short", "_Long")

    def __init__(Self, Explicit: bool, Little: bool):
        Order = "<" if Little else ">"
        Self.Explicit = Explicit
        Self.Little = Little
        Self._Tag = struct.Struct(Order + "HH")
        Self._Short = struct.Struct(Order + "H")
        Self._Long = struct.Struct(Order + "I")

    @classmethod
    def Of(Cls, Transfer_Syntax: str) -> "Encoding":
        if Transfer_Syntax == Implicit_Vr_Little_Endian:
            return Implicit_Little
        if Transfer_Syntax == Explicit_Vr_Big_Endian:
            return Explicit_Big
        # Encapsulated transfer syntaxes encode the data set as Explicit VR LE, so make a check on the syntax.
        return Explicit_Little

    def Tag_At(Self, Data: bytes, At: int) -> int:
        Group, Element_Val = Self._Tag.unpack_from(Data, At)
        return (Group << 16) | Element_Val

    def Short_At(Self, Data: bytes, At: int) -> int:
        return Self._Short.unpack_from(Data, At)[0]

    def Long_At(Self, Data: bytes, At: int) -> int:
        return Self._Long.unpack_from(Data, At)[0]


Explicit_Little = Encoding(True, True)
Implicit_Little = Encoding(False, True)
Explicit_Big = Encoding(True, False)


@dataclass(frozen=True)
class ElementHeader:
    """where one data element's value starts and how it is typed"""

    Tag: int
    Vr: Optional[str]
    Length: int
    Value_At: int

    @property
    def Undefined(Self) -> bool:
        return Self.Length == Undefined_Length

    @property
    def Next_At(Self) -> int:
        return Self.Value_At + Self.Length


def Read_Header(Data: bytes, At: int, Encoding_Inst: Encoding) -> ElementHeader:
    """Decode the header of the data element positioned at `At` and explain the caller the structure."""
    if At < 0 or At + 8 > len(Data):
        raise MalformedDICOM(f"a data element header at byte {At} runs past the end of the file")
    Tag = Encoding_Inst.Tag_At(Data, At)
    # Items and delimiters never carry a VR, whatever the data set's VR mode.
    if Tag >> 16 == 0xFFFE or not Encoding_Inst.Explicit:
        return ElementHeader(Tag, None, Encoding_Inst.Long_At(Data, At + 4), At + 8)
    Vr = Data[At + 4:At + 6].decode("ascii", "replace")
    if Vr not in Valid_Vrs:
        raise MalformedDICOM(f"{Vr!r} at byte {At + 4} is not a Value Representation")
    if Vr not in Long_Vrs:
        return ElementHeader(Tag, Vr, Encoding_Inst.Short_At(Data, At + 6), At + 8)
    if At + 12 > len(Data):
        raise MalformedDICOM(f"the {Vr} header at byte {At} runs past the end of the file")
    if Data[At + 6:At + 8] != b"\x00\x00":
        raise MalformedDICOM(f"the reserved bytes of the {Vr} value at byte {At} are not zero")
    return ElementHeader(Tag, Vr, Encoding_Inst.Long_At(Data, At + 8), At + 12)


def Walk_Past_Sequence(Data: bytes, At: int, Encoding_Inst: Encoding) -> int:
    """Return the offset just past an undefined-length sequence.

    Uses an explicit stack of the delimiters still to be matched rather than
    recursion. Delimiters are located by decoding element headers, never by
    searching for their bytes, which may legitimately occur inside a value.
    """
    Pending = [Sequence_Delimiter]
    while Pending:
        if len(Pending) > Max_Sequence_Depth:
            raise MalformedDICOM("sequences are nested more deeply than this server parses")
        Header = Read_Header(Data, At, Encoding_Inst)
        if Header.Tag in Delimiters:
            if Header.Length:
                raise MalformedDICOM("a sequence or item delimiter has a non-zero length")
            if Header.Tag != Pending[-1]:
                raise MalformedDICOM("sequence and item delimiters are interleaved")
            Pending.pop()
            At = Header.Value_At
        elif Header.Undefined:
            Pending.append(Item_Delimiter if Header.Tag == Item else Sequence_Delimiter)
            At = Header.Value_At
        else:
            if Header.Next_At > len(Data):
                raise MalformedDICOM("a value inside a sequence runs past the end of the file")
            At = Header.Next_At
    return At


def Walk_Top_Level(Data: bytes, At: int, Encoding_Inst: Encoding) -> Iterator[Tuple[ElementHeader, bytes]]:
    """Yield top-level elements, stepping over sequences, ending at Pixel Data."""
    while At + 8 <= len(Data):
        Header = Read_Header(Data, At, Encoding_Inst)
        if Header.Tag >> 16 == 0xFFFE:
            raise MalformedDICOM("an item or delimiter appears at the top level of a data set")
        if Header.Undefined:
            if Header.Tag in Pixel_Data_Tags:
                yield Header, b""
                return
            if Header.Vr not in ("SQ", None):
                raise MalformedDICOM(f"VR {Header.Vr} may not have an undefined length")
            At = Walk_Past_Sequence(Data, Header.Value_At, Encoding_Inst)
            continue
        if Header.Next_At > len(Data):
            raise MalformedDICOM(f"the value of {Tag_Label(Header.Tag)} runs past the end of the file")
        yield Header, Data[Header.Value_At:Header.Next_At]
        if Header.Tag in Pixel_Data_Tags:
            return
        At = Header.Next_At

# Encapsulated Pixel Data

_Item_Header = struct.Struct("<HHI")


@dataclass(frozen=True)
class Fragment:
    """One item of encapsulated Pixel Data."""

    #: Offset of this item's tag from the first fragment's tag, which is the
    #: origin that the Basic Offset Table counts from.
    Position: int
    At: int
    Length: int


@dataclass(frozen=True)
class Encapsulated:
    Offsets: Tuple[int, ...] = ()
    Fragments: Tuple[Fragment, ...] = ()
    Ends_At: int = 0


def Read_Encapsulated(Data: bytes, At: int) -> Encapsulated:
    """Read the items of encapsulated Pixel Data, which are always little endian."""

    def Next_Item(Position: int) -> Tuple[int, int, int]:
        if Position + 8 > len(Data):
            raise MalformedDICOM("encapsulated Pixel Data ends without a delimiter")
        Group, Element_Val, Length = _Item_Header.unpack_from(Data, Position)
        Tag = (Group << 16) | Element_Val
        if Tag == Sequence_Delimiter:
            if Length:
                raise MalformedDICOM("the Pixel Data delimiter has a non-zero length")
            return Tag, Position + 8, 0
        if Tag != Item or Length == Undefined_Length:
            raise MalformedDICOM("encapsulated Pixel Data contains an invalid item")
        if Position + 8 + Length > len(Data):
            raise MalformedDICOM("an encapsulated Pixel Data item runs past the end of the file")
        return Tag, Position + 8, Length

    # the first item is the Basic Offset Table, an index rather than data
    Tag, Value_At, Length = Next_Item(At)
    if Tag == Sequence_Delimiter:
        return Encapsulated(Ends_At=Value_At)
    if Length % 4:
        raise MalformedDICOM("the Basic Offset Table length is not a multiple of four")
    Offsets = struct.unpack_from(f"<{Length // 4}I", Data, Value_At) if Length else ()

    Origin = Value_At + Length
    Position = Origin
    Fragments: List[Fragment] = []
    while True:
        Tag, Value_At, Length = Next_Item(Position)
        if Tag == Sequence_Delimiter:
            return Encapsulated(tuple(Offsets), tuple(Fragments), Value_At)
        Fragments.append(Fragment(Position - Origin, Value_At, Length))
        Position = Value_At + Length

# Elements and whole data sets

@dataclass(frozen=True)
class Element:
    """one parsed data element. Bulk values are located, never copied"""

    Tag: int
    Vr: str
    Value: bytes = b""
    Items: Tuple[Tuple["Element", ...], ...] = ()
    Bulk: bool = False
    At: int = 0
    Length: int = 0
    Fragments: Tuple[Fragment, ...] = ()
    Encapsulated: bool = False

    @property
    def Key(Self) -> str:
        return Tag_Key(Self.Tag)

    @property
    def Is_Bulk(Self) -> bool:
        return Self.Bulk or Self.Encapsulated

    @property
    def Has_Bytes(Self) -> bool:
        """Whether this bulk element has a value worth delivering."""
        return bool(Self.Fragments) if Self.Encapsulated else Self.Length > 0


Elements = Tuple[Element, ...]


def Parse_Items(Data: bytes, At: int, Stop: int, Encoding_Inst: Encoding,
                Depth: int) -> Tuple[Tuple[Elements, ...], int]:
    """read sequence items up to `Stop` or a Sequence Delimitation Item"""
    Items: List[Elements] = []
    while At < Stop:
        Header = Read_Header(Data, At, Encoding_Inst)
        if Header.Tag == Sequence_Delimiter:
            if Header.Length:
                raise MalformedDICOM("a sequence delimiter has a non-zero length")
            return tuple(Items), Header.Value_At
        if Header.Tag != Item:
            raise MalformedDICOM(f"expected a sequence item, found {Tag_Label(Header.Tag)}")
        if Header.Undefined:
            Item_Val, At, Ended = Parse_Elements(Data, Header.Value_At, len(Data), Encoding_Inst, Depth + 1)
            if Ended != Item_Delimiter:
                raise MalformedDICOM("an undefined-length item was not terminated")
        else:
            if Header.Next_At > Stop:
                raise MalformedDICOM("a sequence item runs past the end of its sequence")
            Item_Val, _, _ = Parse_Elements(Data, Header.Value_At, Header.Next_At, Encoding_Inst, Depth + 1)
            At = Header.Next_At
        Items.append(Item_Val)
    return tuple(Items), At


def Parse_Elements(Data: bytes, At: int, Stop: int, Encoding_Inst: Encoding,
                   Depth: int = 0) -> Tuple[Elements, int, Optional[int]]:
    """Read a data set, recursing into sequences.

    Returns the elements, the offset just past them, and the delimiter tag that
    ended the data set (None when it simply reached `Stop`).
    """
    if Depth > Max_Sequence_Depth:
        raise MalformedDICOM("sequences are nested more deeply than this server parses")
    Elements_List: List[Element] = []
    while At + 8 <= Stop:
        Header = Read_Header(Data, At, Encoding_Inst)
        if Header.Tag in Delimiters:
            if Header.Length:
                raise MalformedDICOM("a sequence or item delimiter has a non-zero length")
            return tuple(Elements_List), Header.Value_At, Header.Tag
        if Header.Tag == Item:
            raise MalformedDICOM("a sequence item appears outside a sequence")
        Vr = Header.Vr or Dictionary_Vr(Header.Tag)

        if Header.Undefined:
            if Header.Tag in Pixel_Data_Tags:
                Encapsulated_Val = Read_Encapsulated(Data, Header.Value_At)
                Elements_List.append(Element(
                    Header.Tag, Vr if Vr in Bulk_Vrs else "OB",
                    Fragments=Encapsulated_Val.Fragments, Encapsulated=True,
                ))
                At = Encapsulated_Val.Ends_At
                continue
            if Vr not in ("SQ", "UN"):
                raise MalformedDICOM(f"VR {Vr} may not have an undefined length")
            # [PS3.5]: an undefined-length UN value is an Implicit VR LE sequence.
            Inner = Encoding_Inst if Vr == "SQ" else Implicit_Little
            Items, At = Parse_Items(Data, Header.Value_At, len(Data), Inner, Depth)
            Elements_List.append(Element(Header.Tag, "SQ", Items=Items))
            continue

        if Header.Next_At > Stop:
            raise MalformedDICOM(f"the value of {Tag_Label(Header.Tag)} runs past its data set")
        if Vr == "SQ":
            Items, _ = Parse_Items(Data, Header.Value_At, Header.Next_At, Encoding_Inst, Depth)
            Elements_List.append(Element(Header.Tag, "SQ", Items=Items))
        elif Vr in Bulk_Vrs:
            Elements_List.append(Element(Header.Tag, Vr, Bulk=True,
                                         At=Header.Value_At, Length=Header.Length))
        else:
            Elements_List.append(Element(Header.Tag, Vr, Value=Data[Header.Value_At:Header.Next_At]))
        At = Header.Next_At
    return tuple(Elements_List), At, None

# Part 10 files

@dataclass(frozen=True)
class Part10File:
    """a file's bytes together with where and how its data set is encoded"""

    Buffer: bytes
    Dataset_At: int
    Transfer_Syntax_Uid: str
    Meta: Elements

    @property
    def Encoding(Self) -> Encoding:
        return Encoding.Of(Self.Transfer_Syntax_Uid)


def Read_Part10(File_Path: str) -> Part10File:
    """Read a Part 10 file, inflating a deflated data set in place.

    For Deflated Explicit VR Little Endian the buffer is the File Meta
    Information followed by the *inflated* data set, so that every offset in
    this module addresses one buffer regardless of transfer syntax.
    """
    with open(File_Path, "rb") as Stream:
        Data = Stream.read()
    if len(Data) < 132 or Data[128:132] != b"DICM":
        raise MalformedDICOM("not a DICOM Part 10 file (no DICM prefix)")

    At = 132
    Limit = len(Data)
    # File Meta Information is always Explicit VR Little Endian, and its Group
    # Length delimits the group exactly when it is present.
    Opening = Read_Header(Data, At, Explicit_Little)
    if Opening.Tag == File_Meta_Group_Length:
        if Opening.Length != 4:
            raise MalformedDICOM("File Meta Information Group Length is not four bytes")
        Limit = min(Limit, Opening.Next_At + Explicit_Little.Long_At(Data, Opening.Value_At))

    Meta: List[Element] = []
    Transfer_Syntax = ""
    while At + 8 <= Limit:
        Header = Read_Header(Data, At, Explicit_Little)
        if Header.Tag >> 16 != 0x0002:
            break
        if Header.Undefined or Header.Next_At > len(Data):
            raise MalformedDICOM("a File Meta Information element has an invalid length")
        Value = Data[Header.Value_At:Header.Next_At]
        Meta.append(Element(Header.Tag, Header.Vr or "UN", Value=Value))
        if Header.Tag == Transfer_Syntax_Uid:
            Transfer_Syntax = Ascii_Text(Value)
        At = Header.Next_At

    if not Is_Uid(Transfer_Syntax):
        raise MalformedDICOM("the File Meta Information has no valid Transfer Syntax UID")
    if Transfer_Syntax == Deflated_Explicit_Vr_Little_Endian:
        try:
            Data = Data[:At] + zlib.decompress(Data[At:], -zlib.MAX_WBITS)
        except zlib.error as Error:
            raise MalformedDICOM(f"the deflated data set could not be inflated: {Error}") from Error
    return Part10File(Data, At, Transfer_Syntax, tuple(Meta))


@dataclass(frozen=True)
class ParsedInstance:
    """A fully parsed instance, with every offset relative to `Buffer`."""

    File_Path: str
    Buffer: bytes
    Transfer_Syntax_Uid: str
    Meta: Elements
    Dataset: Elements
    Encoding: Encoding
    Charset: str


def Parse_Instance(File_Path: str) -> ParsedInstance:
    """Parse a file completely, including sequences, for metadata and bulkdata."""
    Stored = Read_Part10(File_Path)
    Encoding_Inst = Stored.Encoding
    Dataset, _, _ = Parse_Elements(Stored.Buffer, Stored.Dataset_At, len(Stored.Buffer), Encoding_Inst)
    return ParsedInstance(
        File_Path=File_Path,
        Buffer=Stored.Buffer,
        Transfer_Syntax_Uid=Stored.Transfer_Syntax_Uid,
        Meta=Stored.Meta,
        Dataset=Dataset,
        Encoding=Encoding_Inst,
        Charset=Charset_Of(Dataset),
    )


def Charset_Of(Dataset: Elements) -> str:
    for Element_Val in Dataset:
        if Element_Val.Tag == Specific_Character_Set:
            # Code extensions list several terms; the first one this server
            # recognises decodes the unextended portion of every string.
            for Term in Ascii_Text(Element_Val.Value).split("\\"):
                Codec = Character_Sets.get(Term.strip())
                if Codec:
                    return Codec
            break
    return "ascii"

# Small value helpers

def Ascii_Text(Raw: bytes) -> str:
    """Decode a value the standard defines over the default repertoire,"""
    return Raw.rstrip(b"\x00 ").decode("ascii", "replace")


def Is_Uid(Text: str) -> bool:
    """Whether `Text` is a syntactically valid UID: dot-separated digit runs."""
    if not Text or len(Text) > 64:
        return False
    for Component in Text.split("."):
        if not Component.isascii() or not Component.isdigit():
            return False
        if len(Component) > 1 and Component.startswith("0"):
            return False
    return True


class TopLevelValues:
    """Typed access to the handful of values the catalog reads from a data set."""

    def __init__(Self, Values: Dict[int, bytes], Encoding_Inst: Encoding):
        Self._Values = Values
        Self._Encoding = Encoding_Inst

    def Text(Self, Tag: int) -> str:
        return Ascii_Text(Self._Values.get(Tag, b""))

    def Unsigned(Self, Tag: int, Default: int = 0) -> int:
        Raw = Self._Values.get(Tag, b"")
        return Self._Encoding.Short_At(Raw, 0) if len(Raw) >= 2 else Default

    def Integer(Self, Tag: int, Default: int) -> int:
        """The first value of an Integer String, or `Default` when absent."""
        Raw = Self._Values.get(Tag)
        if Raw is None:
            return Default
        Text = Ascii_Text(Raw).split("\\", 1)[0].strip()
        if not Text:
            return Default
        try:
            return int(Text)
        except ValueError as Error:
            raise MalformedDICOM(f"{Tag_Label(Tag)} is not an integer: {Text!r}") from Error

# Instances and the catalog

@dataclass(frozen=True)
class Instance:
    """what the catalog keeps resident for one instance."""

    File_Path: str
    File_Size: int
    Study_Uid: str
    Series_Uid: str
    Instance_Uid: str
    Sop_Class_Uid: str
    Transfer_Syntax_Uid: str
    Instance_Number: int = 0
    Number_Of_Frames: int = 1
    Rows: int = 0
    Columns: int = 0
    Samples_Per_Pixel: int = 1
    Bits_Allocated: int = 0
    Pixel_Data_At: int = 0
    Pixel_Data_Length: int = 0
    Encapsulated: bool = False
    Offsets: Tuple[int, ...] = ()
    Fragments: Tuple[Fragment, ...] = ()

    @property
    def Uids(Self) -> Tuple[str, str, str]:
        return (Self.Study_Uid, Self.Series_Uid, Self.Instance_Uid)

    @property
    def Has_Pixel_Data(Self) -> bool:
        return Self.Pixel_Data_At > 0

    @property
    def Order(Self) -> Tuple[int, str]:
        return (Self.Instance_Number, Self.Instance_Uid)


#: The top-level values `Scan_Instance` keeps; everything else is read on demand.
_Indexed_Tags = frozenset({
    Sop_Class_Uid, Sop_Instance_Uid, Study_Instance_Uid, Series_Instance_Uid,
    Instance_Number, Samples_Per_Pixel, Number_Of_Frames, Rows, Columns,
    Bits_Allocated,
})


def Scan_Instance(File_Path: str) -> Instance:
    """Read only what the catalog and frame extraction need, then stop."""
    Stored = Read_Part10(File_Path)
    Encoding_Inst = Stored.Encoding
    Collected: Dict[int, bytes] = {}
    Pixel_Data_At = Pixel_Data_Length = 0
    Encapsulated_Val = Encapsulated()
    Is_Encapsulated = False

    for Header, Value in Walk_Top_Level(Stored.Buffer, Stored.Dataset_At, Encoding_Inst):
        if Header.Tag in _Indexed_Tags:
            Collected[Header.Tag] = Value
        if Header.Tag in Pixel_Data_Tags:
            Pixel_Data_At = Header.Value_At
            if Header.Undefined:
                Is_Encapsulated = True
                Encapsulated_Val = Read_Encapsulated(Stored.Buffer, Header.Value_At)
            else:
                Pixel_Data_Length = Header.Length
            break

    Values = TopLevelValues(Collected, Encoding_Inst)
    Frames = Values.Integer(Number_Of_Frames, 1)
    if Frames < 1:
        raise MalformedDICOM("Number of Frames is not a positive integer")

    Instance_Inst = Instance(
        File_Path=File_Path,
        File_Size=os.path.getsize(File_Path),
        Study_Uid=Values.Text(Study_Instance_Uid),
        Series_Uid=Values.Text(Series_Instance_Uid),
        Instance_Uid=Values.Text(Sop_Instance_Uid),
        Sop_Class_Uid=Values.Text(Sop_Class_Uid),
        Transfer_Syntax_Uid=Stored.Transfer_Syntax_Uid,
        Instance_Number=Values.Integer(Instance_Number, 0),
        Number_Of_Frames=Frames,
        Rows=Values.Unsigned(Rows),
        Columns=Values.Unsigned(Columns),
        Samples_Per_Pixel=Values.Unsigned(Samples_Per_Pixel, 1),
        Bits_Allocated=Values.Unsigned(Bits_Allocated),
        Pixel_Data_At=Pixel_Data_At,
        Pixel_Data_Length=Pixel_Data_Length,
        Encapsulated=Is_Encapsulated,
        Offsets=Encapsulated_Val.Offsets,
        Fragments=Encapsulated_Val.Fragments,
    )
    _Check_File_Meta_Agrees(Stored.Meta, Instance_Inst)
    return Instance_Inst


def _Check_File_Meta_Agrees(Meta: Elements, Instance_Inst: Instance) -> None:
    """The File Meta Information restates two data-set UIDs; they must match."""
    Stated = {Element_Val.Tag: Ascii_Text(Element_Val.Value) for Element_Val in Meta}
    for Tag, From_Dataset, Name in (
        (Media_Sop_Class_Uid, Instance_Inst.Sop_Class_Uid, "SOP Class"),
        (Media_Sop_Instance_Uid, Instance_Inst.Instance_Uid, "SOP Instance"),
    ):
        Declared = Stated.get(Tag, "")
        if Declared and Declared != From_Dataset:
            raise MalformedDICOM(f"Media Storage {Name} UID does not match the data set")


@dataclass(frozen=True)
class CatalogFailure:
    File_Path: str
    Reason: str


@dataclass
class SeriesEntry:
    Uid: str
    Instances: List[Instance] = field(default_factory=list)


@dataclass
class StudyEntry:
    Uid: str
    Series: Dict[str, SeriesEntry] = field(default_factory=dict)


class Catalog:
    """A study/series/instance hierarchy built once from a folder three."""

    def __init__(Self, Root: str, Recursive: bool = True):
        Self.Root = os.path.realpath(Root)
        Self.Recursive = Recursive
        Self.Studies: Dict[str, StudyEntry] = {}
        Self.Instances: Dict[Tuple[str, str, str], Instance] = {}
        Self.Failures: List[CatalogFailure] = []
        Self.Reload()

    def Reload(Self) -> None:
        Studies: Dict[str, StudyEntry] = {}
        Instances: Dict[Tuple[str, str, str], Instance] = {}
        Failures: List[CatalogFailure] = []
        for File_Path in Self._Files():
            try:
                Instance_Inst = Scan_Instance(File_Path)
                if not all(Is_Uid(Uid) for Uid in Instance_Inst.Uids):
                    raise MalformedDICOM("the Study, Series, or SOP Instance UID is missing or invalid")
                Already = Instances.get(Instance_Inst.Uids)
                if Already is not None:
                    raise MalformedDICOM(
                        f"this SOP Instance UID is already catalogued from {Already.File_Path}"
                    )
                Instances[Instance_Inst.Uids] = Instance_Inst
                Study = Studies.setdefault(Instance_Inst.Study_Uid, StudyEntry(Instance_Inst.Study_Uid))
                Series = Study.Series.setdefault(Instance_Inst.Series_Uid,
                                                 SeriesEntry(Instance_Inst.Series_Uid))
                Series.Instances.append(Instance_Inst)
            except (OSError, MalformedDICOM) as Error:
                Failures.append(CatalogFailure(File_Path, str(Error)))
        for Study in Studies.values():
            for Series in Study.Series.values():
                # When we are with multiple instances in a series, sort them by instance number.
                Series.Instances.sort(key=lambda Instance_Item: Instance_Item.Order)
        Self.Studies = Studies
        Self.Instances = Instances
        Self.Failures = Failures

    def _Files(Self) -> List[str]:
        Root_Path = Path(Self.Root)
        Found = Root_Path.rglob("*") if Self.Recursive else Root_Path.glob("*")
        return [str(Path_Obj) for Path_Obj in sorted(Found) if Path_Obj.is_file()]

    def Study_Instances(Self, Study_Uid: str) -> List[Instance]:
        Study = Self.Studies.get(Study_Uid)
        if Study is None:
            return []
        return [Instance_Item for Uid in sorted(Study.Series) for Instance_Item in Study.Series[Uid].Instances]

    def Series_Instances(Self, Study_Uid: str, Series_Uid: str) -> List[Instance]:
        Study = Self.Studies.get(Study_Uid)
        Series = Study.Series.get(Series_Uid) if Study else None
        return list(Series.Instances) if Series else []

    def Instance(Self, Study_Uid: str, Series_Uid: str, Instance_Uid: str) -> Optional[Instance]:
        return Self.Instances.get((Study_Uid, Series_Uid, Instance_Uid))

    @property
    def Study_Count(Self) -> int:
        return len(Self.Studies)

    @property
    def Series_Count(Self) -> int:
        return sum(len(Study.Series) for Study in Self.Studies.values())

    @property
    def Instance_Count(Self) -> int:
        return len(Self.Instances)


# DICOM JSON Model [PS3.18 Annex F]

def _Person_Name(Text: str) -> Dict[str, str]:
    Parts = Text.split("=")
    Labels = ("Alphabetic", "Ideographic", "Phonetic")
    return {Label: Part for Label, Part in zip(Labels, Parts) if Part}


def _Text_Values(Vr: str, Raw: bytes, Charset: str) -> List:
    Text = Raw.decode(Charset, "replace").rstrip("\x00")
    Components = [Text] if Vr in Single_Value_Vrs else Text.split("\\")
    Values: List = []
    for Component in Components:
        Component = Component.rstrip() if Vr in ("LT", "ST", "UT") else Component.strip()
        if Vr == "IS":
            Values.append(int(Component) if Component.lstrip("+-").isdigit() else (Component or None))
        elif Vr == "DS":
            try:
                Values.append(float(Component))
            except ValueError:
                Values.append(Component or None)
        elif Vr == "PN":
            Values.append(_Person_Name(Component))
        else:
            Values.append(Component)
    return Values


def Element_Values(Element_Inst: Element, Charset: str, Encoding_Inst: Encoding) -> Optional[List]:
    """The JSON form of an inline value, or None when there is nothing to emit."""
    Raw = Element_Inst.Value
    if not Raw:
        return None
    Order = "<" if Encoding_Inst.Little else ">"
    if Element_Inst.Vr == "AT":
        Count = len(Raw) // 4
        Numbers = struct.unpack(Order + "HH" * Count, Raw[:Count * 4])
        return [f"{Numbers[I]:04X}{Numbers[I + 1]:04X}" for I in range(0, len(Numbers), 2)]
    Code = Binary_Vrs.get(Element_Inst.Vr)
    if Code:
        Size = struct.calcsize(Code)
        Count = len(Raw) // Size
        if not Count:
            return None
        return list(struct.unpack(Order + f"{Count}{Code}", Raw[:Count * Size]))
    return _Text_Values(Element_Inst.Vr, Raw, Charset)


def Elements_To_Json(Elements_List: Elements, Charset: str, Encoding_Inst: Encoding,
                     Bulk_Base: str, Path_Tuple: Tuple[str, ...] = ()) -> Dict[str, Dict]:
    """Convert elements to the DICOM JSON Model, bulk values becoming URIs."""
    Document: Dict[str, Dict] = {}
    for Element_Inst in Elements_List:
        Entry: Dict = {"vr": Element_Inst.Vr}
        if Element_Inst.Vr == "SQ":
            Items = [
                Elements_To_Json(Item_Val, Charset, Encoding_Inst, Bulk_Base,
                                 Path_Tuple + (Element_Inst.Key, str(Number)))
                for Number, Item_Val in enumerate(Element_Inst.Items, 1)
            ]
            if Items:
                Entry["Value"] = Items
        elif Element_Inst.Is_Bulk:
            # [PS3.18 10.4.1.1.2]: metadata carries a reference, never the bytes.
            if Element_Inst.Has_Bytes:
                Entry["BulkDataURI"] = "/".join((Bulk_Base,) + Path_Tuple + (Element_Inst.Key,))
        else:
            Values = Element_Values(Element_Inst, Charset, Encoding_Inst)
            if Values is not None:
                Entry["Value"] = Values
        Document[Element_Inst.Key] = Entry
    return Document


def Instance_To_Json(Parsed: ParsedInstance, Bulk_Base: str) -> Dict[str, Dict]:
    """Instance metadata, with the File Meta Information group retained.

    Keeping (0002,0010) is an addition to [PS3.18 Annex F]: a client that receives frames
    from this server cannot interpret them without knowing the stored transfer
    syntax, and this server never transcodes.
    """
    Document = Elements_To_Json(Parsed.Meta, "ascii", Explicit_Little, Bulk_Base)
    Document.update(Elements_To_Json(Parsed.Dataset, Parsed.Charset, Parsed.Encoding, Bulk_Base))
    return Document

# Bulkdata addressing

_Tag_Pattern = re.compile(r"[0-9A-Fa-f]{8}\Z")


def Parse_Bulkdata_Reference(Text: str) -> Tuple:
    """Parse `TTTTTTTT/1/TTTTTTTT` into alternating tags and 1-based item numbers."""
    Components = [Part for Part in Text.split("/") if Part]
    if not Components or len(Components) % 2 == 0:
        raise ValueError("a bulkdata reference alternates tags and item numbers, ending with a tag")
    Reference: List = []
    for Position, Component in enumerate(Components):
        if Position % 2:
            if not Component.isascii() or not Component.isdigit() or int(Component) < 1:
                raise ValueError(f"{Component!r} is not a positive item number")
            Reference.append(int(Component))
        else:
            if not _Tag_Pattern.match(Component):
                raise ValueError(f"{Component!r} is not an eight-digit hexadecimal tag")
            Reference.append(int(Component, 16))
    return tuple(Reference)


def Reference_To_Path(Reference: Sequence) -> str:
    return "/".join(Tag_Key(Part) if Position % 2 == 0 else str(Part)
                    for Position, Part in enumerate(Reference))


def Find_Bulkdata(Dataset: Elements, Reference: Sequence) -> Element:
    """Resolve a parsed bulkdata reference to its element and eventually raise NoSuchResource if missing."""
    Elements_List = Dataset
    Position = 0
    while True:
        Tag = Reference[Position]
        Match = next((Element_Inst for Element_Inst in Elements_List if Element_Inst.Tag == Tag), None)
        if Match is None:
            raise NoSuchResource(f"{Tag_Label(Tag)} is not present in the instance")
        if Position + 1 == len(Reference):
            if not Match.Is_Bulk:
                raise NoSuchResource(f"{Tag_Label(Tag)} is not a bulkdata attribute")
            return Match
        Item_Number = Reference[Position + 1]
        if Match.Vr != "SQ" or Item_Number > len(Match.Items):
            raise NoSuchResource("the bulkdata reference addresses a missing sequence item")
        Elements_List = Match.Items[Item_Number - 1]
        Position += 2


def Collect_Bulkdata(Elements_List: Elements, Path_Tuple: Tuple[str, ...] = ()) -> List[Tuple[str, Element]]:
    """Every bulk value in the data set, depth first, with its reference path."""
    Found: List[Tuple[str, Element]] = []
    for Element_Inst in Elements_List:
        if Element_Inst.Vr == "SQ":
            for Number, Item_Val in enumerate(Element_Inst.Items, 1):
                Found.extend(Collect_Bulkdata(Item_Val, Path_Tuple + (Element_Inst.Key, str(Number))))
        elif Element_Inst.Is_Bulk and Element_Inst.Has_Bytes:
            Found.append(("/".join(Path_Tuple + (Element_Inst.Key,)), Element_Inst))
    return Found


def Bulkdata_Bytes(Element_Inst: Element, Buffer: bytes) -> bytes:
    if Element_Inst.Encapsulated:
        return b"".join(Buffer[Part.At:Part.At + Part.Length] for Part in Element_Inst.Fragments)
    return Buffer[Element_Inst.At:Element_Inst.At + Element_Inst.Length]

# frames.

def Parse_Frame_List(Text: str, Number_Of_Frames_Val: int) -> List[int]:
    """Parse the {framelist} path segment: 1-based, comma separated."""
    Frames: List[int] = []
    for Component in Text.split(","):
        if not Component.isascii() or not Component.isdigit() or int(Component) < 1:
            raise ValueError("frame numbers shall be positive integers separated by commas")
        Number = int(Component)
        if Number > Number_Of_Frames_Val:
            raise NoSuchResource(f"frame {Number} does not exist; the instance has {Number_Of_Frames_Val}")
        Frames.append(Number)
    if not Frames:
        raise ValueError("the frame list shall not be empty")
    return Frames


def Frame_Media_Type(Instance_Inst: Instance) -> str:
    """The media type in which a frame of this instance can be delivered."""
    if not Instance_Inst.Encapsulated:
        return Octet_Stream_Media_Type
    Media_Type = Encapsulated_Media_Types.get(Instance_Inst.Transfer_Syntax_Uid)
    if Media_Type is None:
        raise UnsupportedInstance(
            f"no media type is defined for frames stored as {Instance_Inst.Transfer_Syntax_Uid}"
        )
    return Media_Type


def Extract_Frame(Instance_Inst: Instance, Buffer: bytes, Number: int) -> bytes:
    """Return the stored bytes of one frame, without decoding them.

    If memory allocation fails, retry reading over 5 seconds before raising an error.
    """
    if not Instance_Inst.Has_Pixel_Data:
        raise UnsupportedInstance("the instance has no Pixel Data")
    if Instance_Inst.Encapsulated:
        return _Encapsulated_Frame(Instance_Inst, Buffer, Number)
    if Instance_Inst.Transfer_Syntax_Uid not in Native_Transfer_Syntaxes:
        raise UnsupportedInstance(
            f"Transfer Syntax {Instance_Inst.Transfer_Syntax_Uid} is neither native nor encapsulated"
        )
    if min(Instance_Inst.Rows, Instance_Inst.Columns, Instance_Inst.Bits_Allocated) <= 0:
        raise UnsupportedInstance("Rows, Columns, and Bits Allocated are required to locate a frame")
    Bits = Instance_Inst.Rows * Instance_Inst.Columns * Instance_Inst.Samples_Per_Pixel * Instance_Inst.Bits_Allocated
    if Bits % 8:
        # Returning such a frame alone would mean repacking bits, and the result
        # would no longer be the stored bytes.
        raise UnsupportedInstance("frames of this instance are not byte aligned")
    Size = Bits // 8
    Start = Instance_Inst.Pixel_Data_At + (Number - 1) * Size
    if Start + Size > Instance_Inst.Pixel_Data_At + Instance_Inst.Pixel_Data_Length:
        raise UnsupportedInstance("Pixel Data is shorter than Number of Frames requires")
    return Buffer[Start:Start + Size]


def _Encapsulated_Frame(Instance_Inst: Instance, Buffer: bytes, Number: int) -> bytes:
    Fragments = Instance_Inst.Fragments
    if not Fragments:
        raise UnsupportedInstance("encapsulated Pixel Data contains no fragments")
    Offsets = Instance_Inst.Offsets
    if len(Offsets) == Instance_Inst.Number_Of_Frames:
        Start = Offsets[Number - 1]
        Stop = Offsets[Number] if Number < len(Offsets) else None
        Chosen = [F for F in Fragments if F.Position >= Start and (Stop is None or F.Position < Stop)]
    elif len(Fragments) == Instance_Inst.Number_Of_Frames:
        Chosen = [Fragments[Number - 1]]
    elif Instance_Inst.Number_Of_Frames == 1:
        Chosen = list(Fragments)
    else:
        raise UnsupportedInstance(
            "multi-frame encapsulated Pixel Data has neither a Basic Offset Table "
            "nor one fragment per frame, so frames cannot be delimited"
        )
    if not Chosen:
        raise UnsupportedInstance(f"no encapsulated fragment corresponds to frame {Number}")
    return b"".join(Buffer[Part.At:Part.At + Part.Length] for Part in Chosen)

# Content negotiation

@dataclass(frozen=True)
class Representation:
    """one way this server can deliver a resource"""

    Media_Type: str
    Multipart: bool = True
    Transfer_Syntax: Optional[str] = None


@dataclass(frozen=True)
class MediaRange:
    """one entry of an Accept header"""

    Media_Type: str
    Params: Tuple[Tuple[str, str], ...]
    Quality: float
    Position: int

    def Param(Self, Name: str) -> Optional[str]:
        for Key, Value in Self.Params:
            if Key == Name:
                return Value
        return None

    @property
    def Precision(Self) -> int:
        """How specific this media range is; the most specific one takes precedence."""
        if Self.Media_Type == "*/*":
            return 0
        if Self.Media_Type.endswith("/*"):
            return 1
        return 2 + len(Self.Params)


def Split_Unquoted(Text: str, Separator: str) -> List[str]:
    """Split on `Separator`, ignoring separators inside a quoted string."""
    Pieces: List[str] = []
    Current: List[str] = []
    Quoted = False
    Escaped = False
    for Character in Text:
        if Escaped:
            Current.append(Character)
            Escaped = False
        elif Quoted and Character == "\\":
            Escaped = True
        elif Character == '"':
            Quoted = not Quoted
        elif Character == Separator and not Quoted:
            Pieces.append("".join(Current))
            Current = []
        else:
            Current.append(Character)
    Pieces.append("".join(Current))
    return Pieces


def Parse_Media_Range(Text: str, Position: int) -> MediaRange:
    Fields = Split_Unquoted(Text, ";")
    Media_Type = Fields[0].strip().lower()
    Parts = Media_Type.split("/")
    if len(Parts) != 2 or not all(Parts):
        raise ValueError(f"{Text.strip()!r} is not a valid media range")
    Quality = 1.0
    Params: List[Tuple[str, str]] = []
    for Field_Text in Fields[1:]:
        if not Field_Text.strip():
            continue
        Name, Separator, Value = Field_Text.partition("=")
        if not Separator:
            raise ValueError("Accept parameters shall be written name=value")
        Name = Name.strip().lower()
        Value = Value.strip()
        if Name == "q":
            try:
                Quality = float(Value)
            except ValueError as Error:
                raise ValueError("Accept q values shall be numbers between 0 and 1") from Error
            if not 0.0 <= Quality <= 1.0:
                raise ValueError("Accept q values shall be between 0 and 1")
        else:
            Params.append((Name, Value.lower() if Name == "type" else Value))
    return MediaRange(Media_Type, tuple(Params), Quality, Position)


def Parse_Accept(Header: str) -> List[MediaRange]:
    """Parse an Accept header (or `accept` query value) into media ranges."""
    Ranges = [Parse_Media_Range(Text, Position)
              for Position, Text in enumerate(Split_Unquoted(Header, ","))]
    if not Ranges:
        raise ValueError("Accept shall list at least one media range")
    return Ranges


def _Matches(Media_Range_Inst: MediaRange, Offer: Representation) -> bool:
    Wildcard = Media_Range_Inst.Media_Type == "*/*"
    Wanted_Type = Media_Range_Inst.Param("type")
    if Offer.Multipart:
        if not Wildcard and Media_Range_Inst.Media_Type != "multipart/related":
            return False
        if Wanted_Type is not None and Wanted_Type != Offer.Media_Type:
            return False
    else:
        if not Wildcard:
            Major = Offer.Media_Type.split("/", 1)[0]
            if Media_Range_Inst.Media_Type not in (Offer.Media_Type, f"{Major}/*"):
                return False
        if Wanted_Type is not None:
            return False
    Transfer_Syntax = Media_Range_Inst.Param("transfer-syntax")
    if Transfer_Syntax is not None and Transfer_Syntax != "*":
        return Transfer_Syntax == Offer.Transfer_Syntax
    return True


def Quality_For(Ranges: Sequence[MediaRange], Offer: Representation) -> float:
    """The q value of the most specific matching range."""
    Matching = [(Item_Val.Precision, -Item_Val.Position, Item_Val.Quality)
                for Item_Val in Ranges if _Matches(Item_Val, Offer)]
    return max(Matching)[2] if Matching else 0.0


def Acceptable(Accept_Header: Optional[str], Accept_Query: Optional[str],
               Offers: Sequence[Representation]) -> List[Representation]:
    """Every offered representation the client is willing to receive.

    A missing Accept header is treated as `*/*`. The `accept` query
    parameter is evaluated first, as [PS3.18] requires, but can only narrow what
    the Accept header already allows.
    """
    Ranges = Parse_Accept(Accept_Header if Accept_Header else "*/*")
    Ranked = [(Quality_For(Ranges, Offer), Position, Offer)
              for Position, Offer in enumerate(Offers)]
    # Highest q first; ties keep this server's own order of preference.
    Allowed = [Offer for _, _, Offer in
               sorted((Entry for Entry in Ranked if Entry[0] > 0),
                      key=lambda Entry: (-Entry[0], Entry[1]))]
    if Accept_Query:
        Wanted = Parse_Accept(Accept_Query)
        if any("*" in Item_Val.Media_Type for Item_Val in Wanted):
            raise ValueError("the accept query parameter shall not contain wildcards")
        Narrowed = [Offer for Offer in Allowed if Quality_For(Wanted, Offer) > 0]
        if Narrowed:
            Allowed = Narrowed
    return Allowed


def Negotiate(Accept_Header: Optional[str], Accept_Query: Optional[str],
              Offers: Sequence[Representation]) -> Representation:
    """Select the most preferred acceptable representation, or fail with 406."""
    Allowed = Acceptable(Accept_Header, Accept_Query, Offers)
    if not Allowed:
        Available = ", ".join(dict.fromkeys(Offer.Media_Type for Offer in Offers))
        raise UnacceptableMedia(
            f"no acceptable representation; this resource is available as {Available}"
        )
    return Allowed[0]

# Response bodies

Read_Block = 1 << 20


@dataclass(frozen=True)
class BodyPart:
    """One multipart part, sourced either from memory or from a file range."""

    Content_Type: str
    Location: Optional[str] = None
    Payload: Optional[bytes] = None
    File_Path: Optional[str] = None
    File_At: int = 0
    File_Length: int = 0

    @property
    def Length(Self) -> int:
        return len(Self.Payload) if Self.Payload is not None else Self.File_Length

    def Write_To(Self, Stream: BinaryIO) -> None:
        if Self.Payload is not None:
            Stream.write(Self.Payload)
            return
        Remaining = Self.File_Length
        with open(Self.File_Path or "", "rb") as Source:
            Source.seek(Self.File_At)
            while Remaining > 0:
                Block = Source.read(min(Read_Block, Remaining))
                if not Block:
                    raise OSError(f"{Self.File_Path} is shorter than the catalog recorded")
                Stream.write(Block)
                Remaining -= len(Block)


class MultipartBody:
    """A multipart/related body whose length is known before it is written."""

    def __init__(Self, Parts: Sequence[BodyPart], Media_Type: str, Boundary: Optional[str] = None):
        Self.Parts = list(Parts)
        Self.Media_Type = Media_Type
        Self.Boundary = Boundary or uuid.uuid4().hex

    @property
    def Content_Type(Self) -> str:
        return f'multipart/related; type="{Self.Media_Type}"; boundary={Self.Boundary}'

    def _Preamble(Self, Part: BodyPart) -> bytes:
        Lines = [f"--{Self.Boundary}", f"Content-Type: {Part.Content_Type}"]
        if Part.Location:
            Lines.append(f"Content-Location: {Part.Location}")
        Lines.append(f"Content-Length: {Part.Length}")
        return ("\r\n".join(Lines) + "\r\n\r\n").encode("ascii")

    @property
    def Length(Self) -> int:
        Closing = len(Self.Boundary) + 4  # "--" + boundary + "--"
        return sum(len(Self._Preamble(Part)) + Part.Length + 2 for Part in Self.Parts) + Closing

    def Write_To(Self, Stream: BinaryIO) -> None:
        for Part in Self.Parts:
            Stream.write(Self._Preamble(Part))
            Part.Write_To(Stream)
            Stream.write(b"\r\n")
        Stream.write(f"--{Self.Boundary}--".encode("ascii"))


@dataclass
class Reply:
    """A complete response, sized before any of it is written."""

    Status: int
    Content_Type: str
    Body: object
    Warning: Optional[str] = None

    @property
    def Length(Self) -> int:
        if isinstance(Self.Body, MultipartBody):
            return Self.Body.Length
        return len(Self.Body)  # type: ignore[arg-type]

    def Header_Fields(Self) -> List[Tuple[str, str]]:
        Fields = [
            ("Content-Type", Self.Content_Type),
            ("Content-Length", str(Self.Length)),
            ("X-Content-Type-Options", "nosniff"),
        ]
        if Self.Warning:
            Fields.append(("Warning", Self.Warning))
        return Fields

    def Write_To(Self, Stream: BinaryIO) -> None:
        if isinstance(Self.Body, MultipartBody):
            Self.Body.Write_To(Stream)
        else:
            Stream.write(Self.Body)  # type: ignore[arg-type]


def Json_Reply(Document: object, Media_Type: str = Dicom_Json_Media_Type,
               Status: int = HTTPStatus.OK) -> Reply:
    Payload = json.dumps(Document, ensure_ascii=False).encode("utf-8")
    return Reply(Status, f"{Media_Type}; charset=utf-8", Payload)


# Routes and constant messages

_Uid_Pattern = "([^/]+)"
_Study_Path = f"/studies/{_Uid_Pattern}"
_Series_Path = f"{_Study_Path}/series/{_Uid_Pattern}"
_Instance_Path = f"{_Series_Path}/instances/{_Uid_Pattern}"

Route_Study = re.compile(f"{_Study_Path}/?")
Route_Study_Metadata = re.compile(f"{_Study_Path}/metadata/?")
Route_Study_Bulkdata = re.compile(f"{_Study_Path}/bulkdata/?")
Route_Series = re.compile(f"{_Series_Path}/?")
Route_Series_Metadata = re.compile(f"{_Series_Path}/metadata/?")
Route_Series_Bulkdata = re.compile(f"{_Series_Path}/bulkdata/?")
Route_Instance = re.compile(f"{_Instance_Path}/?")
Route_Instance_Metadata = re.compile(f"{_Instance_Path}/metadata/?")
Route_Instance_Bulkdata = re.compile(f"{_Instance_Path}/bulkdata/?")
Route_Bulkdata_Reference = re.compile(f"{_Instance_Path}/bulkdata/(.+?)/?")
Route_Frames = re.compile(f"{_Instance_Path}/frames/([^/]+)/?")
#: Answered 404 on purpose rather than by accident.
Route_Rendered = re.compile(r"/studies/.+/(?:rendered|thumbnail)/?")

Documented_Routes = (
    "GET /studies/{study}/series/{series}",
    "GET /studies/{study}/series/{series}/instances/{instance}",
)

Cors_Fields = (
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS"),
    ("Access-Control-Allow-Headers", "Accept"),
    ("Access-Control-Expose-Headers", "Content-Type, Content-Length, Warning"),
)

Stored_Syntax_Warning = (
    '299 static-wado-rs "Instances are returned in their stored Transfer Syntax; '
    'this server does not transcode"'
)
Partial_Warning = (
    '299 static-wado-rs "Some instances are not stored in the requested Transfer Syntax"'
)
Rendered_Gone = (
    "the Retrieve Rendered Transaction is not implemented; this server returns "
    "stored bytes only and contains no image decoder or encoder"
)
Unknown_Resource = (
    "unknown WADO-RS resource"
)


def Sole_Query_Value(Query: Dict[str, List[str]], Name: str) -> Optional[str]:
    Values = Query.get(Name)
    if not Values:
        return None
    if len(Values) > 1:
        raise ValueError(f"the {Name!r} query parameter shall appear once")
    return Values[0]

# HTTP

class RetrieveHandler(BaseHTTPRequestHandler):
    """serves WADO-RS retrieve requests"""

    Catalog: Optional[Catalog] = None
    catalog: Optional[Catalog] = None
    Server_Version = "StaticWADO-RS/3.0"
    Sys_Version = ""
    Protocol_Version = "HTTP/1.1"

    server_version = Server_Version
    sys_version = Sys_Version
    protocol_version = Protocol_Version

    # writing responses

    def _Reply(Self, Reply_Inst: Reply, With_Body: bool) -> None:
        Self.send_response(Reply_Inst.Status)
        for Name, Value in Reply_Inst.Header_Fields():
            Self.send_header(Name, Value)
        for Name, Value in Cors_Fields:
            Self.send_header(Name, Value)
        Self.end_headers()
        if not With_Body:
            return
        try:
            Reply_Inst.Write_To(Self.wfile)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _Problem(Self, Status: HTTPStatus, Message: str, With_Body: bool) -> None:
        Payload = json.dumps({"status": int(Status), "error": Message},
                             ensure_ascii=False).encode("utf-8")
        Self._Reply(Reply(Status, "application/json; charset=utf-8", Payload), With_Body)

    def _Bare(Self, Status: HTTPStatus, Extra: Sequence[Tuple[str, str]] = ()) -> None:
        Self.send_response(Status)
        for Name, Value in Extra:
            Self.send_header(Name, Value)
        Self.send_header("Content-Length", "0")
        for Name, Value in Cors_Fields:
            Self.send_header(Name, Value)
        Self.end_headers()

    # methods

    def Do_GET(Self) -> None:
        Self._Serve(With_Body=True)

    def Do_HEAD(Self) -> None:
        Self._Serve(With_Body=False)

    def Do_OPTIONS(Self) -> None:
        Self._Bare(HTTPStatus.NO_CONTENT)

    def Do_POST(Self) -> None:
        Self._Bare(HTTPStatus.METHOD_NOT_ALLOWED, (("Allow", "GET, HEAD, OPTIONS"),))

    def Do_PUT(Self) -> None:
        Self.Do_POST()

    def Do_PATCH(Self) -> None:
        Self.Do_POST()

    def Do_DELETE(Self) -> None:
        Self.Do_POST()

    do_GET = Do_GET
    do_HEAD = Do_HEAD
    do_OPTIONS = Do_OPTIONS
    do_POST = Do_POST
    do_PUT = Do_PUT
    do_PATCH = Do_PATCH
    do_DELETE = Do_DELETE

    # request handling

    def _Serve(Self, With_Body: bool) -> None:
        Target = urlsplit(Self.path)
        if Target.path in ("", "/"):
            Self._Reply(Self._Front_Page(), With_Body)
            return
        if Route_Rendered.fullmatch(Target.path):
            Self._Problem(HTTPStatus.NOT_FOUND, Rendered_Gone, With_Body)
            return
        try:
            Query = (parse_qs(Target.query, keep_blank_values=True, strict_parsing=True)
                     if Target.query else {})
            Reply_Inst = Self._Resolve(Target.path, Query)
        except NoSuchResource as Error:
            Self._Problem(HTTPStatus.NOT_FOUND, str(Error), With_Body)
        except UnacceptableMedia as Error:
            Self._Problem(HTTPStatus.NOT_ACCEPTABLE, str(Error), With_Body)
        except ValueError as Error:
            Self._Problem(HTTPStatus.BAD_REQUEST, str(Error), With_Body)
        except UnsupportedInstance as Error:
            Self._Problem(HTTPStatus.NOT_IMPLEMENTED, str(Error), With_Body)
        except OSError as Error:
            Self._Problem(HTTPStatus.INTERNAL_SERVER_ERROR, str(Error), With_Body)
        else:
            if Reply_Inst is None:
                Self._Problem(HTTPStatus.NOT_FOUND, Unknown_Resource, With_Body)
            else:
                Self._Reply(Reply_Inst, With_Body)

    def _Resolve(Self, Path_Str: str, Query: Dict[str, List[str]]) -> Optional[Reply]:
        Header = Self.headers.get("Accept") if Self.headers else None
        Wanted = Sole_Query_Value(Query, "accept")

        Found = Route_Instance.fullmatch(Path_Str)
        if Found:
            return Self._Instances([Self._One_Instance(Found)], Header, Wanted)
        Found = Route_Series.fullmatch(Path_Str)
        if Found:
            return Self._Instances(Self._Series(Found), Header, Wanted)
        Found = Route_Study.fullmatch(Path_Str)
        if Found:
            return Self._Instances(Self._Study(Found), Header, Wanted)

        Found = Route_Instance_Metadata.fullmatch(Path_Str)
        if Found:
            return Self._Metadata([Self._One_Instance(Found)], Header, Wanted, Alone=True)
        Found = Route_Series_Metadata.fullmatch(Path_Str)
        if Found:
            return Self._Metadata(Self._Series(Found), Header, Wanted, Alone=False)
        Found = Route_Study_Metadata.fullmatch(Path_Str)
        if Found:
            return Self._Metadata(Self._Study(Found), Header, Wanted, Alone=False)

        # The unqualified bulkdata resources must be tried before the reference
        # route, which will also not match if swamped by trailing slashes.
        Found = Route_Instance_Bulkdata.fullmatch(Path_Str)
        if Found:
            return Self._Bulkdata([Self._One_Instance(Found)], None, Header, Wanted)
        Found = Route_Series_Bulkdata.fullmatch(Path_Str)
        if Found:
            return Self._Bulkdata(Self._Series(Found), None, Header, Wanted)
        Found = Route_Study_Bulkdata.fullmatch(Path_Str)
        if Found:
            return Self._Bulkdata(Self._Study(Found), None, Header, Wanted)
        Found = Route_Bulkdata_Reference.fullmatch(Path_Str)
        if Found:
            Reference = Parse_Bulkdata_Reference(unquote(Found.group(4)))
            return Self._Bulkdata([Self._One_Instance(Found)], Reference, Header, Wanted)

        Found = Route_Frames.fullmatch(Path_Str)
        if Found:
            return Self._Frames(Self._One_Instance(Found), unquote(Found.group(4)), Header, Wanted)
        return None

    # resource lookup

    def _Require_Catalog(Self) -> Catalog:
        Cat = Self.Catalog or Self.catalog
        if Cat is None:
            raise UnsupportedInstance("no DICOM catalog is configured")
        return Cat

    @staticmethod
    def _Path_Uids(Found: "re.Match[str]", Count: int) -> Tuple[str, ...]:
        Uids = tuple(unquote(Found.group(Number)) for Number in range(1, Count + 1))
        if not all(Is_Uid(Uid) for Uid in Uids):
            raise ValueError("path parameters shall be valid DICOM UIDs")
        return Uids

    def _Study(Self, Found: "re.Match[str]") -> List[Instance]:
        (Study_Uid,) = Self._Path_Uids(Found, 1)
        Instances = Self._Require_Catalog().Study_Instances(Study_Uid)
        if not Instances:
            raise NoSuchResource("the requested study was not found")
        return Instances

    def _Series(Self, Found: "re.Match[str]") -> List[Instance]:
        Study_Uid, Series_Uid = Self._Path_Uids(Found, 2)
        Instances = Self._Require_Catalog().Series_Instances(Study_Uid, Series_Uid)
        if not Instances:
            raise NoSuchResource("the requested series was not found")
        return Instances

    def _One_Instance(Self, Found: "re.Match[str]") -> Instance:
        Uids = Self._Path_Uids(Found, 3)
        Instance_Inst = Self._Require_Catalog().Instance(*Uids)
        if Instance_Inst is None:
            raise NoSuchResource("the requested DICOM instance was not found")
        return Instance_Inst

    # URIs

    def _Origin(Self) -> str:
        Host = Self.headers.get("Host") if Self.headers else None
        if not Host:
            Server = getattr(Self, "server", None)
            Address, Port = Server.server_address[:2] if Server else ("localhost", 80)
            Host = f"{Address}:{Port}"
        return f"http://{Host}"

    def _Instance_Uri(Self, Instance_Inst: Instance) -> str:
        return (f"{Self._Origin()}/studies/{Instance_Inst.Study_Uid}"
                f"/series/{Instance_Inst.Series_Uid}/instances/{Instance_Inst.Instance_Uid}")

    # transactions

    def _Instances(Self, Instances: Sequence[Instance], Accept_Header: Optional[str],
                   Accept_Query: Optional[str]) -> Reply:
        """Retrieve Instances: the stored Part 10 files that haven't received no modifications."""
        # One offer per transfer syntax actually present, so a client naming
        # `transfer-syntax=` selects a subset rather than all or nothing, and an
        # unconstrained request gets every instance.
        Present = list(dict.fromkeys(Instance_Item.Transfer_Syntax_Uid for Instance_Item in Instances))
        Offers = [Representation(Dicom_Media_Type, True, Syntax) for Syntax in Present]
        Allowed = Acceptable(Accept_Header, Accept_Query, Offers)
        if not Allowed:
            raise UnacceptableMedia(
                "no acceptable representation; these instances are stored as " + ", ".join(Present)
            )
        Syntaxes = {Offer.Transfer_Syntax for Offer in Allowed}
        Chosen = [Instance_Item for Instance_Item in Instances if Instance_Item.Transfer_Syntax_Uid in Syntaxes]
        Body = MultipartBody([
            BodyPart(
                Content_Type=f"{Dicom_Media_Type}; transfer-syntax={Instance_Item.Transfer_Syntax_Uid}",
                Location=Self._Instance_Uri(Instance_Item),
                File_Path=Instance_Item.File_Path,
                File_Length=Instance_Item.File_Size,
            )
            for Instance_Item in Chosen
        ], Dicom_Media_Type)

        Partial = len(Chosen) != len(Instances)
        if Partial:
            Warning: Optional[str] = Partial_Warning
        elif Syntaxes != {Explicit_Vr_Little_Endian}:
            Warning = Stored_Syntax_Warning
        else:
            Warning = None
        Status = HTTPStatus.PARTIAL_CONTENT if Partial else HTTPStatus.OK
        return Reply(Status, Body.Content_Type, Body, Warning)

    def _Metadata(Self, Instances: Sequence[Instance], Accept_Header: Optional[str],
                  Accept_Query: Optional[str], Alone: bool) -> Reply:
        """Retrieve Metadata: the DICOM JSON Model, bulk values by reference."""
        Chosen = Negotiate(Accept_Header, Accept_Query, [
            Representation(Dicom_Json_Media_Type, Multipart=False),
            Representation(Json_Media_Type, Multipart=False),
        ])
        Documents = [
            Instance_To_Json(Parse_Instance(Instance_Item.File_Path),
                             f"{Self._Instance_Uri(Instance_Item)}/bulkdata")
            for Instance_Item in Instances
        ]
        return Json_Reply(Documents[0] if Alone else Documents, Media_Type=Chosen.Media_Type)

    def _Bulkdata(Self, Instances: Sequence[Instance], Reference: Optional[Tuple],
                  Accept_Header: Optional[str], Accept_Query: Optional[str]) -> Reply:
        """Retrieve Bulkdata: raw values, addressed by tag path."""
        Offers = [Representation(Octet_Stream_Media_Type, Multipart=True)]
        if Reference is not None:
            # A single addressed value may also be delivered without a wrapper.
            Offers.append(Representation(Octet_Stream_Media_Type, Multipart=False))
        Chosen = Negotiate(Accept_Header, Accept_Query, Offers)

        Parts: List[BodyPart] = []
        for Instance_Item in Instances:
            Parsed = Parse_Instance(Instance_Item.File_Path)
            Base = f"{Self._Instance_Uri(Instance_Item)}/bulkdata"
            if Reference is None:
                Found = Collect_Bulkdata(Parsed.Dataset)
            else:
                Found = [(Reference_To_Path(Reference), Find_Bulkdata(Parsed.Dataset, Reference))]
            Parts.extend(
                BodyPart(
                    Content_Type=Octet_Stream_Media_Type,
                    Location=f"{Base}/{Location}",
                    Payload=Bulkdata_Bytes(Element_Inst, Parsed.Buffer),
                )
                for Location, Element_Inst in Found
            )
        if not Parts:
            raise NoSuchResource("the addressed resource contains no bulkdata")
        if not Chosen.Multipart:
            return Reply(HTTPStatus.OK, Octet_Stream_Media_Type, Parts[0].Payload)
        Body = MultipartBody(Parts, Octet_Stream_Media_Type)
        return Reply(HTTPStatus.OK, Body.Content_Type, Body)

    def _Frames(Self, Instance_Inst: Instance, Frame_List: str, Accept_Header: Optional[str],
                Accept_Query: Optional[str]) -> Reply:
        """Retrieve Frames: stored frame bytes, never decoded or re-encoded."""
        Numbers = Parse_Frame_List(Frame_List, Instance_Inst.Number_Of_Frames)
        Media_Type = Frame_Media_Type(Instance_Inst)
        Syntax = Instance_Inst.Transfer_Syntax_Uid
        Negotiate(Accept_Header, Accept_Query, [
            Representation(Media_Type, Multipart=True, Transfer_Syntax=Syntax),
        ])
        Buffer = Read_Part10(Instance_Inst.File_Path).Buffer
        Base = f"{Self._Instance_Uri(Instance_Inst)}/frames"
        Body = MultipartBody([
            BodyPart(
                Content_Type=f"{Media_Type}; transfer-syntax={Syntax}",
                Location=f"{Base}/{Number}",
                Payload=Extract_Frame(Instance_Inst, Buffer, Number),
            )
            for Number in Numbers
        ], Media_Type)
        Warning = None if Syntax == Explicit_Vr_Little_Endian else Stored_Syntax_Warning
        return Reply(HTTPStatus.OK, Body.Content_Type, Body, Warning)

    # front page

    def _Front_Page(Self) -> Reply:
        Catalog_Inst = Self._Require_Catalog() if (Self.Catalog or Self.catalog) else None
        Patterns = "".join(f"<li><code>{html.escape(Route)}</code></li>"
                           for Route in Documented_Routes)
        Links = "".join(
            f"<li><strong>{html.escape(Label)}:</strong> "
            f"<a href='{html.escape(Url, quote=True)}'>{html.escape(Url)}</a></li>"
            for Label, Url in Startup_Links(Catalog_Inst, Self._Origin())
        ) if Catalog_Inst else ""
        Page = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Static WADO-RS server</title></head><body>"
            "<h1>Static WADO-RS server</h1>"
            "<p>WADO-RS retrieve transactions. Rendered and thumbnail resources are "
            "not implemented and always return 404.</p>"
            f"<p>Catalogued: {Catalog_Inst.Study_Count if Catalog_Inst else 0} study/studies, "
            f"{Catalog_Inst.Series_Count if Catalog_Inst else 0} series, "
            f"{Catalog_Inst.Instance_Count if Catalog_Inst else 0} instances.</p>"
            f"<h2>Transaction examples</h2><ul>{Links}</ul>"
            f"<h2>Retrieve transactions</h2><ul>{Patterns}</ul>"
            "</body></html>"
        ).encode("utf-8")
        return Reply(HTTPStatus.OK, "text/html; charset=utf-8", Page)

# Command line

def Startup_Links(Catalog_Inst: Catalog, Base_Uri: str) -> List[Tuple[str, str]]:
    """Concrete links for the documented series and instance transactions."""
    Base_Uri = Base_Uri.rstrip("/")
    Links: List[Tuple[str, str]] = []
    for Study_Uid in sorted(Catalog_Inst.Studies):
        Study = f"{Base_Uri}/studies/{Study_Uid}"
        for Series_Uid in sorted(Catalog_Inst.Studies[Study_Uid].Series):
            Series = f"{Study}/series/{Series_Uid}"
            Links.append(("Series instances", Series))
            for Instance_Item in Catalog_Inst.Series_Instances(Study_Uid, Series_Uid):
                Resource = f"{Series}/instances/{Instance_Item.Instance_Uid}"
                Links.append(("DICOM instance", Resource))
    return Links


def _Browser_Base_Uri(Host: str, Port: int) -> str:
    """turn a bind address into one that a local browser can open"""
    if Host in ("0.0.0.0", "::"):
        Host = "localhost"
    elif ":" in Host and not Host.startswith("["):
        Host = f"[{Host}]"
    return f"http://{Host}:{Port}"


def _Tcp_Port(Text: str) -> int:
    try:
        Port = int(Text)
    except ValueError as Error:
        raise argparse.ArgumentTypeError(f"{Text!r} is not a port number") from Error
    if not 0 <= Port <= 65535:
        raise argparse.ArgumentTypeError("a port shall be between 0 and 65535")
    return Port


def _Readable_Directory(Text: str) -> str:
    Path_Str = os.path.realpath(Text)
    if not os.path.isdir(Path_Str):
        raise argparse.ArgumentTypeError(f"not a directory: {Path_Str}")
    return Path_Str


def Build_Parser() -> argparse.ArgumentParser:
    Parser = argparse.ArgumentParser(
        prog="wado_server",
        description="Dependency-free static WADO-RS server (no rendered transactions)",
    )
    Parser.add_argument("--dir", "-d", dest="Directory", type=_Readable_Directory,
                        default=".", metavar="PATH",
                        help="directory holding DICOM Part 10 files (default: .)")
    Parser.add_argument("--host", "-H", dest="Host", default="0.0.0.0", metavar="ADDRESS",
                        help="bind address (default: 0.0.0.0)")
    Parser.add_argument("--port", "-p", dest="Port", type=_Tcp_Port, default=10104, metavar="PORT",
                        help="TCP port (default: 10104)")
    Parser.add_argument("--no-recursive", dest="Recursive", action="store_false",
                        help="catalog only the top-level directory")
    return Parser


def Main(Argv: Optional[Sequence[str]] = None) -> int:
    Options = Build_Parser().parse_args(Argv)
    Catalog_Inst = Catalog(Options.Directory, Recursive=Options.Recursive)
    RetrieveHandler.Catalog = Catalog_Inst
    RetrieveHandler.catalog = Catalog_Inst
    with ThreadingHTTPServer((Options.Host, Options.Port), RetrieveHandler) as Server:
        Address, Port = Server.server_address[:2]
        Base_Uri = _Browser_Base_Uri(Address, Port)
        print(f"Serving {Catalog_Inst.Study_Count} study/studies, {Catalog_Inst.Series_Count} series, "
              f"{Catalog_Inst.Instance_Count} instance(s) at {Base_Uri}")
        if Catalog_Inst.Failures:
            print(f"Skipped {len(Catalog_Inst.Failures)} unreadable, malformed, or duplicate file(s)")
        print("\nTransaction examples:")
        for Label, Url in Startup_Links(Catalog_Inst, Base_Uri):
            print(f"  {Label}:\n    {Url}")
        print(flush=True)
        try:
            Server.serve_forever()
        except KeyboardInterrupt:
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(Main())
