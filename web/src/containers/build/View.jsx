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

import * as React from 'react'
import PropTypes from 'prop-types'
import { connect } from 'react-redux'
import { Panel } from 'react-bootstrap'

const linkify = (ln, num) => {
  return (<a name={num} href={'#'+num}>{ln}</a>)
}

class View extends React.Component {
  static propTypes = {
    build: PropTypes.object,
    item: PropTypes.object,
    tenant: PropTypes.object,
    data: PropTypes.object,
  }

  render () {
    const { build, data } = this.props

    return (
      <Panel>
        <Panel.Heading>Build result {build.uuid}</Panel.Heading>
        <Panel.Body>
          <pre className="zuul-log-output">
            {data.split(/\r?\n/).map((line, idx) => (
              <span key={idx}>
                {linkify(line, idx)}{'\n'}
              </span>
            ))}
          </pre>
        </Panel.Body>
      </Panel>
    )
  }
}


export default connect(state => ({tenant: state.tenant}))(View)
