# Copyright 2012 Hewlett-Packard Development Company, L.P.
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

import io
import json
import logging
import os
import textwrap
import gc
import time
from unittest import skip

import paramiko

import zuul.configloader
from zuul.lib import encryption
from tests.base import (
    AnsibleZuulTestCase,
    ZuulTestCase,
    FIXTURE_DIR,
    simple_layout,
)


class TestMultipleTenants(AnsibleZuulTestCase):
    # A temporary class to hold new tests while others are disabled

    tenant_config_file = 'config/multi-tenant/main.yaml'

    def test_multiple_tenants(self):
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A')
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(self.getJobFromHistory('project1-test1').result,
                         'SUCCESS')
        self.assertEqual(self.getJobFromHistory('python27').result,
                         'SUCCESS')
        self.assertEqual(A.data['status'], 'MERGED')
        self.assertEqual(A.reported, 2,
                         "A should report start and success")
        self.assertIn('tenant-one-gate', A.messages[1],
                      "A should transit tenant-one gate")
        self.assertNotIn('tenant-two-gate', A.messages[1],
                         "A should *not* transit tenant-two gate")

        B = self.fake_gerrit.addFakeChange('org/project2', 'master', 'B')
        B.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(B.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(self.getJobFromHistory('python27',
                                                'org/project2').result,
                         'SUCCESS')
        self.assertEqual(self.getJobFromHistory('project2-test1').result,
                         'SUCCESS')
        self.assertEqual(B.data['status'], 'MERGED')
        self.assertEqual(B.reported, 2,
                         "B should report start and success")
        self.assertIn('tenant-two-gate', B.messages[1],
                      "B should transit tenant-two gate")
        self.assertNotIn('tenant-one-gate', B.messages[1],
                         "B should *not* transit tenant-one gate")

        self.assertEqual(A.reported, 2, "Activity in tenant two should"
                         "not affect tenant one")


class TestProtected(ZuulTestCase):
    tenant_config_file = 'config/protected/main.yaml'

    def test_protected_ok(self):
        # test clean usage of final parent job
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: job-protected
                protected: true
                run: playbooks/job-protected.yaml

            - project:
                name: org/project
                check:
                  jobs:
                    - job-child-ok

            - job:
                name: job-child-ok
                parent: job-protected

            - project:
                name: org/project
                check:
                  jobs:
                    - job-child-ok

            """)

        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(A.reported, 1)
        self.assertEqual(A.patchsets[-1]['approvals'][0]['value'], '1')

    def test_protected_reset(self):
        # try to reset protected flag
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: job-protected
                protected: true
                run: playbooks/job-protected.yaml

            - job:
                name: job-child-reset-protected
                parent: job-protected
                protected: false

            - project:
                name: org/project
                check:
                  jobs:
                    - job-child-reset-protected

            """)

        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        # The second patch tried to override some variables.
        # Thus it should fail.
        self.assertEqual(A.reported, 1)
        self.assertEqual(A.patchsets[-1]['approvals'][0]['value'], '-1')
        self.assertIn('Unable to reset protected attribute', A.messages[0])

    def test_protected_inherit_not_ok(self):
        # try to inherit from a protected job in different project
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: job-child-notok
                run: playbooks/job-child-notok.yaml
                parent: job-protected

            - project:
                name: org/project1
                check:
                  jobs:
                    - job-child-notok

            """)

        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(A.reported, 1)
        self.assertEqual(A.patchsets[-1]['approvals'][0]['value'], '-1')
        self.assertIn(
            "which is defined in review.example.com/org/project is protected "
            "and cannot be inherited from other projects.", A.messages[0])


class TestAbstract(ZuulTestCase):
    tenant_config_file = 'config/abstract/main.yaml'

    def test_abstract_fail(self):
        in_repo_conf = textwrap.dedent(
            """
            - project:
                check:
                  jobs:
                    - job-abstract
            """)

        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(A.reported, 1)
        self.assertEqual(A.patchsets[-1]['approvals'][0]['value'], '-1')
        self.assertIn('may not be directly run', A.messages[0])

    def test_child_of_abstract(self):
        in_repo_conf = textwrap.dedent(
            """
            - project:
                check:
                  jobs:
                    - job-child
            """)

        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(A.reported, 1)
        self.assertEqual(A.patchsets[-1]['approvals'][0]['value'], '1')


class TestFinal(ZuulTestCase):

    tenant_config_file = 'config/final/main.yaml'

    def test_final_variant_ok(self):
        # test clean usage of final parent job
        in_repo_conf = textwrap.dedent(
            """
            - project:
                name: org/project
                check:
                  jobs:
                    - job-final
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(A.reported, 1)
        self.assertEqual(A.patchsets[-1]['approvals'][0]['value'], '1')

    def test_final_variant_error(self):
        # test misuse of final parent job
        in_repo_conf = textwrap.dedent(
            """
            - project:
                name: org/project
                check:
                  jobs:
                    - job-final:
                        vars:
                          dont_override_this: bar
            """)
        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        # The second patch tried to override some variables.
        # Thus it should fail.
        self.assertEqual(A.reported, 1)
        self.assertEqual(A.patchsets[-1]['approvals'][0]['value'], '-1')
        self.assertIn('Unable to modify final job', A.messages[0])

    def test_final_inheritance(self):
        # test misuse of final parent job
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test
                parent: job-final
                run: playbooks/project-test.yaml

            - project:
                name: org/project
                check:
                  jobs:
                    - project-test
            """)

        in_repo_playbook = textwrap.dedent(
            """
            - hosts: all
              tasks: []
            """)

        file_dict = {'.zuul.yaml': in_repo_conf,
                     'playbooks/project-test.yaml': in_repo_playbook}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        # The second patch tried to override some variables.
        # Thus it should fail.
        self.assertEqual(A.reported, 1)
        self.assertEqual(A.patchsets[-1]['approvals'][0]['value'], '-1')
        self.assertIn('Unable to modify final job', A.messages[0])


class TestBranchDeletion(ZuulTestCase):
    tenant_config_file = 'config/branch-deletion/main.yaml'

    def test_branch_delete(self):
        # This tests a tenant reconfiguration on deleting a branch
        # *after* an earlier failed tenant reconfiguration.  This
        # ensures that cached data are appropriately removed, even if
        # we are recovering from an invalid config.
        self.create_branch('org/project', 'stable/queens')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project', 'stable/queens'))
        self.waitUntilSettled()

        in_repo_conf = textwrap.dedent(
            """
            - project:
                check:
                  jobs:
                    - nonexistent-job
            """)

        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'stable/queens', 'A',
                                           files=file_dict)
        A.setMerged()
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()

        self.delete_branch('org/project', 'stable/queens')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchDeletedEvent(
                'org/project', 'stable/queens'))
        self.waitUntilSettled()

        in_repo_conf = textwrap.dedent(
            """
            - project:
                check:
                  jobs:
                    - base
            """)

        file_dict = {'zuul.yaml': in_repo_conf}
        B = self.fake_gerrit.addFakeChange('org/project1', 'master', 'B',
                                           files=file_dict)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(B.reported, 1)
        self.assertHistory([
            dict(name='base', result='SUCCESS', changes='2,1')])

    def test_branch_delete_full_reconfiguration(self):
        # This tests a full configuration after deleting a branch
        # *after* an earlier failed tenant reconfiguration.  This
        # ensures that cached data are appropriately removed, even if
        # we are recovering from an invalid config.
        self.create_branch('org/project', 'stable/queens')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project', 'stable/queens'))
        self.waitUntilSettled()

        in_repo_conf = textwrap.dedent(
            """
            - project:
                check:
                  jobs:
                    - nonexistent-job
            """)

        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'stable/queens', 'A',
                                           files=file_dict)
        A.setMerged()
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()

        self.delete_branch('org/project', 'stable/queens')
        self.sched.reconfigure(self.config)
        self.waitUntilSettled()

        in_repo_conf = textwrap.dedent(
            """
            - project:
                check:
                  jobs:
                    - base
            """)

        file_dict = {'zuul.yaml': in_repo_conf}
        B = self.fake_gerrit.addFakeChange('org/project1', 'master', 'B',
                                           files=file_dict)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(B.reported, 1)
        self.assertHistory([
            dict(name='base', result='SUCCESS', changes='2,1')])


class TestBranchTag(ZuulTestCase):
    tenant_config_file = 'config/branch-tag/main.yaml'

    def test_no_branch_match(self):
        # Test that tag jobs run with no explicit branch matchers
        event = self.fake_gerrit.addFakeTag('org/project', 'master', 'foo')
        self.fake_gerrit.addEvent(event)
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='central-job', result='SUCCESS', ref='refs/tags/foo'),
            dict(name='test-job', result='SUCCESS', ref='refs/tags/foo')],
            ordered=False)

    def test_no_branch_match_multi_branch(self):
        # Test that tag jobs run with no explicit branch matchers in a
        # multi-branch project (where jobs generally get implied
        # branch matchers)
        self.create_branch('org/project', 'stable/pike')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project', 'stable/pike'))
        self.waitUntilSettled()

        event = self.fake_gerrit.addFakeTag('org/project', 'master', 'foo')
        self.fake_gerrit.addEvent(event)
        self.waitUntilSettled()
        # test-job does not run in this case because it is defined in
        # a branched repo with implied branch matchers.  A release job
        # defined in a multi-branch repo would need at least one
        # top-level variant with no branch matcher in order to match a
        # tag.
        self.assertHistory([
            dict(name='central-job', result='SUCCESS', ref='refs/tags/foo')])


class TestBranchNegative(ZuulTestCase):
    tenant_config_file = 'config/branch-negative/main.yaml'

    def test_negative_branch_match(self):
        # Test that a negative branch matcher works with implied branches.
        self.create_branch('org/project', 'stable/pike')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project', 'stable/pike'))
        self.waitUntilSettled()

        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        B = self.fake_gerrit.addFakeChange('org/project', 'stable/pike', 'A')
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='test-job', result='SUCCESS', changes='1,1')])


class TestBranchTemplates(ZuulTestCase):
    tenant_config_file = 'config/branch-templates/main.yaml'

    def test_template_removal_from_branch(self):
        # Test that a template can be removed from one branch but not
        # another.
        # This creates a new branch with a copy of the config in master
        self.create_branch('puppet-integration', 'stable/newton')
        self.create_branch('puppet-integration', 'stable/ocata')
        self.create_branch('puppet-tripleo', 'stable/newton')
        self.create_branch('puppet-tripleo', 'stable/ocata')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'puppet-integration', 'stable/newton'))
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'puppet-integration', 'stable/ocata'))
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'puppet-tripleo', 'stable/newton'))
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'puppet-tripleo', 'stable/ocata'))
        self.waitUntilSettled()

        in_repo_conf = textwrap.dedent(
            """
            - project:
                name: puppet-tripleo
                check:
                  jobs:
                    - puppet-something
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('puppet-tripleo', 'stable/newton',
                                           'A', files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='puppet-something', result='SUCCESS', changes='1,1')])

    def test_template_change_on_branch(self):
        # Test that the contents of a template can be changed on one
        # branch without affecting another.

        # This creates a new branch with a copy of the config in master
        self.create_branch('puppet-integration', 'stable/newton')
        self.create_branch('puppet-integration', 'stable/ocata')
        self.create_branch('puppet-tripleo', 'stable/newton')
        self.create_branch('puppet-tripleo', 'stable/ocata')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'puppet-integration', 'stable/newton'))
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'puppet-integration', 'stable/ocata'))
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'puppet-tripleo', 'stable/newton'))
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'puppet-tripleo', 'stable/ocata'))
        self.waitUntilSettled()

        in_repo_conf = textwrap.dedent("""
            - job:
                name: puppet-unit-base
                run: playbooks/run-unit-tests.yaml

            - job:
                name: puppet-unit-3.8
                parent: puppet-unit-base
                branches: ^(stable/(newton|ocata)).*$
                vars:
                  puppet_gem_version: 3.8

            - job:
                name: puppet-something
                run: playbooks/run-unit-tests.yaml

            - project-template:
                name: puppet-unit
                check:
                  jobs:
                    - puppet-something

            - project:
                name: puppet-integration
                templates:
                  - puppet-unit
        """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('puppet-integration',
                                           'stable/newton',
                                           'A', files=file_dict)
        B = self.fake_gerrit.addFakeChange('puppet-tripleo',
                                           'stable/newton',
                                           'B')
        B.data['commitMessage'] = '%s\n\nDepends-On: %s\n' % (
            B.subject, A.data['id'])
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='puppet-something', result='SUCCESS',
                 changes='1,1 2,1')])


