# Copyright 2014 Rackspace Australia
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

import extras
import hmac
from hashlib import sha1
from time import time
import os
import random
import string
import urlparse

swiftclient = extras.try_import('swiftclient')


class Swift(object):
    def __init__(self, config):
        self.config = config
        self.connection = False
        if self.config.has_option('swift', 'X-Account-Meta-Temp-Url-Key'):
            self.secure_key = self.config.get('swift',
                                              'X-Account-Meta-Temp-Url-Key')
        else:
            self.secure_key = ''.join(
                random.choice(string.ascii_uppercase + string.digits)
                for x in range(20)
            )

        self.connect()

    def connect(self):
        if swiftclient and self.config.has_section('swift'):
            # required
            authurl = self.config.get('swift', 'authurl')

            user = (self.config.get('swift', 'user')
                    if self.config.has_option('swift', 'user') else None)
            key = (self.config.get('swift', 'key')
                   if self.config.has_option('swift', 'key') else None)
            retries = (self.config.get('swift', 'retries')
                       if self.config.has_option('swift', 'retries') else 5)
            preauthurl = (self.config.get('swift', 'preauthurl')
                          if self.config.has_option('swift', 'preauthurl')
                          else None)
            preauthtoken = (self.config.get('swift', 'preauthtoken')
                            if self.config.has_option('swift', 'preauthtoken')
                            else None)
            snet = (self.config.get('swift', 'snet')
                    if self.config.has_option('swift', 'snet') else False)
            starting_backoff = (self.config.get('swift', 'starting_backoff')
                                if self.config.has_option('swift',
                                                          'starting_backoff')
                                else 1)
            max_backoff = (self.config.get('swift', 'max_backoff')
                           if self.config.has_option('swift', 'max_backoff')
                           else 64)
            tenant_name = (self.config.get('swift', 'tenant_name')
                           if self.config.has_option('swift', 'tenant_name')
                           else None)
            auth_version = (self.config.get('swift', 'auth_version')
                            if self.config.has_option('swift', 'auth_version')
                            else 2.0)
            cacert = (self.config.get('swift', 'cacert')
                      if self.config.has_option('swift', 'cacert') else None)
            insecure = (self.config.get('swift', 'insecure')
                        if self.config.has_option('swift', 'insecure')
                        else False)
            ssl_compression = (self.config.get('swift', 'ssl_compression')
                               if self.config.has_option('swift',
                                                         'ssl_compression')
                               else True)

            available_os_options = ['tenant_id', 'auth_token', 'service_type',
                                    'endpoint_type', 'tenant_name',
                                    'object_storage_url', 'region_name']

            os_options = {}
            for os_option in available_os_options:
                if self.config.has_option('swift', os_option):
                    os_options[os_option] = self.config.get('swift', os_option)

            self.connection = swiftclient.client.Connection(
                authurl=authurl, user=user, key=key, retries=retries,
                preauthurl=preauthurl, preauthtoken=preauthtoken, snet=snet,
                starting_backoff=starting_backoff, max_backoff=max_backoff,
                tenant_name=tenant_name, os_options=os_options,
                auth_version=auth_version, cacert=cacert, insecure=insecure,
                ssl_compression=ssl_compression)

            # Tell swift of our key
            headers = {}
            headers['X-Account-Meta-Temp-Url-Key'] = self.secure_key
            self.connection.post_account(headers)

            self.storage_url, self.auth_token = self.connection.get_auth()

    def generate_form_post_middleware_params(self, destination_prefix='',
                                             **kwargs):
        """Generate the FormPost middleware params for the given settings"""

        # Define the available settings and their defaults
        settings = {
            'container': '',
            'expiry': 7200,
            'max_file_size': 104857600,
            'max_file_count': 10,
            'file_path_prefix': ''
        }

        for key, default in settings.iteritems():
            if key in kwargs:
                settings[key] = kwargs[key]
            elif self.config.has_option('swift', 'default_' + key):
                settings[key] = self.config.get('swift', 'default_' + key)

        expires = int(time() + settings['expiry'])
        redirect = ''

        url = os.path.join(self.storage_url, settings['container'],
                           settings['file_path_prefix'],
                           destination_prefix)
        u = urlparse.urlparse(url)

        hmac_body = '%s\n%s\n%s\n%s\n%s' % (u.path, redirect,
                                            settings['max_file_size'],
                                            settings['max_file_count'],
                                            expires)

        signature = hmac.new(self.secure_key, hmac_body, sha1).hexdigest()

        return url, hmac_body, signature
