#!/usr/bin/env python3
"""Guard cast-history snapshots across synchronous Lua queue callbacks."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "nampower" / "spellevents.cpp"
CAST_SOURCE_PATH = ROOT / "nampower" / "spellcast.cpp"


def failed_hook_lines():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    start = source.index("void Spell_C_SpellFailedHook")
    end = source.index("\n    int SpellCooldownHandlerHook", start)
    return source[start:end].splitlines()


def previous_code_line(lines, index):
    for line in reversed(lines[:index]):
        stripped = line.strip()
        if stripped and not stripped.startswith("//"):
            return stripped
    return ""


def function_source(path, signature, next_signature):
    source = path.read_text(encoding="utf-8")
    start = source.index(signature)
    end = source.index(next_signature, start)
    return source[start:end]


class RetryReentrancyStaticTest(unittest.TestCase):
    def test_queue_callbacks_consume_only_pre_event_snapshots(self):
        lines = failed_hook_lines()
        event_indices = [
            index
            for index, line in enumerate(lines)
            if "TriggerSpellQueuedEvent(QueueEvents::" in line
            and "_QUEUED, spellId);" in line
        ]

        self.assertEqual(4, len(event_indices), "expected all four server-retry queue paths")
        self.assertEqual(
            4,
            sum("auto const retryParams = *castParams;" in line for line in lines),
            "each retry path must snapshot its exact history entry",
        )

        for event_index in event_indices:
            event_line = lines[event_index]
            self.assertEqual(
                "auto const retryParams = *castParams;",
                previous_code_line(lines, event_index),
                f"snapshot must immediately precede callback: {event_line.strip()}",
            )

            expected_consumer = (
                "gNonGcdCastQueue.push(retryParams,"
                if "NON_GCD_QUEUED" in event_line
                else "gLastNormalCastParams = retryParams;"
            )
            event_indent = len(event_line) - len(event_line.lstrip())
            callback_suffix = []
            for line in lines[event_index + 1 :]:
                stripped = line.strip()
                indent = len(line) - len(line.lstrip())
                if stripped.startswith("}") and indent < event_indent:
                    break
                callback_suffix.append(line)

            suffix = "\n".join(callback_suffix)
            self.assertIn(expected_consumer, suffix)
            self.assertNotIn("*castParams", suffix)
            self.assertNotIn("castParams->", suffix)

    def test_normal_queue_consumes_old_generation_before_reentrant_cast(self):
        function = function_source(
            CAST_SOURCE_PATH,
            "void CastQueuedNormalSpell()",
            "void CastQueuedSpells()",
        )

        snapshot = "auto const normalCastParams = gLastNormalCastParams;"
        consume = "gCastData.normalSpellQueued = false;"
        cast = "Spell_C_CastSpellHook("
        pop = (
            "TriggerSpellQueuedEvent(NORMAL_QUEUE_POPPED, "
            "normalCastParams.spellId);"
        )

        self.assertEqual(1, function.count("gLastNormalCastParams"))
        self.assertEqual(1, function.count(consume))
        self.assertLess(function.index(snapshot), function.index(consume))
        self.assertLess(function.index(consume), function.index(cast))
        self.assertLess(function.index(cast), function.index(pop))
        self.assertNotIn(consume, function[function.index(cast) :])
        self.assertEqual(function.rfind("TriggerSpellQueuedEvent("), function.index(pop))
        self.assertNotIn("gCastData.", function[function.index(pop) + len(pop) :])


if __name__ == "__main__":
    unittest.main()
