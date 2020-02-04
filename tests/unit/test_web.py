# Copyright 2014 Hewlett-Packard Development Company, L.P.
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

import json
import os
import urllib.parse
import socket
import time
import jwt

import requests

import zuul.web
import zuul.rpcclient

from tests.base import ZuulTestCase, ZuulDBTestCase, AnsibleZuulTestCase
from tests.base import ZuulWebFixture, FIXTURE_DIR, iterate_timeout


class FakeConfig(object):

    def __init__(self, config):
        self.config = config or {}

    def has_option(self, section, option):
        return option in self.config.get(section, {})

    def get(self, section, option):
        return self.config.get(section, {}).get(option)


class BaseTestWeb(ZuulTestCase):
    tenant_config_file = 'config/single-tenant/main.yaml'
    config_ini_data = {}

    def setUp(self):
        super(BaseTestWeb, self).setUp()

        self.zuul_ini_config = FakeConfig(self.config_ini_data)
        # Start the web server
        self.web = self.useFixture(
            ZuulWebFixture(
                self.gearman_server.port,
                self.config,
                self.test_root,
                info=zuul.model.WebInfo.fromConfig(self.zuul_ini_config),
                zk_hosts=self.zk_config))

        self.executor_server.hold_jobs_in_build = True

        self.host = 'localhost'
        self.port = self.web.port
        # Wait until web server is started
        while True:
            try:
                with socket.create_connection((self.host, self.port)):
                    break
            except ConnectionRefusedError:
                pass
        self.base_url = "http://{host}:{port}".format(
            host=self.host, port=self.port)

    def add_base_changes(self):
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        B = self.fake_gerrit.addFakeChange('org/project1', 'master', 'B')
        B.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(B.addApproval('Approved', 1))
        self.waitUntilSettled()

    def get_url(self, url, *args, **kwargs):
        return requests.get(
            urllib.parse.urljoin(self.base_url, url), *args, **kwargs)

    def post_url(self, url, *args, **kwargs):
        return requests.post(
            urllib.parse.urljoin(self.base_url, url), *args, **kwargs)

    def delete_url(self, url, *args, **kwargs):
        return requests.delete(
            urllib.parse.urljoin(self.base_url, url), *args, **kwargs)

    def tearDown(self):
        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()
        super(BaseTestWeb, self).tearDown()


