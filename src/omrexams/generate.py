import time
import click
import random
import time
import logging
import glob
import os
import re
import io
from shutil import copy2, rmtree
import multiprocessing as mp
from functools import partial
from . utils.markdown import QuestionRenderer, DocumentStripRenderer, Document
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf._page import PageObject
import math
from datetime import datetime as dt
from tinydb import TinyDB
from . utils.directories import BASEDIR
from collections import deque
from itertools import zip_longest
import hashlib
import importlib.resources as pkg_resources
from copy import copy
from queue import Empty
from .profiles import config_for_profile, question_rules, validate_profiles

logger = logging.getLogger("omrexams")

QUESTION_MARKER_RE = re.compile(r'-{3,}\s*\n')
TITLE_RE = re.compile(r"#\s+.*")
QUESTION_RE = re.compile(r"##\s*(.+?)(?={topic:#|\n)({topic:#[\w-]+})?")
OPEN_QUESTION_RE = re.compile(r"#{3,}\s*(.+?)")
TOPIC_RE = re.compile(r"##\s*.*{topic:(#[\w-]+)}")

# The text area and OMR margin are separated by 0.5 cm in omrexam.cls.
OVERFULL_WARNING_PT = 0.5 * 72.27 / 2.54
OVERFULL_RE = re.compile(r'Overfull \\hbox \((\d+(?:\.\d+)?)pt too wide\)([^\n]*)')

def _overfull_warnings(log, tex, label):
    lines = tex.splitlines()
    locations = {}
    question_number = 0
    in_question = False
    for line_number, line in enumerate(lines, 1):
        if line.strip() == r'\begin{minipage}{\linewidth}':
            in_question = True
            current = None
        if in_question and re.match(r'^\s*\\question(?:\s|$)', line):
            question_number += 1
            heading = line.split(r'\question', 1)[1].strip()
            if not heading:
                for following_line in lines[line_number:]:
                    heading = following_line.strip()
                    if heading and not re.fullmatch(r'\\omrchoices\{\d+\}', heading):
                        break
            heading = heading.replace(r'\allowbreak{}', '')[:120]
            current = (question_number, heading)
        if in_question and current:
            locations[line_number] = current
        if line.strip() == r'\end{minipage}':
            in_question = False

    warnings = []
    for match in OVERFULL_RE.finditer(log):
        amount = float(match.group(1))
        if amount <= OVERFULL_WARNING_PT:
            continue
        line_match = re.search(r'at lines?\s+(\d+)(?:--(\d+))?', match.group(2))
        location = None
        if line_match:
            start = int(line_match.group(1))
            end = int(line_match.group(2) or start)
            location = next((locations[line] for line in range(start, end + 1) if line in locations), None)
        question = f", domanda {location[0]} ({location[1]})" if location else ""
        warnings.append(
            f"{label}{question}: possibile invasione dell'area di lettura ottica "
            f"(Overfull hbox: {amount:.1f} pt). Controllare il PDF."
        )
    return warnings

def _compile_pdf_with_warnings(document, filename, compiler_args, label):
    document.generate_pdf(
        filepath=filename, compiler='latexmk', compiler_args=compiler_args,
        clean=False, clean_tex=False,
    )
    with open(f"{filename}.log", encoding="utf-8", errors="replace") as log_file:
        log = log_file.read()
    with open(f"{filename}.tex", encoding="utf-8") as tex_file:
        tex = tex_file.read()
    warnings = _overfull_warnings(log, tex, label)
    for extension in ('.tex', '.aux', '.log', '.out', '.fls', '.fdb_latexmk', '.xdv'):
        try:
            os.remove(f"{filename}{extension}")
        except FileNotFoundError:
            pass
    return warnings

# A4 size, portrait is 595pt x 842pt
A4SIZE = { 'width': 595, 'height': 842 }
# A3 size, landscape is 1190pt x 842pt
A3SIZE = { 'width': 1190, 'height': 842 }

def assemble_booklet(pages):
    """assembles the booklet according to the order [-1, 0, 1, -2] and going in.

    Assumes an iterable with the length a multiple of 4"""
    pages = deque(pages)
    assert not len(pages) % 4, 'booklet length, must be a multiple of 4'
    while pages:
        selection = pages.pop(), pages.popleft(), pages.popleft(), pages.pop()
        yield from selection

