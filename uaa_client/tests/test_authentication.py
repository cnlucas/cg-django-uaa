from unittest import mock
import json
import urllib.parse
import jwt
from django.test import TestCase, RequestFactory
from django.test.utils import override_settings
from django.contrib.auth.models import User
import httmock

from .. import authentication as auth


get_user_by_email = auth.UaaBackend.get_user_by_email


@override_settings(
    DEBUG=True,
    UAA_CLIENT_ID='clientid',
    UAA_CLIENT_SECRET='clientsecret',
    UAA_AUTH_URL='fake:',
    UAA_TOKEN_URL='fake:'
)
class FakeAuthenticationTests(TestCase):
    def test_get_auth_url_works(self):
        req = RequestFactory().get('/')
        self.assertEqual(auth.get_auth_url(req),
                         'http://testserver/fake/oauth/authorize')

    def test_get_token_url_works(self):
        req = RequestFactory().get('/')
        self.assertEqual(auth.get_token_url(req),
                         'http://testserver/fake/oauth/token')

    def test_authorize_endpoint_displays_page_without_email(self):
        res = self.client.get('/fake/oauth/authorize', {
            'client_id': 'clientid',
            'response_type': 'code'
        })
        self.assertEqual(res.status_code, 200)

    def test_authorize_endpoint_redirects_with_email(self):
        res = self.client.get('/fake/oauth/authorize', {
            'client_id': 'clientid',
            'response_type': 'code',
            'email': 'boop@gsa.gov'
        })
        self.assertEqual(res.status_code, 302)

    def test_token_endpoint_works(self):
        res = self.client.post('/fake/oauth/token', {
            'client_id': 'clientid',
            'client_secret': 'clientsecret',
            'grant_type': 'authorization_code',
            'response_type': 'token',
            'code': 'boop@gsa.gov',
        })
        self.assertEqual(res.status_code, 200)
        obj = json.loads(res.content.decode('utf-8'))
        user_info = jwt.decode(obj['access_token'], verify=False)
        self.assertEqual(user_info['email'], 'boop@gsa.gov')


@override_settings(
    UAA_CLIENT_ID='clientid',
    UAA_CLIENT_SECRET='clientsecret',
    UAA_AUTH_URL='https://example.org/auth',
    UAA_TOKEN_URL='https://example.org/token'
)
class AuthenticationTests(TestCase):

    def test_get_auth_url_works(self):
        self.assertEqual(auth.get_auth_url(None), 'https://example.org/auth')

    def test_get_token_url_works(self):
        self.assertEqual(auth.get_token_url(None), 'https://example.org/token')

    @mock.patch('uaa_client.authentication.logger.warn')
    def test_exchange_code_for_access_token_returns_none_on_failure(self, m):
        def mock_404_response(url, request):
            return httmock.response(404, "nope")

        req = mock.MagicMock()
        with httmock.HTTMock(mock_404_response):
            self.assertEqual(auth.exchange_code_for_access_token(req, 'u'),
                             None)
        m.assert_called_with('POST https://example.org/token returned 404 '
                             'w/ content b\'nope\'')

    def test_exchange_code_for_access_token_returns_token_on_success(self):
        def mock_200_response(url, request):
            self.assertEqual(request.url, 'https://example.org/token')
            body = dict(urllib.parse.parse_qsl(request.body))
            self.assertEqual(body, {
                'code': 'foo',
                'client_id': 'clientid',
                'client_secret': 'clientsecret',
                'grant_type': 'authorization_code',
                'redirect_uri': 'https://redirect_uri',
                'response_type': 'token',
            })
            return httmock.response(200, {
                'access_token': 'lol',
                'expires_in': 15
            }, {
                'content-type': 'application/json'
            })

        req = mock.MagicMock()
        req.build_absolute_uri.return_value = 'https://redirect_uri'

        with httmock.HTTMock(mock_200_response):
            self.assertEqual(auth.exchange_code_for_access_token(req, 'foo'),
                             'lol')

        req.session.set_expiry.assert_called_with(15)

    def test_get_user_by_email_returns_existing_user(self):
        user = User.objects.create_user('foo', 'foo@example.org')
        self.assertEqual(get_user_by_email('foo@example.org'), user)

    def test_get_user_by_email_is_case_insensitive(self):
        user = User.objects.create_user('foo', 'FOO@example.org')
        self.assertEqual(get_user_by_email('foo@example.org'), user)
        user = User.objects.create_user('bar', 'bar@example.org')
        self.assertEqual(get_user_by_email('BAR@example.org'), user)

    def test_get_user_by_email_returns_none_when_user_does_not_exist(self):
        self.assertEqual(get_user_by_email('foo@example.org'), None)

    def test_authenticate_returns_none_when_kwargs_not_passed(self):
        backend = auth.UaaBackend()
        self.assertEqual(backend.authenticate(), None)

    @mock.patch('uaa_client.authentication.exchange_code_for_access_token',
                return_value=None)
    def test_authenticate_returns_none_when_code_is_invalid(self, m):
        backend = auth.UaaBackend()
        self.assertEqual(backend.authenticate('invalidcode', 'req'), None)
        m.assert_called_with('req', 'invalidcode')

    def test_authenticate_returns_user_on_success(self):
        backend = auth.UaaBackend()
        access_token = jwt.encode({
            'email': 'foo@example.org'
        }, 'unused secret key').decode('ascii')
        User.objects.create_user('foo', 'foo@example.org')

        with mock.patch(
            'uaa_client.authentication.exchange_code_for_access_token',
            return_value=access_token
        ) as ex:
            user = backend.authenticate('validcode', 'req')
            self.assertEqual(user.email, 'foo@example.org')
            ex.assert_called_with('req', 'validcode')

    def test_get_user_returns_none_when_id_is_invalid(self):
        backend = auth.UaaBackend()
        self.assertEqual(backend.get_user(32434), None)

    def test_get_user_returns_user_when_id_is_valid(self):
        backend = auth.UaaBackend()
        user = User.objects.create_user('foo', 'foo@example.org')
        self.assertEqual(backend.get_user(user.id), user)
