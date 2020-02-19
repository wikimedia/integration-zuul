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

import re
import socket

from tests.base import ZuulTestCase, simple_layout
from tests.base import ZuulWebFixture

from testtools.matchers import MatchesRegex


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
        A = self.fake_gitlab.openFakeMergeRequest(
            'org/project', 'master', 'A')
        self.fake_gitlab.emitEvent(A.getMergeRequestOpenedEvent(),
                                   use_zuulweb=False,
                                   project='org/project')
        self.waitUntilSettled()

        self.assertEqual('SUCCESS',
                         self.getJobFromHistory('project-test1').result)

    @simple_layout('layouts/basic-gitlab.yaml', driver='gitlab')
    def test_webhook_via_zuulweb(self):
        A = self.fake_gitlab.openFakeMergeRequest(
            'org/project', 'master', 'A')
        self.fake_gitlab.emitEvent(A.getMergeRequestOpenedEvent(),
                                   use_zuulweb=True,
                                   project='org/project')
        self.waitUntilSettled()

        self.assertEqual('SUCCESS',
                         self.getJobFromHistory('project-test1').result)


class TestGitlabDriver(ZuulTestCase):
    config_file = 'zuul-gitlab-driver.conf'

    @simple_layout('layouts/basic-gitlab.yaml', driver='gitlab')
    def test_merge_request_opened(self):

        description = "This is the\nMR description."
        A = self.fake_gitlab.openFakeMergeRequest(
            'org/project', 'master', 'A', description=description)
        self.fake_gitlab.emitEvent(
            A.getMergeRequestOpenedEvent(), project='org/project')
        self.waitUntilSettled()

        self.assertEqual('SUCCESS',
                         self.getJobFromHistory('project-test1').result)

        self.assertEqual('SUCCESS',
                         self.getJobFromHistory('project-test2').result)

        job = self.getJobFromHistory('project-test2')
        zuulvars = job.parameters['zuul']
        self.assertEqual(str(A.number), zuulvars['change'])
        self.assertEqual(str(A.patch_number), zuulvars['patchset'])
        self.assertEqual('master', zuulvars['branch'])
        self.assertEquals('https://gitlab/org/project/merge_requests/1',
                          zuulvars['items'][0]['change_url'])
        self.assertEqual(zuulvars["message"], description)
        self.assertEqual(2, len(self.history))
        self.assertEqual(2, len(A.notes))
        self.assertEqual(
            A.notes[0]['body'], "Starting check jobs.")
        self.assertThat(
            A.notes[1]['body'],
            MatchesRegex(r'.*project-test1.*SUCCESS.*', re.DOTALL))
        self.assertThat(
            A.notes[1]['body'],
            MatchesRegex(r'.*project-test2.*SUCCESS.*', re.DOTALL))

    @simple_layout('layouts/basic-gitlab.yaml', driver='gitlab')
    def test_merge_request_commented(self):

        A = self.fake_gitlab.openFakeMergeRequest('org/project', 'master', 'A')
        self.fake_gitlab.emitEvent(A.getMergeRequestOpenedEvent())
        self.waitUntilSettled()
        self.assertEqual(2, len(self.history))

        self.fake_gitlab.emitEvent(
            A.getMergeRequestCommentedEvent('I like that change'))
        self.waitUntilSettled()
        self.assertEqual(2, len(self.history))

        self.fake_gitlab.emitEvent(
            A.getMergeRequestCommentedEvent('recheck'))
        self.waitUntilSettled()
        self.assertEqual(4, len(self.history))
