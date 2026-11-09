"""Actual failed wire fixture plus synthetic byte cases; never run native code.
The archived raw fixture is immutable. Its one-byte derivation is diagnostic
only and is never accepted as a new native capture or a training observation.
"""
import base64
import json
from pathlib import Path
import re
import unittest
import chess
import protocol

HERE = Path(__file__).resolve().parent
FAILED_RUN = 42597456584
FAILED_STDOUT_SHA256 = "f432b61cafa2c0fcef814b259f882ad37088bfe3b199492a60929b019b12d80f"
RAW_NATIVE_BASE64 = "eyJzY2hlbWEiOiJ0ZXN0MTc1Ny1mYzItYWN0aXZhdGlvbi8xIiwicm93X2lkIjowLCJzb3VyY2VfY29tbWl0IjoiN2ExN2I1M2JjYWY5M2Y1NGEyM2FkNjVmMzA5YzQxMzU4NTUwMGM4MiIsInN1cHBsaWVkX2ZlbiI6ImszcjMvOC84LzNwUDMvOC84LzgvNEszIHcgLSBkNiAwIDEiLCJtb3ZlcyI6W10sImZpbmFsX2ZlbiI6ImszcjMvOC84LzNwUDMvOC84LzgvNEszIHcgLSAtIDAgMSIsImhpc3Rvcnlfc2NvcGUiOiJzdXBwbGllZF9mZW5fYW5kX2xlZ2FsX3ByZWZpeF9vbmx5IiwicGh5c2ljYWxfaGVhZCI6MTAsInN0b3JlZF9oZWFkcyI6OTIsImluX2NoZWNrIjpmYWxzZSwib3JkaW5hcnlfZXZhbF90cmFjZV9hdmFpbGFibGUiOnRydWUsInNpZGVfdG9fbW92ZSI6ACwicnVsZTUwIjowLCJmYzAiOls1NDQwLC0xNTc5LDE1NDM2LDIyMzksLTQ4Mzk0LDY2NjE3LDE5MDI1LDUzNjIsODUwMzAsLTE5MjE1LC0zMjE1NiwtMTczNDM4LDE1MTI0OSwxMTc1MywtMzgxMjIsLTI4OTgzLC0xNjA4NTksLTE2NzIxLDIzMTExLDE4MjgsLTIyMzg2LC0xMzc5MiwtNzIwNywxNTA3OSwtNDcyNzUsLTEwNjY0LDIwMTgsLTgwNjUsODMyNjIsNDk5ODAsMjA3MywxNTE3OF0sImZjMSI6Wy02MjQyLC02NDY2LDQyLC01MzI4MiwtMTE4OTIsLTE2MzgsMTA1NjQsLTM1MTQzLC0xOTIyLDYyNTUsLTY0OTU5LC0xMTU1NSwtMjg0MywtOTI5LC01NTc3MiwtMjMwMTMsLTEwOTMsLTYyOTg3LDExNDA2LDEyNDYsLTQ2OTg4LC03MTYsMTg3MTksLTY3NDMsLTc2NDAsLTE0MjEsMjI1OCwtMTAwMTEsLTIwMjYwLC0zMzg1NCwyMjkxLDExMDFdLCJpbnB1dF9uYXRpdmUiOlsxNCwxLDExMywyLDEyNywxMjcsMTI3LDEyNywxMjcsMTI3LDEyNywxLDEyNyw1NCwxLDMxLDEyNywxMjcsMTI3LDEzLDEyNyw2NSwxMjcsMTI3LDEyNyw5MCwyNCwxMDgsMTI3LDEyNywyLDEwOSw0MiwwLDEyMCwxNywxMjcsMCwwLDAsMCwwLDEyNywxNCwwLDAsMTUsMCwwLDEyNywxMjcsNDEsMTI3LDkxLDAsMCwwLDAsMCwxMTcsMTI3LDEyNywxNiwxMTgsNzQsNzksMCwxMjcsNyw3NCwxMjcsMTI3LDIsMTI3LDEyNywyLDExMSwzLDksMTI3LDEyNyw1LDEyNywxMjcsMTUsMSwxMjcsMTI3LDEyNywwLDEyNyw4NiwxMjcsMTI3LDEwLDIsMCwwLDAsMCwwLDk3LDAsMCwwLDAsMTI3LDE5LDAsMCwzNSwwLDAsMCwxMjcsMCwwLDAsMCwwLDAsMCwxMjcsMCwwLDAsMzUsMTddLCJpbnB1dF9zZXJpYWxpemVkIjpbMTQsMSwxMTMsMiwxMjcsMTI3LDEyNywxMywxMjcsMTI3LDEyNywxMjcsMTI3LDY1LDEyNywxMjcsMTI3LDEyNywxMjcsMSwxMjcsOTAsMjQsMTA4LDEyNyw1NCwxLDMxLDEyNywxMjcsMiwxMDksNDIsMCwxMjAsMTcsMCwxMjcsMTI3LDQxLDEyNywwLDAsMCwxMjcsOTEsMCwwLDAsMCwxMjcsMTQsMCwwLDAsMTE3LDAsMCwxNSwwLDEyNywxMjcsMTYsMTE4LDc0LDc5LDAsMTI3LDEyNyw1LDEyNywxMjcsNyw3NCwxMjcsMTI3LDE1LDEsMTI3LDEyNywyLDEyNywxMjcsMiwxMjcsMCwxMjcsODYsMTExLDMsOSwxMjcsMTI3LDEyNywxMCwyLDAsMCwwLDAsMCwwLDEyNywwLDAsOTcsMCwwLDAsMCwwLDAsMCwwLDEyNywxOSwwLDAsMTI3LDAsMCwwLDM1LDAsMCwwLDM1LDE3XSwic2VyaWFsaXplZF90b19uYXRpdmUiOlswLDEsMiwzLDE2LDE3LDE4LDE5LDQsNSw2LDcsMjAsMjEsMjIsMjMsOCw5LDEwLDExLDI0LDI1LDI2LDI3LDEyLDEzLDE0LDE1LDI4LDI5LDMwLDMxLDMyLDMzLDM0LDM1LDQ4LDQ5LDUwLDUxLDM2LDM3LDM4LDM5LDUyLDUzLDU0LDU1LDQwLDQxLDQyLDQzLDU2LDU3LDU4LDU5LDQ0LDQ1LDQ2LDQ3LDYwLDYxLDYyLDYzLDY0LDY1LDY2LDY3LDgwLDgxLDgyLDgzLDY4LDY5LDcwLDcxLDg0LDg1LDg2LDg3LDcyLDczLDc0LDc1LDg4LDg5LDkwLDkxLDc2LDc3LDc4LDc5LDkyLDkzLDk0LDk1LDk2LDk3LDk4LDk5LDExMiwxMTMsMTE0LDExNSwxMDAsMTAxLDEwMiwxMDMsMTE2LDExNywxMTgsMTE5LDEwNCwxMDUsMTA2LDEwNywxMjAsMTIxLDEyMiwxMjMsMTA4LDEwOSwxMTAsMTExLDEyNCwxMjUsMTI2LDEyN10sIm91dHB1dF93ZWlnaHRzIjpbMyw1LC0yLDAsLTEsMiwwLC0zLDMsLTIsMCwtMiwyLC01LC00LC0xLDAsMCwtNSwtMywtMiwyLDQsLTQsLTQsLTEsMiwtMSwzLC0xMCwtMTQsMTQsMywzLC0xMCwwLC0yLC01LC0yLC02LC0yMiwxOCw2LDE3LC0zLC0yLDYsLTI2LDIsMSwwLC0yLDIsLTYsMiwyLDE2LDEsLTQsLTIsLTUsLTUwLC0xMDQsOTEsLTEsLTIsNCwtMiwtNywtNSwtMSwyNCwzLC0xNSwwLC0yMCwxLC0yNSwtMTEsLTQyLDI3LC0xLC0xNCwxMCwxOSwxNCwtNSw0LC0yNSw2LDMzLDMsLTExLC0xLDEwLDI5LC0zOSwtMTI3LDM0LDM4LDM4LC0yOCwtMzksLTEwNywtNTcsLTksMTI3LC0xLDYzLDUwLDEyNywxMjcsLTEsNjMsLTIzLDIsLTEyNywzLC0yOCwxMyw4LDE3LC0xNCwyOCwtMywyNiwzLC0zXSwib3V0cHV0X2JpYXMiOi0zNDA0LCJmYzJfYWZmaW5lIjotMzMzOTMsInNraXAiOi0xMzEwNSwiY29tYmluZWQiOi00NjQ5OCwic2NhbGVkIjotMjcyNDQsInJhd19ubnVlIjotMTcwMiwib3JkaW5hcnlfcmF3X25udWUiOi0xNzAyLCJoZWxwZXJfc3RhdGljX3plcm8iOi0xNzYzLCJoZWxwZXJfc3RhdGljX2NwIjotNTEzLCJvcmRpbmFyeV9zdGF0aWNfemVybyI6LTE3NjMsIm9yZGluYXJ5X3N0YXRpY19jcCI6LTUxMywic3RhdGljX2xvc3NfYXZhaWxhYmxlIjp0cnVlLCJzdGF0aWNfbG9zc191bmF2YWlsYWJsZV9yZWFzb24iOm51bGwsInJhd19vZmZzZXRfbWluIjotMjQsInJhd19vZmZzZXRfbWF4IjoyNCwiY3BfcGVyc3BlY3RpdmUiOiJzaWRlX3RvX21vdmUiLCJyYXdfb2Zmc2V0X3N0YXRpY196ZXJvIjpbLTE3ODgsLTE3ODcsLTE3ODYsLTE3ODUsLTE3ODQsLTE3ODMsLTE3ODIsLTE3ODEsLTE3ODAsLTE3NzksLTE3NzgsLTE3NzcsLTE3NzYsLTE3NzUsLTE3NzQsLTE3NzMsLTE3NzIsLTE3NzEsLTE3NzAsLTE3NjksLTE3NjgsLTE3NjcsLTE3NjYsLTE3NjUsLTE3NjMsLTE3NjIsLTE3NjEsLTE3NjAsLTE3NTksLTE3NTgsLTE3NTcsLTE3NTYsLTE3NTUsLTE3NTQsLTE3NTMsLTE3NTIsLTE3NTEsLTE3NTAsLTE3NDksLTE3NDgsLTE3NDcsLTE3NDYsLTE3NDUsLTE3NDQsLTE3NDMsLTE3NDIsLTE3NDEsLTE3NDAsLTE3MzldLCJyYXdfb2Zmc2V0X2NwIjpbLTUyMCwtNTIwLC01MjAsLTUxOSwtNTE5LC01MTksLTUxOCwtNTE4LC01MTgsLTUxOCwtNTE3LC01MTcsLTUxNywtNTE2LC01MTYsLTUxNiwtNTE1LC01MTUsLTUxNSwtNTE1LC01MTQsLTUxNCwtNTE0LC01MTMsLTUxMywtNTEzLC01MTIsLTUxMiwtNTEyLC01MTEsLTUxMSwtNTExLC01MTEsLTUxMCwtNTEwLC01MTAsLTUwOSwtNTA5LC01MDksLTUwOCwtNTA4LC01MDgsLTUwOCwtNTA3LC01MDcsLTUwNywtNTA2LC01MDYsLTUwNl0sImludGVnZXJfcGFyaXR5Ijp0cnVlfQ=="
DIAGNOSTIC_CONTEXT_JSON = r'{"raw_to_physical":[0,0,1,1,2,3,4,6,4,4,5,6,7,7,7,7,8,8,9,9,9,9,9,9,10,11,12,13,12,14,15,16,17,18,17,19,20,21,22,23,22,24,25,26,27,28,27,29,30,31,32,33,32,34,35,36,37,38,37,39,40,41,42,43,42,44,45,46,47,48,47,49,50,51,52,53,52,53,54,55,56,56,54,56,57,58,59,59,57,59,60,61,62,63,62,63,64,65,64,66,64,66,67,68,69,69,67,69,70,71,72,72,70,72,73,74,75,75,73,75,76,76,77,77,77,77,78,78,79,79,79,79,80,80,81,81,81,81,82,82,83,83,83,83,84,84,84,84,84,84,85,85,85,85,85,85,86,86,86,86,86,86,87,87,87,87,87,87,88,88,88,88,88,88,89,89,89,89,89,89,90,90,90,90,90,90,91,91,91,91,91,91],"readout":{"bias_before":-3404,"bias_offset":135508548,"physical_head":10,"serialized_to_native":[0,1,2,3,16,17,18,19,4,5,6,7,20,21,22,23,8,9,10,11,24,25,26,27,12,13,14,15,28,29,30,31,32,33,34,35,48,49,50,51,36,37,38,39,52,53,54,55,40,41,42,43,56,57,58,59,44,45,46,47,60,61,62,63,64,65,66,67,80,81,82,83,68,69,70,71,84,85,86,87,72,73,74,75,88,89,90,91,76,77,78,79,92,93,94,95,96,97,98,99,112,113,114,115,100,101,102,103,116,117,118,119,104,105,106,107,120,121,122,123,108,109,110,111,124,125,126,127],"weight_offsets":[135508552,135508553,135508554,135508555,135508556,135508557,135508558,135508559,135508560,135508561,135508562,135508563,135508564,135508565,135508566,135508567,135508568,135508569,135508570,135508571,135508572,135508573,135508574,135508575,135508576,135508577,135508578,135508579,135508580,135508581,135508582,135508583,135508584,135508585,135508586,135508587,135508588,135508589,135508590,135508591,135508592,135508593,135508594,135508595,135508596,135508597,135508598,135508599,135508600,135508601,135508602,135508603,135508604,135508605,135508606,135508607,135508608,135508609,135508610,135508611,135508612,135508613,135508614,135508615,135508616,135508617,135508618,135508619,135508620,135508621,135508622,135508623,135508624,135508625,135508626,135508627,135508628,135508629,135508630,135508631,135508632,135508633,135508634,135508635,135508636,135508637,135508638,135508639,135508640,135508641,135508642,135508643,135508644,135508645,135508646,135508647,135508648,135508649,135508650,135508651,135508652,135508653,135508654,135508655,135508656,135508657,135508658,135508659,135508660,135508661,135508662,135508663,135508664,135508665,135508666,135508667,135508668,135508669,135508670,135508671,135508672,135508673,135508674,135508675,135508676,135508677,135508678,135508679],"weights":[3,5,-2,0,-1,2,0,-3,3,-2,0,-2,2,-5,-4,-1,0,0,-5,-3,-2,2,4,-4,-4,-1,2,-1,3,-10,-14,14,3,3,-10,0,-2,-5,-2,-6,-22,18,6,17,-3,-2,6,-26,2,1,0,-2,2,-6,2,2,16,1,-4,-2,-5,-50,-104,91,-1,-2,4,-2,-7,-5,-1,24,3,-15,0,-20,1,-25,-11,-42,27,-1,-14,10,19,14,-5,4,-25,6,33,3,-11,-1,10,29,-39,-127,34,38,38,-28,-39,-107,-57,-9,127,-1,63,50,127,127,-1,63,-23,2,-127,3,-28,13,8,17,-14,28,-3,26,3,-3]}}'


