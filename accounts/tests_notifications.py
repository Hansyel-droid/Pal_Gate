"""
Tests for the in-app notification system: the Notification model, the
accounts.notifications helpers, the topbar bell's context processor, the
notifications inbox views, and the real call sites that create
notifications (a new application entering the review queue, and an
admin approving/rejecting/issuing).
"""
import tempfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Notification, PolicyAcceptance, User
from accounts.notifications import notify_admins, notify_user
from accounts.policy import CAMPUS_POLICY_VERSION
from applications.models import StickerApplication
from appointments.models import AppointmentSlot


def accept_policy(user):
    PolicyAcceptance.objects.create(user=user, version=CAMPUS_POLICY_VERSION)


def make_doc(name='doc.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 test document', content_type='application/pdf')


class NotifyHelperTests(TestCase):
    def test_notify_user_creates_an_unread_row(self):
        user = User.objects.create_user(
            username='u1', password='pw-1234567', role='applicant'
        )
        n = notify_user(user, 'hello there', link='/somewhere/')

        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(n.recipient, user)
        self.assertEqual(n.message, 'hello there')
        self.assertEqual(n.link, '/somewhere/')
        self.assertFalse(n.is_read)

    def test_notify_user_with_no_user_does_not_crash(self):
        """
        Legacy walk-in records have no linked login — notify_user must be
        a safe no-op for those rather than something every caller has to
        guard against.
        """
        self.assertIsNone(notify_user(None, 'hello'))
        self.assertEqual(Notification.objects.count(), 0)

    def test_notify_admins_reaches_active_admins_only(self):
        admin1 = User.objects.create_user(
            username='admin1', password='pw-1234567', role='admin'
        )
        admin2 = User.objects.create_user(
            username='admin2', password='pw-1234567', role='admin'
        )
        suspended_admin = User.objects.create_user(
            username='admin3', password='pw-1234567', role='admin'
        )
        suspended_admin.is_active = False
        suspended_admin.save()
        # Neither of these should hear about it — security can't open a
        # sticker_admin link anyway, and this isn't an applicant's own news.
        User.objects.create_user(
            username='sec1', password='pw-1234567', role='security'
        )
        User.objects.create_user(
            username='app1', password='pw-1234567', role='applicant'
        )

        notify_admins('New application from someone')

        recipients = set(
            Notification.objects.values_list('recipient__username', flat=True)
        )
        self.assertEqual(recipients, {admin1.username, admin2.username})


class NotificationContextProcessorTests(TestCase):
    def test_anonymous_visitor_gets_no_count(self):
        response = self.client.get(reverse('login'))
        self.assertNotIn('unread_notification_count', response.context)

    def test_authenticated_user_sees_only_their_unread_count(self):
        user = User.objects.create_user(
            username='counter', password='pw-1234567', role='applicant'
        )
        accept_policy(user)
        other = User.objects.create_user(
            username='someone_else', password='pw-1234567', role='applicant'
        )
        Notification.objects.create(recipient=user, message='unread 1')
        Notification.objects.create(recipient=user, message='unread 2')
        Notification.objects.create(recipient=user, message='already read', is_read=True)
        Notification.objects.create(recipient=other, message='not mine')

        self.client.force_login(user)
        response = self.client.get(reverse('applicant_home'))

        self.assertEqual(response.context['unread_notification_count'], 2)
        self.assertContains(response, 'notif-bell-badge')
        self.assertContains(response, '>2<')


class NotificationsInboxViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='viewer', password='pw-1234567', role='applicant'
        )
        accept_policy(self.user)
        self.other = User.objects.create_user(
            username='other', password='pw-1234567', role='applicant'
        )
        accept_policy(self.other)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse('notifications_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_list_shows_only_the_signed_in_users_own_notifications(self):
        Notification.objects.create(recipient=self.user, message='mine to see')
        Notification.objects.create(recipient=self.other, message='not mine to see')
        self.client.force_login(self.user)

        response = self.client.get(reverse('notifications_list'))

        self.assertContains(response, 'mine to see')
        self.assertNotContains(response, 'not mine to see')

    def test_opening_a_notification_marks_it_read_and_redirects_to_its_link(self):
        n = Notification.objects.create(
            recipient=self.user, message='x', link='/accounts/campus-policy/'
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('notification_open', args=[n.pk]))

        self.assertRedirects(response, '/accounts/campus-policy/')
        n.refresh_from_db()
        self.assertTrue(n.is_read)

    def test_opening_a_notification_with_no_link_goes_to_the_inbox(self):
        n = Notification.objects.create(recipient=self.user, message='x', link='')
        self.client.force_login(self.user)

        response = self.client.get(reverse('notification_open', args=[n.pk]))

        self.assertRedirects(response, reverse('notifications_list'))

    def test_cannot_open_or_read_someone_elses_notification(self):
        theirs = Notification.objects.create(recipient=self.other, message='private')
        self.client.force_login(self.user)

        response = self.client.get(reverse('notification_open', args=[theirs.pk]))

        self.assertEqual(response.status_code, 404)
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_read)

    def test_mark_all_read_only_touches_the_signed_in_users_rows(self):
        Notification.objects.create(recipient=self.user, message='a')
        Notification.objects.create(recipient=self.user, message='b')
        theirs = Notification.objects.create(recipient=self.other, message='c')
        self.client.force_login(self.user)

        self.client.post(reverse('notifications_mark_all_read'))

        self.assertEqual(
            self.user.notifications.filter(is_read=False).count(), 0
        )
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_read)

    def test_mark_all_read_requires_post(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('notifications_mark_all_read'))
        self.assertEqual(response.status_code, 405)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class NewApplicationNotifiesAdminsTests(TestCase):
    """
    appointments.services.book_appointment is the one place every new
    submission (fresh or renewal) passes through — see the comment there
    for why that's the chosen hook instead of duplicating this at every
    call site that can create an application.
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username='reviewer', password='pw-1234567', role='admin'
        )
        self.applicant = User.objects.create_user(
            username='newcomer', password='pw-1234567', role='applicant',
            email='newcomer@psu.palawan.edu.ph',
        )
        accept_policy(self.applicant)
        self.slot = AppointmentSlot.objects.create(
            date=timezone.localdate() + timedelta(days=5),
            is_active=True, capacity=5,
        )
        self.application = StickerApplication.objects.create(
            applicant=self.applicant, full_name='New Comer',
            college_department='CCIS', id_number='2020-9999',
            classification='student', plate_number='NEW-001',
            vehicle_type='four_wheels', vehicle_color='blue', is_owner=True,
            official_receipt=make_doc(), vehicle_registration=make_doc(),
            drivers_license=make_doc(), status='draft',
        )

    def test_booking_an_appointment_notifies_every_admin(self):
        from appointments.services import book_appointment

        with self.captureOnCommitCallbacks(execute=True):
            appointment = book_appointment(self.application, self.slot.pk, '08:00')

        self.assertIsNotNone(appointment)
        notif = Notification.objects.get(recipient=self.admin)
        self.assertIn('NEW-001', notif.message)
        self.assertEqual(
            notif.link, reverse('application_detail', args=[self.application.pk])
        )

    def test_applicant_also_gets_their_own_appointment_notification(self):
        from appointments.services import book_appointment

        with self.captureOnCommitCallbacks(execute=True):
            book_appointment(self.application, self.slot.pk, '08:00')

        notif = Notification.objects.get(recipient=self.applicant)
        self.assertIn('NEW-001', notif.message)
        self.assertEqual(notif.link, reverse('my_applications'))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class StaffActionsNotifyApplicantTests(TestCase):
    """
    The three status-changing admin actions (approve, reject, issue) each
    already emailed the applicant — see applications/notifications.py.
    These pin that the same events now also leave an in-app notification.
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username='staff', password='pw-1234567', role='admin'
        )
        self.applicant = User.objects.create_user(
            username='subject', password='pw-1234567', role='applicant',
            email='subject@psu.palawan.edu.ph',
        )
        accept_policy(self.applicant)
        self.application = StickerApplication.objects.create(
            applicant=self.applicant, full_name='Notify Subject',
            college_department='CCIS', id_number='2020-8888',
            classification='student', plate_number='NOT-001',
            vehicle_type='four_wheels', vehicle_color='black', is_owner=True,
            official_receipt=make_doc(), vehicle_registration=make_doc(),
            drivers_license=make_doc(), status='scheduled',
        )

    def test_approve_notifies_the_applicant_in_app(self):
        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse('approve_application', args=[self.application.pk])
            )

        notif = Notification.objects.get(recipient=self.applicant)
        self.assertIn('approved', notif.message.lower())
        self.assertIn('NOT-001', notif.message)
        self.assertEqual(notif.link, reverse('my_applications'))

    def test_reject_notifies_the_applicant_in_app(self):
        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse('reject_application', args=[self.application.pk]),
                data={'rejection_reason': 'Blurry OR/CR scan.'},
            )

        notif = Notification.objects.get(recipient=self.applicant)
        self.assertIn('attention', notif.message.lower())

    def test_issuing_a_sticker_notifies_the_applicant_in_app(self):
        self.application.status = 'approved'
        self.application.save()
        self.client.force_login(self.admin)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse('issue_sticker', args=[self.application.pk]),
                data={'rfid_uid': 'FREE-UID-999'},
            )

        notif = Notification.objects.get(recipient=self.applicant)
        self.assertIn('issued', notif.message.lower())
