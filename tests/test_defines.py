"""defines.py: a file's #define constants as evaluator values."""

from skyfish.analysis.defines import defines

HEADER = """
#include <stdint.h>

#define TX_MAX 64
#define packetDelimOuter 0xDB
#define RETRY_DELAY_MS (500U)
#define TOTAL_SLOTS (TX_MAX / 2)      /* uses an earlier define */
#define GREETING "OK\\r\\n"
#define COLON ':'
#define SCALE 1.5f
#define DEBUG                         // a bare flag - no value to hold
#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define BAUD 115200UL \\
              + 0

#ifdef STM32G4
#define CORE_MHZ 170
#endif

static uint16_t reg = REG_BASE;
#define FROM_REGISTER (*(volatile uint32_t *)0x40000000)
"""


def test_defines_computed_in_file_order():
    found = defines(HEADER)
    assert found["TX_MAX"] == 64
    assert found["packetDelimOuter"] == 0xDB
    assert found["RETRY_DELAY_MS"] == 500
    assert found["TOTAL_SLOTS"] == 32          # TX_MAX was already known
    assert found["GREETING"] == "OK\r\n"
    assert found["COLON"] == ord(":")
    assert found["SCALE"] == 1.5
    assert found["BAUD"] == 115200             # continuation line folded in
    assert found["CORE_MHZ"] == 170            # inside an #ifdef block


def test_what_cant_hold_a_value_is_left_out():
    found = defines(HEADER)
    assert "DEBUG" not in found                # bare flag
    assert "MAX" not in found                  # function-like
    assert "FROM_REGISTER" not in found        # needs the hardware


def test_card_values_win_over_defines():
    # The documented merge order: defines under, the operator's values over.
    from skyfish.analysis.evaluate import evaluate_condition
    merged = {**defines(HEADER), "TX_MAX": 8}
    assert evaluate_condition("length > TX_MAX", {**merged, "length": 10}).value is True
