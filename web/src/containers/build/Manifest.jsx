// Copyright 2019 Red Hat, Inc
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

import React from 'react'
import PropTypes from 'prop-types'
import {
  TreeView,
} from 'patternfly-react'
import { Link } from 'react-router-dom'

const renderTree = (tenant, build, path, obj) => {
  const node = {}
  let name = obj.name
  var log_prefix : string

  if ('children' in obj && obj.children) {
    node.nodes = obj.children.map(n => renderTree(tenant, build, path+'/'+obj.name+'/', n))
  }
  if (obj.mimetype === 'application/directory') {
    name = obj.name + '/'
  } else {
    node.icon = 'fa fa-file-o'
  }
  if (path === '') {
    log_prefix = '/log/'
  } else {
    log_prefix = '/log'
  }
  if (obj.mimetype === 'text/plain') {
    node.text = (
      <span>
        <Link to={tenant.linkPrefix + '/build/' + build.uuid + log_prefix + path + name}>{obj.name}</Link>
        &nbsp;&nbsp;
        (<a href={build.log_url + path + name}>raw</a>
        &nbsp;<span className="fa fa-external-link"/>)
      </span>)
  } else {
    node.text = (
      <span>
        <a href={build.log_url + path + name}>{obj.name}</a>
        &nbsp;<span className="fa fa-external-link"/>
      </span>
    )
  }
  return node
}

class Manifest extends React.Component {
  static propTypes = {
    tenant: PropTypes.object.isRequired,
    build: PropTypes.object.isRequired
  }

  render() {
    const { tenant, build } = this.props

    const nodes = build.manifest.tree.map(n => renderTree(tenant, build, '', n))

    return (
      <React.Fragment>
        <br/>
        <div className="tree-view-container">
          <TreeView
            nodes={nodes}
          />
        </div>
      </React.Fragment>
    )
  }
}

export default Manifest