class TestBranchVariants(ZuulTestCase):
    tenant_config_file = 'config/branch-variants/main.yaml'

    def test_branch_variants(self):
        # Test branch variants of jobs with inheritance
        self.executor_server.hold_jobs_in_build = True
        # This creates a new branch with a copy of the config in master
        self.create_branch('puppet-integration', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'puppet-integration', 'stable'))
        self.waitUntilSettled()

        A = self.fake_gerrit.addFakeChange('puppet-integration', 'stable', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(len(self.builds[0].parameters['pre_playbooks']), 3)
        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

    def test_branch_variants_reconfigure(self):
        # Test branch variants of jobs with inheritance
        self.executor_server.hold_jobs_in_build = True
        # This creates a new branch with a copy of the config in master
        self.create_branch('puppet-integration', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'puppet-integration', 'stable'))
        self.waitUntilSettled()

        with open(os.path.join(FIXTURE_DIR,
                               'config/branch-variants/git/',
                               'puppet-integration/.zuul.yaml')) as f:
            config = f.read()

        # Push a change that triggers a dynamic reconfiguration
        file_dict = {'.zuul.yaml': config}
        A = self.fake_gerrit.addFakeChange('puppet-integration', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        ipath = self.builds[0].parameters['zuul']['_inheritance_path']
        for i in ipath:
            self.log.debug("inheritance path %s", i)
        self.assertEqual(len(ipath), 5)
        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

    def test_branch_variants_divergent(self):
        # Test branches can diverge and become independent
        self.executor_server.hold_jobs_in_build = True
        # This creates a new branch with a copy of the config in master
        self.create_branch('puppet-integration', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'puppet-integration', 'stable'))
        self.waitUntilSettled()

        with open(os.path.join(FIXTURE_DIR,
                               'config/branch-variants/git/',
                               'puppet-integration/stable.zuul.yaml')) as f:
            config = f.read()

        file_dict = {'.zuul.yaml': config}
        C = self.fake_gerrit.addFakeChange('puppet-integration', 'stable', 'C',
                                           files=file_dict)
        C.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(C.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.fake_gerrit.addEvent(C.getChangeMergedEvent())
        self.waitUntilSettled()

        A = self.fake_gerrit.addFakeChange('puppet-integration', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        B = self.fake_gerrit.addFakeChange('puppet-integration', 'stable', 'B')
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(self.builds[0].parameters['zuul']['jobtags'],
                         ['master'])

        self.assertEqual(self.builds[1].parameters['zuul']['jobtags'],
                         ['stable'])

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()


class TestBranchMismatch(ZuulTestCase):
    tenant_config_file = 'config/branch-mismatch/main.yaml'

    def test_job_override_branch(self):
        "Test that override-checkout overrides branch matchers as well"

        # Make sure the parent job repo is branched, so it gets
        # implied branch matchers.
        self.create_branch('org/project1', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project1', 'stable'))

        # The child job repo should have a branch which does not exist
        # in the parent job repo.
        self.create_branch('org/project2', 'devel')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project2', 'devel'))

        # A job in a repo with a weird branch name should use the
        # parent job from the parent job's master (default) branch.
        A = self.fake_gerrit.addFakeChange('org/project2', 'devel', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        # project-test2 should run because it inherits from
        # project-test1 and we will use the fallback branch to find
        # project-test1 variants, but project-test1 itself, even
        # though it is in the project-pipeline config, should not run
        # because it doesn't directly match.
        self.assertHistory([
            dict(name='project-test1', result='SUCCESS', changes='1,1'),
            dict(name='project-test2', result='SUCCESS', changes='1,1'),
        ], ordered=False)


class TestAllowedProjects(ZuulTestCase):
    tenant_config_file = 'config/allowed-projects/main.yaml'

    def test_allowed_projects(self):
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(A.reported, 1)
        self.assertIn('Build succeeded', A.messages[0])

        B = self.fake_gerrit.addFakeChange('org/project2', 'master', 'B')
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(B.reported, 1)
        self.assertIn('Project org/project2 is not allowed '
                      'to run job test-project2', B.messages[0])

        C = self.fake_gerrit.addFakeChange('org/project3', 'master', 'C')
        self.fake_gerrit.addEvent(C.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(C.reported, 1)
        self.assertIn('Project org/project3 is not allowed '
                      'to run job restricted-job', C.messages[0])

        self.assertHistory([
            dict(name='test-project1', result='SUCCESS', changes='1,1'),
            dict(name='restricted-job', result='SUCCESS', changes='1,1'),
        ], ordered=False)

    def test_allowed_projects_dynamic_config(self):
        # It is possible to circumvent allowed-projects with a
        # depends-on.
        in_repo_conf2 = textwrap.dedent(
            """
            - job:
                name: test-project2b
                parent: restricted-job
                allowed-projects:
                  - org/project1
            """)
        in_repo_conf1 = textwrap.dedent(
            """
            - project:
                check:
                  jobs:
                    - test-project2b
            """)

        file_dict = {'zuul.yaml': in_repo_conf2}
        A = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A',
                                           files=file_dict)
        file_dict = {'zuul.yaml': in_repo_conf1}
        B = self.fake_gerrit.addFakeChange('org/project1', 'master', 'B',
                                           files=file_dict)
        B.data['commitMessage'] = '%s\n\nDepends-On: %s\n' % (
            B.subject, A.data['id'])
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='test-project2b', result='SUCCESS', changes='1,1 2,1'),
        ], ordered=False)

    def test_allowed_projects_dynamic_config_secret(self):
        # It is not possible to circumvent allowed-projects with a
        # depends-on if there is a secret involved.
        in_repo_conf2 = textwrap.dedent(
            """
            - secret:
                name: project2_secret
                data: {}
            - job:
                name: test-project2b
                parent: restricted-job
                secrets: project2_secret
                allowed-projects:
                  - org/project1
            """)
        in_repo_conf1 = textwrap.dedent(
            """
            - project:
                check:
                  jobs:
                    - test-project2b
            """)

        file_dict = {'zuul.yaml': in_repo_conf2}
        A = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A',
                                           files=file_dict)
        file_dict = {'zuul.yaml': in_repo_conf1}
        B = self.fake_gerrit.addFakeChange('org/project1', 'master', 'B',
                                           files=file_dict)
        B.data['commitMessage'] = '%s\n\nDepends-On: %s\n' % (
            B.subject, A.data['id'])
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([])
        self.assertEqual(B.reported, 1)
        self.assertIn('Project org/project1 is not allowed '
                      'to run job test-project2b', B.messages[0])


