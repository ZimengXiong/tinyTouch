'use strict';

// These helpers run in Node as well as in the page, without browser automation.
function hasFeedback(entry, draft = {}) {
  return (typeof draft.proposed === 'string' && draft.proposed !== entry.original) || Boolean((draft.comment || '').trim());
}
function filterEntries(entries, drafts, {group = '', query = '', status = 'all', technical = false} = {}) {
  const words = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  return entries.filter(entry => {
    if (group && entry.group !== group) return false;
    if (!technical && entry.group === 'Technical appendix') return false;
    const draft = drafts[entry.id] || {};
    if (status === 'feedback' && !hasFeedback(entry, draft)) return false;
    if (status === 'reviewed' && !draft.reviewed) return false;
    if (status === 'unreviewed' && draft.reviewed) return false;
    const searchable = [entry.id, entry.original, entry.context, entry.group, draft.proposed || '', draft.comment || ''].join(' ').toLocaleLowerCase();
    return words.every(word => searchable.includes(word));
  });
}
function feedbackText(catalog, state) {
  const lines = ['tinyTouch CLI copy feedback', `Snapshot: ${catalog.commit} · CLI ${catalog.version}`, ''];
  if ((state.general || '').trim()) lines.push('General comment:', state.general.trim(), '');
  for (const entry of catalog.entries) {
    const draft = state.entries[entry.id] || {};
    if (!hasFeedback(entry, draft)) continue;
    lines.push(`[${entry.id}] ${entry.group} / ${entry.context}`, 'Original:', entry.original);
    if (typeof draft.proposed === 'string' && draft.proposed !== entry.original) {
      lines.push('Proposed:', draft.proposed === '' ? '[remove this line]' : draft.proposed);
    }
    if ((draft.comment || '').trim()) lines.push('Comment:', draft.comment.trim());
    lines.push('');
  }
  return lines.join('\n');
}
if (typeof module !== 'undefined' && module.exports) module.exports = {hasFeedback, filterEntries, feedbackText};

