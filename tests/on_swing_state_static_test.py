#!/usr/bin/env python3
"""Guard exact, reentrant-safe on-next-swing ownership and event ordering."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
NAMP = ROOT / "nampower"


def source(name):
    return (NAMP / name).read_text(encoding="utf-8")


def function_source(name, signature, next_signature):
    text = source(name)
    start = text.index(signature)
    end = text.index(next_signature, start)
    return text[start:end]


class OnSwingStateStaticTest(unittest.TestCase):
    def test_both_classic_attribute_bits_use_shared_classifier(self):
        helper = function_source(
            "helper.cpp", "bool SpellIsOnSwing", "bool SpellIsAttackTradeskillOrEnchant"
        )
        self.assertIn("SPELL_ATTR_ON_NEXT_SWING_1", helper)
        self.assertIn("SPELL_ATTR_ON_NEXT_SWING_2", helper)
        self.assertIn("|", helper)

        spell_go = function_source("spellevents.cpp", "void SpellGoHook", "int SpellStartHandlerHook")
        self.assertIn("SpellIsOnSwing(spell)", spell_go)
        self.assertNotIn("SPELL_ATTR_ON_NEXT_SWING_", spell_go)

    def test_armed_and_buffered_generations_are_value_owned(self):
        types = source("types.h")
        self.assertIn("struct OnSwingState", types)
        self.assertIn("CastSpellParams armedParams;", types)
        self.assertIn("CastSpellParams bufferedParams;", types)

        all_native = "\n".join(source(name) for name in (
            "main.hpp", "main.cpp", "spellcast.cpp", "spellevents.cpp"
        ))
        self.assertNotIn("gLastOnSwingCastParams", all_native)

        for name, value in (
            ("ARMED", 0), ("BUFFERED", 1), ("ARMED_REPLACED", 2),
            ("BUFFER_REPLACED", 3), ("BUFFER_POPPED", 4), ("CONSUMED", 5),
            ("FAILED", 6), ("CANCELLED", 7), ("BUFFER_CANCELLED", 8),
        ):
            self.assertIn(f"ON_SWING_STATE_{name} = {value}", types)

    def test_transitions_mutate_before_lua_and_never_clear_after(self):
        arm = function_source("spellcast.cpp", "void ArmOnSwingState", "void BufferOnSwingState")
        self.assertLess(arm.index("gOnSwingState.armed = true;"), arm.index("TriggerOnSwingStateEvent("))

        buffer = function_source("spellcast.cpp", "void BufferOnSwingState", "}\n    }\n\n    void CancelOnSwingState")
        self.assertLess(buffer.index("gOnSwingState.buffered = true;"), buffer.index("TriggerOnSwingStateEvent("))

        for signature, next_signature in (
            ("void CancelOnSwingState", "void FailOnSwingState"),
            ("void FailOnSwingState", "OnSwingState BeginOnSwingResolution"),
            ("OnSwingState BeginOnSwingResolution", "void FinishOnSwingResolution"),
        ):
            body = function_source("spellcast.cpp", signature, next_signature)
            clear = "gOnSwingState = OnSwingState{};"
            self.assertLess(body.index(clear), body.index("TriggerOnSwingStateEvent("))
            self.assertNotIn(clear, body[body.index("TriggerOnSwingStateEvent(") :])

        failure = function_source(
            "spellcast.cpp", "void FailOnSwingState", "OnSwingState BeginOnSwingResolution"
        )
        ambiguous = failure[failure.index("if (attemptId == 0)") :]
        ambiguous = ambiguous[: ambiguous.index("if (gOnSwingState.armedParams.castId")]
        self.assertIn("return;", ambiguous)
        self.assertNotIn("CancelOnSwingState", ambiguous)
        self.assertNotIn("gOnSwingState =", ambiguous)

        finish = function_source("spellcast.cpp", "void FinishOnSwingResolution", "void\n    TriggerSpellCastEvent")
        self.assertIn("auto const bufferedParams = resolvedState.bufferedParams;", finish)
        self.assertLess(finish.index("bufferedParams ="), finish.index("TriggerOnSwingStateEvent("))
        self.assertLess(finish.index("TriggerOnSwingStateEvent("), finish.index("Spell_C_CastSpellHook("))
        callback_guard = "if (gOnSwingState.armed || gOnSwingState.buffered)"
        self.assertIn(callback_guard, finish)
        self.assertLess(finish.index(callback_guard), finish.index("Spell_C_CastSpellHook("))
        guard_suffix = finish[finish.index(callback_guard) : finish.index("Spell_C_CastSpellHook(")]
        self.assertIn("ON_SWING_STATE_BUFFER_CANCELLED", guard_suffix)
        self.assertIn("return;", guard_suffix)

        legacy_pop = function_source(
            "spellcast.cpp",
            "void TriggerLegacyOnSwingBufferPopIfNoReplacement",
            "void ArmOnSwingState",
        )
        self.assertIn("if (!gOnSwingState.buffered)", legacy_pop)
        self.assertIn("TriggerSpellQueuedEvent(ON_SWING_QUEUE_POPPED", legacy_pop)

        state_source = source("spellcast.cpp")
        self.assertEqual(1, state_source.count("TriggerSpellQueuedEvent(ON_SWING_QUEUE_POPPED"))

    def test_cast_hook_settles_state_before_cast_event_callback(self):
        cast_hook = function_source(
            "spellcast.cpp", "bool\n    Spell_C_CastSpellHook(", "void\n    CancelSpellHook"
        )
        branch_start = cast_hook.index("if (spellIsOnSwing)")
        branch_end = cast_hook.index("return ret;", branch_start) + len("return ret;")
        branch = cast_hook[branch_start:branch_end]
        cast_event = branch.index("TriggerSpellCastEvent(")
        snapshot = branch.index("stateBeforeClientCast = gOnSwingState")
        client_cast = branch.index("ret = castSpell(")
        generation_guard = branch.index("HasSameOnSwingGenerations(")
        self.assertLess(snapshot, client_cast)
        self.assertLess(client_cast, generation_guard)
        self.assertLess(branch.index("ArmOnSwingState(attemptParams);"), cast_event)
        self.assertLess(branch.index("BufferOnSwingState(attemptParams);"), cast_event)
        self.assertIn("item == nullptr", branch)
        self.assertIn("attemptParams.guid", branch[cast_event:])
        self.assertIn("attemptId, true", branch[cast_event:])
        self.assertNotIn("gOnSwingState =", branch[cast_event:])

    def test_consumed_precedes_same_spellgo_terminal_events(self):
        spell_go = function_source("spellevents.cpp", "void SpellGoHook", "int SpellStartHandlerHook")
        begin = spell_go.index("BeginOnSwingResolution(spellId)")
        self.assertLess(begin, spell_go.index("TriggerSpellMissEvent("))
        self.assertLess(begin, spell_go.index("TriggerSpellGoEvent("))

        original = spell_go.index("spellGo(itemGUID, casterGUID, spellId, packet);")
        finish = spell_go.index("FinishOnSwingResolution(resolvedOnSwingState)")
        self.assertLess(original, finish)

    def test_on_swing_server_failure_cannot_enter_normal_retry_queues(self):
        failed = function_source(
            "spellevents.cpp", "void Spell_C_SpellFailedHook", "int SpellCooldownHandlerHook"
        )
        guard = failed.index("castParams->castType == CastType::ON_SWING")
        self.assertLess(guard, failed.index("GetRemainingCooldownForSpell(spellId)"))
        self.assertIn("return;", failed[guard : failed.index("GetRemainingCooldownForSpell(spellId)")])

    def test_spell_stop_finishes_native_cancel_before_state_callbacks(self):
        stop = function_source(
            "spell_scripts.cpp", "Script_SpellStopCastingHook", "Script_IsSpellInRange"
        )
        original = stop.index("spellStopCasting(luaState)")
        reset = stop.index("ResetCastFlags()")
        callbacks = stop.index("ClearQueuedSpells()")
        self.assertLess(original, reset)
        self.assertLess(reset, callbacks)
        self.assertNotIn("spellStopCasting(luaState)", stop[callbacks:])

        clear = function_source("main.cpp", "void ClearQueuedSpells", "void ResetDisenchantState")
        first_callback = clear.index("CancelOnSwingState()")
        for consume in (
            "gCastData.normalSpellQueued = false;",
            "gCastData.cooldownNormalSpellQueued = false;",
            "gCastData.nonGcdSpellQueued = false;",
            "gCastData.cooldownNonGcdSpellQueued = false;",
        ):
            self.assertLess(clear.index(consume), first_callback)
        self.assertNotIn(" = false;", clear[first_callback:])

    def test_exact_read_api_and_event_are_registered(self):
        api = function_source("spell_scripts.cpp", "Script_GetOnSwingInfo", "Script_GetCastInfo")
        for field in (
            "pending", "armed", "spellId", "targetGuid", "attemptId", "buffered",
            "bufferedSpellId", "bufferedTargetGuid", "bufferedAttemptId",
        ):
            self.assertIn(f"LuaFields::{field}", api)
        self.assertIn("std::to_string(armedAttemptId)", api)
        self.assertIn("std::to_string(bufferedAttemptId)", api)
        self.assertIn("GetOnSwingInfo", source("main.cpp"))
        self.assertIn("SPELL_ON_SWING_STATE", source("main.cpp"))
        self.assertIn("SPELL_ON_SWING_STATE = 657", source("game.hpp"))
        self.assertNotIn("casterUnit", api)
        self.assertNotIn("item", api)

        event = function_source(
            "spellcast.cpp", "void TriggerOnSwingStateEvent", "namespace {"
        )
        self.assertIn('format[] = "%d%d%s%s"', event)
        self.assertIn("params.spellId", event)
        self.assertIn("params.guid", event)
        self.assertIn("std::to_string(params.castId)", event)
        self.assertNotIn("params.casterUnit", event)
        self.assertNotIn("params.item", event)
        self.assertLess(event.index("SignalEventParam"), event.index("FreeGuidString"))


if __name__ == "__main__":
    unittest.main()
