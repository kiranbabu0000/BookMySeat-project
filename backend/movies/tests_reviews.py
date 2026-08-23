"""Review + trailer regression suite.

Covers the required behaviour matrix:

* normal review creation / update / duplication rules
* immediate visibility after submission
* server-derived Verified Booking badge from REAL booking/show/payment data
  (wrong movie, cancelled, failed payment, refunded, show-not-finished all
  refuse verification)
* client-supplied "verified"/booking hints can never forge a badge
* YouTube id extraction / embed URL generation / graceful fallback rendering
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from admin_panel.models import Payment as AdminPayment
from admin_panel.models import Review, Trailer
from movies.models import Booking, Movie, Reservation, Seat, Theater
from movies.reviews import (
    annotate_review_verification,
    find_verified_booking,
    has_completed_viewing,
)
from movies.services import generate_booking_ref
from movies.templatetags.movie_extras import (
    youtube_embed,
    youtube_id,
    youtube_watch_url,
)

VIDEO_ID = 'dQw4w9WgXcQ'


def _movie(name='Review Target', duration=120, **kwargs):
    defaults = {'rating': 8.0, 'cast': 'Actor', 'status': 'now_showing'}
    defaults.update(kwargs)
    return Movie.objects.create(name=name, duration=duration, **defaults)


def _show(movie, when, name='PVR Cinemas'):
    return Theater.objects.create(
        name=name, movie=movie, time=when,
        screen_name='Screen 1', ticket_price=200, status='active',
    )


_seat_n = 0


def _seat(show):
    global _seat_n
    _seat_n += 1
    return Seat.objects.create(
        theater=show, seat_number=f'R{_seat_n}', row_label=f'R{_seat_n}'
    )


def _booking(user, movie, show, status='confirmed', with_reservation=False):
    seat = _seat(show)
    reservation = None
    if with_reservation:
        reservation = Reservation.objects.create(
            token=generate_booking_ref(), user=user, show=show,
            status='booked', payment_status='completed',
            expires_at=timezone.now() + timedelta(hours=1),
        )
    return Booking.objects.create(
        user=user, seat=seat, movie=movie, theater=show,
        status=status, booking_ref=generate_booking_ref(),
        reservation=reservation,
    )


def _pay(booking, status='completed'):
    return AdminPayment.objects.create(
        booking=booking, amount=booking.total or 200,
        payment_method='upi', transaction_id='TXN1', status=status,
    )


class ReviewSubmissionTests(TestCase):
    """Spec section 3/8/9: creation, update, duplicates, UX feedback."""

    def setUp(self):
        self.user = User.objects.create_user('kiran', 'k@example.com', 'pass12345')
        self.other = User.objects.create_user('other', 'o@example.com', 'pass12345')
        self.movie = _movie()
        self.client.force_login(self.user)
        self.url = reverse('submit_review', args=[self.movie.id])
        self.detail = reverse('movie_detail', args=[self.movie.id])

    def post_review(self, rating='5', comment='Great movie!', **extra):
        return self.client.post(self.url, {'rating': rating, 'comment': comment, **extra})

    # TEST CASE 1: no completed booking -> normal review, appears, unverified.
    def test_normal_review_without_any_booking(self):
        response = self.post_review()
        self.assertEqual(response.status_code, 302)
        review = Review.objects.get(movie=self.movie, user=self.user)
        self.assertTrue(review.is_approved)          # visible immediately
        self.assertIsNone(review.booking)
        self.assertFalse(review.is_reported)
        page = self.client.get(self.detail)
        self.assertContains(page, 'Great movie!')
        self.assertNotContains(page, 'Verified Booking')

    # TEST CASE 7: review visible right after the redirect.
    def test_review_appears_immediately_after_submission(self):
        response = self.post_review(comment='Visible instantly')
        self.assertEqual(response.status_code, 302)
        page = self.client.get(self.detail)
        self.assertContains(page, 'Visible instantly')
        self.assertContains(page, 'Review posted successfully!')

    def test_success_message_for_verified_submission(self):
        show = _show(self.movie, timezone.now() - timedelta(hours=6))
        booking = _booking(self.user, self.movie, show, with_reservation=True)
        _pay(booking)
        self.post_review()
        page = self.client.get(self.detail)
        self.assertContains(page, 'Your verified review has been posted!')

    def test_resubmit_updates_same_row_no_duplicates(self):
        self.post_review(rating='4', comment='First take')
        response = self.post_review(rating='2', comment='Changed my mind')
        self.assertEqual(response.status_code, 302)
        reviews = Review.objects.filter(movie=self.movie, user=self.user)
        self.assertEqual(reviews.count(), 1)
        review = reviews.get()
        self.assertEqual(review.rating, 2)
        self.assertEqual(review.comment, 'Changed my mind')
        self.assertIsNotNone(review.edited_at)
        page = self.client.get(self.detail)
        self.assertContains(page, 'Your review has been updated.')

    def test_invalid_rating_shows_error_and_preserves_draft(self):
        self.post_review(rating='9', comment='Needs a real rating')
        self.assertFalse(Review.objects.exists())
        page = self.client.get(self.detail)
        self.assertContains(page, 'Please select a valid rating (1-5).')
        self.assertContains(page, 'Needs a real rating')   # draft text kept

    def test_blank_comment_rejected_with_message(self):
        response = self.client.post(self.url, {'rating': '5', 'comment': '   '})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Review.objects.exists())
        page = self.client.get(self.detail)
        self.assertContains(page, 'Please write a review comment.')

    def test_unauthenticated_user_redirected_to_login(self):
        self.client.logout()
        response = self.post_review()
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])
        self.assertFalse(Review.objects.exists())

    def test_users_have_independent_reviews(self):
        show = _show(self.movie, timezone.now() - timedelta(hours=6))
        booking = _booking(self.user, self.movie, show)
        _pay(booking)
        self.post_review(rating='5', comment='Kiran says hi')
        self.client.force_login(self.other)
        self.post_review(rating='3', comment='Other opinion')
        self.assertEqual(Review.objects.filter(movie=self.movie).count(), 2)
        mine = Review.objects.get(user=self.user, movie=self.movie)
        theirs = Review.objects.get(user=self.other, movie=self.movie)
        self.assertEqual(mine.rating, 5)
        self.assertEqual(theirs.rating, 3)
        self.assertIsNone(theirs.booking)

    def test_client_cannot_forge_verified_or_foreign_booking(self):
        stranger_show = _show(self.movie, timezone.now() - timedelta(hours=6))
        stranger = User.objects.create_user('stranger', 's@example.com', 'pass12345')
        stranger_booking = _booking(stranger, self.movie, stranger_show)
        _pay(stranger_booking)
        response = self.post_review(
            verified='true', is_verified='1', booking=stranger_booking.pk,
        )
        self.assertEqual(response.status_code, 302)
        review = Review.objects.get(movie=self.movie, user=self.user)
        self.assertIsNone(review.booking)                # FK untouched by client
        page = self.client.get(self.detail)
        self.assertNotContains(page, 'Verified Booking')


class ReviewVerificationTests(TestCase):
    """Spec sections 4-6: badge only for genuinely completed viewings."""

    def setUp(self):
        self.user = User.objects.create_user('viewer', 'v@example.com', 'pass12345')
        self.movie = _movie(duration=120)               # 2h runtime
        self.client.force_login(self.user)
        self.url = reverse('submit_review', args=[self.movie.id])
        self.detail = reverse('movie_detail', args=[self.movie.id])

    def _paid_ended_booking(self, movie=None, hours_ago=6, **booking_kwargs):
        target = movie or self.movie
        show = _show(target, timezone.now() - timedelta(hours=hours_ago))
        booking = _booking(self.user, target, show, **booking_kwargs)
        _pay(booking)
        return booking

    def _post(self, **kw):
        kw.setdefault('comment', kw.pop('comment', 'Solid entertainer'))
        kw.setdefault('rating', kw.pop('rating', '5'))
        return self.client.post(self.url, kw)

    def _page_has_badge(self):
        return b'Verified Booking' in self.client.get(self.detail).content

    # TEST CASE 3: paid booking, show ended -> verified.
    def test_verified_after_completed_show(self):
        booking = self._paid_ended_booking()
        response = self._post()
        self.assertEqual(response.status_code, 302)
        review = Review.objects.get(movie=self.movie, user=self.user)
        self.assertEqual(review.booking, booking)
        annotated = annotate_review_verification(
            Review.objects.filter(pk=review.pk), self.movie
        ).get()
        self.assertTrue(annotated.is_verified)
        self.assertTrue(self._page_has_badge())

    # TEST CASE 2: booked but the show has NOT ended -> not verified.
    def test_not_verified_while_show_upcoming(self):
        show = _show(self.movie, timezone.now() + timedelta(hours=3))
        booking = _booking(self.user, self.movie, show)
        _pay(booking)
        self._post()
        annotated = annotate_review_verification(
            Review.objects.all(), self.movie).get()
        self.assertFalse(annotated.is_verified)
        self.assertFalse(self._page_has_badge())

    def test_not_verified_while_show_still_running(self):
        show = _show(self.movie, timezone.now() - timedelta(minutes=30))
        booking = _booking(self.user, self.movie, show)
        _pay(booking)                                    # started 30m ago of 120m
        self._post()
        annotated = annotate_review_verification(
            Review.objects.all(), self.movie).get()
        self.assertFalse(annotated.is_verified)

    # TEST CASE 4: booked movie A, reviewed movie B -> not verified.
    def test_wrong_movie_booking_does_not_verify(self):
        other_movie = _movie(name='Another Film')
        self._paid_ended_booking(movie=other_movie)
        self._post()                                     # reviews self.movie
        annotated = annotate_review_verification(
            Review.objects.all(), self.movie).get()
        self.assertFalse(annotated.is_verified)
        self.assertFalse(self._page_has_badge())

    # TEST CASE 5: payment failed -> not verified.
    def test_failed_payment_does_not_verify(self):
        show = _show(self.movie, timezone.now() - timedelta(hours=6))
        booking = _booking(self.user, self.movie, show, with_reservation=True)
        _pay(booking, status='failed')
        booking.reservation.payment_status = 'failed'
        booking.reservation.save(update_fields=['payment_status'])
        self._post()
        annotated = annotate_review_verification(
            Review.objects.all(), self.movie).get()
        self.assertFalse(annotated.is_verified)

    # TEST CASE 6: cancelled booking (payments refunded) -> not verified.
    def test_cancelled_booking_does_not_verify(self):
        show = _show(self.movie, timezone.now() - timedelta(hours=6))
        booking = _booking(self.user, self.movie, show, with_reservation=True)
        pay = _pay(booking)
        booking.status = 'cancelled'
        booking.save(update_fields=['status'])
        pay.status = 'refunded'
        pay.save(update_fields=['status'])
        booking.reservation.payment_status = 'refunded'
        booking.reservation.save(update_fields=['payment_status'])
        self._post()
        annotated = annotate_review_verification(
            Review.objects.all(), self.movie).get()
        self.assertFalse(annotated.is_verified)

    def test_refunded_payment_without_cancel_flag_does_not_verify(self):
        show = _show(self.movie, timezone.now() - timedelta(hours=6))
        booking = _booking(self.user, self.movie, show)
        pay = _pay(booking)
        pay.status = 'refunded'                          # refund edge case
        pay.save(update_fields=['status'])
        self._post()
        annotated = annotate_review_verification(
            Review.objects.all(), self.movie).get()
        self.assertFalse(annotated.is_verified)

    def test_walkin_reservation_completion_verifies(self):
        show = _show(self.movie, timezone.now() - timedelta(hours=6))
        booking = _booking(self.user, self.movie, show, with_reservation=True)
        # No admin_panel.Payment row: reservation-level completion suffices.
        self._post()
        review = Review.objects.get(movie=self.movie, user=self.user)
        annotated = annotate_review_verification(
            Review.objects.filter(pk=review.pk), self.movie).get()
        self.assertTrue(annotated.is_verified)

    def test_later_edit_after_show_end_verifies(self):
        # Submitted before the show finished, then updated after it ended.
        # Runtime 120m: show started 150m ago -> ended 30m ago.
        show = _show(self.movie, timezone.now() - timedelta(minutes=150))
        booking = _booking(self.user, self.movie, show)
        _pay(booking)
        review = Review.objects.create(
            movie=self.movie, user=self.user, rating='4',
            comment='Trailer looks promising',
        )
        # Backdate creation into the middle of the show (before its end).
        Review.objects.filter(pk=review.pk).update(
            created_at=timezone.now() - timedelta(minutes=140),
        )
        annotated = annotate_review_verification(
            Review.objects.all(), self.movie).get()
        self.assertFalse(annotated.is_verified)
        response = self.client.post(self.url, {
            'rating': '5', 'comment': 'Watched it. Amazing!',
        })
        self.assertEqual(response.status_code, 302)
        annotated = annotate_review_verification(
            Review.objects.filter(pk=review.pk), self.movie).get()
        self.assertTrue(annotated.is_verified)
        review.refresh_from_db()
        self.assertEqual(review.booking, booking)        # evidence attached on edit

    def test_cancelling_after_posting_removes_badge_retroactively(self):
        booking = self._paid_ended_booking(with_reservation=True)
        self._post()
        self.assertTrue(self._page_has_badge())
        pay = AdminPayment.objects.get(booking=booking)
        booking.status = 'cancelled'
        booking.save(update_fields=['status'])
        pay.status = 'refunded'
        pay.save(update_fields=['status'])
        if booking.reservation_id:
            booking.reservation.payment_status = 'refunded'
            booking.reservation.save(update_fields=['payment_status'])
        self.assertFalse(self._page_has_badge())         # derived, never stale

    def test_duration_fallback_when_missing(self):
        movie = _movie(name='No Duration Film', duration=None)
        url = reverse('submit_review', args=[movie.id])
        detail = reverse('movie_detail', args=[movie.id])
        show = _show(movie, timezone.now() - timedelta(minutes=100))  # <180m
        booking = _booking(self.user, movie, show)
        _pay(booking)
        self.client.post(url, {'rating': '4', 'comment': 'Too early'})
        annotated = annotate_review_verification(
            Review.objects.all(), movie).get()
        self.assertFalse(annotated.is_verified)
        self.assertNotIn(b'Verified Booking', self.client.get(detail).content)

    def test_helper_functions(self):
        self.assertFalse(has_completed_viewing(self.user, self.movie))
        self.assertIsNone(find_verified_booking(self.user, self.movie))
        booking = self._paid_ended_booking()
        self.assertTrue(has_completed_viewing(self.user, self.movie))
        found = find_verified_booking(self.user, self.movie)
        # The strict finder also demands the review be written after the end;
        # with no review yet it still locates the qualifying booking itself.
        self.assertIsNotNone(found)


class TrailerEmbedFilterTests(TestCase):
    """YouTube URL parsing hardening (Error 153 root cause)."""

    def test_watch_url_with_params(self):
        url = f'https://www.youtube.com/watch?v={VIDEO_ID}&t=42s&ab_channel=X'
        self.assertEqual(youtube_id(url), VIDEO_ID)
        self.assertEqual(
            youtube_embed(url),
            f'https://www.youtube-nocookie.com/embed/{VIDEO_ID}?rel=0',
        )

    def test_short_link(self):
        self.assertEqual(youtube_id(f'https://youtu.be/{VIDEO_ID}?si=z'), VIDEO_ID)

    def test_embed_shorts_live_v_paths(self):
        for path in ('embed', 'shorts', 'live', 'v'):
            self.assertEqual(
                youtube_id(f'https://www.youtube.com/{path}/{VIDEO_ID}'),
                VIDEO_ID,
            )

    def test_privacy_enhanced_input_roundtrip(self):
        url = f'https://www.youtube-nocookie.com/embed/{VIDEO_ID}'
        self.assertEqual(youtube_id(url), VIDEO_ID)
        self.assertIn('nocookie', youtube_embed(url))

    def test_watch_url_normalisation(self):
        self.assertEqual(
            youtube_watch_url(f'https://youtu.be/{VIDEO_ID}'),
            f'https://www.youtube.com/watch?v={VIDEO_ID}',
        )

    def test_invalid_urls_return_empty(self):
        bad = [
            '',
            None,
            'not a url at all',
            'https://example.com/video/123',
            'https://www.youtube.com/watch?list=PL123&index=2',
            'https://www.youtube.com/watch?v=',                 # empty id
            'javascript:alert(1)',
            '<script>alert(1)</script>',
            'https://www.youtube.com/watch?v=' + 'x' * 40,      # absurd length
        ]
        for url in bad:
            with self.subTest(url=url):
                if url is None:
                    continue                     # stringfilter guards None upstream
                self.assertEqual(youtube_id(url), '')
                self.assertEqual(youtube_embed(url), '')

    def test_unavailable_video_ids_still_parse(self):
        # Deleted/private ids parse fine; the JS onError fallback handles them.
        self.assertEqual(youtube_id('https://youtu.be/xxxxxxxxxxx'), 'xxxxxxxxxxx')


class TrailerPageRenderingTests(TestCase):
    """Fallback card renders instead of a broken iframe (spec 15 / case 8)."""

    def setUp(self):
        self.movie = _movie()

    def test_valid_trailer_renders_embed(self):
        Trailer.objects.create(
            movie=self.movie, title='Official Trailer',
            url=f'https://www.youtube.com/watch?v={VIDEO_ID}',
        )
        response = self.client.get(
            reverse('movie_detail', args=[self.movie.id]))
        content = response.content.decode()
        self.assertIn(f'data-yt-player="{VIDEO_ID}"', content)
        self.assertIn(f'https://www.youtube-nocookie.com/embed/{VIDEO_ID}', content)
        self.assertIn('enablejsapi=1', content)
        self.assertIn('strict-origin-when-cross-origin', content)

    def test_unparseable_trailer_shows_fallback_card(self):
        Trailer.objects.create(
            movie=self.movie, title='Mystery Clip',
            url='https://some-broken-host.example/not-a-youtube-link',
        )
        response = self.client.get(
            reverse('movie_detail', args=[self.movie.id]))
        content = response.content.decode()
        self.assertIn('trailer-fallback', content)
        self.assertIn('Watch this trailer on YouTube', content)
        self.assertNotIn('youtube-nocookie.com/embed', content)
        self.assertNotIn('<iframe class="trailer-card__frame"', content)

    def test_mixed_good_and_bad_trailers(self):
        Trailer.objects.create(
            movie=self.movie, title='Good', url=f'https://youtu.be/{VIDEO_ID}')
        Trailer.objects.create(
            movie=self.movie, title='Bad', url='garbage-entry')
        response = self.client.get(
            reverse('movie_detail', args=[self.movie.id]))
        content = response.content.decode()
        self.assertIn(f'data-yt-player="{VIDEO_ID}"', content)
        self.assertIn('trailer-fallback', content)
