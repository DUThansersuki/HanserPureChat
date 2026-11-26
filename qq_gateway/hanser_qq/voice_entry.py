"""QQ-specific short utterances on the existing frozen Voice runtime."""
from __future__ import annotations

import re
import os
import random

def short_bubble_planner(base):
    class QQSpeechPlanner(base):
        mapping_revision = "qq_speech_comma_pause_v5"

        def __init__(self, *, comma_pause_min_ms=None, comma_pause_max_ms=None, **kwargs):
            super().__init__(**kwargs)
            self.soft_max_chars = 36
            self.hard_max_chars = 40
            self.comma_pause_min_ms = int(os.environ.get('HANSER_QQ_COMMA_PAUSE_MIN_MS', '160')) if comma_pause_min_ms is None else comma_pause_min_ms
            self.comma_pause_max_ms = int(os.environ.get('HANSER_QQ_COMMA_PAUSE_MAX_MS', '220')) if comma_pause_max_ms is None else comma_pause_max_ms

        def _gap(self, text):
            if text.rstrip().endswith(('，', ',')):
                return random.randint(self.comma_pause_min_ms, self.comma_pause_max_ms)
            return base._gap(text)

        def _plain_characters(self, builder, start, end):
            # Old sparse semantic replies still have source coordinates. Convert
            # only CJK clause spaces in speech, recording each replacement.
            text, cursor = builder.semantic_text, start
            for match in re.finditer(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])", text[start:end]):
                left, right = start + match.start(), start + match.end()
                base._plain_characters(builder, cursor, left)
                builder.add(left, right, "，", operation="replace", precision="rule_based",
                            rule_id="qq_sparse_clause_pause")
                cursor = right
            base._plain_characters(builder, cursor, end)

        def _segment_ranges(self, text):
            # Preserve sentence grouping; comma boundaries get an explicit
            # timeline gap rather than depending on the model's internal pause.
            phrases, current = [], ''
            for sentence in re.split(r'(?<=[。！？!?；;\n])', text):
                if not sentence:
                    continue
                if current and len(current) + len(sentence) > 18:
                    phrases.append(current)
                    current = ''
                current += sentence
            if current:
                phrases.append(current)
            ranges, offset = [], 0
            for part in phrases:
                for clause in re.split(r'(?<=[，,])', part):
                    if not clause:
                        continue
                    ranges.extend((offset + left, offset + right)
                                  for left, right in super()._segment_ranges(clause))
                    offset += len(clause)
            return ranges

    return QQSpeechPlanner


def main():
    import runpy
    import voice_runtime.speech as speech
    speech.SpeechPlanner = short_bubble_planner(speech.SpeechPlanner)
    runpy.run_module("voice_runtime.api", run_name="__main__")


if __name__ == "__main__":
    main()
