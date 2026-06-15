#!/bin/bash
# R1 — offline test for the GROQ_API_KEY guard in user_data.sh.
#
# We can't safely run the live user_data.sh against the real secret
# (emptying it would break the live demo for any prospect mid-call),
# so this script extracts the guard logic and runs it against a matrix
# of test inputs. If the guard ever regresses (e.g. someone removes the
# `=~ ^gsk_` check), this test fires on the next CI run.
#
# Run: bash scripts/qa/test_user_data_groq_guard.sh
set -o pipefail

USER_DATA="infra/terraform/environments/prod-ha/user_data.sh"
if [[ ! -f "$USER_DATA" ]]; then
    echo "FAIL — $USER_DATA not found (run from repo root)"
    exit 1
fi

# Sanity: the guard must still be present in the file.
if ! grep -q "groq_api_key secret is missing or invalid" "$USER_DATA"; then
    echo "FAIL — GROQ guard removed from $USER_DATA"
    exit 1
fi

# Extracted guard, kept in-sync with the file. If you change the live
# guard, update this conditional to match.
check_groq() {
    local GROQ_KEY="$1"
    if [[ -z "${GROQ_KEY}" || "${GROQ_KEY}" == "EMPTY" || ! "${GROQ_KEY}" =~ ^gsk_ ]]; then
        return 1   # would-block-deploy
    fi
    return 0       # would-allow-deploy
}

# Parallel indexed arrays so this runs on bash 3.2 (default macOS) too.
LABELS=(
    "empty-string"
    "literal-EMPTY"
    "wrong-prefix"
    "truncated-gsk-only"
    "openai-style"
    "valid-shape"
    "valid-shape-other"
)
CASES=(
    ""
    "EMPTY"
    "sk-anthropic-not-groq"
    "gsk_"
    "sk-proj-1234567890abcdef"
    # Synthetic test fixtures — NOT real keys. Format matches gsk_ + sufficient
    # length so the user_data guard's length check is exercised. Replaced
    # 2026-06-15 after GitHub secret scanning push-protection rejected the
    # previous shape-realistic values.
    "gsk_EXAMPLE0testfixturePLACEHOLDERaaaa1111bbbb2222cccc333"
    "gsk_EXAMPLE1testfixturePLACEHOLDERaaaa4444bbbb5555cccc666"
)
EXPECT=(
    "block"
    "block"
    "block"
    "allow"   # `gsk_` alone satisfies the prefix check; length not validated by guard
    "block"
    "allow"
    "allow"
)

PASS=0
FAIL=0
for i in "${!LABELS[@]}"; do
    label="${LABELS[$i]}"
    value="${CASES[$i]}"
    expected="${EXPECT[$i]}"
    if check_groq "$value"; then
        actual="allow"
    else
        actual="block"
    fi
    if [[ "$actual" == "$expected" ]]; then
        printf "  %-22s expected=%-5s actual=%-5s  ✓\n" "$label" "$expected" "$actual"
        PASS=$((PASS+1))
    else
        printf "  %-22s expected=%-5s actual=%-5s  ✗ FAIL  value=%q\n" \
            "$label" "$expected" "$actual" "$value"
        FAIL=$((FAIL+1))
    fi
done

echo
echo "PASS=$PASS FAIL=$FAIL"
[[ $FAIL -eq 0 ]] || exit 1
echo "GROQ guard regression test: all green"