def diagnostic_only_integer_derivation(raw):
    # Test fixture operation only. No production parser relaxes or repairs JSON.
    def numeric(match):
        return match[1] + str(match[2][0]).encode("ascii")
    result, count = re.subn(rb'("side_to_move":)([\x00\x01])(?=,)', numeric, raw)
    if count != 1:
        raise ValueError("Expected one retained byte-sized Color field")
    return result


class WireSerializationTests(unittest.TestCase):
    def test_actual_failed_native_row_reproduces_column379(self):
        raw = base64.b64decode(RAW_NATIVE_BASE64)
        with self.assertRaises(json.JSONDecodeError) as raised:
            json.loads(raw)
        self.assertEqual(raised.exception.pos, 378)
        self.assertEqual(raw[378], 0)

    def test_only_numeric_color_change_makes_all_later_fields_valid(self):
        raw = base64.b64decode(RAW_NATIVE_BASE64)
        changed = diagnostic_only_integer_derivation(raw)
        self.assertEqual(len(changed), len(raw))
        self.assertEqual(sum(a != b for a, b in zip(raw, changed)), 1)
        row = json.loads(changed)
        context = json.loads(DIAGNOSTIC_CONTEXT_JSON)
        readout = context["readout"]
        protocol.validate(row, mapping=context["raw_to_physical"],
                          readout=(readout["bias_before"], readout["weights"]),
                          expected_board=chess.Board(row["final_fen"]))
        self.assertFalse(any(byte < 32 for byte in changed))

    def test_both_color_byte_values_must_be_decimal_integers(self):
        for color in (0, 1):
            raw = b'{"side_to_move":' + bytes([color]) + b',"bool":false,"none":null}'
            with self.assertRaises(json.JSONDecodeError): json.loads(raw)
            row = json.loads(diagnostic_only_integer_derivation(raw))
            self.assertIs(type(row["side_to_move"]), int)
            self.assertEqual(row["side_to_move"], color)
            self.assertIs(row["bool"], False)
            self.assertIsNone(row["none"])

    def test_emitter_promotes_color_and_array_scalars_explicitly(self):
        source = (HERE / "overlay/src/fc2_activation_command.h").read_text()
        self.assertIn("<< static_cast<int>(position.side_to_move())", source)
        self.assertNotIn("<< position.side_to_move()", source)
        self.assertIn("out << std::int64_t(values[i]);", source)
        self.assertIn('position.checkers() ? "true" : "false"', source)
        self.assertIn('if (position.checkers()) out << "null"', source)

    def test_signed_uint8_boolean_boundary_tokens_remain_numeric_or_literals(self):
        value = {"unsigned": [0, 1, 127, 128, 255], "signed": [-128, -1, 0, 127],
                 "booleans": [False, True], "none": None}
        raw = json.dumps(value, separators=(",", ":")).encode("ascii")
        self.assertEqual(json.loads(raw), value)
        self.assertFalse(any(byte < 32 for byte in raw))


if __name__ == "__main__": unittest.main()
