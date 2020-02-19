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

from zuul.model import Project
from zuul.source import BaseSource

from zuul.driver.gitlab.gitlabmodel import GitlabRefFilter


class GitlabSource(BaseSource):
    name = 'gitlab'
    log = logging.getLogger("zuul.source.GitlabSource")

    def __init__(self, driver, connection, config=None):
        hostname = connection.canonical_hostname
        super(GitlabSource, self).__init__(driver, connection,
                                           hostname, config)

    def getRefSha(self, project, ref):
        """Return a sha for a given project ref."""
        raise NotImplementedError()

    def waitForRefSha(self, project, ref, old_sha=''):
        """Block until a ref shows up in a given project."""
        raise NotImplementedError()

    def isMerged(self, change, head=None):
        """Determine if change is merged."""
        raise NotImplementedError()

    def canMerge(self, change, allow_needs, event=None):
        """Determine if change can merge."""
        raise NotImplementedError()

    def postConfig(self):
        """Called after configuration has been processed."""
        raise NotImplementedError()

    def getChange(self, event, refresh=False):
        return self.connection.getChange(event, refresh)

    def getChangeByURL(self, url):
        raise NotImplementedError()

    def getChangesDependingOn(self, change, projects, tenant):
        raise NotImplementedError()

    def getCachedChanges(self):
        return self.connection._change_cache.values()

    def getProject(self, name):
        p = self.connection.getProject(name)
        if not p:
            p = Project(name, self)
            self.connection.addProject(p)
        return p

    def getProjectBranches(self, project, tenant):
        return self.connection.getProjectBranches(project, tenant)

    def getProjectOpenChanges(self, project):
        """Get the open changes for a project."""
        raise NotImplementedError()

    def updateChange(self, change, history=None):
        """Update information for a change."""
        raise NotImplementedError()

    def getGitUrl(self, project):
        """Get the git url for a project."""
        return self.connection.getGitUrl(project)

    def getGitwebUrl(self, project, sha=None):
        """Get the git-web url for a project."""
        raise NotImplementedError()

    def getRequireFilters(self, config):
        f = GitlabRefFilter()
        return [f]

    def getRejectFilters(self, config):
        raise NotImplementedError()

    def getRefForChange(self, change):
        raise NotImplementedError()


# Require model
def getRequireSchema():
    require = {}
    return require


def getRejectSchema():
    reject = {}
    return reject
