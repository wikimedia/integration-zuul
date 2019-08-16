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
import { Link } from 'react-router-dom'


function updateSelection (event) {
  const lines = window.location.hash.substring(1).split('-').map(Number)
  const lineClicked = Number(event.currentTarget.innerText)
  if (!event.shiftKey || lines.length === 0) {
    // First line clicked
    lines[0] = [lineClicked]
    lines.splice(1, 1)
  } else {
    // Second line shift-clicked
    const distances = lines.map((pos) => (Math.abs(lineClicked - pos)))
    // Adjust the range based on the edge distance
    if (distances[0] < distances[1]) {
      lines[0] = lineClicked
    } else {
      lines[1] = lineClicked
    }
  }
  window.location.hash = '#' + lines.sort().join('-')
}


class LogFile extends React.Component {
  static propTypes = {
    build: PropTypes.object,
    item: PropTypes.object,
    tenant: PropTypes.object,
    data: PropTypes.array,
    severity: PropTypes.string
  }

  render () {
    const { build, data, severity } = this.props
    return (
      <React.Fragment>
        <Panel>
          <Panel.Heading>Build result {build.uuid}</Panel.Heading>
          <Panel.Body>
            <Link to="?">All</Link>&nbsp;
            <Link to="?severity=1">Debug</Link>&nbsp;
            <Link to="?severity=2">Info</Link>&nbsp;
            <Link to="?severity=3">Warning</Link>&nbsp;
            <Link to="?severity=4">Error</Link>&nbsp;
            <Link to="?severity=5">Trace</Link>&nbsp;
            <Link to="?severity=6">Audit</Link>&nbsp;
            <Link to="?severity=7">Critical</Link>&nbsp;
          </Panel.Body>
        </Panel>
        <pre className="zuul-log-output">
          <table>
            <tbody>
              {data.map((line) => (
                ((!severity || (line.severity >= severity)) &&
                 <tr key={line.index} className={'ln-' + line.index}>
                   <td className="line-number" onClick={updateSelection}>
                     {line.index}
                   </td>
                   <td>
                     <span className={'zuul-log-sev-'+(line.severity||0)}>
                       {line.text+'\n'}
                     </span>
                   </td>
                 </tr>
                )))}
            </tbody>
          </table>
        </pre>
      </React.Fragment>
    )
  }
}


export default connect(state => ({tenant: state.tenant}))(LogFile)
