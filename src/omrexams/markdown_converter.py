import xml.etree.ElementTree as ET
from markdownify import markdownify
import re
import os
import logging

logger = logging.getLogger("omrexams")

class MarkdownConverter:
    def __init__(self, moodle_file, questions_dir, output_name=None):
        self.tree = ET.parse(moodle_file)
        self.root = self.tree.getroot()
        self.category = MarkdownConverter.dispatch_category(self.root.find("question[@type='category']"))
        if output_name:
            self.file_name = os.path.join(questions_dir, output_name)
        else:
            self.file_name = os.path.join(questions_dir, re.sub(r'\s', r'_', self.category.lower()) + '.md')

    @staticmethod
    def dispatch_category(category):
        text = category.find('category/text').text.split('/')[-1]
        return text

    @staticmethod
    def dispatch_answer(answer):
        format = answer.get('format')
        fraction = float(answer.get('fraction'))
        text = answer.find('text').text
        if format == 'html':
            text = re.sub(r'<(/?)h[1-9]>', r'<\1strong>', text)
            text = markdownify(text)
        return (text, fraction > 0)

    @staticmethod
    def dispatch_question(question):
        qtype = question.get('type')
        tmp = question.find('questiontext')
        text = tmp.find('text').text
        if qtype != 'multichoice' and qtype != 'essay':
            message = f'Only multichoice and essay moodle questions are supported at present, not {qtype} (question {text})'
            logging.error(message)
            raise ValueError(message)
        format = tmp.get('format')
        if format == 'html':
            text = re.sub(r'<(/?)h[1-9]>', r'<\1strong>', text)
            text = markdownify(text)
        if qtype != 'essay':
            single = question.find('single').text == 'true'

            # Let's read all the answers
            raw_answers = []
            for a in question.findall('answer'):
                text_ans, _ = MarkdownConverter.dispatch_answer(a)
                fract = float(a.get('fraction'))
                raw_answers.append((text_ans, fract))

            if not raw_answers:
                # Fallback for XML files exported with old bug coding
                # open questions as empty multichoice.
                return (text, False, None)

            if single:
                max_fraction = max(a[1] for a in raw_answers)
                answers = [(a[0], a[1] == max_fraction and max_fraction > 0) for a in raw_answers]
                if sum(a[1] for a in answers) != 1:
                    message = f'Question is supposed to have a single correct answer (question {text})'
                    logging.error(message)
                    raise ValueError(message)
            else:
                answers = [(a[0], a[1] > 0) for a in raw_answers]

            shuffle = question.find('shuffleanswers').text == 'true'
            return (text, shuffle, answers)
        else:
            return (text, False, None)

    @staticmethod
    def translate_to_markdown(question):
        if question[2] is None: # essay question
            t = f'### {question[0]}'
            t += '\n\n{lines:2.5cm}'
            return t
        else:
            t = f'## {question[0]}'
            t += '\n\n'
            if question[1]:
                a_template = '- [{correct}] {text}'
            else:
                a_template = '* [{correct}] {text}'
            for a in question[2]:
                t += a_template.format(correct='x' if a[1] else ' ', text=a[0].replace('\n', ' ')) + '\n'
            return t

    def convert(self):
        questions = []
        for q in self.root.findall('question'):
            if q.get('type') != 'multichoice' and q.get('type') != 'essay':
                continue
            try:
                question = MarkdownConverter.dispatch_question(q)
                questions.append(question)
            except Exception as e:
                import traceback
                traceback.print_exc()
                continue
        with open(self.file_name, 'w', encoding='utf-8') as f:
            f.write('# ' + self.category.replace("\n", " ") + '\n')
            for q in questions:
                f.write('\n---\n\n')
                f.write(MarkdownConverter.translate_to_markdown(q))