import copy
import unittest
from datetime import datetime, timezone
from notification_ha import notification

class SourceTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000
        self.event = dict(event_type='call_service', origin='LOCAL',
            time_fired=datetime.fromtimestamp(self.now, timezone.utc).isoformat(), context={'id':'unique'},
            data=dict(domain='notify', service='send_message', service_data={'entity_id':['notify.ying'], 'message':'水满通知'}))
    def test_exact_destination_and_dedup(self):
        a = notification(self.event, 'notify.ying', self.now)
        self.assertEqual(a['text'], '水满通知')
        self.assertEqual(a, notification(copy.deepcopy(self.event), 'notify.ying', self.now))
    def test_other_targets(self):
        for targets in [['notify.other'], ['notify.ying','notify.other'], 'all', None]:
            self.event['data']['service_data']['entity_id'] = targets
            self.assertIsNone(notification(self.event, 'notify.ying', self.now))
    def test_stale_or_future(self):
        for now in [self.now+31, self.now-6]:
            self.assertIsNone(notification(self.event, 'notify.ying', now))
    def test_invalid_message(self):
        for text in ['', None, 42, 'a'*4001]:
            self.event['data']['service_data']['message'] = text
            self.assertIsNone(notification(self.event, 'notify.ying', self.now))
    def test_no_bot_updates(self):
        self.event['event_type'] = 'telegram_text'
        self.assertIsNone(notification(self.event, 'notify.ying', self.now))
    def test_no_other_services(self):
        self.event['data']['service'] = 'send_photo'
        self.assertIsNone(notification(self.event, 'notify.ying', self.now))
    def test_no_indirect_targets(self):
        self.event['data']['service_data']['area_id'] = 'bedroom'
        self.assertIsNone(notification(self.event, 'notify.ying', self.now))
    def test_title_and_string_destination(self):
        self.event['data']['service_data'].update(entity_id='notify.ying', title='告警')
        self.assertEqual(notification(self.event,'notify.ying',self.now)['text'], '告警\n水满通知')

if __name__ == '__main__':
    unittest.main()
