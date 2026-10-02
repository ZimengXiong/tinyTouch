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

test('suggested rewrites stay out of feedback until reviewed, edited, or commented on', () => {
  const entry = {...catalog.entries[0], proposed: 'Touch with finger {finger} now.'};
  const review = {...catalog, entries: [entry]};
  assert.equal(hasFeedback(entry), false);
  assert.doesNotMatch(feedbackText(review, {general: '', entries: {}}), /TT-1/);
  assert.equal(hasFeedback(entry, {reviewed: true}), true);
  assert.match(feedbackText(review, {general: '', entries: {'TT-1': {reviewed: true}}}), /Proposed:\nTouch with finger \{finger\} now\./);
  assert.equal(hasFeedback(entry, {reviewed: true, proposed: entry.original}), false);
  assert.match(feedbackText(review, {general: '', entries: {'TT-1': {comment: 'Explain the timing.'}}}), /Explain the timing\./);
});
test('combined occurrences remain searchable and appear in every applicable category', () => {
  const entry = {...catalog.entries[0], occurrences: [{group: 'Setup', context: 'first-time setup'}]};
  assert.deepEqual(filterEntries([entry], {}, {query: 'first-time', group: 'Setup'}).map(e => e.id), ['TT-1']);
});
test('old duplicate comments and alternative edits survive draft migration', () => {
  const {restoreDraft} = require('./site/app.js');
  const entry = {...catalog.entries[0], proposed: 'Suggested rewrite.', legacyIds: ['old-1', 'old-2']};
  const migrated = restoreDraft({...catalog, entries: [entry]}, {general: 'General note', entries: {
    'old-1': {proposed: 'First edit.', comment: 'First comment'},
    'old-2': {proposed: 'Second edit.', comment: 'Second comment'},
    'short-line': {comment: 'Keep the short label.'},
  }});
  assert.equal(migrated.entries['TT-1'].proposed, 'First edit.');
  assert.match(migrated.entries['TT-1'].comment, /First comment/);
  assert.match(migrated.entries['TT-1'].comment, /Second comment/);
  assert.match(migrated.entries['TT-1'].comment, /Second edit\./);
  assert.match(migrated.general, /Keep the short label\./);
});
test('old reviewed wording does not silently approve a new suggested rewrite', () => {
  const {restoreDraft} = require('./site/app.js');
  const entry = {...catalog.entries[0], proposed: 'Suggested rewrite.', legacyIds: ['old-1']};
  const migrated = restoreDraft({...catalog, entries: [entry]}, {entries: {'old-1': {reviewed: true}}});
  assert.equal(migrated.entries['TT-1'].proposed, entry.original);
  assert.equal(hasFeedback(entry, migrated.entries['TT-1']), false);
});
test('unchanged excluded wording is not exported as an earlier edit request', () => {
  const {restoreDraft} = require('./site/app.js');
  const migrated = restoreDraft({...catalog, legacyOriginals: {'short-line': 'Back'}}, {entries: {'short-line': {proposed: 'Back', reviewed: true}}});
  assert.equal(migrated.general, '');
});
test('legacy menu numbering does not turn an unchanged draft into a copy edit', () => {
  const {restoreDraft} = require('./site/app.js');
  const entry = {...catalog.entries[0], original: 'Preview a color and effect without saving', proposed: 'Preview a color and effect without saving changes.', legacyIds: ['old-menu']};
  const original = '  1. Preview a color and effect without saving [preview]';
  const migrated = restoreDraft({...catalog, entries: [entry], legacyOriginals: {'old-menu': original}}, {entries: {'old-menu': {proposed: original, reviewed: true}}});
  assert.equal(migrated.entries['TT-1'].proposed, entry.original);
  assert.equal(hasFeedback(entry, migrated.entries['TT-1']), false);
});
