# Hanser voice assets

The local integrated profile uses two user-reviewed recordings copied from
`H:\voice_process\without_bgm_clarified`. Runtime copies live under this
project, while `manifest.jsonl` preserves their frozen revisions and transcripts.

Lifecycle directories:

- `raw/`: immutable intake; never selected by runtime.
- `cleaned/`: editing output pending review.
- `reference/`: fixed identity anchor candidates.
- `prompts/`: real style prompt candidates.
- `transcripts/`: exact human-reviewed transcripts.
- `profiles/`: listening-test and approval records.

An asset becomes selectable only when `review_status` is `approved`, its file
exists, and the containing voice profile is validated and enabled.