class TestCentralJobs(ZuulTestCase):
    tenant_config_file = 'config/central-jobs/main.yaml'

    def setUp(self):
        super(TestCentralJobs, self).setUp()
        self.create_branch('org/project', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project', 'stable'))
        self.waitUntilSettled()

    def _updateConfig(self, config, branch):
        file_dict = {'.zuul.yaml': config}
        C = self.fake_gerrit.addFakeChange('org/project', branch, 'C',
                                           files=file_dict)
        C.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(C.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.fake_gerrit.addEvent(C.getChangeMergedEvent())
        self.waitUntilSettled()

    def _test_central_job_on_branch(self, branch, other_branch):
        # Test that a job defined on a branchless repo only runs on
        # the branch applied
        config = textwrap.dedent(
            """
            - project:
                name: org/project
                check:
                  jobs:
                    - central-job
            """)
        self._updateConfig(config, branch)

        A = self.fake_gerrit.addFakeChange('org/project', branch, 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertHistory([
            dict(name='central-job', result='SUCCESS', changes='2,1')])

        # No jobs should run for this change.
        B = self.fake_gerrit.addFakeChange('org/project', other_branch, 'B')
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertHistory([
            dict(name='central-job', result='SUCCESS', changes='2,1')])

    def test_central_job_on_stable(self):
        self._test_central_job_on_branch('master', 'stable')

    def test_central_job_on_master(self):
        self._test_central_job_on_branch('stable', 'master')

    def _test_central_template_on_branch(self, branch, other_branch):
        # Test that a project-template defined on a branchless repo
        # only runs on the branch applied
        config = textwrap.dedent(
            """
            - project:
                name: org/project
                templates: ['central-jobs']
            """)
        self._updateConfig(config, branch)

        A = self.fake_gerrit.addFakeChange('org/project', branch, 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertHistory([
            dict(name='central-job', result='SUCCESS', changes='2,1')])

        # No jobs should run for this change.
        B = self.fake_gerrit.addFakeChange('org/project', other_branch, 'B')
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertHistory([
            dict(name='central-job', result='SUCCESS', changes='2,1')])

    def test_central_template_on_stable(self):
        self._test_central_template_on_branch('master', 'stable')

    def test_central_template_on_master(self):
        self._test_central_template_on_branch('stable', 'master')


class TestInRepoConfig(ZuulTestCase):
    # A temporary class to hold new tests while others are disabled

    config_file = 'zuul-connections-gerrit-and-github.conf'
    tenant_config_file = 'config/in-repo/main.yaml'

    def test_in_repo_config(self):
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(self.getJobFromHistory('project-test1').result,
                         'SUCCESS')
        self.assertEqual(A.data['status'], 'MERGED')
        self.assertEqual(A.reported, 2,
                         "A should report start and success")
        self.assertIn('tenant-one-gate', A.messages[1],
                      "A should transit tenant-one gate")

    @skip("This test is useful, but not reliable")
    def test_full_and_dynamic_reconfig(self):
        self.executor_server.hold_jobs_in_build = True
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1

            - project:
                name: org/project
                tenant-one-gate:
                  jobs:
                    - project-test1
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.sched.reconfigure(self.config)
        self.waitUntilSettled()

        gc.collect()
        pipelines = [obj for obj in gc.get_objects()
                     if isinstance(obj, zuul.model.Pipeline)]
        self.assertEqual(len(pipelines), 4)

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

    def test_dynamic_config(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1

            - job:
                name: project-test2
                run: playbooks/project-test2.yaml

            - job:
                name: project-test3
                run: playbooks/project-test2.yaml

            # add a job by the short project name
            - project:
                name: org/project
                tenant-one-gate:
                  jobs:
                    - project-test2

            # add a job by the canonical project name
            - project:
                name: review.example.com/org/project
                tenant-one-gate:
                  jobs:
                    - project-test3
            """)

        in_repo_playbook = textwrap.dedent(
            """
            - hosts: all
              tasks: []
            """)

        file_dict = {'.zuul.yaml': in_repo_conf,
                     'playbooks/project-test2.yaml': in_repo_playbook}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(A.data['status'], 'MERGED')
        self.assertEqual(A.reported, 2,
                         "A should report start and success")
        self.assertIn('tenant-one-gate', A.messages[1],
                      "A should transit tenant-one gate")
        self.assertHistory([
            dict(name='project-test2', result='SUCCESS', changes='1,1'),
            dict(name='project-test3', result='SUCCESS', changes='1,1'),
        ], ordered=False)

        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()

        # Now that the config change is landed, it should be live for
        # subsequent changes.
        B = self.fake_gerrit.addFakeChange('org/project', 'master', 'B')
        B.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(B.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(self.getJobFromHistory('project-test2').result,
                         'SUCCESS')
        self.assertHistory([
            dict(name='project-test2', result='SUCCESS', changes='1,1'),
            dict(name='project-test3', result='SUCCESS', changes='1,1'),
            dict(name='project-test2', result='SUCCESS', changes='2,1'),
            dict(name='project-test3', result='SUCCESS', changes='2,1'),
        ], ordered=False)

    def test_dynamic_template(self):
        # Tests that a project can't update a template in another
        # project.
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1

            - project-template:
                name: common-config-template
                check:
                  jobs:
                    - project-test1

            - project:
                name: org/project
                templates: [common-config-template]
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(A.patchsets[0]['approvals'][0]['value'], "-1")
        self.assertIn('Project template common-config-template '
                      'is already defined',
                      A.messages[0],
                      "A should have failed the check pipeline")

    def test_dynamic_config_errors_not_accumulated(self):
        """Test that requesting broken dynamic configs
        does not appear in tenant layout error accumulator"""
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1

            - project:
                name: org/project
                check:
                  jobs:
                    - non-existent-job
            """)

        in_repo_playbook = textwrap.dedent(
            """
            - hosts: all
              tasks: []
            """)

        file_dict = {'.zuul.yaml': in_repo_conf,
                     'playbooks/project-test2.yaml': in_repo_playbook}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        tenant = self.sched.abide.tenants.get('tenant-one')
        self.assertEquals(
            len(tenant.layout.loading_errors), 0,
            "No error should have been accumulated")
        self.assertHistory([])

    def test_dynamic_config_non_existing_job(self):
        """Test that requesting a non existent job fails"""
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1

            - project:
                name: org/project
                check:
                  jobs:
                    - non-existent-job
            """)

        in_repo_playbook = textwrap.dedent(
            """
            - hosts: all
              tasks: []
            """)

        file_dict = {'.zuul.yaml': in_repo_conf,
                     'playbooks/project-test2.yaml': in_repo_playbook}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertEqual(A.patchsets[0]['approvals'][0]['value'], "-1")
        self.assertIn('Job non-existent-job not defined', A.messages[0],
                      "A should have failed the check pipeline")
        self.assertHistory([])
        self.assertEqual(len(A.comments), 1)
        comments = sorted(A.comments, key=lambda x: x['line'])
        self.assertEqual(comments[0],
                         {'file': '.zuul.yaml',
                          'line': 9,
                          'message': 'Job non-existent-job not defined',
                          'reviewer': {'email': 'zuul@example.com',
                                       'name': 'Zuul',
                                       'username': 'jenkins'},
                          'range': {'end_character': 0,
                                    'end_line': 9,
                                    'start_character': 2,
                                    'start_line': 5},
                          })

    def test_dynamic_config_non_existing_job_in_template(self):
        """Test that requesting a non existent job fails"""
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1

            - project-template:
                name: test-template
                check:
                  jobs:
                    - non-existent-job

            - project:
                name: org/project
                templates:
                  - test-template
            """)

        in_repo_playbook = textwrap.dedent(
            """
            - hosts: all
              tasks: []
            """)

        file_dict = {'.zuul.yaml': in_repo_conf,
                     'playbooks/project-test2.yaml': in_repo_playbook}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertEqual(A.patchsets[0]['approvals'][0]['value'], "-1")
        self.assertIn('Job non-existent-job not defined', A.messages[0],
                      "A should have failed the check pipeline")
        self.assertHistory([])

    def test_dynamic_config_new_patchset(self):
        self.executor_server.hold_jobs_in_build = True

        tenant = self.sched.abide.tenants.get('tenant-one')
        check_pipeline = tenant.layout.pipelines['check']

        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1
                run: playbooks/project-test1.yaml

            - job:
                name: project-test2
                run: playbooks/project-test2.yaml

            - project:
                name: org/project
                check:
                  jobs:
                    - project-test2
            """)

        in_repo_playbook = textwrap.dedent(
            """
            - hosts: all
              tasks: []
            """)

        file_dict = {'.zuul.yaml': in_repo_conf,
                     'playbooks/project-test2.yaml': in_repo_playbook}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        items = check_pipeline.getAllItems()
        self.assertEqual(items[0].change.number, '1')
        self.assertEqual(items[0].change.patchset, '1')
        self.assertTrue(items[0].live)

        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1
                run: playbooks/project-test1.yaml

            - job:
                name: project-test2
                run: playbooks/project-test2.yaml

            - project:
                name: org/project
                check:
                  jobs:
                    - project-test1
                    - project-test2
            """)
        file_dict = {'.zuul.yaml': in_repo_conf,
                     'playbooks/project-test2.yaml': in_repo_playbook}

        A.addPatchset(files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(2))

        self.waitUntilSettled()

        items = check_pipeline.getAllItems()
        self.assertEqual(items[0].change.number, '1')
        self.assertEqual(items[0].change.patchset, '2')
        self.assertTrue(items[0].live)

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release('project-test1')
        self.waitUntilSettled()
        self.executor_server.release()
        self.waitUntilSettled()

        self.assertHistory([
            dict(name='project-test2', result='ABORTED', changes='1,1'),
            dict(name='project-test1', result='SUCCESS', changes='1,2'),
            dict(name='project-test2', result='SUCCESS', changes='1,2')])

    def test_in_repo_branch(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1

            - job:
                name: project-test2
                run: playbooks/project-test2.yaml

            - project:
                name: org/project
                tenant-one-gate:
                  jobs:
                    - project-test2
            """)

        in_repo_playbook = textwrap.dedent(
            """
            - hosts: all
              tasks: []
            """)

        file_dict = {'.zuul.yaml': in_repo_conf,
                     'playbooks/project-test2.yaml': in_repo_playbook}
        self.create_branch('org/project', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project', 'stable'))
        self.waitUntilSettled()
        A = self.fake_gerrit.addFakeChange('org/project', 'stable', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(A.data['status'], 'MERGED')
        self.assertEqual(A.reported, 2,
                         "A should report start and success")
        self.assertIn('tenant-one-gate', A.messages[1],
                      "A should transit tenant-one gate")
        self.assertHistory([
            dict(name='project-test2', result='SUCCESS', changes='1,1')])
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()

        # The config change should not affect master.
        B = self.fake_gerrit.addFakeChange('org/project', 'master', 'B')
        B.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(B.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='project-test2', result='SUCCESS', changes='1,1'),
            dict(name='project-test1', result='SUCCESS', changes='2,1')])

        # The config change should be live for further changes on
        # stable.
        C = self.fake_gerrit.addFakeChange('org/project', 'stable', 'C')
        C.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(C.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='project-test2', result='SUCCESS', changes='1,1'),
            dict(name='project-test1', result='SUCCESS', changes='2,1'),
            dict(name='project-test2', result='SUCCESS', changes='3,1')])

    def test_crd_dynamic_config_branch(self):
        # Test that we can create a job in one repo and be able to use
        # it from a different branch on a different repo.

        self.create_branch('org/project1', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project1', 'stable'))

        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1

            - job:
                name: project-test2
                run: playbooks/project-test2.yaml

            - project:
                name: org/project
                check:
                  jobs:
                    - project-test2
            """)

        in_repo_playbook = textwrap.dedent(
            """
            - hosts: all
              tasks: []
            """)

        file_dict = {'.zuul.yaml': in_repo_conf,
                     'playbooks/project-test2.yaml': in_repo_playbook}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)

        second_repo_conf = textwrap.dedent(
            """
            - project:
                name: org/project1
                check:
                  jobs:
                    - project-test2
            """)

        second_file_dict = {'.zuul.yaml': second_repo_conf}
        B = self.fake_gerrit.addFakeChange('org/project1', 'stable', 'B',
                                           files=second_file_dict)
        B.data['commitMessage'] = '%s\n\nDepends-On: %s\n' % (
            B.subject, A.data['id'])

        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(A.reported, 1, "A should report")
        self.assertHistory([
            dict(name='project-test2', result='SUCCESS', changes='1,1'),
            dict(name='project-test2', result='SUCCESS', changes='1,1 2,1'),
        ])

    def test_yaml_list_error(self):
        in_repo_conf = textwrap.dedent(
            """
            job: foo
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('not a list', A.messages[0],
                      "A should have a syntax error reported")

    def test_yaml_dict_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - job
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('not a dictionary', A.messages[0],
                      "A should have a syntax error reported")

    def test_yaml_duplicate_key_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: foo
                name: bar
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('appears more than once', A.messages[0],
                      "A should have a syntax error reported")

    def test_yaml_key_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
              name: project-test2
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('has more than one key', A.messages[0],
                      "A should have a syntax error reported")

    def test_yaml_unknown_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - foobar:
                foo: bar
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('not recognized', A.messages[0],
                      "A should have a syntax error reported")

    def test_untrusted_syntax_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test2
                foo: error
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('syntax error', A.messages[0],
                      "A should have a syntax error reported")

    def test_trusted_syntax_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test2
                foo: error
            """)

        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('common-config', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('syntax error', A.messages[0],
                      "A should have a syntax error reported")

    def test_untrusted_yaml_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
            foo: error
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('syntax error', A.messages[0],
                      "A should have a syntax error reported")

    def test_untrusted_shadow_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: common-config-test
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('not permitted to shadow', A.messages[0],
                      "A should have a syntax error reported")

    def test_untrusted_pipeline_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - pipeline:
                name: test
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('Pipelines may not be defined', A.messages[0],
                      "A should have a syntax error reported")

    def test_untrusted_project_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - project:
                name: org/project1
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('the only project definition permitted', A.messages[0],
                      "A should have a syntax error reported")

    def test_untrusted_depends_on_trusted(self):
        with open(os.path.join(FIXTURE_DIR,
                               'config/in-repo/git/',
                               'common-config/zuul.yaml')) as f:
            common_config = f.read()

        common_config += textwrap.dedent(
            """
            - job:
                name: project-test9
            """)

        file_dict = {'zuul.yaml': common_config}
        A = self.fake_gerrit.addFakeChange('common-config', 'master', 'A',
                                           files=file_dict)
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1
            - project:
                name: org/project
                check:
                  jobs:
                    - project-test9
            """)

        file_dict = {'zuul.yaml': in_repo_conf}
        B = self.fake_gerrit.addFakeChange('org/project', 'master', 'B',
                                           files=file_dict)
        B.data['commitMessage'] = '%s\n\nDepends-On: %s\n' % (
            B.subject, A.data['id'])
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(B.data['status'], 'NEW')
        self.assertEqual(B.reported, 1,
                         "B should report failure")
        self.assertIn('depends on a change to a config project',
                      B.messages[0],
                      "A should have a syntax error reported")

    def test_duplicate_node_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - nodeset:
                name: duplicate
                nodes:
                  - name: compute
                    label: foo
                  - name: compute
                    label: foo
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('appears multiple times', A.messages[0],
                      "A should have a syntax error reported")

    def test_duplicate_group_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - nodeset:
                name: duplicate
                nodes:
                  - name: compute
                    label: foo
                groups:
                  - name: group
                    nodes: compute
                  - name: group
                    nodes: compute
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('appears multiple times', A.messages[0],
                      "A should have a syntax error reported")

    def test_secret_not_found_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1
                run: playbooks/project-test1.yaml
                secrets: does-not-exist
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('secret "does-not-exist" was not found', A.messages[0],
                      "A should have a syntax error reported")

    def test_nodeset_not_found_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: test
                nodeset: does-not-exist
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('nodeset "does-not-exist" was not found', A.messages[0],
                      "A should have a syntax error reported")

    def test_required_project_not_found_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1
            - job:
                name: test
                required-projects:
                  - does-not-exist
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('Unknown project does-not-exist', A.messages[0],
                      "A should have a syntax error reported")

    def test_template_not_found_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1
            - project:
                name: org/project
                templates:
                  - does-not-exist
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('project template "does-not-exist" was not found',
                      A.messages[0],
                      "A should have a syntax error reported")

    def test_job_list_in_project_template_not_dict_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1
            - project-template:
                name: some-jobs
                check:
                  jobs:
                    - project-test1:
                        - required-projects:
                            org/project2
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('expected str for dictionary value',
                      A.messages[0], "A should have a syntax error reported")

    def test_job_list_in_project_not_dict_error(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1
            - project:
                name: org/project1
                check:
                  jobs:
                    - project-test1:
                        - required-projects:
                            org/project2
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('expected str for dictionary value',
                      A.messages[0], "A should have a syntax error reported")

    def test_project_template(self):
        # Tests that a project template is not modified when used, and
        # can therefore be used in subsequent reconfigurations.
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1
                run: playbooks/project-test1.yaml
            - project-template:
                name: some-jobs
                tenant-one-gate:
                  jobs:
                    - project-test1:
                        required-projects:
                          - org/project1
            - project:
                name: org/project
                templates:
                  - some-jobs
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(A.data['status'], 'MERGED')
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()
        in_repo_conf = textwrap.dedent(
            """
            - project:
                name: org/project1
                templates:
                  - some-jobs
            """)
        file_dict = {'.zuul.yaml': in_repo_conf}
        B = self.fake_gerrit.addFakeChange('org/project1', 'master', 'B',
                                           files=file_dict)
        B.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(B.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(B.data['status'], 'MERGED')

    def test_job_remove_add(self):
        # Tests that a job can be removed from one repo and added in another.
        # First, remove the current config for project1 since it
        # references the job we want to remove.
        file_dict = {'.zuul.yaml': None}
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A',
                                           files=file_dict)
        A.setMerged()
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()
        # Then propose a change to delete the job from one repo...
        file_dict = {'.zuul.yaml': None}
        B = self.fake_gerrit.addFakeChange('org/project', 'master', 'B',
                                           files=file_dict)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        # ...and a second that depends on it that adds it to another repo.
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1
                run: playbooks/project-test1.yaml

            - project:
                name: org/project1
                check:
                  jobs:
                    - project-test1
            """)
        in_repo_playbook = textwrap.dedent(
            """
            - hosts: all
              tasks: []
            """)
        file_dict = {'.zuul.yaml': in_repo_conf,
                     'playbooks/project-test1.yaml': in_repo_playbook}
        C = self.fake_gerrit.addFakeChange('org/project1', 'master', 'C',
                                           files=file_dict,
                                           parent='refs/changes/1/1/1')
        C.data['commitMessage'] = '%s\n\nDepends-On: %s\n' % (
            C.subject, B.data['id'])
        self.fake_gerrit.addEvent(C.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='project-test1', result='SUCCESS', changes='2,1 3,1'),
        ], ordered=False)

    def test_multi_repo(self):
        downstream_repo_conf = textwrap.dedent(
            """
            - project:
                name: org/project1
                tenant-one-gate:
                  jobs:
                    - project-test1

            - job:
                name: project1-test1
                parent: project-test1
            """)

        file_dict = {'.zuul.yaml': downstream_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'MERGED')
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()

        upstream_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1
                run: playbooks/project-test1.yaml

            - job:
                name: project-test2

            - project:
                name: org/project
                tenant-one-gate:
                  jobs:
                    - project-test1
            """)

        file_dict = {'.zuul.yaml': upstream_repo_conf}
        B = self.fake_gerrit.addFakeChange('org/project', 'master', 'B',
                                           files=file_dict)
        B.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(B.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.assertEqual(B.data['status'], 'MERGED')
        self.fake_gerrit.addEvent(B.getChangeMergedEvent())
        self.waitUntilSettled()

        tenant = self.sched.abide.tenants.get('tenant-one')
        # Ensure the latest change is reflected in the config; if it
        # isn't this will raise an exception.
        tenant.layout.getJob('project-test2')

    def test_pipeline_error(self):
        with open(os.path.join(FIXTURE_DIR,
                               'config/in-repo/git/',
                               'common-config/zuul.yaml')) as f:
            base_common_config = f.read()

        in_repo_conf_A = textwrap.dedent(
            """
            - pipeline:
                name: periodic
                foo: error
            """)

        file_dict = {'zuul.yaml': None,
                     'zuul.d/main.yaml': base_common_config,
                     'zuul.d/test1.yaml': in_repo_conf_A}
        A = self.fake_gerrit.addFakeChange('common-config', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertIn('syntax error',
                      A.messages[0],
                      "A should have an error reported")

    def test_change_series_error(self):
        with open(os.path.join(FIXTURE_DIR,
                               'config/in-repo/git/',
                               'common-config/zuul.yaml')) as f:
            base_common_config = f.read()

        in_repo_conf_A = textwrap.dedent(
            """
            - pipeline:
                name: periodic
                foo: error
            """)

        file_dict = {'zuul.yaml': None,
                     'zuul.d/main.yaml': base_common_config,
                     'zuul.d/test1.yaml': in_repo_conf_A}
        A = self.fake_gerrit.addFakeChange('common-config', 'master', 'A',
                                           files=file_dict)

        in_repo_conf_B = textwrap.dedent(
            """
            - job:
                name: project-test2
                foo: error
            """)

        file_dict = {'zuul.yaml': None,
                     'zuul.d/main.yaml': base_common_config,
                     'zuul.d/test1.yaml': in_repo_conf_A,
                     'zuul.d/test2.yaml': in_repo_conf_B}
        B = self.fake_gerrit.addFakeChange('common-config', 'master', 'B',
                                           files=file_dict)
        B.setDependsOn(A, 1)
        C = self.fake_gerrit.addFakeChange('common-config', 'master', 'C')
        C.setDependsOn(B, 1)
        self.fake_gerrit.addEvent(C.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(C.reported, 1,
                         "C should report failure")
        self.assertIn('Zuul encountered a syntax error while parsing '
                      'its configuration',
                      C.messages[0],
                      "C should have an error reported")

    def test_pipeline_debug(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1
                run: playbooks/project-test1.yaml
            - project:
                name: org/project
                check:
                  debug: True
                  jobs:
                    - project-test1
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(A.reported, 1,
                         "A should report success")
        self.assertIn('Debug information:',
                      A.messages[0], "A should have debug info")


class TestJobContamination(AnsibleZuulTestCase):

    config_file = 'zuul-connections-gerrit-and-github.conf'
    tenant_config_file = 'config/zuul-job-contamination/main.yaml'

    def test_job_contamination_playbooks(self):
        conf = textwrap.dedent(
            """
            - job:
                name: base
                post-run:
                  - playbooks/something-new.yaml
                parent: null
                vars:
                  basevar: basejob
            """)

        file_dict = {'zuul.d/jobs.yaml': conf}
        A = self.fake_github.openFakePullRequest(
            'org/global-config', 'master', 'A', files=file_dict)
        self.fake_github.emitEvent(A.getPullRequestOpenedEvent())
        self.waitUntilSettled()

        B = self.fake_github.openFakePullRequest('org/project1', 'master', 'A')
        self.fake_github.emitEvent(B.getPullRequestOpenedEvent())
        self.waitUntilSettled()

        statuses_b = self.fake_github.getCommitStatuses(
            'org/project1', B.head_sha)

        self.assertEqual(len(statuses_b), 1)

        # B should not be affected by the A PR
        self.assertEqual('success', statuses_b[0]['state'])

    def test_job_contamination_vars(self):
        conf = textwrap.dedent(
            """
            - job:
                name: base
                parent: null
                vars:
                  basevar: basejob-modified
            """)

        file_dict = {'zuul.d/jobs.yaml': conf}
        A = self.fake_github.openFakePullRequest(
            'org/global-config', 'master', 'A', files=file_dict)
        self.fake_github.emitEvent(A.getPullRequestOpenedEvent())
        self.waitUntilSettled()

        B = self.fake_github.openFakePullRequest('org/project1', 'master', 'A')
        self.fake_github.emitEvent(B.getPullRequestOpenedEvent())
        self.waitUntilSettled()

        statuses_b = self.fake_github.getCommitStatuses(
            'org/project1', B.head_sha)

        self.assertEqual(len(statuses_b), 1)

        # B should not be affected by the A PR
        self.assertEqual('success', statuses_b[0]['state'])


class TestInRepoJoin(ZuulTestCase):
    # In this config, org/project is not a member of any pipelines, so
    # that we may test the changes that cause it to join them.

    tenant_config_file = 'config/in-repo-join/main.yaml'

    def test_dynamic_dependent_pipeline(self):
        # Test dynamically adding a project to a
        # dependent pipeline for the first time
        self.executor_server.hold_jobs_in_build = True

        tenant = self.sched.abide.tenants.get('tenant-one')
        gate_pipeline = tenant.layout.pipelines['gate']
        self.assertEqual(gate_pipeline.queues, [])

        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1
                run: playbooks/project-test1.yaml

            - job:
                name: project-test2
                run: playbooks/project-test2.yaml

            - project:
                name: org/project
                gate:
                  jobs:
                    - project-test2
            """)

        in_repo_playbook = textwrap.dedent(
            """
            - hosts: all
              tasks: []
            """)

        file_dict = {'.zuul.yaml': in_repo_conf,
                     'playbooks/project-test2.yaml': in_repo_playbook}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        items = gate_pipeline.getAllItems()
        self.assertEqual(items[0].change.number, '1')
        self.assertEqual(items[0].change.patchset, '1')
        self.assertTrue(items[0].live)

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

        # Make sure the dynamic queue got cleaned up
        self.assertEqual(gate_pipeline.queues, [])

    def test_dynamic_dependent_pipeline_failure(self):
        # Test that a change behind a failing change adding a project
        # to a dependent pipeline is dequeued.
        self.executor_server.hold_jobs_in_build = True

        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test1
                run: playbooks/project-test1.yaml

            - project:
                name: org/project
                gate:
                  jobs:
                    - project-test1
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.executor_server.failJob('project-test1', A)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()

        B = self.fake_gerrit.addFakeChange('org/project', 'master', 'B')
        B.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(B.addApproval('Approved', 1))
        self.waitUntilSettled()

        self.orderedRelease()
        self.waitUntilSettled()
        self.assertEqual(A.reported, 2,
                         "A should report start and failure")
        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(B.reported, 1,
                         "B should report start")
        self.assertHistory([
            dict(name='project-test1', result='FAILURE', changes='1,1'),
            dict(name='project-test1', result='ABORTED', changes='1,1 2,1'),
        ], ordered=False)

    def test_dynamic_dependent_pipeline_absent(self):
        # Test that a series of dependent changes don't report merge
        # failures to a pipeline they aren't in.
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        B = self.fake_gerrit.addFakeChange('org/project', 'master', 'B')
        B.setDependsOn(A, 1)

        A.addApproval('Code-Review', 2)
        A.addApproval('Approved', 1)
        B.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(B.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(A.reported, 0,
                         "A should not report")
        self.assertEqual(A.data['status'], 'NEW')
        self.assertEqual(B.reported, 0,
                         "B should not report")
        self.assertEqual(B.data['status'], 'NEW')
        self.assertHistory([])


class TestAnsible(AnsibleZuulTestCase):
    # A temporary class to hold new tests while others are disabled

    tenant_config_file = 'config/ansible/main.yaml'

    def test_playbook(self):
        # This test runs a bit long and needs extra time.
        self.wait_timeout = 120
        # Keep the jobdir around so we can inspect contents if an
        # assert fails.
        self.executor_server.keep_jobdir = True
        # Output extra ansible info so we might see errors.
        self.executor_server.verbose = True
        # Add a site variables file, used by check-vars
        path = os.path.join(FIXTURE_DIR, 'config', 'ansible',
                            'variables.yaml')
        self.config.set('executor', 'variables', path)
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        build_timeout = self.getJobFromHistory('timeout')
        with self.jobLog(build_timeout):
            self.assertEqual(build_timeout.result, 'TIMED_OUT')
            post_flag_path = os.path.join(self.test_root, build_timeout.uuid +
                                          '.post.flag')
            self.assertTrue(os.path.exists(post_flag_path))
        build_post_timeout = self.getJobFromHistory('post-timeout')
        with self.jobLog(build_post_timeout):
            self.assertEqual(build_post_timeout.result, 'POST_FAILURE')
        build_faillocal = self.getJobFromHistory('faillocal')
        with self.jobLog(build_faillocal):
            self.assertEqual(build_faillocal.result, 'FAILURE')
        build_failpost = self.getJobFromHistory('failpost')
        with self.jobLog(build_failpost):
            self.assertEqual(build_failpost.result, 'POST_FAILURE')
        build_check_vars = self.getJobFromHistory('check-vars')
        with self.jobLog(build_check_vars):
            self.assertEqual(build_check_vars.result, 'SUCCESS')
        build_check_hostvars = self.getJobFromHistory('check-hostvars')
        with self.jobLog(build_check_hostvars):
            self.assertEqual(build_check_hostvars.result, 'SUCCESS')
        build_check_secret_names = self.getJobFromHistory('check-secret-names')
        with self.jobLog(build_check_secret_names):
            self.assertEqual(build_check_secret_names.result, 'SUCCESS')
        build_hello = self.getJobFromHistory('hello-world')
        with self.jobLog(build_hello):
            self.assertEqual(build_hello.result, 'SUCCESS')
        build_add_host = self.getJobFromHistory('add-host')
        with self.jobLog(build_add_host):
            self.assertEqual(build_add_host.result, 'SUCCESS')
        build_python27 = self.getJobFromHistory('python27')
        with self.jobLog(build_python27):
            self.assertEqual(build_python27.result, 'SUCCESS')
            flag_path = os.path.join(self.test_root,
                                     build_python27.uuid + '.flag')
            self.assertTrue(os.path.exists(flag_path))
            copied_path = os.path.join(self.test_root, build_python27.uuid +
                                       '.copied')
            self.assertTrue(os.path.exists(copied_path))
            failed_path = os.path.join(self.test_root, build_python27.uuid +
                                       '.failed')
            self.assertFalse(os.path.exists(failed_path))
            pre_flag_path = os.path.join(self.test_root, build_python27.uuid +
                                         '.pre.flag')
            self.assertTrue(os.path.exists(pre_flag_path))
            post_flag_path = os.path.join(self.test_root, build_python27.uuid +
                                          '.post.flag')
            self.assertTrue(os.path.exists(post_flag_path))
            bare_role_flag_path = os.path.join(self.test_root,
                                               build_python27.uuid +
                                               '.bare-role.flag')
            self.assertTrue(os.path.exists(bare_role_flag_path))
            secrets_path = os.path.join(self.test_root,
                                        build_python27.uuid + '.secrets')
            with open(secrets_path) as f:
                self.assertEqual(f.read(), "test-username test-password")

            msg = A.messages[0]
            success = "{} https://success.example.com/zuul-logs/{}"
            fail = "{} https://failure.example.com/zuul-logs/{}"
            self.assertIn(success.format("python27", build_python27.uuid), msg)
            self.assertIn(fail.format("faillocal", build_faillocal.uuid), msg)
            self.assertIn(success.format("check-vars",
                                         build_check_vars.uuid), msg)
            self.assertIn(success.format("hello-world", build_hello.uuid), msg)
            self.assertIn(fail.format("timeout", build_timeout.uuid), msg)
            self.assertIn(fail.format("failpost", build_failpost.uuid), msg)

    def test_repo_ansible(self):
        A = self.fake_gerrit.addFakeChange('org/ansible', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(A.reported, 1,
                         "A should report success")
        self.assertHistory([
            dict(name='hello-ansible', result='SUCCESS', changes='1,1'),
        ])

    def _add_job(self, job_name):
        conf = textwrap.dedent(
            """
            - job:
                name: %s
                run: playbooks/%s.yaml

            - project:
                name: org/plugin-project
                check:
                  jobs:
                    - %s
            """ % (job_name, job_name, job_name))

        file_dict = {'.zuul.yaml': conf}
        A = self.fake_gerrit.addFakeChange('org/plugin-project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

    def test_plugins(self):
        # Keep the jobdir around so we can inspect contents if an
        # assert fails.
        self.executor_server.keep_jobdir = True
        # Output extra ansible info so we might see errors.
        self.executor_server.verbose = True

        count = 0
        plugin_tests = [
            ('passwd', 'FAILURE'),
            ('cartesian', 'SUCCESS'),
            ('consul_kv', 'FAILURE'),
            ('credstash', 'FAILURE'),
            ('csvfile_good', 'SUCCESS'),
            ('csvfile_bad', 'FAILURE'),
            ('uri_bad_path', 'FAILURE'),
            ('uri_bad_scheme', 'FAILURE'),
            ('block_local_override', 'FAILURE'),
            ('file_local_good', 'SUCCESS'),
            ('file_local_bad', 'FAILURE'),
            ('zuul_return', 'SUCCESS'),
        ]
        for job_name, result in plugin_tests:
            count += 1
            self._add_job(job_name)

            job = self.getJobFromHistory(job_name)
            with self.jobLog(job):
                self.assertEqual(count, len(self.history))
                build = self.history[-1]
                self.assertEqual(build.result, result)

        # TODOv3(jeblair): parse the ansible output and verify we're
        # getting the exception we expect.


class TestPrePlaybooks(AnsibleZuulTestCase):
    # A temporary class to hold new tests while others are disabled

    tenant_config_file = 'config/pre-playbook/main.yaml'
    wait_timeout = 60

    def test_pre_playbook_fail(self):
        # Test that we run the post playbooks (but not the actual
        # playbook) when a pre-playbook fails.
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        build = self.getJobFromHistory('python27')
        self.assertIsNone(build.result)
        self.assertIn('RETRY_LIMIT', A.messages[0])
        flag_path = os.path.join(self.test_root, build.uuid +
                                 '.main.flag')
        self.assertFalse(os.path.exists(flag_path))
        pre_flag_path = os.path.join(self.test_root, build.uuid +
                                     '.pre.flag')
        self.assertFalse(os.path.exists(pre_flag_path))
        post_flag_path = os.path.join(self.test_root, build.uuid +
                                      '.post.flag')
        self.assertTrue(os.path.exists(post_flag_path),
                        "The file %s should exist" % post_flag_path)

    def test_post_playbook_fail_autohold(self):
        client = zuul.rpcclient.RPCClient('127.0.0.1',
                                          self.gearman_server.port)
        self.addCleanup(client.shutdown)
        r = client.autohold('tenant-one', 'org/project3', 'python27-node-post',
                            "", "", "reason text", 1)
        self.assertTrue(r)

        A = self.fake_gerrit.addFakeChange('org/project3', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        build = self.getJobFromHistory('python27-node-post')
        self.assertEqual(build.result, 'POST_FAILURE')

        # Check nodepool for a held node
        held_node = None
        for node in self.fake_nodepool.getNodes():
            if node['state'] == zuul.model.STATE_HOLD:
                held_node = node
                break
        self.assertIsNotNone(held_node)
        # Validate node has recorded the failed job
        self.assertEqual(
            held_node['hold_job'],
            " ".join(['tenant-one',
                      'review.example.com/org/project3',
                      'python27-node-post', '.*'])
        )
        self.assertEqual(held_node['comment'], "reason text")

    def test_pre_playbook_fail_autohold(self):
        client = zuul.rpcclient.RPCClient('127.0.0.1',
                                          self.gearman_server.port)
        self.addCleanup(client.shutdown)
        r = client.autohold('tenant-one', 'org/project2', 'python27-node',
                            "", "", "reason text", 1)
        self.assertTrue(r)

        A = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        build = self.getJobFromHistory('python27-node')
        self.assertIsNone(build.result)
        self.assertIn('RETRY_LIMIT', A.messages[0])

        # Check nodepool for a held node
        held_node = None
        for node in self.fake_nodepool.getNodes():
            if node['state'] == zuul.model.STATE_HOLD:
                held_node = node
                break
        self.assertIsNotNone(held_node)
        # Validate node has recorded the failed job
        self.assertEqual(
            held_node['hold_job'],
            " ".join(['tenant-one',
                      'review.example.com/org/project2',
                      'python27-node', '.*'])
        )
        self.assertEqual(held_node['comment'], "reason text")


class TestPostPlaybooks(AnsibleZuulTestCase):
    tenant_config_file = 'config/post-playbook/main.yaml'
    wait_timeout = 60

    def test_post_playbook_abort(self):
        # Test that when we abort a job in the post playbook, that we
        # don't send back POST_FAILURE.
        self.executor_server.verbose = True
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))

        while not len(self.builds):
            time.sleep(0.1)
        build = self.builds[0]

        post_start = os.path.join(self.test_root, build.uuid +
                                  '.post_start.flag')
        start = time.time()
        while time.time() < start + 90:
            if os.path.exists(post_start):
                break
            time.sleep(0.1)
        # The post playbook has started, abort the job
        self.fake_gerrit.addEvent(A.getChangeAbandonedEvent())
        self.waitUntilSettled()

        build = self.getJobFromHistory('python27')
        self.assertEqual('ABORTED', build.result)

        post_end = os.path.join(self.test_root, build.uuid +
                                '.post_end.flag')
        self.assertTrue(os.path.exists(post_start))
        self.assertFalse(os.path.exists(post_end))


class TestBrokenConfig(ZuulTestCase):
    # Test we can deal with a broken config

    tenant_config_file = 'config/broken/main.yaml'

    def test_broken_config_on_startup(self):
        # verify get the errors at tenant level.
        tenant = self.sched.abide.tenants.get('tenant-one')
        self.assertEquals(
            len(tenant.layout.loading_errors), 1,
            "An error should have been stored")
        self.assertIn(
            "Zuul encountered a syntax error",
            str(tenant.layout.loading_errors[0].error))

    @simple_layout('layouts/broken-template.yaml')
    def test_broken_config_on_startup_template(self):
        # Verify that a missing project-template doesn't break gate
        # pipeline construction.
        tenant = self.sched.abide.tenants.get('tenant-one')
        self.assertEquals(
            len(tenant.layout.loading_errors), 1,
            "An error should have been stored")
        self.assertIn(
            "Zuul encountered a syntax error",
            str(tenant.layout.loading_errors[0].error))

    def test_dynamic_ignore(self):
        # Verify dynamic config behaviors inside a tenant broken config
        tenant = self.sched.abide.tenants.get('tenant-one')
        # There is a configuration error
        self.assertEquals(
            len(tenant.layout.loading_errors), 1,
            "An error should have been stored")

        # Inside a broken tenant configuration environment,
        # send a valid config to an "unbroken" project and verify
        # that tenant configuration have been validated and job executed
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test
                run: playbooks/project-test.yaml

            - project:
                check:
                  jobs:
                    - project-test
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(A.patchsets[0]['approvals'][0]['value'], "1")
        self.assertHistory([
            dict(name='project-test', result='SUCCESS', changes='1,1')])

    def test_dynamic_fail_unbroken(self):
        # Verify dynamic config behaviors inside a tenant broken config
        tenant = self.sched.abide.tenants.get('tenant-one')
        # There is a configuration error
        self.assertEquals(
            len(tenant.layout.loading_errors), 1,
            "An error should have been stored")

        # Inside a broken tenant configuration environment,
        # send an invalid config to an "unbroken" project and verify
        # that tenant configuration have not been validated
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test
                run: playbooks/project-test.yaml

            - project:
                check:
                  jobs:
                    - non-existent-job
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        B = self.fake_gerrit.addFakeChange('org/project', 'master', 'B',
                                           files=file_dict)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(B.reported, 1,
                         "A should report failure")
        self.assertEqual(B.patchsets[0]['approvals'][0]['value'], "-1")
        self.assertIn('Job non-existent-job not defined', B.messages[0],
                      "A should have failed the check pipeline")

    def test_dynamic_fail_broken(self):
        # Verify dynamic config behaviors inside a tenant broken config
        tenant = self.sched.abide.tenants.get('tenant-one')
        # There is a configuration error
        self.assertEquals(
            len(tenant.layout.loading_errors), 1,
            "An error should have been stored")

        # Inside a broken tenant configuration environment,
        # send an invalid config to a "broken" project and verify
        # that tenant configuration have not been validated
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test
                run: playbooks/project-test.yaml

            - project:
                check:
                  jobs:
                    - non-existent-job
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        C = self.fake_gerrit.addFakeChange('org/project2', 'master', 'C',
                                           files=file_dict)
        self.fake_gerrit.addEvent(C.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(C.reported, 1,
                         "A should report failure")
        self.assertEqual(C.patchsets[0]['approvals'][0]['value'], "-1")
        self.assertIn('Job non-existent-job not defined', C.messages[0],
                      "A should have failed the check pipeline")

    def test_dynamic_fix_broken(self):
        # Verify dynamic config behaviors inside a tenant broken config
        tenant = self.sched.abide.tenants.get('tenant-one')
        # There is a configuration error
        self.assertEquals(
            len(tenant.layout.loading_errors), 1,
            "An error should have been stored")

        # Inside a broken tenant configuration environment,
        # send an valid config to a "broken" project and verify
        # that tenant configuration have been validated and job executed
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: project-test2
                run: playbooks/project-test.yaml

            - project:
                check:
                  jobs:
                    - project-test2
         """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        D = self.fake_gerrit.addFakeChange('org/project2', 'master', 'D',
                                           files=file_dict)
        self.fake_gerrit.addEvent(D.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(D.patchsets[0]['approvals'][0]['value'], "1")
        self.assertHistory([
            dict(name='project-test2', result='SUCCESS', changes='1,1')])

    def test_dynamic_fail_cross_repo(self):
        # Verify dynamic config behaviors inside a tenant broken config
        tenant = self.sched.abide.tenants.get('tenant-one')
        # There is a configuration error
        self.assertEquals(
            len(tenant.layout.loading_errors), 1,
            "An error should have been stored")

        # Inside a broken tenant configuration environment, remove a
        # job used in another repo and verify that an error is
        # reported despite the error being in a repo other than the
        # change.
        in_repo_conf = textwrap.dedent(
            """
            - pipeline:
                name: check
                manager: independent
                trigger:
                  gerrit:
                    - event: patchset-created
                success:
                  gerrit:
                    Verified: 1
                failure:
                  gerrit:
                    Verified: -1
            - job:
                name: base
                parent: null

            - project:
                name: common-config
                check:
                  jobs:
                    - noop
            """)

        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('common-config', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertEqual(A.patchsets[0]['approvals'][0]['value'], "-1")
        self.assertIn('Job central-test not defined', A.messages[0],
                      "A should have failed the check pipeline")


class TestProjectKeys(ZuulTestCase):
    # Test that we can generate project keys

    # Normally the test infrastructure copies a static key in place
    # for each project before starting tests.  This saves time because
    # Zuul's automatic key-generation on startup can be slow.  To make
    # sure we exercise that code, in this test we allow Zuul to create
    # keys for the project on startup.
    create_project_keys = True
    config_file = 'zuul-connections-gerrit-and-github.conf'
    tenant_config_file = 'config/in-repo/main.yaml'

    def test_key_generation(self):
        test_keys = []
        key_fns = ['private.pem', 'ssh.pem']
        for fn in key_fns:
            with open(os.path.join(FIXTURE_DIR, fn)) as i:
                test_keys.append(i.read())

        key_root = os.path.join(self.state_root, 'keys')
        secrets_key_file = os.path.join(
            key_root,
            'secrets/project/gerrit/org/project/0.pem')
        # Make sure that a proper key was created on startup
        with open(secrets_key_file, "rb") as f:
            private_secrets_key, public_secrets_key = \
                encryption.deserialize_rsa_keypair(f.read())

        # Make sure that we didn't just end up with the static fixture
        # key
        self.assertTrue(private_secrets_key not in test_keys)

        # Make sure it's the right length
        self.assertEqual(4096, private_secrets_key.key_size)

        ssh_key_file = os.path.join(
            key_root,
            'ssh/project/gerrit/org/project/0.pem')
        # Make sure that a proper key was created on startup
        ssh_key = paramiko.RSAKey.from_private_key_file(ssh_key_file)

        # Make sure that we didn't just end up with the static fixture
        # key
        self.assertTrue(private_secrets_key not in test_keys)

        # Make sure it's the right length
        self.assertEqual(2048, ssh_key.get_bits())


class RoleTestCase(ZuulTestCase):
    def _getRolesPaths(self, build, playbook):
        path = os.path.join(self.test_root, build.uuid,
                            'ansible', playbook, 'ansible.cfg')
        roles_paths = []
        with open(path) as f:
            for line in f:
                if line.startswith('roles_path'):
                    roles_paths.append(line)
        return roles_paths

    def _assertRolePath(self, build, playbook, content):
        roles_paths = self._getRolesPaths(build, playbook)
        if content:
            self.assertEqual(len(roles_paths), 1,
                             "Should have one roles_path line in %s" %
                             (playbook,))
            self.assertIn(content, roles_paths[0])
        else:
            self.assertEqual(len(roles_paths), 0,
                             "Should have no roles_path line in %s" %
                             (playbook,))

    def _assertInRolePath(self, build, playbook, files):
        roles_paths = self._getRolesPaths(build, playbook)[0]
        roles_paths = roles_paths.split('=')[-1].strip()
        roles_paths = roles_paths.split(':')

        files = set(files)
        matches = set()
        for rpath in roles_paths:
            for rolename in os.listdir(rpath):
                if rolename in files:
                    matches.add(rolename)
        self.assertEqual(files, matches)


class TestRoleBranches(RoleTestCase):
    tenant_config_file = 'config/role-branches/main.yaml'

    def _addRole(self, project, branch, role, parent=None):
        data = textwrap.dedent("""
            - name: %s
              debug:
                msg: %s
            """ % (role, role))
        file_dict = {'roles/%s/tasks/main.yaml' % role: data}
        A = self.fake_gerrit.addFakeChange(project, branch,
                                           'add %s' % role,
                                           files=file_dict,
                                           parent=parent)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()
        return A.patchsets[-1]['ref']

    def _addPlaybook(self, project, branch, playbook, role, parent=None):
        data = textwrap.dedent("""
            - hosts: all
              roles:
                - %s
            """ % role)
        file_dict = {'playbooks/%s.yaml' % playbook: data}
        A = self.fake_gerrit.addFakeChange(project, branch,
                                           'add %s' % playbook,
                                           files=file_dict,
                                           parent=parent)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()
        return A.patchsets[-1]['ref']

    def _assertInFile(self, path, content):
        with open(path) as f:
            self.assertIn(content, f.read())

    def test_playbook_role_branches(self):
        # This tests that the correct branch of a repo which contains
        # a playbook or a role is checked out.  Most of the action
        # happens on project1, which holds a parent job, so that we
        # can test the behavior of a project which is not in the
        # dependency chain.
        # First we create some branch-specific content in project1:
        self.create_branch('project1', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'project1', 'stable'))
        self.waitUntilSettled()

        # A pre-playbook with unique stable branch content.
        p = self._addPlaybook('project1', 'stable',
                              'parent-job-pre', 'parent-stable-role')
        # A role that only exists on the stable branch.
        self._addRole('project1', 'stable', 'stable-role', parent=p)

        # The same for the master branch.
        p = self._addPlaybook('project1', 'master',
                              'parent-job-pre', 'parent-master-role')
        self._addRole('project1', 'master', 'master-role', parent=p)

        self.sched.reconfigure(self.config)
        # Push a change to project2 which will run 3 jobs which
        # inherit from project1.
        self.executor_server.hold_jobs_in_build = True
        A = self.fake_gerrit.addFakeChange('project2', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(len(self.builds), 3)

        # This job should use the master branch since that's the
        # zuul.branch for this change.
        build = self.getBuildByName('child-job')
        self._assertInRolePath(build, 'playbook_0', ['master-role'])
        self._assertInFile(build.jobdir.pre_playbooks[1].path,
                           'parent-master-role')

        # The main playbook is on the master branch of project2, but
        # there is a job-level branch override, so the project1 role
        # should be from the stable branch.  The job-level override
        # will cause Zuul to select the project1 pre-playbook from the
        # stable branch as well, so we should see it using the stable
        # role.
        build = self.getBuildByName('child-job-override')
        self._assertInRolePath(build, 'playbook_0', ['stable-role'])
        self._assertInFile(build.jobdir.pre_playbooks[1].path,
                           'parent-stable-role')

        # The same, but using a required-projects override.
        build = self.getBuildByName('child-job-project-override')
        self._assertInRolePath(build, 'playbook_0', ['stable-role'])
        self._assertInFile(build.jobdir.pre_playbooks[1].path,
                           'parent-stable-role')

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()


class TestRoles(RoleTestCase):
    tenant_config_file = 'config/roles/main.yaml'

    def test_role(self):
        # This exercises a proposed change to a role being checked out
        # and used.
        A = self.fake_gerrit.addFakeChange('bare-role', 'master', 'A')
        B = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        B.data['commitMessage'] = '%s\n\nDepends-On: %s\n' % (
            B.subject, A.data['id'])
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='project-test', result='SUCCESS', changes='1,1 2,1'),
        ])

    def test_role_inheritance(self):
        self.executor_server.hold_jobs_in_build = True
        conf = textwrap.dedent(
            """
            - job:
                name: parent
                roles:
                  - zuul: bare-role
                pre-run: playbooks/parent-pre.yaml
                post-run: playbooks/parent-post.yaml

            - job:
                name: project-test
                parent: parent
                run: playbooks/project-test.yaml
                roles:
                  - zuul: org/project

            - project:
                name: org/project
                check:
                  jobs:
                    - project-test
            """)

        file_dict = {'.zuul.yaml': conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(len(self.builds), 1)
        build = self.getBuildByName('project-test')
        self._assertRolePath(build, 'pre_playbook_0', 'role_0')
        self._assertRolePath(build, 'playbook_0', 'role_0')
        self._assertRolePath(build, 'playbook_0', 'role_1')
        self._assertRolePath(build, 'post_playbook_0', 'role_0')

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

        self.assertHistory([
            dict(name='project-test', result='SUCCESS', changes='1,1'),
        ])

    def test_role_error(self):
        conf = textwrap.dedent(
            """
            - job:
                name: project-test
                run: playbooks/project-test.yaml
                roles:
                  - zuul: common-config

            - project:
                name: org/project
                check:
                  jobs:
                    - project-test
            """)

        file_dict = {'.zuul.yaml': conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertIn(
            '- project-test project-test : ERROR Unable to find role',
            A.messages[-1])


class TestImplicitRoles(RoleTestCase):
    tenant_config_file = 'config/implicit-roles/main.yaml'

    def test_missing_roles(self):
        # Test implicit and explicit roles for a project which does
        # not have roles.  The implicit role should be silently
        # ignored since the project doesn't supply roles, but if a
        # user declares an explicit role, it should error.
        self.executor_server.hold_jobs_in_build = True
        A = self.fake_gerrit.addFakeChange('org/norole-project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(len(self.builds), 2)
        build = self.getBuildByName('implicit-role-fail')
        self._assertRolePath(build, 'playbook_0', None)

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()
        # The retry_limit doesn't get recorded
        self.assertHistory([
            dict(name='implicit-role-fail', result='SUCCESS', changes='1,1'),
        ])

    def test_roles(self):
        # Test implicit and explicit roles for a project which does
        # have roles.  In both cases, we should end up with the role
        # in the path.  In the explicit case, ensure we end up with
        # the name we specified.
        self.executor_server.hold_jobs_in_build = True
        A = self.fake_gerrit.addFakeChange('org/role-project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual(len(self.builds), 2)
        build = self.getBuildByName('implicit-role-ok')
        self._assertRolePath(build, 'playbook_0', 'role_0')

        build = self.getBuildByName('explicit-role-ok')
        self._assertRolePath(build, 'playbook_0', 'role_0')

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='implicit-role-ok', result='SUCCESS', changes='1,1'),
            dict(name='explicit-role-ok', result='SUCCESS', changes='1,1'),
        ], ordered=False)


class TestShadow(ZuulTestCase):
    tenant_config_file = 'config/shadow/main.yaml'

    def test_shadow(self):
        # Test that a repo is allowed to shadow another's job definitions.
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='test1', result='SUCCESS', changes='1,1'),
            dict(name='test2', result='SUCCESS', changes='1,1'),
        ], ordered=False)


class TestDataReturn(AnsibleZuulTestCase):
    tenant_config_file = 'config/data-return/main.yaml'

    def test_data_return(self):
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='data-return', result='SUCCESS', changes='1,1'),
            dict(name='data-return-relative', result='SUCCESS', changes='1,1'),
            dict(name='child', result='SUCCESS', changes='1,1'),
        ], ordered=False)
        self.assertIn('- data-return http://example.com/test/log/url/',
                      A.messages[-1])
        self.assertIn('- data-return-relative '
                      'http://example.com/test/log/url/docs/index.html',
                      A.messages[-1])

    def test_data_return_child_jobs(self):
        self.wait_timeout = 120
        self.executor_server.hold_jobs_in_build = True

        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.executor_server.release('data-return-child-jobs')
        self.waitUntilSettled()

        self.executor_server.release('data-return-child-jobs')
        self.waitUntilSettled()

        # Make sure skipped jobs are not reported as failing
        tenant = self.sched.abide.tenants.get("tenant-one")
        status = tenant.layout.pipelines["check"].formatStatusJSON()
        self.assertEqual(
            status["change_queues"][0]["heads"][0][0]["failing_reasons"], [])

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

        self.assertHistory([
            dict(name='data-return-child-jobs', result='SUCCESS',
                 changes='1,1'),
            dict(name='data-return', result='SUCCESS', changes='1,1'),
        ])
        self.assertIn(
            '- data-return-child-jobs http://example.com/test/log/url/',
            A.messages[-1])
        self.assertIn(
            '- data-return http://example.com/test/log/url/',
            A.messages[-1])
        self.assertIn('child : SKIPPED', A.messages[-1])
        self.assertIn('Build succeeded', A.messages[-1])

    def test_data_return_invalid_child_job(self):
        A = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='data-return-invalid-child-job', result='SUCCESS',
                 changes='1,1')])
        self.assertIn(
            '- data-return-invalid-child-job http://example.com/test/log/url/',
            A.messages[-1])
        self.assertIn('data-return : SKIPPED', A.messages[-1])
        self.assertIn('Build succeeded', A.messages[-1])

    def test_data_return_skip_all_child_jobs(self):
        A = self.fake_gerrit.addFakeChange('org/project3', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='data-return-skip-all', result='SUCCESS',
                 changes='1,1'),
        ])
        self.assertIn(
            '- data-return-skip-all http://example.com/test/log/url/',
            A.messages[-1])
        self.assertIn('child : SKIPPED', A.messages[-1])
        self.assertIn('data-return : SKIPPED', A.messages[-1])
        self.assertIn('Build succeeded', A.messages[-1])

    def test_several_zuul_return(self):
        A = self.fake_gerrit.addFakeChange('org/project4', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='several-zuul-return-child', result='SUCCESS',
                 changes='1,1'),
        ])
        self.assertIn(
            '- several-zuul-return-child http://example.com/test/log/url/',
            A.messages[-1])
        self.assertIn('data-return : SKIPPED', A.messages[-1])
        self.assertIn('Build succeeded', A.messages[-1])


class TestDiskAccounting(AnsibleZuulTestCase):
    config_file = 'zuul-disk-accounting.conf'
    tenant_config_file = 'config/disk-accountant/main.yaml'

    def test_disk_accountant_kills_job(self):
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='dd-big-empty-file', result='ABORTED', changes='1,1')])


class TestMaxNodesPerJob(AnsibleZuulTestCase):
    tenant_config_file = 'config/multi-tenant/main.yaml'

    def test_max_timeout_exceeded(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: test-job
                nodeset:
                  nodes:
                    - name: node01
                      label: fake
                    - name: node02
                      label: fake
                    - name: node03
                      label: fake
                    - name: node04
                      label: fake
                    - name: node05
                      label: fake
                    - name: node06
                      label: fake
            """)
        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertIn('The job "test-job" exceeds tenant max-nodes-per-job 5.',
                      A.messages[0], "A should fail because of nodes limit")

        B = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertNotIn("exceeds tenant max-nodes", B.messages[0],
                         "B should not fail because of nodes limit")


class TestMaxTimeout(ZuulTestCase):
    tenant_config_file = 'config/multi-tenant/main.yaml'

    def test_max_nodes_reached(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: test-job
                timeout: 3600
            """)
        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertIn('The job "test-job" exceeds tenant max-job-timeout',
                      A.messages[0], "A should fail because of timeout limit")

        B = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertNotIn("exceeds tenant max-job-timeout", B.messages[0],
                         "B should not fail because of timeout limit")


class TestAllowedConnection(AnsibleZuulTestCase):
    config_file = 'zuul-connections-gerrit-and-github.conf'
    tenant_config_file = 'config/multi-tenant/main.yaml'

    def test_allowed_triggers(self):
        in_repo_conf = textwrap.dedent(
            """
            - pipeline:
                name: test
                manager: independent
                trigger:
                  github:
                    - event: pull_request
            """)
        file_dict = {'zuul.d/test.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange(
            'tenant-two-config', 'master', 'A', files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertIn(
            'Unknown connection named "github"', A.messages[0],
            "A should fail because of allowed-trigger")

        B = self.fake_gerrit.addFakeChange(
            'tenant-one-config', 'master', 'A', files=file_dict)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertNotIn(
            'Unknown connection named "github"', B.messages[0],
            "B should not fail because of allowed-trigger")

    def test_allowed_reporters(self):
        in_repo_conf = textwrap.dedent(
            """
            - pipeline:
                name: test
                manager: independent
                success:
                  outgoing_smtp:
                    to: you@example.com
            """)
        file_dict = {'zuul.d/test.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange(
            'tenant-one-config', 'master', 'A', files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertIn(
            'Unknown connection named "outgoing_smtp"', A.messages[0],
            "A should fail because of allowed-reporters")

        B = self.fake_gerrit.addFakeChange(
            'tenant-two-config', 'master', 'A', files=file_dict)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertNotIn(
            'Unknown connection named "outgoing_smtp"', B.messages[0],
            "B should not fail because of allowed-reporters")


class TestAllowedLabels(AnsibleZuulTestCase):
    config_file = 'zuul-connections-gerrit-and-github.conf'
    tenant_config_file = 'config/multi-tenant/main.yaml'

    def test_allowed_labels(self):
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: test
                nodeset:
                  nodes:
                    - name: controller
                      label: tenant-two-label
            """)
        file_dict = {'zuul.d/test.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange(
            'tenant-one-config', 'master', 'A', files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertIn(
            'Label named "tenant-two-label" is not part of the allowed',
            A.messages[0],
            "A should fail because of allowed-labels")


class TestPragma(ZuulTestCase):
    tenant_config_file = 'config/pragma/main.yaml'

    def test_no_pragma(self):
        self.create_branch('org/project', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project', 'stable'))
        self.waitUntilSettled()
        with open(os.path.join(FIXTURE_DIR,
                               'config/pragma/git/',
                               'org_project/nopragma.yaml')) as f:
            config = f.read()
        file_dict = {'.zuul.yaml': config}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()

        # This is an untrusted repo with 2 branches, so it should have
        # an implied branch matcher for the job.
        tenant = self.sched.abide.tenants.get('tenant-one')
        jobs = tenant.layout.getJobs('test-job')
        self.assertEqual(len(jobs), 1)
        for job in tenant.layout.getJobs('test-job'):
            self.assertIsNotNone(job.branch_matcher)

    def test_pragma(self):
        self.create_branch('org/project', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project', 'stable'))
        self.waitUntilSettled()
        with open(os.path.join(FIXTURE_DIR,
                               'config/pragma/git/',
                               'org_project/pragma.yaml')) as f:
            config = f.read()
        file_dict = {'.zuul.yaml': config}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()

        # This is an untrusted repo with 2 branches, so it would
        # normally have an implied branch matcher, but our pragma
        # overrides it.
        tenant = self.sched.abide.tenants.get('tenant-one')
        jobs = tenant.layout.getJobs('test-job')
        self.assertEqual(len(jobs), 1)
        for job in tenant.layout.getJobs('test-job'):
            self.assertIsNone(job.branch_matcher)


class TestPragmaMultibranch(ZuulTestCase):
    tenant_config_file = 'config/pragma-multibranch/main.yaml'

    def test_no_branch_matchers(self):
        self.create_branch('org/project1', 'stable/pike')
        self.create_branch('org/project2', 'stable/jewel')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project1', 'stable/pike'))
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project2', 'stable/jewel'))
        self.waitUntilSettled()
        # We want the jobs defined on the stable/pike branch of
        # project1 to apply to the stable/jewel branch of project2.

        # First, without the pragma line, the jobs should not run
        # because in project1 they have branch matchers for pike, so
        # they will not match a jewel change.
        B = self.fake_gerrit.addFakeChange('org/project2', 'stable/jewel', 'B')
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([])

        # Add a pragma line to disable implied branch matchers in
        # project1, so that the jobs and templates apply to both
        # branches.
        with open(os.path.join(FIXTURE_DIR,
                               'config/pragma-multibranch/git/',
                               'org_project1/zuul.yaml')) as f:
            config = f.read()
        extra_conf = textwrap.dedent(
            """
            - pragma:
                implied-branch-matchers: False
            """)
        config = extra_conf + config
        file_dict = {'zuul.yaml': config}
        A = self.fake_gerrit.addFakeChange('org/project1', 'stable/pike', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()

        # Now verify that when we propose a change to jewel, we get
        # the pike/jewel jobs.
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='test-job1', result='SUCCESS', changes='1,1'),
            dict(name='test-job2', result='SUCCESS', changes='1,1'),
        ], ordered=False)

    def test_supplied_branch_matchers(self):
        self.create_branch('org/project1', 'stable/pike')
        self.create_branch('org/project2', 'stable/jewel')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project1', 'stable/pike'))
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project2', 'stable/jewel'))
        self.waitUntilSettled()
        # We want the jobs defined on the stable/pike branch of
        # project1 to apply to the stable/jewel branch of project2.

        # First, without the pragma line, the jobs should not run
        # because in project1 they have branch matchers for pike, so
        # they will not match a jewel change.
        B = self.fake_gerrit.addFakeChange('org/project2', 'stable/jewel', 'B')
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([])

        # Add a pragma line to disable implied branch matchers in
        # project1, so that the jobs and templates apply to both
        # branches.
        with open(os.path.join(FIXTURE_DIR,
                               'config/pragma-multibranch/git/',
                               'org_project1/zuul.yaml')) as f:
            config = f.read()
        extra_conf = textwrap.dedent(
            """
            - pragma:
                implied-branches:
                  - stable/pike
                  - stable/jewel
            """)
        config = extra_conf + config
        file_dict = {'zuul.yaml': config}
        A = self.fake_gerrit.addFakeChange('org/project1', 'stable/pike', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()
        # Now verify that when we propose a change to jewel, we get
        # the pike/jewel jobs.
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='test-job1', result='SUCCESS', changes='1,1'),
            dict(name='test-job2', result='SUCCESS', changes='1,1'),
        ], ordered=False)


class TestBaseJobs(ZuulTestCase):
    tenant_config_file = 'config/base-jobs/main.yaml'

    def test_multiple_base_jobs(self):
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='my-job', result='SUCCESS', changes='1,1'),
            dict(name='other-job', result='SUCCESS', changes='1,1'),
        ], ordered=False)
        self.assertEqual(self.getJobFromHistory('my-job').
                         parameters['zuul']['jobtags'],
                         ['mybase'])
        self.assertEqual(self.getJobFromHistory('other-job').
                         parameters['zuul']['jobtags'],
                         ['otherbase'])

    def test_untrusted_base_job(self):
        """Test that a base job may not be defined in an untrusted repo"""
        in_repo_conf = textwrap.dedent(
            """
            - job:
                name: fail-base
                parent: null
            """)

        file_dict = {'.zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(A.reported, 1,
                         "A should report failure")
        self.assertEqual(A.patchsets[0]['approvals'][0]['value'], "-1")
        self.assertIn('Base jobs must be defined in config projects',
                      A.messages[0])
        self.assertHistory([])


