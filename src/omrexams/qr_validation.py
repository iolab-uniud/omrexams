import binascii
import io
import json
import os
import re

import cv2
import numpy as np
import zxingcpp
from pypdf import PdfReader, PdfWriter
from wand.color import Color
from wand.image import Image

from .utils.qrdecoder import BOTTOM_RIGHT_REGEX, TOP_LEFT_REGEX, decode_top_left


def _signature(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def decode_page_qrs(page, page_number):
    writer = PdfWriter()
    writer.add_page(page)
    buffer = io.BytesIO()
    writer.write(buffer)
    for resolution in (120, 180, 240):
        with Image(blob=buffer.getvalue(), resolution=resolution) as image:
            image.background_color = Color("white")
            image.alpha_channel = "remove"
            image.format = "png"
            pixels = cv2.imdecode(np.frombuffer(image.make_blob(), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        codes = [item.text for item in zxingcpp.read_barcodes(pixels)]
        tops = list(filter(None, (re.fullmatch(TOP_LEFT_REGEX, code) for code in codes)))
        bottoms = list(filter(None, (re.fullmatch(BOTTOM_RIGHT_REGEX, code) for code in codes)))
        if len(tops) == 1 and len(bottoms) == 1:
            return tops[0].group(0), bottoms[0].group(0)
    raise ValueError(f"Impossibile leggere entrambi i QR della pagina {page_number}.")


def generation_json_path(pdf_path):
    stem = os.path.splitext(pdf_path)[0]
    candidates = [(stem + ".json", False)]
    chunk = re.fullmatch(r"(.+)-\d+", stem)
    if chunk:
        candidates.append((chunk.group(1) + ".json", True))
    for path, is_chunk in candidates:
        if os.path.isfile(path) and not os.path.islink(path):
            return path, is_chunk
    raise ValueError("JSON della generazione non trovato accanto al PDF.")


def _load_exams(data):
    table = data.get("exams") if isinstance(data, dict) else None
    if not isinstance(table, dict) or not table:
        raise ValueError("Il JSON non contiene una tabella exams valida.")
    exams = {}
    for exam in table.values():
        if not isinstance(exam, dict):
            raise ValueError("Il JSON contiene un compito non valido.")
        identity = exam.get("student_id")
        answers = exam.get("answers")
        questions = exam.get("questions")
        if not isinstance(identity, str) or not identity:
            raise ValueError("Il JSON contiene una matricola non valida.")
        if identity in exams:
            raise ValueError(f"Matricola duplicata nel JSON: {identity}.")
        if (
            not isinstance(answers, list)
            or not all(isinstance(answer, str) and re.fullmatch(r"[A-H]*", answer) for answer in answers)
            or not isinstance(questions, list)
            or len(questions) > len(answers)
        ):
            raise ValueError(f"Chiavi di correzione non valide nel JSON per la matricola {identity}.")
        for index, question in enumerate(questions):
            if not isinstance(question, list) or len(question) < 3 or question[2] != answers[index]:
                raise ValueError(f"Chiavi questions e answers discordanti nel JSON per la matricola {identity}.")
        exams[identity] = answers
    return exams


def validate_exam_qrs(pdf_path, progress=None, json_path=None):
    if json_path is None:
        json_path, is_chunk = generation_json_path(pdf_path)
    else:
        pdf_stem = os.path.splitext(os.path.basename(pdf_path))[0]
        json_stem = os.path.splitext(os.path.basename(json_path))[0]
        is_chunk = re.fullmatch(re.escape(json_stem) + r"-\d+", pdf_stem) is not None
    if os.path.islink(json_path) or os.path.islink(pdf_path):
        raise ValueError("PDF e JSON devono essere file originali, non collegamenti simbolici.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(json_path, flags), encoding="utf-8") as source_json, \
            os.fdopen(os.open(pdf_path, flags), "rb") as source_pdf:
        json_signature = _signature(os.fstat(source_json.fileno()))
        pdf_signature = _signature(os.fstat(source_pdf.fileno()))
        data = json.load(source_json)
        exams = _load_exams(data)
        reader = PdfReader(source_pdf)
        if reader.is_encrypted:
            raise ValueError("Il PDF è protetto da password.")
        total = len(reader.pages)
        if not total:
            raise ValueError("Il PDF non contiene pagine.")
        issues = []
        seen = set()
        covered = {}
        last_identity = None
        last_number = 0
        padding = False
        qr_pages = 0
        padding_pages = 0

        def issue(page, message, identity=None):
            issues.append({"page": page, "student_id": identity, "message": message})

        for index, page in enumerate(reader.pages, 1):
            if progress:
                progress(index - 1, total, "Verifica leggibilità QR e chiavi di correzione")
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            if abs(width - 595.28) > 3 or abs(height - 841.89) > 3 or page.rotation % 360:
                issue(index, "La pagina non è un A4 verticale non ruotato.")
                continue
            if page.get_contents() is None:
                if last_identity is None or padding or last_number % 2 == 0:
                    issue(index, "Pagina vuota inattesa.")
                else:
                    padding_pages += 1
                padding = True
                continue
            try:
                top, bottom = decode_page_qrs(page, index)
                decoded = decode_top_left(top)
            except (ValueError, binascii.Error) as exc:
                issue(index, str(exc))
                continue
            if decoded is None:
                issue(index, "Il QR della matricola non è valido.")
                continue
            qr_pages += 1
            identity = decoded["student_id"]
            geometry = re.fullmatch(BOTTOM_RIGHT_REGEX, bottom)
            number = int(geometry.group("page"))
            if number == 1:
                if identity in seen:
                    issue(index, "Il compito compare più di una volta nel PDF.", identity)
                seen.add(identity)
            elif identity != last_identity or padding or number != last_number + 1:
                issue(index, "Sequenza delle pagine del compito non valida.", identity)
            last_identity, last_number, padding = identity, number, False
            if identity not in exams:
                issue(index, "Matricola non presente nel JSON della generazione.", identity)
                continue
            expected = exams[identity]
            actual = decoded["correct"]
            if actual != expected:
                differing = [
                    question + 1 for question in range(max(len(actual), len(expected)))
                    if question >= len(actual) or question >= len(expected) or actual[question] != expected[question]
                ]
                issue(index, f"Chiavi di correzione discordanti alle domande: {', '.join(map(str, differing))}.", identity)
            start, end = geometry.group("start"), geometry.group("end")
            if start is None or end is None:
                issue(index, "Il QR non indica l'intervallo delle domande.", identity)
                continue
            start, end = int(start), int(end)
            if start == end == 0:
                continue
            if start < 1 or end < start or end > len(expected):
                issue(index, "Intervallo delle domande nel QR non valido.", identity)
                continue
            previous = covered.setdefault(identity, set())
            questions = set(range(start, end + 1))
            if previous & questions:
                issue(index, "Intervalli delle domande sovrapposti tra le pagine.", identity)
            previous.update(questions)

        for identity in seen & exams.keys():
            missing = set(range(1, len(exams[identity]) + 1)) - covered.get(identity, set())
            if missing:
                issue(None, f"Domande non coperte dai QR: {', '.join(map(str, sorted(missing)))}.", identity)
        if not is_chunk:
            for identity in sorted(exams.keys() - seen):
                issue(None, "Compito del JSON non presente nel PDF.", identity)
        if (
            _signature(os.fstat(source_pdf.fileno())) != pdf_signature
            or _signature(os.stat(pdf_path, follow_symlinks=False)) != pdf_signature
            or _signature(os.fstat(source_json.fileno())) != json_signature
            or _signature(os.stat(json_path, follow_symlinks=False)) != json_signature
        ):
            raise ValueError("PDF o JSON modificati durante la verifica; ripetere il controllo.")
        if progress:
            progress(total, total, "Verifica completata")
        metadata = data.get("metadata", {})
        first_metadata = next(iter(metadata.values()), {}) if isinstance(metadata, dict) else {}
        seed = first_metadata.get("seed") if isinstance(first_metadata, dict) else None
        return {
            "valid": not issues,
            "pdf_filename": os.path.basename(pdf_path),
            "json_filename": os.path.basename(json_path),
            "seed": seed,
            "pages_total": total,
            "qr_pages": qr_pages,
            "padding_pages": padding_pages,
            "exams_checked": len(seen),
            "json_exams": len(exams),
            "is_chunk": is_chunk,
            "issues": issues,
        }