if (typeof document !== 'undefined') {
  const $ = id => document.getElementById(id);
  let catalog;
  let state = {entries: {}, general: ''};
  let activeGroup = '';
  let page = 0;
  let storageKey;
  let storageAvailable = true;
  let queryTimer;
  let toastTimer;
  const pageSize = 35;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function toast(message) {
    $('toast').textContent = message;
    $('toast').hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4500);
  }
  function persist() {
    try {
      localStorage.setItem(storageKey, JSON.stringify(state));
      $('save-state').textContent = 'Draft saved in this browser';
    } catch (_) {
      if (storageAvailable) toast('Browser storage is unavailable. Copy or download your feedback before leaving.');
      storageAvailable = false;
      $('save-state').textContent = 'Draft in memory — copy before leaving';
    }
  }
  function updateCounts() {
    const count = catalog.entries.filter(entry => hasFeedback(entry, state.entries[entry.id])).length;
    $('feedback-count').textContent = count.toLocaleString();
    $('reviewed-count').textContent = catalog.entries.filter(entry => state.entries[entry.id]?.reviewed).length.toLocaleString();
    const ready = count > 0 || Boolean(state.general.trim());
    $('copy-feedback').disabled = !ready;
    $('download-feedback').disabled = !ready;
  }
  function updateEntry(entry, field, value, card) {
    state.entries[entry.id] = {...state.entries[entry.id], [field]: value};
    card.classList.toggle('has-feedback', hasFeedback(entry, state.entries[entry.id]));
    persist();
    updateCounts();
    // Do not rerender while typing: keep cursor, selection and textarea height intact.
  }
  function field(labelText, id, value, rows, placeholder) {
    const wrapper = element('div');
    const label = element('label', 'field-label', labelText);
    label.htmlFor = id;
    const input = element('textarea');
    input.id = id;
    input.rows = rows;
    input.value = value;
    if (placeholder) input.placeholder = placeholder;
    wrapper.append(label, input);
    return {wrapper, input};
  }
  function renderCard(entry) {
    const draft = state.entries[entry.id] || {};
    const card = element('article', 'entry');
    card.classList.toggle('has-feedback', hasFeedback(entry, draft));
    const header = element('div', 'entry-header');
    const context = element('div', 'entry-context');
    const meta = element('div', 'entry-meta');
    meta.append(element('span', 'entry-id', entry.id), element('span', 'badge', entry.group));
    if (entry.kind !== 'copy') meta.append(element('span', 'badge', entry.kind));
    context.append(meta, element('h3', '', entry.context));
    const reviewedLabel = element('label', 'review-toggle');
    const reviewed = element('input');
    reviewed.type = 'checkbox';
    reviewed.checked = Boolean(draft.reviewed);
    reviewed.setAttribute('aria-label', `Mark ${entry.id} reviewed`);
    reviewed.addEventListener('change', () => updateEntry(entry, 'reviewed', reviewed.checked, card));
    reviewedLabel.append(reviewed, document.createTextNode('Reviewed'));
    header.append(context, reviewedLabel);
    const body = element('div', 'entry-body');
    const original = element('div', 'original-column');
    original.append(element('span', 'field-label', 'Original text'), element('pre', 'original', entry.original));
    const proposed = field('Your proposed text', `proposed-${entry.id}`, draft.proposed ?? entry.original, 2);
    proposed.wrapper.className = 'edit-column';
    proposed.input.spellcheck = true;
    proposed.input.addEventListener('input', () => updateEntry(entry, 'proposed', proposed.input.value, card));
    const comment = field('Comment', `comment-${entry.id}`, draft.comment || '', 1, 'Leave a note, question, or instruction for this line…');
    comment.wrapper.className = 'comment-field';
    comment.input.addEventListener('input', () => updateEntry(entry, 'comment', comment.input.value, card));
    body.append(original, proposed.wrapper, comment.wrapper);
    const footer = element('div', 'entry-footer');
    footer.append(element('small', '', entry.source || 'Generated CLI output'));
    const restore = element('button', '', 'Restore original text');
    restore.addEventListener('click', () => {
      proposed.input.value = entry.original;
      updateEntry(entry, 'proposed', entry.original, card);
    });
    footer.append(restore);
    card.append(header, body, footer);
    return card;
  }
  function filters() {
    return {group: activeGroup, query: $('search').value, status: $('status-filter').value, technical: $('include-technical').checked};
  }
  function renderEntries() {
    const entries = filterEntries(catalog.entries, state.entries, filters());
    const pages = Math.max(1, Math.ceil(entries.length / pageSize));
    page = Math.min(page, pages - 1);
    const start = page * pageSize;
    const end = Math.min(start + pageSize, entries.length);
    $('entries').replaceChildren(...entries.slice(start, end).map(renderCard));
    $('group-title').textContent = activeGroup || 'All copy';
    $('result-count').textContent = `${entries.length.toLocaleString()} matching lines${entries.length ? ` · showing ${start + 1}–${end}` : ''}`;
    $('page-info').textContent = `${page + 1} / ${pages}`;
    $('previous').disabled = page === 0;
    $('next').disabled = page === pages - 1;
    $('empty').hidden = entries.length !== 0;
    for (const button of $('groups').children) button.classList.toggle('active', button.dataset.group === activeGroup);
  }
  function renderNavigation() {
    const counts = new Map();
    for (const entry of catalog.entries) counts.set(entry.group, (counts.get(entry.group) || 0) + 1);
    const groups = ['Interactive menus', 'Settings and lighting', 'Fingerprint enrollment', 'Setup and HID',
      'PIV and pairing', 'Fingerprints, computers and reset', 'Connection and shared output', 'Errors and validation',
      'Updates and downloads', 'Command help', 'Argument errors', 'Branding and layout', 'Installer', 'Helper and native errors',
      'Device diagnostics', 'External and dynamic output', 'Technical appendix'];
    const mainCount = catalog.entries.filter(entry => entry.group !== 'Technical appendix').length;
    for (const group of ['', ...groups.filter(name => counts.has(name))]) {
      const button = element('button');
      button.dataset.group = group;
      button.append(document.createTextNode(group || 'All primary copy'), element('span', '', (group ? counts.get(group) : mainCount).toLocaleString()));
      button.addEventListener('click', () => {
        activeGroup = group;
        if (group === 'Technical appendix') $('include-technical').checked = true;
        page = 0;
        renderEntries();
      });
      $('groups').append(button);
    }
  }
  function resetFilters() {
    activeGroup = '';
    page = 0;
    $('search').value = '';
    $('status-filter').value = 'all';
    $('include-technical').checked = false;
    renderEntries();
  }
  async function copyFeedback() {
    const output = feedbackText(catalog, state);
    try {
      await navigator.clipboard.writeText(output);
      toast('Feedback copied. Paste it into our chat when ready.');
    } catch (_) {
      $('copy-fallback').value = output;
      $('copy-dialog').showModal();
      $('copy-fallback').focus();
      $('copy-fallback').select();
    }
  }
  function downloadFeedback() {
    const url = URL.createObjectURL(new Blob([feedbackText(catalog, state)], {type: 'text/plain;charset=utf-8'}));
    const anchor = element('a');
    anchor.href = url;
    anchor.download = `tinytouch-copy-feedback-${catalog.commit}.txt`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function start() {
    try {
      const response = await fetch('catalog.json');
      if (!response.ok) throw new Error(`Catalog request failed (${response.status})`);
      catalog = await response.json();
      storageKey = `tinytouch-copy-review:${catalog.commit}:${catalog.entries.length}`;
      try {
        const raw = localStorage.getItem(storageKey);
        const saved = raw ? JSON.parse(raw) : null;
        if (saved && typeof saved.entries === 'object' && saved.entries !== null) {
          state.entries = saved.entries;
          state.general = typeof saved.general === 'string' ? saved.general : '';
        }
        $('save-state').textContent = raw ? 'Saved draft restored' : 'Drafts save in this browser';
      } catch (_) {
        storageAvailable = false;
        $('save-state').textContent = 'Draft in memory — copy before leaving';
      }
      $('copy-count').textContent = catalog.entries.filter(entry => entry.group !== 'Technical appendix').length.toLocaleString();
      $('route-count').textContent = catalog.routes.length;
      $('version').textContent = `CLI ${catalog.version} · ${catalog.commit}`;
      $('snapshot').textContent = `${catalog.entries.length.toLocaleString()} total lines · ${catalog.coverage.length} source files · snapshot ${catalog.commit}`;
      for (const route of catalog.routes) $('routes').append(element('li', '', `${route.path} → ${route.target}`));
      for (const source of catalog.coverage) $('coverage').append(element('p', '', `${source.file} · ${source.literals} strings · ${source.method}`));
      $('general-comment').value = state.general;
      $('general-comment').addEventListener('input', () => { state.general = $('general-comment').value; persist(); updateCounts(); });
      $('search').addEventListener('input', () => {
        clearTimeout(queryTimer);
        queryTimer = setTimeout(() => { page = 0; renderEntries(); }, 150);
      });
      for (const id of ['status-filter', 'include-technical']) $(id).addEventListener('change', () => {
        if (!$('include-technical').checked && activeGroup === 'Technical appendix') activeGroup = '';
        page = 0;
        renderEntries();
      });
      $('reset-filters').addEventListener('click', resetFilters);
      $('copy-feedback').addEventListener('click', copyFeedback);
      $('download-feedback').addEventListener('click', downloadFeedback);
      $('close-dialog').addEventListener('click', () => $('copy-dialog').close());
      $('previous').addEventListener('click', () => { page--; renderEntries(); $('group-title').scrollIntoView({block: 'start'}); });
      $('next').addEventListener('click', () => { page++; renderEntries(); $('group-title').scrollIntoView({block: 'start'}); });
      renderNavigation();
      renderEntries();
      updateCounts();
    } catch (error) {
      $('fatal').textContent = `The copy catalog could not load. Reload this page to retry. ${error.message}`;
      $('fatal').hidden = false;
      $('save-state').textContent = 'Catalog unavailable';
      $('result-count').textContent = 'Unable to load catalog';
    }
  }
  start();
}