def grouper(iterable, n, fillvalue=None):
    """Collect data into fixed-length chunks or blocks

    grouper('ABCDEFG', 3, 'x') --> ABC DEF Gxx"
    https://docs.python.org/3/library/itertools.html#itertools-recipes
    """
    args = [iter(iterable)] * n
    return zip_longest(*args, fillvalue=fillvalue)

def make_book(pages, booklet_length):
    '''Combines all the lists into one and returns the result.'''
    assert not booklet_length % 4, 'booklet length, must be a multiple of 4'
    for group in grouper(pages, booklet_length, fillvalue=None):
        yield tuple(assemble_booklet(group))
        # yield from assemble_booklet(group) # dependent on the output you want

class Generate:
    """
    This class is responsible of creating the individual exams for a number of students
    or an overall testing document for checking the questions and their answers.
    """

    def __init__(self, config, questions, output_prefix, test=False, paper='A4', students=None, exam_date=dt.now(), seed=None, split=None, folded=True, rotated=False, dyslexia_count=None, progress_callback=None, warning_callback=None, check_overfull=False):
        validate_profiles(config)
        if config.get("profiles") and dyslexia_count is not None:
            raise ValueError("Con i profili, la dislessia si configura per profilo, non per le prime N righe")
        self.config = config
        self.questions_path = questions
        self.output_prefix = output_prefix
        self.progress_callback = progress_callback
        self.warning_callback = warning_callback
        self.check_overfull = check_overfull
        self.dyslexia_count = dyslexia_count
        self.test = test
        self.paper = paper.upper()
        if self.paper not in ('A4', 'A3'):
            raise AttributeError('paper value should be either "A3" or "A4"')
        # TODO: emit logging if these parameters are not set
        if not self.test:
            self.output_list_filename = f"{output_prefix}.json"
            self.students = students or []
            if config.get("profiles"):
                self.students = [
                    (student[0], student[1], student[2] if len(student) > 2 else config["profile_selector"]["default"])
                    for student in self.students
                ]
                for student in self.students:
                    if student[2] not in config["profiles"]:
                        raise ValueError(f"Profilo inesistente: {student[2]}")
            self.exam_date = exam_date
            self.topics = {}
            self.seed = config.get("seed", 42) if seed is None else seed
            if isinstance(self.seed, bool) or not isinstance(self.seed, int):
                raise ValueError("Il seed della generazione deve essere un intero")
            with TinyDB(self.output_list_filename) as db:
                db.drop_table('metadata')
                db.drop_table('exams')
                db.table('metadata').insert({ 'seed': self.seed, 'generation_date': dt.now().strftime("%F") })
            self.folded = folded
            self.rotated = rotated
            self.split = split

    def load_rules(self):
        rules = self.config.get('questions', [{ "from": "*.md", "draw": 1 }])
        rules_expanded = []
        # first expand the generic rules, then the more specific ones
        for rule in filter(lambda r: re.search(r'[\*\?]', r['from']), rules):
            for filename in glob.glob(os.path.join(self.questions_path, rule['from'])):
                rules_expanded.append((filename, rule))
        for rule in filter(lambda r: not re.search(r'[\*\?]', r['from']), rules):
            rules_expanded.append((os.path.join(self.questions_path, rule['from']), rule))
        return rules_expanded

    @staticmethod
    def _question_range_bounds(question_count, rule):
        question_range = rule.get('range') if rule else None
        if question_range is None:
            return 0, question_count
        if not isinstance(question_range, (list, tuple)) or len(question_range) != 2:
            raise ValueError("L'intervallo deve contenere un indice iniziale e uno finale")
        if all(value is None or (isinstance(value, str) and not value.strip()) for value in question_range):
            return 0, question_count

        def parse_bound(value, default, label):
            if value is None or (isinstance(value, str) and not value.strip()):
                return default
            if isinstance(value, bool):
                raise ValueError(f"L'indice {label} dell'intervallo deve essere un intero positivo")
            try:
                parsed = int(value)
            except (OverflowError, TypeError, ValueError):
                raise ValueError(f"L'indice {label} dell'intervallo deve essere un intero positivo") from None
            if parsed < 1 or (isinstance(value, float) and not value.is_integer()):
                raise ValueError(f"L'indice {label} dell'intervallo deve essere un intero positivo")
            return parsed

        start = parse_bound(question_range[0], 1, "iniziale")
        end = parse_bound(question_range[1], question_count, "finale")
        end_is_set = question_range[1] is not None and not (
            isinstance(question_range[1], str) and not question_range[1].strip()
        )
        if end_is_set and end < start:
            raise ValueError("L'indice finale deve essere maggiore o uguale a quello iniziale")
        return start - 1, end

    def _load_question_entries(self, filename, rule=None, open_questions=False):
        with open(filename, 'r', encoding='utf-8') as f:
            questions = [
                question
                for question in QUESTION_MARKER_RE.split(f.read())
                if question.strip() and not TITLE_RE.match(question)
            ]
        range_start, range_end = self._question_range_bounds(len(questions), rule)
        entries = []
        type_index = 0
        for index, question in enumerate(questions):
            is_open_question = bool(OPEN_QUESTION_RE.match(question))
            if index >= range_start and index < range_end and is_open_question == open_questions:
                entries.append((question, type_index))
            if is_open_question == open_questions:
                type_index += 1
        return entries

    def load_questions(self, filename, rule=None):
        return [question for question, _ in self._load_question_entries(filename, rule)]

    def load_open_questions(self, filename, rule=None):
        return [
            question
            for question, _ in self._load_question_entries(filename, rule, open_questions=True)
        ]

    def load_topics(self, filename):
        with open(filename, 'r', encoding='utf-8') as f:
            questions_with_topics = filter(lambda q: TOPIC_RE.match(q), QUESTION_MARKER_RE.split(f.read()))
            topics = set()
            for q in questions_with_topics:
                topics.add(TOPIC_RE.match(q).group(1))
            return topics

    def process(self):
        if not self.test:
            self.generate_exams()
        else:
            self.generate_test()

    def generate_exams(self):
        self.profile_generators = {}
        if self.config.get("profiles"):
            self.questions, self.open_questions = {}, {}
            for profile in {student[2] for student in self.students}:
                generator = copy(self)
                generator.config = config_for_profile(self.config, profile)
                generator._load_question_banks()
                if not any(
                    topic["draw"] > 0 and topic["content"]
                    for bank in (generator.questions, generator.open_questions)
                    for topic in bank.values()
                ):
                    raise ValueError(f"La variante {profile} non include domande")
                self.profile_generators[profile] = generator
        else:
            self._load_question_banks()
        self._generate_loaded_exams()

    def _load_question_banks(self):
        rules = self.load_rules()
        self.questions = {}
        self.open_questions = {}
        for rule_index, (r, rule_data) in enumerate(sorted(rules, key=lambda rule: rule[0])):
            rule_key = (os.path.basename(r), rule_index)
            draw = rule_data.get('draw', rule_data.get('use', 1))
            closed_questions = self._load_question_entries(r, rule_data)
            open_questions = self._load_question_entries(r, rule_data, open_questions=True)
            topic = {'filename': os.path.basename(r), 'draw': draw}
            self.questions[rule_key] = {
                **topic,
                'content': [question for question, _ in closed_questions],
                'indices': [index for _, index in closed_questions],
            }
            self.open_questions[rule_key] = {
                **topic,
                'content': [question for question, _ in open_questions],
                'indices': [index for _, index in open_questions],
            }

    def _generate_loaded_exams(self):
        logger.info('Creating and preparing tmp directory')
        if self.open_questions:
            logger.info('There are open questions')
        if os.path.exists('tmp'):
            rmtree('tmp')
        os.mkdir('tmp')
        with pkg_resources.path("omrexams.texmf", "omrexam.cls") as template_path:
            # Copy the file to the temporary directory
            click.secho(f'Copying omrexam.cls to tmp', fg='yellow')
            copy2(template_path, 'tmp')
            qr_eclevel = self.config.get('exam', {}).get('qr_eclevel', 'H')
            qr_size = '2.8cm' if qr_eclevel == 'H' else '2.5cm'
            cls_path = os.path.join('tmp', 'omrexam.cls')
            with open(cls_path, 'r', encoding='utf-8') as f:
                cls_content = f.read()
            cls_content = re.sub(r'\\setlength\{\\OMR@BarcodeWidth\}\{.*?\}', lambda m: f'\\setlength{{\\OMR@BarcodeWidth}}{{{qr_size}}}', cls_content)
            cls_content = re.sub(r'\\setlength\{\\OMR@BarcodeHeight\}\{.*?\}', lambda m: f'\\setlength{{\\OMR@BarcodeHeight}}{{{qr_size}}}', cls_content)
            cls_content = re.sub(r'eclevel=[LMQH]', f'eclevel={qr_eclevel}', cls_content)
            with open(cls_path, 'w', encoding='utf-8') as f:
                f.write(cls_content)
        click.secho(f'Generating {len(self.students)} exams (this may take a while)', fg='red', underline=True)
        with click.progressbar(length=len(self.students), label='Generating exams',
                               bar_template='%(label)s |%(bar)s| %(info)s',
                               fill_char=click.style(u'█', fg='cyan'),
                               empty_char=' ', show_pos=True) as bar:
            self.generate_tasks_queue = mp.JoinableQueue()
            self.results_mutex = mp.RLock()
            self.generate_task_done = mp.Condition(self.results_mutex)
            self.results = mp.Value('i', 0, lock=self.results_mutex)
            self.error = mp.Value('b', False, lock=self.results_mutex)
            self.failures = mp.Queue()
            self.layout_warnings = mp.Queue()
            worker_count = min(mp.cpu_count(), max(1, len(self.students)))
            for i, student in enumerate(self.students):
                is_dyslexic = False
                if self.profile_generators:
                    is_dyslexic = self.profile_generators[student[2]].config.get('dyslexia', False)
                elif self.config.get('dyslexia', False):
                    if self.dyslexia_count is None or i < self.dyslexia_count:
                        is_dyslexic = True
                self.generate_tasks_queue.put((i, student, is_dyslexic))
            for _ in range(worker_count):
                self.generate_tasks_queue.put((None, None, None))
            workers = []
            prev = 0
            try:
                for _ in range(worker_count):
                    worker = mp.Process(target=self.worker_main_generate)
                    worker.start()
                    workers.append(worker)
                while prev < len(self.students):
                    with self.generate_task_done:
                        self.generate_task_done.wait_for(lambda: self.results.value > prev, timeout=1)
                        current = self.results.value
                    if any(worker.exitcode not in (None, 0) for worker in workers):
                        raise RuntimeError("Exam generation worker terminated unexpectedly")
                    if current > prev:
                        bar.update(current - prev)
                        if self.progress_callback:
                            self.progress_callback(current, len(self.students), 'Generating exams')
                        prev = current
                    elif not any(worker.is_alive() for worker in workers):
                        raise RuntimeError("Exam generation workers stopped before completing all tasks")
                    self._drain_layout_warnings()
                for worker in workers:
                    while worker.is_alive():
                        self._drain_layout_warnings()
                        worker.join(timeout=0.1)
                self._drain_layout_warnings()
                if any(worker.exitcode != 0 for worker in workers):
                    raise RuntimeError("Exam generation worker terminated unexpectedly")
            except BaseException:
                for worker in workers:
                    if worker.is_alive():
                        worker.terminate()
                for worker in workers:
                    worker.join()
                raise
            if self.error.value:
                try:
                    error = self.failures.get(timeout=1)
                except Empty:
                    raise RuntimeError("Exam generation failed without an error message") from None
                raise RuntimeError(f"Exam generation failed: {error}")

        click.secho('Collating PDF', fg='red', underline=True)
        pdf_files = sorted(glob.glob(os.path.join('tmp', '*.pdf')))
        if self.split is not None:
            num_chunks = math.ceil(len(pdf_files) / self.split)
            click.secho(f'Splitting output files every {self.split} exams, chunks {num_chunks}', fg='yellow')
            digits = max(1, len(str(num_chunks)))
            tasks = []
            for i in range(0, len(pdf_files), self.split):
                tasks.append((f"{self.output_prefix}-{(i // self.split) + 1:0{digits}d}.pdf", pdf_files[i:i + self.split]))

            chunksize = max(1, len(tasks) // mp.cpu_count())
            with mp.Pool(mp.cpu_count()) as pool, click.progressbar(length=num_chunks, label='Chunks of exam files',
                               bar_template='%(label)s |%(bar)s| %(info)s',
                               fill_char=click.style(u'█', fg='cyan'),
                               empty_char=' ', show_pos=True) as bar:
                current_chunk = 0
                for _ in pool.imap_unordered(partial(_collate_star, paper=self.paper, rotated=self.rotated, folded=self.folded), tasks, chunksize=chunksize):
                    bar.update(1)
                    current_chunk += 1
                    if self.progress_callback:
                        self.progress_callback(current_chunk, num_chunks, 'Chunks of exam files')
        else:
            with click.progressbar(length=len(pdf_files), label='Exam files',
                               bar_template='%(label)s |%(bar)s| %(info)s',
                               fill_char=click.style(u'█', fg='cyan'),
                               empty_char=' ', show_pos=True) as bar:
                if self.paper == 'A4':
                    Generate.collate_exams_a4(f"{self.output_prefix}.pdf", pdf_files, bar=bar, progress_callback=self.progress_callback)
                else:
                    Generate.collate_exams_a3(f"{self.output_prefix}.pdf", pdf_files, bar=bar, folded=self.folded, rotated=self.rotated, progress_callback=self.progress_callback)


        if not self.error.value:
            click.secho('Removing tmp', fg='yellow')
            # rmtree('tmp') # original row
            for _ in range(5): # try 5 times
                try:
                    rmtree('tmp')
                    break # if it has happened it exit the loop
                except OSError:
                    time.sleep(0.5) # wait 0.5 seconds than try again
            else:
                click.secho('Could not remove tmp directory, please remove it manually.', fg='red')
        click.secho('Finished', fg='red', underline=True)

    def _report_layout_warning(self, warning):
        logger.warning(warning)
        if self.warning_callback:
            self.warning_callback(warning)

    def _compile_pdf(self, document, filename, compiler_args, label):
        if self.check_overfull:
            return _compile_pdf_with_warnings(document, filename, compiler_args, label)
        document.generate_pdf(filepath=filename, compiler='latexmk', compiler_args=compiler_args)
        try:
            os.remove(f"{filename}.xdv")
        except FileNotFoundError:
            pass
        return []

    def _drain_layout_warnings(self):
        while True:
            try:
                warning = self.layout_warnings.get_nowait()
            except Empty:
                return
            self._report_layout_warning(warning)

    def worker_main_generate(self):
        while True:
            task, student, is_dyslexic = self.generate_tasks_queue.get()
            if task is None:
                self.generate_tasks_queue.task_done()
                break
            if self.error.value:
                with self.generate_task_done:
                    self.results.value += 1
                    self.generate_task_done.notify()
                self.generate_tasks_queue.task_done()
                continue
            logger.info(f'Started processing student {student[0]} {student[1]}')
            try:
                s = int(student[0])
            except:
                # To capture strange matriculation numbers
                hashed_result = hashlib.md5(student[0].encode()).hexdigest()
                s = ord(hashed_result[0])
            random.seed(self.seed + s)
            generator = self.profile_generators.get(student[2], self) if len(student) > 2 else self
            done = False
            try:
                for _ in range(50):
                    document, questions, answers = generator.create_exam(student, is_dyslexic)
                    digits = math.ceil(math.log10(len(self.students)))
                    f = f'{{:0{digits}d}}-{{}}-{{}}'
                    filename = os.path.join('tmp', f"{task:0{digits}d}-{student[0]}-{student[1].replace(' ', '_')}")
                    warnings = self._compile_pdf(
                        document, filename, ['-xelatex', '-shell-escape'],
                        f"Esame {task + 1} (matricola {student[0]}, {student[1]})"
                    )
                    # check the generated output in terms of pages
                    # TODO: it should be done also in terms of the qrcode, number of questions, coherence of answers
                    with open(f"{filename}.pdf", 'rb') as f:
                        pdf_file = PdfReader(f, strict=False)
                        limit = generator.config['exam'].get('page_limits')
                        if not limit or len(pdf_file.pages) <= int(limit):
                            done = True
                            break
                if not done:
                    click.secho(f"Couldn't get an exam with at most {limit} pages for student {student[0]} {student[1]}", fg='red', blink=True)
                    logger.warning(f"Couldn't get an exam with at most {limit} pages for student {student[0]} {student[1]}")
                    raise RuntimeError(f"Couldn't generate an exam within the {limit}-page limit")
                for warning in warnings:
                    self.layout_warnings.put(warning)
                with self.results_mutex:
                    self.append_exam(student, questions, answers)
            except Exception as e:
                with self.results_mutex:
                    if not self.error.value:
                        self.failures.put(f"{type(e).__name__}: {str(e)[:4000]}")
                        self.error.value = True
            finally:
                with self.generate_task_done:
                    self.results.value += 1
                    self.generate_task_done.notify()
                self.generate_tasks_queue.task_done()


    @staticmethod
    def collate_exams_a4(filename, pdf_files, bar=None, progress_callback=None):
        # This is for A4 management
        merger = PdfWriter()
        _blank = PdfWriter()
        _blank.add_blank_page(**A4SIZE)
        blank = io.BytesIO()
        _blank.write(blank)

        opened_files = [] # Track opened files

        current_file = 0
        try:
            for exam in pdf_files:
                f = open(exam, 'rb')
                opened_files.append(f) # Save the file reference
                pdf = PdfReader(f, strict=False)
                merger.append(pdf)
                if len(pdf.pages) % 2 == 1:
                    merger.append(blank)
                if bar:
                    bar.update(1)
                current_file += 1
                if progress_callback:
                    progress_callback(current_file, len(pdf_files), 'Collating exams (A4)')

            with open(filename, 'wb') as f:
                merger.write(f)
        finally:
            # FILE CLOSURE: Tell the OS to unlock the files
            for f in opened_files:
                f.close()

    @staticmethod
    def collate_exams_a3(filename, pdf_files, folded=True, rotated=False, bar=None, progress_callback=None):
        # This is for A3 management
        writer = PdfWriter()
        a3page = None

        opened_files = [] # Track opened files

        current_file = 0
        try:
            for exam in pdf_files:
                f = open(exam, 'rb')
                opened_files.append(f) # Save the file reference
                pdf = PdfReader(f, strict=False)
                a3page = PageObject.create_blank_page(**A3SIZE)
                rotate = False
                if folded:
                    # Create a booklet
                    sheets = math.ceil(len(pdf.pages) / 4)
                    booklet = next(make_book(range(1, len(pdf.pages) + 1), sheets * 4))
                    left = True
                    for p in booklet:
                        if p is not None:
                            page = pdf.pages[p - 1]
                        else:
                            page = PageObject.create_blank_page(**A4SIZE)
                        if left:
                            # page left
                            if rotated and rotate:
                                transformation = Transformation().rotate(180).translate(A3SIZE['width'] / 2,  A3SIZE['height'])
                            else:
                                transformation = Transformation().translate(0, 0)
                            left = False
                        else:
                            # page right
                            if rotated and rotate:
                                transformation = Transformation().rotate(180).translate(A3SIZE['width'], A3SIZE['height'])
                            else:
                                transformation = Transformation().translate(A3SIZE['width'] / 2, 0)
                            left = True
                        a3page.merge_transformed_page(page, transformation, expand=False)
                        if left:
                            # add page
                            writer.add_page(a3page)
                            a3page = PageObject.create_blank_page(**A3SIZE)
                            rotate = not rotate
                else:
                    # normal A3, two A4 for A3
                    for p, page in enumerate(pdf.pages):
                        if p % 2 == 0:
                            # page left
                            transformation = Transformation().translate(0, 0)
                            a3page.merge_transformed_page(page, transformation, expand=False)
                        else:
                            # page right
                            transformation = Transformation().translate(A3SIZE['width'] / 2, 0)
                            a3page.merge_transformed_page(page, transformation, expand=False)
                            # add page
                            writer.add_page(a3page)
                            a3page = PageObject.create_blank_page(**A3SIZE)

                if bar:
                    bar.update(1)
                current_file += 1
                if progress_callback:
                    progress_callback(current_file, len(pdf_files), 'Collating exams (A3)')

            with open(filename, 'wb') as f:
                writer.write(f)

        finally:
            # FILE CLOSURE: Release all source files
            for f in opened_files:
                f.close()

    def draw_questions(self, Q):
        questions = []
        for filename, topic in Q:
            source_filename = topic.get('filename', filename)
            candidate_questions = list((q, i) for i, q in enumerate(topic['content']))
            current_questions = []
            while candidate_questions:
                t = candidate_questions.pop()
                topic_mutually_exclusive = [t]
                q = re.search(QUESTION_RE, t[0])
                if not q:
                    raise RuntimeError(f"Apparently, question \"{t[0]}\" in filename {source_filename} has no text")
                q_id = q.group(2).strip() if q.group(2) else None
                q = q.group(1).strip().lower()
                j = 0
                while j < len(candidate_questions):
                    cq = re.search(QUESTION_RE, candidate_questions[j][0])
                    if not cq:
                        raise RuntimeError(f"Apparently, question \"{topic['content'][j]}\" in filename {source_filename} has no text")
                    cq_id = cq.group(2).strip() if cq.group(2) else None
                    if q == cq.group(1).strip().lower() or (q_id is not None and  q_id == cq_id):
                        topic_mutually_exclusive.append(candidate_questions.pop(j))
                    else:
                        j = j + 1
                current_questions += random.sample(topic_mutually_exclusive, 1)
            sample = random.sample(list(range(len(current_questions))), min(topic['draw'], len(current_questions)))
            questions += list(map(
                lambda index: (
                    source_filename,
                    topic.get('indices', list(range(len(topic['content']))))[current_questions[index][1]],
                    current_questions[index][0],
                ),
                sample,
            ))
        return questions


    def create_exam(self, student, is_dyslexic=False):
        def code_answer(answers):
                current = ""
                for i in range(len(answers)):
                    if answers[i]:
                        current += chr(ord('A') + i)
                return current
        logger.info(f'Creating exam {student[0]} {student[1]}')
        # randomly select a given number of questions from each file
        # however, avoid to select more than once the questions with the same text
        questions = self.draw_questions(self.questions.items())
        if self.config['exam'].get('shuffle_questions', False):
            random.shuffle(questions)
        limit = self.config['exam'].get('max_questions')
        if limit or (self.config.get("profiles") and type(limit) is int):
            questions = questions[:limit]

        open_questions = self.draw_questions(self.open_questions.items())
        if self.config['exam'].get('shuffle_questions', False):
            random.shuffle(open_questions)
        limit = self.config['exam'].get('max_open_questions')
        if limit or (self.config.get("profiles") and type(limit) is int):
            open_questions = open_questions[:limit]

        total = self.config['exam'].get('max_total_questions')
        if total is not None:
            questions = questions[:total]
            open_questions = open_questions[:max(0, total - len(questions))]

        if self.config.get('header'):
            with DocumentStripRenderer(basedir=self.config.get('basedir')) as renderer:
                header = renderer.render(Document(self.config.get('header')))
        else:
            header = ''
        if self.config.get('preamble'):
            with DocumentStripRenderer(basedir=self.config.get('basedir')) as renderer:
                preamble = renderer.render(Document(self.config.get('preamble')))
        else:
            preamble = ''
        if self.config.get('footer'):
            with DocumentStripRenderer(basedir=self.config.get('basedir')) as renderer:
                footer = renderer.render(Document(self.config.get('footer')))
        else:
            footer = ''
        with QuestionRenderer(language=self.config['exam'].get('language'),
                              date=self.exam_date, exam=self.config['exam'].get('name'),
                              student_no=student[0],
                              student_name=student[1] if student[1] != 'Additional student' else '_' * 20,
                              header=header,
                              preamble=preamble,
                              footer=footer,
                              packages=self.config.get('packages', {}),
                              commands=self.config.get('commands', {}),
                              shuffle=self.config['exam'].get('shuffle_answers', True),
                              dyslexia=is_dyslexic,
                              easyreading=self.config.get('easyreading', False),
                              circled=self.config.get('choices', {}).get('circled', False),
                              usesf=self.config.get('choices', {}).get('usesf', False),
                              oneparchoices=self.config.get('choices', {}).get('oneparchoices', False),
                              basedir=os.path.realpath(self.questions_path)) as renderer:
            content = '---\n' + '\n---\n'.join(map(lambda q: q[2], questions)) + '\n---\n'
            if open_questions:
                open_qs = list(map(lambda q: q[2], open_questions))
                if open_qs and self.config.get('exam', {}).get('separate_open_questions', False):
                    open_qs[0] = '[NEWPAGE]\n' + open_qs[0]
                content += '\n---\n'.join(open_qs) + '\n---\n'
            document = renderer.render(Document(content))
            tmp = list(map(lambda i: (*questions[i][:2], code_answer(renderer.questions[i]['answers']), renderer.questions[i]['permutation']), range(len(questions))))
#            tmp += list(map(lambda i: (*open_questions[i][:2], code_answer(renderer.questions[i + len(questions)]['answers']), renderer.questions[i + len(questions)]['permutation']), range(len(open_questions))))
            overall_answers = list(code_answer(q['answers']) for q in renderer.questions)
            return document, tmp, overall_answers

    def append_exam(self, student, questions, answers):
        data = {
            "student_id": str(student[0]),
            "fullname": student[1],
            "questions": []
        }
        for q in questions:
            data['questions'].append(q)
        data['answers'] = answers
        if len(student) > 2:
            data['profile'] = student[2]
        with TinyDB(self.output_list_filename) as db:
#            import json
#            print(json.dumps(data))
            db.table('exams').insert(data)

    def generate_test(self):
        rules = self.load_rules()
        if self.config.get("profiles"):
            preview = copy(self)
            preview.config = {**self.config, "questions": list(question_rules(self.config))}
            rules = preview.load_rules()
        self.topics = {}
        rule_files = sorted({filename for filename, _ in rules})
        for r in rule_files:
            self.topics[os.path.basename(r)] = self.load_topics(r)
        for n, t in self.topics.items():
            click.secho(f"Topics of {n} {len(t)}")
        if self.config.get('header'):
            with DocumentStripRenderer(basedir=self.config.get('basedir')) as renderer:
                header = renderer.render(Document(self.config.get('header')))
        else:
            header = ''
        if self.config.get('preamble'):
            with DocumentStripRenderer(basedir=self.config.get('basedir')) as renderer:
                preamble = renderer.render(Document(self.config.get('preamble')))
        else:
            preamble = ''
        if self.config.get('footer'):
            with DocumentStripRenderer(basedir=self.config.get('basedir')) as renderer:
                footer = renderer.render(Document(self.config.get('footer')))
        else:
            footer = ''

        questions = ""
        for r in rule_files:
            click.secho(f'Testing {os.path.basename(r)}', fg='cyan')
            with open(r, 'r', encoding='utf-8') as f:
                current_questions = f.read()
            with QuestionRenderer(language=self.config['exam'].get('language'),
                              date=dt.now(),
                              exam=self.config['exam'].get('name'),
                              header=header,
                              preamble=preamble,
                              footer=footer,
                              packages=self.config.get('packages', {}),
                              commands=self.config.get('commands', {}),
                              test=True,
                              dyslexia=self.config.get('dyslexia', False),
                              easyreading=self.config.get('easyreading', False),
                              circled=self.config.get('choices', {}).get('circled', False),
                              basedir=os.path.realpath(self.questions_path)) as renderer:
                renderer.render(Document(current_questions))
            questions += current_questions + "\n\n"

        logger.info('Creating and preparing tmp directory')
        if not os.path.exists('tmp'):
           os.mkdir('tmp')
        with pkg_resources.path("omrexams.texmf", "omrexam.cls") as template_path:
            # Copy the file to the temporary directory
            click.secho(f'Copying omrexam.cls to tmp', fg='yellow')
            copy2(template_path, 'tmp')
            qr_eclevel = self.config.get('exam', {}).get('qr_eclevel', 'H')
            qr_size = '2.8cm' if qr_eclevel == 'H' else '2.5cm'
            cls_path = os.path.join('tmp', 'omrexam.cls')
            with open(cls_path, 'r', encoding='utf-8') as f:
                cls_content = f.read()
            cls_content = re.sub(r'\\setlength\{\\OMR@BarcodeWidth\}\{.*?\}', lambda m: f'\\setlength{{\\OMR@BarcodeWidth}}{{{qr_size}}}', cls_content)
            cls_content = re.sub(r'\\setlength\{\\OMR@BarcodeHeight\}\{.*?\}', lambda m: f'\\setlength{{\\OMR@BarcodeHeight}}{{{qr_size}}}', cls_content)
            cls_content = re.sub(r'eclevel=[LMQH]', f'eclevel={qr_eclevel}', cls_content)
            with open(cls_path, 'w', encoding='utf-8') as f:
                f.write(cls_content)
        with QuestionRenderer(language=self.config['exam'].get('language'),
                              date=dt.now(),
                              exam=self.config['exam'].get('name'),
                              student_no=0,
                              student_name="",
                              header=header,
                              preamble=preamble,
                              footer=footer,
                              packages=self.config.get('packages', {}),
                              commands=self.config.get('commands', {}),
                              test=True,
                              dyslexia=self.config.get('dyslexia', False),
                              easyreading=self.config.get('easyreading', False),
                              circled=self.config.get('choices', {}).get('circled', False),
                              basedir=os.path.realpath(self.questions_path)) as renderer:
            document = renderer.render(Document(questions))
        click.secho('Generating PDF with all corrected questions', fg='red', underline=True)
        filename = ".".join(os.path.basename(f"{self.output_prefix}.pdf").split(".")[:-1])
        warnings = self._compile_pdf(
            document, os.path.join("tmp", filename), ['-xelatex'], "Anteprima"
        )
        for warning in warnings:
            self._report_layout_warning(warning)
        output_dir = os.path.dirname(self.output_prefix) if os.path.dirname(self.output_prefix) else '.'
        copy2(os.path.join('tmp',  f"{filename}.pdf"), output_dir)


def _collate_star(args, paper, rotated=False, folded=True):
    """
    Helper for multiprocessing collate_exams
    """
    out_path, files = args
    if paper == "A4":
        return Generate.collate_exams_a4(out_path, files)
    else:
        return Generate.collate_exams_a3(out_path, files, folded=folded, rotated=rotated)