class TestWeb(BaseTestWeb):

    def test_web_index(self):
        "Test that we can retrieve the index page"
        resp = self.get_url('api')
        data = resp.json()
        # no point checking the whole thing; just make sure _something_ we
        # expect is here
        self.assertIn('info', data)

    def test_web_status(self):
        "Test that we can retrieve JSON status info"
        self.add_base_changes()
        self.executor_server.hold_jobs_in_build = True
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.executor_server.release('project-merge')
        self.waitUntilSettled()

        resp = self.get_url("api/tenant/tenant-one/status")
        self.assertIn('Content-Length', resp.headers)
        self.assertIn('Content-Type', resp.headers)
        self.assertEqual(
            'application/json; charset=utf-8', resp.headers['Content-Type'])
        self.assertIn('Access-Control-Allow-Origin', resp.headers)
        self.assertIn('Cache-Control', resp.headers)
        self.assertIn('Last-Modified', resp.headers)
        self.assertTrue(resp.headers['Last-Modified'].endswith(' GMT'))

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

        data = resp.json()
        status_jobs = []
        for p in data['pipelines']:
            for q in p['change_queues']:
                if p['name'] in ['gate', 'conflict']:
                    self.assertEqual(q['window'], 20)
                else:
                    self.assertEqual(q['window'], 0)
                for head in q['heads']:
                    for change in head:
                        self.assertIn(
                            'review.example.com/org/project',
                            change['project_canonical'])
                        self.assertTrue(change['active'])
                        self.assertIn(change['id'], ('1,1', '2,1', '3,1'))
                        for job in change['jobs']:
                            status_jobs.append(job)
        self.assertEqual('project-merge', status_jobs[0]['name'])
        # TODO(mordred) pull uuids from self.builds
        self.assertEqual(
            'stream/{uuid}?logfile=console.log'.format(
                uuid=status_jobs[0]['uuid']),
            status_jobs[0]['url'])
        self.assertEqual(
            'finger://{hostname}/{uuid}'.format(
                hostname=self.executor_server.hostname,
                uuid=status_jobs[0]['uuid']),
            status_jobs[0]['finger_url'])
        # TOOD(mordred) configure a success-url on the base job
        self.assertEqual(
            'finger://{hostname}/{uuid}'.format(
                hostname=self.executor_server.hostname,
                uuid=status_jobs[0]['uuid']),
            status_jobs[0]['report_url'])
        self.assertEqual('project-test1', status_jobs[1]['name'])
        self.assertEqual(
            'stream/{uuid}?logfile=console.log'.format(
                uuid=status_jobs[1]['uuid']),
            status_jobs[1]['url'])
        self.assertEqual(
            'finger://{hostname}/{uuid}'.format(
                hostname=self.executor_server.hostname,
                uuid=status_jobs[1]['uuid']),
            status_jobs[1]['finger_url'])
        self.assertEqual(
            'finger://{hostname}/{uuid}'.format(
                hostname=self.executor_server.hostname,
                uuid=status_jobs[1]['uuid']),
            status_jobs[1]['report_url'])

        self.assertEqual('project-test2', status_jobs[2]['name'])
        self.assertEqual(
            'stream/{uuid}?logfile=console.log'.format(
                uuid=status_jobs[2]['uuid']),
            status_jobs[2]['url'])
        self.assertEqual(
            'finger://{hostname}/{uuid}'.format(
                hostname=self.executor_server.hostname,
                uuid=status_jobs[2]['uuid']),
            status_jobs[2]['finger_url'])
        self.assertEqual(
            'finger://{hostname}/{uuid}'.format(
                hostname=self.executor_server.hostname,
                uuid=status_jobs[2]['uuid']),
            status_jobs[2]['report_url'])

        # check job dependencies
        self.assertIsNotNone(status_jobs[0]['dependencies'])
        self.assertIsNotNone(status_jobs[1]['dependencies'])
        self.assertIsNotNone(status_jobs[2]['dependencies'])
        self.assertEqual(len(status_jobs[0]['dependencies']), 0)
        self.assertEqual(len(status_jobs[1]['dependencies']), 1)
        self.assertEqual(len(status_jobs[2]['dependencies']), 1)
        self.assertIn('project-merge', status_jobs[1]['dependencies'])
        self.assertIn('project-merge', status_jobs[2]['dependencies'])

    def test_web_tenants(self):
        "Test that we can retrieve JSON status info"
        self.add_base_changes()
        self.executor_server.hold_jobs_in_build = True
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.executor_server.release('project-merge')
        self.waitUntilSettled()

        resp = self.get_url("api/tenants")
        self.assertIn('Content-Length', resp.headers)
        self.assertIn('Content-Type', resp.headers)
        self.assertEqual(
            'application/json; charset=utf-8', resp.headers['Content-Type'])
        # self.assertIn('Access-Control-Allow-Origin', resp.headers)
        # self.assertIn('Cache-Control', resp.headers)
        # self.assertIn('Last-Modified', resp.headers)
        data = resp.json()

        self.assertEqual('tenant-one', data[0]['name'])
        self.assertEqual(3, data[0]['projects'])
        self.assertEqual(3, data[0]['queue'])

        # release jobs and check if the queue size is 0
        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

        data = self.get_url("api/tenants").json()
        self.assertEqual('tenant-one', data[0]['name'])
        self.assertEqual(3, data[0]['projects'])
        self.assertEqual(0, data[0]['queue'])

        # test that non-live items are not counted
        self.executor_server.hold_jobs_in_build = True
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        B = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        B.setDependsOn(A, 1)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        req = urllib.request.Request(
            "http://127.0.0.1:%s/api/tenants" % self.port)
        f = urllib.request.urlopen(req)
        data = f.read().decode('utf8')
        data = json.loads(data)

        self.assertEqual('tenant-one', data[0]['name'])
        self.assertEqual(3, data[0]['projects'])
        self.assertEqual(1, data[0]['queue'])

    def test_web_connections_list(self):
        data = self.get_url('api/connections').json()
        connection = {
            'driver': 'gerrit',
            'name': 'gerrit',
            'baseurl': 'https://review.example.com',
            'canonical_hostname': 'review.example.com',
            'server': 'review.example.com',
            'port': 29418,
        }
        self.assertEqual([connection], data)

    def test_web_bad_url(self):
        # do we redirect to index.html
        resp = self.get_url("status/foo")
        self.assertEqual(200, resp.status_code)

    def test_web_find_change(self):
        # can we filter by change id
        self.add_base_changes()
        data = self.get_url("api/tenant/tenant-one/status/change/1,1").json()

        self.assertEqual(1, len(data), data)
        self.assertEqual("org/project", data[0]['project'])

        data = self.get_url("api/tenant/tenant-one/status/change/2,1").json()

        self.assertEqual(1, len(data), data)
        self.assertEqual("org/project1", data[0]['project'], data)

    def test_web_find_job(self):
        # can we fetch the variants for a single job
        data = self.get_url('api/tenant/tenant-one/job/project-test1').json()

        common_config_role = {
            'implicit': True,
            'project_canonical_name': 'review.example.com/common-config',
            'target_name': 'common-config',
            'type': 'zuul',
        }
        source_ctx = {
            'branch': 'master',
            'path': 'zuul.yaml',
            'project': 'common-config',
        }
        run = [{
            'path': 'playbooks/project-test1.yaml',
            'roles': [{
                'implicit': True,
                'project_canonical_name': 'review.example.com/common-config',
                'target_name': 'common-config',
                'type': 'zuul'
            }],
            'secrets': [],
            'source_context': source_ctx,
        }]

        self.assertEqual([
            {
                'name': 'project-test1',
                'abstract': False,
                'ansible_version': None,
                'attempts': 4,
                'branches': [],
                'dependencies': [],
                'description': None,
                'files': [],
                'irrelevant_files': [],
                'match_on_config_updates': True,
                'final': False,
                'implied_branch': None,
                'nodeset': {
                    'groups': [],
                    'name': '',
                    'nodes': [{'comment': None,
                               'hold_job': None,
                               'label': 'label1',
                               'name': 'controller',
                               'aliases': [],
                               'state': 'unknown'}],
                },
                'override_checkout': None,
                'parent': 'base',
                'post_review': None,
                'protected': None,
                'provides': [],
                'required_projects': [],
                'requires': [],
                'roles': [common_config_role],
                'run': run,
                'pre_run': [],
                'post_run': [],
                'cleanup_run': [],
                'semaphore': None,
                'source_context': source_ctx,
                'tags': [],
                'timeout': None,
                'variables': {},
                'variant_description': '',
                'voting': True
            }, {
                'name': 'project-test1',
                'abstract': False,
                'ansible_version': None,
                'attempts': 3,
                'branches': ['stable'],
                'dependencies': [],
                'description': None,
                'files': [],
                'irrelevant_files': [],
                'match_on_config_updates': True,
                'final': False,
                'implied_branch': None,
                'nodeset': {
                    'groups': [],
                    'name': '',
                    'nodes': [{'comment': None,
                               'hold_job': None,
                               'label': 'label2',
                               'name': 'controller',
                               'aliases': [],
                               'state': 'unknown'}],
                },
                'override_checkout': None,
                'parent': 'base',
                'post_review': None,
                'protected': None,
                'provides': [],
                'required_projects': [],
                'requires': [],
                'roles': [common_config_role],
                'run': run,
                'pre_run': [],
                'post_run': [],
                'cleanup_run': [],
                'semaphore': None,
                'source_context': source_ctx,
                'tags': [],
                'timeout': None,
                'variables': {},
                'variant_description': 'stable',
                'voting': True
            }], data)

        data = self.get_url('api/tenant/tenant-one/job/test-job').json()
        run[0]['path'] = 'playbooks/project-merge.yaml'
        self.assertEqual([
            {
                'abstract': False,
                'ansible_version': None,
                'attempts': 3,
                'branches': [],
                'dependencies': [],
                'description': None,
                'files': [],
                'final': False,
                'implied_branch': None,
                'irrelevant_files': [],
                'match_on_config_updates': True,
                'name': 'test-job',
                'override_checkout': None,
                'parent': 'base',
                'post_review': None,
                'protected': None,
                'provides': [],
                'required_projects': [
                    {'override_branch': None,
                     'override_checkout': None,
                     'project_name': 'review.example.com/org/project'}],
                'requires': [],
                'roles': [common_config_role],
                'run': run,
                'pre_run': [],
                'post_run': [],
                'cleanup_run': [],
                'semaphore': None,
                'source_context': source_ctx,
                'tags': [],
                'timeout': None,
                'variables': {},
                'variant_description': '',
                'voting': True
            }], data)

    def test_find_job_complete_playbooks(self):
        # can we fetch the variants for a single job
        data = self.get_url('api/tenant/tenant-one/job/complete-job').json()

        def expected_pb(path):
            return {
                'path': path,
                'roles': [{
                    'implicit': True,
                    'project_canonical_name':
                    'review.example.com/common-config',
                    'target_name': 'common-config',
                    'type': 'zuul'
                }],
                'secrets': [],
                'source_context': {
                    'branch': 'master',
                    'path': 'zuul.yaml',
                    'project': 'common-config',
                }
            }
        self.assertEqual([
            expected_pb("playbooks/run.yaml")
        ], data[0]['run'])
        self.assertEqual([
            expected_pb("playbooks/pre-run.yaml")
        ], data[0]['pre_run'])
        self.assertEqual([
            expected_pb("playbooks/post-run-01.yaml"),
            expected_pb("playbooks/post-run-02.yaml")
        ], data[0]['post_run'])
        self.assertEqual([
            expected_pb("playbooks/cleanup-run.yaml")
        ], data[0]['cleanup_run'])

    def test_web_nodes_list(self):
        # can we fetch the nodes list
        self.add_base_changes()
        data = self.get_url('api/tenant/tenant-one/nodes').json()
        self.assertGreater(len(data), 0)
        self.assertEqual("test-provider", data[0]["provider"])
        self.assertEqual("label1", data[0]["type"])

    def test_web_labels_list(self):
        # can we fetch the labels list
        data = self.get_url('api/tenant/tenant-one/labels').json()
        expected_list = [{'name': 'label1'}]
        self.assertEqual(expected_list, data)

    def test_web_pipeline_list(self):
        # can we fetch the list of pipelines
        data = self.get_url('api/tenant/tenant-one/pipelines').json()

        gerrit_trigger = {'name': 'gerrit', 'driver': 'gerrit'}
        timer_trigger = {'name': 'timer', 'driver': 'timer'}
        expected_list = [
            {'name': 'check', 'triggers': [gerrit_trigger]},
            {'name': 'gate', 'triggers': [gerrit_trigger]},
            {'name': 'post', 'triggers': [gerrit_trigger]},
            {'name': 'periodic', 'triggers': [timer_trigger]},
        ]
        self.assertEqual(expected_list, data)

    def test_web_project_list(self):
        # can we fetch the list of projects
        data = self.get_url('api/tenant/tenant-one/projects').json()

        expected_list = [
            {'name': 'common-config', 'type': 'config'},
            {'name': 'org/project', 'type': 'untrusted'},
            {'name': 'org/project1', 'type': 'untrusted'},
            {'name': 'org/project2', 'type': 'untrusted'}
        ]
        for p in expected_list:
            p["canonical_name"] = "review.example.com/%s" % p["name"]
            p["connection_name"] = "gerrit"
        self.assertEqual(expected_list, data)

    def test_web_project_get(self):
        # can we fetch project details
        data = self.get_url(
            'api/tenant/tenant-one/project/org/project1').json()

        jobs = [[{'abstract': False,
                  'ansible_version': None,
                  'attempts': 3,
                  'branches': [],
                  'dependencies': [],
                  'description': None,
                  'files': [],
                  'final': False,
                  'implied_branch': None,
                  'irrelevant_files': [],
                  'match_on_config_updates': True,
                  'name': 'project-merge',
                  'override_checkout': None,
                  'parent': 'base',
                  'post_review': None,
                  'protected': None,
                  'provides': [],
                  'required_projects': [],
                  'requires': [],
                  'roles': [],
                  'run': [],
                  'pre_run': [],
                  'post_run': [],
                  'cleanup_run': [],
                  'semaphore': None,
                  'source_context': {
                      'branch': 'master',
                      'path': 'zuul.yaml',
                      'project': 'common-config'},
                  'tags': [],
                  'timeout': None,
                  'variables': {},
                  'variant_description': '',
                  'voting': True}],
                [{'abstract': False,
                  'ansible_version': None,
                  'attempts': 3,
                  'branches': [],
                  'dependencies': [{'name': 'project-merge',
                                    'soft': False}],
                  'description': None,
                  'files': [],
                  'final': False,
                  'implied_branch': None,
                  'irrelevant_files': [],
                  'match_on_config_updates': True,
                  'name': 'project-test1',
                  'override_checkout': None,
                  'parent': 'base',
                  'post_review': None,
                  'protected': None,
                  'provides': [],
                  'required_projects': [],
                  'requires': [],
                  'roles': [],
                  'run': [],
                  'pre_run': [],
                  'post_run': [],
                  'cleanup_run': [],
                  'semaphore': None,
                  'source_context': {
                      'branch': 'master',
                      'path': 'zuul.yaml',
                      'project': 'common-config'},
                  'tags': [],
                  'timeout': None,
                  'variables': {},
                  'variant_description': '',
                  'voting': True}],
                [{'abstract': False,
                  'ansible_version': None,
                  'attempts': 3,
                  'branches': [],
                  'dependencies': [{'name': 'project-merge',
                                    'soft': False}],
                  'description': None,
                  'files': [],
                  'final': False,
                  'implied_branch': None,
                  'irrelevant_files': [],
                  'match_on_config_updates': True,
                  'name': 'project-test2',
                  'override_checkout': None,
                  'parent': 'base',
                  'post_review': None,
                  'protected': None,
                  'provides': [],
                  'required_projects': [],
                  'requires': [],
                  'roles': [],
                  'run': [],
                  'pre_run': [],
                  'post_run': [],
                  'cleanup_run': [],
                  'semaphore': None,
                  'source_context': {
                      'branch': 'master',
                      'path': 'zuul.yaml',
                      'project': 'common-config'},
                  'tags': [],
                  'timeout': None,
                  'variables': {},
                  'variant_description': '',
                  'voting': True}],
                [{'abstract': False,
                  'ansible_version': None,
                  'attempts': 3,
                  'branches': [],
                  'dependencies': [{'name': 'project-merge',
                                    'soft': False}],
                  'description': None,
                  'files': [],
                  'final': False,
                  'implied_branch': None,
                  'irrelevant_files': [],
                  'match_on_config_updates': True,
                  'name': 'project1-project2-integration',
                  'override_checkout': None,
                  'parent': 'base',
                  'post_review': None,
                  'protected': None,
                  'provides': [],
                  'required_projects': [],
                  'requires': [],
                  'roles': [],
                  'run': [],
                  'pre_run': [],
                  'post_run': [],
                  'cleanup_run': [],
                  'semaphore': None,
                  'source_context': {
                      'branch': 'master',
                      'path': 'zuul.yaml',
                      'project': 'common-config'},
                  'tags': [],
                  'timeout': None,
                  'variables': {},
                  'variant_description': '',
                  'voting': True}]]

        self.assertEqual(
            {
                'canonical_name': 'review.example.com/org/project1',
                'connection_name': 'gerrit',
                'name': 'org/project1',
                'configs': [{
                    'templates': [],
                    'default_branch': 'master',
                    'merge_mode': 'merge-resolve',
                    'pipelines': [{
                        'name': 'check',
                        'queue_name': None,
                        'jobs': jobs,
                    }, {
                        'name': 'gate',
                        'queue_name': 'integrated',
                        'jobs': jobs,
                    }, {'name': 'post',
                        'queue_name': None,
                        'jobs': [[
                            {'abstract': False,
                             'ansible_version': None,
                             'attempts': 3,
                             'branches': [],
                             'dependencies': [],
                             'description': None,
                             'files': [],
                             'final': False,
                             'implied_branch': None,
                             'irrelevant_files': [],
                             'match_on_config_updates': True,
                             'name': 'project-post',
                             'override_checkout': None,
                             'parent': 'base',
                             'post_review': None,
                             'post_run': [],
                             'cleanup_run': [],
                             'pre_run': [],
                             'protected': None,
                             'provides': [],
                             'required_projects': [],
                             'requires': [],
                             'roles': [],
                             'run': [],
                             'semaphore': None,
                             'source_context': {'branch': 'master',
                                                'path': 'zuul.yaml',
                                                'project': 'common-config'},
                             'tags': [],
                             'timeout': None,
                             'variables': {},
                             'variant_description': '',
                             'voting': True}
                        ]],
                    }
                    ]
                }]
            }, data)

    def test_web_keys(self):
        with open(os.path.join(FIXTURE_DIR, 'public.pem'), 'rb') as f:
            public_pem = f.read()

        resp = self.get_url("api/tenant/tenant-one/key/org/project.pub")
        self.assertEqual(resp.content, public_pem)
        self.assertIn('text/plain', resp.headers.get('Content-Type'))

        resp = self.get_url("api/tenant/non-tenant/key/org/project.pub")
        self.assertEqual(404, resp.status_code)

        resp = self.get_url("api/tenant/tenant-one/key/org/no-project.pub")
        self.assertEqual(404, resp.status_code)

        with open(os.path.join(FIXTURE_DIR, 'ssh.pub'), 'rb') as f:
            public_ssh = f.read()

        resp = self.get_url("api/tenant/tenant-one/project-ssh-key/"
                            "org/project.pub")
        self.assertEqual(resp.content, public_ssh)
        self.assertIn('text/plain', resp.headers.get('Content-Type'))

    def test_web_404_on_unknown_tenant(self):
        resp = self.get_url("api/tenant/non-tenant/status")
        self.assertEqual(404, resp.status_code)

    def test_autohold_info_404_on_invalid_id(self):
        resp = self.get_url("api/tenant/tenant-one/autohold/12345")
        self.assertEqual(404, resp.status_code)

    def test_autohold_delete_404_on_invalid_id(self):
        resp = self.delete_url("api/tenant/tenant-one/autohold/12345")
        self.assertEqual(404, resp.status_code)

    def test_autohold_info(self):
        client = zuul.rpcclient.RPCClient('127.0.0.1',
                                          self.gearman_server.port)
        self.addCleanup(client.shutdown)
        r = client.autohold('tenant-one', 'org/project', 'project-test2',
                            "", "", "reason text", 1)
        self.assertTrue(r)

        # Use autohold-list API to retrieve request ID
        resp = self.get_url(
            "api/tenant/tenant-one/autohold")
        self.assertEqual(200, resp.status_code, resp.text)
        autohold_requests = resp.json()
        self.assertNotEqual([], autohold_requests)
        self.assertEqual(1, len(autohold_requests))
        request_id = autohold_requests[0]['id']

        # Now try the autohold-info API
        resp = self.get_url("api/tenant/tenant-one/autohold/%s" % request_id)
        self.assertEqual(200, resp.status_code, resp.text)
        request = resp.json()

        self.assertEqual(request_id, request['id'])
        self.assertEqual('tenant-one', request['tenant'])
        self.assertIn('org/project', request['project'])
        self.assertEqual('project-test2', request['job'])
        self.assertEqual(".*", request['ref_filter'])
        self.assertEqual(1, request['count'])
        self.assertEqual("reason text", request['reason'])

    def test_autohold_list(self):
        """test listing autoholds through zuul-web"""
        client = zuul.rpcclient.RPCClient('127.0.0.1',
                                          self.gearman_server.port)
        self.addCleanup(client.shutdown)
        r = client.autohold('tenant-one', 'org/project', 'project-test2',
                            "", "", "reason text", 1)
        self.assertTrue(r)
        resp = self.get_url(
            "api/tenant/tenant-one/autohold")
        self.assertEqual(200, resp.status_code, resp.text)
        autohold_requests = resp.json()

        self.assertNotEqual([], autohold_requests)
        self.assertEqual(1, len(autohold_requests))
        ah_request = autohold_requests[0]

        self.assertEqual('tenant-one', ah_request['tenant'])
        self.assertIn('org/project', ah_request['project'])
        self.assertEqual('project-test2', ah_request['job'])
        self.assertEqual(".*", ah_request['ref_filter'])
        self.assertEqual(1, ah_request['count'])
        self.assertEqual("reason text", ah_request['reason'])

        # filter by project
        resp = self.get_url(
            "api/tenant/tenant-one/autohold?project=org/project2")
        self.assertEqual(200, resp.status_code, resp.text)
        autohold_requests = resp.json()
        self.assertEqual([], autohold_requests)
        resp = self.get_url(
            "api/tenant/tenant-one/autohold?project=org/project")
        self.assertEqual(200, resp.status_code, resp.text)
        autohold_requests = resp.json()

        self.assertNotEqual([], autohold_requests)
        self.assertEqual(1, len(autohold_requests))
        ah_request = autohold_requests[0]

        self.assertEqual('tenant-one', ah_request['tenant'])
        self.assertIn('org/project', ah_request['project'])
        self.assertEqual('project-test2', ah_request['job'])
        self.assertEqual(".*", ah_request['ref_filter'])
        self.assertEqual(1, ah_request['count'])
        self.assertEqual("reason text", ah_request['reason'])

    def test_admin_routes_404_by_default(self):
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/autohold",
            json={'job': 'project-test1',
                  'count': 1,
                  'reason': 'because',
                  'node_hold_expiration': 36000})
        self.assertEqual(404, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            json={'trigger': 'gerrit',
                  'change': '2,1',
                  'pipeline': 'check'})
        self.assertEqual(404, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            json={'trigger': 'gerrit',
                  'ref': 'abcd',
                  'newrev': 'aaaa',
                  'oldrev': 'bbbb',
                  'pipeline': 'check'})
        self.assertEqual(404, resp.status_code)

    def test_jobs_list(self):
        jobs = self.get_url("api/tenant/tenant-one/jobs").json()
        self.assertEqual(len(jobs), 10)

        resp = self.get_url("api/tenant/non-tenant/jobs")
        self.assertEqual(404, resp.status_code)

    def test_jobs_list_variants(self):
        resp = self.get_url("api/tenant/tenant-one/jobs").json()
        for job in resp:
            if job['name'] in ["base", "noop"]:
                variants = None
            elif job['name'] == 'project-test1':
                variants = [
                    {'parent': 'base'},
                    {'branches': ['stable'], 'parent': 'base'},
                ]
            else:
                variants = [{'parent': 'base'}]
            self.assertEqual(variants, job.get('variants'))

    def test_jobs_list_tags(self):
        resp = self.get_url("api/tenant/tenant-one/jobs").json()

        post_job = None
        for job in resp:
            if job['name'] == 'project-post':
                post_job = job
                break
        self.assertIsNotNone(post_job)
        self.assertEqual(['post'], post_job.get('tags'))

    def test_web_job_noop(self):
        job = self.get_url("api/tenant/tenant-one/job/noop").json()
        self.assertEqual("noop", job[0]["name"])

    def test_freeze_jobs(self):
        # Test can get a list of the jobs for a given project+pipeline+branch.
        resp = self.get_url(
            "api/tenant/tenant-one/pipeline/check"
            "/project/org/project1/branch/master/freeze-jobs")

        freeze_jobs = [{
            'name': 'project-merge',
            'dependencies': [],
        }, {
            'name': 'project-test1',
            'dependencies': [{
                'name': 'project-merge',
                'soft': False,
            }],
        }, {
            'name': 'project-test2',
            'dependencies': [{
                'name': 'project-merge',
                'soft': False,
            }],
        }, {
            'name': 'project1-project2-integration',
            'dependencies': [{
                'name': 'project-merge',
                'soft': False,
            }],
        }]
        self.assertEqual(freeze_jobs, resp.json())

    def test_freeze_jobs_set_includes_all_jobs(self):
        # When freezing a job set we want to include all jobs even if they
        # have certain matcher requirements (such as required files) since we
        # can't otherwise evaluate them.

        resp = self.get_url(
            "api/tenant/tenant-one/pipeline/gate"
            "/project/org/project/branch/master/freeze-jobs")
        expected = {
            'name': 'project-testfile',
            'dependencies': [{
                'name': 'project-merge',
                'soft': False,
            }],
        }
        self.assertIn(expected, resp.json())


