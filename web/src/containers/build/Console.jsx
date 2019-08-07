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
import ReactJson from 'react-json-view'
import {
  Icon,
  ListView,
  Row,
  Col,
  Modal,
} from 'patternfly-react'


const INTERESTING_KEYS = ['msg', 'stdout', 'stderr']


function didTaskFail(task) {
  if (task.failed) {
    return true
  }
  if ('failed_when_result' in task && !task.failed_when_result) {
    return false
  }
  if ('rc' in task && task.rc) {
    return true
  }
  return false
}

function hostTaskStats (state, host) {
  if (didTaskFail(host)) { state.failed += 1}
  else if (host.changed) { state.changed += 1}
  else if (host.skip_reason) { state.skipped += 1}
  else { state.ok += 1}
}

class TaskOutput extends React.Component {
  static propTypes = {
    data: PropTypes.object,
    include: PropTypes.array,
  }

  findLoopLabel(item) {
    const label = item._ansible_item_label
    if (typeof(label) === 'string') {
      return label
    }
    return ''
  }

  renderResults(value) {
    return (
      <div key='results'>
        <h3 key='results-header'>results</h3>
        {value.map((result, idx) => (
          <div className='zuul-console-task-result' key={idx}>
            <h2 key={idx}>{idx}: {this.findLoopLabel(result)}</h2>
            {Object.entries(result).map(([key, value]) => (
              this.renderData(key, value, true)
            ))}
          </div>
        ))}
      </div>
    )
  }

  renderData(key, value, ignore_underscore) {
    let ret
    if (ignore_underscore && key[0] === '_') {
      return (<React.Fragment key={key}/>)
    }
    if (this.props.include) {
      if (!this.props.include.includes(key)) {
        return (<React.Fragment key={key}/>)
      }
      if (value === '') {
        return (<React.Fragment key={key}/>)
      }
    }
    if (value === null) {
      ret = (
        <pre>
          null
        </pre>
      )
    } else if (typeof(value) === 'string') {
      ret = (
        <pre>
          {value}
        </pre>
      )
    } else if (typeof(value) === 'object') {
      ret = (
        <pre>
          <ReactJson
            src={value}
            name={null}
            sortKeys={true}
            enableClipboard={false}
            displayDataTypes={false}/>
        </pre>
      )
    } else {
      ret = (
        <pre>
          {value.toString()}
        </pre>
      )
    }

    return (
      <div key={key}>
        {ret && <h3>{key}</h3>}
        {ret && ret}
      </div>
    )
  }

  render () {
    const { data } = this.props

    return (
      <React.Fragment>
        {Object.entries(data).map(([key, value]) => (
          key==='results'?this.renderResults(value):this.renderData(key, value)
        ))}
      </React.Fragment>
    )
  }
}

class HostTask extends React.Component {
  static propTypes = {
    hostname: PropTypes.string,
    task: PropTypes.object,
    host: PropTypes.object,
    errorIds: PropTypes.object,
  }

  state = {
    showModal: false,
    failed: 0,
    changed: 0,
    skipped: 0,
    ok: 0
  }

  open = () => {
    this.setState({ showModal: true})
  }

  close = () => {
    this.setState({ showModal: false})
  }

  constructor (props) {
    super(props)

    const { host } = this.props

    hostTaskStats(this.state, host)
  }

