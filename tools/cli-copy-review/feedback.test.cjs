'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {hasFeedback, filterEntries, feedbackText} = require('./site/app.js');
const catalog = {commit: 'source123', version: '0.1.31', entries: [
  {id: 'TT-1', original: 'Touch {finger} now.', group: 'Enrollment', context: 'enroll / touch'},
  {id: 'TT-2', original: 'Lift your finger.', group: 'Enrollment', context: 'enroll / lift'},
  {id: 'TT-3', original: 'LED_IDLE_COLOR', group: 'Technical appendix', context: 'device token'},
]};
test('feedback includes edits and comments across groups, preserving placeholders and multiline comments', () => {
  const state = {general: 'Prefer direct instructions.', entries: {
    'TT-1': {proposed: 'Touch with finger {finger}.', comment: 'Keep the placeholder.\nUse this in setup too.'},
    'TT-3': {comment: 'Explain this token in diagnostics.'},
  }};
  const text = feedbackText(catalog, state);
  assert.match(text, /Snapshot: source123/);
  assert.match(text, /General comment:\nPrefer direct instructions\./);
  assert.match(text, /Proposed:\nTouch with finger \{finger\}\./);
  assert.match(text, /Keep the placeholder\.\nUse this in setup too\./);
  assert.match(text, /TT-3/);
  assert.doesNotMatch(text, /TT-2/);
});
test('empty proposed text explicitly requests removing a line', () => {
  const state = {general: '', entries: {'TT-1': {proposed: ''}}};
  assert.equal(hasFeedback(catalog.entries[0], state.entries['TT-1']), true);
  assert.match(feedbackText(catalog, state), /Proposed:\n\[remove this line\]/);
});
test('reviewed marks, unchanged proposed copy and whitespace-only comments do not become edit requests', () => {
  const draft = {proposed: catalog.entries[0].original, reviewed: true, comment: ' \n '};
  assert.equal(hasFeedback(catalog.entries[0], draft), false);
  assert.doesNotMatch(feedbackText(catalog, {general: '', entries: {'TT-1': draft}}), /TT-1/);
});
test('search finds comments and proposed copy while respecting group, review and technical filters', () => {
  const drafts = {'TT-1': {comment: 'Need a warmer tone', reviewed: true}, 'TT-3': {proposed: 'Idle ring color'}};
  assert.deepEqual(filterEntries(catalog.entries, drafts, {query: 'warmer tone', status: 'reviewed'}).map(e => e.id), ['TT-1']);
  assert.deepEqual(filterEntries(catalog.entries, drafts, {query: 'idle ring'}), []);
  assert.deepEqual(filterEntries(catalog.entries, drafts, {query: 'idle ring', technical: true}).map(e => e.id), ['TT-3']);
  assert.deepEqual(filterEntries(catalog.entries, drafts, {group: 'Enrollment', status: 'unreviewed'}).map(e => e.id), ['TT-2']);
});
test('JSON round trip retains a manual draft without changing the catalog', () => {
  const before = JSON.stringify(catalog);
  const state = {general: 'Overall comment', entries: {'TT-2': {proposed: 'Lift your finger now.', comment: '<script>literal text</script>', reviewed: true}}};
  const restored = JSON.parse(JSON.stringify(state));
  assert.equal(feedbackText(catalog, restored), feedbackText(catalog, state));
  assert.equal(JSON.stringify(catalog), before);
  assert.match(feedbackText(catalog, state), /<script>literal text<\/script>/);
});