class TestWebMultiTenant(BaseTestWeb):
    tenant_config_file = 'config/multi-tenant/main.yaml'

    def test_web_labels_allowed_list(self):
        labels = ["tenant-one-label", "fake", "tenant-two-label"]
        self.fake_nodepool.registerLauncher(labels, "FakeLauncher2")
        # Tenant-one has label restriction in place on tenant-two
        res = self.get_url('api/tenant/tenant-one/labels').json()
        self.assertEqual([{'name': 'fake'}, {'name': 'tenant-one-label'}], res)
        # Tenant-two has label restriction in place on tenant-one
        expected = ["label1", "fake", "tenant-two-label"]
        res = self.get_url('api/tenant/tenant-two/labels').json()
        self.assertEqual(
            list(map(lambda x: {'name': x}, sorted(expected))), res)


class TestWebSecrets(BaseTestWeb):
    tenant_config_file = 'config/secrets/main.yaml'

    def test_web_find_job_secret(self):
        data = self.get_url('api/tenant/tenant-one/job/project1-secret').json()
        run = data[0]['run']
        secret = {'name': 'project1_secret', 'alias': 'secret_name'}
        self.assertEqual([secret], run[0]['secrets'])


class TestInfo(BaseTestWeb):

    def setUp(self):
        super(TestInfo, self).setUp()
        web_config = self.config_ini_data.get('web', {})
        self.websocket_url = web_config.get('websocket_url')
        self.stats_url = web_config.get('stats_url')
        statsd_config = self.config_ini_data.get('statsd', {})
        self.stats_prefix = statsd_config.get('prefix')

    def test_info(self):
        info = self.get_url("api/info").json()
        self.assertEqual(
            info, {
                "info": {
                    "capabilities": {
                        "job_history": False
                    },
                    "stats": {
                        "url": self.stats_url,
                        "prefix": self.stats_prefix,
                        "type": "graphite",
                    },
                    "websocket_url": self.websocket_url,
                }
            })

    def test_tenant_info(self):
        info = self.get_url("api/tenant/tenant-one/info").json()
        self.assertEqual(
            info, {
                "info": {
                    "tenant": "tenant-one",
                    "capabilities": {
                        "job_history": False
                    },
                    "stats": {
                        "url": self.stats_url,
                        "prefix": self.stats_prefix,
                        "type": "graphite",
                    },
                    "websocket_url": self.websocket_url,
                }
            })