class TestSecrets(ZuulTestCase):
    tenant_config_file = 'config/secrets/main.yaml'
    secret = {'password': 'test-password',
              'username': 'test-username'}

    def _getSecrets(self, job, pbtype):
        secrets = []
        build = self.getJobFromHistory(job)
        for pb in build.parameters[pbtype]:
            secrets.append(pb['secrets'])
        return secrets

    def test_secret_branch(self):
        # Test that we can use a secret defined in another branch of
        # the same project.
        self.create_branch('org/project2', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project2', 'stable'))
        self.waitUntilSettled()

        with open(os.path.join(FIXTURE_DIR,
                               'config/secrets/git/',
                               'org_project2/zuul-secret.yaml')) as f:
            config = f.read()

        file_dict = {'zuul.yaml': config}
        A = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(A.data['status'], 'MERGED')
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()

        in_repo_conf = textwrap.dedent(
            """
            - job:
                parent: base
                name: project2-secret
                run: playbooks/secret.yaml
                secrets: [project2_secret]

            - project:
                check:
                  jobs:
                    - project2-secret
                gate:
                  jobs:
                    - noop
            """)
        file_dict = {'zuul.yaml': in_repo_conf}
        B = self.fake_gerrit.addFakeChange('org/project2', 'stable', 'B',
                                           files=file_dict)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(B.reported, 1, "B should report success")
        self.assertHistory([
            dict(name='project2-secret', result='SUCCESS', changes='2,1'),
        ])
        self.assertEqual(
            self._getSecrets('project2-secret', 'playbooks'),
            [{'project2_secret': self.secret}])

    def test_secret_branch_duplicate(self):
        # Test that we can create a duplicate secret on a different
        # branch of the same project -- i.e., that when we branch
        # master to stable on a project with a secret, nothing
        # changes.
        self.create_branch('org/project1', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project1', 'stable'))
        self.waitUntilSettled()

        A = self.fake_gerrit.addFakeChange('org/project1', 'stable', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(A.reported, 1,
                         "A should report success")
        self.assertHistory([
            dict(name='project1-secret', result='SUCCESS', changes='1,1'),
        ])
        self.assertEqual(
            self._getSecrets('project1-secret', 'playbooks'),
            [{'project1_secret': self.secret}])

    def test_secret_branch_error_same_branch(self):
        # Test that we are unable to define a secret twice on the same
        # project-branch.
        in_repo_conf = textwrap.dedent(
            """
            - secret:
                name: project1_secret
                data: {}
            - secret:
                name: project1_secret
                data: {}
            """)
        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertIn('already defined', A.messages[0])

    def test_secret_branch_error_same_project(self):
        # Test that we are unable to create a secret which differs
        # from another with the same name -- i.e., that if we have a
        # duplicate secret on multiple branches of the same project,
        # they must be identical.
        self.create_branch('org/project1', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project1', 'stable'))
        self.waitUntilSettled()

        in_repo_conf = textwrap.dedent(
            """
            - secret:
                name: project1_secret
                data: {}
            """)
        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project1', 'stable', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertIn('does not match existing definition in branch master',
                      A.messages[0])

    def test_secret_branch_error_other_project(self):
        # Test that we are unable to create a secret with the same
        # name as another.  We're never allowed to have a secret with
        # the same name outside of a project.
        in_repo_conf = textwrap.dedent(
            """
            - secret:
                name: project1_secret
                data: {}
            """)
        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertIn('already defined in project org/project1',
                      A.messages[0])

    def test_complex_secret(self):
        # Test that we can use a complex secret
        with open(os.path.join(FIXTURE_DIR,
                               'config/secrets/git/',
                               'org_project2/zuul-complex.yaml')) as f:
            config = f.read()

        file_dict = {'zuul.yaml': config}
        A = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(A.reported, 1, "A should report success")
        self.assertHistory([
            dict(name='project2-complex', result='SUCCESS', changes='1,1'),
        ])
        secret = {'complex_secret':
                  {'dict': {'password': 'test-password',
                            'username': 'test-username'},
                   'list': ['one', 'test-password', 'three'],
                   'profile': 'cloudy'}}

        self.assertEqual(
            self._getSecrets('project2-complex', 'playbooks'),
            [secret])


class TestSecretInheritance(ZuulTestCase):
    tenant_config_file = 'config/secret-inheritance/main.yaml'

    def _getSecrets(self, job, pbtype):
        secrets = []
        build = self.getJobFromHistory(job)
        for pb in build.parameters[pbtype]:
            secrets.append(pb['secrets'])
        return secrets

    def _checkTrustedSecrets(self):
        secret = {'longpassword': 'test-passwordtest-password',
                  'password': 'test-password',
                  'username': 'test-username'}
        base_secret = {'username': 'base-username'}
        self.assertEqual(
            self._getSecrets('trusted-secrets', 'playbooks'),
            [{'trusted-secret': secret}])
        self.assertEqual(
            self._getSecrets('trusted-secrets', 'pre_playbooks'),
            [{'base-secret': base_secret}])
        self.assertEqual(
            self._getSecrets('trusted-secrets', 'post_playbooks'), [])

        self.assertEqual(
            self._getSecrets('trusted-secrets-trusted-child',
                             'playbooks'), [{}])
        self.assertEqual(
            self._getSecrets('trusted-secrets-trusted-child',
                             'pre_playbooks'),
            [{'base-secret': base_secret}])
        self.assertEqual(
            self._getSecrets('trusted-secrets-trusted-child',
                             'post_playbooks'), [])

        self.assertEqual(
            self._getSecrets('trusted-secrets-untrusted-child',
                             'playbooks'), [{}])
        self.assertEqual(
            self._getSecrets('trusted-secrets-untrusted-child',
                             'pre_playbooks'),
            [{'base-secret': base_secret}])
        self.assertEqual(
            self._getSecrets('trusted-secrets-untrusted-child',
                             'post_playbooks'), [])

    def _checkUntrustedSecrets(self):
        secret = {'longpassword': 'test-passwordtest-password',
                  'password': 'test-password',
                  'username': 'test-username'}
        base_secret = {'username': 'base-username'}
        self.assertEqual(
            self._getSecrets('untrusted-secrets', 'playbooks'),
            [{'untrusted-secret': secret}])
        self.assertEqual(
            self._getSecrets('untrusted-secrets', 'pre_playbooks'),
            [{'base-secret': base_secret}])
        self.assertEqual(
            self._getSecrets('untrusted-secrets', 'post_playbooks'), [])

        self.assertEqual(
            self._getSecrets('untrusted-secrets-trusted-child',
                             'playbooks'), [{}])
        self.assertEqual(
            self._getSecrets('untrusted-secrets-trusted-child',
                             'pre_playbooks'),
            [{'base-secret': base_secret}])
        self.assertEqual(
            self._getSecrets('untrusted-secrets-trusted-child',
                             'post_playbooks'), [])

        self.assertEqual(
            self._getSecrets('untrusted-secrets-untrusted-child',
                             'playbooks'), [{}])
        self.assertEqual(
            self._getSecrets('untrusted-secrets-untrusted-child',
                             'pre_playbooks'),
            [{'base-secret': base_secret}])
        self.assertEqual(
            self._getSecrets('untrusted-secrets-untrusted-child',
                             'post_playbooks'), [])

    def test_trusted_secret_inheritance_check(self):
        A = self.fake_gerrit.addFakeChange('common-config', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='trusted-secrets', result='SUCCESS', changes='1,1'),
            dict(name='trusted-secrets-trusted-child',
                 result='SUCCESS', changes='1,1'),
            dict(name='trusted-secrets-untrusted-child',
                 result='SUCCESS', changes='1,1'),
        ], ordered=False)

        self._checkTrustedSecrets()

    def test_untrusted_secret_inheritance_check(self):
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        # This configuration tries to run untrusted secrets in an
        # non-post-review pipeline and should therefore run no jobs.
        self.assertHistory([])


class TestSecretPassToParent(ZuulTestCase):
    tenant_config_file = 'config/pass-to-parent/main.yaml'

    def _getSecrets(self, job, pbtype):
        secrets = []
        build = self.getJobFromHistory(job)
        for pb in build.parameters[pbtype]:
            secrets.append(pb['secrets'])
        return secrets

    def test_secret_no_pass_to_parent(self):
        # Test that secrets are not available in the parent if
        # pass-to-parent is not set.
        file_dict = {'no-pass.txt': ''}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(A.data['status'], 'MERGED')
        self.assertHistory([
            dict(name='no-pass', result='SUCCESS', changes='1,1'),
        ])

        self.assertEqual(
            self._getSecrets('no-pass', 'playbooks'),
            [{'parent_secret': {'password': 'password3'}}])
        self.assertEqual(
            self._getSecrets('no-pass', 'pre_playbooks'),
            [{'parent_secret': {'password': 'password3'}}])
        self.assertEqual(
            self._getSecrets('no-pass', 'post_playbooks'),
            [{'parent_secret': {'password': 'password3'}}])

    def test_secret_pass_to_parent(self):
        # Test that secrets are available in the parent if
        # pass-to-parent is set.
        file_dict = {'pass.txt': ''}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(A.data['status'], 'MERGED')
        self.assertHistory([
            dict(name='pass', result='SUCCESS', changes='1,1'),
        ])

        self.assertEqual(
            self._getSecrets('pass', 'playbooks'),
            [{'parent_secret': {'password': 'password3'},
              'secret': {'password': 'password1'}}])
        self.assertEqual(
            self._getSecrets('pass', 'pre_playbooks'),
            [{'parent_secret': {'password': 'password3'},
              'secret': {'password': 'password1'}}])
        self.assertEqual(
            self._getSecrets('pass', 'post_playbooks'),
            [{'parent_secret': {'password': 'password3'},
              'secret': {'password': 'password1'}}])

        B = self.fake_gerrit.addFakeChange('org/project', 'master', 'B',
                                           files=file_dict)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='pass', result='SUCCESS', changes='1,1'),
        ])
        self.assertIn('does not allow post-review', B.messages[0])

    def test_secret_override(self):
        # Test that secrets passed to parents don't override existing
        # secrets.
        file_dict = {'override.txt': ''}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(A.data['status'], 'MERGED')
        self.assertHistory([
            dict(name='override', result='SUCCESS', changes='1,1'),
        ])

        self.assertEqual(
            self._getSecrets('override', 'playbooks'),
            [{'parent_secret': {'password': 'password3'},
              'secret': {'password': 'password1'}}])
        self.assertEqual(
            self._getSecrets('override', 'pre_playbooks'),
            [{'parent_secret': {'password': 'password3'},
              'secret': {'password': 'password1'}}])
        self.assertEqual(
            self._getSecrets('override', 'post_playbooks'),
            [{'parent_secret': {'password': 'password3'},
              'secret': {'password': 'password1'}}])

    def test_secret_ptp_trusted_untrusted(self):
        # Test if we pass a secret to a parent and one of the parents
        # is untrusted, the job becomes post-review.
        file_dict = {'trusted-under-untrusted.txt': ''}
        A = self.fake_gerrit.addFakeChange('common-config', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(A.data['status'], 'MERGED')
        self.assertHistory([
            dict(name='trusted-under-untrusted',
                 result='SUCCESS', changes='1,1'),
        ])

        self.assertEqual(
            self._getSecrets('trusted-under-untrusted', 'playbooks'),
            [{'secret': {'password': 'trustedpassword1'}}])
        self.assertEqual(
            self._getSecrets('trusted-under-untrusted', 'pre_playbooks'),
            [{'secret': {'password': 'trustedpassword1'}}])
        self.assertEqual(
            self._getSecrets('trusted-under-untrusted', 'post_playbooks'),
            [{'secret': {'password': 'trustedpassword1'}}])

        B = self.fake_gerrit.addFakeChange('common-config', 'master', 'B',
                                           files=file_dict)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='trusted-under-untrusted',
                 result='SUCCESS', changes='1,1'),
        ])
        self.assertIn('does not allow post-review', B.messages[0])

    def test_secret_ptp_trusted_trusted(self):
        # Test if we pass a secret to a parent and all of the parents
        # are trusted, the job does not become post-review.
        file_dict = {'trusted-under-trusted.txt': ''}
        A = self.fake_gerrit.addFakeChange('common-config', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(A.data['status'], 'MERGED')
        self.assertHistory([
            dict(name='trusted-under-trusted',
                 result='SUCCESS', changes='1,1'),
        ])

        self.assertEqual(
            self._getSecrets('trusted-under-trusted', 'playbooks'),
            [{'secret': {'password': 'trustedpassword1'}}])
        self.assertEqual(
            self._getSecrets('trusted-under-trusted', 'pre_playbooks'),
            [{'secret': {'password': 'trustedpassword1'}}])
        self.assertEqual(
            self._getSecrets('trusted-under-trusted', 'post_playbooks'),
            [{'secret': {'password': 'trustedpassword1'}}])

        B = self.fake_gerrit.addFakeChange('common-config', 'master', 'B',
                                           files=file_dict)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='trusted-under-trusted',
                 result='SUCCESS', changes='1,1'),
            dict(name='trusted-under-trusted',
                 result='SUCCESS', changes='2,1'),
        ])


