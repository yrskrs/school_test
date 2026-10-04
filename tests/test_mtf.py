"""Run: .venv/bin/python tests/test_mtf.py"""
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.services.mtf_service import decode_mtf, mtf_to_xml, rtf_text
from mtf_fixture import build_mtf, encrypt_body, record, string


class MtfConversion(unittest.TestCase):
    def test_all_types_and_correct_arrays(self):
        root = ET.fromstring(mtf_to_xml(build_mtf()))
        questions = root.findall('.//Task')
        self.assertEqual(root.findtext('Title'), 'Synthetic MTF')
        self.assertEqual(len(questions), 8)
        self.assertEqual([v.get('CorrectAnswer') for v in questions[0].find('Variants')], ['False', 'True'])
        self.assertEqual([v.get('CorrectAnswer') for v in questions[2].find('Variants')], ['2', '1'])
        self.assertEqual([v.get('CorrectAnswer') for v in questions[3].find('Variants')], ['2', '1'])
        self.assertEqual(questions[4].findtext('InputText/Value'), '2,0;2.0')
        self.assertEqual(questions[5].findtext('InputNum/Value'), '16')
        self.assertEqual(questions[6].findtext('Regions/Region'), '(0, 0)-(2, 0)-(2, 2)-(0, 2)')
        self.assertTrue(questions[6].findtext('QuestionImage'))
        self.assertEqual(len(questions[7].findall('.//VariantImage')), 2)
        self.assertEqual(questions[7].findtext('Variants2/VariantText/PlainText'), 'A')

    def test_rejects_corruption_password_version_and_size(self):
        for data in [b'', b'not an MTF', build_mtf()[:-1], encrypt_body(b'', '10.1.0.0')]:
            with self.subTest(data=data[:20]), self.assertRaises(ValueError):
                decode_mtf(data)
        with patch('app.services.mtf_service.MAX_MTF_BYTES', 10), self.assertRaises(ValueError):
            decode_mtf(build_mtf())
        with patch('app.services.mtf_service.MAX_BODY_BYTES', 100), self.assertRaises(ValueError):
            decode_mtf(build_mtf())

    def test_unsupported_question_is_not_silently_removed(self):
        with self.assertRaisesRegex(ValueError, 'Запитання 9'):
            mtf_to_xml(build_mtf(record(5, b'')))
        with self.assertRaisesRegex(ValueError, 'порожня'):
            mtf_to_xml(build_mtf(record(7, string('') + bytes(2))))

    def test_rtf_unicode_codepages_and_literals(self):
        self.assertEqual(rtf_text(r"{\rtf1\ansi\ansicpg1251{\fonttbl{\f0 Hidden;}}\'d2\'e5\'f1\'f2\par \u1111? \{x\} \\}"),
                         'Тест\nї {x} \\')
        self.assertEqual(rtf_text(r"{\rtf1\ansicpg1251{\fonttbl{\f0\fcharset204 Cyrillic;}{\f1\fcharset161 Greek;}}\f0\'f0\f1\'f0}"), 'рπ')


if __name__ == '__main__':
    unittest.main(verbosity=2)