class TestTenantInfoConfigBroken(BaseTestWeb):

    tenant_config_file = 'config/broken/main.yaml'

    def test_tenant_info_broken_config(self):
        config_errors = self.get_url(
            "api/tenant/tenant-one/config-errors").json()
        self.assertEqual(
            len(config_errors), 2)

        self.assertEqual(
            config_errors[0]['source_context']['project'], 'org/project3')
        self.assertIn('Zuul encountered an error while accessing the repo '
                      'org/project3',
                      config_errors[0]['error'])

        self.assertEqual(
            config_errors[1]['source_context']['project'], 'org/project2')
        self.assertEqual(
            config_errors[1]['source_context']['branch'], 'master')
        self.assertEqual(
            config_errors[1]['source_context']['path'], '.zuul.yaml')
        self.assertIn('Zuul encountered a syntax error',
                      config_errors[1]['error'])

        resp = self.get_url("api/tenant/non-tenant/config-errors")
        self.assertEqual(404, resp.status_code)


class TestWebSocketInfo(TestInfo):

    config_ini_data = {
        'web': {
            'websocket_url': 'wss://ws.example.com'
        }
    }


class TestGraphiteUrl(TestInfo):

    config_ini_data = {
        'statsd': {
            'prefix': 'example'
        },
        'web': {
            'stats_url': 'https://graphite.example.com',
        }
    }


