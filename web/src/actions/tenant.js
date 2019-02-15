// Copyright 2018 Red Hat, Inc
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may
// not use this file except in compliance with the License. You may obtain
// a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
// WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
// License for the specific language governing permissions and limitations
// under the License.

export const TENANT_SET = 'TENANT_SET'

export function setTenantAction (name, whiteLabel) {
  let apiPrefix = ''
  let linkPrefix = ''
  let routePrefix = ''
  let defaultRoute = '/status'
  if (!whiteLabel) {
    apiPrefix = 'tenant/' + name + '/'
    linkPrefix = '/t/' + name
    routePrefix = '/t/:tenant'
    defaultRoute = '/tenants'
  }
  return {
    type: TENANT_SET,
    tenant: {
      name: name,
      whiteLabel: whiteLabel,
      defaultRoute: defaultRoute,
      linkPrefix: linkPrefix,
      apiPrefix: apiPrefix,
      routePrefix: routePrefix
    }
  }
}
