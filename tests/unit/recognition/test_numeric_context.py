from unittest.mock import Mock

from PIL import Image
import pytest

from ocr_recognition.numeric_context import retry_numeric_context


def test_incomplete_group_uses_model_consensus_and_keeps_original_raw_number():
    def read(images):
        return ['3.420.741']*len(images), [.94]*len(images)
    _, texts, _, audit = retry_numeric_context([Image.new('RGB', (105, 28))], ['3.420.74'], [.9358], read)
    assert texts == ['3.420.741']
    assert audit[0]['baseline']['text_raw'] == '3.420.74'
    assert audit[0]['applied'] and audit[0]['numeric_disagreement']


@pytest.mark.parametrize('text', ['7.547', '12.271', '1234.56', '1.234,56', '120', '2024', '(1.234.567)'])
def test_complete_groups_and_decimal_formats_do_not_trigger_retry(text):
    read = Mock()
    _, values, _, audit = retry_numeric_context([Image.new('RGB', (105, 28))], [text], [.95], read)
    assert values == [text] and not audit
    read.assert_not_called()


@pytest.mark.parametrize('candidate', ['3.421.741', '3,420,741', '-3.420.741', '9.420.741', '3.420.741 thừa'])
def test_changed_internal_digits_signs_or_extra_words_cannot_replace_baseline(candidate):
    def read(images):
        return [candidate]*len(images), [.99]*len(images)
    _, texts, _, audit = retry_numeric_context([Image.new('RGB', (105, 28))], ['3.420.74'], [.9358], read)
    assert texts == ['3.420.74'] and not audit[0]['applied']