class TestBuildInfo(ZuulDBTestCase, BaseTestWeb):
    config_file = 'zuul-sql-driver.conf'
    tenant_config_file = 'config/sql-driver/main.yaml'

    def test_web_list_builds(self):
        # Generate some build records in the db.
        self.add_base_changes()
        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

        builds = self.get_url("api/tenant/tenant-one/builds").json()
        self.assertEqual(len(builds), 6)

        uuid = builds[0]['uuid']
        build = self.get_url("api/tenant/tenant-one/build/%s" % uuid).json()
        self.assertEqual(build['job_name'], builds[0]['job_name'])

        resp = self.get_url("api/tenant/tenant-one/build/1234")
        self.assertEqual(404, resp.status_code)

        builds_query = self.get_url("api/tenant/tenant-one/builds?"
                                    "project=org/project&"
                                    "project=org/project1").json()
        self.assertEqual(len(builds_query), 6)

        resp = self.get_url("api/tenant/non-tenant/builds")
        self.assertEqual(404, resp.status_code)

    def test_web_list_buildsets(self):
        # Generate some build records in the db.
        self.add_base_changes()
        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

        buildsets = self.get_url("api/tenant/tenant-one/buildsets").json()
        self.assertEqual(2, len(buildsets))
        project_bs = [x for x in buildsets if x["project"] == "org/project"][0]

        buildset = self.get_url(
            "api/tenant/tenant-one/buildset/%s" % project_bs['uuid']).json()
        self.assertEqual(3, len(buildset["builds"]))

        project_test1_build = [x for x in buildset["builds"]
                               if x["job_name"] == "project-test1"][0]
        self.assertEqual('SUCCESS', project_test1_build['result'])

        project_test2_build = [x for x in buildset["builds"]
                               if x["job_name"] == "project-test2"][0]
        self.assertEqual('SUCCESS', project_test2_build['result'])

        project_merge_build = [x for x in buildset["builds"]
                               if x["job_name"] == "project-merge"][0]
        self.assertEqual('SUCCESS', project_merge_build['result'])


class TestArtifacts(ZuulDBTestCase, BaseTestWeb, AnsibleZuulTestCase):
    config_file = 'zuul-sql-driver.conf'
    tenant_config_file = 'config/sql-driver/main.yaml'

    def test_artifacts(self):
        # Generate some build records in the db.
        self.add_base_changes()
        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

        build_query = self.get_url("api/tenant/tenant-one/builds?"
                                   "project=org/project&"
                                   "job_name=project-test1").json()
        self.assertEqual(len(build_query), 1)
        self.assertEqual(len(build_query[0]['artifacts']), 3)
        arts = build_query[0]['artifacts']
        arts.sort(key=lambda x: x['name'])
        self.assertEqual(build_query[0]['artifacts'], [
            {'url': 'http://example.com/docs',
             'name': 'docs'},
            {'url': 'http://logs.example.com/build/relative/docs',
             'name': 'relative',
             'metadata': {'foo': 'bar'}},
            {'url': 'http://example.com/tarball',
             'name': 'tarball'},
        ])

    def test_buildset_artifacts(self):
        self.add_base_changes()
        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

        buildsets = self.get_url("api/tenant/tenant-one/buildsets").json()
        project_bs = [x for x in buildsets if x["project"] == "org/project"][0]
        buildset = self.get_url(
            "api/tenant/tenant-one/buildset/%s" % project_bs['uuid']).json()
        self.assertEqual(3, len(buildset["builds"]))

        test1_build = [x for x in buildset["builds"]
                       if x["job_name"] == "project-test1"][0]
        arts = test1_build['artifacts']
        arts.sort(key=lambda x: x['name'])
        self.assertEqual([
            {'url': 'http://example.com/docs',
             'name': 'docs'},
            {'url': 'http://logs.example.com/build/relative/docs',
             'name': 'relative',
             'metadata': {'foo': 'bar'}},
            {'url': 'http://example.com/tarball',
             'name': 'tarball'},
        ], test1_build['artifacts'])