  render () {
    const { hostname, task, host, errorIds } = this.props

    const ai = []
    if (this.state.skipped) {
      ai.push(
        <ListView.InfoItem key="skipped" title="Click for details">
          <span className="task-skipped" onClick={this.open}>SKIPPED</span>
        </ListView.InfoItem>)
    }
    if (this.state.changed) {
      ai.push(
        <ListView.InfoItem key="changed" title="Click for details">
          <span className="task-changed" onClick={this.open}>CHANGED</span>
        </ListView.InfoItem>)
    }
    if (this.state.failed) {
      ai.push(
        <ListView.InfoItem key="failed" title="Click for details">
          <span className="task-failed" onClick={this.open}>FAILED</span>
        </ListView.InfoItem>)
    }
    if (this.state.ok) {
      ai.push(
        <ListView.InfoItem key="ok" title="Click for details">
          <span className="task-ok" onClick={this.open}>OK</span>
        </ListView.InfoItem>)
    }
    ai.push(
      <ListView.InfoItem key="hostname">
        <Icon type='pf' name='container-node' />
        {hostname}
      </ListView.InfoItem>
    )

    const expand = errorIds.has(task.task.id)
    let name = task.task.name
    if (!name) {
      name = host.action
    }
    if (task.role) {
      name = task.role.name + ': ' + name
    }
    return (
      <React.Fragment>
        <ListView.Item
          key='header'
          heading={name}
          initExpanded={expand}
          additionalInfo={ai}
        >
         <Row>
           <Col sm={11}>
             <pre>
               <TaskOutput data={this.props.host} include={INTERESTING_KEYS}/>
             </pre>
           </Col>
         </Row>
        </ListView.Item>
        <Modal key='modal' show={this.state.showModal} onHide={this.close}
               dialogClassName="zuul-console-task-detail">
          <Modal.Header>
            <button
              className="close"
              onClick={this.close}
              aria-hidden="true"
              aria-label="Close"
            >
              <Icon type="pf" name="close" />
            </button>
            <Modal.Title>{hostname}</Modal.Title>
          </Modal.Header>
          <Modal.Body>
            <TaskOutput data={host}/>
          </Modal.Body>
        </Modal>
      </React.Fragment>
    )
  }
}

class PlayBook extends React.Component {
  static propTypes = {
    playbook: PropTypes.object,
    errorIds: PropTypes.object,
  }

  render () {
    const { playbook, errorIds } = this.props

    const expandAll = (playbook.phase === 'run')
    const expand = (expandAll || errorIds.has(playbook.phase + playbook.index))

    const ai = []
    if (playbook.trusted) {
      ai.push(
        <ListView.InfoItem key="trusted" title="This playbook runs in a trusted execution context, which permits executing code on the Zuul executor and allows access to all Ansible features.">
          <Icon type='pf' name='info' /> Trusted
        </ListView.InfoItem>
      )
    }

    return (
      <ListView.Item
        stacked={true}
        additionalInfo={ai}
        initExpanded={expand}
        heading={playbook.phase[0].toUpperCase() + playbook.phase.slice(1) + ' playbook'}
        description={playbook.playbook}
      >
          {playbook.plays.map((play, idx) => (
            <React.Fragment key={idx}>
              <Row key='play'>
                <Col sm={12}>
                  <strong>Play: {play.play.name}</strong>
                </Col>
              </Row>
              {play.tasks.map((task, idx2) => (
                Object.entries(task.hosts).map(([hostname, host]) => (
                  <Row key={idx2+hostname}>
                    <Col sm={12}>
                      <HostTask hostname={hostname}
                                task={task} host={host} errorIds={errorIds}/>
                    </Col>
                  </Row>
                ))))}
            </React.Fragment>
          ))}
      </ListView.Item>
    )
  }
}

class Console extends React.Component {
  static propTypes = {
    output: PropTypes.array,
  }

  constructor (props) {
    super(props)

    const { output } = this.props

    const errorIds = new Set()
    this.errorIds = errorIds

    // Identify all of the hosttasks (and therefore tasks, plays, and
    // playbooks) which have failed.  The errorIds are either task or
    // play uuids, or the phase+index for the playbook.  Since they are
    // different formats, we can store them in the same set without
    // collisions.
    output.forEach(playbook => {
      playbook.plays.forEach(play => {
        play.tasks.forEach(task => {
          Object.entries(task.hosts).forEach(([, host]) => {
            if (host.results) {
              host.results.forEach(result => {
                if (didTaskFail(result)) {
                  errorIds.add(task.task.id)
                  errorIds.add(play.play.id)
                  errorIds.add(playbook.phase + playbook.index)
                }
              })
            }
            if (didTaskFail(host)) {
              errorIds.add(task.task.id)
              errorIds.add(play.play.id)
              errorIds.add(playbook.phase + playbook.index)
            }
          })
        })
      })
    })
  }

  render () {
    const { output } = this.props

    return (
      <React.Fragment>
        <ListView key="playbooks" className="zuul-console">
          {output.map((playbook, idx) => (
            <PlayBook key={idx} playbook={playbook} errorIds={this.errorIds}/>))}
        </ListView>
      </React.Fragment>
    )
  }
}


export default Console
