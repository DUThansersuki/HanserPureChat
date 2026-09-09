# Hanser voice assets

No voice asset is bundled or approved yet. Add only lawfully usable source
recordings, keep identity reference and style prompts distinct, and record the
exact transcript and review scope in `manifest.jsonl`.

Lifecycle directories:

- `raw/`: immutable intake; never selected by runtime.
- `cleaned/`: editing output pending review.
- `reference/`: fixed identity anchor candidates.
- `prompts/`: real style prompt candidates.
- `transcripts/`: exact human-reviewed transcripts.
- `profiles/`: listening-test and approval records.

An asset becomes selectable only when `review_status` is `approved`, its file
exists, and the containing voice profile is validated and enabled.
