# Copyright 2014 Antoine "hashar" Musso
# Copyright 2014 Wikimedia Foundation Inc.
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

import git
import logging
import os
import re
import yaml

from git import GitCommandError
from zuul.lib.clonemapper import CloneMapper
from zuul.merger.merger import Repo


class Cloner(object):
    log = logging.getLogger("zuul.Cloner")

    def __init__(self, args):
        self.args = args
        self.clone_map = []
        self.dests = None

        self.git_url = self.args.gitbaseurl
        self.zuul_url = self.args.zuul_url

        if self.args.clone_map_file:
            self.read_clone_map()

    def read_clone_map(self):
        clone_map_file = os.path.expanduser(self.args.clone_map_file)
        if not os.path.exists(clone_map_file):
            raise Exception("Unable to read clone map file at %s." %
                            clone_map_file)
        clone_map_file = open(self.args.clone_map_file)
        self.clone_map = yaml.load(clone_map_file).get('clonemap')
        self.log.info("Loaded map containing %s rules", len(self.clone_map))
        return self.clone_map

    def execute(self):
        mapper = CloneMapper(self.clone_map, self.args.projects)
        dests = mapper.expand(workspace=self.args.workspace)

        self.log.info("Preparing %s repositories", len(dests))
        for project, dest in dests.iteritems():
            self.prepare_repo(project, dest)
        self.log.info("Prepared all repositories")

    def clone_upstream(self, project, dest):
        git_upstream = '%s/%s' % (self.git_url, project)
        self.log.info("Creating repo %s from upstream %s",
                      project, git_upstream)
        repo = Repo(
            remote=git_upstream,
            local=dest,
            email=None,
            username=None)

        if not repo.isInitialized():
            raise Exception("Error cloning %s to %s" % (git_upstream, dest))

        return repo

    def fetch_from_zuul(self, repo, project, ref):
        zuul_remote = '%s/%s' % (self.zuul_url, project)

        try:
            repo.fetch_from(zuul_remote, ref)
            self.log.debug("Fetched ref %s from %s", ref, project)
            return True
        except (ValueError, GitCommandError):
            self.log.debug("Project %s in Zuul does not have ref %s",
                           project, ref)
            return False

    def prepare_repo(self, project, dest):
        """Clone a repository for project at dest and apply a reference
        suitable for testing. The reference lookup is attempted in this order:

         1) Zuul reference for the indicated branch
         2) Zuul reference for the master branch
         3) The tip of the indicated branch
         4) The tip of the master branch
        """

        repo = self.clone_upstream(project, dest)

        repo.update()
        repo.prune()

        override_zuul_ref = self.args.zuul_ref
        # FIXME should be origin HEAD branch which might not be 'master'
        fallback_branch = 'master'
        fallback_zuul_ref = re.sub(self.args.zuul_branch, fallback_branch,
                                   self.args.zuul_ref)

        if self.args.branch:
            override_zuul_ref = re.sub(self.args.zuul_branch, self.args.branch,
                                       self.args.zuul_ref)
            if repo.has_branch(self.args.branch):
                self.log.debug("upstream repo has branch %s", self.args.branch)
                fallback_branch = self.args.branch
                fallback_zuul_ref = self.args.zuul_ref
            else:
                self.log.exception("upstream repo is missing branch %s",
                                   self.args.branch)

        if (self.fetch_from_zuul(repo, project, override_zuul_ref)
                or self.fetch_from_zuul(repo, project, fallback_zuul_ref)):
            # Work around a bug in GitPython which can not parse FETCH_HEAD
            gitcmd = git.Git(dest)
            fetch_head = gitcmd.rev_parse('FETCH_HEAD')
            repo.checkout(fetch_head)
            self.log.info("Prepared %s repo with commit %s",
                          project, fetch_head)
        else:
            # Checkout branch
            self.log.debug("Fallbacking to branch %s", fallback_branch)
            try:
                repo.checkout('remotes/origin/%s' % fallback_branch)
            except (ValueError, GitCommandError):
                self.log.exception("Fallback branch not found: %s",
                                   fallback_branch)
            self.log.info("Prepared %s repo with branch %s",
                          project, fallback_branch)
