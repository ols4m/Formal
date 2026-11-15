"""Tests for the Gmail pre-Groq filter pipeline."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from apps.gmail.gmail_scraper import (
    _build_frequency_index,
    _classify_one,
    _is_low_engagement,
    _is_low_engagement_at_threshold,
    _is_rare_sender,
    _make_noise,
    _pipeline_summary,
    _sender_domain,
    build_engagement_index,
)


def _email(**overrides) -> dict:
    """Build a minimal email dict with sensible defaults."""
    base = {
        'id': 'test_123',
        'from': 'someone@example.com',
        'subject': 'Test email',
        'snippet': '',
        'date': '2026-10-30',
        'body': 'Test body',
        'source': 'personal',
        'gmail_tab': 'primary',
        'is_important': False,
        'has_list_unsubscribe': True,
    }
    base.update(overrides)
    return base


# Groq client mock that returns a valid classification
def _mock_client():
    client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = (
        '{"category": "task", "intent": "action", "confidence": 0.9, '
        '"consequence": 3, "urgency_context": "evergreen", '
        '"summary": "Do something", "deadline": null}'
    )
    client.chat.completions.create.return_value = response
    return client


class TestPromoSocialGate(unittest.TestCase):
    """1. Promotions/social → engagement-based gate."""

    def _low_eng(self):
        return {'replied_domains': set(), 'opened_domains': {}}

    def _high_eng(self, domain='example.com'):
        return {'replied_domains': set(), 'opened_domains': {domain: 5}}

    def test_promotions_low_engagement_dropped(self):
        e = _email(gmail_tab='promotions')
        r = _classify_one(e, _mock_client(), engagement_index=self._low_eng())
        self.assertEqual(r['category'], 'noise')
        self.assertEqual(r['_filter_stage'], 'tab_noise')

    def test_social_low_engagement_dropped(self):
        e = _email(gmail_tab='social')
        r = _classify_one(e, _mock_client(), engagement_index=self._low_eng())
        self.assertEqual(r['_filter_stage'], 'tab_noise')

    def test_promotions_engaged_passes(self):
        e = _email(gmail_tab='promotions')
        r = _classify_one(e, _mock_client(), engagement_index=self._high_eng())
        self.assertNotEqual(r['_filter_stage'], 'tab_noise')

    def test_social_engaged_passes(self):
        e = _email(gmail_tab='social')
        r = _classify_one(e, _mock_client(), engagement_index=self._high_eng())
        self.assertNotEqual(r['_filter_stage'], 'tab_noise')

    def test_promotions_important_passes(self):
        e = _email(gmail_tab='promotions', is_important=True)
        r = _classify_one(e, _mock_client(), engagement_index=self._low_eng())
        self.assertNotEqual(r['_filter_stage'], 'tab_noise')

    def test_promotions_replied_passes(self):
        e = _email(gmail_tab='promotions')
        eng = {'replied_domains': {'example.com'}, 'opened_domains': {}}
        r = _classify_one(e, _mock_client(), engagement_index=eng)
        self.assertNotEqual(r['_filter_stage'], 'tab_noise')

    def test_promotions_below_threshold_dropped(self):
        """4 opened emails is below the ≥5 threshold."""
        e = _email(gmail_tab='promotions')
        eng = {'replied_domains': set(), 'opened_domains': {'example.com': 4}}
        r = _classify_one(e, _mock_client(), engagement_index=eng)
        self.assertEqual(r['_filter_stage'], 'tab_noise')

class TestForumsGate(unittest.TestCase):
    """Forums go through the behavioral gate, not hard-dropped."""

    def test_forums_important_passes(self):
        e = _email(gmail_tab='forums', is_important=True)
        r = _classify_one(e, _mock_client())
        self.assertEqual(r['_filter_stage'], 'groq')

    def test_forums_rare_sender_passes(self):
        e = _email(gmail_tab='forums', is_important=False,
                   **{'from': 'regist@drew.edu'})
        freq = {'drew.edu': 1}
        eng = {'replied_domains': set(), 'opened_domains': {}}
        r = _classify_one(e, _mock_client(), engagement_index=eng, frequency_index=freq)
        self.assertEqual(r['_filter_stage'], 'groq')

    def test_forums_engaged_sender_passes(self):
        e = _email(gmail_tab='forums', is_important=False,
                   **{'from': 'health@drew.edu'})
        freq = {'drew.edu': 10}
        eng = {'replied_domains': {'drew.edu'}, 'opened_domains': {}}
        r = _classify_one(e, _mock_client(), engagement_index=eng, frequency_index=freq)
        self.assertEqual(r['_filter_stage'], 'groq')

    def test_forums_high_freq_no_engagement_dropped(self):
        e = _email(gmail_tab='forums', is_important=False,
                   **{'from': 'digest@reddit.com'})
        freq = {'reddit.com': 20}
        eng = {'replied_domains': set(), 'opened_domains': {}}
        r = _classify_one(e, _mock_client(), engagement_index=eng, frequency_index=freq)
        self.assertEqual(r['_filter_stage'], 'updates_noise')



class TestUpdatesGateRareSender(unittest.TestCase):
    """3. Updates + not important + rare sender → passes to Groq."""

    def test_rare_sender_passes(self):
        e = _email(gmail_tab='updates', is_important=False,
                   **{'from': 'noreply@studentaid.gov'})
        freq = {'studentaid.gov': 1}  # ≤ 2 = rare
        eng = {'replied_domains': set(), 'opened_domains': {}}
        r = _classify_one(e, _mock_client(), engagement_index=eng, frequency_index=freq)
        self.assertEqual(r['_filter_stage'], 'groq')

    def test_fafsa_passes_with_zero_history(self):
        e = _email(gmail_tab='updates', is_important=False,
                   **{'from': 'noreply@studentaid.gov'})
        freq = {}  # never seen = 0 ≤ 2 = rare
        eng = {'replied_domains': set(), 'opened_domains': {}}
        r = _classify_one(e, _mock_client(), engagement_index=eng, frequency_index=freq)
        self.assertEqual(r['_filter_stage'], 'groq')


class TestUpdatesGateEngagedSender(unittest.TestCase):
    """4. Updates + not important + engaged sender → passes to Groq."""

    def test_replied_domain_passes(self):
        e = _email(gmail_tab='updates', is_important=False,
                   **{'from': 'zach@hackclub.com'})
        freq = {'hackclub.com': 5}  # not rare
        eng = {'replied_domains': {'hackclub.com'}, 'opened_domains': {}}
        r = _classify_one(e, _mock_client(), engagement_index=eng, frequency_index=freq)
        self.assertEqual(r['_filter_stage'], 'groq')

    def test_opened_domain_passes(self):
        e = _email(gmail_tab='updates', is_important=False,
                   **{'from': 'noreply@someapp.com'})
        freq = {'someapp.com': 5}
        eng = {'replied_domains': set(), 'opened_domains': {'someapp.com': 3}}
        r = _classify_one(e, _mock_client(), engagement_index=eng, frequency_index=freq)
        self.assertEqual(r['_filter_stage'], 'groq')


class TestUpdatesGateDropped(unittest.TestCase):
    """5. Updates + not important + high freq + low engagement → dropped."""

    def test_facebook_digest_dropped(self):
        e = _email(gmail_tab='updates', is_important=False,
                   **{'from': 'notification@priority.facebookmail.com'})
        freq = {'priority.facebookmail.com': 10}  # high freq
        eng = {'replied_domains': set(), 'opened_domains': {}}  # no engagement
        r = _classify_one(e, _mock_client(), engagement_index=eng, frequency_index=freq)
        self.assertEqual(r['_filter_stage'], 'updates_noise')
        self.assertEqual(r['category'], 'noise')


class TestUpdatesImportantPasses(unittest.TestCase):
    """6. Updates + is_important → passes to Groq regardless."""

    def test_important_updates_pass(self):
        e = _email(gmail_tab='updates', is_important=True,
                   **{'from': 'notification@priority.facebookmail.com'})
        freq = {'priority.facebookmail.com': 10}
        eng = {'replied_domains': set(), 'opened_domains': {}}
        r = _classify_one(e, _mock_client(), engagement_index=eng, frequency_index=freq)
        self.assertEqual(r['_filter_stage'], 'groq')


class TestPrimaryPassesThrough(unittest.TestCase):
    """7. Primary/personal/unknown → straight to Groq."""

    def test_primary(self):
        r = _classify_one(_email(gmail_tab='primary'), _mock_client())
        self.assertEqual(r['_filter_stage'], 'groq')

    def test_personal(self):
        r = _classify_one(_email(gmail_tab='personal'), _mock_client())
        self.assertEqual(r['_filter_stage'], 'groq')

    def test_unknown(self):
        r = _classify_one(_email(gmail_tab='unknown'), _mock_client())
        self.assertEqual(r['_filter_stage'], 'groq')


class TestBackwardCompat(unittest.TestCase):
    """8. Missing has_list_unsubscribe defaults to True (bulk) → Groq."""

    def test_missing_field_defaults_bulk(self):
        e = _email(gmail_tab='primary')
        del e['has_list_unsubscribe']
        r = _classify_one(e, _mock_client())
        self.assertEqual(r['_filter_stage'], 'groq')


class TestFrequencyEdgeCases(unittest.TestCase):
    """9. Frequency index edge cases."""

    def test_empty_history_all_rare(self):
        self.assertTrue(_is_rare_sender('noreply@new-domain.com', {}))

    def test_at_threshold_is_rare(self):
        self.assertTrue(_is_rare_sender('x@test.com', {'test.com': 2}))

    def test_above_threshold_not_rare(self):
        self.assertFalse(_is_rare_sender('x@test.com', {'test.com': 3}))


class TestEngagementEdgeCases(unittest.TestCase):
    """10. Engagement index is None → no suppression."""

    def test_none_index_not_low(self):
        self.assertFalse(_is_low_engagement('anyone@example.com', None))

    def test_sender_domain_extraction(self):
        self.assertEqual(_sender_domain('Sam <sam@drew.edu>'), 'drew.edu')
        self.assertEqual(_sender_domain('plain@test.org'), 'test.org')


class TestPoolFallback(unittest.TestCase):
    """When client=None, non-noise emails pool as uncategorized."""

    def test_primary_pools_uncategorized(self):
        e = _email(gmail_tab='primary', subject='[Action Required] Submit your form')
        r = _classify_one(e, client=None)
        self.assertEqual(r['_filter_stage'], 'unclassified')
        self.assertEqual(r['category'], 'uncategorized')

    def test_updates_pools_uncategorized(self):
        e = _email(gmail_tab='updates', has_list_unsubscribe=True,
                   subject='Drew This Week - Campus News')
        r = _classify_one(e, client=None)
        self.assertEqual(r['_filter_stage'], 'unclassified')
        self.assertEqual(r['category'], 'uncategorized')

    def test_no_unsub_pools_uncategorized(self):
        e = _email(has_list_unsubscribe=False, subject='You spent $6.30')
        r = _classify_one(e, client=None)
        self.assertEqual(r['_filter_stage'], 'unclassified')
        self.assertEqual(r['category'], 'uncategorized')

    def test_tab_noise_still_works_without_client(self):
        e = _email(gmail_tab='promotions')
        eng = {'replied_domains': set(), 'opened_domains': {}}
        r = _classify_one(e, client=None, engagement_index=eng)
        self.assertEqual(r['_filter_stage'], 'tab_noise')

    def test_engaged_promo_pools_uncategorized(self):
        e = _email(gmail_tab='promotions', has_list_unsubscribe=True,
                   subject='Weekly deals from LinkedIn')
        eng = {'replied_domains': set(), 'opened_domains': {'example.com': 10}}
        r = _classify_one(e, client=None, engagement_index=eng)
        self.assertEqual(r['_filter_stage'], 'unclassified')
        self.assertEqual(r['category'], 'uncategorized')


class TestEnrichmentFields(unittest.TestCase):
    """Every classified email has engagement and domain annotations."""

    def test_enrichment_on_unclassified(self):
        e = _email(gmail_tab='primary')
        r = _classify_one(e, client=None)
        self.assertIn('_rare_sender', r)
        self.assertIn('_engaged_sender', r)
        self.assertIn('_sender_domain', r)
        self.assertEqual(r['_sender_domain'], 'example.com')

    def test_enrichment_on_groq(self):
        e = _email(gmail_tab='primary')
        r = _classify_one(e, _mock_client())
        self.assertIn('_sender_domain', r)

    def test_enrichment_on_noise(self):
        e = _email(gmail_tab='promotions')
        eng = {'replied_domains': set(), 'opened_domains': {}}
        r = _classify_one(e, client=None, engagement_index=eng)
        self.assertIn('_sender_domain', r)

    def test_enrichment_on_structural(self):
        e = _email(subject='Your verification code is 123456')
        r = _classify_one(e, client=None)
        self.assertIn('_sender_domain', r)


class TestPassReason(unittest.TestCase):
    """Every pooled email records why it passed the gates."""

    def test_primary_tab_reason(self):
        e = _email(gmail_tab='primary')
        r = _classify_one(e, client=None)
        self.assertEqual(r['_pass_reason'], 'primary_tab')

    def test_promotions_engaged_reason(self):
        e = _email(gmail_tab='promotions')
        eng = {'replied_domains': set(), 'opened_domains': {'example.com': 10}}
        r = _classify_one(e, _mock_client(), engagement_index=eng)
        self.assertEqual(r['_pass_reason'], 'kept_5')

    def test_promotions_important_reason(self):
        e = _email(gmail_tab='promotions', is_important=True)
        eng = {'replied_domains': set(), 'opened_domains': {}}
        r = _classify_one(e, _mock_client(), engagement_index=eng)
        self.assertEqual(r['_pass_reason'], 'important')

    def test_updates_rare_reason(self):
        e = _email(gmail_tab='updates', is_important=False,
                   **{'from': 'noreply@studentaid.gov'})
        freq = {'studentaid.gov': 1}
        eng = {'replied_domains': set(), 'opened_domains': {}}
        r = _classify_one(e, _mock_client(), engagement_index=eng, frequency_index=freq)
        self.assertEqual(r['_pass_reason'], 'rare_sender')

    def test_updates_important_reason(self):
        e = _email(gmail_tab='updates', is_important=True)
        r = _classify_one(e, _mock_client())
        self.assertEqual(r['_pass_reason'], 'important')

    def test_updates_engaged_reason(self):
        e = _email(gmail_tab='updates', is_important=False,
                   **{'from': 'noreply@drew.edu'})
        freq = {'drew.edu': 10}
        eng = {'replied_domains': {'drew.edu'}, 'opened_domains': {}}
        r = _classify_one(e, _mock_client(), engagement_index=eng, frequency_index=freq)
        self.assertEqual(r['_pass_reason'], 'replied')

    def test_noise_has_no_pass_reason(self):
        e = _email(gmail_tab='promotions')
        eng = {'replied_domains': set(), 'opened_domains': {}}
        r = _classify_one(e, client=None, engagement_index=eng)
        self.assertIsNone(r['_pass_reason'])

    def test_updates_noise_has_no_pass_reason(self):
        e = _email(gmail_tab='updates', **{'from': 'digest@forum.example.com'})
        eng = {'replied_domains': set(), 'opened_domains': {}}
        r = _classify_one(e, client=None, engagement_index=eng,
                          frequency_index={'forum.example.com': 20})
        self.assertEqual(r['_filter_stage'], 'updates_noise')
        self.assertIsNone(r['_pass_reason'])

    def test_updates_kept_reason(self):
        e = _email(gmail_tab='updates', **{'from': 'noreply@app.example.com'})
        eng = {'replied_domains': set(), 'opened_domains': {'app.example.com': 2}}
        r = _classify_one(e, client=None, engagement_index=eng,
                          frequency_index={'app.example.com': 9})
        self.assertEqual(r['_pass_reason'], 'kept_2')

    def test_missing_index_is_labeled_honestly(self):
        e = _email(gmail_tab='promotions')
        r = _classify_one(e, client=None, engagement_index=None)
        self.assertEqual(r['_pass_reason'], 'no_index')


class TestDismissedSender(unittest.TestCase):
    """Senders you keep dismissing are dropped from bulk tabs."""

    FB = {'from': 'Facebook <notification@facebookmail.com>'}
    ENG = {'replied_domains': set(), 'opened_domains': {'facebookmail.com': 9}}

    def _run(self, reputation, **overrides):
        e = _email(**{'gmail_tab': 'social', **self.FB, **overrides})
        return _classify_one(e, client=None, engagement_index=self.ENG,
                             reputation=reputation)

    def test_three_dismissals_drop(self):
        r = self._run({'facebookmail.com': (0, 3)})
        self.assertEqual(r['_filter_stage'], 'dismissed_sender')
        self.assertIsNone(r['_pass_reason'])

    def test_two_dismissals_still_pass(self):
        self.assertEqual(self._run({'facebookmail.com': (0, 2)})['_filter_stage'], 'unclassified')

    def test_any_done_keeps_sender(self):
        self.assertEqual(self._run({'facebookmail.com': (1, 9)})['_filter_stage'], 'unclassified')

    def test_important_overrides_dismissals(self):
        r = self._run({'facebookmail.com': (0, 9)}, is_important=True)
        self.assertEqual(r['_pass_reason'], 'important')

    def test_primary_tab_never_dropped(self):
        r = self._run({'facebookmail.com': (0, 9)}, gmail_tab='primary')
        self.assertEqual(r['_pass_reason'], 'primary_tab')


class TestFrequencyIndexBatch(unittest.TestCase):
    def test_batch_counts_when_no_history(self):
        batch = [_email(id=str(i), **{'from': 'news@example.org'}) for i in range(3)]
        freq = _build_frequency_index('does/not/exist.json', batch=batch)
        self.assertEqual(freq['example.org'], 3)
        self.assertFalse(_is_rare_sender('news@example.org', freq))

    def test_duplicate_ids_count_once(self):
        batch = [_email(id='same', **{'from': 'a@example.org'})] * 2
        self.assertEqual(_build_frequency_index('does/not/exist.json', batch=batch)['example.org'], 1)


class TestEngagementCache(unittest.TestCase):
    """A build with API errors is used once and never cached."""

    def _service(self, fail=False):
        svc = MagicMock()
        msgs = svc.users.return_value.messages.return_value
        msgs.list.return_value.execute.return_value = {'messages': [{'id': '1'}]}
        headers = [{'name': 'To', 'value': 'prof@example.edu'},
                   {'name': 'From', 'value': 'news@example.org'}]
        if fail:
            msgs.get.return_value.execute.side_effect = Exception('rate limited')
        else:
            msgs.get.return_value.execute.return_value = {'payload': {'headers': headers}}
        return svc

    def test_clean_build_is_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'eng.json'
            with redirect_stdout(io.StringIO()):
                idx = build_engagement_index(self._service(), cache_path=cache)
            self.assertTrue(cache.exists())
            self.assertIn('example.edu', idx['replied_domains'])

    def test_failed_build_is_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'eng.json'
            with redirect_stdout(io.StringIO()):
                build_engagement_index(self._service(fail=True), cache_path=cache)
            self.assertFalse(cache.exists())

    def test_rebuild_ignores_fresh_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'eng.json'
            with redirect_stdout(io.StringIO()):
                build_engagement_index(self._service(), cache_path=cache)
                svc = self._service()
                build_engagement_index(svc, cache_path=cache)
                svc.users.assert_not_called()
                build_engagement_index(svc, cache_path=cache, rebuild=True)
                svc.users.assert_called()


class TestPipelineSummary(unittest.TestCase):
    def test_counts_stages_and_reasons(self):
        results = [
            {'_filter_stage': 'unclassified', '_pass_reason': 'primary_tab'},
            {'_filter_stage': 'unclassified', '_pass_reason': 'kept_5'},
            {'_filter_stage': 'tab_noise', '_pass_reason': None},
        ]
        lines = _pipeline_summary(results)
        self.assertIn('2 unclassified', lines[0])
        self.assertIn('1 tab_noise', lines[0])
        self.assertIn('1 kept_5', lines[1])


if __name__ == '__main__':
    unittest.main()