class TestSecretLeaks(AnsibleZuulTestCase):
    tenant_config_file = 'config/secret-leaks/main.yaml'

    def searchForContent(self, path, content):
        matches = []
        for (dirpath, dirnames, filenames) in os.walk(path):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                with open(filepath, 'rb') as f:
                    if content in f.read():
                        matches.append(filepath[len(path):])
        return matches

    def _test_secret_file(self):
        # Or rather -- test that they *don't* leak.
        # Keep the jobdir around so we can inspect contents.
        self.executor_server.keep_jobdir = True
        conf = textwrap.dedent(
            """
            - project:
                name: org/project
                check:
                  jobs:
                    - secret-file
            """)

        file_dict = {'.zuul.yaml': conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='secret-file', result='SUCCESS', changes='1,1'),
        ], ordered=False)
        matches = self.searchForContent(self.history[0].jobdir.root,
                                        b'test-password')
        self.assertEqual(set(['/work/secret-file.txt']),
                         set(matches))

    def test_secret_file(self):
        self._test_secret_file()

    def test_secret_file_verbose(self):
        # Output extra ansible info to exercise alternate logging code
        # paths.
        self.executor_server.verbose = True
        self._test_secret_file()

    def _test_secret_file_fail(self):
        # Or rather -- test that they *don't* leak.
        # Keep the jobdir around so we can inspect contents.
        self.executor_server.keep_jobdir = True
        conf = textwrap.dedent(
            """
            - project:
                name: org/project
                check:
                  jobs:
                    - secret-file-fail
            """)

        file_dict = {'.zuul.yaml': conf}
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='secret-file-fail', result='FAILURE', changes='1,1'),
        ], ordered=False)
        matches = self.searchForContent(self.history[0].jobdir.root,
                                        b'test-password')
        self.assertEqual(set(['/work/failure-file.txt']),
                         set(matches))

    def test_secret_file_fail(self):
        self._test_secret_file_fail()

    def test_secret_file_fail_verbose(self):
        # Output extra ansible info to exercise alternate logging code
        # paths.
        self.executor_server.verbose = True
        self._test_secret_file_fail()


