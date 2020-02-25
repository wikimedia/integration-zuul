# Copyright 2019 BMW Group
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

from graphene import Boolean, Field, Int, List, ObjectType, String


class FakePageInfo(ObjectType):
    end_cursor = String()
    has_next_page = Boolean()

    def resolve_end_cursor(parent, info):
        return 'testcursor'

    def resolve_has_next_page(parent, info):
        return False


class FakeBranchProtectionRule(ObjectType):
    pattern = String()
    requiredStatusCheckContexts = List(String)
    requiresApprovingReviews = Boolean()
    requiresCodeOwnerReviews = Boolean()

    def resolve_pattern(parent, info):
        return parent.pattern

    def resolve_requiredStatusCheckContexts(parent, info):
        return parent.required_contexts

    def resolve_requiresApprovingReviews(parent, info):
        return parent.require_reviews

    def resolve_requiresCodeOwnerReviews(parent, info):
        return parent.require_codeowners_review


class FakeBranchProtectionRules(ObjectType):
    nodes = List(FakeBranchProtectionRule)

    def resolve_nodes(parent, info):
        return parent.values()


class FakeStatusContext(ObjectType):
    state = String()
    context = String()

    def resolve_state(parent, info):
        state = parent.state.upper()
        return state

    def resolve_context(parent, info):
        return parent.context


class FakeStatus(ObjectType):
    contexts = List(FakeStatusContext)

    def resolve_contexts(parent, info):
        return parent


class FakeCheckRun(ObjectType):
    name = String()
    conclusion = String()

    def resolve_name(parent, info):
        return parent.name

    def resolve_conclusion(parent, info):
        return parent.conclusion.upper()


class FakeCheckRuns(ObjectType):
    nodes = List(FakeCheckRun)

    def resolve_nodes(parent, info):
        return parent


class FakeCheckSuite(ObjectType):
    checkRuns = Field(FakeCheckRuns, first=Int())

    def resolve_checkRuns(parent, info, first=None):
        return parent


class FakeCheckSuites(ObjectType):

    nodes = List(FakeCheckSuite)

    def resolve_nodes(parent, info):
        # Note: we only use a single check suite in the tests so return a
        # single item to keep it simple.
        return [parent]


class FakeCommit(ObjectType):

    class Meta:
        # Graphql object type that defaults to the class name, but we require
        # 'Commit'.
        name = 'Commit'

    status = Field(FakeStatus)
    checkSuites = Field(FakeCheckSuites, first=Int())

    def resolve_status(parent, info):
        seen = set()
        result = []
        for status in parent._statuses:
            if status.context not in seen:
                seen.add(status.context)
                result.append(status)
        # Github returns None if there are no results
        return result or None

    def resolve_checkSuites(parent, info, first=None):
        # Tests only utilize one check suite so return all runs for that.
        return parent._check_runs


class FakePullRequest(ObjectType):
    isDraft = Boolean()

    def resolve_isDraft(parent, info):
        return parent.draft


class FakeRepository(ObjectType):
    name = String()
    branchProtectionRules = Field(FakeBranchProtectionRules, first=Int())
    pullRequest = Field(FakePullRequest, number=Int(required=True))
    object = Field(FakeCommit, expression=String(required=True))

    def resolve_name(parent, info):
        org, name = parent.name.split('/')
        return name

    def resolve_branchProtectionRules(parent, info, first):
        return parent._branch_protection_rules

    def resolve_pullRequest(parent, info, number):
        return parent.data.pull_requests.get(number)

    def resolve_object(parent, info, expression):
        return parent._commits.get(expression)


class FakeGithubQuery(ObjectType):
    repository = Field(FakeRepository, owner=String(required=True),
                       name=String(required=True))

    def resolve_repository(root, info, owner, name):
        return info.context.repos.get((owner, name))
