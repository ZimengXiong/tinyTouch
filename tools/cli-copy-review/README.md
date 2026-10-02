# CLI sentence review

A static copyediting page for tinyTouch's CLI. Review the suggested wording, edit a proposal, add comments, and copy feedback into chat for manual implementation. The page does not change the CLI or send comments to a server. Drafts save in browser local storage; feedback can also be downloaded as plain text.

The page shows unique sentences and longer phrases of **six words or more**. Exact repeats are combined across menus, help and output paths. Case, surrounding whitespace, final punctuation, menu numbers and option keys do not create separate entries. Each card lists its other contexts. Short labels, command syntax and diagnostic tokens remain in the source and the downloadable original catalog.

`wording.json` supplies reviewed proposals in approximately 80% AES technical English: direct instructions, short sentences, consistent terms, and a clear problem followed by the recovery action. Command names, limits and template values retain their meaning. Whole prompts use sentence case; fragments inserted after “to” remain lowercase.

The left column retains the current CLI wording. Suggestions are not exported automatically. Checking **Reviewed** accepts a changed proposal for feedback export. Manual edits and comments are also exported. Browser drafts from the earlier page are restored where wording survives; feedback outside this filter moves to the general comment instead of being lost. Drafts do not sync across browsers.

Rebuild and validate from the repository root:

```sh
python3 tools/cli-copy-review/build.py
node --check tools/cli-copy-review/site/app.js
node --test tools/cli-copy-review/feedback.test.cjs
python3 -m unittest discover -s tools/cli-copy-review -p '*_test.py'
```

The published `site/index.html` embeds its styles, JavaScript, and catalog in one document. This prevents cached assets from mixing an older script with newer page controls. Edit `index.template.html` for page markup and rebuild before publishing.

Generation imports the CLI's metadata and reads runtime string literals from the CLI, native support modules, installer, and tracked firmware C files. Help descriptions are read before argparse wraps them into display lines. Generation does not execute command handlers, connect to hardware, change device settings, or import native macOS helper modules.

Publish the static directory with Devshare:

```sh
~/.local/bin/devshare publish --keep tools/cli-copy-review/site
```

Use `--update SHARE_URL` to update an existing share. The page uses stable content-based entry IDs to preserve comments when other entries are added or removed. The complete extracted source text remains in `site/original-catalog.json`.
