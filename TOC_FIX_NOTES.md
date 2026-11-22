# Naver Blog TOC consistency fix

## Root cause
The converter recognized only exact labels such as `목차`, `Contents`, and `Table of Contents`.
A bilingual heading such as `📚 목차 (Contents)` was therefore rendered as a normal article heading, so no generated TOC list was inserted.

## Changes
- Recognize combined Korean/English TOC labels, including `📚 목차 (Contents)` and `Contents (목차)`.
- Use the same TOC-heading recognition for HTML preview, Naver plain-text output, and DOCX formatting.
- Add regression coverage for bilingual TOC headings and keep longer titles such as `목차 작성 방법` as regular headings.
- Existing draft refresh is handled automatically by comparing Notion `last_edited_time` and a hash of the page block tree, and checking that generated files still exist. No manual checkbox or JSON reset is required. Legacy tracker entries without a content hash are regenerated once when they are next in scope. Published drafts remain protected.

## Apply
Replace the files in the patch ZIP at the same paths in the repository, then commit and push.

For existing unpublished drafts, run the desired date scope (for example `Week`). A modified Notion page or block tree is regenerated automatically; a missing generated folder is also recreated even if its processing record remains. The first run after this update rebuilds legacy drafts in the selected scope once to save their content hashes. Published drafts remain protected. A Notion page without any TOC heading block is still left without a TOC; this fix recognizes the bilingual heading variant but does not force a TOC into documents that do not contain a TOC placeholder.

## Validation
- `python -m pytest -q`: 73 passed after content fingerprinting, automatic change detection, and missing-draft recovery were added.
