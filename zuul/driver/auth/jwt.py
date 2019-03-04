# Copyright 2019 OpenStack Foundation
# Copyright 2019 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import logging
import time
import jwt
import requests
import json

from zuul import exceptions
from zuul.driver import AuthenticatorInterface


logger = logging.getLogger("zuul.auth.jwt")


class JWTAuthenticator(AuthenticatorInterface):
    """The base class for JWT-based authentication."""

    def __init__(self, **conf):
        # Common configuration for all authenticators
        self.uid_claim = conf.get('uid_claim', 'sub')
        self.issuer_id = conf.get('issuer_id')
        self.audience = conf.get('client_id')
        self.realm = conf.get('realm')
        self.allow_authz_override = conf.get('allow_authz_override', False)
        if isinstance(self.allow_authz_override, str):
            if self.allow_authz_override.lower() == 'true':
                self.allow_authz_override = True
            else:
                self.allow_authz_override = False

    def _decode(self, rawToken):
        raise NotImplementedError

    def decodeToken(self, rawToken):
        """Verify the raw token and return the decoded dictionary of claims"""
        try:
            decoded = self._decode(rawToken)
        except jwt.exceptions.InvalidSignatureError:
            raise exceptions.AuthTokenInvalidSignatureException(
                realm=self.realm)
        except jwt.DecodeError:
            raise exceptions.AuthTokenUndecodedException(
                realm=self.realm)
        except jwt.exceptions.ExpiredSignatureError:
            raise exceptions.TokenExpiredError(
                realm=self.realm)
        except jwt.InvalidIssuerError:
            raise exceptions.IssuerUnknownError(
                realm=self.realm)
        except jwt.InvalidAudienceError:
            raise exceptions.IncorrectAudienceError(
                realm=self.realm)
        except Exception as e:
            raise exceptions.AuthTokenUnauthorizedException(
                realm=self.realm,
                msg=e)
        if not all(x in decoded for x in ['aud', 'iss', 'exp', 'sub']):
            raise exceptions.MissingClaimError(realm=self.realm)
        if self.uid_claim not in decoded:
            raise exceptions.MissingUIDClaimError(realm=self.realm)
        expires = decoded.get('exp', 0)
        if expires < time.time():
            raise exceptions.TokenExpiredError(realm=self.realm)
        zuul_claims = decoded.get('zuul', {})
        admin_tenants = zuul_claims.get('admin', [])
        if not isinstance(admin_tenants, list):
            raise exceptions.IncorrectZuulAdminClaimError(realm=self.realm)
        if admin_tenants and not self.allow_authz_override:
            msg = ('Issuer "%s" attempt to override User "%s" '
                   'authorization denied')
            logger.info(msg % (decoded['iss'], decoded[self.uid_claim]))
            logger.debug('%r' % admin_tenants)
            raise exceptions.UnauthorizedZuulAdminClaimError(
                realm=self.realm)
        if admin_tenants and self.allow_authz_override:
            msg = ('Issuer "%s" attempt to override User "%s" '
                   'authorization granted')
            logger.info(msg % (decoded['iss'], decoded[self.uid_claim]))
            logger.debug('%r' % admin_tenants)
        return decoded

    def authenticate(self, rawToken):
        decoded = self.decodeToken(rawToken)
        # inject the special authenticator-specific uid
        decoded['__zuul_uid_claim'] = decoded[self.uid_claim]
        return decoded


class HS256Authenticator(JWTAuthenticator):
    """JWT authentication using the HS256 algorithm.

    Requires a shared secret between Zuul and the identity provider."""

    name = algorithm = 'HS256'

    def __init__(self, **conf):
        super(HS256Authenticator, self).__init__(**conf)
        self.secret = conf.get('secret')

    def _decode(self, rawToken):
        return jwt.decode(rawToken, self.secret, issuer=self.issuer_id,
                          audience=self.audience,
                          algorithms=self.algorithm)


class RS256Authenticator(JWTAuthenticator):
    """JWT authentication using the RS256 algorithm.

    Requires a copy of the public key of the identity provider."""

    name = algorithm = 'RS256'

    def __init__(self, **conf):
        super(RS256Authenticator, self).__init__(**conf)
        with open(conf.get('public_key')) as pk:
            self.public_key = pk.read()

    def _decode(self, rawToken):
        return jwt.decode(rawToken, self.public_key, issuer=self.issuer_id,
                          audience=self.audience,
                          algorithms=self.algorithm)


class RS256withJWKSAuthenticator(JWTAuthenticator):
    """JWT authentication using the RS256 algorithm.

    Requires the URL of the certificates used by the Identity Provier. It can
    be found usually under the key "jwks_uri" at the provider's
    .well-known/openid-configuration URL."""

    algorithm = 'RS256'
    name = 'RS256withJWKS'

    def __init__(self, **conf):
        super(RS256withJWKSAuthenticator, self).__init__(**conf)
        self.keys_url = conf.get('keys_url', None)

    def _decode(self, rawToken):
        unverified_headers = jwt.get_unverified_header(rawToken)
        key_id = unverified_headers.get('kid', None)
        if key_id is None:
            raise exceptions.JWKSException(
                self.realm, 'No key ID in token header')
        # TODO keys can probably be cached
        try:
            certs = requests.get(self.keys_url).json()
        except Exception as e:
            msg = 'Could not fetch Identity Provider keys at %s: %s'
            logger.error(msg % (self.keys_url, e))
            raise exceptions.JWKSException(
                realm=self.realm,
                msg='There was an error while fetching '
                    'keys for Identity Provider')
        for key_dict in certs['keys']:
            if key_dict.get('kid') == key_id:
                key = jwt.algorithms.RSAAlgorithm.from_jwk(
                    json.dumps(key_dict))
                return jwt.decode(rawToken, key, issuer=self.issuer_id,
                                  audience=self.audience,
                                  algorithms=self.algorithm)
        raise exceptions.JWKSException(
            self.realm,
            'Cannot verify token: public key %s '
            'not listed by Identity Provider' % key_id)


AUTHENTICATORS = {
    'HS256': HS256Authenticator,
    'RS256': RS256Authenticator,
    'RS256withJWKS': RS256withJWKSAuthenticator,
}


def get_authenticator_by_name(name):
    return AUTHENTICATORS[name]