class TestTenantScopedWebApi(BaseTestWeb):
    config_file = 'zuul-admin-web.conf'

    def test_admin_routes_no_token(self):
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/autohold",
            json={'job': 'project-test1',
                  'count': 1,
                  'reason': 'because',
                  'node_hold_expiration': 36000})
        self.assertEqual(401, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            json={'trigger': 'gerrit',
                  'change': '2,1',
                  'pipeline': 'check'})
        self.assertEqual(401, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            json={'trigger': 'gerrit',
                  'ref': 'abcd',
                  'newrev': 'aaaa',
                  'oldrev': 'bbbb',
                  'pipeline': 'check'})
        self.assertEqual(401, resp.status_code)

    def test_bad_key_JWT_token(self):
        authz = {'iss': 'zuul_operator',
                 'aud': 'zuul.example.com',
                 'sub': 'testuser',
                 'zuul': {
                     'admin': ['tenant-one', ],
                 },
                 'exp': time.time() + 3600}
        token = jwt.encode(authz, key='OnlyZuulNoDana',
                           algorithm='HS256').decode('utf-8')
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/autohold",
            headers={'Authorization': 'Bearer %s' % token},
            json={'job': 'project-test1',
                  'count': 1,
                  'reason': 'because',
                  'node_hold_expiration': 36000})
        self.assertEqual(401, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            headers={'Authorization': 'Bearer %s' % token},
            json={'trigger': 'gerrit',
                  'change': '2,1',
                  'pipeline': 'check'})
        self.assertEqual(401, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            headers={'Authorization': 'Bearer %s' % token},
            json={'trigger': 'gerrit',
                  'ref': 'abcd',
                  'newrev': 'aaaa',
                  'oldrev': 'bbbb',
                  'pipeline': 'check'})
        self.assertEqual(401, resp.status_code)

    def test_expired_JWT_token(self):
        authz = {'iss': 'zuul_operator',
                 'sub': 'testuser',
                 'aud': 'zuul.example.com',
                 'zuul': {
                     'admin': ['tenant-one', ]
                 },
                 'exp': time.time() - 3600}
        token = jwt.encode(authz, key='NoDanaOnlyZuul',
                           algorithm='HS256').decode('utf-8')
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/autohold",
            headers={'Authorization': 'Bearer %s' % token},
            json={'job': 'project-test1',
                  'count': 1,
                  'reason': 'because',
                  'node_hold_expiration': 36000})
        self.assertEqual(401, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            headers={'Authorization': 'Bearer %s' % token},
            json={'trigger': 'gerrit',
                  'change': '2,1',
                  'pipeline': 'check'})
        self.assertEqual(401, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            headers={'Authorization': 'Bearer %s' % token},
            json={'trigger': 'gerrit',
                  'ref': 'abcd',
                  'newrev': 'aaaa',
                  'oldrev': 'bbbb',
                  'pipeline': 'check'})
        self.assertEqual(401, resp.status_code)

    def test_valid_JWT_bad_tenants(self):
        authz = {'iss': 'zuul_operator',
                 'sub': 'testuser',
                 'aud': 'zuul.example.com',
                 'zuul': {
                     'admin': ['tenant-six', 'tenant-ten', ]
                 },
                 'exp': time.time() + 3600}
        token = jwt.encode(authz, key='NoDanaOnlyZuul',
                           algorithm='HS256').decode('utf-8')
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/autohold",
            headers={'Authorization': 'Bearer %s' % token},
            json={'job': 'project-test1',
                  'count': 1,
                  'reason': 'because',
                  'node_hold_expiration': 36000})
        self.assertEqual(403, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            headers={'Authorization': 'Bearer %s' % token},
            json={'trigger': 'gerrit',
                  'change': '2,1',
                  'pipeline': 'check'})
        self.assertEqual(403, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            headers={'Authorization': 'Bearer %s' % token},
            json={'trigger': 'gerrit',
                  'ref': 'abcd',
                  'newrev': 'aaaa',
                  'oldrev': 'bbbb',
                  'pipeline': 'check'})
        self.assertEqual(403, resp.status_code)

        # For autohold-delete, we first must make sure that an autohold
        # exists before the delete attempt.
        good_authz = {'iss': 'zuul_operator',
                      'aud': 'zuul.example.com',
                      'sub': 'testuser',
                      'zuul': {'admin': ['tenant-one', ]},
                      'exp': time.time() + 3600}
        args = {"reason": "some reason",
                "count": 1,
                'job': 'project-test2',
                'change': None,
                'ref': None,
                'node_hold_expiration': None}
        good_token = jwt.encode(good_authz, key='NoDanaOnlyZuul',
                                algorithm='HS256').decode('utf-8')
        req = self.post_url(
            'api/tenant/tenant-one/project/org/project/autohold',
            headers={'Authorization': 'Bearer %s' % good_token},
            json=args)
        self.assertEqual(200, req.status_code, req.text)
        client = zuul.rpcclient.RPCClient('127.0.0.1',
                                          self.gearman_server.port)
        self.addCleanup(client.shutdown)
        autohold_requests = client.autohold_list()
        self.assertNotEqual([], autohold_requests)
        self.assertEqual(1, len(autohold_requests))
        request = autohold_requests[0]
        resp = self.delete_url(
            "api/tenant/tenant-one/autohold/%s" % request['id'],
            headers={'Authorization': 'Bearer %s' % token})
        self.assertEqual(403, resp.status_code)

    def test_autohold(self):
        """Test that autohold can be set through the admin web interface"""
        args = {"reason": "some reason",
                "count": 1,
                'job': 'project-test2',
                'change': None,
                'ref': None,
                'node_hold_expiration': None}
        authz = {'iss': 'zuul_operator',
                 'aud': 'zuul.example.com',
                 'sub': 'testuser',
                 'zuul': {
                     'admin': ['tenant-one', ]
                 },
                 'exp': time.time() + 3600}
        token = jwt.encode(authz, key='NoDanaOnlyZuul',
                           algorithm='HS256').decode('utf-8')
        req = self.post_url(
            'api/tenant/tenant-one/project/org/project/autohold',
            headers={'Authorization': 'Bearer %s' % token},
            json=args)
        self.assertEqual(200, req.status_code, req.text)
        data = req.json()
        self.assertEqual(True, data)

        # Check result in rpc client
        client = zuul.rpcclient.RPCClient('127.0.0.1',
                                          self.gearman_server.port)
        self.addCleanup(client.shutdown)
        autohold_requests = client.autohold_list()
        self.assertNotEqual([], autohold_requests)
        self.assertEqual(1, len(autohold_requests))
        request = autohold_requests[0]
        self.assertEqual('tenant-one', request['tenant'])
        self.assertIn('org/project', request['project'])
        self.assertEqual('project-test2', request['job'])
        self.assertEqual(".*", request['ref_filter'])
        self.assertEqual("some reason", request['reason'])
        self.assertEqual(1, request['max_count'])

    def test_autohold_delete(self):
        authz = {'iss': 'zuul_operator',
                 'aud': 'zuul.example.com',
                 'sub': 'testuser',
                 'zuul': {
                     'admin': ['tenant-one', ]
                 },
                 'exp': time.time() + 3600}
        token = jwt.encode(authz, key='NoDanaOnlyZuul',
                           algorithm='HS256').decode('utf-8')

        client = zuul.rpcclient.RPCClient('127.0.0.1',
                                          self.gearman_server.port)
        self.addCleanup(client.shutdown)
        r = client.autohold('tenant-one', 'org/project', 'project-test2',
                            "", "", "reason text", 1)
        self.assertTrue(r)

        # Use autohold-list API to retrieve request ID
        resp = self.get_url(
            "api/tenant/tenant-one/autohold")
        self.assertEqual(200, resp.status_code, resp.text)
        autohold_requests = resp.json()
        self.assertNotEqual([], autohold_requests)
        self.assertEqual(1, len(autohold_requests))
        request_id = autohold_requests[0]['id']

        # now try the autohold-delete API
        resp = self.delete_url(
            "api/tenant/tenant-one/autohold/%s" % request_id,
            headers={'Authorization': 'Bearer %s' % token})
        self.assertEqual(204, resp.status_code, resp.text)

        # autohold-list should be empty now
        resp = self.get_url(
            "api/tenant/tenant-one/autohold")
        self.assertEqual(200, resp.status_code, resp.text)
        autohold_requests = resp.json()
        self.assertEqual([], autohold_requests)

    def test_enqueue(self):
        """Test that the admin web interface can enqueue a change"""
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        A.addApproval('Code-Review', 2)
        A.addApproval('Approved', 1)

        authz = {'iss': 'zuul_operator',
                 'aud': 'zuul.example.com',
                 'sub': 'testuser',
                 'zuul': {
                     'admin': ['tenant-one', ]
                 },
                 'exp': time.time() + 3600}
        token = jwt.encode(authz, key='NoDanaOnlyZuul',
                           algorithm='HS256').decode('utf-8')
        path = "api/tenant/%(tenant)s/project/%(project)s/enqueue"
        enqueue_args = {'tenant': 'tenant-one',
                        'project': 'org/project', }
        change = {'trigger': 'gerrit',
                  'change': '1,1',
                  'pipeline': 'gate', }
        req = self.post_url(path % enqueue_args,
                            headers={'Authorization': 'Bearer %s' % token},
                            json=change)
        # The JSON returned is the same as the client's output
        self.assertEqual(200, req.status_code, req.text)
        data = req.json()
        self.assertEqual(True, data)
        self.waitUntilSettled()

    def test_enqueue_ref(self):
        """Test that the admin web interface can enqueue a ref"""
        p = "review.example.com/org/project"
        upstream = self.getUpstreamRepos([p])
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        A.setMerged()
        A_commit = str(upstream[p].commit('master'))
        self.log.debug("A commit: %s" % A_commit)

        path = "api/tenant/%(tenant)s/project/%(project)s/enqueue"
        enqueue_args = {'tenant': 'tenant-one',
                        'project': 'org/project', }
        ref = {'trigger': 'gerrit',
               'ref': 'master',
               'oldrev': '90f173846e3af9154517b88543ffbd1691f31366',
               'newrev': A_commit,
               'pipeline': 'post', }
        authz = {'iss': 'zuul_operator',
                 'aud': 'zuul.example.com',
                 'sub': 'testuser',
                 'zuul': {
                     'admin': ['tenant-one', ]
                 },
                 'exp': time.time() + 3600}
        token = jwt.encode(authz, key='NoDanaOnlyZuul',
                           algorithm='HS256').decode('utf-8')
        req = self.post_url(path % enqueue_args,
                            headers={'Authorization': 'Bearer %s' % token},
                            json=ref)
        self.assertEqual(200, req.status_code, req.text)
        # The JSON returned is the same as the client's output
        data = req.json()
        self.assertEqual(True, data)
        self.waitUntilSettled()

    def test_dequeue(self):
        """Test that the admin web interface can dequeue a change"""
        start_builds = len(self.builds)
        self.create_branch('org/project', 'stable')
        self.executor_server.hold_jobs_in_build = True
        self.commitConfigUpdate('common-config', 'layouts/timer.yaml')
        self.sched.reconfigure(self.config)
        self.waitUntilSettled()

        for _ in iterate_timeout(30, 'Wait for a build on hold'):
            if len(self.builds) > start_builds:
                break
        self.waitUntilSettled()

        authz = {'iss': 'zuul_operator',
                 'aud': 'zuul.example.com',
                 'sub': 'testuser',
                 'zuul': {
                     'admin': ['tenant-one', ]
                 },
                 'exp': time.time() + 3600}
        token = jwt.encode(authz, key='NoDanaOnlyZuul',
                           algorithm='HS256').decode('utf-8')
        path = "api/tenant/%(tenant)s/project/%(project)s/dequeue"
        dequeue_args = {'tenant': 'tenant-one',
                        'project': 'org/project', }
        change = {'ref': 'refs/heads/stable',
                  'pipeline': 'periodic', }
        req = self.post_url(path % dequeue_args,
                            headers={'Authorization': 'Bearer %s' % token},
                            json=change)
        # The JSON returned is the same as the client's output
        self.assertEqual(200, req.status_code, req.text)
        data = req.json()
        self.assertEqual(True, data)
        self.waitUntilSettled()

        self.commitConfigUpdate('common-config',
                                'layouts/no-timer.yaml')
        self.sched.reconfigure(self.config)
        self.waitUntilSettled()
        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()
        self.assertEqual(self.countJobResults(self.history, 'ABORTED'), 1)


