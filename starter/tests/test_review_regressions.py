import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import networkx as nx
import pandas as pd

from analysis import amount_temporal_support, structural_betweenness
from starter import write_outputs
from validation import ValidationError, validate_output_files
from test_analysis import BASE, fixture, run_analysis
from test_contracts import output_fixture


def transfers(rows):
    frame = pd.DataFrame(rows, columns=['src', 'dst', 'date', 'sum_kzt'])
    frame['date'] = pd.to_datetime(frame.date)
    return frame


class TemporalAmountTests(unittest.TestCase):
    def test_small_receipt_cannot_support_large_same_day_flow(self):
        tx = transfers([(1, 2, '2026-07-15', 6600.), (1, 2, '2026-07-22', 652000.),
                        (2, 3, '2026-07-22', 652000.)])
        result = amount_temporal_support(tx)[2]
        self.assertEqual(result['temporal_matched_kzt'], 6600.)
        self.assertEqual(result['same_day_uncertain_kzt'], 645400.)
        self.assertAlmostEqual(result['temporal_matched_out_share'], 6600 / 652000)

    def test_receipts_are_consumed_once_and_partial_coverage_counts(self):
        tx = transfers([(1, 2, '2026-07-01', 100.), (2, 3, '2026-07-02', 75.),
                        (2, 4, '2026-07-03', 75.)])
        result = amount_temporal_support(tx)[2]
        self.assertEqual(result['temporal_matched_kzt'], 100.)
        self.assertAlmostEqual(result['temporal_matched_out_share'], 2 / 3)
        self.assertAlmostEqual(result['temporal_unmatched_out_share'], 1 / 3)

    def test_same_day_uncertainty_consumes_receipts_before_next_day(self):
        tx = transfers([(1, 2, '2026-07-01', 100.), (2, 3, '2026-07-01', 100.),
                        (2, 4, '2026-07-02', 100.)])
        result = amount_temporal_support(tx)[2]
        self.assertEqual(result['temporal_matched_kzt'], 0.)
        self.assertEqual(result['same_day_uncertain_kzt'], 100.)
        self.assertEqual(result['temporal_unmatched_out_share'], .5)

    def test_window_expiry_future_and_same_day_are_not_support(self):
        for outgoing, expected in [('2026-06-30', 0), ('2026-07-01', 0),
                                   ('2026-07-02', 10), ('2026-07-08', 10), ('2026-07-09', 0)]:
            with self.subTest(day=outgoing):
                tx = transfers([(1, 2, '2026-07-01', 10.), (2, 3, outgoing, 10.)])
                self.assertEqual(amount_temporal_support(tx)[2]['temporal_matched_kzt'], expected)

    def test_calendar_days_remain_correct_across_daylight_saving(self):
        tx = transfers([(1, 2, '2026-03-01', 10.), (2, 3, '2026-03-09', 10.)])
        tx['date'] = tx.date.dt.tz_localize('America/New_York')
        self.assertEqual(amount_temporal_support(tx)[2]['temporal_matched_kzt'], 0.)

    def test_fifo_spends_old_receipt_before_newer_receipt(self):
        tx = transfers([(1, 2, '2026-07-01', 10.), (1, 2, '2026-07-07', 20.),
                        (2, 3, '2026-07-08', 10.), (2, 3, '2026-07-09', 20.)])
        self.assertEqual(amount_temporal_support(tx)[2]['temporal_matched_kzt'], 30.)

    def test_tiyn_precision_self_transfers_and_order_independence(self):
        tx = transfers([(1, 2, '2026-07-01', .01), (1, 2, '2026-07-01', .02),
                        (2, 2, '2026-07-01', 1000.), (2, 3, '2026-07-02', .02),
                        (2, 4, '2026-07-02', .02)])
        result = amount_temporal_support(tx)
        self.assertEqual(result[2]['temporal_matched_kzt'], .03)
        self.assertEqual(result[2]['temporal_matched_out_share'], .75)
        self.assertEqual(result, amount_temporal_support(tx.sample(frac=1, random_state=12)))

    def test_seed_completeness_caps_flow_confidence_without_changing_priority(self):
        edges, nodes, tx = fixture()
        first = run_analysis(edges, nodes, tx)[0].set_index('gid')
        nodes.loc[nodes.gid.eq(BASE + 11), ['depth', 'is_seed']] = [0, True]
        second = run_analysis(edges, nodes, tx)[0].set_index('gid')
        before, after = first.loc[BASE + 11], second.loc[BASE + 11]
        self.assertEqual(before.role, 'transit')
        self.assertEqual(after.role, 'transit')
        self.assertEqual(before.role_score_uncapped, after.role_score_uncapped)
        self.assertGreater(before.role_score, .65)
        self.assertEqual(after.role_score, .65)
        self.assertEqual(after.role_score_cap, .65)
        self.assertEqual(after.observation_completeness, 'seed_inflows_incomplete')
        self.assertEqual(before.observation_completeness, 'sample_only')
        self.assertEqual(before.priority_score, after.priority_score)
        self.assertIn('вход неполон', after.evidence)
        self.assertTrue(second.loc[BASE + 13, 'depth_outflow_incomplete'])