class TestNodesets(ZuulTestCase):
    tenant_config_file = 'config/nodesets/main.yaml'

    def test_nodeset_branch(self):
        # Test that we can use a nodeset defined in another branch of
        # the same project.
        self.create_branch('org/project2', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project2', 'stable'))
        self.waitUntilSettled()

        with open(os.path.join(FIXTURE_DIR,
                               'config/nodesets/git/',
                               'org_project2/zuul-nodeset.yaml')) as f:
            config = f.read()

        file_dict = {'zuul.yaml': config}
        A = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(A.data['status'], 'MERGED')
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()

        in_repo_conf = textwrap.dedent(
            """
            - job:
                parent: base
                name: project2-test
                nodeset: project2-nodeset

            - project:
                check:
                  jobs:
                    - project2-test
                gate:
                  jobs:
                    - noop
            """)
        file_dict = {'zuul.yaml': in_repo_conf}
        B = self.fake_gerrit.addFakeChange('org/project2', 'stable', 'B',
                                           files=file_dict)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(B.reported, 1, "B should report success")
        self.assertHistory([
            dict(name='project2-test', result='SUCCESS', changes='2,1',
                 node='ubuntu-xenial'),
        ])

    def test_nodeset_branch_duplicate(self):
        # Test that we can create a duplicate nodeset on a different
        # branch of the same project -- i.e., that when we branch
        # master to stable on a project with a nodeset, nothing
        # changes.
        self.create_branch('org/project1', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project1', 'stable'))
        self.waitUntilSettled()

        A = self.fake_gerrit.addFakeChange('org/project1', 'stable', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(A.reported, 1,
                         "A should report success")
        self.assertHistory([
            dict(name='project1-test', result='SUCCESS', changes='1,1',
                 node='ubuntu-xenial'),
        ])

    def test_nodeset_branch_error_same_branch(self):
        # Test that we are unable to define a nodeset twice on the same
        # project-branch.
        in_repo_conf = textwrap.dedent(
            """
            - nodeset:
                name: project1-nodeset
                nodes: []
            - nodeset:
                name: project1-nodeset
                nodes: []
            """)
        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertIn('already defined', A.messages[0])

    def test_nodeset_branch_error_same_project(self):
        # Test that we are unable to create a nodeset which differs
        # from another with the same name -- i.e., that if we have a
        # duplicate nodeset on multiple branches of the same project,
        # they must be identical.
        self.create_branch('org/project1', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project1', 'stable'))
        self.waitUntilSettled()

        in_repo_conf = textwrap.dedent(
            """
            - nodeset:
                name: project1-nodeset
                nodes: []
            """)
        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project1', 'stable', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertIn('does not match existing definition in branch master',
                      A.messages[0])

    def test_nodeset_branch_error_other_project(self):
        # Test that we are unable to create a nodeset with the same
        # name as another.  We're never allowed to have a nodeset with
        # the same name outside of a project.
        in_repo_conf = textwrap.dedent(
            """
            - nodeset:
                name: project1-nodeset
                nodes: []
            """)
        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertIn('already defined in project org/project1',
                      A.messages[0])


class TestSemaphoreBranches(ZuulTestCase):
    tenant_config_file = 'config/semaphore-branches/main.yaml'

    def test_semaphore_branch(self):
        # Test that we can use a semaphore defined in another branch of
        # the same project.
        self.create_branch('org/project2', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project2', 'stable'))
        self.waitUntilSettled()

        with open(os.path.join(FIXTURE_DIR,
                               'config/semaphore-branches/git/',
                               'org_project2/zuul-semaphore.yaml')) as f:
            config = f.read()

        file_dict = {'zuul.yaml': config}
        A = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A',
                                           files=file_dict)
        A.addApproval('Code-Review', 2)
        self.fake_gerrit.addEvent(A.addApproval('Approved', 1))
        self.waitUntilSettled()
        self.assertEqual(A.data['status'], 'MERGED')
        self.fake_gerrit.addEvent(A.getChangeMergedEvent())
        self.waitUntilSettled()

        in_repo_conf = textwrap.dedent(
            """
            - job:
                parent: base
                name: project2-test
                semaphore: project2-semaphore

            - project:
                check:
                  jobs:
                    - project2-test
                gate:
                  jobs:
                    - noop
            """)
        file_dict = {'zuul.yaml': in_repo_conf}
        B = self.fake_gerrit.addFakeChange('org/project2', 'stable', 'B',
                                           files=file_dict)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(B.reported, 1, "B should report success")
        self.assertHistory([
            dict(name='project2-test', result='SUCCESS', changes='2,1')
        ])

    def test_semaphore_branch_duplicate(self):
        # Test that we can create a duplicate semaphore on a different
        # branch of the same project -- i.e., that when we branch
        # master to stable on a project with a semaphore, nothing
        # changes.
        self.create_branch('org/project1', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project1', 'stable'))
        self.waitUntilSettled()

        A = self.fake_gerrit.addFakeChange('org/project1', 'stable', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertEqual(A.reported, 1,
                         "A should report success")
        self.assertHistory([
            dict(name='project1-test', result='SUCCESS', changes='1,1')
        ])

    def test_semaphore_branch_error_same_branch(self):
        # Test that we are unable to define a semaphore twice on the same
        # project-branch.
        in_repo_conf = textwrap.dedent(
            """
            - semaphore:
                name: project1-semaphore
                max: 2
            - semaphore:
                name: project1-semaphore
                max: 2
            """)
        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project1', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertIn('already defined', A.messages[0])

    def test_semaphore_branch_error_same_project(self):
        # Test that we are unable to create a semaphore which differs
        # from another with the same name -- i.e., that if we have a
        # duplicate semaphore on multiple branches of the same project,
        # they must be identical.
        self.create_branch('org/project1', 'stable')
        self.fake_gerrit.addEvent(
            self.fake_gerrit.getFakeBranchCreatedEvent(
                'org/project1', 'stable'))
        self.waitUntilSettled()

        in_repo_conf = textwrap.dedent(
            """
            - semaphore:
                name: project1-semaphore
                max: 4
            """)
        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project1', 'stable', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertIn('does not match existing definition in branch master',
                      A.messages[0])

    def test_semaphore_branch_error_other_project(self):
        # Test that we are unable to create a semaphore with the same
        # name as another.  We're never allowed to have a semaphore with
        # the same name outside of a project.
        in_repo_conf = textwrap.dedent(
            """
            - semaphore:
                name: project1-semaphore
                max: 2
            """)
        file_dict = {'zuul.yaml': in_repo_conf}
        A = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertIn('already defined in project org/project1',
                      A.messages[0])


class TestJobOutput(AnsibleZuulTestCase):
    tenant_config_file = 'config/job-output/main.yaml'

    def _get_file(self, build, path):
        p = os.path.join(build.jobdir.root, path)
        with open(p) as f:
            return f.read()

    def test_job_output(self):
        # Verify that command standard output appears in the job output,
        # and that failures in the final playbook get logged.

        # This currently only verifies we receive output from
        # localhost.  Notably, it does not verify we receive output
        # via zuul_console streaming.
        self.executor_server.keep_jobdir = True
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='job-output', result='SUCCESS', changes='1,1'),
        ], ordered=False)

        token = 'Standard output test %s' % (self.history[0].jobdir.src_root)
        j = json.loads(self._get_file(self.history[0],
                                      'work/logs/job-output.json'))
        self.assertEqual(token,
                         j[0]['plays'][0]['tasks'][0]
                         ['hosts']['localhost']['stdout'])

        self.log.info(self._get_file(self.history[0],
                                     'work/logs/job-output.txt'))
        self.assertIn(token,
                      self._get_file(self.history[0],
                                     'work/logs/job-output.txt'))

    def test_job_output_missing_role(self):
        # Verify that ansible errors such as missing roles are part of the
        # buildlog.

        self.executor_server.keep_jobdir = True
        A = self.fake_gerrit.addFakeChange('org/project3', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='job-output-missing-role', result='FAILURE',
                 changes='1,1'),
        ], ordered=False)

        job_output = self._get_file(self.history[0],
                                    'work/logs/job-output.txt')
        self.assertIn('the role \'not_existing\' was not found', job_output)

    def test_job_output_failure_log(self):
        logger = logging.getLogger('zuul.AnsibleJob')
        output = io.StringIO()
        logger.addHandler(logging.StreamHandler(output))

        # Verify that a failure in the last post playbook emits the contents
        # of the json output to the log
        self.executor_server.keep_jobdir = True
        A = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()
        self.assertHistory([
            dict(name='job-output-failure',
                 result='POST_FAILURE', changes='1,1'),
        ], ordered=False)

        token = 'Standard output test %s' % (self.history[0].jobdir.src_root)
        j = json.loads(self._get_file(self.history[0],
                                      'work/logs/job-output.json'))
        self.assertEqual(token,
                         j[0]['plays'][0]['tasks'][0]
                         ['hosts']['localhost']['stdout'])

        self.log.info(self._get_file(self.history[0],
                                     'work/logs/job-output.json'))
        self.assertIn(token,
                      self._get_file(self.history[0],
                                     'work/logs/job-output.txt'))

        log_output = output.getvalue()
        self.assertIn('Final playbook failed', log_output)
        self.assertIn('Failure test', log_output)


