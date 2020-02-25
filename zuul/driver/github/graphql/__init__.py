# Copyright 2020 BMW Group
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

import pathspec
from pkg_resources import resource_string


def nested_get(d, *keys, default=None):
    temp = d
    for key in keys[:-1]:
        temp = temp.get(key, {}) if temp is not None else None
    return temp.get(keys[-1], default) if temp is not None else default


class GraphQLClient:
    log = logging.getLogger('zuul.github.graphql')

    def __init__(self, url):
        self.url = url
        self.queries = {}
        self._load_queries()

    def _load_queries(self):
        self.log.debug('Loading prepared graphql queries')
        query_names = [
            'canmerge',
        ]
        for query_name in query_names:
            self.queries[query_name] = resource_string(
                __name__, '%s.graphql' % query_name).decode('utf-8')

    @staticmethod
    def _prepare_query(query, variables):
        data = {
            'query': query,
            'variables': variables,
        }
        return data

    def _fetch_canmerge(self, github, owner, repo, pull, sha):
        variables = {
            'zuul_query': 'canmerge',  # used for logging
            'owner': owner,
            'repo': repo,
            'pull': pull,
            'head_sha': sha,
        }
        query = self._prepare_query(self.queries['canmerge'], variables)
        response = github.session.post(self.url, json=query)
        return response.json()

    def fetch_canmerge(self, github, change):
        owner, repo = change.project.name.split('/')

        data = self._fetch_canmerge(github, owner, repo, change.number,
                                    change.patchset)
        result = {}

        repository = nested_get(data, 'data', 'repository')
        # Find corresponding rule to our branch
        rules = nested_get(repository, 'branchProtectionRules', 'nodes',
                           default=[])
        matching_rule = None
        for rule in rules:
            pattern = pathspec.patterns.GitWildMatchPattern(
                rule.get('pattern'))
            match = pathspec.match_files([pattern], [change.branch])
            if match:
                matching_rule = rule
                break

        # If there is a matching rule, get required status checks
        if matching_rule:
            result['requiredStatusCheckContexts'] = matching_rule.get(
                'requiredStatusCheckContexts', [])
            result['requiresApprovingReviews'] = matching_rule.get(
                'requiresApprovingReviews')
            result['requiresCodeOwnerReviews'] = matching_rule.get(
                'requiresCodeOwnerReviews')
        else:
            result['requiredStatusCheckContexts'] = []

        # Check for draft
        pull_request = nested_get(repository, 'pullRequest')
        result['isDraft'] = nested_get(pull_request, 'isDraft', default=False)

        # Add status checks
        result['status'] = {}
        commit = nested_get(data, 'data', 'repository', 'object')
        # Status can be explicit None so make sure we work with a dict
        # afterwards
        status = commit.get('status') or {}
        for context in status.get('contexts', []):
            result['status'][context['context']] = context['state']

        # Add check runs
        for suite in nested_get(commit, 'checkSuites', 'nodes', default=[]):
            for run in nested_get(suite, 'checkRuns', 'nodes', default=[]):
                result['status'][run['name']] = run['conclusion']

        return result
