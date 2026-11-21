from ocr_contracts.page import ExtractedPage
from ocr_contracts.page_type import PageType
from ocr_contracts.table import TableCell,TableStructure
from ocr_contracts.text_block import TextBlock
from ocr_contracts.quality import PageQualityReport
from ocr_document.source_identity import lock_text_evidence
from ocr_document.resolve_document_layout import _reconcile_evidence
from ocr_structure.financial_tables.provenance import bind_sources


def fixture():
    block=TextBlock(text='Năm 2024',text_raw='Năm 2024',bbox=[10,10,100,25],page=1)
    page=ExtractedPage(page_num=1,width=300,height=400,page_type=PageType.MIXED,blocks=[block],
                       quality=PageQualityReport(page=1,page_type='mixed'))
    lock_text_evidence(page.blocks,'a'*16,1,page.metadata)
    table=TableStructure(table_id='geometry',page=1,bbox=list(block.bbox),num_rows=1,num_cols=1,
      engine='deterministic_geometry',cells=[TableCell(row_idx=0,col_idx=0,text=block.text,bbox=list(block.bbox))])
    assert bind_sources(table,[block],[block])
    page.tables=[table]
    return page


def test_exact_bound_source_is_not_repeated_outside_its_table():
    page=fixture();_reconcile_evidence(page)
    assert page.blocks==[]
    assert next(iter(page.metadata['source_dispositions'].values()))['status']=='REPLACED_BY_TABLE'
    assert page.metadata['text_evidence']['items'][0]['text_raw']=='Năm 2024'


def test_changed_cell_restores_raw_source_instead_of_hiding_it():
    page=fixture();page.tables[0].cells[0].text='Năm 2025'
    _reconcile_evidence(page)
    assert page.blocks[0].text_raw=='Năm 2024'
    assert next(iter(page.metadata['source_dispositions'].values()))['status']=='KEPT_FOR_REVIEW'