class TestPlugins(AnsibleZuulTestCase):
    tenant_config_file = 'config/speculative-plugins/main.yaml'

    def _run_job(self, job_name, project='org/project', roles=''):
        # Output extra ansible info so we might see errors.
        self.executor_server.verbose = True
        conf = textwrap.dedent(
            """
            - job:
                name: {job_name}
                run: playbooks/{job_name}/test.yaml
                nodeset:
                  nodes:
                    - name: controller
                      label: whatever
                {roles}
            - project:
                check:
                  jobs:
                    - {job_name}
            """.format(job_name=job_name, roles=roles))

        file_dict = {'zuul.yaml': conf}
        A = self.fake_gerrit.addFakeChange(project, 'master', 'A',
                                           files=file_dict)
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        message = A.messages[0]
        self.assertIn('ERROR Ansible plugin dir', message)
        self.assertIn('found adjacent to playbook', message)
        self.assertIn('in non-trusted repo', message)

    def test_filter_plugin(self):
        self._run_job('filter-plugin-playbook')
        self._run_job('filter-plugin-playbook-symlink')
        self._run_job('filter-plugin-bare-role')
        self._run_job('filter-plugin-role')
        self._run_job('filter-plugin-repo-role', project='org/projectrole')
        self._run_job('filter-plugin-shared-role',
                      roles="roles: [{zuul: 'org/project2'}]")
        self._run_job('filter-plugin-shared-bare-role',
                      roles="roles: [{zuul: 'org/project3', name: 'shared'}]")


