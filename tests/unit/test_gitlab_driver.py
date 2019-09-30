# Copyright 2019 Red Hat
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

import socket

from tests.base import ZuulTestCase, simple_layout
from tests.base import ZuulWebFixture


class TestGitlabWebhook(ZuulTestCase):
    config_file = 'zuul-gitlab-driver.conf'

    def setUp(self):
        super().setUp()

        # Start the web server
        self.web = self.useFixture(
            ZuulWebFixture(self.gearman_server.port,
                           self.config, self.test_root))

        host = '127.0.0.1'
        # Wait until web server is started
        while True:
            port = self.web.port
            try:
                with socket.create_connection((host, port)):
                    break
            except ConnectionRefusedError:
                pass

        self.fake_gitlab.setZuulWebPort(port)

    def tearDown(self):
        super(TestGitlabWebhook, self).tearDown()

    @simple_layout('layouts/basic-gitlab.yaml', driver='gitlab')
    def test_webhook(self):
        pass
