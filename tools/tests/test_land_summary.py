"""Structural and file-safety regressions; arithmetic is checked in real Office."""
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from openpyxl import Workbook, load_workbook

SPEC = importlib.util.spec_from_file_location('land_summary', Path(__file__).resolve().parents[1] / 'land_summary.py')
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)


def workbook(keys=(), count=3):
    wb = Workbook()
    wd = wb.active
    wd.title = core.DETAIL
    ws = wb.create_sheet(core.SUMMARY)
    ws.merge_cells('A1:AF1')
    ws['A1'] = '合成土地汇总'
    ws['A9'] = '合计'
    ws['A10'] = '填表说明：保留本行'
    wd[f'A{6 + count}'] = '合计'
    wd[f'A{17 + count}'] = '填表说明：明细末行以显式参数为准'
    for i, key in enumerate(keys, 6):
        wd[f'A{i}'] = f'O-{i}'
        wd[f'BO{i}'], wd[f'BP{i}'] = key
    return wb


class LandSummaryTests(unittest.TestCase):
    def test_explicit_range_excludes_description_and_total(self):
        wb = workbook([('村A', '农用地')])
        result = core.install(wb, 8, True)
        self.assertEqual(result['last_detail_row'], 8)
        self.assertEqual(result['groups'], [('村A', '农用地')])
        formulas = [c.value for row in wb[core.SUMMARY] for c in row if c.data_type == 'f']
        self.assertFalse(any('$19' in f or '$20' in f for f in formulas))
        self.assertEqual(wb[core.DETAIL]['A20'].value, '填表说明：明细末行以显式参数为准')

    def test_invalid_boundary_fails_before_rebuild(self):
        for end in [5, 19, 8.0, True, 1048576]:
            wb = workbook()
            with self.assertRaises(ValueError):
                core.install(wb, end)
            self.assertEqual(wb[core.SUMMARY]['A9'].value, '合计')
            self.assertIsNone(wb[core.DETAIL]['BO5'].value)

    def test_total_inside_explicit_range_is_rejected(self):
        wb = workbook(count=4)
        wb[core.DETAIL]['A8'] = '合计'
        with self.assertRaises(ValueError):
            core.install(wb, 9, True)

    def test_interleaved_duplicate_keys_emit_one_group(self):
        keys = [('村A', '农用地'), ('村B', '建设用地'), ('村A', '农用地'), ('村A', '建设用地')]
        wb = workbook(keys, count=4)
        result = core.install(wb, 9, True)
        self.assertEqual(result['groups'], [keys[0], keys[1], keys[3]])
        self.assertEqual([wb[core.SUMMARY][f'B{r}'].value for r in range(6, 9)], ['村A', '村B', '村A'])

    def test_missing_group_metadata_is_rejected(self):
        wb = workbook([('村A', '')])
        with self.assertRaises(ValueError):
            core.install(wb, 8, True)
        self.assertEqual(wb[core.SUMMARY]['A9'].value, '合计')

    def test_more_than_three_groups_preserves_note_and_header(self):
        keys = [(f'村{i}', '农用地') for i in range(5)]
        wb = workbook(keys, count=5)
        result = core.install(wb, 10, True)
        ws = wb[core.SUMMARY]
        self.assertEqual(result['summary_total_row'], 11)
        self.assertEqual(ws['A12'].value, '填表说明：保留本行')
        self.assertEqual(ws['A1'].value, '合成土地汇总')
        self.assertIn('A1:AF1', {str(x) for x in ws.merged_cells.ranges})
        self.assertEqual(str(ws.print_area), "'补偿审核汇总表'!$A$1:$AF$11")

    def test_rebuild_is_idempotent_and_can_shrink(self):
        keys = [(f'村{i}', '农用地') for i in range(5)]
        wb = workbook(keys, count=5)
        core.install(wb, 10, True)
        state = [[c.value for c in row] for row in wb[core.SUMMARY]]
        core.install(wb, 10, True)
        self.assertEqual(state, [[c.value for c in row] for row in wb[core.SUMMARY]])
        wd = wb[core.DETAIL]
        for r in range(6, 11):
            wd[f'BO{r}'], wd[f'BP{r}'] = '村A', '农用地'
        core.install(wb, 10, True)
        self.assertEqual(wb[core.SUMMARY]['A9'].value, '合计')
        self.assertEqual(wb[core.SUMMARY]['A10'].value, '填表说明：保留本行')
        self.assertIsNone(wb[core.SUMMARY]['B7'].value)
        self.assertIsNone(wb[core.SUMMARY]['B8'].value)

    def test_unidentified_rows_are_not_inferred_as_groups(self):
        wb = workbook([('村A', '农用地')])
        wd = wb[core.DETAIL]
        wd['BO7'], wd['BP7'], wd['BI7'] = '村B', '农用地', 1000
        self.assertEqual(core.install(wb, 8, True)['groups'], [('村A', '农用地')])

    def test_manual_audit_values_and_custom_formulas_are_preserved(self):
        wb = workbook([('村A', '农用地')])
        wd = wb[core.DETAIL]
        wd['W6'], wd['AL6'], wd['BE6'] = 900, 0.5, 8
        wd['Z6'] = '=M6*0.8'
        wd['X6'] = '=ROUND(P6*W6+100,2)'
        before = {col: wd[f'{col}6'].value for col in ['W', 'AL', 'BE', 'Z', 'X']}
        core.install(wb, 8, True)
        self.assertEqual(before, {col: wd[f'{col}6'].value for col in before})
        self.assertIn('AL6*AN6', wd['AO6'].value)
        self.assertIn('BE6*BD6', wd['BF6'].value)

    def test_missing_adopted_quantity_is_not_filled_from_submission(self):
        wb = workbook([('村A', '农用地')])
        wd = wb[core.DETAIL]
        wd['D6'], wd['J6'], wd['M6'], wd['BN2'] = 1, 1000, 2000, 0.15
        core.install(wb, 8, True)
        self.assertTrue(all(wd[f'{col}6'].value is None for col in 'QRSTUV'))
        self.assertIn('COUNT(Q6:V6)=0', wd['P6'].value)

    def test_cleared_audit_inputs_stay_pending_and_explicit_zero_is_preserved(self):
        wb = workbook([('村A', '农用地')])
        wd = wb[core.DETAIL]
        wd['J6'], wd['M6'], wd['AF6'], wd['AX6'] = 1000, 2000, 100, 500
        for col in core.AUDIT_INPUTS:
            wd[f'{col}6'] = '' if col in {'W', 'AL'} else None
        core.install(wb, 8, True)
        for col in core.AUDIT_INPUTS:
            self.assertIn(wd[f'{col}6'].value, (None, ''))
        for col in core.AUDIT_INPUTS:
            wd[f'{col}6'] = 0
        core.install(wb, 8, True)
        self.assertTrue(all(wd[f'{col}6'].value == 0 for col in core.AUDIT_INPUTS))

    def test_quantity_subtotals_include_missing_value_protection(self):
        wb = workbook([('村A', '农用地')])
        core.install(wb, 8, True)
        wd, ws = wb[core.DETAIL], wb[core.SUMMARY]
        for col in ['P', 'AL', 'AP', 'BE']:
            self.assertIn('COUNTIFS', wd[f'{col}9'].value)
        for col in ['K', 'V', 'AA']:
            self.assertIn('COUNTIFS', ws[f'{col}9'].value)
        self.assertEqual(core.AUDITED_SUMMARY - {'AF'},
                         {col for col, source in core.SOURCES.items() if source in core.AUDITED}
                         | {col for col, sources in core.COMBINED.items() if any(source in core.AUDITED for source in sources)})

    def test_cli_does_not_overwrite_input_or_existing_output(self):
        root = Path(os.environ['LAND_AUDIT_TEST_DIR'])
        with tempfile.TemporaryDirectory(prefix='land-summary-', dir=root) as directory:
            p = Path(directory)
            source, output = p / 'input.xlsx', p / 'output.xlsx'
            workbook().save(source)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            with self.assertRaises(ValueError):
                core.main([str(source), '--output', str(source), '--detail-end-row', '8'])
            output.write_bytes(b'protected')
            with self.assertRaises(ValueError):
                core.main([str(source), '--output', str(output), '--detail-end-row', '8'])
            self.assertEqual(output.read_bytes(), b'protected')
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)

    def test_cli_explicit_end_is_required(self):
        with self.assertRaises(SystemExit):
            core.main(['input.xlsx', '--output', 'output.xlsx'])


if __name__ == '__main__':
    unittest.main()