class TestNoLog(AnsibleZuulTestCase):
    tenant_config_file = 'config/ansible-no-log/main.yaml'

    def _get_file(self, build, path):
        p = os.path.join(build.jobdir.root, path)
        with open(p) as f:
            return f.read()

    def test_no_log_unreachable(self):
        # Output extra ansible info so we might see errors.
        self.executor_server.verbose = True
        self.executor_server.keep_jobdir = True

        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')

        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        json_log = self._get_file(self.history[0], 'work/logs/job-output.json')
        text_log = self._get_file(self.history[0], 'work/logs/job-output.txt')

        self.assertNotIn('my-very-secret-password-1', json_log)
        self.assertNotIn('my-very-secret-password-2', json_log)
        self.assertNotIn('my-very-secret-password-1', text_log)
        self.assertNotIn('my-very-secret-password-2', text_log)


class TestUnreachable(AnsibleZuulTestCase):
    tenant_config_file = 'config/ansible-unreachable/main.yaml'

    def _get_file(self, build, path):
        p = os.path.join(build.jobdir.root, path)
        with open(p) as f:
            return f.read()

    def test_unreachable(self):
        self.wait_timeout = 120

        # Output extra ansible info so we might see errors.
        self.executor_server.verbose = True
        self.executor_server.keep_jobdir = True

        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')

        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        # The result must be retry limit because jobs with unreachable nodes
        # will be retried.
        self.assertIn('RETRY_LIMIT', A.messages[0])
        self.assertHistory([
            dict(name='pre-unreachable', result=None, changes='1,1'),
            dict(name='pre-unreachable', result=None, changes='1,1'),
            dict(name='run-unreachable', result=None, changes='1,1'),
            dict(name='run-unreachable', result=None, changes='1,1'),
            dict(name='post-unreachable', result=None, changes='1,1'),
            dict(name='post-unreachable', result=None, changes='1,1'),
        ], ordered=False)
        unreachable_log = self._get_file(self.history[0],
                                         '.ansible/nodes.unreachable')
        self.assertEqual('fake\n', unreachable_log)


class TestJobPause(AnsibleZuulTestCase):
    tenant_config_file = 'config/job-pause/main.yaml'

    def _get_file(self, build, path):
        p = os.path.join(build.jobdir.root, path)
        with open(p) as f:
            return f.read()

    def test_job_pause(self):

        self.wait_timeout = 120

        # Output extra ansible info so we might see errors.
        self.executor_server.verbose = True
        self.executor_server.keep_jobdir = True

        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')

        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertHistory([
            dict(name='test-fail', result='FAILURE', changes='1,1'),
            dict(name='test-good', result='SUCCESS', changes='1,1'),
            dict(name='test1-after-compile1', result='SUCCESS', changes='1,1'),
            dict(name='test2-after-compile1', result='SUCCESS', changes='1,1'),
            dict(name='test-after-compile2', result='SUCCESS', changes='1,1'),
            dict(name='compile2', result='SUCCESS', changes='1,1'),
            dict(name='compile1', result='SUCCESS', changes='1,1'),
        ], ordered=False)

        # The order of some of these tests is not deterministic so check that
        # the last two are compile2, compile1 in this order.
        history_compile1 = self.history[-1]
        history_compile2 = self.history[-2]
        self.assertEqual('compile1', history_compile1.name)
        self.assertEqual('compile2', history_compile2.name)

    def test_job_node_failure_resume(self):
        self.wait_timeout = 120

        # Output extra ansible info so we might see errors.
        self.executor_server.verbose = True

        # Second node request should fail
        fail = {'_oid': '200-0000000001'}
        self.fake_nodepool.addFailRequest(fail)

        A = self.fake_gerrit.addFakeChange('org/project2', 'master', 'A')

        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertEqual([], self.builds)
        self.assertHistory([
            dict(name='just-pause', result='SUCCESS', changes='1,1'),
        ], ordered=False)

    def test_job_pause_skipped_child(self):
        """
        Tests that a paused job is resumed with externally skipped jobs.

        Tests that this situation won't lead to stuck buildsets.
        Compile pauses before pre-test fails.

        1. compile (pauses) --+
                              |
                              +--> test (skipped because of pre-test)
                              |
        2. pre-test (fails) --+
        """
        self.wait_timeout = 120
        self.executor_server.hold_jobs_in_build = True

        # Output extra ansible info so we might see errors.
        self.executor_server.verbose = True
        self.executor_server.keep_jobdir = True

        A = self.fake_gerrit.addFakeChange('org/project3', 'master', 'A')

        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.executor_server.release('compile')
        self.waitUntilSettled()

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

        self.assertHistory([
            dict(name='pre-test', result='FAILURE', changes='1,1'),
            dict(name='compile', result='SUCCESS', changes='1,1'),
        ])

        self.assertIn('test : SKIPPED', A.messages[0])

    def test_job_pause_pre_skipped_child(self):
        """
        Tests that a paused job is resumed with pre-existing skipped jobs.

        Tests that this situation won't lead to stuck buildsets.
        The pre-test fails before compile pauses so test is already skipped
        when compile pauses.

        1. pre-test (fails) --+
                              |
                              +--> test (skipped because of pre-test)
                              |
        2. compile (pauses) --+
        """
        self.wait_timeout = 120
        self.executor_server.hold_jobs_in_build = True

        # Output extra ansible info so we might see errors.
        self.executor_server.verbose = True
        self.executor_server.keep_jobdir = True

        A = self.fake_gerrit.addFakeChange('org/project3', 'master', 'A')

        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.executor_server.release('pre-test')
        self.waitUntilSettled()

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

        self.assertHistory([
            dict(name='pre-test', result='FAILURE', changes='1,1'),
            dict(name='compile', result='SUCCESS', changes='1,1'),
        ])

        self.assertIn('test : SKIPPED', A.messages[0])


class TestContainerJobs(AnsibleZuulTestCase):
    tenant_config_file = "config/container-build-resources/main.yaml"

    def test_container_jobs(self):
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        self.assertHistory([
            dict(name='container-machine', result='SUCCESS', changes='1,1'),
            dict(name='container-native', result='SUCCESS', changes='1,1'),
        ])
