from datetime import timedelta
from django.utils import timezone
from rest_framework.test import APITestCase
from .models import ClubContent


class ActivityContentTests(APITestCase):
    def setUp(self):
        self.now = timezone.now()

    def content(self, code, **values):
        return ClubContent.objects.create(code=code, title=code, kind='HOT', **values)

    def test_archive_keeps_ended_events_but_hides_future_and_disabled_content(self):
        old = self.content('old', starts_at=self.now-timedelta(days=30), ends_at=self.now-timedelta(days=20))
        current = self.content('current', starts_at=self.now-timedelta(days=1), ends_at=self.now+timedelta(days=1))
        future = self.content('future', starts_at=self.now+timedelta(days=2))
        disabled = self.content('disabled', is_active=False)
        rows = self.client.get('/api/club/content/?kind=HOT&archive=1').data['data']
        self.assertEqual([row['id'] for row in rows], [current.pk, old.pk])
        home = self.client.get('/api/club/content/?kind=HOT').data['data']
        self.assertEqual([row['id'] for row in home], [current.pk])
        self.assertEqual(self.client.get(f'/api/club/content/{old.pk}/').status_code, 200)
        for item in [future, disabled]:
            self.assertEqual(self.client.get(f'/api/club/content/{item.pk}/').status_code, 404)

    def test_activity_order_uses_publication_time_not_edit_or_priority(self):
        recent = self.content('recent', starts_at=self.now-timedelta(days=1), sort_order=0)
        old = self.content('old', starts_at=self.now-timedelta(days=10), sort_order=9999)
        old.title = 'edited'; old.save()
        rows = self.client.get('/api/club/content/?kind=HOT&archive=1').data['data']
        self.assertEqual([row['id'] for row in rows], [recent.pk, old.pk])

    def test_archive_pages_and_legacy_popup_never_published(self):
        for i in range(32): self.content(f'event-{i}')
        rows = self.client.get('/api/club/content/?kind=HOT&archive=1').data['data']
        page2 = self.client.get('/api/club/content/?kind=HOT&archive=1&page=2').data['data']
        self.assertEqual((len(rows), len(page2)), (30, 2))
        self.assertFalse({row['id'] for row in rows} & {row['id'] for row in page2})
        popup = ClubContent.objects.create(code='legacy-popup', title='VIP', kind='POPUP')
        self.assertEqual(self.client.get(f'/api/club/content/{popup.pk}/').status_code, 404)
        self.assertNotIn(popup.pk, [row['id'] for row in self.client.get('/api/club/content/').data['data']])
