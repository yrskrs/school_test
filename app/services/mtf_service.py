"""Read unprotected MyTestX 10.2 files without Windows or external executables.

XCrypt/header documentation: iamschmuck/MyTestStudentX-.MTF-Decoder (MIT).
The record reader was checked against the original Mtf2Xml XML export.
See THIRD_PARTY_NOTICES.md for attribution and license.
"""
from __future__ import annotations

import base64
import csv
import io
import math
import re
import struct
import xml.etree.ElementTree as ET
import zlib

from app.services.media_service import MAX_IMAGE_BYTES, validate_image

MAX_MTF_BYTES = 20 * 1024 * 1024
MAX_BODY_BYTES = 100 * 1024 * 1024
TYPE_NAMES = {1: "TYPE_TASK_CHOICE_SINGLE", 2: "TYPE_TASK_CHOICE_MULTIPLE",
              3: "TYPE_TASK_CHOICE_ORDER", 4: "TYPE_TASK_CHOICE_COLLATION",
              6: "TYPE_TASK_ENTER_NUM", 7: "TYPE_TASK_ENTER_TEXT", 8: "TYPE_TASK_IMAGE_POINT"}


class Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data, self.pos = data, pos

    def take(self, size: int) -> bytes:
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError("MTF пошкоджено: несподіваний кінець файлу")
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def number(self, fmt: str = "I"):
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))[0]

    def count(self, maximum: int = 10000) -> int:
        value = self.number()
        if value > maximum:
            raise ValueError("MTF містить непідтримувану структуру або завеликі дані")
        return value

    def string(self) -> str:
        try:
            return self.take(self.count(MAX_BODY_BYTES // 2) * 2).decode("utf-16-le")
        except UnicodeDecodeError as exc:
            raise ValueError("MTF пошкоджено: невірне кодування тексту") from exc


def decode_mtf(content: bytes) -> bytes:
    if not content or len(content) > MAX_MTF_BYTES:
        raise ValueError("Файл MTF має бути непорожнім і не більшим за 20 МБ")
    # Feedback uses the ciphertext byte plus the current key, with carry discarded.
    state, plain = 0x0101, bytearray(len(content))
    for i, byte in enumerate(content):
        key = state & 0xFF
        plain[i] = byte ^ key
        state = (((plain[i] + key) & 0xFF) * 0x6CA9 + 0xCE1C) & 0xFFFF
    reader = Reader(bytes(plain))
    if reader.take(14) != "MyTestX".encode("utf-16-le"):
        raise ValueError("Не вдалося прочитати MTF. Очікується тест MyTestX 10.2 без пароля")
    version = reader.string()
    if not version.startswith("10.2."):
        raise ValueError(f"Версія MTF {version[:80]} не підтримується. Експортуйте тест у XML")
    try:
        inflater = zlib.decompressobj()
        body = inflater.decompress(reader.data[reader.pos:], MAX_BODY_BYTES + 1)
    except zlib.error as exc:
        raise ValueError("MTF пошкоджено: помилка розпакування") from exc
    if len(body) > MAX_BODY_BYTES or inflater.unconsumed_tail:
        raise ValueError("Розпакований тест MTF перевищує 100 МБ")
    if not inflater.eof or inflater.unused_data:
        raise ValueError("MTF пошкоджено: неповний або невірний стиснений потік")
    return body


def rtf_text(text: str) -> str:
    """Read RTF text with group destinations, codepages and Unicode fallbacks."""
    if not text.startswith("{\\rtf"):
        return text.strip()
    skip, uc, codepage = False, 1, "cp1251"
    charsets = {0: "cp1252", 128: "cp932", 129: "cp949", 134: "gbk", 136: "cp950",
                161: "cp1253", 162: "cp1254", 163: "cp1258", 177: "cp1255",
                178: "cp1256", 186: "cp1257", 204: "cp1251", 222: "cp874", 238: "cp1250"}
    fonts = {int(font): charsets.get(int(charset)) for font, charset
             in re.findall(r"\\f(\d+)[^{};]*?\\fcharset(\d+)", text)}
    stack, output, fallback = [], [], 0
    token = re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|[{}]|[^\\{}]+")
    destinations = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "fldinst", "header", "footer"}
    words = {"par": "\n", "line": "\n", "tab": "\t", "emdash": "—", "endash": "–",
             "bullet": "•", "lquote": "‘", "rquote": "’", "ldblquote": "“", "rdblquote": "”"}

    def emit(value):
        nonlocal fallback
        if skip:
            return
        dropped = min(fallback, len(value))
        fallback -= dropped
        output.append(value[dropped:])

    for match in token.finditer(text):
        value, word, arg, hx, symbol = match.group(0), *match.groups()
        if value == "{":
            stack.append((skip, uc, codepage))
        elif value == "}":
            if stack:
                skip, uc, codepage = stack.pop()
        elif hx:
            emit(bytes([int(hx, 16)]).decode(codepage, errors="replace"))
        elif symbol:
            if symbol == "*":
                skip = True
            elif symbol in "{}\\":
                emit(symbol)
            elif symbol in "~_":
                emit(" " if symbol == "~" else "-")
        elif word:
            if word in destinations:
                skip = True
            elif word == "ansicpg" and arg:
                try:
                    b"".decode("cp" + arg)
                    codepage = "cp" + arg
                except LookupError:
                    raise ValueError(f"Непідтримуване кодування RTF: {arg}")
            elif word == "uc" and arg:
                uc = max(0, min(int(arg), 16))
            elif word == "f" and arg and not skip:
                font_codepage = fonts.get(int(arg))
                if font_codepage:
                    codepage = font_codepage
            elif word == "u" and arg and not skip:
                output.append(chr(int(arg) & 0xFFFF))
                fallback = uc
            elif word in words:
                emit(words[word])
        else:
            emit(value.replace("\r", "").replace("\n", ""))
    # RTF stores supplementary Unicode characters as UTF-16 surrogate pairs.
    result = "".join(output).encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    return result.strip()


def _dib_bitmap(dib: bytes) -> bytes:
    if len(dib) < 40 or struct.unpack_from("<I", dib)[0] != 40:
        raise ValueError("Непідтримуване зображення DIB у MTF")
    bits = struct.unpack_from("<H", dib, 14)[0]
    compression, _, _, _, colors = struct.unpack_from("<5I", dib, 16)
    palette = colors or (1 << bits if bits <= 8 else 0)
    offset = 14 + 40 + palette * 4 + (12 if compression == 3 else 0)
    return struct.pack("<2sIHHI", b"BM", 14 + len(dib), 0, 0, offset) + dib


def inline_image(rtf: str):
    """Extract raster pictures, including DIB embedded in a WMF wrapper."""
    pictures = list(re.finditer(r"\{\\pict\b([^{}]*)\}", rtf, re.S))
    if not pictures:
        if "\\pict" in rtf:
            raise ValueError("Непідтримувана структура вбудованого зображення MTF")
        return None
    if len(pictures) != 1:
        raise ValueError("Кілька вбудованих зображень в одному варіанті MTF не підтримуються")
    picture = pictures[0].group(1)
    payload = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", picture)
    try:
        data = bytes.fromhex(payload)
    except ValueError as exc:
        raise ValueError("Пошкоджене вбудоване зображення MTF") from exc
    if "\\pngblip" in picture:
        ext = ".png"
    elif "\\jpegblip" in picture:
        ext = ".jpg"
    elif "\\dibitmap" in picture:
        data, ext = _dib_bitmap(data), ".bmp"
    elif "\\wmetafile" in picture:
        # Standard WMF: 18-byte header, then records sized in 16-bit words.
        records = Reader(data, 18)
        bitmaps = []
        while records.pos < len(data):
            size, function = records.number() * 2, records.number("H")
            if size < 6:
                raise ValueError("Пошкоджений WMF у MTF")
            record = records.take(size - 6)
            if function == 0x0F43:  # META_STRETCHDIB: 22 bytes of parameters.
                bitmaps.append(_dib_bitmap(record[22:]))
            elif function == 0:
                break
            elif function not in (0x020B, 0x020C):  # Window origin / extent.
                raise ValueError("Векторні елементи WMF у MTF не підтримуються")
        if len(bitmaps) != 1:
            raise ValueError("Векторне зображення WMF у MTF не підтримується")
        data, ext = bitmaps[0], ".bmp"
    else:
        raise ValueError("Непідтримуваний тип вбудованого зображення MTF")
    validate_image(data, ext)
    return (ext, data)


def _text_element(parent, tag, text):
    node = ET.SubElement(parent, tag)
    ET.SubElement(node, "PlainText").text = rtf_text(text)
    picture = inline_image(text)
    if picture:
        ext, data = picture
        target, image_tag = (parent, "QuestionImage") if tag == "QuestionText" else (node, "VariantImage")
        ET.SubElement(target, image_tag, FileName="image" + ext).text = base64.b64encode(data).decode("ascii")
    return node


def mtf_to_xml(content: bytes) -> str:
    """Convert every question or reject the file; never guess correct answers."""
    body = decode_mtf(content)
    root = ET.Element("MyTestX")
    metadata = Reader(body)
    ET.SubElement(root, "Title").text = metadata.string()
    metadata.string()  # author
    metadata.string()  # author email
    description = metadata.string()
    ET.SubElement(root, "Description").text = rtf_text(description)

    # The settings section ends in the test GUID, followed by the group table.
    guid = re.search(rb"\x26\x00\x00\x00\x7b\x00(?:[0-9A-Fa-f-]\x00){36}\x7d\x00", body[metadata.pos:])
    if guid is None:
        raise ValueError("Непідтримувана структура налаштувань MTF")
    groups_reader = Reader(body, metadata.pos + guid.end())
    groups_node, groups = ET.SubElement(root, "Groups"), {}
    for _ in range(groups_reader.count(1000)):
        group_id = groups_reader.number()
        group = ET.SubElement(groups_node, "Group")
        ET.SubElement(group, "Title").text = groups_reader.string()
        groups_reader.string()  # group description
        group.set("CountLimit", str(groups_reader.number()))
        groups_reader.number("B")
        if group_id in groups:
            raise ValueError("MTF містить повторні ідентифікатори тем")
        groups[group_id] = group

    # The global question count immediately precedes the first record.
    marker = re.search(rb"[\x01-\x09]\x05\x00\x00\x00.{4}\x7b\x00\x5c\x00\x72\x00\x74\x00\x66\x00", body[groups_reader.pos:], re.S)
    if marker is None:
        raise ValueError("У MTF не знайдено запитань")
    start = groups_reader.pos + marker.start()
    reader = Reader(body, start - 4)
    count = reader.count()
    if count == 0:
        raise ValueError("У MTF не знайдено запитань")
    image_refs = []
    for index in range(count):
        try:
            kind = reader.number("B")
            if kind not in TYPE_NAMES:
                raise ValueError(f"Тип запитання MTF {kind} ще не підтримується; використайте XML")
            if reader.number() != 5:
                raise ValueError("Непідтримувана структура запитання MTF")
            question = reader.string()
            extra = [reader.string() for _ in range(6)]
            if not rtf_text(question) and not (extra[4] or inline_image(question)):
                raise ValueError("Запитання MTF не містить тексту або зображення")
            score = reader.number()
            reader.take(20)
            group_id, random = reader.number(), reader.number("B")
            if group_id not in groups or random not in (0, 1):
                raise ValueError("Непідтримувані параметри запитання MTF")
            tasks = groups[group_id].find("Tasks")
            if tasks is None:
                tasks = ET.SubElement(groups[group_id], "Tasks")
            task = ET.SubElement(tasks, "Task", Type=TYPE_NAMES[kind], Score=str(score))
            question_node = _text_element(task, "QuestionText", question)
            if not question_node.findtext("PlainText"):
                question_node.find("PlainText").text = f"Запитання {index + 1}"
            if extra[4]:
                if task.find("QuestionImage") is not None:
                    raise ValueError("Кілька зображень в одному запитанні MTF не підтримуються")
                image = ET.SubElement(task, "QuestionImage", FileName=extra[4])
                image_refs.append((image, extra[4]))
            if kind in (1, 2, 3, 4):
                answers = [reader.string() for _ in range(reader.count(100))]
                flags = [reader.number() for _ in range(reader.count(100))]
                if len(flags) != len(answers):
                    raise ValueError("Кількість відповідей MTF не відповідає масиву правильних відповідей")
                right = [reader.string() for _ in range(reader.count(100))] if kind == 4 else []
                active = [i for i, text in enumerate(answers) if text]
                if len(active) < 2:
                    raise ValueError("Запитання MTF містить менше двох варіантів")
                if kind in (1, 2) and (any(flags[i] not in (0, 1) for i in active) or not any(flags[i] for i in active)):
                    raise ValueError("Невірний масив правильних відповідей MTF")
                if kind == 1 and sum(flags[i] for i in active) != 1:
                    raise ValueError("Одиночний вибір MTF містить кілька правильних відповідей")
                if kind == 3 and sorted(flags[i] for i in active) != list(range(1, len(active) + 1)):
                    raise ValueError("Невірна послідовність відповідей MTF")
                if kind == 4:
                    if any(not 1 <= flags[i] <= len(right) or not right[flags[i] - 1] for i in active):
                        raise ValueError("Невірні пари відповідностей MTF")
                    # The site can display pictures in the left column. Swap the
                    # paired columns when the original right column has pictures.
                    if any(inline_image(text) for text in right if text):
                        used = {flags[i] - 1 for i in active}
                        if (any(inline_image(text) for text in answers if text)
                                or len(used) != len(active)
                                or used != {i for i, text in enumerate(right) if text}):
                            raise ValueError("Зображення в обох колонках відповідностей MTF не підтримуються")
                        pairs = [(right[flags[i] - 1], answers[i]) for i in active]
                        answers, right = [p[0] for p in pairs], [p[1] for p in pairs]
                        active, flags = list(range(len(pairs))), list(range(1, len(pairs) + 1))
                    right_node = ET.SubElement(task, "Variants2")
                    # Keep empty padded slots: CorrectAnswer indexes the original array.
                    for text in right:
                        _text_element(right_node, "VariantText", text)
                variants = ET.SubElement(task, "Variants")
                for i in active:
                    node = _text_element(variants, "VariantText", answers[i])
                    node.set("CorrectAnswer", str(bool(flags[i])) if kind in (1, 2) else str(flags[i]))
            elif kind == 7:
                text = reader.string()
                case_sensitive, normalize = reader.number("B"), reader.number("B")
                if case_sensitive or normalize:
                    raise ValueError("Спеціальні правила перевірки тексту MTF не підтримуються")
                if not text.strip():
                    raise ValueError("Текстова відповідь MTF порожня")
                values = io.StringIO()
                csv.writer(values, delimiter=";", lineterminator="").writerow(text.splitlines())
                ET.SubElement(ET.SubElement(task, "InputText"), "Value").text = values.getvalue()
            elif kind == 6:
                reader.number("B")
                low, high = reader.number("d"), reader.number("d")
                reader.number()
                reader.number("B")
                if not math.isfinite(low) or low != high:
                    raise ValueError("Числові діапазони MTF не підтримуються")
                ET.SubElement(ET.SubElement(task, "InputNum"), "Value").text = format(low, ".15g")
            elif kind == 8:
                regions = ET.SubElement(task, "Regions")
                for _ in range(reader.count(100)):
                    points = [(reader.number("i"), reader.number("i")) for _ in range(reader.count(1000))]
                    if len(points) < 3:
                        raise ValueError("Область зображення MTF має менше трьох точок")
                    ET.SubElement(regions, "Region").text = "-".join(f"({x}, {y})" for x, y in points)
                if not len(regions) or not extra[4]:
                    raise ValueError("Запитання MTF з областю не містить зображення або правильної області")
            # Optional explanation string and final flag; always consume them,
            # so a changed record layout cannot silently corrupt subsequent tasks.
            reader.string()
            reader.number("B")
        except ValueError as exc:
            raise ValueError(f"Запитання {index + 1}: {exc}") from exc

    images = {}
    for _ in range(reader.count(1000)):
        name = reader.string()
        data = reader.take(reader.count(MAX_IMAGE_BYTES))
        ext = "." + name.rsplit(".", 1)[-1].lower()
        validate_image(data, ext)
        if name in images:
            raise ValueError("Повторне ім'я зображення MTF")
        images[name] = base64.b64encode(data).decode("ascii")
    if reader.pos != len(body):
        raise ValueError("MTF містить непідтримувані додаткові дані")
    for node, name in image_refs:
        if name not in images:
            raise ValueError("У MTF відсутнє зображення, потрібне для запитання")
        node.text = images[name]
    return ET.tostring(root, encoding="unicode")