class BetweennessTests(unittest.TestCase):
    def test_small_graph_matches_all_sources_exactly(self):
        graph = nx.DiGraph([(1, 2), (2, 3), (3, 4), (1, 5), (5, 3)])
        actual, mode, sources = structural_betweenness(graph)
        self.assertEqual(actual, nx.betweenness_centrality(graph, weight=None))
        self.assertEqual(mode, 'exact')
        self.assertEqual(sources, len(graph))

    def test_each_size_limit_switches_to_reproducible_sampling(self):
        graph = nx.path_graph(300, create_using=nx.DiGraph)
        for limit in ['EXACT_MAX_NODES', 'EXACT_MAX_EDGES']:
            with self.subTest(limit=limit), patch('analysis.' + limit, 100):
                actual, mode, sources = structural_betweenness(graph)
                self.assertEqual(mode, 'sampled')
                self.assertEqual(sources, 256)
                self.assertEqual(actual, nx.betweenness_centrality(graph, k=256, weight=None, seed=42))
                reversed_insertion = nx.DiGraph()
                reversed_insertion.add_nodes_from(reversed(list(graph)))
                reversed_insertion.add_edges_from(reversed(list(graph.edges())))
                self.assertEqual(actual, structural_betweenness(reversed_insertion)[0])


class AtomicOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.out = self.root / 'out'
        self.r, self.c, self.t, self.n, self.e = output_fixture()
        self.graph = nx.DiGraph()
        self.graph.add_nodes_from(self.n.gid)
        self.save()
        self.original = self.snapshot()
        self.previous_version = self.out.resolve()
        self.r['evidence'] = 'updated fixture 2'
        self.t['why'] = 'updated priority 2'

    def save(self):
        with contextlib.redirect_stdout(io.StringIO()):
            write_outputs(self.r, self.c, self.t, self.n, self.e, self.out, graph=self.graph)

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.out.iterdir()}

    def assert_preserved(self):
        self.assertEqual(self.original, self.snapshot())
        validate_output_files(self.out, self.n, self.e)
        self.assertEqual(set(self.out.iterdir()), {self.out / name for name in self.original})

    def test_second_csv_write_failure_preserves_previous_generation(self):
        original_to_csv = pd.DataFrame.to_csv
        calls = []
        def interrupted(frame, path, *args, **kwargs):
            calls.append(Path(path).name)
            if len(calls) == 2:
                Path(path).write_text('partial second file')
                raise OSError('disk full')
            return original_to_csv(frame, path, *args, **kwargs)
        with patch.object(pd.DataFrame, 'to_csv', interrupted):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.save()
        self.assertEqual(calls, ['nodes_roles.csv', 'clusters.csv'])
        self.assert_preserved()
        self.assertEqual(list(self.root.glob('.out-version-*')), [self.previous_version])

    def test_validation_and_json_failures_preserve_previous_generation(self):
        for target in ['starter.validate_output_files', 'starter.write_graph_json']:
            with self.subTest(target=target), patch(target, side_effect=ValueError('injected failure')):
                with self.assertRaisesRegex(ValueError, 'injected failure'):
                    self.save()
            self.assert_preserved()

    def test_switch_failure_preserves_previous_generation(self):
        with patch('output_store.os.replace', side_effect=OSError('switch failed')):
            with self.assertRaisesRegex(OSError, 'switch failed'):
                self.save()
        self.assert_preserved()

    def test_success_switches_csv_and_json_together_and_retains_old_snapshot(self):
        self.save()
        self.assertTrue(self.out.is_symlink())
        self.assertNotEqual(self.previous_version, self.out.resolve())
        self.assertEqual(self.original, {p.name: p.read_bytes() for p in self.previous_version.iterdir()})
        validate_output_files(self.previous_version, self.n, self.e)
        validate_output_files(self.out, self.n, self.e)
        roles = pd.read_csv(self.out / 'nodes_roles.csv')
        graph = json.loads((self.out / 'graph.json').read_text())
        self.assertEqual(roles.evidence.tolist(), [row['evidence'] for row in graph['nodes']])
        self.assertEqual(set(roles.evidence), {'updated fixture 2'})

    def test_validator_pins_a_generation_while_publisher_switches(self):
        original_read = pd.read_csv
        observed = []
        publishing = False
        def read_and_switch(path, *args, **kwargs):
            nonlocal publishing
            if not publishing:
                observed.append(Path(path).parent)
                if len(observed) == 1:
                    publishing = True
                    self.save()
                    publishing = False
            return original_read(path, *args, **kwargs)
        with patch('validation.pd.read_csv', read_and_switch):
            validate_output_files(self.out, self.n, self.e)
        self.assertEqual(observed, [self.previous_version] * 3)
        self.assertNotEqual(self.previous_version, self.out.resolve())

    def test_legacy_directory_is_atomically_migrated_and_old_files_retained(self):
        import shutil
        legacy = self.root / 'legacy'
        shutil.copytree(self.previous_version, legacy)
        self.out = legacy
        self.save()
        self.assertTrue(legacy.is_symlink())
        backups = list(self.root.glob('.legacy-previous-*'))
        self.assertEqual(len(backups), 1)
        self.assertTrue(backups[0].is_dir())
        self.assertFalse(backups[0].is_symlink())
        self.assertEqual(self.original, {p.name: p.read_bytes() for p in backups[0].iterdir()})
        validate_output_files(legacy, self.n, self.e)

    def test_unsupported_legacy_exchange_leaves_real_directory_untouched(self):
        import shutil
        legacy = self.root / 'legacy'
        shutil.copytree(self.previous_version, legacy)
        self.out = legacy
        with patch('output_store.exchange_paths', side_effect=OSError('unsupported filesystem')):
            with self.assertRaisesRegex(OSError, 'unsupported filesystem'):
                self.save()
        self.assertFalse(legacy.is_symlink())
        self.assert_preserved()

    def test_failed_first_generation_does_not_publish_partial_files(self):
        self.out = self.root / 'fresh'
        with patch('starter.write_graph_json', side_effect=OSError('JSON write failed')):
            with self.assertRaisesRegex(OSError, 'JSON write failed'):
                self.save()
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.glob('.fresh-*')), [])


if __name__ == '__main__':
    unittest.main()