class TestTenantScopedWebApiWithAuthRules(BaseTestWeb):
    config_file = 'zuul-admin-web-no-override.conf'
    tenant_config_file = 'config/authorization/single-tenant/main.yaml'

    def test_override_not_allowed(self):
        """Test that authz cannot be overriden if config does not allow it"""
        args = {"reason": "some reason",
                "count": 1,
                'job': 'project-test2',
                'change': None,
                'ref': None,
                'node_hold_expiration': None}
        authz = {'iss': 'zuul_operator',
                 'aud': 'zuul.example.com',
                 'sub': 'testuser',
                 'zuul': {
                     'admin': ['tenant-one', ],
                 },
                 'exp': time.time() + 3600}
        token = jwt.encode(authz, key='NoDanaOnlyZuul',
                           algorithm='HS256').decode('utf-8')
        req = self.post_url(
            'api/tenant/tenant-one/project/org/project/autohold',
            headers={'Authorization': 'Bearer %s' % token},
            json=args)
        self.assertEqual(401, req.status_code, req.text)

    def test_tenant_level_rule(self):
        """Test that authz rules defined at tenant level are checked"""
        path = "api/tenant/%(tenant)s/project/%(project)s/enqueue"

        def _test_project_enqueue_with_authz(i, project, authz, expected):
            f_ch = self.fake_gerrit.addFakeChange(project, 'master',
                                                  '%s %i' % (project, i))
            f_ch.addApproval('Code-Review', 2)
            f_ch.addApproval('Approved', 1)
            change = {'trigger': 'gerrit',
                      'change': '%i,1' % i,
                      'pipeline': 'gate', }
            enqueue_args = {'tenant': 'tenant-one',
                            'project': project, }

            token = jwt.encode(authz, key='NoDanaOnlyZuul',
                               algorithm='HS256').decode('utf-8')
            req = self.post_url(path % enqueue_args,
                                headers={'Authorization': 'Bearer %s' % token},
                                json=change)
            self.assertEqual(expected, req.status_code, req.text)
            self.waitUntilSettled()

        i = 0
        for p in ['org/project', 'org/project1', 'org/project2']:
            i += 1
            # Authorized sub
            authz = {'iss': 'zuul_operator',
                     'aud': 'zuul.example.com',
                     'sub': 'venkman',
                     'exp': time.time() + 3600}
            _test_project_enqueue_with_authz(i, p, authz, 200)
            i += 1
            # Unauthorized sub
            authz = {'iss': 'zuul_operator',
                     'aud': 'zuul.example.com',
                     'sub': 'vigo',
                     'exp': time.time() + 3600}
            _test_project_enqueue_with_authz(i, p, authz, 403)
            i += 1
            # unauthorized issuer
            authz = {'iss': 'columbia.edu',
                     'aud': 'zuul.example.com',
                     'sub': 'stantz',
                     'exp': time.time() + 3600}
            _test_project_enqueue_with_authz(i, p, authz, 401)
        self.waitUntilSettled()

    def test_group_rule(self):
        """Test a group rule"""
        A = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A')
        A.addApproval('Code-Review', 2)
        A.addApproval('Approved', 1)

        authz = {'iss': 'zuul_operator',
                 'aud': 'zuul.example.com',
                 'sub': 'melnitz',
                 'groups': ['ghostbusters', 'secretary'],
                 'exp': time.time() + 3600}
        token = jwt.encode(authz, key='NoDanaOnlyZuul',
                           algorithm='HS256').decode('utf-8')
        path = "api/tenant/%(tenant)s/project/%(project)s/enqueue"
        enqueue_args = {'tenant': 'tenant-one',
                        'project': 'org/project2', }
        change = {'trigger': 'gerrit',
                  'change': '1,1',
                  'pipeline': 'gate', }
        req = self.post_url(path % enqueue_args,
                            headers={'Authorization': 'Bearer %s' % token},
                            json=change)
        self.assertEqual(200, req.status_code, req.text)
        self.waitUntilSettled()

    def test_depth_claim_rule(self):
        """Test a rule based on a complex claim"""
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        A.addApproval('Code-Review', 2)
        A.addApproval('Approved', 1)

        authz = {'iss': 'zuul_operator',
                 'aud': 'zuul.example.com',
                 'sub': 'zeddemore',
                 'vehicle': {
                     'car': 'ecto-1'},
                 'exp': time.time() + 3600}
        token = jwt.encode(authz, key='NoDanaOnlyZuul',
                           algorithm='HS256').decode('utf-8')
        path = "api/tenant/%(tenant)s/project/%(project)s/enqueue"
        enqueue_args = {'tenant': 'tenant-one',
                        'project': 'org/project', }
        change = {'trigger': 'gerrit',
                  'change': '1,1',
                  'pipeline': 'gate', }
        req = self.post_url(path % enqueue_args,
                            headers={'Authorization': 'Bearer %s' % token},
                            json=change)
        self.assertEqual(200, req.status_code, req.text)
        self.waitUntilSettled()

    def test_user_actions_action_override(self):
        """Test that user with 'zuul.admin' claim does NOT get it back"""
        admin_tenants = ['tenant-zero', ]
        authz = {'iss': 'zuul_operator',
                 'aud': 'zuul.example.com',
                 'sub': 'testuser',
                 'zuul': {'admin': admin_tenants},
                 'exp': time.time() + 3600}
        token = jwt.encode(authz, key='NoDanaOnlyZuul',
                           algorithm='HS256').decode('utf-8')
        req = self.get_url('/api/user/authorizations',
                           headers={'Authorization': 'Bearer %s' % token})
        self.assertEqual(401, req.status_code, req.text)

    def test_user_actions(self):
        """Test that users get the right 'zuul.actions' trees"""
        users = [
            {'authz': {'iss': 'zuul_operator',
                       'aud': 'zuul.example.com',
                       'sub': 'vigo'},
             'zuul.admin': []},
            {'authz': {'iss': 'zuul_operator',
                       'aud': 'zuul.example.com',
                       'sub': 'venkman'},
             'zuul.admin': ['tenant-one', ]},
            {'authz': {'iss': 'zuul_operator',
                       'aud': 'zuul.example.com',
                       'sub': 'stantz'},
             'zuul.admin': []},
            {'authz': {'iss': 'zuul_operator',
                       'aud': 'zuul.example.com',
                       'sub': 'zeddemore',
                       'vehicle': {
                           'car': 'ecto-1'
                       }},
             'zuul.admin': ['tenant-one', ]},
            {'authz': {'iss': 'zuul_operator',
                       'aud': 'zuul.example.com',
                       'sub': 'melnitz',
                       'groups': ['secretary', 'ghostbusters']},
             'zuul.admin': ['tenant-one', ]},
        ]

        for test_user in users:
            authz = test_user['authz']
            authz['exp'] = time.time() + 3600
            token = jwt.encode(authz, key='NoDanaOnlyZuul',
                               algorithm='HS256').decode('utf-8')
            req = self.get_url('/api/user/authorizations',
                               headers={'Authorization': 'Bearer %s' % token})
            self.assertEqual(200, req.status_code, req.text)
            data = req.json()
            self.assertTrue('zuul' in data,
                            "%s got %s" % (authz['sub'], data))
            self.assertTrue('admin' in data['zuul'],
                            "%s got %s" % (authz['sub'], data))
            self.assertEqual(test_user['zuul.admin'],
                             data['zuul']['admin'],
                             "%s got %s" % (authz['sub'], data))


