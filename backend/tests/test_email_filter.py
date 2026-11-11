"""Tests for the Gmail pre-Groq filter pipeline."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from apps.gmail.gmail_scraper import (
    _classify_one,
    _is_low_engagement,
    _is_low_engagement_at_threshold,
    _is_rare_sender,
    _make_noise,
    _sender_domain,
    _structural_classify,
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


class TestStructuralClassifier(unittest.TestCase):
    """Layer 1: structural regex classification before Groq."""

    def test_completed_subject_detected(self):
        e = _email(subject='Scheduled System Maintenance - COMPLETE')
        r = _structural_classify(e)
        self.assertIsNotNone(r)
        self.assertEqual(r['category'], 'uncategorized')
        self.assertEqual(r['_filter_stage'], 'structural')

    def test_status_update_detected(self):
        e = _email(subject='Systems Currently Unavailable',
                   body='Our systems are currently experiencing downtime.')
        r = _structural_classify(e)
        self.assertIsNotNone(r)
        self.assertEqual(r['category'], 'uncategorized')

    def test_confirmation_detected(self):
        e = _email(subject='Thank you for registering!',
                   body='Welcome to our platform.')
        r = _structural_classify(e)
        self.assertIsNotNone(r)
        self.assertEqual(r['category'], 'uncategorized')

    def test_otp_detected(self):
        e = _email(subject='Your verification code is 123456')
        r = _structural_classify(e)
        self.assertIsNotNone(r)
        self.assertEqual(r['category'], 'uncategorized')

    def test_deposit_detected(self):
        e = _email(subject='Your payment will be deposited today')
        r = _structural_classify(e)
        self.assertIsNotNone(r)
        self.assertEqual(r['category'], 'uncategorized')

    def test_delivery_detected(self):
        e = _email(subject='Your order is here!')
        r = _structural_classify(e)
        self.assertIsNotNone(r)
        self.assertEqual(r['category'], 'uncategorized')

    def test_ambiguous_returns_none(self):
        """Emails without clear structural signals go to Groq."""
        e = _email(subject='Sign up for advising appointments')
        r = _structural_classify(e)
        self.assertIsNone(r)

    def test_action_email_returns_none(self):
        """Action-oriented emails should NOT be caught by structural classifier."""
        e = _email(subject='[Action Required] Submit your FAFSA')
        r = _structural_classify(e)
        self.assertIsNone(r)


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

    def test_primary_tab_not_promo_social(self):
        e = _email(gmail_tab='primary')
        r = _classify_one(e, client=None)
        self.assertEqual(r['_pass_reason'], 'not_promo_social')

    def test_promotions_engaged_reason(self):
        e = _email(gmail_tab='promotions')
        eng = {'replied_domains': set(), 'opened_domains': {'example.com': 10}}
        r = _classify_one(e, _mock_client(), engagement_index=eng)
        self.assertEqual(r['_pass_reason'], 'engaged_5')

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
        self.assertEqual(r['_pass_reason'], 'engaged_sender')

    def test_noise_has_no_pass_reason(self):
        e = _email(gmail_tab='promotions')
        eng = {'replied_domains': set(), 'opened_domains': {}}
        r = _classify_one(e, client=None, engagement_index=eng)
        self.assertIsNone(r['_pass_reason'])


if __name__ == '__main__':
    unittest.main()