class TestTenantScopedWebApiTokenWithExpiry(BaseTestWeb):
    config_file = 'zuul-admin-web-token-expiry.conf'

    def test_iat_claim_mandatory(self):
        """Test that the 'iat' claim is mandatory when
        max_validity_time is set"""
        authz = {'iss': 'zuul_operator',
                 'sub': 'testuser',
                 'aud': 'zuul.example.com',
                 'zuul': {
                     'admin': ['tenant-one', ]
                 },
                 'exp': time.time() + 3600}
        token = jwt.encode(authz, key='NoDanaOnlyZuul',
                           algorithm='HS256').decode('utf-8')
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/autohold",
            headers={'Authorization': 'Bearer %s' % token},
            json={'job': 'project-test1',
                  'count': 1,
                  'reason': 'because',
                  'node_hold_expiration': 36000})
        self.assertEqual(401, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            headers={'Authorization': 'Bearer %s' % token},
            json={'trigger': 'gerrit',
                  'change': '2,1',
                  'pipeline': 'check'})
        self.assertEqual(401, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            headers={'Authorization': 'Bearer %s' % token},
            json={'trigger': 'gerrit',
                  'ref': 'abcd',
                  'newrev': 'aaaa',
                  'oldrev': 'bbbb',
                  'pipeline': 'check'})
        self.assertEqual(401, resp.status_code)

    def test_token_from_the_future(self):
        authz = {'iss': 'zuul_operator',
                 'sub': 'testuser',
                 'aud': 'zuul.example.com',
                 'zuul': {
                     'admin': ['tenant-one', ],
                 },
                 'exp': time.time() + 7200,
                 'iat': time.time() + 3600}
        token = jwt.encode(authz, key='NoDanaOnlyZuul',
                           algorithm='HS256').decode('utf-8')
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/autohold",
            headers={'Authorization': 'Bearer %s' % token},
            json={'job': 'project-test1',
                  'count': 1,
                  'reason': 'because',
                  'node_hold_expiration': 36000})
        self.assertEqual(401, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            headers={'Authorization': 'Bearer %s' % token},
            json={'trigger': 'gerrit',
                  'change': '2,1',
                  'pipeline': 'check'})
        self.assertEqual(401, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            headers={'Authorization': 'Bearer %s' % token},
            json={'trigger': 'gerrit',
                  'ref': 'abcd',
                  'newrev': 'aaaa',
                  'oldrev': 'bbbb',
                  'pipeline': 'check'})
        self.assertEqual(401, resp.status_code)

    def test_token_expired(self):
        authz = {'iss': 'zuul_operator',
                 'sub': 'testuser',
                 'aud': 'zuul.example.com',
                 'zuul': {
                     'admin': ['tenant-one', ],
                 },
                 'exp': time.time() + 3600,
                 'iat': time.time()}
        token = jwt.encode(authz, key='NoDanaOnlyZuul',
                           algorithm='HS256').decode('utf-8')
        time.sleep(10)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/autohold",
            headers={'Authorization': 'Bearer %s' % token},
            json={'job': 'project-test1',
                  'count': 1,
                  'reason': 'because',
                  'node_hold_expiration': 36000})
        self.assertEqual(401, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            headers={'Authorization': 'Bearer %s' % token},
            json={'trigger': 'gerrit',
                  'change': '2,1',
                  'pipeline': 'check'})
        self.assertEqual(401, resp.status_code)
        resp = self.post_url(
            "api/tenant/tenant-one/project/org/project/enqueue",
            headers={'Authorization': 'Bearer %s' % token},
            json={'trigger': 'gerrit',
                  'ref': 'abcd',
                  'newrev': 'aaaa',
                  'oldrev': 'bbbb',
                  'pipeline': 'check'})
        self.assertEqual(401, resp.status_code)

    def test_autohold(self):
        """Test that autohold can be set through the admin web interface"""
        args = {"reason": "some reason",
                "count": 1,
                'job': 'project-test2',
                'change': None,
                'ref': None,
                'node_hold_expiration': None}
        authz = {'iss': 'zuul_operator',
                 'aud': 'zuul.example.com',
                 'sub': 'testuser',
                 'zuul': {
                     'admin': ['tenant-one', ],
                 },
                 'exp': time.time() + 3600,
                 'iat': time.time()}
        token = jwt.encode(authz, key='NoDanaOnlyZuul',
                           algorithm='HS256').decode('utf-8')
        req = self.post_url(
            'api/tenant/tenant-one/project/org/project/autohold',
            headers={'Authorization': 'Bearer %s' % token},
            json=args)
        self.assertEqual(200, req.status_code, req.text)
        data = req.json()
        self.assertEqual(True, data)

        # Check result in rpc client
        client = zuul.rpcclient.RPCClient('127.0.0.1',
                                          self.gearman_server.port)
        self.addCleanup(client.shutdown)

        autohold_requests = client.autohold_list()
        self.assertNotEqual([], autohold_requests)
        self.assertEqual(1, len(autohold_requests))

        ah_request = autohold_requests[0]
        self.assertEqual('tenant-one', ah_request['tenant'])
        self.assertIn('org/project', ah_request['project'])
        self.assertEqual('project-test2', ah_request['job'])
        self.assertEqual(".*", ah_request['ref_filter'])
        self.assertEqual("some reason", ah_request['reason'])


class TestWebMulti(BaseTestWeb):
    config_file = 'zuul-gerrit-github.conf'

    def test_web_connections_list_multi(self):
        data = self.get_url('api/connections').json()
        gerrit_connection = {
            'driver': 'gerrit',
            'name': 'gerrit',
            'baseurl': 'https://review.example.com',
            'canonical_hostname': 'review.example.com',
            'server': 'review.example.com',
            'port': 29418,
        }
        github_connection = {
            'baseurl': 'https://api.github.com',
            'canonical_hostname': 'github.com',
            'driver': 'github',
            'name': 'github',
            'server': 'github.com',
        }
        self.assertEqual([gerrit_connection, github_connection], data)
